"""Icone disegnate al volo (bianche, nitide su schermi Retina): nessun file
immagine da distribuire."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

WHITE = QColor(235, 235, 235)
DISABLED = QColor(105, 105, 105)


def _arrow_head(p: QPainter, tip: QPointF, angle: float, size: float):
    a1 = angle + math.radians(150)
    a2 = angle - math.radians(150)
    path = QPainterPath(tip)
    path.lineTo(tip.x() + size * math.cos(a1), tip.y() + size * math.sin(a1))
    path.lineTo(tip.x() + size * math.cos(a2), tip.y() + size * math.sin(a2))
    path.closeSubpath()
    p.fillPath(path, p.pen().color())


def _draw(name: str, p: QPainter, s: float, c: QColor):
    pen = QPen(c, s * 0.085, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    m = s * 0.16
    if name in ("undo", "redo"):
        path = QPainterPath()
        if name == "undo":
            path.moveTo(s * 0.3, s * 0.38)
            path.cubicTo(s * 0.5, s * 0.22, s * 0.86, s * 0.3, s * 0.8, s * 0.6)
            path.cubicTo(s * 0.76, s * 0.76, s * 0.6, s * 0.8, s * 0.5, s * 0.8)
            p.drawPath(path)
            _arrow_head(p, QPointF(s * 0.18, s * 0.48), math.radians(-150), s * 0.24)
        else:
            path.moveTo(s * 0.7, s * 0.38)
            path.cubicTo(s * 0.5, s * 0.22, s * 0.14, s * 0.3, s * 0.2, s * 0.6)
            path.cubicTo(s * 0.24, s * 0.76, s * 0.4, s * 0.8, s * 0.5, s * 0.8)
            p.drawPath(path)
            _arrow_head(p, QPointF(s * 0.82, s * 0.48), math.radians(-30), s * 0.24)
    elif name == "new":
        path = QPainterPath()
        path.moveTo(s * 0.25, m)
        path.lineTo(s * 0.6, m)
        path.lineTo(s * 0.78, s * 0.34)
        path.lineTo(s * 0.78, s - m)
        path.lineTo(s * 0.25, s - m)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(s * 0.6, m), QPointF(s * 0.6, s * 0.34))
        p.drawLine(QPointF(s * 0.6, s * 0.34), QPointF(s * 0.78, s * 0.34))
        p.drawLine(QPointF(s * 0.38, s * 0.6), QPointF(s * 0.64, s * 0.6))
        p.drawLine(QPointF(s * 0.51, s * 0.47), QPointF(s * 0.51, s * 0.73))
    elif name == "open":
        path = QPainterPath()
        path.moveTo(m, s * 0.78)
        path.lineTo(m, s * 0.24)
        path.lineTo(s * 0.4, s * 0.24)
        path.lineTo(s * 0.48, s * 0.33)
        path.lineTo(s * 0.8, s * 0.33)
        path.lineTo(s * 0.8, s * 0.44)
        p.drawPath(path)
        path = QPainterPath()
        path.moveTo(m, s * 0.78)
        path.lineTo(s * 0.28, s * 0.46)
        path.lineTo(s - m * 0.6, s * 0.46)
        path.lineTo(s * 0.72, s * 0.78)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "save":
        r = QRectF(m, m, s - 2 * m, s - 2 * m)
        p.drawRoundedRect(r, s * 0.06, s * 0.06)
        p.drawRect(QRectF(s * 0.32, m, s * 0.36, s * 0.24))
        p.drawRect(QRectF(s * 0.28, s * 0.56, s * 0.44, s * 0.28))
    elif name == "recent":
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.34, s * 0.34)
        p.drawLine(QPointF(s / 2, s / 2), QPointF(s / 2, s * 0.3))
        p.drawLine(QPointF(s / 2, s / 2), QPointF(s * 0.66, s * 0.6))
    elif name == "import":
        p.drawLine(QPointF(s / 2, m), QPointF(s / 2, s * 0.6))
        _arrow_head(p, QPointF(s / 2, s * 0.66), math.radians(90), s * 0.26)
        path = QPainterPath()
        path.moveTo(m, s * 0.62)
        path.lineTo(m, s - m)
        path.lineTo(s - m, s - m)
        path.lineTo(s - m, s * 0.62)
        p.drawPath(path)
    elif name == "export":
        p.drawLine(QPointF(s / 2, s * 0.68), QPointF(s / 2, s * 0.24))
        _arrow_head(p, QPointF(s / 2, m), math.radians(-90), s * 0.26)
        path = QPainterPath()
        path.moveTo(m, s * 0.62)
        path.lineTo(m, s - m)
        path.lineTo(s - m, s - m)
        path.lineTo(s - m, s * 0.62)
        p.drawPath(path)
    elif name == "settings":
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.14, s * 0.14)
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(s / 2 + math.cos(a) * s * 0.24, s / 2 + math.sin(a) * s * 0.24),
                       QPointF(s / 2 + math.cos(a) * s * 0.36, s / 2 + math.sin(a) * s * 0.36))
    elif name == "sidebar":
        p.drawRoundedRect(QRectF(m, s * 0.2, s - 2 * m, s * 0.6), s * 0.06, s * 0.06)
        p.drawLine(QPointF(s * 0.4, s * 0.2), QPointF(s * 0.4, s * 0.8))
        p.setBrush(QColor(c.red(), c.green(), c.blue(), 110))
        p.drawRect(QRectF(m, s * 0.2, s * 0.4 - m, s * 0.6))
    elif name == "ripple":
        p.drawRect(QRectF(m, s * 0.34, s * 0.26, s * 0.32))
        p.drawRect(QRectF(s * 0.58, s * 0.34, s * 0.26, s * 0.32))
        p.drawLine(QPointF(s * 0.56, s * 0.5), QPointF(s * 0.46, s * 0.5))
        _arrow_head(p, QPointF(s * 0.44, s * 0.5), math.pi, s * 0.16)
    elif name == "slip":
        # clip lunga (il file) con dentro la parte usata, e frecce per scorrerla
        p.drawRect(QRectF(m, s * 0.22, s - 2 * m, s * 0.34))
        p.setBrush(QColor(c.red(), c.green(), c.blue(), 110))
        p.drawRect(QRectF(s * 0.34, s * 0.22, s * 0.32, s * 0.34))
        p.drawLine(QPointF(s * 0.3, s * 0.78), QPointF(s * 0.7, s * 0.78))
        _arrow_head(p, QPointF(s * 0.26, s * 0.78), math.pi, s * 0.16)
        _arrow_head(p, QPointF(s * 0.74, s * 0.78), 0.0, s * 0.16)
    elif name == "magnet":
        pen.setWidthF(s * 0.13)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(s * 0.26, s * 0.2)
        path.lineTo(s * 0.26, s * 0.52)
        path.arcTo(QRectF(s * 0.26, s * 0.28, s * 0.48, s * 0.48), 180, 180)
        path.lineTo(s * 0.74, s * 0.2)
        p.drawPath(path)
        p.setPen(QPen(QColor(232, 163, 61), s * 0.13, Qt.SolidLine, Qt.FlatCap))
        p.drawLine(QPointF(s * 0.26, s * 0.14), QPointF(s * 0.26, s * 0.27))
        p.drawLine(QPointF(s * 0.74, s * 0.14), QPointF(s * 0.74, s * 0.27))
    elif name == "box":
        p.setBrush(QColor(c.red(), c.green(), c.blue(), 90))
        p.drawRoundedRect(QRectF(m, s * 0.26, s - 2 * m, s * 0.48), s * 0.06, s * 0.06)
    elif name == "text":
        p.drawLine(QPointF(s * 0.22, s * 0.22), QPointF(s * 0.78, s * 0.22))
        p.drawLine(QPointF(s / 2, s * 0.22), QPointF(s / 2, s * 0.8))
    elif name == "split":
        p.drawLine(QPointF(s / 2, m), QPointF(s / 2, s - m))
        p.drawEllipse(QPointF(s * 0.3, s * 0.7), s * 0.1, s * 0.1)
        p.drawEllipse(QPointF(s * 0.7, s * 0.7), s * 0.1, s * 0.1)


def icon(name: str, size: int = 18) -> QIcon:
    ic = QIcon()
    for mode, col in ((QIcon.Normal, WHITE), (QIcon.Disabled, DISABLED), (QIcon.Active, QColor(255, 255, 255))):
        for scale in (1, 2):
            pm = QPixmap(size * scale, size * scale)
            pm.setDevicePixelRatio(scale)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            _draw(name, p, float(size), col)
            p.end()
            ic.addPixmap(pm, mode, QIcon.Off)
    return ic
