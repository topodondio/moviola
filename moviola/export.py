"""Export finale con FFmpeg, sempre dai file originali (mai dai proxy).

La timeline viene scomposta in tratti; ogni tratto prende il pezzo video
visibile, lo porta alla risoluzione del progetto e gli applica la LUT del
colore (clip intera -> pezzo -> timeline). L'audio è il mix delle tracce.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import threading

from . import audio, color, paths, stabilize
from . import speed as spd
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


def _bars_filter(bars, H):
    """Bande nere sopra e sotto (bars = altezza di ciascuna, frazione del fotogramma)."""
    bh = int(round(max(0.0, min(0.49, bars)) * H))
    if bh <= 0:
        return ""
    return (f",drawbox=x=0:y=0:w=iw:h={bh}:color=black:t=fill"
            f",drawbox=x=0:y=ih-{bh}:w=iw:h={bh}:color=black:t=fill")


def _retime_expr(it, src_t, cs):
    """Espressione per setpts: dal tempo nel file (relativo a src_t) al tempo
    in uscita (relativo all'inizio del tratto). Lineare con velocità
    costante, a tratti con la curva di velocità."""
    tin = "(PTS-STARTPTS)*TB"
    if spd.curve_points(it) is None:
        return f"(PTS-STARTPTS)/{spd.const_speed(it):g}"
    base = src_t - it["src_in"]
    knots = [(sv - base, x - cs) for sv, x in spd.inverse_knots(it)]
    expr = None
    for (p0, q0), (p1, q1) in reversed(list(zip(knots[:-1], knots[1:]))):
        k = (q1 - q0) / max(p1 - p0, 1e-9)
        lin = f"({q0:.6f}+({tin}-({p0:.6f}))*{k:.6f})"
        expr = lin if expr is None else f"if(lt({tin},{p1:.6f}),{lin},{expr})"
    return f"'({expr})/TB'"


def build_job(project, store, out_path, fmt="mp4_h264", quality="alta", workdir=None, start=0.0, end=None,
              text_renderer=None, size=None):
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
    W, H = size or project.size
    W, H = int(W) - int(W) % 2, int(H) - int(H) % 2
    fps = project.fps
    segs = project.video_segments(start, end)

    inputs = []
    n_inputs = 0
    chains = []
    lut_cache = {}

    leads = {}

    def lead_of(path):
        if path not in leads:
            leads[path] = stabilize.video_lead(path)
        return leads[path]

    # la stabilizzazione ha bisogno dell'analisi: se manca (cache svuotata,
    # altro computer) la si rifà adesso, prima di esportare
    for it in project.items:
        if it.get("kind") == "video" and it.get("stab"):
            src = project.source(it["source"])
            if src and os.path.exists(src["path"]) and stabilize.track_for(src["path"]) is None:
                stabilize.analyze(src["path"])

    def black(label, dur):
        chains.append(f"color=c=black:s={W}x{H}:r={fps:g}:d={dur:.6f},format=rgb24,setsar=1[{label}]")

    def clip(label, it, seg_start, dur, fades=True, extra_fade=None, anim_static=None):
        """Catena di un pezzo tra seg_start e seg_start+dur (tempi timeline):
        velocità, inquadratura, colore, dissolvenze, bande. Anche fuori dal
        pezzo (margini delle transizioni): oltre i limiti del file si tiene
        fermo il primo/ultimo fotogramma."""
        nonlocal n_inputs
        src = project.source(it["source"])
        if src is None or not os.path.exists(src["path"]):
            black(label, dur)
            return
        fr = 1.0 / fps
        item_dur = project.item_dur(it)
        off = seg_start - it["start"]
        fi = min(float(it.get("fade_in", 0) or 0), item_dur) if fades else 0.0
        fo = min(float(it.get("fade_out", 0) or 0), item_dur) if fades else 0.0
        # le dissolvenze si calcolano dall'inizio del pezzo: se il tratto ne
        # contiene solo una parte si parte un po' prima e poi si ritaglia
        cs = off
        if fi > 0 and off < fi:
            cs = min(cs, 0.0)
        if fo > 0 and off + dur > item_dur - fo:
            cs = min(cs, max(0.0, item_dur - fo))
        lead = off - cs
        idx = n_inputs
        n_inputs += 1
        sdur = float(src.get("duration") or 0.0)
        freeze = it.get("freeze") is not None
        raw_t = float(project.src_time(it, it["start"] + cs))
        src_t = min(max(0.0, raw_t), max(0.0, sdur - 2 * fr))
        pre_pad = 0.0
        if raw_t < 0 and not freeze:  # prima dell'inizio del file: primo fotogramma fermo
            pre_pad = -raw_t / max(spd.speed_at(it, it["start"] + cs), 1e-3)
        src_end = float(project.src_time(it, it["start"] + cs + lead + dur))
        span = 1.0 if freeze else max(0.2, min(src_end, sdur) - src_t + 0.5)
        inputs.extend(["-ss", f"{src_t:.6f}", "-t", f"{span:.6f}", "-i", src["path"]])
        zoom = float(it.get("zoom", 1.0) or 1.0)
        w1, h1 = _fit_size(src, W, H, zoom)
        x = (W - w1) / 2 + float(it.get("pos_x", 0.0) or 0.0) * W
        y = (H - h1) / 2 + float(it.get("pos_y", 0.0) or 0.0) * H
        # colore: fisso, oppure (se animato) miscela nel tempo tra il colore
        # all'inizio e alla fine del tratto, come nell'anteprima
        if anim_static is not None:
            ga = gb = project.grades_at(it, anim_static)
        else:
            ga = project.grades_at(it, seg_start)
            gb = project.grades_at(it, seg_start + dur)
        key = color.grade_key(ga)
        key_b = color.grade_key(gb)
        # stabilizzazione: per prima, sui tempi del file (prima di cambiare velocità)
        c = f"[{idx}:v]"
        track = stabilize.track_for(src["path"]) if it.get("stab") else None
        if track is not None:
            ws, hs = _fit_size(src, W, H, min(zoom, 1.0))
            name = f"st{idx}"
            c += stabilize.export_filters(track, it, name, ws, hs, src_t, src_t + span, lead_of(src["path"]),
                                          os.path.join(workdir, f"stab_{idx}.cmd")) + ","
        if freeze:
            c += f"trim=end_frame=1,setpts=PTS-STARTPTS,fps={fps:g}"
        else:
            c += f"setpts={_retime_expr(it, src_t, cs)},fps={fps:g}"
            if pre_pad > 0:
                c += f",tpad=start_mode=clone:start_duration={pre_pad:.6f}"
        c += f",scale={w1}:{h1}:flags=bicubic,setsar=1,format=rgb24"

        def lut_filter(k, grades):
            if k == "identity":
                return "null"
            if k not in lut_cache:
                name = f"lut_{len(lut_cache)}.cube"
                color.write_cube(os.path.join(workdir, name), color.build_lut(grades, 33))
                lut_cache[k] = name
            return f"lut3d=file={lut_cache[k]}:interp=tetrahedral"

        tail = f"tpad=stop_mode=clone:stop_duration={lead + dur + 2:.3f}[c{label}]"
        if key == key_b:
            if key != "identity":
                c += "," + lut_filter(key, ga)
            chains.append(c + "," + tail)
        else:
            # T del filtro blend = secondi dall'inizio di questa catena (= timeline it.start + cs)
            off = seg_start - (it["start"] + cs)
            chains.append(c + f"[pre{label}]")
            chains.append(f"[pre{label}]split=2[pa{label}][pb{label}]")
            chains.append(f"[pa{label}]{lut_filter(key, ga)}[la{label}]")
            chains.append(f"[pb{label}]{lut_filter(key_b, gb)}[lb{label}]")
            chains.append(f"[la{label}][lb{label}]blend=all_expr='A+(B-A)*clip((T-{off:.6f})/{max(dur, 1e-6):.6f}\\,0\\,1)',"
                          f"format=rgb24,{tail}")
        chains.append(f"color=c=black:s={W}x{H}:r={fps:g}:d={lead + dur + 0.2:.6f},format=rgb24[b{label}]")
        f = f"[b{label}][c{label}]overlay=x={x:.2f}:y={y:.2f}:eof_action=repeat:format=rgb"
        if fi > 0 and lead < fi:
            col = "white" if it.get("fade_in_color") == "white" else "black"
            f += f",fade=t=in:st={-cs:.6f}:d={fi:.6f}:color={col}"
        if fo > 0:
            col = "white" if it.get("fade_out_color") == "white" else "black"
            f += f",fade=t=out:st={item_dur - fo - cs:.6f}:d={fo:.6f}:color={col}"
        if extra_fade:  # (in/out, colore) su tutto il tratto: passaggi dal nero/bianco
            f += f",fade=t={extra_fade[0]}:st={lead:.6f}:d={dur:.6f}:color={extra_fade[1]}"
        f += _bars_filter(float(it.get("bars", 0.0) or 0.0), H)
        f += f",trim=start={lead:.6f}:duration={dur:.6f},setpts=PTS-STARTPTS,format=rgb24,setsar=1[{label}]"
        chains.append(f)

    for k, seg in enumerate(segs):
        dur = seg["end"] - seg["start"]
        it = seg["item"]
        tr = seg.get("trans")
        if tr is not None:
            kind = tr["type"]
            if kind in ("dip_black", "dip_white"):
                col = "black" if kind == "dip_black" else "white"
                half = tr["cut"] - seg["start"]
                mid = (tr["t0"] + tr["t1"]) / 2
                clip(f"s{k}a", tr["a"], seg["start"], half, fades=False, extra_fade=("out", col), anim_static=mid)
                clip(f"s{k}b", tr["b"], tr["cut"], seg["end"] - tr["cut"], fades=False, extra_fade=("in", col),
                     anim_static=mid)
                chains.append(f"[s{k}a][s{k}b]concat=n=2:v=1:a=0,format=rgb24,setsar=1[s{k}]")
            else:
                mid = (tr["t0"] + tr["t1"]) / 2
                clip(f"s{k}a", tr["a"], seg["start"], dur, fades=False, anim_static=mid)
                clip(f"s{k}b", tr["b"], seg["start"], dur, fades=False, anim_static=mid)
                xf = {"dissolve": "fade", "wipe_l": "wipeleft", "wipe_r": "wiperight",
                      "push_l": "slideleft", "push_r": "slideright"}.get(kind, "fade")
                chains.append(f"[s{k}a][s{k}b]xfade=transition={xf}:duration={dur:.6f}:offset=0,"
                              f"trim=duration={dur:.6f},format=rgb24,setsar=1[s{k}]")
            continue
        if it is None:
            black(f"s{k}", dur)
            continue
        clip(f"s{k}", it, seg["start"], dur)

    n = len(segs)
    chains.append("".join(f"[s{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0[cat]")
    last = "cat"
    gbars = float(project.data["settings"].get("bars", 0.0) or 0.0)
    if gbars > 0:
        chains.append(f"[cat]format=rgb24{_bars_filter(gbars, H)}[catb]")
        last = "catb"

    # scritte: PNG trasparenti sovrapposti al montaggio
    if text_renderer is not None:
        texts = [t for t in project.texts() if project.item_end(t) > start and t["start"] < end]
        for k, t in enumerate(sorted(texts, key=lambda i: i["track"])):
            a = max(t["start"], start) - start
            d = project.item_end(t) - t["start"]
            if project.text_animated(t):
                # scritta animata: una sequenza di PNG, uno per fotogramma (quelli
                # uguali si copiano, così si disegna solo dove qualcosa si muove)
                nfr = int(math.ceil(d * fps)) + 2
                last_key, last_png = None, None
                for n in range(nfr):
                    png = os.path.join(workdir, f"testo_{k}_{n:05d}.png")
                    key = tuple(round(x, 3) for x in project.text_anim(t, min(n / fps, d)))
                    if key == last_key and last_png:
                        shutil.copyfile(last_png, png)
                    elif not text_renderer(t, W, H, png, u=min(n / fps, d)):
                        break
                    last_key, last_png = key, png
                else:
                    idx = n_inputs
                    n_inputs += 1
                    inputs += ["-framerate", f"{fps:g}", "-i", os.path.join(workdir, f"testo_{k}_%05d.png")]
                    png = None
                if png is not None:
                    continue
            else:
                png = os.path.join(workdir, f"testo_{k}.png")
                if not text_renderer(t, W, H, png):
                    continue
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
