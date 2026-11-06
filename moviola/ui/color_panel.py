"""Pannello colore in stile fotografico: cursori, curva, HSL per colore."""
from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSlider, QTabWidget,
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


class GradeSlider(QWidget):
    """Cursore con etichetta e valore; doppio clic = azzera."""

    pressed = Signal(str)
    changed = Signal(str, float)

    def __init__(self, key, label, lo, hi, step, swatch=None, parent=None):
        super().__init__(parent)
        self.key = key
        self.step = step
        self.scale = 1.0 / step
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(6)
        self.lab = _ResetLabel(label)
        self.lab.setFixedWidth(78)
        if swatch:
            self.lab.setText(f'<span style="color:{swatch}">■</span> {label}')
        self.lab.setToolTip("Doppio clic per azzerare")
        self.sl = _ResetSlider(Qt.Horizontal)
        self.sl.setRange(int(round(lo * self.scale)), int(round(hi * self.scale)))
        self.sl.setSingleStep(1)
        self.sl.setPageStep(int(10 * max(1, self.scale / 20)))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(2 if step < 1 else 0)
        self.spin.setFixedWidth(56)
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.setAlignment(Qt.AlignRight)
        lay.addWidget(self.lab)
        lay.addWidget(self.sl, 1)
        lay.addWidget(self.spin)
        self._block = False
        self.sl.sliderPressed.connect(lambda: self.pressed.emit(self.key))
        self.sl.valueChanged.connect(self._from_slider)
        self.spin.editingFinished.connect(self._from_spin)
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

    def show_grade(self, grade, info=""):
        g = color.normalize(grade)
        for k, s in self.sliders.items():
            s.set_value(g[k])
        self.curve.set_curves(g["curves"])
        for k, row in self.hsl_sliders.items():
            for i, s in enumerate(row):
                s.set_value(g["hsl"][k][i])
        self.info.setText(info)

    def set_editable(self, ok: bool):
        for w in list(self.sliders.values()) + [s for r in self.hsl_sliders.values() for s in r]:
            w.setEnabled(ok)
        self.curve.setEnabled(ok)
