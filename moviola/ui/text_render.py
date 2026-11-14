"""Disegno delle scritte: la stessa funzione serve all'anteprima e all'export,
così il risultato finale coincide con quello che si vede."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter, QPainterPath,
                           QPen)

from ..project import Project

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
    sp = float(item.get("spacing", 0.0) or 0.0)
    if sp:
        f.setLetterSpacing(QFont.AbsoluteSpacing, px * sp / 100.0)
    return f


def _lines(item):
    text = item.get("text") or ""
    return (text.upper() if item.get("caps") else text).split("\n") or [""]


def _layout(item, frame: QRectF):
    px = float(item.get("size", 8.0)) / 100.0 * frame.height()
    font = _font(item, px)
    fm = QFontMetricsF(font)
    lines = _lines(item)
    widths = [fm.horizontalAdvance(line) for line in lines]
    lh = fm.lineSpacing()
    bw = max(widths) if widths else 0.0
    bh = lh * len(lines)
    cx = frame.left() + float(item.get("pos_x", 0.5)) * frame.width()
    cy = frame.top() + float(item.get("pos_y", 0.5)) * frame.height()
    box = QRectF(cx - bw / 2, cy - bh / 2, bw, bh)
    return px, font, fm, lines, widths, lh, box


def shape_rect(item, frame: QRectF) -> QRectF:
    w = max(0.0, float(item.get("width", 0.6))) * frame.width()
    h = max(0.0, float(item.get("height", 0.16))) * frame.height()
    cx = frame.left() + float(item.get("pos_x", 0.5)) * frame.width()
    cy = frame.top() + float(item.get("pos_y", 0.5)) * frame.height()
    return QRectF(cx - w / 2, cy - h / 2, w, h)


def draw_shape(p: QPainter, frame: QRectF, item, opacity: float = 1.0):
    a = max(0.0, min(1.0, float(item.get("opacity", 1.0)))) * opacity
    if a <= 0.001:
        return
    r = shape_rect(item, frame)
    c = QColor(item.get("color", "#000000"))
    c.setAlphaF(a)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    rad = max(0.0, float(item.get("radius", 0.0))) * min(r.width(), r.height()) / 2
    if rad > 0.5:
        p.drawRoundedRect(r, rad, rad)
    else:
        p.drawRect(r)
    p.restore()


def draw_overlay(p: QPainter, frame: QRectF, item, opacity: float = 1.0):
    if item.get("kind") == "shape":
        draw_shape(p, frame, item, opacity)
    else:
        draw_text(p, frame, item, opacity)


def text_box(item, frame: QRectF) -> QRectF:
    """Rettangolo occupato dalla scritta o dal riquadro (per selezionarlo col mouse)."""
    if item.get("kind") == "shape":
        return shape_rect(item, frame)
    px, _, _, _, _, _, box = _layout(item, frame)
    pad = px * 0.3
    return box.adjusted(-pad, -pad * 0.6, pad, pad * 0.6)


def draw_text(p: QPainter, frame: QRectF, item, opacity: float = 1.0):
    opacity *= max(0.0, min(1.0, float(item.get("opacity", 1.0))))
    u = item.get("_u")  # secondi dall'inizio della scritta (per le animazioni)
    dx = dy = 0.0
    sc, chars = 1.0, 1.0
    if u is not None and Project.text_animated(item):
        a, dx, dy, sc, chars = Project.text_anim(item, float(u))
        opacity *= a
    if opacity <= 0.001 or not (item.get("text") or "").strip():
        return
    px, font, fm, lines, widths, lh, box = _layout(item, frame)
    p.save()
    p.setOpacity(max(0.0, min(1.0, opacity)))
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    if dx or dy or sc != 1.0:
        c = box.center()
        p.translate(c.x() + dx * frame.height(), c.y() + dy * frame.height())
        p.scale(sc, sc)
        p.translate(-c.x(), -c.y())
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
    glow = bool(item.get("glow"))
    outline = bool(item.get("outline"))
    off = max(1.0, px * 0.05)
    col = QColor(item.get("color", "#ffffff"))
    # macchina da scrivere: quante lettere mostrare (il testo intero decide l'ingombro)
    total = sum(len(x) for x in lines)
    left = int(round(chars * total)) if chars < 1.0 else total
    for i, line in enumerate(lines):
        w = widths[i]
        if align == "left":
            x = box.left()
        elif align == "right":
            x = box.right() - w
        else:
            x = box.center().x() - w / 2
        y = box.top() + i * lh + fm.ascent()
        shown = line[:max(0, left)]
        left -= len(line)
        if not shown:
            continue
        path = None
        if glow or outline:
            path = QPainterPath()
            path.addText(QPointF(x, y), font, shown)
        if glow:
            gc = QColor(item.get("glow_color", "#e8a33d"))
            amt = max(0.0, min(1.0, float(item.get("glow_amt", 0.6))))
            steps = 7
            for k in range(steps, 0, -1):
                gc.setAlphaF(min(1.0, amt * 0.35 * (1.0 - (k - 1) / steps) ** 1.5))
                p.strokePath(path, QPen(gc, px * 0.07 * k, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if shadow:
            p.setPen(QColor(0, 0, 0, 150))
            p.drawText(QPointF(x + off, y + off), shown)
        if outline:
            oc = QColor(item.get("outline_color", "#000000"))
            ow = max(0.005, float(item.get("outline_w", 0.06))) * px
            p.strokePath(path, QPen(oc, ow * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setPen(col)
        p.drawText(QPointF(x, y), shown)
    p.restore()


def render_png(item, W: int, H: int, path: str, u=None) -> bool:
    """Scritta su un PNG trasparente grande quanto il fotogramma (per l'export).
    u: secondi dall'inizio della scritta, per le animazioni."""
    if u is not None:
        item = dict(item, _u=u)
    img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    draw_overlay(p, QRectF(0, 0, W, H), item, 1.0)
    p.end()
    return img.save(path, "PNG")
