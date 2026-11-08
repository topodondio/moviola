"""Scheda "Clip": clip intere e parti selezionate, organizzate ad albero,
in due sezioni (video, audio/musica).

clip1 (file originale)
  ├ clip1.1
  └ clip1.2
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QItemSelectionModel, QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)


MIME = "application/x-moviola-refs"


def fmt_dur(sec: float) -> str:
    sec = max(0.0, sec)
    m, s = divmod(sec, 60)
    h, m = divmod(int(m), 60)
    if h:
        return f"{h}:{m:02d}:{s:04.1f}"
    return f"{m}:{s:04.1f}"


TAGS = [("Verde (mi piace)", "#3e9a4a"), ("Giallo", "#b9a028"), ("Arancione", "#c27a2a"),
        ("Rosso (da scartare)", "#b04040"), ("Blu", "#3a6db8"), ("Viola", "#7d4fb0"), ("Grigio", "#6a6a6a")]


def _mark_used(item, used):
    """Le clip già messe in timeline hanno una spunta e il testo più tenue:
    quelle ancora da usare saltano all'occhio."""
    if used:
        item.setText(1, "✓ " + item.text(1))
        for col in range(3):
            item.setForeground(col, QColor(140, 140, 140))
        item.setToolTip(1, "Già usata in timeline")
    else:
        item.setToolTip(1, "Non ancora usata in timeline")


def _tag_row(item, tag):
    """Colora la riga con l'etichetta scelta (sfondo tenue + bordino pieno)."""
    if not tag:
        return
    c = QColor(tag)
    soft = QColor(c.red(), c.green(), c.blue(), 85)
    for col in range(item.columnCount() or 3):
        item.setBackground(col, soft)
    pm = QPixmap(6, 26)
    pm.fill(c)
    if item.icon(0).isNull():
        item.setIcon(0, QIcon(pm))


GREEN, RED = TAGS[0][1], TAGS[3][1]
FILTERS = [("Tutte le clip", "all"), ("Solo le verdi", "green"), ("Nascondi le rosse", "nored"),
           ("Non ancora usate", "unused")]


class BinTree(QTreeWidget):
    files_dropped = Signal(list)
    hover = Signal(str, float)  # clip sotto al mouse ("" = uscito) e posizione 0..1 nella riga

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHeaderLabels(["Nome", "Durata", "Ingresso → Uscita"])
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setAlternatingRowColors(True)
        self.setIconSize(QSize(44, 25))
        self.setTextElideMode(Qt.ElideRight)
        self.setIndentation(14)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setUniformRowHeights(False)
        self.setRootIsDecorated(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setMouseTracking(True)
        self._hover_id = ""
        h = self.header()
        h.setStretchLastSection(False)
        h.setMinimumSectionSize(40)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)

    def selected_ids(self):
        return [it.data(0, Qt.UserRole) for it in self.selectedItems()]

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        if e.buttons() != Qt.NoButton:
            return
        it = self.itemAt(e.position().toPoint())
        rid = it.data(0, Qt.UserRole) if it is not None else ""
        frac = min(1.0, max(0.0, e.position().x() / max(1, self.viewport().width())))
        if rid or self._hover_id:
            self._hover_id = rid
            self.hover.emit(rid, frac)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if self._hover_id:
            self._hover_id = ""
            self.hover.emit("", 0.0)

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
    """Un'unica lista: clip video e, in fondo, musica e suoni (♪)."""

    selection_changed = Signal(list)  # id selezionati
    current_changed = Signal(str)  # id da mostrare nel visore
    request = Signal(str, list)  # azione, id
    import_files = Signal(list)
    hover = Signal(str, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 4)
        lay.setSpacing(4)
        self.tree, w = self._section("CLIP", [
            ("+ Video", "import", "Importa clip video (Ctrl/Cmd+I).\nPuoi anche trascinare file o cartelle qui."),
            ("+ Cartella", "import_folder", "Importa tutta una cartella"),
            ("+ ♪ Audio", "import_audio", "Importa musica o suoni (mp3, wav, m4a, aiff…).\n"
                                         "In timeline vanno sull'ultima traccia audio.")])
        lay.addWidget(w, 1)
        self.audio_tree = self.tree
        self.trees = (self.tree,)
        self._audio_ids: set[str] = set()
        self._items: dict[str, QTreeWidgetItem] = {}
        self._status: dict[str, str] = {}
        self._src_names: dict[str, tuple] = {}
        self._icons: dict[str, QIcon] = {}
        self._block = False
        self._active = self.tree

    def _section(self, title, buttons):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        top = QHBoxLayout()
        lab = QLabel(title)
        lab.setStyleSheet("color:#e8a33d; font-weight:700; letter-spacing:1px;")
        top.addWidget(lab)
        self.filter = QComboBox()
        for text, key in FILTERS:
            self.filter.addItem(text, key)
        self.filter.setToolTip("Quali clip mostrare. Valutazione veloce: 1 verde · 2 giallo · 3 rosso · 0 nessuna")
        self.filter.currentIndexChanged.connect(lambda _: self._apply_filter())
        top.addWidget(self.filter, 1)
        v.addLayout(top)
        row = QHBoxLayout()
        for text, req, tip in buttons:
            b = QPushButton(text)
            b.setStyleSheet("padding: 3px 6px;")
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, r=req: self.request.emit(r, []))
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        tree = BinTree()
        tree.hover.connect(self.hover)
        v.addWidget(tree, 1)
        tree.itemSelectionChanged.connect(lambda t=tree: self._sel(t))
        tree.customContextMenuRequested.connect(lambda pos, t=tree: self._menu(t, pos))
        tree.files_dropped.connect(self.import_files)
        tree.itemDoubleClicked.connect(lambda it, c: self.request.emit("append", [it.data(0, Qt.UserRole)]))
        return tree, w

    # ------------------------------------------------------------------
    def _tree_for(self, src):
        return self.tree

    def rebuild(self, project, select=None):
        self._block = True
        expanded = {k for k, it in self._items.items() if it.isExpanded()}
        selected = set(select) if select is not None else set(self.selected_ids())
        first = not self._items
        for t in self.trees:
            t.clear()
        self._items.clear()
        self._audio_ids = set()
        # prima le clip video, poi musica e suoni
        ordered = [x for x in project.sources if x.get("has_video")] + \
                  [x for x in project.sources if not x.get("has_video")]
        used_subs = {i.get("origin") for i in project.items}
        used_srcs = {i.get("source") for i in project.items}
        self._meta = {}  # id -> (etichetta, usata, id della clip intera)
        for src in ordered:
            if not src.get("has_video"):
                self._audio_ids.add(src["id"])
                self._audio_ids.update(c["id"] for c in project.subclips_of(src["id"]))
            tree = self._tree_for(src)
            missing = not os.path.exists(src["path"])
            mark = "" if src.get("has_video") else "♪ "
            top = QTreeWidgetItem(["", fmt_dur(src["duration"]), ""])
            top.setData(0, Qt.UserRole, src["id"])
            self._src_names[src["id"]] = (mark + src["id"], src.get("label") or src["name"])
            tip = src["path"] + (f"\n{src['width']}×{src['height']} · {src['fps']:.2f} fps" if src["has_video"] else "")
            top.setToolTip(0, tip)
            self._items[src["id"]] = top
            self._set_top_text(src["id"], "file mancante" if missing else None)
            if missing:
                top.setForeground(0, QColor(230, 90, 80))
            _tag_row(top, src.get("tag"))
            if src["id"] in self._icons:
                top.setIcon(0, self._icons[src["id"]])
            self._meta[src["id"]] = (src.get("tag"), src["id"] in used_srcs, None)
            _mark_used(top, src["id"] in used_srcs)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            tree.addTopLevelItem(top)
            for sub in project.subclips_of(src["id"]):
                lab = sub["id"] + (f"   {sub['label']}" if sub.get("label") else "")
                ch = QTreeWidgetItem([lab, fmt_dur(sub["out"] - sub["in"]),
                                      f"{fmt_dur(sub['in'])} → {fmt_dur(sub['out'])}"])
                ch.setData(0, Qt.UserRole, sub["id"])
                ch.setToolTip(0, lab)
                _tag_row(ch, sub.get("tag"))
                self._meta[sub["id"]] = (sub.get("tag"), sub["id"] in used_subs, src["id"])
                _mark_used(ch, sub["id"] in used_subs)
                top.addChild(ch)
                self._items[sub["id"]] = ch
            top.setExpanded(first or src["id"] in expanded or any(s in selected for s in
                                                                  [c["id"] for c in project.subclips_of(src["id"])]))
        for k in selected:
            if k in self._items:
                self._items[k].setSelected(True)
        cur = next((self._items[k] for k in (select or []) if k in self._items), None)
        if cur is not None:
            t = cur.treeWidget()
            t.setCurrentItem(cur, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
            t.scrollToItem(cur)
            self._active = t
        self._apply_filter()
        self._block = False

    def _apply_filter(self):
        """Nasconde le righe secondo il filtro scelto (le clip intere restano se
        hanno almeno una parte da mostrare)."""
        mode = self.filter.currentData()
        meta = getattr(self, "_meta", {})

        def ok(rid):
            tag, used, parent = meta.get(rid, (None, False, None))
            if mode == "green":
                return tag == GREEN
            if mode == "nored":
                ptag = meta.get(parent, (None,))[0] if parent else None
                return tag != RED and ptag != RED
            if mode == "unused":
                return not used
            return True

        for rid, it in self._items.items():
            if meta.get(rid, (None, False, None))[2] is not None:
                it.setHidden(not ok(rid))
        for rid, it in self._items.items():
            if meta.get(rid, (None, False, None))[2] is None:
                kids = [it.child(k) for k in range(it.childCount())]
                show = ok(rid) or any(not k.isHidden() for k in kids)
                if mode == "nored" and meta.get(rid, (None,))[0] == RED:
                    show = False
                it.setHidden(not show)

    def update_subclip(self, sub):
        it = self._items.get(sub["id"])
        if it:
            it.setText(1, fmt_dur(sub["out"] - sub["in"]))
            it.setText(2, f"{fmt_dur(sub['in'])} → {fmt_dur(sub['out'])}")

    def _set_top_text(self, sid, status=None):
        """Riga della clip: codice in grassetto sulla prima riga, nome del file
        (e lo stato dei lavori in corso) sulla seconda."""
        it = self._items.get(sid)
        names = self._src_names.get(sid)
        if it is None or names is None:
            return
        st = status if status is not None else self._status.get(sid, "")
        second = names[1] + (f"  ·  {st}" if st else "")
        it.setText(0, f"{names[0]}\n{second}")

    def set_status(self, sid, text):
        self._status[sid] = text
        self._set_top_text(sid)

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
        for t in self.trees:
            t.clearSelection()
        for k in ids:
            if k in self._items:
                self._items[k].setSelected(True)
                self._items[k].treeWidget().scrollToItem(self._items[k])
        if ids and ids[0] in self._items:
            it = self._items[ids[0]]
            it.treeWidget().setCurrentItem(it, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
            self._active = it.treeWidget()
        self._block = False

    def selected_ids(self):
        out = []
        for t in self.trees:
            out += t.selected_ids()
        return out

    def _sel(self, tree):
        if self._block:
            return
        # la selezione è in una sola sezione alla volta
        if tree.selectedItems():
            self._block = True
            for t in self.trees:
                if t is not tree:
                    t.clearSelection()
            self._block = False
            self._active = tree
        ids = self.selected_ids()
        self.selection_changed.emit(ids)
        cur = tree.currentItem()
        if cur is not None and cur.isSelected():
            self.current_changed.emit(cur.data(0, Qt.UserRole))
        elif ids:
            self.current_changed.emit(ids[-1])

    def _menu(self, tree, pos):
        ids = tree.selected_ids()
        it = tree.itemAt(pos)
        if it is not None and it.data(0, Qt.UserRole) not in ids:
            ids = [it.data(0, Qt.UserRole)]
        audio = bool(ids) and all(i in self._audio_ids for i in ids)
        m = QMenu(self)
        if ids:
            m.addAction("Accoda alla timeline\tE", lambda: self.request.emit("append", ids))
            m.addAction("Inserisci alla testina\tF9", lambda: self.request.emit("insert_playhead", ids))
            m.addSeparator()
            m.addAction("Duplica\tCtrl+D", lambda: self.request.emit("duplicate", ids))
            m.addAction("Rinomina…\tF2", lambda: self.request.emit("rename", ids[:1]))
            m.addAction("Elimina", lambda: self.request.emit("delete", ids))
            tm = m.addMenu("Colore etichetta")
            for lab, col in TAGS:
                pm = QPixmap(14, 14)
                pm.fill(QColor(col))
                tm.addAction(QIcon(pm), lab, lambda cc=col: self.request.emit("tag:" + cc, ids))
            tm.addSeparator()
            tm.addAction("Nessuno", lambda: self.request.emit("tag:", ids))
            m.addSeparator()
            if not audio:
                m.addAction("Copia regolazioni colore", lambda: self.request.emit("copy_grade", ids[:1]))
                m.addAction("Incolla regolazioni colore", lambda: self.request.emit("paste_grade", ids))
                m.addSeparator()
                m.addAction("Crea proxy", lambda: self.request.emit("proxy", ids))
            m.addAction("Ricollega file…", lambda: self.request.emit("relink", ids[:1]))
            m.addAction("Mostra il file nella cartella", lambda: self.request.emit("reveal", ids[:1]))
        else:
            m.addAction("Importa clip video…", lambda: self.request.emit("import", []))
            m.addAction("Importa musica o suoni…", lambda: self.request.emit("import_audio", []))
        m.exec(tree.viewport().mapToGlobal(pos))
