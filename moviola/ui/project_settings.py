"""Impostazioni del progetto: risoluzione, fotogrammi al secondo, bande nere
generali. Valgono per l'anteprima della timeline e per l'export."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QSpinBox, QVBoxLayout)

RESOLUTIONS = [("1920×1080 (Full HD)", 1920, 1080), ("3840×2160 (4K UHD)", 3840, 2160),
               ("2560×1440 (QHD)", 2560, 1440), ("1280×720 (HD)", 1280, 720),
               ("1080×1920 (verticale)", 1080, 1920), ("1080×1350 (4:5)", 1080, 1350),
               ("1080×1080 (quadrato)", 1080, 1080)]
FPS = [23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60]
BARS = [("Nessuna", 0.0), ("2.39:1 (cinema)", 2.39), ("2:1", 2.0), ("1.85:1", 1.85)]


def bars_for_ratio(ratio, W, H):
    """Altezza di ciascuna banda (frazione del fotogramma) per ottenere il formato ratio."""
    if not ratio:
        return 0.0
    return max(0.0, (1.0 - (W / H) / ratio) / 2.0)


class ProjectSettingsDialog(QDialog):
    def __init__(self, settings, source_fps=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Impostazioni progetto")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.res = QComboBox()
        for lab, w, h in RESOLUTIONS:
            self.res.addItem(lab, (w, h))
        self.res.addItem("Personalizzata", None)
        self.w = QSpinBox()
        self.w.setRange(16, 8192)
        self.w.setSingleStep(2)
        self.h = QSpinBox()
        self.h.setRange(16, 8192)
        self.h.setSingleStep(2)
        row = QHBoxLayout()
        row.addWidget(self.w)
        row.addWidget(QLabel("×"))
        row.addWidget(self.h)
        form.addRow("Risoluzione", self.res)
        form.addRow("", row)
        self.fps = QComboBox()
        self.fps.setEditable(True)
        for f in FPS:
            self.fps.addItem(f"{f:g}", f)
        form.addRow("Fotogrammi al secondo", self.fps)
        self.bars = QComboBox()
        for lab, r in BARS:
            self.bars.addItem(lab, r)
        self.bars.addItem("Personalizzata", -1)
        self.bars_pct = QDoubleSpinBox()
        self.bars_pct.setRange(0, 40)
        self.bars_pct.setSuffix(" % per banda")
        self.bars_pct.setSingleStep(0.5)
        form.addRow("Bande nere (tutto)", self.bars)
        form.addRow("", self.bars_pct)
        lay.addLayout(form)
        note = QLabel("Gli fps del progetto valgono per l'anteprima della timeline e per il video "
                      "esportato. Le clip con fps diversi vengono adattate (fotogrammi ripetuti o saltati)."
                      + (f"\nLe clip importate sono a {source_fps}." if source_fps else ""))
        note.setWordWrap(True)
        note.setStyleSheet("color:#9a9a9a;")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        W, H = int(settings["width"]), int(settings["height"])
        self.w.setValue(W)
        self.h.setValue(H)
        i = next((k for k, (_, w, h) in enumerate(RESOLUTIONS) if (w, h) == (W, H)), len(RESOLUTIONS))
        self.res.setCurrentIndex(i)
        fps = float(settings["fps"])
        j = next((k for k, f in enumerate(FPS) if abs(f - fps) < 1e-3), -1)
        if j >= 0:
            self.fps.setCurrentIndex(j)
        else:
            self.fps.setEditText(f"{fps:g}")
        b = float(settings.get("bars", 0.0) or 0.0)
        self.bars_pct.setValue(b * 100)
        k = next((k for k, (_, r) in enumerate(BARS) if abs(bars_for_ratio(r, W, H) - b) < 1e-4), None)
        self.bars.setCurrentIndex(k if k is not None else len(BARS))
        self.res.currentIndexChanged.connect(self._res_changed)
        self.bars.currentIndexChanged.connect(self._bars_changed)
        self.w.valueChanged.connect(self._bars_changed)
        self.h.valueChanged.connect(self._bars_changed)
        self.bars_pct.valueChanged.connect(self._pct_edited)
        self._bars_changed()

    def _res_changed(self):
        d = self.res.currentData()
        if d:
            self.w.setValue(d[0])
            self.h.setValue(d[1])

    def _bars_changed(self):
        r = self.bars.currentData()
        if r is not None and r >= 0:
            self.bars_pct.blockSignals(True)
            self.bars_pct.setValue(bars_for_ratio(r, self.w.value(), self.h.value()) * 100)
            self.bars_pct.blockSignals(False)

    def _pct_edited(self):
        self.bars.blockSignals(True)
        self.bars.setCurrentIndex(len(BARS))
        self.bars.blockSignals(False)

    def values(self):
        try:
            fps = float(self.fps.currentText().replace(",", "."))
        except ValueError:
            fps = 25.0
        fps = max(1.0, min(240.0, fps))
        w = self.w.value() - self.w.value() % 2
        h = self.h.value() - self.h.value() % 2
        return {"width": w, "height": h, "fps": fps, "bars": self.bars_pct.value() / 100.0, "auto": False}
