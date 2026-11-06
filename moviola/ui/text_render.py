"""Disegno delle scritte: la stessa funzione serve all'anteprima e all'export,
così il risultato finale coincide con quello che si vede."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter

_DEFAULT_FAMILY = None


def default_family() -> str:
    global _DEFAULT_FAMILY
    if _DEFAULT_FAMILY is None:
        _DEFAULT_FAMILY = QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()
    return _DEFAULT_FAMILY


def _font(item, px):
    f = QFont(item.get("font") or default_family())
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(bool(item.get("bold", True)))
    f.setItalic(bool(item.get("italic", False)))
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def _layout(item, frame: QRectF):
    px = float(item.get("size", 8.0)) / 100.0 * frame.height()
    font = _font(item, px)
    fm = QFontMetricsF(font)
    lines = (item.get("text") or "").split("\n") or [""]
    widths = [fm.horizontalAdvance(line) for line in lines]
    lh = fm.lineSpacing()
    bw = max(widths) if widths else 0.0
    bh = lh * len(lines)
    cx = frame.left() + float(item.get("pos_x", 0.5)) * frame.width()
    cy = frame.top() + float(item.get("pos_y", 0.5)) * frame.height()
    box = QRectF(cx - bw / 2, cy - bh / 2, bw, bh)
    return px, font, fm, lines, widths, lh, box


def text_box(item, frame: QRectF) -> QRectF:
    """Rettangolo occupato dalla scritta (per selezionarla col mouse)."""
    px, _, _, _, _, _, box = _layout(item, frame)
    pad = px * 0.3
    return box.adjusted(-pad, -pad * 0.6, pad, pad * 0.6)


def draw_text(p: QPainter, frame: QRectF, item, opacity: float = 1.0):
    if opacity <= 0.001 or not (item.get("text") or "").strip():
        return
    px, font, fm, lines, widths, lh, box = _layout(item, frame)
    p.save()
    p.setOpacity(max(0.0, min(1.0, opacity)))
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    if item.get("box"):
        c = QColor(item.get("box_color", "#000000"))
        c.setAlphaF(max(0.0, min(1.0, float(item.get("box_opacity", 0.5)))))
        pad = px * 0.3
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(box.adjusted(-pad, -pad * 0.6, pad, pad * 0.6), pad * 0.5, pad * 0.5)
    p.setFont(font)
    align = item.get("align", "center")
    shadow = bool(item.get("shadow", True))
    off = max(1.0, px * 0.05)
    col = QColor(item.get("color", "#ffffff"))
    for i, line in enumerate(lines):
        w = widths[i]
        if align == "left":
            x = box.left()
        elif align == "right":
            x = box.right() - w
        else:
            x = box.center().x() - w / 2
        y = box.top() + i * lh + fm.ascent()
        if shadow:
            p.setPen(QColor(0, 0, 0, 150))
            p.drawText(QPointF(x + off, y + off), line)
        p.setPen(col)
        p.drawText(QPointF(x, y), line)
    p.restore()


def render_png(item, W: int, H: int, path: str) -> bool:
    """Scritta su un PNG trasparente grande quanto il fotogramma (per l'export)."""
    img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    draw_text(p, QRectF(0, 0, W, H), item, 1.0)
    p.end()
    return img.save(path, "PNG")
