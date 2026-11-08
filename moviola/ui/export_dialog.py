"""Finestra di esportazione."""
from __future__ import annotations

import os
import subprocess
import sys
import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QProgressBar, QPushButton, QVBoxLayout)

from .. import export


class ExportDialog(QDialog):
    progress = Signal(float)
    finished_job = Signal(object)

    def __init__(self, project, store, default_dir, parent=None, text_renderer=None):
        super().__init__(parent)
        self.text_renderer = text_renderer
        self.setWindowTitle("Esporta video")
        self.setMinimumWidth(520)
        self.project = project
        self.store = store
        self._cancel = threading.Event()
        self._running = False
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.fmt = QComboBox()
        for k, v in export.FORMATS.items():
            self.fmt.addItem(v["label"], k)
        self.quality = QComboBox()
        for lab, k in (("Alta", "alta"), ("Buona", "buona"), ("Leggera", "leggera")):
            self.quality.addItem(lab, k)
        w, h = project.size
        name = os.path.splitext(os.path.basename(project.path or "montaggio"))[0]
        self.path = QLineEdit(os.path.join(default_dir, name + ".mp4"))
        pick = QPushButton("Sfoglia…")
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(pick)
        self.res = QComboBox()
        self.res.addItem(f"Come il progetto · {w}×{h}", (w, h))
        for th in (2160, 1440, 1080, 720, 540):
            if th == h:
                continue
            tw = int(round(w * th / h))
            tw -= tw % 2
            self.res.addItem(f"{tw}×{th}", (tw, th))
        form.addRow("Formato", self.fmt)
        form.addRow("Risoluzione", self.res)
        form.addRow("Qualità", self.quality)
        form.addRow("File", row)
        dur = project.timeline_end()
        m, s = divmod(dur, 60)
        form.addRow("Durata", QLabel(f"{int(m)}:{s:04.1f} · {project.fps:g} fps"))
        lay.addLayout(form)
        note = QLabel("L'export usa sempre i file originali (mai i proxy), indipendentemente dalla "
                      "risoluzione scelta per l'anteprima, e applica il colore di clip intera, pezzo e timeline.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9a9a9a;")
        lay.addWidget(note)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        lay.addWidget(self.bar)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.cancel_btn = QPushButton("Chiudi")
        self.go = QPushButton("Esporta")
        self.go.setDefault(True)
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.go)
        lay.addLayout(btns)
        pick.clicked.connect(self._pick)
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        self.go.clicked.connect(self._start)
        self.cancel_btn.clicked.connect(self._cancel_or_close)
        self.progress.connect(lambda f: self.bar.setValue(int(f * 1000)))
        self.finished_job.connect(self._done)

    def _fmt_changed(self):
        ext = export.FORMATS[self.fmt.currentData()]["ext"]
        base = os.path.splitext(self.path.text())[0]
        self.path.setText(base + ext)
        self.quality.setEnabled(self.fmt.currentData() == "mp4_h264")

    def _pick(self):
        ext = export.FORMATS[self.fmt.currentData()]["ext"]
        p, _ = QFileDialog.getSaveFileName(self, "Esporta come", self.path.text(), f"Video (*{ext})")
        if p:
            if not p.lower().endswith(ext):
                p += ext
            self.path.setText(p)

    def _start(self):
        out = self.path.text().strip()
        if not out:
            return
        if os.path.exists(out) and QMessageBox.question(self, "Sovrascrivere?",
                                                        f"Il file esiste già:\n{out}\nSovrascriverlo?") \
                != QMessageBox.Yes:
            return
        if any(os.path.abspath(out) == os.path.abspath(s["path"]) for s in self.project.sources):
            QMessageBox.warning(self, "Attenzione", "Non puoi sovrascrivere un file originale.")
            return
        self._running = True
        self._cancel.clear()
        self.go.setEnabled(False)
        self.cancel_btn.setText("Annulla")
        fmt = self.fmt.currentData()
        q = self.quality.currentData()
        size = self.res.currentData()

        def work():
            try:
                cmd, wd, dur = export.build_job(self.project, self.store, out, fmt, q,
                                                text_renderer=self.text_renderer, size=size)
                err = export.run_job(cmd, wd, dur, progress=self.progress.emit, cancel=self._cancel)
            except Exception as e:
                err = str(e)
            self.finished_job.emit((err, out))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, res):
        err, out = res
        self._running = False
        self.go.setEnabled(True)
        self.cancel_btn.setText("Chiudi")
        if err:
            QMessageBox.warning(self, "Export non riuscito", err[-1500:])
        else:
            self.bar.setValue(1000)
            box = QMessageBox(self)
            box.setWindowTitle("Fatto")
            box.setText("Video esportato.")
            box.setInformativeText(out)
            show = box.addButton("Mostra nel Finder" if sys.platform == "darwin" else "Mostra cartella",
                                 QMessageBox.ActionRole)
            box.addButton(QMessageBox.Ok)
            box.exec()
            if box.clickedButton() is show:
                _reveal(out)
            self.accept()

    def _cancel_or_close(self):
        if self._running:
            self._cancel.set()
        else:
            self.reject()

    def closeEvent(self, e):
        if self._running:
            self._cancel.set()
        super().closeEvent(e)


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
