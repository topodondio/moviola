"""Scorciatoie da tastiera personalizzabili: elenco di tutti i comandi, assegnazione, conflitti, ripristino.
Le scelte stanno nelle impostazioni del programma (cfg["shortcuts"]) e valgono su Mac e Windows:
si salvano in forma portabile («Ctrl+S»), che Qt traduce in Cmd su Mac."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

PORTABLE = QKeySequence.PortableText
NATIVE = QKeySequence.NativeText


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _norm(seq: str) -> str:
    return QKeySequence(seq).toString(PORTABLE)


def combo_text(ev) -> str:
    """Il tasto premuto in forma portabile (per i comandi che non sono azioni di menu)."""
    mods = ev.modifiers() & ~Qt.KeypadModifier
    try:
        from PySide6.QtCore import QKeyCombination
        return QKeySequence(QKeyCombination(mods, Qt.Key(ev.key()))).toString(PORTABLE)
    except Exception:  # noqa: BLE001
        return QKeySequence(int(mods.value if hasattr(mods, "value") else mods) | ev.key()).toString(PORTABLE)


class Registry:
    def __init__(self, cfg, save=None):
        self.cfg = cfg
        self.save = save or (lambda: None)
        self.entries: dict[str, dict] = {}

    # ---------------------------------------------------------- registro
    def _custom(self) -> dict:
        d = self.cfg.get("shortcuts")
        if not isinstance(d, dict):
            d = self.cfg["shortcuts"] = {}
        return d

    @staticmethod
    def group_of(menu) -> str:
        names = []
        m = menu
        while m is not None and hasattr(m, "title"):
            names.append(m.title().replace("&", ""))
            m = m.parent()
        return " › ".join(reversed(names))

    def register(self, menu, label, default, action):
        group = self.group_of(menu) if not isinstance(menu, str) else menu
        base = _slug(group + "-" + label)
        cid, n = base, 2
        while cid in self.entries:
            cid, n = f"{base}-{n}", n + 1
        self.entries[cid] = {"id": cid, "group": group, "label": label,
                             "default": [_norm(s) for s in default], "action": action}
        if action is not None:
            self._apply(cid)
        return cid

    def register_special(self, group, label, default, cid):
        self.entries[cid] = {"id": cid, "group": group, "label": label,
                             "default": [_norm(s) for s in default], "action": None}

    def keys(self, cid) -> list[str]:
        e = self.entries[cid]
        c = self._custom().get(cid)
        return list(e["default"]) if c is None else [_norm(s) for s in c if s]

    def matches(self, cid, ev) -> bool:
        return combo_text(ev) in self.keys(cid)

    def main_keys(self, cid):
        return {QKeySequence(s)[0].key() for s in self.keys(cid)}

    def _apply(self, cid):
        e = self.entries[cid]
        a = e["action"]
        if a is not None:
            a.setShortcuts([QKeySequence(s) for s in self.keys(cid)])
            a.setShortcutContext(Qt.WindowShortcut)

    # ------------------------------------------------------- modifiche
    def set_keys(self, cid, keys):
        keys = [_norm(k) for k in keys if k]
        if keys == self.entries[cid]["default"]:
            self._custom().pop(cid, None)
        else:
            self._custom()[cid] = keys
        self._apply(cid)
        self.save()

    def reset(self, cid):
        self._custom().pop(cid, None)
        self._apply(cid)
        self.save()

    def reset_all(self):
        self._custom().clear()
        for cid in self.entries:
            self._apply(cid)
        self.save()

    def is_custom(self, cid):
        return cid in self._custom()

    def owner_of(self, key, exclude=None):
        key = _norm(key)
        for cid in self.entries:
            if cid != exclude and key in self.keys(cid):
                return cid
        return None

    def groups(self):
        out: dict[str, list] = {}
        for e in self.entries.values():
            out.setdefault(e["group"], []).append(e)
        return out


def _show(keys):
    return "   ·   ".join(QKeySequence(k).toString(NATIVE) for k in keys) if keys else "—"


class ShortcutsDialog(QDialog):
    def __init__(self, reg: Registry, parent=None):
        super().__init__(parent)
        self.reg = reg
        self.setWindowTitle("Scorciatoie da tastiera")
        self.setMinimumSize(720, 560)
        lay = QVBoxLayout(self)
        hint = QLabel("Scegli un comando, poi premi il tasto (o la combinazione) nel campo «Principale» e clicca "
                      "<b>Assegna</b>. Se il tasto è già usato da un altro comando te lo segnalo. Su Mac Ctrl "
                      "diventa Cmd.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9a9a9a;")
        lay.addWidget(hint)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Cerca un comando…")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Comando", "Scorciatoia"])
        self.tree.setColumnWidth(0, 360)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemSelectionChanged.connect(self._selected)
        lay.addWidget(self.tree, 1)
        self.name = QLabel("")
        self.name.setStyleSheet("font-weight:700;")
        lay.addWidget(self.name)
        row = QHBoxLayout()
        row.addWidget(QLabel("Principale"))
        self.e1 = QKeySequenceEdit()
        self.e1.setMaximumSequenceLength(1)
        row.addWidget(self.e1, 1)
        row.addWidget(QLabel("Alternativa"))
        self.e2 = QKeySequenceEdit()
        self.e2.setMaximumSequenceLength(1)
        row.addWidget(self.e2, 1)
        lay.addLayout(row)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet("color:#e8a33d;")
        lay.addWidget(self.msg)
        row = QHBoxLayout()
        self.b_set = QPushButton("Assegna")
        self.b_clear = QPushButton("Togli scorciatoia")
        self.b_reset = QPushButton("Ripristina questa")
        self.b_all = QPushButton("Ripristina tutte le predefinite")
        b_close = QPushButton("Chiudi")
        for b in (self.b_set, self.b_clear, self.b_reset):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.b_all)
        row.addWidget(b_close)
        lay.addLayout(row)
        self.b_set.clicked.connect(self._assign)
        self.b_clear.clicked.connect(self._clear)
        self.b_reset.clicked.connect(self._reset_one)
        self.b_all.clicked.connect(self._reset_all)
        b_close.clicked.connect(self.accept)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._build()
        self._selected()

    def _build(self):
        self.tree.clear()
        self._items.clear()
        for group, entries in self.reg.groups().items():
            g = QTreeWidgetItem([group, ""])
            f = g.font(0)
            f.setBold(True)
            g.setFont(0, f)
            self.tree.addTopLevelItem(g)
            for e in entries:
                it = QTreeWidgetItem([e["label"], ""])
                it.setData(0, Qt.UserRole, e["id"])
                g.addChild(it)
                self._items[e["id"]] = it
                self._refresh_row(e["id"])
            g.setExpanded(True)

    def _refresh_row(self, cid):
        it = self._items[cid]
        keys = self.reg.keys(cid)
        it.setText(1, _show(keys) + ("   (modificata)" if self.reg.is_custom(cid) else ""))

    def _filter(self, text):
        t = text.strip().lower()
        for g in range(self.tree.topLevelItemCount()):
            grp = self.tree.topLevelItem(g)
            vis = 0
            for k in range(grp.childCount()):
                ch = grp.child(k)
                hide = bool(t) and t not in ch.text(0).lower() and t not in ch.text(1).lower() \
                    and t not in grp.text(0).lower()
                ch.setHidden(hide)
                vis += not hide
            grp.setHidden(vis == 0)

    def _cid(self):
        its = self.tree.selectedItems()
        return its[0].data(0, Qt.UserRole) if its else None

    def _selected(self):
        cid = self._cid()
        on = cid is not None
        for w in (self.e1, self.e2, self.b_set, self.b_clear, self.b_reset):
            w.setEnabled(on)
        self.msg.setText("")
        if not on:
            self.name.setText("Scegli un comando dall'elenco")
            self.e1.clear()
            self.e2.clear()
            return
        e = self.reg.entries[cid]
        self.name.setText(f"{e['group']} › {e['label']}")
        keys = self.reg.keys(cid)
        self.e1.setKeySequence(QKeySequence(keys[0]) if keys else QKeySequence())
        self.e2.setKeySequence(QKeySequence(keys[1]) if len(keys) > 1 else QKeySequence())

    def _assign(self):
        cid = self._cid()
        if cid is None:
            return
        new = [s.toString(PORTABLE) for s in (self.e1.keySequence(), self.e2.keySequence()) if not s.isEmpty()]
        if len(set(new)) < len(new):
            new = new[:1]
        for k in new:
            other = self.reg.owner_of(k, exclude=cid)
            if other is None:
                continue
            oe = self.reg.entries[other]
            r = QMessageBox.question(
                self, "Tasto già usato",
                f"«{QKeySequence(k).toString(NATIVE)}» è già usato da «{oe['label']}» ({oe['group']}).\n"
                "Lo tolgo da lì e lo assegno a questo comando?")
            if r != QMessageBox.Yes:
                self.msg.setText("Non assegnato: scegli un altro tasto.")
                return
            self.reg.set_keys(other, [x for x in self.reg.keys(other) if x != _norm(k)])
            self._refresh_row(other)
        self.reg.set_keys(cid, new)
        self._refresh_row(cid)
        self.msg.setText("Assegnato." if new else "Scorciatoia tolta.")

    def _clear(self):
        cid = self._cid()
        if cid is None:
            return
        self.reg.set_keys(cid, [])
        self.e1.clear()
        self.e2.clear()
        self._refresh_row(cid)
        self.msg.setText("Scorciatoia tolta.")

    def _reset_one(self):
        cid = self._cid()
        if cid is None:
            return
        self.reg.reset(cid)
        self._refresh_row(cid)
        self._selected()
        self.msg.setText("Ripristinata.")

    def _reset_all(self):
        if QMessageBox.question(self, "Ripristina", "Ripristinare tutte le scorciatoie a quelle predefinite?") \
                != QMessageBox.Yes:
            return
        self.reg.reset_all()
        for cid in self._items:
            self._refresh_row(cid)
        self._selected()
        self.msg.setText("Tutte le scorciatoie sono tornate a quelle predefinite.")
