"""Pannello "Clip": proprietà dei pezzi selezionati in timeline
(velocità, inquadratura, dissolvenze, volume, scritte)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from .. import speed as spd
from .speed_curve import SpeedCurveWidget
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFontComboBox, QFormLayout,
                               QGridLayout,
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


_FALLBACK = {("video", "reverse"): False, ("audio", "reverse"): False, ("video", "flip_h"): False, ("video", "flip_v"): False, ("video", "stab"): False, ("video", "stab_strength"): 0.5, ("video", "stab_crop"): 0.10,
             ("text", "outline"): False, ("text", "outline_w"): 0.06, ("text", "glow"): False,
             ("text", "glow_amt"): 0.6, ("text", "spacing"): 0.0, ("text", "caps"): False,
             ("text", "anim_in"): "none", ("text", "anim_in_dur"): 0.6,
             ("text", "anim_out"): "none", ("text", "anim_out_dur"): 0.6,
             ("audio", "hp_hz"): 0, ("audio", "denoise"): 0.0, ("audio", "normalize"): False}

# stili pronti per le scritte: un clic e cambiano contorno, ombra, riquadro, lettere…
TEXT_STYLES = [
    ("Pulito", {"bold": True, "caps": False, "spacing": 0.0, "shadow": True, "outline": False, "glow": False,
                "box": False}),
    ("Sottotitolo", {"bold": False, "caps": False, "spacing": 0.0, "shadow": False, "outline": False,
                     "glow": False, "box": True, "box_color": "#000000", "box_opacity": 0.55, "size": 5.0,
                     "pos_y": 0.88, "pos_x": 0.5}),
    ("Contorno", {"bold": True, "caps": False, "shadow": False, "outline": True, "outline_color": "#000000",
                  "outline_w": 0.07, "glow": False, "box": False, "color": "#ffffff"}),
    ("Cinema", {"bold": False, "caps": True, "spacing": 18.0, "shadow": False, "outline": False,
                "glow": False, "box": False, "color": "#f2f2f2", "size": 6.0}),
    ("Neon", {"bold": True, "caps": False, "shadow": False, "outline": False, "glow": True,
              "glow_color": "#e8a33d", "glow_amt": 0.9, "color": "#fff3d6", "box": False}),
    ("Titolo", {"bold": True, "caps": True, "spacing": 6.0, "shadow": True, "outline": False, "glow": False,
                "box": False, "size": 12.0}),
]

AUDIO_PRESETS = [
    ("Vento", {"hp_hz": 150, "denoise": 0.3}),
    ("Voce", {"hp_hz": 90, "denoise": 0.5, "normalize": True}),
    ("Nessuna", {"hp_hz": 0, "denoise": 0.0, "normalize": False}),
]

ANIM_IN = [("Nessuna", "none"), ("Sale dal basso", "up"), ("Scende dall'alto", "down"),
           ("Entra da destra", "left"), ("Entra da sinistra", "right"), ("Zoom con rimbalzo", "zoom"),
           ("Macchina da scrivere", "type")]
ANIM_OUT = [("Nessuna", "none"), ("Sale verso l'alto", "up"), ("Scende verso il basso", "down"),
            ("Esce a sinistra", "left"), ("Esce a destra", "right"), ("Rimpicciolisce", "zoom"),
            ("Si cancella (macchina)", "type")]


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
    stretch_toggled = Signal(bool)  # «allunga la clip» acceso/spento
    rot_anim = Signal(object)  # punti [[t, gradi]] della rotazione animata, o None = fissa
    rot_seek = Signal(float)  # tempo relativo al pezzo da mostrare nel visore
    preview_at = Signal(float)  # frazione del pezzo da mostrare nel visore (nodi della curva)

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
        self._stretch_chks = []
        self._stretch = False

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
        st = QCheckBox("Allunga la clip nella timeline")
        st.setToolTip("Spuntato: cambiando la velocità la clip diventa più lunga o più corta in timeline.\n"
                      "Non spuntato: la clip resta lunga com'è e cambia solo la velocità "
                      "(si usa più o meno file originale).")
        st.setChecked(getattr(self, "_stretch", False))
        self._stretch_chks.append(st)

        def stretch_changed(on):
            self._stretch = on
            for o in self._stretch_chks:
                if o.isChecked() != on:
                    o.setChecked(on)
            self.stretch_toggled.emit(on)

        st.toggled.connect(stretch_changed)
        f.addRow(st)
        rv = self._check(kind, "reverse", "Riproduzione inversa")
        rv.setToolTip("La clip (con il suo audio collegato) scorre all'indietro, dalla fine all'inizio "
                      "della parte usata.")
        f.addRow(rv)
        chk = QCheckBox("Velocità variabile (curva)")
        f.addRow(chk)
        curve_box = QWidget()
        vl = QVBoxLayout(curve_box)
        vl.setContentsMargins(0, 0, 0, 0)
        cw = SpeedCurveWidget()
        cw.pressed.connect(lambda: self._begin("speed_curve"))
        cw.changed.connect(lambda pts: self._emit(kind, "speed_curve", pts))
        cw.node_at.connect(self.preview_at)
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
        note = QLabel("Con «Allunga la clip» la durata cambia e ciò che segue in timeline si sposta; "
                      "senza, la durata resta e cambia la parte di file usata. "
                      "L'audio collegato segue la stessa velocità.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#808080; font-size:11px;")
        f.addRow(note)
        return gb

    def _transition_group(self):
        """Transizione con la clip che finisce dove inizia questa (stessa traccia)."""
        from ..project import TRANSITIONS

        gb, f = _group("Transizione con la clip precedente")
        self.tr_type = QComboBox()
        self.tr_type.addItem("Nessuna", None)
        for k, lab in TRANSITIONS.items():
            self.tr_type.addItem(lab, k)
        self.tr_dur = QDoubleSpinBox()
        self.tr_dur.setRange(0.1, 10.0)
        self.tr_dur.setSingleStep(0.1)
        self.tr_dur.setDecimals(1)
        self.tr_dur.setSuffix(" s")
        self.tr_dur.setValue(1.0)
        self.tr_dur.setKeyboardTracking(False)
        self.tr_audio = QCheckBox("Incrocia anche l'audio")
        self.tr_audio.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(self.tr_type, 1)
        row.addWidget(self.tr_dur)
        f.addRow(row)
        f.addRow(self.tr_audio)
        self.tr_info = QLabel("")
        self.tr_info.setWordWrap(True)
        self.tr_info.setStyleSheet("color:#808080; font-size:11px;")
        f.addRow(self.tr_info)

        def emit_tr(*_):
            if self._loading:
                return
            self._begin("trans")
            kind = self.tr_type.currentData()
            self._emit("video", "trans", None if not kind else
                       {"type": kind, "dur": self.tr_dur.value(), "audio": self.tr_audio.isChecked()})

        self.tr_type.currentIndexChanged.connect(emit_tr)
        self.tr_dur.valueChanged.connect(emit_tr)
        self.tr_audio.toggled.connect(emit_tr)
        return gb

    def _freeze_group(self):
        gb, f = _group("Fermo immagine")
        row = QHBoxLayout()
        self.freeze_dur = QDoubleSpinBox()
        self.freeze_dur.setRange(0.1, 60.0)
        self.freeze_dur.setSingleStep(0.5)
        self.freeze_dur.setDecimals(1)
        self.freeze_dur.setSuffix(" s")
        self.freeze_dur.setValue(2.0)
        b = QPushButton("Ferma alla testina")
        b.setToolTip("Blocca il fotogramma alla testina per la durata scelta (Maiusc+F).\n"
                     "Ciò che viene dopo si sposta in avanti; la musica che sta già suonando continua.")
        b.clicked.connect(lambda: self.action.emit(f"freeze:{self.freeze_dur.value()}"))
        row.addWidget(self.freeze_dur)
        row.addWidget(b, 1)
        f.addRow(row)
        return gb

    def _stab_group(self):
        gb, f = _group("Stabilizzazione")
        ch = self._check("video", "stab", "Smorza vibrazioni e movimenti bruschi")
        ch.setToolTip("Analizza il movimento della camera (una volta, in background) e corregge\n"
                      "ogni fotogramma spostandolo e ruotandolo. Anteprima ed export coincidono.")
        f.addRow(ch)
        s = self._slider("video", "stab_strength", 0, 100, 1, 0, " %", 50, scale=100)
        s.setToolTip("Basso: toglie solo il tremolio e lascia i movimenti come sono.\n"
                     "Alto: ammorbidisce anche i movimenti, fino a una camera quasi ferma.")
        f.addRow("Intensità", s)
        s = self._slider("video", "stab_crop", 2, 30, 1, 0, " %", 10, scale=100)
        s.setToolTip("Di quanto si ingrandisce l'immagine per nascondere i bordi.\n"
                     "Più ritaglio = più spazio per correggere, ma inquadratura più stretta.")
        f.addRow("Ritaglio", s)
        self.stab_info = QLabel("")
        self.stab_info.setWordWrap(True)
        self.stab_info.setStyleSheet("color:#808080; font-size:11px;")
        f.addRow(self.stab_info)
        return gb

    def set_stab_info(self, text, busy=False):
        self.stab_info.setText(text)
        self.stab_info.setStyleSheet("color:#e8a33d; font-size:11px;" if busy else "color:#808080; font-size:11px;")

    def set_transition_info(self, text):
        self.tr_info.setText(text)

    def _orient_group(self):
        from .color_panel import GradeSlider

        gb, f = _group("Specchio e rotazione")
        row = QHBoxLayout()
        row.addWidget(self._check("video", "flip_h", "Specchia ↔"))
        row.addWidget(self._check("video", "flip_v", "Specchia ↕"))
        f.addRow(row)
        self.rot = GradeSlider("rotate", "Rotazione", -360, 360, 0.5)
        self.rot.lab.setFixedWidth(70)
        self.rot.anim_chk.setToolTip("Anima la rotazione nel tempo: l'angolo cambia lungo il pezzo "
                                     "(clic sul grafico per aggiungere punti)")
        self.rot.pressed.connect(self._begin)
        self.rot.changed.connect(lambda k, v: self._emit("video", "rotate", v))
        self.rot.anim_changed.connect(lambda k, pts: None if self._loading else self.rot_anim.emit(pts))
        self.rot.anim_seek.connect(self.rot_seek)
        f.addRow(self.rot)
        row = QHBoxLayout()
        for lab, act, tip in (("↺ 90°", "rot_ccw", "Ruota di 90° in senso antiorario"),
                              ("↻ 90°", "rot_cw", "Ruota di 90° in senso orario"),
                              ("0°", "rot_zero", "Rotazione a zero")):
            b = QPushButton(lab)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, a=act: self.action.emit(a))
            row.addWidget(b)
        f.addRow(row)
        hint = QLabel("Gradi in senso orario. Gli angoli vuoti restano neri.")
        hint.setStyleSheet("color:#808080; font-size:11px;")
        f.addRow(hint)
        return gb

    def _video_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.addWidget(self._transition_group())
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
        lay.addWidget(self._orient_group())
        lay.addWidget(self._stab_group())
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
        lay.addWidget(self._freeze_group())
        lay.addStretch(1)
        return w

    def _audio_page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 4, 0)
        gb, f = _group("Volume")
        f.addRow(self._slider("audio", "gain_db", -40, 12, 0.5, 1, " dB", 0))
        hint = QLabel("Volume disegnato: sulla clip in timeline clicca la linea gialla per aggiungere "
                      "un punto, trascinalo per alzare o abbassare, doppio clic per toglierlo.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#808080; font-size:11px;")
        f.addRow(hint)
        b = QPushButton("Azzera volume disegnato")
        b.clicked.connect(lambda: self.action.emit("clear_gain"))
        f.addRow(b)
        lay.addWidget(gb)
        gb, f = _group("Pulizia")
        f.addRow("Taglia i bassi (vento)", self._slider("audio", "hp_hz", 0, 400, 10, 0, " Hz", 0))
        f.addRow("Riduci il rumore", self._slider("audio", "denoise", 0, 100, 1, 0, " %", 0, scale=100))
        f.addRow(self._check("audio", "normalize", "Normalizza il volume"))
        self.norm_label = QLabel("")
        self.norm_label.setStyleSheet("color:#9a9a9a; font-size:11px;")
        f.addRow(self.norm_label)
        grid = QGridLayout()
        for n, (lab, props) in enumerate(AUDIO_PRESETS):
            b = QPushButton(lab)
            b.clicked.connect(lambda _=False, pp=props: self._apply_audio_preset(pp))
            grid.addWidget(b, 0, n)
        f.addRow(grid)
        b = QPushButton("Ricalcola il volume")
        b.setToolTip("Misura di nuovo la clip (dopo aver rifilato o cambiato la pulizia)")
        b.clicked.connect(lambda: self.action.emit("audio_norm"))
        f.addRow(b)
        hint = QLabel("La pulizia non cambia il file originale. La riduzione del rumore ha bisogno di qualche "
                      "secondo: finché non è pronta senti l'audio com'era.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#808080; font-size:11px;")
        f.addRow(hint)
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
        gb, f = _group("Stili pronti")
        grid = QGridLayout()
        for n, (lab, props) in enumerate(TEXT_STYLES):
            b = QPushButton(lab)
            b.clicked.connect(lambda _=False, pp=props: self._apply_text_style(pp))
            grid.addWidget(b, n // 3, n % 3)
        f.addRow(grid)
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
        f.addRow("Opacità", self._slider("text", "opacity", 0, 100, 1, 0, " %", 100, scale=100))
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
        gb, f = _group("Effetti")
        row = QHBoxLayout()
        row.addWidget(self._check("text", "outline", "Contorno"))
        self.outline_btn = ColorButton()
        self.outline_btn.picked.connect(lambda c: (self._begin("outline_color"), self._emit("text", "outline_color", c)))
        row.addWidget(self.outline_btn)
        row.addStretch(1)
        f.addRow(row)
        f.addRow("Spessore", self._slider("text", "outline_w", 1, 20, 0.5, 1, " %", 6, scale=100))
        row = QHBoxLayout()
        row.addWidget(self._check("text", "glow", "Alone luminoso"))
        self.glow_btn = ColorButton()
        self.glow_btn.picked.connect(lambda c: (self._begin("glow_color"), self._emit("text", "glow_color", c)))
        row.addWidget(self.glow_btn)
        row.addStretch(1)
        f.addRow(row)
        f.addRow("Intensità", self._slider("text", "glow_amt", 0, 100, 1, 0, " %", 60, scale=100))
        f.addRow("Spaziatura lettere", self._slider("text", "spacing", -10, 60, 1, 0, " %", 0))
        f.addRow(self._check("text", "caps", "Tutto maiuscolo"))
        lay.addWidget(gb)
        gb, f = _group("Animazione")
        f.addRow("Entrata", self._combo("text", "anim_in", ANIM_IN))
        f.addRow("Durata entrata", self._spin("text", "anim_in_dur", 0.1, 5, 0.1))
        f.addRow("Uscita", self._combo("text", "anim_out", ANIM_OUT))
        f.addRow("Durata uscita", self._spin("text", "anim_out_dur", 0.1, 5, 0.1))
        hint = QLabel("Le animazioni si vedono riproducendo. Nell'export si può usare anche la dissolvenza sotto.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#808080; font-size:11px;")
        f.addRow(hint)
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

    def _apply_audio_preset(self, props):
        self._begin("audio_preset")
        for k, v in props.items():
            self._emit("audio", k, v)
        self.action.emit("refresh_inspector")

    def _apply_text_style(self, props):
        """Stile pronto: un solo passo di annulla per tutte le proprietà."""
        self._begin("text_style")
        for k, v in props.items():
            self._emit("text", k, v)
        self.action.emit("refresh_inspector")

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
    def stretch_on(self) -> bool:
        return bool(self._stretch)

    def set_stretch(self, on: bool):
        self._stretch = bool(on)
        for o in self._stretch_chks:
            o.setChecked(bool(on))

    def show_item(self, item, count=1, duration=None, playhead=0.0):
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
                if val is None:  # progetti salvati prima che esistesse questa regolazione
                    val = _FALLBACK.get((kind, key))
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
            if kind == "audio":
                nd = float(item.get("norm_db", 0.0) or 0.0)
                self.norm_label.setText(f"Volume applicato: {nd:+.1f} dB" if item.get("normalize") else "")
            if kind == "video":
                pts = item.get("rotate_anim")
                ang = float(item.get("rotate", 0.0) or 0.0)
                self.rot.set_value(ang)
                self.rot.set_anim(pts, duration or 1.0, playhead)
                tr = item.get("trans") or {}
                i = self.tr_type.findData(tr.get("type")) if tr else 0
                self.tr_type.setCurrentIndex(max(0, i))
                self.tr_dur.setValue(float(tr.get("dur", self.tr_dur.value())) if tr else self.tr_dur.value())
                self.tr_audio.setChecked(bool(tr.get("audio", True)) if tr else True)
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
                self.outline_btn.set_color(item.get("outline_color", "#000000"))
                self.glow_btn.set_color(item.get("glow_color", "#e8a33d"))
            if kind == "shape":
                self.shape_color.set_color(item.get("color", "#000000"))
        finally:
            self._loading = False
