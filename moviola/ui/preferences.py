"""Preferenze di Moviola: dove tenere la cache (anche su un disco esterno),
quanto può crescere, e la cartella dei progetti."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout)

from .. import paths

CACHE_FOLDER_NAME = "Moviola Cache"
DEFAULT_LIMIT_GB = 10.0


def _short(path, n=48):
    if not path:
        return ""
    return path if len(path) <= n else "…" + path[-(n - 1):]


class PreferencesDialog(QDialog):
    def __init__(self, cfg, keep_files=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferenze")
        self.setMinimumWidth(560)
        self.cfg = cfg
        self.keep = list(keep_files)
        self.cache_dir = cfg.get("cache_dir") or ""
        self.projects_dir = cfg.get("projects_dir") or ""
        lay = QVBoxLayout(self)

        # ---------------------------------------------------------- progetti
        gb = QGroupBox("Progetti")
        f = QFormLayout(gb)
        row = QHBoxLayout()
        self.proj_lab = QLabel()
        b = QPushButton("Scegli…")
        b.clicked.connect(self._pick_projects)
        row.addWidget(self.proj_lab, 1)
        row.addWidget(b)
        f.addRow("Cartella dei progetti", row)
        note = QLabel("La schermata iniziale mostra tutti i progetti di questa cartella. Se progetti e video "
                      "stanno sullo stesso disco esterno, il progetto si apre uguale su Mac e su PC.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8a8a8a;")
        f.addRow(note)
        lay.addWidget(gb)

        # ---------------------------------------------------------- cache
        gb = QGroupBox("Cache (proxy, audio decodificato, miniature)")
        f = QFormLayout(gb)
        row = QHBoxLayout()
        self.cache_lab = QLabel()
        b1 = QPushButton("Scegli cartella…")
        b1.clicked.connect(self._pick_cache)
        b2 = QPushButton("Sul computer")
        b2.setToolTip("Torna a tenere la cache nella cartella di sistema del computer")
        b2.clicked.connect(self._cache_internal)
        row.addWidget(self.cache_lab, 1)
        row.addWidget(b1)
        row.addWidget(b2)
        f.addRow("Posizione", row)
        self.limit = QDoubleSpinBox()
        self.limit.setRange(0.5, 2000)
        self.limit.setDecimals(1)
        self.limit.setSingleStep(1)
        self.limit.setSuffix(" GB")
        self.limit.setValue(float(cfg.get("cache_limit_gb", DEFAULT_LIMIT_GB)))
        self.limit.setToolTip("Superato il limite, Moviola elimina da sola i file usati meno di recente\n"
                              "(mai quelli del progetto aperto). Si ricreano quando servono.")
        f.addRow("Limite", self.limit)
        row = QHBoxLayout()
        self.usage = QLabel()
        b3 = QPushButton("Svuota cache")
        b3.clicked.connect(self._clear)
        row.addWidget(self.usage, 1)
        row.addWidget(b3)
        f.addRow("Occupata", row)
        self.clear_old = QCheckBox("Spostando la cache, elimina quella rimasta sul computer")
        self.clear_old.setChecked(True)
        f.addRow(self.clear_old)
        note = QLabel("Svuotare la cache non tocca i progetti né i video: si ricrea da sola alla riapertura. "
                      "Se il disco esterno non è collegato, Moviola usa per il momento una cache temporanea "
                      "sul computer.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8a8a8a;")
        f.addRow(note)
        lay.addWidget(gb)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._refresh()

    def _refresh(self):
        self.proj_lab.setText(_short(self.projects_dir) or "(non scelta)")
        self.proj_lab.setToolTip(self.projects_dir)
        if self.cache_dir:
            ok = os.path.isdir(self.cache_dir)
            self.cache_lab.setText(_short(self.cache_dir) + ("" if ok else "  — non raggiungibile"))
        else:
            self.cache_lab.setText("Sul computer")
        self.cache_lab.setToolTip(self.cache_dir or paths.internal_root())
        root = self.cache_dir if self.cache_dir and os.path.isdir(self.cache_dir) else \
            (paths.internal_root() if not self.cache_dir else None)
        self.usage.setText(paths.human_size(paths.cache_usage(root)) if root else "—")

    def _pick_projects(self):
        d = QFileDialog.getExistingDirectory(self, "Cartella dei progetti", self.projects_dir or os.path.expanduser("~"))
        if d:
            self.projects_dir = d
            self._refresh()

    def _pick_cache(self):
        d = QFileDialog.getExistingDirectory(self, "Dove tenere la cache (es. sul disco esterno)",
                                             os.path.dirname(self.cache_dir) if self.cache_dir else
                                             (self.projects_dir or os.path.expanduser("~")))
        if not d:
            return
        if os.path.basename(d.rstrip("/\\")) != CACHE_FOLDER_NAME:
            d = os.path.join(d, CACHE_FOLDER_NAME)
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Cache", f"Non riesco a creare la cartella:\n{e}")
            return
        self.cache_dir = d
        self._refresh()

    def _cache_internal(self):
        self.cache_dir = ""
        self._refresh()

    def _clear(self):
        root = self.cache_dir if self.cache_dir else paths.internal_root()
        if not os.path.isdir(root):
            return
        freed = paths.clear_cache(self.keep, root)
        QMessageBox.information(self, "Cache", f"Liberati {paths.human_size(freed)}.\n"
                                               "(I file del progetto aperto restano.)")
        self._refresh()

    def values(self):
        return {"cache_dir": self.cache_dir, "cache_limit_gb": self.limit.value(),
                "projects_dir": self.projects_dir, "clear_old": self.clear_old.isChecked()}
