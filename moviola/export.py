"""Export finale con FFmpeg, sempre dai file originali (mai dai proxy).

La timeline viene scomposta in tratti; ogni tratto prende il pezzo video
visibile, lo porta alla risoluzione del progetto e gli applica la LUT del
colore (clip intera -> pezzo -> timeline). L'audio è il mix delle tracce.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading

from . import audio, color, paths
from .media import parse_ffmpeg_time

FORMATS = {
    "mp4_h264": {"label": "MP4 · H.264 (per condividere)", "ext": ".mp4"},
    "mov_prores": {"label": "MOV · ProRes 422 (alta qualità)", "ext": ".mov"},
}
QUALITY = {"alta": 17, "buona": 20, "leggera": 24}


def _fit_size(src, W, H, zoom):
    sw = src.get("width") or W
    sh = src.get("height") or H
    f = min(W / sw, H / sh) * max(0.05, zoom)
    w = max(2, int(round(sw * f / 2)) * 2)
    h = max(2, int(round(sh * f / 2)) * 2)
    return w, h


def build_job(project, store, out_path, fmt="mp4_h264", quality="alta", workdir=None, start=0.0, end=None,
              text_renderer=None):
    """Prepara LUT, scritte, mix audio e comando FFmpeg. Restituisce (cmd, workdir, durata).

    text_renderer(item, W, H, path) disegna una scritta in un PNG trasparente
    (lo fornisce l'interfaccia, così anteprima ed export coincidono)."""
    end = project.timeline_end() if end is None else end
    if end - start <= 0:
        raise ValueError("La timeline è vuota.")
    exe = paths.ffmpeg_exe()
    if not exe:
        raise RuntimeError("FFmpeg non trovato.")
    workdir = workdir or tempfile.mkdtemp(prefix="moviola_export_")
    W, H = project.size
    fps = project.fps
    segs = project.video_segments(start, end)

    inputs = []
    n_inputs = 0
    chains = []
    lut_cache = {}
    for k, seg in enumerate(segs):
        dur = seg["end"] - seg["start"]
        it = seg["item"]
        src = project.source(it["source"]) if it else None
        if it is None or src is None or not os.path.exists(src["path"]):
            chains.append(f"color=c=black:s={W}x{H}:r={fps:g}:d={dur:.6f},format=rgb24,setsar=1[s{k}]")
            continue
        sp = project.speed(it)
        item_dur = project.item_dur(it)
        off = seg["start"] - it["start"]
        fi = min(float(it.get("fade_in", 0) or 0), item_dur)
        fo = min(float(it.get("fade_out", 0) or 0), item_dur)
        # le dissolvenze si calcolano dall'inizio del pezzo: se il tratto ne
        # contiene solo una parte si parte un po' prima e poi si ritaglia
        cs = off
        if fi > 0 and off < fi:
            cs = 0.0
        if fo > 0 and off + dur > item_dur - fo:
            cs = min(cs, max(0.0, item_dur - fo))
        lead = off - cs
        idx = n_inputs
        n_inputs += 1
        src_t = it["src_in"] + cs * sp
        inputs += ["-ss", f"{src_t:.6f}", "-t", f"{(lead + dur) * sp + 0.5:.6f}", "-i", src["path"]]
        zoom = float(it.get("zoom", 1.0) or 1.0)
        w1, h1 = _fit_size(src, W, H, zoom)
        x = (W - w1) / 2 + float(it.get("pos_x", 0.0) or 0.0) * W
        y = (H - h1) / 2 + float(it.get("pos_y", 0.0) or 0.0) * H
        grades = project.grades_for_item(it)
        key = color.grade_key(grades)
        c = f"[{idx}:v]setpts=(PTS-STARTPTS)/{sp:g},fps={fps:g},scale={w1}:{h1}:flags=bicubic,setsar=1,format=rgb24"
        if key != "identity":
            if key not in lut_cache:
                name = f"lut_{len(lut_cache)}.cube"
                color.write_cube(os.path.join(workdir, name), color.build_lut(grades, 33))
                lut_cache[key] = name
            c += f",lut3d=file={lut_cache[key]}:interp=tetrahedral"
        c += f",tpad=stop_mode=clone:stop_duration=2[c{k}]"
        chains.append(c)
        chains.append(f"color=c=black:s={W}x{H}:r={fps:g}:d={lead + dur + 0.2:.6f},format=rgb24[b{k}]")
        f = f"[b{k}][c{k}]overlay=x={x:.2f}:y={y:.2f}:eof_action=repeat:format=rgb"
        if fi > 0 and lead < fi:
            col = "white" if it.get("fade_in_color") == "white" else "black"
            f += f",fade=t=in:st=0:d={fi:.6f}:color={col}"
        if fo > 0:
            col = "white" if it.get("fade_out_color") == "white" else "black"
            f += f",fade=t=out:st={item_dur - fo - cs:.6f}:d={fo:.6f}:color={col}"
        f += f",trim=start={lead:.6f}:duration={dur:.6f},setpts=PTS-STARTPTS,format=rgb24,setsar=1[s{k}]"
        chains.append(f)

    n = len(segs)
    chains.append("".join(f"[s{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0[cat]")
    last = "cat"

    # scritte: PNG trasparenti sovrapposti al montaggio
    if text_renderer is not None:
        texts = [t for t in project.texts() if project.item_end(t) > start and t["start"] < end]
        for k, t in enumerate(sorted(texts, key=lambda i: i["track"])):
            png = os.path.join(workdir, f"testo_{k}.png")
            if not text_renderer(t, W, H, png):
                continue
            a = max(t["start"], start) - start
            d = project.item_end(t) - t["start"]
            idx = n_inputs
            n_inputs += 1
            inputs += ["-loop", "1", "-framerate", f"{fps:g}", "-t", f"{d + 0.1:.6f}", "-i", png]
            f = f"[{idx}:v]format=rgba"
            fi = min(float(t.get("fade_in", 0) or 0), d)
            fo = min(float(t.get("fade_out", 0) or 0), d)
            if fi > 0:
                f += f",fade=t=in:st=0:d={fi:.6f}:alpha=1"
            if fo > 0:
                f += f",fade=t=out:st={d - fo:.6f}:d={fo:.6f}:alpha=1"
            shift = t["start"] - start
            f += f",setpts=PTS-STARTPTS+{shift:.6f}/TB[t{k}]"
            chains.append(f)
            chains.append(f"[{last}][t{k}]overlay=eof_action=pass:format=rgb:"
                          f"enable='between(t,{a:.6f},{a + d:.6f})'[o{k}]")
            last = f"o{k}"

    final = f"[{last}]scale=out_color_matrix=bt709:out_range=tv"
    if fmt == "mov_prores":
        final += ",format=yuv422p10le[vout]"
    else:
        final += ",format=yuv420p[vout]"
    graph = ";\n".join(chains + [final])

    wav = os.path.join(workdir, "mix.wav")
    audio.write_wav(wav, project, store, start, end)
    a_idx = n_inputs
    inputs += ["-i", wav]

    script = os.path.join(workdir, "filtri.txt")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(graph)
    major = paths.ffmpeg_version()[0]
    fc = ["-/filter_complex", "filtri.txt"] if major >= 7 else ["-filter_complex_script", "filtri.txt"]

    cmd = [exe, "-y", "-hide_banner", "-stats_period", "0.5", "-loglevel", "info"] + inputs + fc
    cmd += ["-map", "[vout]", "-map", f"{a_idx}:a", "-r", f"{fps:g}"]
    cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    if fmt == "mov_prores":
        cmd += ["-c:v", "prores_ks", "-profile:v", "2", "-c:a", "pcm_s16le"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(QUALITY.get(quality, 18)),
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    cmd += ["-t", f"{end - start:.6f}", os.path.abspath(out_path)]
    return cmd, workdir, end - start


def run_job(cmd, workdir, duration, progress=None, cancel: threading.Event | None = None, keep=False):
    """Esegue l'export. Restituisce None se ok, altrimenti il messaggio d'errore."""
    proc = subprocess.Popen(cmd, cwd=workdir, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            errors="replace", **paths.no_window())
    tail = []
    try:
        for line in proc.stderr:
            tail.append(line)
            tail = tail[-40:]
            if cancel is not None and cancel.is_set():
                proc.kill()
                break
            if progress and "time=" in line:
                t = parse_ffmpeg_time(line)
                if t is not None:
                    progress(min(1.0, t / max(duration, 1e-6)))
        proc.wait()
    finally:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)
    if cancel is not None and cancel.is_set():
        return "Export annullato."
    if proc.returncode != 0:
        return "".join(tail[-12:])
    if progress:
        progress(1.0)
    return None
