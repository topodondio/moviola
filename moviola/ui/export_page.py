"""Scheda ESPORTAZIONE: modelli (YouTube, Instagram, Full HD, 4K…), cosa esportare
(tutto, tratto I–O, pezzi selezionati, tratto a scelta, capitoli) e formato."""
from __future__ import annotations

import copy
import os
import subprocess
import sys
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import export
from .text_render import render_png

MODES = ("all", "range", "selection", "custom", "chapters")
QUALITIES = (("Alta", "alta"), ("Buona", "buona"), ("Leggera (file piccolo)", "leggera"))
FPS_CHOICES = (24, 25, 30, 50, 60)


def _fmt_time(t):
    m, s = divmod(max(0.0, float(t)), 60)
    return f"{int(m)}:{s:04.1f}"


def _card(title):
    gb = QGroupBox(title)
    lay = QVBoxLayout(gb)
    lay.setContentsMargins(12, 16, 12, 10)
    lay.setSpacing(7)
    return gb, lay


class ExportPage(QWidget):
    progress = Signal(float)
    finished_job = Signal(object)

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self._loading = True
        self._running = False
        self._cancel = threading.Event()
        self._last_dir = None
        self._built = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        wrap = QHBoxLayout(body)
        wrap.setContentsMargins(24, 18, 24, 18)
        wrap.addStretch(1)
        content = QWidget()
        content.setMaximumWidth(1100)
        content.setMinimumWidth(640)
        wrap.addWidget(content, 100)
        wrap.addStretch(1)
        cols = QHBoxLayout(content)
        cols.setSpacing(18)
        left = QVBoxLayout()
        right = QVBoxLayout()
        cols.addLayout(left, 3)
        cols.addLayout(right, 2)

        # ------------------------------------------------------------- modello
        gb, lay = _card("Modello")
        self.preset = QComboBox()
        last_group = None
        for p in export.PRESETS:
            if p["group"] != last_group:
                last_group = p["group"]
                self.preset.addItem(f"— {last_group} —", None)
                m = self.preset.model().item(self.preset.count() - 1)
                m.setEnabled(False)
            self.preset.addItem(p["label"], p["key"])
        self.preset.addItem("Personalizzato", "custom")
        self.preset_note = QLabel("")
        self.preset_note.setWordWrap(True)
        self.preset_note.setStyleSheet("color:#9a9a9a;")
        lay.addWidget(self.preset)
        lay.addWidget(self.preset_note)
        left.addWidget(gb)

        # ---------------------------------------------------- cosa esportare
        gb, lay = _card("Cosa esportare")
        self.mode_group = QButtonGroup(self)
        self.radios = {}
        labels = {"all": "Tutto il progetto",
                  "range": "Il tratto tra I e O",
                  "selection": "I pezzi selezionati in timeline",
                  "custom": "Un tratto a scelta",
                  "chapters": "Capitoli (un file per ogni capitolo)"}
        for k in MODES:
            r = QRadioButton(labels[k])
            self.radios[k] = r
            self.mode_group.addButton(r)
            lay.addWidget(r)
            if k == "custom":
                self.custom_box = QWidget()
                g = QGridLayout(self.custom_box)
                g.setContentsMargins(22, 0, 0, 0)
                self.t_from = self._time_spin()
                self.t_to = self._time_spin()
                b1 = QPushButton("= testina")
                b2 = QPushButton("= testina")
                b1.setToolTip("Usa la posizione della testina come inizio")
                b2.setToolTip("Usa la posizione della testina come fine")
                b1.clicked.connect(lambda: self.t_from.setValue(self.host.playhead_time()))
                b2.clicked.connect(lambda: self.t_to.setValue(self.host.playhead_time()))
                g.addWidget(QLabel("Da"), 0, 0)
                g.addWidget(self.t_from, 0, 1)
                g.addWidget(b1, 0, 2)
                g.addWidget(QLabel("A"), 1, 0)
                g.addWidget(self.t_to, 1, 1)
                g.addWidget(b2, 1, 2)
                g.setColumnStretch(1, 1)
                lay.addWidget(self.custom_box)
            if k == "chapters":
                self.chap_box = QWidget()
                cl = QVBoxLayout(self.chap_box)
                cl.setContentsMargins(22, 0, 0, 0)
                self.chap_list = QListWidget()
                self.chap_list.setMaximumHeight(150)
                cl.addWidget(self.chap_list)
                self.chap_hint = QLabel("")
                self.chap_hint.setWordWrap(True)
                self.chap_hint.setStyleSheet("color:#808080; font-size:11px;")
                cl.addWidget(self.chap_hint)
                lay.addWidget(self.chap_box)
        left.addWidget(gb)

        # ---------------------------------------------------- formato e qualità
        gb, lay = _card("Formato e qualità")
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(7)
        self.fmt = QComboBox()
        for k, v in export.FORMATS.items():
            self.fmt.addItem(v["label"], k)
        self.w = QSpinBox()
        self.h = QSpinBox()
        for sp in (self.w, self.h):
            sp.setRange(64, 7680)
            sp.setSingleStep(2)
            sp.setKeyboardTracking(False)
        size_row = QHBoxLayout()
        size_row.addWidget(self.w)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.h)
        size_row.addStretch(1)
        self.fit = QComboBox()
        self.fit.addItem("Con bande nere (tutta l'immagine)", False)
        self.fit.addItem("Riempi il fotogramma (taglia i lati)", True)
        self.fps = QComboBox()
        self.quality = QComboBox()
        for lab, k in QUALITIES:
            self.quality.addItem(lab, k)
        rows = (("Formato", self.fmt), ("Dimensione", size_row), ("Se la proporzione cambia", self.fit),
                ("Fotogrammi al secondo", self.fps), ("Qualità", self.quality))
        for n, (lab, w) in enumerate(rows):
            g.addWidget(QLabel(lab), n, 0)
            if isinstance(w, QHBoxLayout):
                g.addLayout(w, n, 1)
            else:
                g.addWidget(w, n, 1)
        g.setColumnStretch(1, 1)
        lay.addLayout(g)
        left.addWidget(gb)

        # ----------------------------------------------------------------- file
        gb, lay = _card("File")
        row = QHBoxLayout()
        self.folder = QLineEdit()
        pick = QPushButton("Cartella…")
        row.addWidget(self.folder, 1)
        row.addWidget(pick)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Nome"))
        self.name = QLineEdit()
        row.addWidget(self.name, 1)
        lay.addLayout(row)
        self.add_suffix = QCheckBox("Aggiungi al nome il modello scelto")
        self.add_suffix.setChecked(True)
        lay.addWidget(self.add_suffix)
        left.addWidget(gb)
        left.addStretch(1)

        # ------------------------------------------------------------ riepilogo
        gb, lay = _card("Riepilogo")
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        lay.addWidget(self.summary)
        self.files = QLabel("")
        self.files.setWordWrap(True)
        self.files.setStyleSheet("color:#9a9a9a;")
        self.files.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.files)
        right.addWidget(gb)
        self.go = QPushButton("Esporta")
        self.go.setMinimumHeight(38)
        self.go.setStyleSheet("QPushButton { background:#e8a33d; color:#1b1b1b; font-weight:700; border:none; "
                              "border-radius:5px; } QPushButton:hover { background:#f0b155; } "
                              "QPushButton:disabled { background:#4a4538; color:#888; }")
        right.addWidget(self.go)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        right.addWidget(self.bar)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#bdbdbd;")
        right.addWidget(self.status)
        self.reveal_btn = QPushButton("Mostra nel Finder" if sys.platform == "darwin" else "Mostra la cartella")
        self.reveal_btn.setVisible(False)
        right.addWidget(self.reveal_btn)
        note = QLabel("L'esportazione usa sempre i file originali (mai i proxy) e applica colore, velocità, "
                      "scritte e audio come nel montaggio. Puoi continuare a lavorare: l'esportazione parte da "
                      "una copia del progetto.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#808080; font-size:11px;")
        right.addWidget(note)
        right.addStretch(1)

        # ---------------------------------------------------------- collegamenti
        self.preset.currentIndexChanged.connect(self._preset_changed)
        for r in self.radios.values():
            r.toggled.connect(lambda _on: self._refresh_summary())
        self.chap_list.itemChanged.connect(lambda _i: self._refresh_summary())
        for sp in (self.t_from, self.t_to, self.w, self.h):
            sp.valueChanged.connect(lambda _v, s=sp: self._edited(s))
        for cb in (self.fmt, self.fit, self.fps, self.quality):
            cb.currentIndexChanged.connect(lambda _i, c=cb: self._edited(c))
        self.name.textChanged.connect(lambda _t: self._refresh_summary())
        self.folder.textChanged.connect(lambda _t: self._refresh_summary())
        self.add_suffix.toggled.connect(lambda _t: self._refresh_summary())
        pick.clicked.connect(self._pick_folder)
        self.go.clicked.connect(self._start)
        self.reveal_btn.clicked.connect(lambda: _reveal(self._last_out) if getattr(self, "_last_out", None) else None)
        self.progress.connect(lambda f: self.bar.setValue(int(f * 1000)))
        self.finished_job.connect(self._done)
        self._loading = False

    # ------------------------------------------------------------ strumenti
    def _time_spin(self):
        sp = QDoubleSpinBox()
        sp.setDecimals(2)
        sp.setSuffix(" s")
        sp.setRange(0.0, 36000.0)
        sp.setKeyboardTracking(False)
        return sp

    @property
    def project(self):
        return self.host.project

    # ------------------------------------------------------------- apertura
    def refresh(self):
        """Chiamata ogni volta che si apre la scheda: legge lo stato del progetto."""
        pr = self.project
        self._loading = True
        end = pr.timeline_end()
        # tempi del tratto a scelta
        for sp in (self.t_from, self.t_to):
            sp.setMaximum(max(end, 0.04))
        if self.t_to.value() <= self.t_from.value() or self.t_to.value() > end + 1e-6:
            self.t_from.setValue(0.0)
            self.t_to.setValue(end)
        # modi disponibili
        a, b = pr.tl_range
        have_range = a is not None and b is not None and b > a
        sel = self._selection_span()
        chap = export.chapters(pr) if any(pr.is_chapter(m) for m in pr.markers) else []
        self.radios["range"].setEnabled(have_range)
        self.radios["range"].setText("Il tratto tra I e O" + (f"  ({_fmt_time(b - a)})" if have_range else
                                                              "  (segna I e O in timeline)"))
        self.radios["selection"].setEnabled(sel is not None)
        self.radios["selection"].setText("I pezzi selezionati in timeline" + (
            f"  ({_fmt_time(sel[1] - sel[0])})" if sel else "  (selezionane in timeline)"))
        self.radios["chapters"].setEnabled(bool(chap))
        # capitoli: mantiene le spunte per nome/tempo
        old = {self.chap_list.item(i).data(Qt.UserRole): self.chap_list.item(i).checkState()
               for i in range(self.chap_list.count())}
        self.chap_list.blockSignals(True)
        self.chap_list.clear()
        self.chapters_cache = chap
        for name, t0, t1 in chap:
            it = QListWidgetItem(f"{name}   ·   {_fmt_time(t0)} → {_fmt_time(t1)}   ({_fmt_time(t1 - t0)})")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            key = (name, round(t0, 2), round(t1, 2))
            it.setData(Qt.UserRole, key)
            it.setCheckState(old.get(key, Qt.Checked))
            self.chap_list.addItem(it)
        self.chap_list.blockSignals(False)
        self.chap_hint.setText("Ogni capitolo va dal segnaposto al successivo. Aggiungili in Montaggio con M "
                               "(o Alt+M per dare nome e colore)." if chap else
                               "Nessun segnaposto in questo progetto.")
        if not self._built:
            self._built = True
            cfg = self.host.cfg
            key = cfg.get("export_preset", "yt1080")
            i = self.preset.findData(key)
            self.preset.setCurrentIndex(i if i >= 0 else 1)
            self.folder.setText(self._default_folder())
            self.radios["all"].setChecked(True)
        base = os.path.splitext(os.path.basename(pr.path or "montaggio"))[0]
        if not getattr(self, "_name_for", None) or self._name_for != base:
            self._name_for = base
            self.name.setText(base)
        cur = self.preset.currentData()
        if cur not in (None, "custom"):
            self._apply_preset(cur, keep_name=True)
        else:
            self._fill_fps()
        # se il modo scelto non c'è più si torna a «tutto»
        for k in ("range", "selection", "chapters"):
            if self.radios[k].isChecked() and not self.radios[k].isEnabled():
                self.radios["all"].setChecked(True)
        self._loading = False
        self._refresh_summary()

    def _default_folder(self):
        cfg = self.host.cfg
        start = cfg.get("last_export_dir")
        if not start or not os.path.isdir(start) or os.path.abspath(start) == os.path.expanduser("~"):
            start = os.path.dirname(os.path.abspath(self.project.path)) if self.project.path else ""
        if not start or not os.path.isdir(start):
            movies = os.path.join(os.path.expanduser("~"), "Movies" if sys.platform == "darwin" else "Videos")
            start = movies if os.path.isdir(movies) else os.path.expanduser("~")
        return start

    def _selection_span(self):
        its = self.host.selected_timeline_items()
        if not its:
            return None
        a = min(i["start"] for i in its)
        b = max(self.project.item_end(i) for i in its)
        return (a, b) if b > a else None

    # --------------------------------------------------------------- modelli
    def _fill_fps(self):
        cur = self.fps.currentData() if self.fps.count() else None
        self.fps.blockSignals(True)
        self.fps.clear()
        self.fps.addItem(f"Come il progetto ({self.project.fps:g})", None)
        for f in FPS_CHOICES:
            self.fps.addItem(f"{f}", float(f))
        i = self.fps.findData(cur)
        self.fps.setCurrentIndex(max(0, i))
        self.fps.blockSignals(False)

    def _apply_preset(self, key, keep_name=False):
        p = export.PRESET_BY_KEY.get(key)
        if p is None:
            return
        was = self._loading
        self._loading = True
        w, h = export.preset_size(p, self.project.size)
        self.w.setValue(w)
        self.h.setValue(h)
        i = self.fmt.findData(p["fmt"])
        self.fmt.setCurrentIndex(max(0, i))
        i = self.quality.findData(p["quality"])
        self.quality.setCurrentIndex(max(0, i))
        self._fill_fps()
        i = self.fps.findData(p.get("fps"))
        self.fps.setCurrentIndex(max(0, i))
        i = self.fit.findData(bool(p["fill"]))
        self.fit.setCurrentIndex(max(0, i))
        self.preset_note.setText(p.get("note", ""))
        self.quality.setEnabled(p["fmt"] == "mp4_h264")
        self._loading = was

    def _preset_changed(self):
        if self._loading:
            return
        key = self.preset.currentData()
        if key is None:
            return
        if key == "custom":
            self.preset_note.setText("Impostazioni scelte a mano.")
        else:
            self._apply_preset(key)
            self.host.cfg["export_preset"] = key
        self._refresh_summary()

    def _edited(self, _widget):
        """Una modifica a mano passa il modello a «Personalizzato»."""
        if self._loading:
            return
        if self.preset.currentData() != "custom" and _widget in (self.w, self.h, self.fmt, self.fit, self.fps,
                                                                   self.quality):
            i = self.preset.findData("custom")
            self._loading = True
            self.preset.setCurrentIndex(i)
            self._loading = False
            self.preset_note.setText("Impostazioni scelte a mano.")
        self.quality.setEnabled(self.fmt.currentData() == "mp4_h264")
        self._refresh_summary()

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Cartella di esportazione", self.folder.text())
        if d:
            self.folder.setText(d)

    # ------------------------------------------------------------- lavori
    def current_mode(self):
        for k, r in self.radios.items():
            if r.isChecked():
                return k
        return "all"

    def jobs(self):
        """[(nome del tratto, inizio, fine)] secondo la scelta fatta."""
        pr = self.project
        end = pr.timeline_end()
        mode = self.current_mode()
        if mode == "range":
            a, b = pr.tl_range
            if a is not None and b is not None and b > a:
                return [("", a, min(b, end))]
        elif mode == "selection":
            sel = self._selection_span()
            if sel:
                return [("", sel[0], sel[1])]
        elif mode == "custom":
            a, b = self.t_from.value(), min(self.t_to.value(), end)
            return [("", a, b)] if b - a > 0.03 else []
        elif mode == "chapters":
            out = []
            for i in range(self.chap_list.count()):
                it = self.chap_list.item(i)
                if it.checkState() == Qt.Checked:
                    name, t0, t1 = self.chapters_cache[i]
                    out.append((name, t0, t1))
            return out
        return [("", 0.0, end)] if end > 0 else []

    def _targets(self):
        """[(percorso, inizio, fine)] dei file da creare."""
        jobs = self.jobs()
        fmt = self.fmt.currentData() or "mp4_h264"
        ext = export.FORMATS[fmt]["ext"]
        base = export.safe_name(self.name.text().strip() or "montaggio")
        key = self.preset.currentData()
        if self.add_suffix.isChecked() and key not in (None, "custom", "progetto"):
            if export.PRESET_BY_KEY[key].get("suffix"):
                base += "_" + export.PRESET_BY_KEY[key]["suffix"]
        folder = self.folder.text().strip()
        out = []
        for n, (name, a, b) in enumerate(jobs, 1):
            stem = base if not name else f"{base}_{n:02d}_{export.safe_name(name)}"
            out.append((os.path.join(folder, stem + ext), a, b))
        return out

    def _refresh_summary(self):
        if self._loading:
            return
        tg = self._targets()
        total = sum(b - a for _p, a, b in tg)
        w, h = self.w.value(), self.h.value()
        fps = self.fps.currentData() or self.project.fps
        fmt = self.fmt.currentText()
        if not tg:
            self.summary.setText("<span style='color:#c9a06a'>Niente da esportare con questa scelta.</span>")
            self.files.setText("")
            self.go.setEnabled(False)
            return
        mode = {"all": "Tutto il progetto", "range": "Tratto tra I e O", "selection": "Pezzi selezionati",
                "custom": "Tratto a scelta", "chapters": f"{len(tg)} capitoli"}[self.current_mode()]
        pw, ph = self.project.size
        ratio = ""
        if abs(w / max(h, 1) - pw / max(ph, 1)) > 0.01:
            ratio = (" · <span style='color:#e8a33d'>proporzione diversa dal progetto: " +
                     ("l'immagine riempie il fotogramma" if self.fit.currentData() else "bande nere") + "</span>")
        self.summary.setText(f"<b>{mode}</b><br>Durata {_fmt_time(total)}"
                             f"{' (in totale)' if len(tg) > 1 else ''}<br>{w}×{h} · {fps:g} fps<br>{fmt}{ratio}")
        names = [os.path.basename(p) for p, _a, _b in tg]
        shown = "\n".join(names[:8]) + (f"\n… e altri {len(names) - 8}" if len(names) > 8 else "")
        self.files.setText(("File:\n" if len(names) > 1 else "File: ") + shown)
        self.go.setEnabled(not self._running and bool(self.folder.text().strip()))
        self.go.setText("Esporta" if len(tg) == 1 else f"Esporta {len(tg)} file")

    # -------------------------------------------------------------- esporta
    def _start(self):
        if self._running:
            return
        tg = self._targets()
        if not tg:
            return
        folder = self.folder.text().strip()
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                QMessageBox.warning(self, "Cartella", f"Non riesco a creare la cartella:\n{e}")
                return
        pr = self.project
        for p, _a, _b in tg:
            if any(os.path.abspath(p) == os.path.abspath(s["path"]) for s in pr.sources):
                QMessageBox.warning(self, "Attenzione", "Non puoi sovrascrivere un file originale.")
                return
        exist = [p for p, _a, _b in tg if os.path.exists(p)]
        if exist and QMessageBox.question(
                self, "Sovrascrivere?",
                (f"Il file esiste già:\n{exist[0]}" if len(exist) == 1 else f"{len(exist)} file esistono già "
                                                                           f"(il primo: {exist[0]}).") +
                "\nSovrascrivere?") != QMessageBox.Yes:
            return
        self.host.engine.stop()
        snap = copy.deepcopy(pr)           # si può continuare a montare: l'esportazione usa questa copia
        opts = dict(fmt=self.fmt.currentData(), quality=self.quality.currentData(), size=(self.w.value(), self.h.value()),
                    fps=self.fps.currentData(), fill=bool(self.fit.currentData()))
        store = self.host.store
        renderer = render_png
        self._running = True
        self._cancel.clear()
        self.go.setText("Annulla")
        self.go.setEnabled(True)
        self.go.clicked.disconnect()
        self.go.clicked.connect(self._cancel_run)
        self.reveal_btn.setVisible(False)
        self.bar.setValue(0)
        self.status.setText("Preparo l'esportazione…")
        self.host.cfg["last_export_dir"] = folder
        n = len(tg)

        def work():
            errs, done = None, []
            for k, (path, a, b) in enumerate(tg):
                if self._cancel.is_set():
                    errs = "Esportazione annullata."
                    break
                try:
                    cmd, wd, dur = export.build_job(snap, store, path, text_renderer=renderer, start=a, end=b, **opts)
                    errs = export.run_job(cmd, wd, dur, progress=lambda f, k=k: self.progress.emit((k + f) / n),
                                          cancel=self._cancel)
                except Exception as e:  # noqa: BLE001
                    errs = str(e)
                if errs:
                    break
                done.append(path)
            self.finished_job.emit((errs, done, n))

        self.status.setText(f"Esporto {n} file…" if n > 1 else "Esporto…")
        threading.Thread(target=work, daemon=True).start()

    def _cancel_run(self):
        self._cancel.set()
        self.status.setText("Annullo…")

    def _done(self, res):
        err, done, n = res
        self._running = False
        self.go.clicked.disconnect()
        self.go.clicked.connect(self._start)
        self._refresh_summary()
        if err:
            self.status.setText("Non riuscita." if "annullat" not in err.lower() else "Esportazione annullata.")
            if "annullat" not in err.lower():
                QMessageBox.warning(self, "Esportazione non riuscita", err[-1500:])
        else:
            self.bar.setValue(1000)
            self.status.setText("Fatto: " + (os.path.basename(done[0]) if len(done) == 1 else f"{len(done)} file creati")
                                + ".")
        if done:
            self._last_out = done[0]
            self.reveal_btn.setVisible(True)

    def is_running(self):
        return self._running

    def cancel(self):
        if self._running:
            self._cancel.set()


def _reveal(path):
    """Apre la cartella del file selezionandolo, dove il sistema lo permette."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception:
        pass
