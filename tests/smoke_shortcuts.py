"""Scorciatoie personalizzabili (senza schermo)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication
app = QApplication([])
from moviola.ui.main_window import MainWindow
from moviola.ui.shortcuts import ShortcutsDialog
w = MainWindow({"gpu": False}); w.show()
reg = w.shortcuts
assert len(reg.entries) > 90, len(reg.entries)
cut = next(c for c, e in reg.entries.items() if e["label"] == "Taglia alla testina")
assert reg.keys(cut) == ["C"] and not any("B" in reg.keys(c) for c in reg.entries)
# nessun conflitto tra i predefiniti
seen = {}
for c in reg.entries:
    for k in reg.keys(c):
        assert k not in seen, (k, c, seen[k]); seen[k] = c
# modifica + persistenza nel cfg + azione aggiornata
reg.set_keys(cut, ["Ctrl+K"])
assert w.cfg["shortcuts"][cut] == ["Ctrl+K"] and reg.entries[cut]["action"].shortcut() == QKeySequence("Ctrl+K")
assert reg.owner_of("Ctrl+K") == cut and reg.owner_of("Ctrl+K", exclude=cut) is None
reg.reset(cut); assert cut not in w.cfg["shortcuts"] and reg.keys(cut) == ["C"]
# tasti speciali (frecce, lampeggio) e reset totale
assert reg.keys("frame_back") == ["Left"] and reg.keys("ref_flash") == ["Q"]
reg.set_keys("ref_flash", ["F"]); assert reg.main_keys("ref_flash") == {Qt.Key_F}
reg.reset_all(); assert not w.cfg["shortcuts"] and reg.keys("ref_flash") == ["Q"]
# nuova finestra: le scelte salvate si riapplicano
w.cfg["shortcuts"] = {cut: ["Ctrl+K"]}
w2 = MainWindow(w.cfg); assert w2.shortcuts.keys(cut) == ["Ctrl+K"]
d = ShortcutsDialog(reg, w); assert d.tree.topLevelItemCount() >= 8
print("ok")
