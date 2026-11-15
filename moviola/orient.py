"""Specchio e rotazione di una clip video. Si applicano prima di zoom e posizione,
con le stesse regole in anteprima e in esportazione: prima lo specchio, poi la
rotazione oraria (in gradi) con angoli vuoti neri e dimensioni che contengono tutta
l'immagine ruotata."""
from __future__ import annotations

import math

import numpy as np


def normalize(rot) -> float:
    r = float(rot or 0.0) % 360.0
    return r - 360.0 if r > 180.0 else r


def quarter(rot) -> int | None:
    """0..3 se l'angolo è un multiplo di 90° (in senso orario), altrimenti None."""
    r = normalize(rot)
    k = round(r / 90.0)
    return int(k) % 4 if abs(r - 90.0 * k) < 1e-6 else None


def animated(it) -> bool:
    return bool(it.get("rotate_anim"))


def angle_at(it, u) -> float:
    """Angolo (gradi, orario) del pezzo al tempo u dall'inizio del pezzo."""
    pts = it.get("rotate_anim")
    if pts:
        from .color import value_at
        return float(value_at(pts, u))
    return float(it.get("rotate", 0.0) or 0.0)


def active(it) -> bool:
    return bool(it.get("flip_h") or it.get("flip_v") or animated(it)
                or quarter(it.get("rotate", 0.0)) != 0)


def canvas(w, h, it) -> tuple[int, int]:
    """Dimensioni dell'immagine dopo la rotazione. Con la rotazione animata la tela
    è fissa (la più grande tra tutti gli angoli del percorso), così l'inquadratura
    non pulsa."""
    pts = it.get("rotate_anim")
    if not pts:
        return dims(w, h, it.get("rotate", 0.0))
    vals = [float(p[1]) for p in pts]
    lo, hi = min(vals), max(vals)
    cand = {lo, hi}
    if hi - lo >= 1.0:
        n = int(min(720, max(2, math.ceil(hi - lo))))
        cand.update(lo + (hi - lo) * i / n for i in range(n + 1))
    best_w = best_h = 0
    for a in cand:
        cw, ch = dims(w, h, a)
        best_w, best_h = max(best_w, cw), max(best_h, ch)
    return best_w, best_h


def dims(w, h, rot) -> tuple[int, int]:
    """Dimensioni dell'immagine w×h dopo la rotazione."""
    q = quarter(rot)
    if q is not None:
        return (h, w) if q % 2 else (w, h)
    a = math.radians(normalize(rot))
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return int(math.ceil(w * c + h * s - 1e-6)), int(math.ceil(w * s + h * c - 1e-6))


def apply(img: np.ndarray, it, u: float = 0.0) -> np.ndarray:
    if it.get("flip_h"):
        img = img[:, ::-1]
    if it.get("flip_v"):
        img = img[::-1]
    rot = angle_at(it, u)
    q = quarter(rot)
    if q is not None and not animated(it):
        if q:
            img = np.rot90(img, -q)   # rot90 gira in senso antiorario
        return np.ascontiguousarray(img)
    from PySide6.QtGui import QColor, QImage, QPainter

    img = np.ascontiguousarray(img)
    h, w = img.shape[:2]
    ow, oh = canvas(w, h, it)
    src = QImage(img.data, w, h, 3 * w, QImage.Format_RGB888)
    out = QImage(ow, oh, QImage.Format_RGB32)
    out.fill(QColor(0, 0, 0))
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.translate(ow / 2, oh / 2)
    p.rotate(normalize(rot))
    p.translate(-w / 2, -h / 2)
    p.drawImage(0, 0, src)
    p.end()
    buf = np.frombuffer(out.constBits(), np.uint8, count=oh * out.bytesPerLine())
    bgra = buf.reshape(oh, out.bytesPerLine())[:, :ow * 4].reshape(oh, ow, 4)
    return np.ascontiguousarray(bgra[:, :, 2::-1])


def ffmpeg_filters(it, w, h, cs: float = 0.0) -> str:
    """Filtri FFmpeg equivalenti, da mettere prima di scale; vuoto se non serve."""
    f = []
    if it.get("flip_h"):
        f.append("hflip")
    if it.get("flip_v"):
        f.append("vflip")
    rot = it.get("rotate", 0.0)
    q = quarter(rot)
    if animated(it):
        pts = sorted((float(p[0]), float(p[1])) for p in it["rotate_anim"])
        u = f"(t+{cs:.6f})"
        e = f"{pts[-1][1]:.6f}"
        for (t0, v0), (t1, v1) in reversed(list(zip(pts[:-1], pts[1:]))):
            if t1 - t0 > 1e-9:
                e = f"if(lt({u}\\,{t1:.6f})\\,{v0:.6f}+({v1 - v0:.6f})*({u}-{t0:.6f})/{t1 - t0:.6f}\\,{e})"
        e = f"if(lt({u}\\,{pts[0][0]:.6f})\\,{pts[0][1]:.6f}\\,{e})" if len(pts) > 1 else e
        ow, oh = canvas(w, h, it)
        f.append(f"rotate='({e})*PI/180':ow={ow}:oh={oh}:c=black:bilinear=1")
    elif q == 1:
        f.append("transpose=1")
    elif q == 2:
        f += ["hflip", "vflip"]
    elif q == 3:
        f.append("transpose=2")
    elif q is None:
        ow, oh = dims(w, h, rot)
        f.append(f"rotate={math.radians(normalize(rot)):.8f}:ow={ow}:oh={oh}:c=black:bilinear=1")
    return ",".join(f)
