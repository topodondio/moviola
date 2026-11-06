"""Pannello "Clip": clip intere e parti selezionate, organizzate ad albero.

clip1 (file originale)
  ├ clip1.1
  └ clip1.2
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QItemSelectionModel, QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QMenu, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)


MIME = "application/x-moviola-refs"


def fmt_dur(sec: float) -> str:
    sec = max(0.0, sec)
    m, s = divmod(sec, 60)
    h, m = divmod(int(m), 60)
    if h:
        return f"{h}:{m:02d}:{s:04.1f}"
    return f"{m}:{s:04.1f}"


class BinTree(QTreeWidget):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels(["Nome", "Durata", "Ingresso → Uscita", "Stato"])
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setAlternatingRowColors(True)
        self.setIconSize(QSize(48, 27))
        self.setUniformRowHeights(False)
        self.setRootIsDecorated(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        h = self.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)

    def selected_ids(self):
        return [it.data(0, Qt.UserRole) for it in self.selectedItems()]

    def startDrag(self, actions):
        ids = self.selected_ids()
        if not ids:
            return
        mime = QMimeData()
        mime.setData(MIME, json.dumps(ids).encode())
        mime.setText(", ".join(ids))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            e.acceptProposedAction()
            self.files_dropped.emit(paths)
        else:
            e.ignore()


class BinPanel(QWidget):
    selection_changed = Signal(list)  # id selezionati
    current_changed = Signal(str)  # id da mostrare nel visore
    request = Signal(str, list)  # azione, id
    import_files = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        row = QHBoxLayout()
        b1 = QPushButton("+ Importa clip")
        b2 = QPushButton("+ Cartella")
        b1.setToolTip("Aggiungi file video (Ctrl/Cmd+I). Puoi anche trascinarli qui.")
        b1.clicked.connect(lambda: self.request.emit("import", []))
        b2.clicked.connect(lambda: self.request.emit("import_folder", []))
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        lay.addLayout(row)
        self.tree = BinTree()
        lay.addWidget(self.tree, 1)
        self.tree.itemSelectionChanged.connect(self._sel)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.files_dropped.connect(self.import_files)
        self.tree.itemDoubleClicked.connect(lambda it, c: self.request.emit("append", [it.data(0, Qt.UserRole)]))
        self._items: dict[str, QTreeWidgetItem] = {}
        self._status: dict[str, str] = {}
        self._icons: dict[str, QIcon] = {}
        self._block = False

    # ------------------------------------------------------------------
    def rebuild(self, project, select=None):
        self._block = True
        expanded = {k for k, it in self._items.items() if it.isExpanded()}
        selected = set(select) if select is not None else set(self.tree.selected_ids())
        first = not self._items
        self.tree.clear()
        self._items.clear()
        for src in project.sources:
            missing = not os.path.exists(src["path"])
            name = src["id"] + "   " + (src.get("label") or src["name"])
            top = QTreeWidgetItem([name, fmt_dur(src["duration"]), "", self._status.get(src["id"], "")])
            top.setData(0, Qt.UserRole, src["id"])
            tip = src["path"] + (f"\n{src['width']}×{src['height']} · {src['fps']:.2f} fps" if src["has_video"] else "")
            top.setToolTip(0, tip)
            if missing:
                top.setForeground(0, QColor(230, 90, 80))
                top.setText(3, "file mancante")
            if src["id"] in self._icons:
                top.setIcon(0, self._icons[src["id"]])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            self._items[src["id"]] = top
            for sub in project.subclips_of(src["id"]):
                lab = sub["id"] + (f"   {sub['label']}" if sub.get("label") else "")
                ch = QTreeWidgetItem([lab, fmt_dur(sub["out"] - sub["in"]),
                                      f"{fmt_dur(sub['in'])} → {fmt_dur(sub['out'])}", ""])
                ch.setData(0, Qt.UserRole, sub["id"])
                top.addChild(ch)
                self._items[sub["id"]] = ch
            top.setExpanded(first or src["id"] in expanded or any(s in selected for s in
                                                                  [c["id"] for c in project.subclips_of(src["id"])]))
        for k in selected:
            if k in self._items:
                self._items[k].setSelected(True)
        cur = next((self._items[k] for k in (select or []) if k in self._items), None)
        if cur is not None:
            self.tree.setCurrentItem(cur, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.tree.scrollToItem(cur)
        self._block = False

    def update_subclip(self, sub):
        it = self._items.get(sub["id"])
        if it:
            it.setText(1, fmt_dur(sub["out"] - sub["in"]))
            it.setText(2, f"{fmt_dur(sub['in'])} → {fmt_dur(sub['out'])}")

    def set_status(self, sid, text):
        self._status[sid] = text
        it = self._items.get(sid)
        if it:
            it.setText(3, text)

    def set_thumb(self, sid, path):
        pm = QPixmap(path)
        if pm.isNull():
            return
        icon = QIcon(pm)
        self._icons[sid] = icon
        it = self._items.get(sid)
        if it:
            it.setIcon(0, icon)

    def select(self, ids):
        self._block = True
        self.tree.clearSelection()
        for k in ids:
            if k in self._items:
                self._items[k].setSelected(True)
                self.tree.scrollToItem(self._items[k])
        if ids and ids[0] in self._items:
            self.tree.setCurrentItem(self._items[ids[0]], 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        self._block = False

    def selected_ids(self):
        return self.tree.selected_ids()

    def _sel(self):
        if self._block:
            return
        ids = self.tree.selected_ids()
        self.selection_changed.emit(ids)
        cur = self.tree.currentItem()
        if cur is not None and cur.isSelected():
            self.current_changed.emit(cur.data(0, Qt.UserRole))
        elif ids:
            self.current_changed.emit(ids[-1])

    def _menu(self, pos):
        ids = self.tree.selected_ids()
        it = self.tree.itemAt(pos)
        if it is not None and it.data(0, Qt.UserRole) not in ids:
            ids = [it.data(0, Qt.UserRole)]
        m = QMenu(self)
        if ids:
            m.addAction("Accoda alla timeline\tE", lambda: self.request.emit("append", ids))
            m.addAction("Inserisci alla testina\tF9", lambda: self.request.emit("insert_playhead", ids))
            m.addSeparator()
            m.addAction("Duplica\tCtrl+D", lambda: self.request.emit("duplicate", ids))
            m.addAction("Rinomina…\tF2", lambda: self.request.emit("rename", ids[:1]))
            m.addAction("Elimina", lambda: self.request.emit("delete", ids))
            m.addSeparator()
            m.addAction("Copia regolazioni colore", lambda: self.request.emit("copy_grade", ids[:1]))
            m.addAction("Incolla regolazioni colore", lambda: self.request.emit("paste_grade", ids))
            m.addSeparator()
            m.addAction("Crea proxy", lambda: self.request.emit("proxy", ids))
            m.addAction("Ricollega file…", lambda: self.request.emit("relink", ids[:1]))
            m.addAction("Mostra il file nella cartella", lambda: self.request.emit("reveal", ids[:1]))
        else:
            m.addAction("Importa clip…", lambda: self.request.emit("import", []))
        m.exec(self.tree.viewport().mapToGlobal(pos))
