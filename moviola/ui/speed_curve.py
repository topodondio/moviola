"""Grafico della velocità variabile: in orizzontale la durata del pezzo,
in verticale la velocità (scala logaritmica: 1x al centro)."""
from __future__ import annotations

import copy
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import speed as spd

LO, HI = math.log2(spd.V_MIN), math.log2(spd.V_MAX)
GRID = [0.25, 0.5, 1, 2, 4]


class SpeedCurveWidget(QWidget):
    pressed = Signal()
    changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.points = [[0.0, 1.0], [1.0, 1.0]]
        self._drag = None
        self.setMouseTracking(True)
        self.setToolTip("Clic: aggiungi punto · trascina: sposta · doppio clic: rimuovi")

    def set_points(self, pts):
        self.points = copy.deepcopy(pts) if pts else [[0.0, 1.0], [1.0, 1.0]]
        self.update()

    def _box(self):
        return QRectF(34, 8, self.width() - 42, self.height() - 26)

    def _to_px(self, u, v):
        b = self._box()
        y = (math.log2(max(spd.V_MIN, min(spd.V_MAX, v))) - LO) / (HI - LO)
        return QPointF(b.left() + u * b.width(), b.bottom() - y * b.height())

    def _from_px(self, p):
        b = self._box()
        u = min(1.0, max(0.0, (p.x() - b.left()) / b.width()))
        y = min(1.0, max(0.0, (b.bottom() - p.y()) / b.height()))
        v = 2 ** (LO + y * (HI - LO))
        # aggancio morbido a 1x
        if abs(math.log2(v)) < 0.08:
            v = 1.0
        return u, round(v, 3)

    def _hit(self, pos):
        for i, (u, v) in enumerate(self.points):
            q = self._to_px(u, v)
            if abs(q.x() - pos.x()) < 8 and abs(q.y() - pos.y()) < 8:
                return i
        return None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        b = self._box()
        p.fillRect(b, QColor(22, 23, 25))
        f = QFont(p.font())
        f.setPointSizeF(8)
        p.setFont(f)
        for g in GRID:
            y = self._to_px(0, g).y()
            p.setPen(QPen(QColor(90, 92, 96) if g == 1 else QColor(48, 50, 54), 1))
            p.drawLine(QPointF(b.left(), y), QPointF(b.right(), y))
            p.setPen(QColor(140, 140, 140))
            p.drawText(QRectF(0, y - 7, 30, 14), Qt.AlignRight | Qt.AlignVCenter, f"{g:g}x")
        p.setPen(QColor(120, 120, 120))
        p.drawText(QRectF(b.left(), b.bottom() + 2, 60, 14), Qt.AlignLeft, "inizio")
        p.drawText(QRectF(b.right() - 60, b.bottom() + 2, 60, 14), Qt.AlignRight, "fine")
        uk, vk, _ = spd.table(self.points)
        path = QPainterPath()
        step = max(1, len(uk) // 300)
        for i in range(0, len(uk), step):
            q = self._to_px(float(uk[i]), float(vk[i]))
            if i == 0:
                path.moveTo(q)
            else:
                path.lineTo(q)
        p.setPen(QPen(QColor(232, 163, 61), 2))
        p.drawPath(path)
        p.setPen(QPen(QColor(20, 20, 20), 1))
        p.setBrush(QColor(232, 163, 61))
        for u, v in self.points:
            p.drawEllipse(self._to_px(u, v), 4.5, 4.5)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(60, 62, 66), 1))
        p.drawRect(b)
        p.end()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self.pressed.emit()
        i = self._hit(e.position())
        if i is None:
            u, v = self._from_px(e.position())
            if 0.0 < u < 1.0:
                self.points.append([u, v])
                self.points.sort(key=lambda q: q[0])
                i = next(k for k, q in enumerate(self.points) if q[0] == u and q[1] == v)
                self.changed.emit(copy.deepcopy(self.points))
        self._drag = i
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            self.setCursor(Qt.PointingHandCursor if self._hit(e.position()) is not None else Qt.CrossCursor)
            return
        i = self._drag
        u, v = self._from_px(e.position())
        pts = self.points
        if i == 0:
            u = 0.0
        elif i == len(pts) - 1:
            u = 1.0
        else:
            u = min(max(u, pts[i - 1][0] + 0.01), pts[i + 1][0] - 0.01)
        pts[i] = [u, v]
        self.changed.emit(copy.deepcopy(pts))
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        i = self._hit(e.position())
        if i is not None and 0 < i < len(self.points) - 1:
            self.pressed.emit()
            del self.points[i]
            self.changed.emit(copy.deepcopy(self.points))
            self.update()
