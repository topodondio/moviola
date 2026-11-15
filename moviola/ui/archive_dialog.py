"""Finestra «Archivia…»: sceglie cosa mettere nella cartella d'archivio e la crea."""
from __future__ import annotations

import os
import shutil
import threading

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QRadioButton, QVBoxLayout)

from .. import archive, paths


class _Worker(QThread):
    prog = Signal(float)
    done = Signal(str, str)      # risultato, errore

    def __init__(self, args, cancel):
        super().__init__()
        self.args, self.cancel = args, cancel

    def run(self):
        try:
            p, dest, mode, extra, zp = self.args
            out = archive.run(p, dest, mode, extra, zp, progress=self.prog.emit, cancel=self.cancel)
            self.done.emit(out, "")
        except Exception as e:
            self.done.emit("", str(e))


class ArchiveDialog(QDialog):
    def __init__(self, project_path, is_open=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Archivia progetto")
        self.setMinimumWidth(520)
        self.path = project_path
        self.result_path = ""
        self.extra = []
        self.dest = os.path.dirname(os.path.abspath(project_path))
        self.worker = None
        self.cancel = threading.Event()
        lay = QVBoxLayout(self)
        name = os.path.splitext(os.path.basename(project_path))[0]
        t = QLabel(f"<b>{name}</b><br>Crea una cartella con tutto quello che serve per riaprire il "
                   "progetto altrove. I file originali non vengono toccati né cancellati.")
        t.setWordWrap(True)
        lay.addWidget(t)
        if is_open:
            n = QLabel("Il progetto è aperto: viene archiviata l'ultima versione salvata (Ctrl+S).")
            n.setStyleSheet("color:#f0c080;")
            lay.addWidget(n)
        self.radios = {}
        texts = {"project": "Solo progetto\n(piccolo: il file del progetto, le copie di sicurezza e l'elenco dei file)",
                 "used": "Progetto + clip usate in timeline\n(consigliato: i video che servono al montaggio)",
                 "all": "Progetto + tutte le clip\n(anche quelle non usate)"}
        for k in archive.MODES:
            r = QRadioButton(texts[k])
            r.toggled.connect(self._refresh)
            lay.addWidget(r)
            self.radios[k] = r
        self.radios["used"].setChecked(True)
        row = QHBoxLayout()
        self.b_exp = QPushButton("Aggiungi video esportati…")
        self.b_exp.clicked.connect(self._pick_exports)
        self.l_exp = QLabel("")
        row.addWidget(self.b_exp)
        row.addWidget(self.l_exp, 1)
        lay.addLayout(row)
        self.zip = QCheckBox("Comprimi in un file .zip (solo per «Solo progetto»)")
        lay.addWidget(self.zip)
        row = QHBoxLayout()
        self.l_dest = QLabel()
        b = QPushButton("Destinazione…")
        b.clicked.connect(self._pick_dest)
        row.addWidget(QLabel("Dove:"))
        row.addWidget(self.l_dest, 1)
        row.addWidget(b)
        lay.addLayout(row)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        row.addStretch(1)
        self.b_cancel = QPushButton("Annulla")
        self.b_go = QPushButton("Archivia")
        self.b_go.setDefault(True)
        row.addWidget(self.b_cancel)
        row.addWidget(self.b_go)
        lay.addLayout(row)
        self.b_cancel.clicked.connect(self._cancel)
        self.b_go.clicked.connect(self._go)
        self._refresh()

    def mode(self):
        return next(k for k, r in self.radios.items() if r.isChecked())

    def _pick_dest(self):
        d = QFileDialog.getExistingDirectory(self, "Dove creare l'archivio", self.dest)
        if d:
            self.dest = d
            self._refresh()

    def _pick_exports(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Video esportati da includere", self.dest,
                                                "Video (*.mp4 *.mov *.m4v *.mkv *.avi *.webm);;Tutti (*)")
        if files:
            self.extra = files
            self._refresh()

    def _refresh(self, *_):
        if not hasattr(self, "b_go"):      # la finestra è ancora in costruzione
            return
        mode = self.mode()
        self.zip.setEnabled(mode == "project")
        if mode != "project":
            self.zip.setChecked(False)
        self.l_dest.setText(self.dest)
        self.l_exp.setText(f"{len(self.extra)} file" if self.extra else "")
        try:
            pl = archive.plan(self.path, mode)
        except Exception as e:
            self.info.setText(f"Progetto non leggibile: {e}")
            self.b_go.setEnabled(False)
            return
        need = pl["bytes"] + sum(os.path.getsize(p) for p in self.extra if os.path.isfile(p))
        try:
            free = shutil.disk_usage(self.dest).free
        except OSError:
            free = 0
        txt = f"Spazio necessario: {paths.human_size(need)} · libero sul disco: {paths.human_size(free)}"
        ok = need * 1.02 + 50 * 1024 * 1024 <= free
        if not ok:
            txt += "\n⚠ Spazio insufficiente: scegli un'altra destinazione."
        if pl["missing"]:
            txt += (f"\n⚠ {len(pl['missing'])} file originali non si trovano (disco scollegato?) e non "
                    "saranno copiati: " + ", ".join(e["name"] for e in pl["missing"][:4]))
        self.info.setText(txt)
        self.b_go.setEnabled(ok)

    def _go(self):
        self.cancel.clear()
        for w in (self.b_go, self.b_exp, self.zip, *self.radios.values()):
            w.setEnabled(False)
        self.bar.show()
        self.worker = _Worker((self.path, self.dest, self.mode(), list(self.extra), self.zip.isChecked()),
                              self.cancel)
        self.worker.prog.connect(lambda f: self.bar.setValue(int(f * 1000)))
        self.worker.done.connect(self._finished)
        self.worker.start()

    def _finished(self, out, err):
        self.worker.wait()
        self.worker = None
        if err:
            self.info.setText("⚠ " + err)
            self.bar.hide()
            for w in (self.b_go, self.b_exp, *self.radios.values()):
                w.setEnabled(True)
            self._refresh()
            return
        self.result_path = out
        self.accept()

    def _cancel(self):
        if self.worker is not None:
            self.cancel.set()
            self.info.setText("Annullo…")
        else:
            self.reject()

    def reject(self):
        if self.worker is not None:
            self.cancel.set()
            self.worker.wait()
        super().reject()
