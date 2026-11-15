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
import sys
import tempfile
import threading

from . import audio, color, orient, paths, stabilize
from . import speed as spd
from .media import parse_ffmpeg_time

FORMATS = {
    "mp4_h264": {"label": "MP4 · H.264 (per condividere)", "ext": ".mp4"},
    "mov_prores": {"label": "MOV · ProRes 422 (alta qualità)", "ext": ".mov"},
}
QUALITY = {"alta": 17, "buona": 20, "leggera": 24}
# velocità di codifica: la x264 più veloce fa file un poco più grandi a parità di qualità
ENCODE_SPEEDS = (("Veloce", "veryfast"), ("Equilibrata", "fast"), ("Compatta (più lenta)", "medium"))
DEFAULT_SPEED = "veryfast"
AUDIO_KBPS = 192
# codificatori della scheda video / del processore, per piattaforma (si provano prima di offrirli)
HW_CANDIDATES = {
    "darwin": [("h264_videotoolbox", "Acceleratore del Mac (VideoToolbox)")],
    "win32": [("h264_nvenc", "Scheda video NVIDIA (NVENC)"), ("h264_qsv", "Intel Quick Sync"),
              ("h264_amf", "Scheda video AMD (AMF)")],
    "linux": [("h264_nvenc", "Scheda video NVIDIA (NVENC)"), ("h264_qsv", "Intel Quick Sync")],
}
_HW_CACHE: list | None = None
_HW_LOCK = threading.Lock()


def estimate_video_kbps(fmt, quality, W, H, fps):
    """Bitrate video medio previsto (kbit/s). È una stima: dipende molto dal contenuto
    (un'inquadratura ferma pesa poco, una scena molto mossa molto di più)."""
    px = max(1, W * H) * float(fps)
    if fmt == "mov_prores":
        return 147000.0 * px / (1920 * 1080 * 25)          # ProRes 422: ~147 Mbit/s a 1080p25
    crf = QUALITY.get(quality, 20)
    bpp = 0.072 * 2 ** ((23 - crf) / 6)                      # ogni 6 punti di CRF il flusso raddoppia
    return max(150.0, px * bpp / 1000.0)


def estimate_size_bytes(fmt, video_kbps, duration):
    audio = 1536.0 if fmt == "mov_prores" else AUDIO_KBPS    # ProRes: audio non compresso
    return (video_kbps + audio) * 1000.0 / 8.0 * max(0.0, duration)


def video_args(encoder="libx264", speed=DEFAULT_SPEED, quality="alta", bitrate_kbps=None):
    """Opzioni FFmpeg del codificatore video H.264."""
    if encoder == "libx264":
        a = ["-c:v", "libx264", "-preset", speed]
        if bitrate_kbps:
            k = int(bitrate_kbps)
            return a + ["-b:v", f"{k}k", "-maxrate", f"{int(k * 1.6)}k", "-bufsize", f"{k * 3}k"]
        return a + ["-crf", str(QUALITY.get(quality, 18))]
    k = int(bitrate_kbps or 8000)
    rate = ["-b:v", f"{k}k", "-maxrate", f"{int(k * 1.5)}k", "-bufsize", f"{k * 2}k"]
    if encoder == "h264_videotoolbox":
        return ["-c:v", encoder, "-profile:v", "high"] + rate
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr"] + rate
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-preset", "medium"] + rate
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-quality", "balanced"] + rate
    return ["-c:v", encoder] + rate


def hw_encoders(force=False):
    """[(chiave, nome)] dei codificatori hardware che funzionano davvero su questo computer
    (si prova una codifica minuscola: compilato in FFmpeg non vuol dire disponibile)."""
    global _HW_CACHE
    with _HW_LOCK:
        if _HW_CACHE is not None and not force:
            return list(_HW_CACHE)
        out = []
        exe = paths.ffmpeg_exe()
        plat = "darwin" if sys.platform == "darwin" else "win32" if sys.platform.startswith("win") else "linux"
        if exe:
            try:
                listed = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                        timeout=15, **paths.no_window()).stdout
            except Exception:  # noqa: BLE001
                listed = ""
            for key, label in HW_CANDIDATES.get(plat, []):
                if key not in listed:
                    continue
                cmd = [exe, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=25:d=0.3",
                       "-pix_fmt", "yuv420p"] + video_args(key, bitrate_kbps=2000) + ["-f", "null", "-"]
                try:
                    r = subprocess.run(cmd, capture_output=True, timeout=20, **paths.no_window())
                    if r.returncode == 0:
                        out.append((key, label))
                except Exception:  # noqa: BLE001
                    pass
        _HW_CACHE = out
        return list(out)


# Modelli di esportazione. size: ("fisso", L, A) oppure ("altezza", A) = quell'altezza con
# la proporzione del progetto, oppure None = come il progetto. fill: il formato è diverso da
# quello del progetto → riempire il fotogramma (taglia i lati) invece di lasciare bande nere.
PRESETS = [
    {"key": "progetto", "group": "Generali", "label": "Come il progetto", "fmt": "mp4_h264", "size": None,
     "quality": "alta", "fps": None, "fill": False,
     "note": "Stessa dimensione e fotogrammi al secondo del progetto."},
    {"key": "fullhd", "group": "Generali", "label": "Full HD 1080p", "fmt": "mp4_h264", "size": ("altezza", 1080),
     "quality": "alta", "fps": None, "fill": False, "note": "MP4 H.264, 1080 righe."},
    {"key": "uhd4k", "group": "Generali", "label": "4K UHD 2160p", "fmt": "mp4_h264", "size": ("altezza", 2160),
     "quality": "alta", "fps": None, "fill": False, "note": "MP4 H.264, 2160 righe: file grande, esportazione lenta."},
    {"key": "hd720", "group": "Generali", "label": "HD 720p leggero", "fmt": "mp4_h264", "size": ("altezza", 720),
     "quality": "buona", "fps": None, "fill": False, "note": "File piccolo, per inviare o provare."},
    {"key": "prores", "group": "Generali", "label": "Master ProRes (archivio / altri programmi)",
     "fmt": "mov_prores", "size": None, "quality": "alta", "fps": None, "fill": False,
     "note": "MOV ProRes 422: qualità massima, file molto grande."},
    {"key": "yt1080", "group": "YouTube", "label": "YouTube · Full HD 1080p", "fmt": "mp4_h264",
     "size": ("fisso", 1920, 1080), "quality": "alta", "fps": None, "fill": False,
     "note": "16:9, 1920×1080."},
    {"key": "yt4k", "group": "YouTube", "label": "YouTube · 4K 2160p", "fmt": "mp4_h264",
     "size": ("fisso", 3840, 2160), "quality": "alta", "fps": None, "fill": False,
     "note": "16:9, 3840×2160."},
    {"key": "ytshorts", "group": "YouTube", "label": "YouTube Shorts (verticale 9:16)", "fmt": "mp4_h264",
     "size": ("fisso", 1080, 1920), "quality": "alta", "fps": None, "fill": True,
     "note": "Verticale 1080×1920, fino a 3 minuti."},
    {"key": "igreel", "group": "Instagram", "label": "Instagram · Reel / Storia (verticale 9:16)",
     "fmt": "mp4_h264", "size": ("fisso", 1080, 1920), "quality": "buona", "fps": None, "fill": True,
     "note": "Verticale 1080×1920."},
    {"key": "igpost45", "group": "Instagram", "label": "Instagram · Post verticale 4:5", "fmt": "mp4_h264",
     "size": ("fisso", 1080, 1350), "quality": "buona", "fps": None, "fill": True, "note": "1080×1350."},
    {"key": "igpost11", "group": "Instagram", "label": "Instagram · Post quadrato 1:1", "fmt": "mp4_h264",
     "size": ("fisso", 1080, 1080), "quality": "buona", "fps": None, "fill": True, "note": "1080×1080."},
    {"key": "tiktok", "group": "Altri social", "label": "TikTok (verticale 9:16)", "fmt": "mp4_h264",
     "size": ("fisso", 1080, 1920), "quality": "buona", "fps": None, "fill": True, "note": "Verticale 1080×1920."},
    {"key": "facebook", "group": "Altri social", "label": "Facebook · orizzontale 720p", "fmt": "mp4_h264",
     "size": ("fisso", 1280, 720), "quality": "buona", "fps": None, "fill": False, "note": "16:9, 1280×720."},
    {"key": "whatsapp", "group": "Altri social", "label": "WhatsApp / messaggi (leggero)", "fmt": "mp4_h264",
     "size": ("altezza", 540), "quality": "leggera", "fps": None, "fill": False,
     "note": "540 righe, file molto piccolo."},
]
_SUFFIX = {"fullhd": "FullHD", "uhd4k": "4K", "hd720": "720p", "prores": "ProRes", "yt1080": "YouTube-1080p",
           "yt4k": "YouTube-4K", "ytshorts": "YouTube-Shorts", "igreel": "Instagram-Reel",
           "igpost45": "Instagram-4x5", "igpost11": "Instagram-1x1", "tiktok": "TikTok",
           "facebook": "Facebook-720p", "whatsapp": "WhatsApp"}
for _p in PRESETS:
    _p["suffix"] = _SUFFIX.get(_p["key"], "")
PRESET_BY_KEY = {p["key"]: p for p in PRESETS}


def preset_size(preset, project_size):
    """(larghezza, altezza) pari risultante dal modello per quel progetto."""
    w, h = project_size
    sz = preset.get("size")
    if sz is None:
        out = (w, h)
    elif sz[0] == "fisso":
        out = (sz[1], sz[2])
    else:
        th = sz[1]
        out = (int(round(w * th / max(h, 1))), th)
    return out[0] - out[0] % 2, out[1] - out[1] % 2


def safe_name(text):
    """Nome adatto a un file (senza barre, due punti ecc.)."""
    out = "".join("_" if c in '\\/:*?"<>|' or ord(c) < 32 else c for c in str(text)).strip(" .")
    return out or "senza nome"


def chapters(project):
    """Capitoli dai segnaposti: [(nome, inizio, fine)]. Il primo parte da zero (se il
    primo segnaposto non è lì c'è un capitolo «Inizio»), l'ultimo arriva alla fine."""
    end = project.timeline_end()
    pts = [(float(m["t"]), (m.get("label") or "").strip()) for m in project.markers
           if project.is_chapter(m) and 0 <= m["t"] < end - 1e-6]
    pts.sort(key=lambda p: p[0])
    if not pts or pts[0][0] > 0.05:
        pts.insert(0, (0.0, "Inizio"))
    out = []
    for n, (t, name) in enumerate(pts):
        nxt = pts[n + 1][0] if n + 1 < len(pts) else end
        if nxt - t > 0.04:
            out.append((name or f"Capitolo {n + 1}", t, nxt))
    return out


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
              text_renderer=None, size=None, fps=None, fill=False, encoder="libx264", speed=DEFAULT_SPEED,
              bitrate_kbps=None):
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
    fps = float(fps or project.fps)
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
        reverse = spd.is_reversed(it)
        if reverse:
            # all'indietro si prende tutta la parte di file, la si rovescia e poi si
            # procede come per una clip normale partendo dall'inizio del pezzo
            lead = off
            cs = 0.0
            raw_t = src_t = min(max(0.0, float(it["src_in"])), max(0.0, sdur - 2 * fr))
            pre_pad = 0.0
            span = max(0.2, float(it["src_out"]) - float(it["src_in"]))
        else:
            raw_t = float(project.src_time(it, it["start"] + cs))
            src_t = min(max(0.0, raw_t), max(0.0, sdur - 2 * fr))
            pre_pad = 0.0
            if raw_t < 0 and not freeze:  # prima dell'inizio del file: primo fotogramma fermo
                pre_pad = -raw_t / max(spd.speed_at(it, it["start"] + cs), 1e-3)
            src_end = float(project.src_time(it, it["start"] + cs + lead + dur))
            span = 1.0 if freeze else max(0.2, min(src_end, sdur) - src_t + 0.5)
        inputs.extend(["-ss", f"{src_t:.6f}", "-t", f"{span:.6f}", "-i", src["path"]])
        zoom = float(it.get("zoom", 1.0) or 1.0)
        osrc = src
        if orient.active(it):
            ow, oh = orient.canvas(src.get("width") or W, src.get("height") or H, it)
            osrc = dict(src, width=ow, height=oh)
        if fill:   # formato diverso dal progetto: l'immagine riempie il fotogramma (taglia i lati)
            sw_, sh_ = osrc.get("width") or W, osrc.get("height") or H
            zoom *= max(W / sw_, H / sh_) / max(min(W / sw_, H / sh_), 1e-9)
        w1, h1 = _fit_size(osrc, W, H, zoom)
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
        of = orient.ffmpeg_filters(it, src.get("width") or W, src.get("height") or H, cs) if orient.active(it) else ""
        if reverse:
            # rovescia già alla dimensione finale e in yuv420p, per non riempire la memoria
            if orient.animated(it):   # la rotazione animata va applicata dopo, sul tempo del pezzo
                wp, hp = _fit_size(src, W, H, zoom)
            else:
                wp, hp = w1, h1
                if of:
                    c += of + ","
                    of = ""
            c += f"scale={wp}:{hp}:flags=bicubic,format=yuv420p,reverse,setpts=PTS-STARTPTS,"
        if freeze:
            c += f"trim=end_frame=1,setpts=PTS-STARTPTS,fps={fps:g}"
        else:
            c += f"setpts={_retime_expr(it, src_t, cs)},fps={fps:g}"
            if pre_pad > 0:
                c += f",tpad=start_mode=clone:start_duration={pre_pad:.6f}"
        if of:
            c += "," + of
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
        if w1 == W and h1 == H and abs(x) < 0.5 and abs(y) < 0.5:
            # il pezzo riempie già tutto il fotogramma: niente da sovrapporre allo sfondo nero
            f = f"[c{label}]null"
        else:
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
    store.ensure_all_clean(project)  # audio ripulito pronto prima del mix
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
        cmd += video_args(encoder, speed, quality, bitrate_kbps)
        cmd += ["-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-movflags", "+faststart"]
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
