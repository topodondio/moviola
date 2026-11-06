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


def build_job(project, store, out_path, fmt="mp4_h264", quality="alta", workdir=None, start=0.0, end=None):
    """Prepara LUT, mix audio e comando FFmpeg. Restituisce (cmd, workdir, durata)."""
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
        src_t = it["src_in"] + (seg["start"] - it["start"])
        idx = n_inputs
        n_inputs += 1
        inputs += ["-ss", f"{src_t:.6f}", "-t", f"{dur + 0.5:.6f}", "-i", src["path"]]
        grades = project.grades_for_item(it)
        key = color.grade_key(grades)
        f = (f"[{idx}:v]fps={fps:g},"
             f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=bicubic,"
             f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=rgb24")
        if key != "identity":
            if key not in lut_cache:
                name = f"lut_{len(lut_cache)}.cube"
                color.write_cube(os.path.join(workdir, name), color.build_lut(grades, 33))
                lut_cache[key] = name
            f += f",lut3d=file={lut_cache[key]}:interp=tetrahedral"
        f += f",tpad=stop_mode=clone:stop_duration=1,trim=duration={dur:.6f},setpts=PTS-STARTPTS[s{k}]"
        chains.append(f)

    n = len(segs)
    concat = "".join(f"[s{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0[cat]"
    final = "[cat]scale=out_color_matrix=bt709:out_range=tv"
    if fmt == "mov_prores":
        final += ",format=yuv422p10le[vout]"
    else:
        final += ",format=yuv420p[vout]"
    graph = ";\n".join(chains + [concat, final])

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
