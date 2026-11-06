"""Barra della clip sorgente: mostra tutta la clip e, come fasce colorate,
le parti già scelte. La parte selezionata si allunga, accorcia o sposta
trascinandone i bordi o il centro."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

BAND_COLORS = [QColor(84, 120, 168), QColor(96, 150, 120), QColor(150, 110, 170), QColor(170, 130, 80),
               QColor(80, 150, 160), QColor(170, 95, 95)]
ACCENT = QColor(232, 163, 61)


class SourceBar(QWidget):
    seek = Signal(float)
    select_sub = Signal(str)
    edit_begin = Signal(str)
    edit_sub = Signal(str, float, float)
    edit_end = Signal(str)

    M = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(56)
        self.setMouseTracking(True)
        self.duration = 0.0
        self.fps = 25.0
        self.position = 0.0
        self.subs = []  # (id, in, out, n)
        self.selected = None
        self.mark_in = None
        self.mark_out = None
        self._drag = None
        self._lanes = {}

    def set_data(self, duration, fps, subs, selected, mark_in=None, mark_out=None):
        self.duration = duration
        self.fps = fps or 25.0
        self.subs = subs
        self.selected = selected
        self.mark_in = mark_in
        self.mark_out = mark_out
        self._compute_lanes()
        self.update()

    def set_position(self, t):
        self.position = t
        self.update()

    def _compute_lanes(self):
        lanes_end = []
        self._lanes = {}
        for sid, a, b, _ in sorted(self.subs, key=lambda s: s[1]):
            for i, e in enumerate(lanes_end):
                if a >= e - 1e-6:
                    lanes_end[i] = b
                    self._lanes[sid] = i
                    break
            else:
                lanes_end.append(b)
                self._lanes[sid] = len(lanes_end) - 1
        self._nlanes = max(1, min(3, len(lanes_end)))

    def _x(self, t):
        w = self.width() - 2 * self.M
        return self.M + (t / self.duration * w if self.duration > 0 else 0)

    def _t(self, x):
        w = self.width() - 2 * self.M
        if w <= 0 or self.duration <= 0:
            return 0.0
        t = (x - self.M) / w * self.duration
        t = max(0.0, min(self.duration, t))
        return round(t * self.fps) / self.fps

    def _band_rect(self, sid, a, b):
        top, bottom = 20, self.height() - 6
        lane = min(self._lanes.get(sid, 0), self._nlanes - 1)
        lh = (bottom - top) / self._nlanes
        return QRectF(self._x(a), top + lane * lh + 1, max(3.0, self._x(b) - self._x(a)), lh - 2)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(24, 25, 27))
        x0, x1 = self._x(0), self._x(self.duration)
        p.fillRect(QRectF(x0, 20, x1 - x0, self.height() - 26), QColor(36, 38, 41))
        # tacche dei secondi
        if self.duration > 0:
            p.setPen(QPen(QColor(80, 82, 86), 1))
            px_per_s = (x1 - x0) / self.duration
            step = 1
            for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
                if s * px_per_s >= 40:
                    step = s
                    break
            else:
                step = 600
            f = QFont(p.font())
            f.setPointSizeF(8)
            p.setFont(f)
            t = 0
            while t <= self.duration:
                x = self._x(t)
                p.drawLine(int(x), 12, int(x), 18)
                m, s = divmod(int(t), 60)
                p.drawText(int(x) + 2, 11, f"{m}:{s:02d}")
                t += step
        # fasce delle parti
        f = QFont(p.font())
        f.setPointSizeF(8.5)
        p.setFont(f)
        for sid, a, b, n in self.subs:
            r = self._band_rect(sid, a, b)
            col = QColor(BAND_COLORS[(n - 1) % len(BAND_COLORS)])
            sel = sid == self.selected
            if not sel:
                col.setAlpha(170)
            p.setPen(QPen(ACCENT if sel else col.darker(140), 2 if sel else 1))
            p.setBrush(col)
            p.drawRoundedRect(r, 3, 3)
            if sel:
                p.setPen(Qt.NoPen)
                p.setBrush(ACCENT)
                p.drawRect(QRectF(r.left() - 1, r.top(), 4, r.height()))
                p.drawRect(QRectF(r.right() - 3, r.top(), 4, r.height()))
            if r.width() > 26:
                p.setPen(QColor(250, 250, 250))
                p.drawText(r.adjusted(5, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft, sid.split(".", 1)[-1]
                           if "." in sid else sid)
        # segni di ingresso/uscita (clip intera)
        if self.mark_in is not None or self.mark_out is not None:
            a = self.mark_in if self.mark_in is not None else 0.0
            b = self.mark_out if self.mark_out is not None else self.duration
            p.fillRect(QRectF(self._x(a), 20, self._x(b) - self._x(a), self.height() - 26),
                       QColor(232, 163, 61, 60))
            p.setPen(QPen(ACCENT, 2))
            if self.mark_in is not None:
                x = self._x(self.mark_in)
                p.drawLine(int(x), 14, int(x), self.height() - 4)
                p.drawText(int(x) + 3, self.height() - 8, "I")
            if self.mark_out is not None:
                x = self._x(self.mark_out)
                p.drawLine(int(x), 14, int(x), self.height() - 4)
                p.drawText(int(x) - 9, self.height() - 8, "O")
        # testina
        x = self._x(self.position)
        p.setPen(QPen(QColor(235, 70, 60), 2))
        p.drawLine(int(x), 0, int(x), self.height())
        p.end()

    def _hit(self, pos):
        if self.selected:
            for sid, a, b, _n in self.subs:
                if sid != self.selected:
                    continue
                r = self._band_rect(sid, a, b)
                if r.top() - 2 <= pos.y() <= r.bottom() + 2:
                    if abs(pos.x() - r.left()) <= 6:
                        return ("in", sid, a, b)
                    if abs(pos.x() - r.right()) <= 6:
                        return ("out", sid, a, b)
                    if r.left() < pos.x() < r.right():
                        return ("slide", sid, a, b)
        for sid, a, b, _n in self.subs:
            r = self._band_rect(sid, a, b)
            if r.contains(pos):
                return ("pick", sid, a, b)
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position()
        h = self._hit(pos) if pos.y() >= 18 else None
        if h is None:
            self._drag = ("scrub",)
            self.seek.emit(self._t(pos.x()))
            return
        kind, sid, a, b = h
        if kind == "pick":
            self.select_sub.emit(sid)
            self._drag = ("scrub",)
            self.seek.emit(self._t(pos.x()))
            return
        self.edit_begin.emit(sid)
        self._drag = (kind, sid, a, b, self._t(pos.x()))

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag is None:
            h = self._hit(pos) if pos.y() >= 18 else None
            if h and h[0] in ("in", "out"):
                self.setCursor(Qt.SizeHorCursor)
            elif h and h[0] == "slide":
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            return
        t = self._t(pos.x())
        if self._drag[0] == "scrub":
            self.seek.emit(t)
            return
        kind, sid, a, b, t0 = self._drag
        if kind == "in":
            self.edit_sub.emit(sid, min(t, b - 1 / self.fps), b)
            self.seek.emit(min(t, b - 1 / self.fps))
        elif kind == "out":
            self.edit_sub.emit(sid, a, max(t, a + 1 / self.fps))
            self.seek.emit(max(t, a + 1 / self.fps))
        else:
            d = t - t0
            d = max(-a, min(d, self.duration - b))
            self.edit_sub.emit(sid, a + d, b + d)

    def mouseReleaseEvent(self, e):
        if self._drag and self._drag[0] in ("in", "out", "slide"):
            self.edit_end.emit(self._drag[1])
        self._drag = None
