"""Pannello "Clip": proprietà dei pezzi selezionati in timeline
(velocità, inquadratura, dissolvenze, volume, scritte)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from .. import speed as spd
from .speed_curve import SpeedCurveWidget
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFontComboBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSlider,
                               QStackedWidget, QVBoxLayout, QWidget)


class ValueSlider(QWidget):
    """Cursore + casella numerica. Doppio clic sull'etichetta = valore iniziale."""

    pressed = Signal()
    changed = Signal(float)

    def __init__(self, lo, hi, step, decimals=0, suffix="", default=0.0, parent=None):
        super().__init__(parent)
        self.scale = 1.0 / step
        self.default = default
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.sl = QSlider(Qt.Horizontal)
        self.sl.setRange(int(round(lo * self.scale)), int(round(hi * self.scale)))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(step)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(78)
        self.spin.setAlignment(Qt.AlignRight)
        lay.addWidget(self.sl, 1)
        lay.addWidget(self.spin)
        self._block = False
        self.sl.sliderPressed.connect(self.pressed)
        self.sl.valueChanged.connect(self._from_slider)
        self.spin.editingFinished.connect(self._from_spin)

    def _from_slider(self, v):
        if self._block:
            return
        val = v / self.scale
        self._block = True
        self.spin.setValue(val)
        self._block = False
        if not self.sl.isSliderDown():
            self.pressed.emit()
        self.changed.emit(val)

    def _from_spin(self):
        if self._block:
            return
        val = self.spin.value()
        self.pressed.emit()
        self._block = True
        self.sl.setValue(int(round(val * self.scale)))
        self._block = False
        self.changed.emit(val)

    def set_value(self, v):
        self._block = True
        self.sl.setValue(int(round(v * self.scale)))
        self.spin.setValue(v)
        self._block = False

    def reset(self):
        self.pressed.emit()
        self.set_value(self.default)
        self.changed.emit(self.default)


class ColorButton(QPushButton):
    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(46)
        self._c = "#ffffff"
        self.clicked.connect(self._pick)

    def set_color(self, c):
        self._c = c
        self.setStyleSheet(f"background:{c}; border:1px solid #555; border-radius:3px;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._c), self, "Colore")
        if c.isValid():
            self.set_color(c.name())
            self.picked.emit(c.name())


def _group(title):
    gb = QGroupBox(title)
    form = QFormLayout(gb)
    form.setContentsMargins(8, 6, 8, 6)
    form.setSpacing(5)
    form.setLabelAlignment(Qt.AlignLeft)
    return gb, form


class Inspector(QWidget):
    """Emette begin(chiave) prima di una modifica e changed(tipo, chiave, valore)."""

    begin = Signal(str)
    changed = Signal(str, str, object)  # tipo pezzo ('video', 'audio', 'text'), chiave, valore
    action = Signal(str)  # 'fill', 'center', 'add_text'

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        self.title = QLabel("")
        self.title.setStyleSheet("color:#e8a33d; font-weight:600;")
        self.title.setWordWrap(True)
        outer.addWidget(self.title)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self._loading = False
        self._w = {}
        self._speed_ui = {}

        # pagina vuota
        empty = QWidget()
        el = QVBoxLayout(empty)
        lab = QLabel("Seleziona un pezzo nella timeline per regolarne velocità, inquadratura, "
                     "dissolvenze o volume.\n\nPer una scritta: tasto T; per un riquadro colorato: tasto R.")
        lab.setWordWrap(True)
        lab.setStyleSheet("color:#9a9a9a;")
        el.addWidget(lab)
        b = QPushButton("+ Aggiungi scritta alla testina")
        b.clicked.connect(lambda: self.action.emit("add_text"))
        el.addWidget(b)
        b = QPushButton("+ Aggiungi riquadro colorato alla testina")
        b.clicked.connect(lambda: self.action.emit("add_box"))
        el.addWidget(b)
        el.addStretch(1)
        self.stack.addWidget(empty)

        self.stack.addWidget(self._scroll(self._video_page()))
        self.stack.addWidget(self._scroll(self._audio_page()))
        self.stack.addWidget(self._scroll(self._text_page()))
        self.stack.addWidget(self._scroll(self._shape_page()))

    def _scroll(self, w):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(w)
        return sa

    # ------------------------------------------------------------ costruzione
    def _slider(self, kind, key, lo, hi, step, dec=0, suffix="", default=0.0, scale=1.0):
        """scale: valore mostrato = valore salvato * scale (es. 100 per le %)."""
        s = ValueSlider(lo, hi, step, dec, suffix, default)
        s.pressed.connect(lambda: self._begin(key))
        s.changed.connect(lambda v: self._emit(kind, key, v / scale))
        self._w[(kind, key)] = (s, scale)
        return s

    def _spin(self, kind, key, lo, hi, step, dec=1, suffix=" s"):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setDecimals(dec)
        sp.setSuffix(suffix)
        sp.setKeyboardTracking(False)
        sp.valueChanged.connect(lambda v: (self._begin(key + "_spin"), self._emit(kind, key, v)))
        self._w[(kind, key)] = (sp, 1.0)
        return sp

    def _combo(self, kind, key, items):
        cb = QComboBox()
        for lab, val in items:
            cb.addItem(lab, val)
        cb.currentIndexChanged.connect(lambda i: (self._begin(key), self._emit(kind, key, cb.itemData(i))))
        self._w[(kind, key)] = (cb, None)
        return cb

    def _check(self, kind, key, text):
        ch = QCheckBox(text)
        ch.toggled.connect(lambda on: (self._begin(key), self._emit(kind, key, on)))
        self._w[(kind, key)] = (ch, None)
        return ch

    def _speed_group(self, kind):
        gb, f = _group("Velocità")
        const = QWidget()
        cl = QVBoxLayout(const)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self._slider(kind, "speed", 10, 800, 5, 0, " %", 100, scale=100))
        row = QHBoxLayout()
        for p in (25, 50, 100, 200, 400):
            b = QPushButton(f"{p}%")
            b.setFixedWidth(52)
            b.clicked.connect(lambda _=False, pp=p: (self._begin("speed_btn"), self._emit(kind, "speed", pp / 100)))
            row.addWidget(b)
        row.addStretch(1)
        cl.addLayout(row)
        f.addRow(const)
        chk = QCheckBox("Velocità variabile (curva)")
        f.addRow(chk)
        curve_box = QWidget()
        vl = QVBoxLayout(curve_box)
        vl.setContentsMargins(0, 0, 0, 0)
        cw = SpeedCurveWidget()
        cw.pressed.connect(lambda: self._begin("speed_curve"))
        cw.changed.connect(lambda pts: self._emit(kind, "speed_curve", pts))
        vl.addWidget(cw)
        prow = QHBoxLayout()
        preset = QComboBox()
        preset.addItem("Modelli…", None)
        for name, pts in spd.PRESETS.items():
            preset.addItem(name, pts)

        def use_preset(i):
            pts = preset.itemData(i)
            if pts and not self._loading:
                self._begin("speed_preset")
                cw.set_points(pts)
                self._emit(kind, "speed_curve", pts)
            preset.setCurrentIndex(0)

        preset.currentIndexChanged.connect(use_preset)
        flat = QPushButton("Piatta 1x")
        flat.clicked.connect(lambda: (self._begin("speed_flat"), cw.set_points(None),
                                      self._emit(kind, "speed_curve", [[0.0, 1.0], [1.0, 1.0]])))
        prow.addWidget(preset, 1)
        prow.addWidget(flat)
        vl.addLayout(prow)
        f.addRow(curve_box)
        dur = QLabel("")
        dur.setStyleSheet("color:#bbb;")
        f.addRow(dur)

        def toggled(on):
            const.setVisible(not on)
            curve_box.setVisible(on)
            if self._loading:
                return
            self._begin("speed_mode")
            if on:
                v = self._w[(kind, "speed")][0].spin.value() / 100.0
                pts = [[0.0, v], [1.0, v]]
                cw.set_points(pts)
                self._emit(kind, "speed_curve", pts)
            else:
                self._emit(kind, "speed_curve", None)

        chk.toggled.connect(toggled)
        curve_box.setVisible(False)
        self._speed_ui[kind] = (chk, cw, dur, const, curve_box)
        note = QLabel("Cambia la durata del pezzo; ciò che segue in timeline si sposta. "
                      "L'audio collegato segue la stessa velocità.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#808080; font-size:11px;")
        f.addRow(note)
        return gb

    def _video_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.addWidget(self._speed_group("video"))
        gb, f = _group("Inquadratura")
        f.addRow("Zoom", self._slider("video", "zoom", 10, 400, 1, 0, " %", 100, scale=100))
        f.addRow("Orizzontale", self._slider("video", "pos_x", -100, 100, 1, 0, " %", 0, scale=100))
        f.addRow("Verticale", self._slider("video", "pos_y", -100, 100, 1, 0, " %", 0, scale=100))
        row = QHBoxLayout()
        for lab, act, tip in (("Adatta", "fit", "Tutta l'immagine visibile (bande se serve)"),
                              ("Riempi", "fill", "Riempie il fotogramma, senza bande"),
                              ("Centra", "center", "Riporta al centro")):
            b = QPushButton(lab)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, a=act: self.action.emit(a))
            row.addWidget(b)
        f.addRow(row)
        hint = QLabel("Puoi anche trascinare l'immagine nel visore.")
        hint.setStyleSheet("color:#808080; font-size:11px;")
        f.addRow(hint)
        lay.addWidget(gb)
        gb, f = _group("Dissolvenze")
        cols = [("da/verso nero", "black"), ("da/verso bianco", "white")]
        row = QHBoxLayout()
        row.addWidget(self._spin("video", "fade_in", 0, 10, 0.1))
        row.addWidget(self._combo("video", "fade_in_color", cols))
        f.addRow("Entrata", row)
        row = QHBoxLayout()
        row.addWidget(self._spin("video", "fade_out", 0, 10, 0.1))
        row.addWidget(self._combo("video", "fade_out_color", cols))
        f.addRow("Uscita", row)
        self.fade_audio = QCheckBox("Sfuma anche l'audio collegato")
        self.fade_audio.setChecked(True)
        f.addRow(self.fade_audio)
        lay.addWidget(gb)
        lay.addStretch(1)
        return w

    def _audio_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 4, 0)
        gb, f = _group("Volume")
        f.addRow(self._slider("audio", "gain_db", -40, 12, 0.5, 1, " dB", 0))
        lay.addWidget(gb)
        gb, f = _group("Dissolvenze audio")
        f.addRow("Entrata", self._spin("audio", "fade_in", 0, 10, 0.1))
        f.addRow("Uscita", self._spin("audio", "fade_out", 0, 10, 0.1))
        lay.addWidget(gb)
        lay.addWidget(self._speed_group("audio"))
        lay.addStretch(1)
        return w

    def _text_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 4, 0)
        gb, f = _group("Scritta")
        self.text_edit = QPlainTextEdit()
        self.text_edit.setFixedHeight(70)
        self._text_timer = QTimer(self)
        self._text_timer.setSingleShot(True)
        self._text_timer.setInterval(120)
        self._text_timer.timeout.connect(lambda: self._emit("text", "text", self.text_edit.toPlainText()))
        self.text_edit.textChanged.connect(self._text_typed)
        f.addRow(self.text_edit)
        self.font_combo = QFontComboBox()
        self.font_combo.currentFontChanged.connect(
            lambda fnt: (self._begin("font"), self._emit("text", "font", fnt.family())))
        f.addRow("Carattere", self.font_combo)
        f.addRow("Dimensione", self._slider("text", "size", 1, 40, 0.5, 1, " %", 8))
        row = QHBoxLayout()
        self.color_btn = ColorButton()
        self.color_btn.picked.connect(lambda c: (self._begin("color"), self._emit("text", "color", c)))
        row.addWidget(self.color_btn)
        row.addWidget(self._check("text", "bold", "Grassetto"))
        row.addWidget(self._check("text", "italic", "Corsivo"))
        row.addStretch(1)
        f.addRow("Colore", row)
        f.addRow("Allineamento", self._combo("text", "align", [("Al centro", "center"), ("A sinistra", "left"),
                                                                ("A destra", "right")]))
        lay.addWidget(gb)
        gb, f = _group("Posizione")
        f.addRow("Orizzontale", self._slider("text", "pos_x", 0, 100, 0.5, 1, " %", 50, scale=100))
        f.addRow("Verticale", self._slider("text", "pos_y", 0, 100, 0.5, 1, " %", 50, scale=100))
        row = QHBoxLayout()
        for lab, y in (("In alto", 0.12), ("Al centro", 0.5), ("In basso", 0.85)):
            b = QPushButton(lab)
            b.clicked.connect(lambda _=False, yy=y: (self._begin("pos_btn"), self._emit("text", "pos_y", yy),
                                                      self._emit("text", "pos_x", 0.5)))
            row.addWidget(b)
        f.addRow(row)
        hint = QLabel("Puoi anche trascinare la scritta nel visore.")
        hint.setStyleSheet("color:#808080; font-size:11px;")
        f.addRow(hint)
        lay.addWidget(gb)
        gb, f = _group("Stile")
        f.addRow(self._check("text", "shadow", "Ombra"))
        row = QHBoxLayout()
        row.addWidget(self._check("text", "box", "Riquadro"))
        self.box_btn = ColorButton()
        self.box_btn.picked.connect(lambda c: (self._begin("box_color"), self._emit("text", "box_color", c)))
        row.addWidget(self.box_btn)
        row.addStretch(1)
        f.addRow(row)
        f.addRow("Opacità riquadro", self._slider("text", "box_opacity", 0, 100, 1, 0, " %", 50, scale=100))
        lay.addWidget(gb)
        gb, f = _group("Tempi")
        f.addRow("Durata", self._spin("text", "duration", 0.1, 600, 0.5))
        f.addRow("Entrata (sfuma)", self._spin("text", "fade_in", 0, 10, 0.1))
        f.addRow("Uscita (sfuma)", self._spin("text", "fade_out", 0, 10, 0.1))
        lay.addWidget(gb)
        lay.addStretch(1)
        return w

    def _begin(self, key):
        if not self._loading:
            self.begin.emit(key)

    def _shape_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 4, 0)
        gb, f = _group("Riquadro")
        row = QHBoxLayout()
        self.shape_color = ColorButton()
        self.shape_color.picked.connect(lambda c: (self._begin("shape_color"), self._emit("shape", "color", c)))
        row.addWidget(self.shape_color)
        for lab, col in (("Nero", "#000000"), ("Bianco", "#ffffff")):
            b = QPushButton(lab)
            b.clicked.connect(lambda _=False, cc=col: (self._begin("shape_color"), self.shape_color.set_color(cc),
                                                        self._emit("shape", "color", cc)))
            row.addWidget(b)
        row.addStretch(1)
        f.addRow("Colore", row)
        f.addRow("Opacità", self._slider("shape", "opacity", 0, 100, 1, 0, " %", 100, scale=100))
        f.addRow("Angoli", self._slider("shape", "radius", 0, 100, 1, 0, " %", 0, scale=100))
        lay.addWidget(gb)
        gb, f = _group("Dimensioni e posizione")
        f.addRow("Larghezza", self._slider("shape", "width", 1, 100, 0.5, 1, " %", 60, scale=100))
        f.addRow("Altezza", self._slider("shape", "height", 1, 100, 0.5, 1, " %", 16, scale=100))
        f.addRow("Orizzontale", self._slider("shape", "pos_x", 0, 100, 0.5, 1, " %", 50, scale=100))
        f.addRow("Verticale", self._slider("shape", "pos_y", 0, 100, 0.5, 1, " %", 50, scale=100))
        row = QHBoxLayout()
        for lab, vals in (("Schermo pieno", (1.0, 1.0, 0.5, 0.5)), ("Fascia in basso", (1.0, 0.16, 0.5, 0.86)),
                          ("Sotto un titolo", (0.6, 0.16, 0.5, 0.84))):
            b = QPushButton(lab)
            b.clicked.connect(lambda _=False, v=vals: self._shape_preset(v))
            row.addWidget(b)
        f.addRow(row)
        hint = QLabel("Puoi anche trascinarlo nel visore. Per mettere una scritta sopra al riquadro,\n"
                      "la scritta deve stare su una traccia più alta.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#808080; font-size:11px;")
        f.addRow(hint)
        lay.addWidget(gb)
        gb, f = _group("Tempi")
        f.addRow("Durata", self._spin("shape", "duration", 0.1, 600, 0.5))
        f.addRow("Entrata (sfuma)", self._spin("shape", "fade_in", 0, 10, 0.1))
        f.addRow("Uscita (sfuma)", self._spin("shape", "fade_out", 0, 10, 0.1))
        lay.addWidget(gb)
        lay.addStretch(1)
        return w

    def _shape_preset(self, vals):
        self._begin("shape_preset")
        for key, v in zip(("width", "height", "pos_x", "pos_y"), vals):
            self._emit("shape", key, v)

    def _text_typed(self):
        if self._loading:
            return
        self._begin("text")
        self._text_timer.start()

    def _emit(self, kind, key, value):
        if not self._loading:
            self.changed.emit(kind, key, value)

    # -------------------------------------------------------------- dati
    def show_item(self, item, count=1, duration=None):
        """Mostra le proprietà di un pezzo (None = pagina vuota)."""
        self._loading = True
        try:
            if item is None:
                self.title.setText("")
                self.stack.setCurrentIndex(0)
                return
            kind = item["kind"]
            name = item.get("origin") or item.get("source") or ""
            if kind == "text":
                first = (item.get("text") or "").split("\n")[0]
                name = f"Scritta «{first[:30]}»"
            elif kind == "shape":
                name = "Riquadro colorato"
            extra = f"  (+{count - 1} selezionati)" if count > 1 else ""
            self.title.setText(f"{name}{extra}")
            self.stack.setCurrentIndex({"video": 1, "audio": 2, "text": 3, "shape": 4}[kind])
            for (k, key), (w, scale) in self._w.items():
                if k != kind:
                    continue
                if key == "duration":
                    val = duration
                else:
                    val = item.get(key)
                if val is None:
                    continue
                if isinstance(w, ValueSlider):
                    w.set_value(float(val) * scale)
                elif isinstance(w, QDoubleSpinBox):
                    w.setValue(float(val))
                elif isinstance(w, QComboBox):
                    i = w.findData(val)
                    if i >= 0:
                        w.setCurrentIndex(i)
                elif isinstance(w, QCheckBox):
                    w.setChecked(bool(val))
            if kind in self._speed_ui:
                chk, cw, dlab, const, box = self._speed_ui[kind]
                pts = spd.curve_points(item)
                chk.setChecked(pts is not None)
                const.setVisible(pts is None)
                box.setVisible(pts is not None)
                cw.set_points(pts)
                if pts is None:
                    self._w[(kind, "speed")][0].set_value(spd.const_speed(item) * 100)
                if duration is not None:
                    dlab.setText(f"Durata in timeline: {duration:.2f} s  ·  velocità media "
                                 f"{spd.mean_speed(item) * 100:.0f}%")
            if kind == "text":
                if self.text_edit.toPlainText() != item.get("text", ""):
                    self.text_edit.setPlainText(item.get("text", ""))
                fam = item.get("font") or ""
                if fam:
                    self.font_combo.setCurrentFont(QFont(fam))
                self.color_btn.set_color(item.get("color", "#ffffff"))
                self.box_btn.set_color(item.get("box_color", "#000000"))
            if kind == "shape":
                self.shape_color.set_color(item.get("color", "#000000"))
        finally:
            self._loading = False
