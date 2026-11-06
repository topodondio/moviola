"""Percorsi di sistema: cartella cache, eseguibile FFmpeg, chiavi dei file."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

APP_NAME = "Moviola"


def cache_root() -> str:
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def cache_dir(kind: str) -> str:
    path = os.path.join(cache_root(), kind)
    os.makedirs(path, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(cache_root(), "impostazioni.json")


def presets_dir() -> str:
    return cache_dir("preset_colore")


def file_key(path: str) -> str:
    """Impronta di un file originale: cambia se il file viene modificato."""
    try:
        st = os.stat(path)
        sig = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = os.path.abspath(path)
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:20]


_FFMPEG = None


def ffmpeg_exe() -> str | None:
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    try:
        import imageio_ffmpeg  # FFmpeg incluso nel pacchetto, nessuna installazione a parte

        _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        _FFMPEG = shutil.which("ffmpeg")
    return _FFMPEG


def ffmpeg_version() -> tuple[int, int]:
    exe = ffmpeg_exe()
    if not exe:
        return (0, 0)
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10,
                             **no_window()).stdout
        tok = out.split()[2]  # "ffmpeg version 6.1.1-..."
        digits = "".join(ch if (ch.isdigit() or ch == ".") else " " for ch in tok).split()
        parts = digits[0].split(".") if digits else ["0"]
        major = int(parts[0]) if parts[0] else 0
        minor = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        if major > 100:  # build git ("N-12345-...")
            return (7, 0)
        return (major, minor)
    except Exception:
        return (0, 0)


def no_window() -> dict:
    """Evita che su Windows si apra una finestra nera per ogni processo."""
    if os.name == "nt":
        return {"creationflags": 0x08000000}
    return {}
