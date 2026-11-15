"""Scheda "Clip": clip intere e parti selezionate, organizzate ad albero.

In alto le cartelle create dall'utente (anche una dentro l'altra), sotto le
clip non ancora sistemate, raggruppate per cartella del disco.

Musica/
  clip4 (♪)
Autunno (disco)/
  clip1 (file originale)
    ├ clip1.1
    └ clip1.2
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QItemSelectionModel, QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QStyle, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)


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


FOLDER = "cartella:"  # cartella del disco (raggruppamento automatico)
USERBIN = "raccoglitore:"  # cartella creata dall'utente
MIME_BINS = "application/x-moviola-bins"


def _is_group(rid) -> bool:
    return str(rid).startswith((FOLDER, USERBIN))
FILTERS = [("Tutte le clip", "all"), ("Non ancora usate", "unused")]


class BinTree(QTreeWidget):
    files_dropped = Signal(list, object)  # file, cartella di arrivo
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

    moved = Signal(list, list, object)  # clip, cartelle, cartella di arrivo (None = fuori)

    def selected_ids(self):
        out = [it.data(0, Qt.UserRole) for it in self.selectedItems()]
        return [x for x in out if x and not _is_group(x)]

    def selected_bins(self):
        out = [it.data(0, Qt.UserRole) for it in self.selectedItems()]
        return [str(x)[len(USERBIN):] for x in out if str(x).startswith(USERBIN)]

    def bin_at(self, item):
        """Cartella dell'utente in cui sta una riga (None = fuori dalle cartelle)."""
        while item is not None:
            rid = str(item.data(0, Qt.UserRole))
            if rid.startswith(USERBIN):
                return rid[len(USERBIN):]
            item = item.parent()
        return None

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        if e.buttons() != Qt.NoButton:
            return
        it = self.itemAt(e.position().toPoint())
        rid = it.data(0, Qt.UserRole) if it is not None else ""
        if _is_group(rid):
            rid = ""
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
        bins = self.selected_bins()
        if not ids and not bins:
            return
        mime = QMimeData()
        if ids:
            mime.setData(MIME, json.dumps(ids).encode())
            mime.setText(", ".join(ids))
        if bins:
            mime.setData(MIME_BINS, json.dumps(bins).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)
        self._set_drop_target(None)

    # trascinando dentro la lista: le clip (e le cartelle) cambiano cartella
    def _internal(self, e):
        md = e.mimeData()
        return e.source() is self and (md.hasFormat(MIME) or md.hasFormat(MIME_BINS))

    def _drop_item(self, e):
        """Riga su cui si lascia: la cartella che la contiene, oppure la riga
        del raggruppamento per disco (= fuori dalle cartelle)."""
        it = self.itemAt(e.position().toPoint())
        while it is not None and not _is_group(it.data(0, Qt.UserRole)):
            it = it.parent()
        return it

    def _set_drop_target(self, it):
        if getattr(self, "_drop_target", None) is not it:
            self._drop_target = it
            self.viewport().update()

    def paintEvent(self, e):
        super().paintEvent(e)
        it = getattr(self, "_drop_target", None)
        if it is None:
            return
        r = self.visualItemRect(it)
        if r.isNull():
            return
        r.setLeft(0)
        r.setRight(self.viewport().width() - 1)
        p = QPainter(self.viewport())
        p.setPen(QPen(QColor(232, 163, 61), 2))
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
        p.end()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or self._internal(e):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._internal(e):
            it = self._drop_item(e)
            self._set_drop_target(it if it is not None and str(it.data(0, Qt.UserRole)).startswith(USERBIN)
                                  else None)
            e.acceptProposedAction()
        elif e.mimeData().hasUrls():
            it = self._drop_item(e)
            self._set_drop_target(it if it is not None and str(it.data(0, Qt.UserRole)).startswith(USERBIN)
                                  else None)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._set_drop_target(None)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        target = self.bin_at(self._drop_item(e))
        self._set_drop_target(None)
        md = e.mimeData()
        if self._internal(e):
            ids = json.loads(bytes(md.data(MIME)).decode()) if md.hasFormat(MIME) else []
            bins = json.loads(bytes(md.data(MIME_BINS)).decode()) if md.hasFormat(MIME_BINS) else []
            e.acceptProposedAction()
            self.moved.emit(ids, bins, target)
        elif md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            e.acceptProposedAction()
            self.files_dropped.emit(paths, target)
        else:
            e.ignore()


class BinPanel(QWidget):
    """Un'unica lista: clip video e, in fondo, musica e suoni (♪)."""

    selection_changed = Signal(list)  # id selezionati
    current_changed = Signal(str)  # id da mostrare nel visore
    request = Signal(str, list)  # azione, id
    import_files = Signal(list, object)  # file, cartella di arrivo (o None)
    hover = Signal(str, float)
    rebuilt = Signal()  # l'elenco è stato ricostruito (clip e parti possono essere cambiate)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 4)
        lay.setSpacing(4)
        self.tree, w = self._section("CLIP", [
            ("+ Video", "import", "Importa clip video (Ctrl/Cmd+I).\nPuoi anche trascinare file o cartelle qui."),
            ("+ ♪ Audio", "import_audio", "Importa musica o suoni (mp3, wav, m4a, aiff…).\n"
                                         "In timeline vanno sull'ultima traccia audio."),
            ("+ Da cartella", "import_folder", "Importa tutti i video e gli audio di una cartella del disco")],
            new_bin=True)
        lay.addWidget(w, 1)
        self.audio_tree = self.tree
        self.trees = (self.tree,)
        self._audio_ids: set[str] = set()
        self._items: dict[str, QTreeWidgetItem] = {}
        self._status: dict[str, str] = {}
        self._src_names: dict[str, tuple] = {}
        self._known_folders: set[str] = set()
        self._icons: dict[str, QIcon] = {}
        self._block = False
        self._active = self.tree

    def _section(self, title, buttons, new_bin=False):
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
        self.filter.setToolTip("Tutte le clip oppure solo quelle non ancora messe in timeline")
        self.filter.currentIndexChanged.connect(lambda _: self._apply_filter())
        top.addWidget(self.filter, 1)
        v.addLayout(top)
        v.addLayout(self._tag_chips())
        row = QHBoxLayout()
        for text, req, tip in buttons:
            b = QPushButton(text)
            b.setStyleSheet("padding: 3px 6px;")
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, r=req: self.request.emit(r, []))
            row.addWidget(b)
        row.addStretch(1)
        if new_bin:
            nb = QPushButton()
            nb.setIcon(_new_folder_icon())
            nb.setFixedWidth(34)
            nb.setToolTip("Nuova cartella (Ctrl/Cmd+Maiusc+N)\n"
                          "Le clip selezionate ci entrano subito. Poi trascina le clip\n"
                          "sulle cartelle per riordinarle; si possono fare cartelle\n"
                          "dentro altre cartelle.")
            nb.clicked.connect(lambda: self.request.emit("new_bin", self.selected_ids()))
            row.addWidget(nb)
        v.addLayout(row)
        tree = BinTree()
        tree.hover.connect(self.hover)
        v.addWidget(tree, 1)
        tree.itemSelectionChanged.connect(lambda t=tree: self._sel(t))
        tree.customContextMenuRequested.connect(lambda pos, t=tree: self._menu(t, pos))
        tree.files_dropped.connect(self.import_files)
        tree.moved.connect(self._moved)
        tree.itemDoubleClicked.connect(self._double_clicked)
        tree.itemExpanded.connect(lambda it: self._remember_open(it, True))
        tree.itemCollapsed.connect(lambda it: self._remember_open(it, False))
        return tree, w

    def _tag_chips(self):
        """Una pastiglia per ogni colore: se ne accendi una o più, l'elenco mostra solo
        le clip con quelle etichette (es. verdi e gialle). Nessuna accesa = tutte."""
        row = QHBoxLayout()
        row.setSpacing(3)
        self._tag_sel: set[str] = set()
        self._chips: dict[str, QToolButton] = {}
        lab = QLabel("Mostra")
        lab.setStyleSheet("color:#8d8d8d;")
        row.addWidget(lab)
        for name, col in TAGS + [("Senza etichetta", "")]:
            b = QToolButton()
            b.setCheckable(True)
            b.setFixedSize(22, 18)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(f"{name}: mostra solo le clip con questa etichetta\n"
                         "(puoi accenderne più di una; spente tutte, vedi tutto)")
            if col:
                b.setStyleSheet(f"QToolButton {{ background:{col}; border:1px solid #1b1b1b; border-radius:3px; }}"
                                "QToolButton:!checked { background:" + QColor(col).darker(210).name() + "; }"
                                "QToolButton:checked { border:2px solid #f2f2f2; }")
            else:
                b.setText("–")
                b.setStyleSheet("QToolButton { color:#bbb; background:#2d2f33; border:1px dashed #6a6a6a; border-radius:3px; }"
                                "QToolButton:checked { border:2px solid #f2f2f2; color:#fff; }")
            b.toggled.connect(lambda on, c=col: self._chip_toggled(c, on))
            self._chips[col] = b
            row.addWidget(b)
        row.addStretch(1)
        clear = QToolButton()
        clear.setText("Tutte")
        clear.setFocusPolicy(Qt.NoFocus)
        clear.setToolTip("Togli il filtro per colore")
        clear.clicked.connect(self.clear_tag_filter)
        row.addWidget(clear)
        return row

    def _chip_toggled(self, col, on):
        (self._tag_sel.add if on else self._tag_sel.discard)(col)
        self._apply_filter()

    def clear_tag_filter(self):
        for b in self._chips.values():
            b.blockSignals(True)
            b.setChecked(False)
            b.blockSignals(False)
        self._tag_sel.clear()
        self._apply_filter()

    # ------------------------------------------------------------------
    def _tree_for(self, src):
        return self.tree

    def rebuild(self, project, select=None):
        self._block = True
        self._project = project
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
        bins = {b["id"]: b for b in project.bins}
        self._bins = bins

        # 1) cartelle create dall'utente, in alto e in ordine alfabetico
        bin_items = {}

        def make_bin(b):
            if b["id"] in bin_items:
                return bin_items[b["id"]]
            bit = QTreeWidgetItem([b["name"], "", ""])
            bid = USERBIN + b["id"]
            bit.setData(0, Qt.UserRole, bid)
            bit.setIcon(0, _user_folder_icon())
            bit.setToolTip(0, "Cartella del progetto: trascina qui clip e musica.\n"
                              "Tasto destro per rinominarla o eliminarla.")
            f = bit.font(0)
            f.setBold(True)
            bit.setFont(0, f)
            bit.setForeground(0, QColor(236, 236, 236))
            parent = bins.get(b.get("parent"))
            if parent is not None and parent["id"] != b["id"]:
                make_bin(parent).addChild(bit)
            else:
                self.tree.addTopLevelItem(bit)
            bin_items[b["id"]] = bit
            self._items[bid] = bit
            return bit

        for b in sorted(bins.values(), key=lambda x: x["name"].lower()):
            make_bin(b)

        # 2) le altre clip raggruppate per cartella del disco (come sono archiviate)
        loose = [x for x in ordered if x.get("bin") not in bins]
        folders = {}
        for src in loose:
            d = os.path.dirname(src["path"])
            if d not in folders:
                fid = FOLDER + d
                fit = QTreeWidgetItem([os.path.basename(d) or d, "", ""])
                fit.setData(0, Qt.UserRole, fid)
                fit.setToolTip(0, d + "\n(cartella del disco: trascina le clip su una tua cartella per spostarle)")
                fit.setIcon(0, self.style().standardIcon(QStyle.SP_DirIcon))
                fit.setForeground(0, QColor(232, 163, 61))
                ff = fit.font(0)
                ff.setBold(True)
                fit.setFont(0, ff)
                fit.setFlags(fit.flags() & ~Qt.ItemIsDragEnabled)
                self.tree.addTopLevelItem(fit)
                self._items[fid] = fit
                folders[d] = fit
        for d, fit in folders.items():
            n = sum(1 for x in loose if os.path.dirname(x["path"]) == d)
            fit.setText(1, f"{n} clip")
            fit.setExpanded(first or FOLDER + d in expanded or FOLDER + d not in self._known_folders)
            self._known_folders.add(FOLDER + d)

        for src in ordered:
            if not src.get("has_video"):
                self._audio_ids.add(src["id"])
                self._audio_ids.update(c["id"] for c in project.subclips_of(src["id"]))
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
            if src.get("bin") in bin_items:
                bin_items[src["bin"]].addChild(top)
            else:
                folders[os.path.dirname(src["path"])].addChild(top)
            for sub in project.subclips_of(src["id"]):
                lab = sub["id"] + (f"   {sub['label']}" if sub.get("label") else "")
                ch = QTreeWidgetItem([lab, fmt_dur(sub["out"] - sub["in"]),
                                      f"{fmt_dur(sub['in'])} → {fmt_dur(sub['out'])}"])
                ch.setData(0, Qt.UserRole, sub["id"])
                ch.setToolTip(0, lab)
                if sub["id"] in self._icons:
                    ch.setIcon(0, self._icons[sub["id"]])
                _tag_row(ch, sub.get("tag"))
                self._meta[sub["id"]] = (sub.get("tag"), sub["id"] in used_subs, src["id"])
                _mark_used(ch, sub["id"] in used_subs)
                top.addChild(ch)
                self._items[sub["id"]] = ch
            top.setExpanded(first or src["id"] in expanded or any(s in selected for s in
                                                                  [c["id"] for c in project.subclips_of(src["id"])]))

        # conteggio e apertura delle cartelle dell'utente (dopo aver messo le clip)
        for bid, bit in bin_items.items():
            n = sum(1 for x in ordered if x.get("bin") == bid)
            sub = sum(1 for x in bins.values() if x.get("parent") == bid)
            parts = ([f"{n} clip"] if n else []) + ([f"{sub} cart."] if sub else [])
            bit.setText(1, " · ".join(parts) if parts else "vuota")
            bit.setExpanded(bool(bins[bid].get("open", True)))

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
        self.rebuilt.emit()

    def _apply_filter(self):
        """Nasconde le righe secondo il filtro scelto (le clip intere restano se
        hanno almeno una parte da mostrare)."""
        mode = self.filter.currentData()
        meta = getattr(self, "_meta", {})

        want = self._tag_sel

        def ok(rid):
            tag, used, parent = meta.get(rid, (None, False, None))
            if want:
                # una parte senza etichetta propria vale come la sua clip
                eff = tag or (meta.get(parent, (None,))[0] if parent else None)
                if (eff or "") not in want:
                    return False
            if mode == "unused" and used:
                return False
            return True

        for rid, it in self._items.items():
            if meta.get(rid, (None, False, None))[2] is not None:
                it.setHidden(not ok(rid))
        for rid, it in self._items.items():
            if str(rid).startswith(FOLDER):
                continue
            if meta.get(rid, (None, False, None))[2] is None:
                kids = [it.child(k) for k in range(it.childCount())]
                it.setHidden(not (ok(rid) or any(not k.isHidden() for k in kids)))
        for rid, it in self._items.items():
            if str(rid).startswith(FOLDER):
                it.setHidden(all(it.child(k).isHidden() for k in range(it.childCount())))

        def hide_empty_bins(it):
            """Con un filtro attivo le cartelle senza niente da mostrare spariscono;
            senza filtro restano tutte (anche quelle appena create, vuote)."""
            visible = False
            for k in range(it.childCount()):
                ch = it.child(k)
                if str(ch.data(0, Qt.UserRole)).startswith(USERBIN):
                    visible = hide_empty_bins(ch) or visible
                else:
                    visible = visible or not ch.isHidden()
            it.setHidden((mode != "all" or bool(want)) and not visible)
            return visible

        for k in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(k)
            if str(top.data(0, Qt.UserRole)).startswith(USERBIN):
                hide_empty_bins(top)

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
        if cur is not None and cur.isSelected() and not _is_group(cur.data(0, Qt.UserRole)):
            self.current_changed.emit(cur.data(0, Qt.UserRole))
        elif ids:
            self.current_changed.emit(ids[-1])

    def target_bin(self):
        """Cartella dell'utente dove finiscono i file importati: quella
        selezionata, o quella che contiene la clip selezionata."""
        it = self.tree.currentItem()
        if it is None or not it.isSelected():
            return None
        return self.tree.bin_at(it)

    def _double_clicked(self, it, col):
        rid = str(it.data(0, Qt.UserRole))
        if rid.startswith(USERBIN):
            self.request.emit("rename_bin:" + rid[len(USERBIN):], [])
        elif not rid.startswith(FOLDER):
            self.request.emit("append", [rid])

    def _remember_open(self, it, state):
        if self._block:
            return
        rid = str(it.data(0, Qt.UserRole))
        if rid.startswith(USERBIN):
            b = getattr(self, "_bins", {}).get(rid[len(USERBIN):])
            if b is not None:
                b["open"] = state  # solo come si presenta la lista: non serve salvare

    def _moved(self, ids, bins, target):
        if bins:
            self.request.emit("move_bins:" + (target or ""), bins)
        if ids:
            self.request.emit("move_to_bin:" + (target or ""), ids)

    def _bin_menu(self, m, ids, here):
        """Sottomenu "Sposta in cartella" con tutte le cartelle (rientrate)."""
        bins = getattr(self, "_bins", {})
        sm = m.addMenu("Sposta in cartella")
        sm.addAction(_new_folder_icon(), "Nuova cartella con queste clip…",
                     lambda: self.request.emit("new_bin", ids))
        if bins:
            sm.addSeparator()

            def add(parent, depth):
                for b in sorted((x for x in bins.values() if x.get("parent") == parent),
                                key=lambda x: x["name"].lower()):
                    a = sm.addAction(_user_folder_icon(), "    " * depth + b["name"],
                                     lambda bb=b["id"]: self.request.emit("move_to_bin:" + bb, ids))
                    a.setEnabled(b["id"] != here)
                    add(b["id"], depth + 1)
            add(None, 0)
        if here:
            sm.addSeparator()
            sm.addAction("Fuori dalle cartelle", lambda: self.request.emit("move_to_bin:", ids))

    def _menu(self, tree, pos):
        ids = tree.selected_ids()
        it = tree.itemAt(pos)
        rid = str(it.data(0, Qt.UserRole)) if it is not None else ""
        m = QMenu(self)
        if rid.startswith(USERBIN):
            bid = rid[len(USERBIN):]
            m.addAction("Rinomina cartella…", lambda: self.request.emit("rename_bin:" + bid, []))
            m.addAction(_new_folder_icon(), "Nuova cartella qui dentro…",
                        lambda: self.request.emit("new_bin_in:" + bid, []))
            m.addSeparator()
            m.addAction("Importa clip video qui…", lambda: self.request.emit("import_in:" + bid, []))
            m.addAction("Importa musica o suoni qui…", lambda: self.request.emit("import_audio_in:" + bid, []))
            m.addSeparator()
            m.addAction("Elimina cartella (le clip restano)", lambda: self.request.emit("delete_bin:" + bid, []))
            m.exec(tree.viewport().mapToGlobal(pos))
            return
        if it is not None and rid not in ids:
            ids = [] if _is_group(rid) else [rid]
        audio = bool(ids) and all(i in self._audio_ids for i in ids)
        if ids:
            m.addAction("Accoda alla timeline\tE", lambda: self.request.emit("append", ids))
            m.addAction("Inserisci alla testina\tF9", lambda: self.request.emit("insert_playhead", ids))
            m.addAction("Sovrascrivi alla testina\tF10", lambda: self.request.emit("overwrite_playhead", ids))
            m.addSeparator()
            self._bin_menu(m, ids, tree.bin_at(it) if it is not None else None)
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
            m.addAction(_new_folder_icon(), "Nuova cartella…", lambda: self.request.emit("new_bin", []))
            m.addSeparator()
            m.addAction("Importa clip video…", lambda: self.request.emit("import", []))
            m.addAction("Importa musica o suoni…", lambda: self.request.emit("import_audio", []))
        m.exec(tree.viewport().mapToGlobal(pos))


_ICONS = {}


def _user_folder_icon():
    """Cartella dell'utente: bianca, per distinguerla da quelle del disco."""
    if "user" not in _ICONS:
        _ICONS["user"] = QIcon(_folder_pixmap(QColor(225, 225, 225), plus=False))
    return _ICONS["user"]


def _new_folder_icon():
    if "new" not in _ICONS:
        _ICONS["new"] = QIcon(_folder_pixmap(QColor(235, 235, 235), plus=True))
    return _ICONS["new"]


def _folder_pixmap(col, plus):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainterPath
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(col, 4))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(6, 16)
    path.lineTo(24, 16)
    path.lineTo(30, 22)
    path.lineTo(58, 22)
    path.lineTo(58, 52)
    path.lineTo(6, 52)
    path.closeSubpath()
    p.drawPath(path)
    if plus:
        p.setBrush(QColor(232, 163, 61))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(36, 34, 26, 26))
        p.setPen(QPen(QColor(30, 30, 30), 4))
        p.drawLine(49, 40, 49, 54)
        p.drawLine(42, 47, 56, 47)
    p.end()
    return pm
