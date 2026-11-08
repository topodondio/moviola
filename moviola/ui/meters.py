"""Indicatori del livello audio (stereo), accanto al visore grande."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

DB_MIN = -60.0


def to_db(v):
    return 20 * math.log10(v) if v > 1e-6 else -120.0


class LevelMeter(QWidget):
    """Due barre verticali: verde fino a -12 dB, giallo fino a -3, rosso oltre.
    Una lineetta tiene il picco per un attimo; in cima si accende un quadratino
    rosso se il suono satura (si spegne da solo dopo qualche secondo)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(30)
        self.setToolTip("Livello audio: verde ok, giallo forte, rosso troppo forte (rischio distorsione)")
        self.level = [DB_MIN, DB_MIN]
        self.peak = [DB_MIN, DB_MIN]
        self.peak_t = [0.0, 0.0]
        self.clip_t = 0.0

    def push(self, left, right):
        now = time.monotonic()
        for i, v in enumerate((left, right)):
            db = max(DB_MIN, to_db(v))
            # sale subito, scende morbido
            self.level[i] = db if db > self.level[i] else max(DB_MIN, self.level[i] - 1.6)
            if db >= self.peak[i] or now - self.peak_t[i] > 1.2:
                self.peak[i] = db
                self.peak_t[i] = now
            if v >= 0.999:
                self.clip_t = now
        self.update()

    def reset(self):
        self.level = [DB_MIN, DB_MIN]
        self.peak = [DB_MIN, DB_MIN]
        self.update()

    def _y(self, db, top, h):
        u = (min(0.0, max(DB_MIN, db)) - DB_MIN) / -DB_MIN
        return top + h - u * h

    def paintEvent(self, e):
        p = QPainter(self)
        top, bottom = 12.0, self.height() - 4.0
        h = bottom - top
        bw = 7.0
        xs = (4.0, 13.0)
        g = QLinearGradient(0, bottom, 0, top)
        g.setColorAt(0.0, QColor(60, 170, 90))
        g.setColorAt(0.78, QColor(90, 200, 100))
        g.setColorAt(0.80, QColor(225, 200, 60))
        g.setColorAt(0.94, QColor(230, 170, 50))
        g.setColorAt(0.95, QColor(225, 70, 60))
        g.setColorAt(1.0, QColor(225, 70, 60))
        for i, x in enumerate(xs):
            p.fillRect(QRectF(x, top, bw, h), QColor(28, 29, 31))
            y = self._y(self.level[i], top, h)
            p.fillRect(QRectF(x, y, bw, bottom - y), g)
            if self.peak[i] > DB_MIN + 1:
                yp = self._y(self.peak[i], top, h)
                p.fillRect(QRectF(x, yp - 1, bw, 2), QColor(235, 235, 235))
        on = time.monotonic() - self.clip_t < 3.0
        p.fillRect(QRectF(xs[0], 2, xs[1] + bw - xs[0], 7), QColor(225, 60, 50) if on else QColor(50, 30, 30))
        f = QFont(p.font())
        f.setPointSizeF(6.5)
        p.setFont(f)
        p.setPen(QColor(110, 110, 110))
        for db in (0, -12, -24, -48):
            y = self._y(db, top, h)
            p.drawLine(int(xs[1] + bw + 1), int(y), int(xs[1] + bw + 3), int(y))
        p.end()
