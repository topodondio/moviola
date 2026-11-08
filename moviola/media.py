"""Analisi dei file e lavori in background (audio decodificato, forme
d'onda, miniature, proxy). I file originali non vengono mai modificati:
tutto ciò che viene generato finisce nella cartella cache."""
from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import audio, paths

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".mxf", ".webm", ".mpg", ".mpeg",
             ".3gp", ".wmv", ".dv", ".mod", ".tod"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff", ".ogg", ".opus", ".wma"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT

PROXY_HEIGHT = 540


def is_media(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in MEDIA_EXT


def probe(path: str) -> dict:
    """Durata, fotogrammi al secondo, dimensioni, presenza di audio/video."""
    import av

    info = {"duration": 0.0, "fps": 25.0, "width": 0, "height": 0, "has_video": False, "has_audio": False,
            "codec": ""}
    with av.open(path) as c:
        dur = float(c.duration / 1_000_000) if c.duration else 0.0
        vs = next((s for s in c.streams.video), None)
        as_ = next((s for s in c.streams.audio), None)
        audio_only_ext = os.path.splitext(path)[1].lower() in AUDIO_EXT  # copertine degli mp3: non sono video
        if vs is not None and not audio_only_ext:
            info["has_video"] = True
            info["width"] = vs.codec_context.width
            info["height"] = vs.codec_context.height
            rate = vs.average_rate or vs.guessed_rate or vs.base_rate
            info["fps"] = float(rate) if rate else 25.0
            info["codec"] = vs.codec_context.name
            if vs.duration and vs.time_base:
                dur = max(dur, float(vs.duration * vs.time_base))
        if as_ is not None:
            info["has_audio"] = True
            if not dur and as_.duration and as_.time_base:
                dur = float(as_.duration * as_.time_base)
        info["duration"] = dur
    if not info["has_video"] and not info["has_audio"]:
        raise ValueError("il file non contiene video né audio")
    return info


# ----------------------------------------------------------------- cache
def audio_cache_path(src_path: str) -> str:
    return os.path.join(paths.cache_dir("audio"), paths.file_key(src_path) + ".s16")


def peaks_path(src_path: str) -> str:
    return os.path.join(paths.cache_dir("audio"), paths.file_key(src_path) + ".peaks.npy")


def thumb_path(src_path: str) -> str:
    return os.path.join(paths.cache_dir("miniature"), paths.file_key(src_path) + ".jpg")


def proxy_path(src_path: str) -> str:
    return os.path.join(paths.cache_dir("proxy"), paths.file_key(src_path) + ".mov")


def needs_proxy(info: dict) -> bool:
    """Proxy consigliato per file pesanti da decodificare su un portatile."""
    if not info.get("has_video"):
        return False
    heavy_codec = info.get("codec", "") in {"hevc", "h265", "av1", "vp9", "prores", "dnxhd"}
    big = (info.get("height") or 0) > 1100 or (info.get("fps") or 0) > 31
    return heavy_codec or big


def _run(cmd, timeout=None, progress=None, duration=0.0, cancel=None):
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            errors="replace", **paths.no_window())
    tail = []
    for line in proc.stderr:
        tail.append(line)
        tail = tail[-30:]
        if cancel is not None and cancel.is_set():
            proc.kill()
            break
        if progress and duration > 0 and "time=" in line:
            t = parse_ffmpeg_time(line)
            if t is not None:
                progress(min(1.0, t / duration))
    proc.wait()
    if proc.returncode != 0 and not (cancel is not None and cancel.is_set()):
        raise RuntimeError("FFmpeg: " + "".join(tail[-6:]))


def parse_ffmpeg_time(line: str):
    i = line.rfind("time=")
    if i < 0:
        return None
    tok = line[i + 5:].split()[0]
    try:
        h, m, s = tok.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return None


def make_audio_cache(src_path: str) -> str:
    out = audio_cache_path(src_path)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        paths.touch(out)
        return out
    exe = paths.ffmpeg_exe()
    tmp = out + ".part"
    _run([exe, "-y", "-v", "error", "-i", src_path, "-vn", "-ac", str(audio.CHANNELS), "-ar", str(audio.RATE),
          "-f", "s16le", "-acodec", "pcm_s16le", tmp])
    os.replace(tmp, out)
    return out


def make_peaks(src_path: str) -> np.ndarray:
    p = peaks_path(src_path)
    if os.path.exists(p):
        paths.touch(p)
        try:
            return np.load(p)
        except Exception:
            pass
    raw_p = audio_cache_path(src_path)
    n = os.path.getsize(raw_p) // (2 * audio.CHANNELS)
    raw = np.memmap(raw_p, dtype=np.int16, mode="r", shape=(n, audio.CHANNELS))
    pk = audio.peaks(raw)
    np.save(p, pk)
    return pk


def make_thumb(src_path: str, at: float = 1.0) -> str:
    out = thumb_path(src_path)
    if os.path.exists(out):
        paths.touch(out)
        return out
    exe = paths.ffmpeg_exe()
    cmd = [exe, "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", src_path, "-frames:v", "1",
           "-vf", "scale=160:-2", "-q:v", "4", out]
    try:
        _run(cmd)
    except RuntimeError:
        cmd[5] = "0"
        _run(cmd)
    return out


def make_proxy(src_path: str, duration: float, progress=None, cancel=None) -> str:
    """Copia leggera per il montaggio (MJPEG 540p, solo fotogrammi chiave:
    si decodifica velocemente anche su processori datati)."""
    out = proxy_path(src_path)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        paths.touch(out)
        return out
    exe = paths.ffmpeg_exe()
    tmp = out + ".part.mov"
    _run([exe, "-y", "-v", "info", "-stats_period", "0.5", "-i", src_path, "-an",
          "-vf", f"scale=-2:{PROXY_HEIGHT}:flags=bicubic", "-c:v", "mjpeg", "-q:v", "4", "-pix_fmt", "yuvj420p",
          tmp], progress=progress, duration=duration, cancel=cancel)
    if cancel is not None and cancel.is_set():
        try:
            os.remove(tmp)
        except OSError:
            pass
        return ""
    os.replace(tmp, out)
    return out


class JobRunner:
    """Esegue i lavori lunghi in background, senza bloccare l'interfaccia."""

    def __init__(self, workers: int = 2):
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.proxy_pool = ThreadPoolExecutor(max_workers=1)
        self.cancel = threading.Event()

    def submit(self, fn, *args, **kw):
        return self.pool.submit(fn, *args, **kw)

    def submit_proxy(self, fn, *args, **kw):
        return self.proxy_pool.submit(fn, *args, **kw)

    def shutdown(self):
        self.cancel.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.proxy_pool.shutdown(wait=False, cancel_futures=True)
