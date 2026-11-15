"""Guide di composizione sopra ai visori: regola dei terzi, rapporto aureo, diagonali…
e maschera di ritaglio per i formati social (bande nere semitrasparenti). Servono solo
per guardare: non entrano nell'esportazione."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

PHI = (1 + math.sqrt(5)) / 2

GRIDS = [
    ("thirds", "Regola dei terzi"),
    ("golden", "Rapporto aureo (linee 38 / 62)"),
    ("spiral", "Spirale aurea"),
    ("diag", "Diagonali"),
    ("harmony", "Diagonali d'armonia (triangoli)"),
    ("center", "Croce al centro"),
    ("safe", "Margini sicuri (titoli 90% / 80%)"),
]
# (chiave, testo, (larghezza, altezza)) — gli stessi formati dei preset di esportazione
CROPS = [
    ("9:16", "Verticale 9:16 · Reel, Storie, TikTok, Shorts", (9, 16)),
    ("4:5", "Post Instagram 4:5", (4, 5)),
    ("1:1", "Quadrato 1:1", (1, 1)),
    ("16:9", "Orizzontale 16:9", (16, 9)),
    ("4:3", "Classico 4:3", (4, 3)),
]
CROP_RATIO = {k: r for k, _t, r in CROPS}


def default_guides():
    return {"grids": [], "crop": None}


def crop_rect(fr: QRectF, ratio) -> QRectF:
    """Parte del fotogramma che resta visibile esportando con quel formato
    (riempie il formato e taglia l'eccesso al centro, come l'esportazione)."""
    if not ratio:
        return QRectF(fr)
    a = ratio[0] / ratio[1]
    fa = fr.width() / max(fr.height(), 1e-9)
    if a < fa:
        w = fr.height() * a
        return QRectF(fr.center().x() - w / 2, fr.top(), w, fr.height())
    h = fr.width() / a
    return QRectF(fr.left(), fr.center().y() - h / 2, fr.width(), h)


def _line(p, r, x0, y0, x1, y1):
    p.drawLine(QPointF(r.left() + x0 * r.width(), r.top() + y0 * r.height()),
               QPointF(r.left() + x1 * r.width(), r.top() + y1 * r.height()))


def _spiral_path(r: QRectF) -> QPainterPath:
    """Spirale aurea in un rettangolo 1:φ, poi adattata (stirata) al rettangolo r.
    Ogni passo toglie un quadrato (a sinistra, in basso, a destra, in alto) e ci
    disegna dentro un quarto di cerchio."""
    path = QPainterPath()
    x, y, w, h = 0.0, 0.0, PHI, 1.0
    first = True
    for i in range(10):
        d = i % 4
        if d == 0:
            s, c, a0 = h, (x + h, y), 180
            x, w = x + s, w - s
        elif d == 1:
            s, c, a0 = w, (x, y + h - w), 90
            h -= s
        elif d == 2:
            s, c, a0 = h, (x + w - h, y + h), 0
            w -= s
        else:
            s, c, a0 = w, (x + w, y + w), -90
            y, h = y + s, h - s
        for k in range(0, 25):
            ang = math.radians(a0 - 90 * k / 24)
            px, py = c[0] + s * math.cos(ang), c[1] + s * math.sin(ang)
            q = QPointF(r.left() + px / PHI * r.width(), r.top() + py * r.height())
            if first:
                path.moveTo(q)
                first = False
            else:
                path.lineTo(q)
    return path


def draw_guides(p, frame: QRectF, guides: dict):
    """Disegna le guide dentro il fotogramma `frame` (coordinate del visore)."""
    if not guides:
        return
    ratio = CROP_RATIO.get(guides.get("crop") or "")
    area = crop_rect(frame, ratio) if ratio else QRectF(frame)
    p.save()
    p.setClipRect(frame)
    if ratio:                                   # bande nere semitrasparenti fuori dal ritaglio
        shade = QColor(0, 0, 0, 165)
        if area.left() > frame.left() + 0.5:
            p.fillRect(QRectF(frame.left(), frame.top(), area.left() - frame.left(), frame.height()), shade)
            p.fillRect(QRectF(area.right(), frame.top(), frame.right() - area.right(), frame.height()), shade)
        if area.top() > frame.top() + 0.5:
            p.fillRect(QRectF(frame.left(), frame.top(), frame.width(), area.top() - frame.top()), shade)
            p.fillRect(QRectF(frame.left(), area.bottom(), frame.width(), frame.bottom() - area.bottom()), shade)
        p.setPen(QPen(QColor(232, 163, 61, 220), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(area)
    grids = set(guides.get("grids") or ())
    p.setClipRect(area)
    p.setBrush(Qt.NoBrush)
    soft = QPen(QColor(255, 255, 255, 190), 1)
    soft.setCosmetic(True)
    p.setPen(soft)
    if "thirds" in grids:
        for f in (1 / 3, 2 / 3):
            _line(p, area, f, 0, f, 1)
            _line(p, area, 0, f, 1, f)
    if "golden" in grids:
        for f in (1 / (1 + PHI), PHI / (1 + PHI)):
            _line(p, area, f, 0, f, 1)
            _line(p, area, 0, f, 1, f)
    if "center" in grids:
        _line(p, area, 0.5, 0, 0.5, 1)
        _line(p, area, 0, 0.5, 1, 0.5)
    if "diag" in grids:
        _line(p, area, 0, 0, 1, 1)
        _line(p, area, 1, 0, 0, 1)
    if "harmony" in grids:
        # diagonale principale + perpendicolari dagli angoli opposti (triangoli armonici)
        w, h = area.width(), area.height()
        _line(p, area, 0, 1, 1, 0)
        fx, fy = (h * h) / (w * w + h * h), (w * w) / (w * w + h * h)
        _line(p, area, 0, 0, fx, fy)
        _line(p, area, 1, 1, 1 - fx, 1 - fy)
    if "spiral" in grids:
        sp = QPen(QColor(232, 163, 61, 230), 1.6)
        p.setPen(sp)
        p.drawPath(_spiral_path(area))
    if "safe" in grids:
        dash = QPen(QColor(120, 200, 255, 200), 1, Qt.DashLine)
        p.setPen(dash)
        for f in (0.9, 0.8):
            m = (1 - f) / 2
            p.drawRect(QRectF(area.left() + m * area.width(), area.top() + m * area.height(),
                              f * area.width(), f * area.height()))
    p.restore()
