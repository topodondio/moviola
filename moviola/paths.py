"""Percorsi di sistema: cartella cache, eseguibile FFmpeg, chiavi dei file."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

APP_NAME = "Moviola"


def internal_root() -> str:
    """Cartella di Moviola sul computer: impostazioni, preset, autosalvataggi,
    copie di sicurezza, registro degli errori (pochi KB)."""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def internal_dir(kind: str) -> str:
    path = os.path.join(internal_root(), kind)
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------- cache media
# proxy, audio decodificato, forme d'onda e miniature: possono stare su un
# disco esterno scelto nelle preferenze. Se quel disco non è collegato si usa
# per il momento una cache temporanea sul computer.
_MEDIA_CACHE = None  # cartella scelta dall'utente (None = sul computer)
MEDIA_KINDS = ("audio", "proxy", "miniature")


def set_media_cache(path):
    global _MEDIA_CACHE
    _MEDIA_CACHE = path or None


def media_cache_setting():
    return _MEDIA_CACHE


def media_cache_available() -> bool:
    if not _MEDIA_CACHE:
        return True
    # deve esistere già: così, se il disco esterno non è collegato, non si crea
    # per sbaglio una cartella con lo stesso nome sul disco del computer
    return os.path.isdir(_MEDIA_CACHE)


def cache_root() -> str:
    """Cartella della cache dei media attualmente in uso."""
    if _MEDIA_CACHE:
        try:
            if media_cache_available():
                if os.access(_MEDIA_CACHE, os.W_OK):
                    return _MEDIA_CACHE
        except OSError:
            pass
        return internal_dir("cache_temporanea")
    return internal_root()


def cache_dir(kind: str) -> str:
    path = os.path.join(cache_root(), kind)
    os.makedirs(path, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(internal_root(), "impostazioni.json")


def presets_dir() -> str:
    return internal_dir("preset_colore")


def file_key(path: str) -> str:
    """Impronta di un file originale: nome e dimensione. Non dipende dalla
    cartella né dal computer, così la cache su un disco esterno vale sia sul
    Mac sia sul PC; se il file viene modificato cambia la dimensione."""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = -1
    sig = f"{os.path.basename(path).lower()}|{size}"
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:20]


def touch(path: str):
    """Segna un file di cache come usato adesso (per la pulizia)."""
    try:
        os.utime(path, None)
    except OSError:
        pass


def cache_files(root=None):
    """(percorso, dimensione, ultimo uso) di tutti i file della cache dei media."""
    root = root or cache_root()
    out = []
    for kind in MEDIA_KINDS:
        d = os.path.join(root, kind)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            p = os.path.join(d, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if os.path.isfile(p):
                out.append((p, st.st_size, st.st_mtime))
    return out


def cache_usage(root=None) -> int:
    return sum(sz for _, sz, _ in cache_files(root))


def enforce_cache_limit(limit_bytes: int, keep=(), root=None) -> int:
    """Se la cache supera il limite, elimina i file usati meno di recente
    (mai quelli in keep, cioè del progetto aperto). Restituisce i byte liberati."""
    files = cache_files(root)
    total = sum(sz for _, sz, _ in files)
    if limit_bytes <= 0 or total <= limit_bytes:
        return 0
    keep = {os.path.abspath(k) for k in keep}
    freed = 0
    for p, sz, _ in sorted(files, key=lambda f: f[2]):
        if total - freed <= limit_bytes * 0.9:  # un po' di margine per non ripulire di continuo
            break
        if os.path.abspath(p) in keep or p.endswith(".part") or ".part." in p:
            continue
        try:
            os.remove(p)
            freed += sz
        except OSError:
            pass
    return freed


def clear_cache(keep=(), root=None) -> int:
    keep = {os.path.abspath(k) for k in keep}
    freed = 0
    for p, sz, _ in cache_files(root):
        if os.path.abspath(p) in keep:
            continue
        try:
            os.remove(p)
            freed += sz
        except OSError:
            pass
    return freed


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


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
