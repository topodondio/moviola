"""Pannello colore in stile fotografico: cursori, curva, HSL per colore."""
from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSlider, QTabWidget,
                               QVBoxLayout, QWidget)

from .. import color


class _ResetLabel(QLabel):
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, e):
        self.double_clicked.emit()


class _ResetSlider(QSlider):
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, e):
        self.double_clicked.emit()


class _ValueBox(QLineEdit):
    """Casella numerica semplice (al posto di QDoubleSpinBox, che su macOS senza
    frecce non mostrava il numero). Frecce su/giù o rotella = un passo."""

    committed = Signal()

    def __init__(self, lo, hi, step, decimals):
        super().__init__()
        self.lo, self.hi, self.step, self.dec = lo, hi, step, decimals
        self._v = 0.0
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.editingFinished.connect(self._commit)
        self.setValue(0.0)

    def value(self):
        return self._v

    def setValue(self, v):
        self._v = max(self.lo, min(self.hi, float(v)))
        txt = f"{self._v:.{self.dec}f}"
        if self.dec and txt.startswith("-0.") and float(txt) == 0:
            txt = txt[1:]
        if self.text() != txt:
            self.setText(txt)

    def _commit(self):
        try:
            v = float(self.text().replace(",", "."))
        except ValueError:
            self.setValue(self._v)
            return
        old = self._v
        self.setValue(v)
        if self._v != old:
            self.committed.emit()

    def _nudge(self, n):
        old = self._v
        self.setValue(round(self._v + n * self.step, 6))
        if self._v != old:
            self.committed.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Up:
            self._nudge(1)
        elif e.key() == Qt.Key_Down:
            self._nudge(-1)
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, e):
        if self.hasFocus():
            self._nudge(1 if e.angleDelta().y() > 0 else -1)
            e.accept()
        else:
            e.ignore()


class AnimGraph(QWidget):
    """Andamento nel tempo di un cursore: orizzontale il tempo del pezzo (o della
    timeline), verticale il valore. Clic aggiunge un punto, trascina lo sposta
    (il visore mostra quel momento), doppio clic lo toglie."""

    pressed = Signal()
    changed = Signal(list)
    seek = Signal(float)

    def __init__(self, lo, hi, parent=None):
        super().__init__(parent)
        self.lo, self.hi = float(lo), float(hi)
        self.duration = 1.0
        self.playhead = None
        self.points = []
        self._drag = None
        self.setFixedHeight(84)
        self.setMouseTracking(True)
        self.setToolTip("Clic: aggiungi punto · trascina: sposta (il visore mostra quel momento) · "
                        "doppio clic: togli")

    def set_data(self, points, duration, playhead=None):
        self.points = copy.deepcopy(points or [])
        self.duration = max(0.04, float(duration))
        self.playhead = playhead
        self.update()

    def set_playhead(self, t):
        self.playhead = t
        self.update()

    def _box(self):
        return QRectF(4, 4, self.width() - 8, self.height() - 16)

    def _to_px(self, t, v):
        b = self._box()
        x = b.left() + min(1.0, max(0.0, t / self.duration)) * b.width()
        y = b.bottom() - (min(self.hi, max(self.lo, v)) - self.lo) / (self.hi - self.lo) * b.height()
        return QPointF(x, y)

    def _from_px(self, p):
        b = self._box()
        t = min(1.0, max(0.0, (p.x() - b.left()) / b.width())) * self.duration
        v = self.lo + min(1.0, max(0.0, (b.bottom() - p.y()) / b.height())) * (self.hi - self.lo)
        step = (self.hi - self.lo) / 200.0
        v = round(v / step) * step
        if abs(v) < (self.hi - self.lo) * 0.015:
            v = 0.0  # aggancio allo zero
        return round(t, 3), round(v, 3)

    def _hit(self, pos):
        for i, (t, v) in enumerate(self.points):
            q = self._to_px(t, v)
            if abs(q.x() - pos.x()) < 7 and abs(q.y() - pos.y()) < 7:
                return i
        return None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        b = self._box()
        p.fillRect(b, QColor(22, 23, 25))
        p.setPen(QPen(QColor(70, 72, 76), 1, Qt.DashLine))
        y0 = self._to_px(0, 0.0).y()
        p.drawLine(QPointF(b.left(), y0), QPointF(b.right(), y0))
        if self.playhead is not None and 0 <= self.playhead <= self.duration:
            x = self._to_px(self.playhead, 0).x()
            p.setPen(QPen(QColor(235, 70, 60), 1))
            p.drawLine(QPointF(x, b.top()), QPointF(x, b.bottom()))
        if self.points:
            pts = sorted(self.points, key=lambda q: q[0])
            path = QPainterPath(QPointF(b.left(), self._to_px(0, pts[0][1]).y()))
            for t, v in pts:
                path.lineTo(self._to_px(t, v))
            path.lineTo(QPointF(b.right(), self._to_px(self.duration, pts[-1][1]).y()))
            p.setPen(QPen(QColor(232, 163, 61), 2))
            p.drawPath(path)
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.setBrush(QColor(232, 163, 61))
            for t, v in pts:
                p.drawEllipse(self._to_px(t, v), 4, 4)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(60, 62, 66), 1))
        p.drawRect(b)
        p.setPen(QColor(120, 120, 120))
        f = p.font()
        f.setPointSizeF(7.5)
        p.setFont(f)
        p.drawText(QRectF(b.left(), b.bottom() + 1, 80, 11), Qt.AlignLeft, "0 s")
        p.drawText(QRectF(b.right() - 80, b.bottom() + 1, 80, 11), Qt.AlignRight, f"{self.duration:.1f} s")
        p.end()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self.pressed.emit()
        i = self._hit(e.position())
        if i is None:
            t, v = self._from_px(e.position())
            self.points.append([t, v])
            self.points.sort(key=lambda q: q[0])
            i = next(k for k, q in enumerate(self.points) if q[0] == t and q[1] == v)
            self.changed.emit(copy.deepcopy(self.points))
        self._drag = i
        self.seek.emit(float(self.points[i][0]))
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            i = self._hit(e.position())
            self.setCursor(Qt.PointingHandCursor if i is not None else Qt.CrossCursor)
            return
        i = self._drag
        t, v = self._from_px(e.position())
        pts = self.points
        if i > 0:
            t = max(t, pts[i - 1][0] + 0.01)
        if i < len(pts) - 1:
            t = min(t, pts[i + 1][0] - 0.01)
        pts[i] = [t, v]
        self.setToolTip(f"{t:.2f} s  →  {v:g}")
        self.changed.emit(copy.deepcopy(pts))
        self.seek.emit(float(t))
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        i = self._hit(e.position())
        if i is not None and len(self.points) > 1:
            self.pressed.emit()
            del self.points[i]
            self.changed.emit(copy.deepcopy(self.points))
            self.update()


class GradeSlider(QWidget):
    """Cursore con etichetta e valore; doppio clic = azzera. La casella davanti
    lo rende animato nel tempo (diventa un grafico)."""

    pressed = Signal(str)
    changed = Signal(str, float)
    anim_changed = Signal(str, object)  # chiave, punti o None
    anim_seek = Signal(float)

    def __init__(self, key, label, lo, hi, step, swatch=None, parent=None):
        super().__init__(parent)
        self.key = key
        self.step = step
        self.scale = 1.0 / step
        self.duration = 1.0
        self.playhead = 0.0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 1, 0, 1)
        outer.setSpacing(2)
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        outer.addWidget(row)
        self.anim_chk = QCheckBox()
        self.anim_chk.setToolTip("Anima nel tempo: il valore cambia lungo il pezzo (o la timeline)")
        lay.addWidget(self.anim_chk)
        self.graph = AnimGraph(lo, hi)
        self.graph.setVisible(False)
        outer.addWidget(self.graph)
        self.anim_chk.toggled.connect(self._anim_toggled)
        self.graph.pressed.connect(lambda: self.pressed.emit("anim:" + self.key))
        self.graph.changed.connect(lambda pts: self.anim_changed.emit(self.key, pts))
        self.graph.seek.connect(self.anim_seek)
        self._anim_loading = False
        self.lab = _ResetLabel(label)
        self.lab.setFixedWidth(78)
        if swatch:
            self.lab.setText(f'<span style="color:{swatch}">■</span> {label}')
        self.lab.setToolTip("Doppio clic per azzerare")
        self.sl = _ResetSlider(Qt.Horizontal)
        self.sl.setRange(int(round(lo * self.scale)), int(round(hi * self.scale)))
        self.sl.setSingleStep(1)
        self.sl.setPageStep(int(10 * max(1, self.scale / 20)))
        self.spin = _ValueBox(lo, hi, step, 2 if step < 1 else 0)
        self.spin.setFixedWidth(56)
        lay.addWidget(self.lab)
        lay.addWidget(self.sl, 1)
        lay.addWidget(self.spin)
        self._block = False
        self.sl.sliderPressed.connect(lambda: self.pressed.emit(self.key))
        self.sl.valueChanged.connect(self._from_slider)
        self.spin.committed.connect(self._from_spin)
        self.lab.double_clicked.connect(self._reset)
        self.sl.double_clicked.connect(self._reset)

    def _reset(self):
        self.pressed.emit(self.key)
        self.set_value(0.0)
        self.changed.emit(self.key, 0.0)

    def _from_slider(self, v):
        if self._block:
            return
        val = v / self.scale
        self._block = True
        self.spin.setValue(val)
        self._block = False
        if not self.sl.isSliderDown():
            self.pressed.emit(self.key)  # tastiera o rotella: punto di annullamento
        self.changed.emit(self.key, val)

    def _from_spin(self):
        if self._block:
            return
        val = self.spin.value()
        self.pressed.emit(self.key)
        self._block = True
        self.sl.setValue(int(round(val * self.scale)))
        self._block = False
        self.changed.emit(self.key, val)

    def set_value(self, v):
        self._block = True
        self.sl.setValue(int(round(v * self.scale)))
        self.spin.setValue(v)
        self._block = False

    def _anim_toggled(self, on):
        self.sl.setVisible(not on)
        self.spin.setVisible(not on)
        self.graph.setVisible(on)
        if self._anim_loading:
            return
        self.pressed.emit("anim:" + self.key)
        if on:
            v = self.spin.value()
            pts = [[0.0, v], [round(self.duration, 3), v]]
            self.graph.set_data(pts, self.duration, self.playhead)
            self.anim_changed.emit(self.key, pts)
        else:
            self.anim_changed.emit(self.key, None)

    def set_anim(self, points, duration, playhead, enabled=True):
        """points None = cursore normale."""
        self._anim_loading = True
        self.duration = duration
        self.playhead = playhead
        self.anim_chk.setEnabled(enabled)
        self.anim_chk.setChecked(bool(points) and enabled)
        self._anim_loading = False
        on = bool(points) and enabled
        self.sl.setVisible(not on)
        self.spin.setVisible(not on)
        self.graph.setVisible(on)
        if on:
            self.graph.set_data(points, duration, playhead)

    def set_playhead(self, t):
        self.playhead = t
        if self.graph.isVisible():
            self.graph.set_playhead(t)


class CurveWidget(QWidget):
    """Curva tonale con istogramma. Clic = aggiungi punto, trascina = sposta,
    doppio clic su un punto = rimuovi."""

    pressed = Signal()
    changed = Signal(str, list)

    CH_COLORS = {"master": QColor(230, 230, 230), "r": QColor(235, 90, 80), "g": QColor(90, 200, 100),
                 "b": QColor(90, 140, 240)}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.channel = "master"
        self.curves = {c: copy.deepcopy(color.IDENTITY_CURVE) for c in color.CURVE_CHANNELS}
        self.hist = None
        self._drag = None
        self.setMouseTracking(True)

    def resizeEvent(self, e):
        self.setFixedHeight(max(180, min(self.width(), 300)))

    def set_curves(self, curves):
        self.curves = copy.deepcopy(curves)
        self.update()

    def set_hist(self, hist):
        self.hist = hist
        self.update()

    def _box(self):
        m = 6
        s = min(self.width(), self.height()) - 2 * m
        return QRectF((self.width() - s) / 2, m, s, s)

    def _to_px(self, x, y):
        b = self._box()
        return QPointF(b.left() + x * b.width(), b.bottom() - y * b.height())

    def _from_px(self, p):
        b = self._box()
        return (min(1, max(0, (p.x() - b.left()) / b.width())), min(1, max(0, (b.bottom() - p.y()) / b.height())))

    def _hit(self, pos):
        pts = self.curves[self.channel]
        for i, (x, y) in enumerate(pts):
            q = self._to_px(x, y)
            if abs(q.x() - pos.x()) < 8 and abs(q.y() - pos.y()) < 8:
                return i
        return None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        b = self._box()
        p.fillRect(b, QColor(22, 23, 25))
        if self.hist is not None and self.hist.max() > 0:
            h = np.sqrt(self.hist / self.hist.max())
            path = QPainterPath(QPointF(b.left(), b.bottom()))
            for i, v in enumerate(h):
                path.lineTo(b.left() + i / 255 * b.width(), b.bottom() - v * b.height() * 0.9)
            path.lineTo(b.right(), b.bottom())
            p.fillPath(path, QColor(70, 72, 78, 160))
        p.setPen(QPen(QColor(50, 52, 56), 1))
        for k in range(1, 4):
            p.drawLine(QPointF(b.left() + b.width() * k / 4, b.top()), QPointF(b.left() + b.width() * k / 4, b.bottom()))
            p.drawLine(QPointF(b.left(), b.top() + b.height() * k / 4), QPointF(b.right(), b.top() + b.height() * k / 4))
        p.drawLine(self._to_px(0, 0), self._to_px(1, 1))
        # altre curve in trasparenza
        for ch in color.CURVE_CHANNELS:
            if ch == self.channel:
                continue
            pts = self.curves[ch]
            if all(abs(x - y) < 1e-6 for x, y in pts):
                continue
            c = QColor(self.CH_COLORS[ch])
            c.setAlpha(90)
            self._draw_curve(p, pts, c, 1)
        pts = self.curves[self.channel]
        self._draw_curve(p, pts, self.CH_COLORS[self.channel], 2)
        p.setPen(QPen(QColor(20, 20, 20), 1))
        p.setBrush(self.CH_COLORS[self.channel])
        for x, y in pts:
            p.drawEllipse(self._to_px(x, y), 4.5, 4.5)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(60, 62, 66), 1))
        p.drawRect(b)
        p.end()

    def _draw_curve(self, p, pts, col, width):
        table = color.curve_table(pts, 256)
        path = QPainterPath(self._to_px(0, float(table[0])))
        for i in range(1, 256):
            path.lineTo(self._to_px(i / 255, float(table[i])))
        p.setPen(QPen(col, width))
        p.drawPath(path)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self.pressed.emit()
        i = self._hit(e.position())
        pts = self.curves[self.channel]
        if i is None:
            x, y = self._from_px(e.position())
            if 0.0 < x < 1.0:
                pts.append([x, y])
                pts.sort(key=lambda q: q[0])
                i = next(k for k, q in enumerate(pts) if q[0] == x and q[1] == y)
                self.changed.emit(self.channel, copy.deepcopy(pts))
        self._drag = i
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            self.setCursor(Qt.PointingHandCursor if self._hit(e.position()) is not None else Qt.CrossCursor)
            return
        pts = self.curves[self.channel]
        i = self._drag
        x, y = self._from_px(e.position())
        if i == 0:
            x = min(x, pts[1][0] - 0.01)
        elif i == len(pts) - 1:
            x = max(x, pts[-2][0] + 0.01)
        else:
            x = min(max(x, pts[i - 1][0] + 0.01), pts[i + 1][0] - 0.01)
        pts[i] = [x, y]
        self.changed.emit(self.channel, copy.deepcopy(pts))
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        i = self._hit(e.position())
        pts = self.curves[self.channel]
        if i is not None and 0 < i < len(pts) - 1:
            self.pressed.emit()
            del pts[i]
            self.changed.emit(self.channel, copy.deepcopy(pts))
            self.update()


class ColorPanel(QWidget):
    """Emette edit(chiave, valore) e lascia alla finestra principale il
    compito di applicarlo ai bersagli selezionati."""

    begin_edit = Signal(str)
    edit = Signal(str, object)
    target_changed = Signal(str)
    action = Signal(str)  # copia, incolla, azzera, prima_dopo
    anim_edit = Signal(str, object)  # chiave, punti [[t, v]] o None
    anim_seek = Signal(float)  # tempo relativo da mostrare nel visore

    TARGETS = [("source", "Clip intera"), ("item", "Pezzo"), ("global", "Timeline")]

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        # bersaglio
        trow = QHBoxLayout()
        self.target_group = QButtonGroup(self)
        for i, (k, lab) in enumerate(self.TARGETS):
            rb = QRadioButton(lab)
            rb.setProperty("key", k)
            self.target_group.addButton(rb, i)
            trow.addWidget(rb)
        self.target_group.button(0).setChecked(True)
        self.target_group.idClicked.connect(lambda i: self.target_changed.emit(self.TARGETS[i][0]))
        outer.addLayout(trow)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#9a9a9a;")
        outer.addWidget(self.info)

        brow = QHBoxLayout()
        for key, lab, tip in (("copia", "Copia", "Copia le regolazioni (Ctrl/Cmd+Maiusc+C)"),
                              ("incolla", "Incolla", "Incolla sulle clip selezionate (Ctrl/Cmd+Maiusc+V)"),
                              ("azzera", "Azzera", "Azzera tutte le regolazioni del bersaglio"),
                              ("prima_dopo", "Prima/Dopo", "Confronta con l'originale (tasto \\)")):
            b = QPushButton(lab)
            b.setToolTip(tip)
            if key == "prima_dopo":
                b.setCheckable(True)
                self.bypass_btn = b
            b.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            brow.addWidget(b)
        outer.addLayout(brow)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(8)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.sliders: dict[str, GradeSlider] = {}
        groups: dict[str, QVBoxLayout] = {}
        for key, (lab, lo, hi, step, section) in color.SLIDERS.items():
            if section not in groups:
                gb = QGroupBox(section)
                gl = QVBoxLayout(gb)
                gl.setSpacing(0)
                gl.setContentsMargins(6, 4, 6, 4)
                groups[section] = gl
                lay.addWidget(gb)
            s = GradeSlider(key, lab, lo, hi, step)
            s.pressed.connect(self.begin_edit)
            s.changed.connect(lambda k, v: self.edit.emit(k, v))
            s.anim_changed.connect(self.anim_edit)
            s.anim_seek.connect(self.anim_seek)
            groups[section].addWidget(s)
            self.sliders[key] = s

        # curva
        gb = QGroupBox("Curva")
        gl = QVBoxLayout(gb)
        crow = QHBoxLayout()
        self.chan = QComboBox()
        for k, lab in (("master", "Luminosità (RGB)"), ("r", "Rosso"), ("g", "Verde"), ("b", "Blu")):
            self.chan.addItem(lab, k)
        crow.addWidget(self.chan, 1)
        rb = QPushButton("Azzera curva")
        crow.addWidget(rb)
        gl.addLayout(crow)
        self.curve = CurveWidget()
        gl.addWidget(self.curve)
        hint = QLabel("Clic: aggiungi punto · trascina: sposta · doppio clic: rimuovi")
        hint.setStyleSheet("color:#808080; font-size:11px;")
        gl.addWidget(hint)
        self.chan.currentIndexChanged.connect(self._chan_changed)
        self.curve.pressed.connect(lambda: self.begin_edit.emit("curves"))
        self.curve.changed.connect(lambda ch, pts: self.edit.emit("curve:" + ch, pts))
        rb.clicked.connect(self._reset_curve)
        lay.addWidget(gb)

        # HSL
        gb = QGroupBox("Colore per tinta (HSL)")
        gl = QVBoxLayout(gb)
        tabs = QTabWidget()
        self.hsl_sliders: dict[str, list[GradeSlider]] = {}
        for k, lab in (("hue", "Tonalità"), ("sat", "Saturazione"), ("lum", "Luminanza")):
            w = QWidget()
            wl = QVBoxLayout(w)
            wl.setSpacing(0)
            wl.setContentsMargins(4, 4, 4, 4)
            row = []
            for i, name in enumerate(color.HSL_BANDS):
                s = GradeSlider(f"hsl:{k}:{i}", name, -100, 100, 1, swatch=color.HSL_COLORS[i])
                s.pressed.connect(self.begin_edit)
                s.changed.connect(lambda kk, v: self.edit.emit(kk, v))
                s.anim_changed.connect(self.anim_edit)
                s.anim_seek.connect(self.anim_seek)
                wl.addWidget(s)
                row.append(s)
            self.hsl_sliders[k] = row
            tabs.addTab(w, lab)
        gl.addWidget(tabs)
        lay.addWidget(gb)
        lay.addStretch(1)
        self.setEnabled(True)

    def _chan_changed(self):
        self.curve.channel = self.chan.currentData()
        self.curve.update()

    def _reset_curve(self):
        ch = self.chan.currentData()
        self.begin_edit.emit("curves")
        self.curve.curves[ch] = copy.deepcopy(color.IDENTITY_CURVE)
        self.curve.update()
        self.edit.emit("curve:" + ch, copy.deepcopy(color.IDENTITY_CURVE))

    def target(self):
        return self.TARGETS[self.target_group.checkedId()][0]

    def set_target(self, key):
        for i, (k, _) in enumerate(self.TARGETS):
            if k == key:
                self.target_group.button(i).setChecked(True)

    def all_sliders(self):
        return list(self.sliders.values()) + [s for r in self.hsl_sliders.values() for s in r]

    def show_grade(self, grade, info="", anim_ctx=None):
        """anim_ctx: (durata, testina relativa) se il bersaglio si può animare
        (pezzo o timeline), None per la clip intera."""
        g = color.normalize(grade)
        anim = g.get("anim") or {}
        dur, ph = anim_ctx if anim_ctx else (1.0, 0.0)
        for k, s in self.sliders.items():
            s.set_value(color.value_at(anim[k], ph) if k in anim else g[k])
            s.set_anim(anim.get(k), dur, ph, enabled=anim_ctx is not None)
        self.curve.set_curves(g["curves"])
        for k, row in self.hsl_sliders.items():
            for i, s in enumerate(row):
                key = f"hsl:{k}:{i}"
                s.set_value(color.value_at(anim[key], ph) if key in anim else g["hsl"][k][i])
                s.set_anim(anim.get(key), dur, ph, enabled=anim_ctx is not None)
        self.info.setText(info)

    def set_playhead(self, t):
        for s in self.all_sliders():
            s.set_playhead(t)

    def set_editable(self, ok: bool):
        for w in list(self.sliders.values()) + [s for r in self.hsl_sliders.values() for s in r]:
            w.setEnabled(ok)
        self.curve.setEnabled(ok)
