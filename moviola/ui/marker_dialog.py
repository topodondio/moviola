"""Elenco dei segnaposti: salto, nome, colore, capitoli per YouTube."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QMenu, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from ..project import MARKER_COLORS
from .timeline import tc

COLOR_NAMES = ("Arancione", "Rosso", "Verde", "Blu", "Viola", "Bianco")


def chapters_text(project) -> str:
    """Elenco «00:00 Titolo» da incollare nella descrizione di un video. YouTube vuole
    il primo capitolo a 00:00."""
    rows = [(m["t"], (m.get("label") or "").strip() or f"Capitolo {n}")
            for n, m in enumerate([m for m in project.markers if project.is_chapter(m)], 1)]
    if rows and rows[0][0] > 0.5:
        rows.insert(0, (0.0, "Inizio"))
    out = []
    for t, name in rows:
        s = int(round(t))
        h, rest = divmod(s, 3600)
        mi, se = divmod(rest, 60)
        out.append(f"{h}:{mi:02d}:{se:02d} {name}" if h else f"{mi:02d}:{se:02d} {name}")
    return "\n".join(out)


class MarkerDialog(QDialog):
    """Finestra non modale: si lascia aperta mentre si monta."""
    goto = Signal(float)
    changed = Signal()
    checkpoint = Signal()

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Segnaposti")
        self.setMinimumSize(420, 340)
        lay = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Tempo", "Nome", "Tipo"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnWidth(0, 110)
        self.tree.setColumnWidth(1, 200)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._goto(it))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        for text, fn, tip in (
            ("Vai", lambda: self._goto(self.tree.currentItem()), "Porta la testina sul segnaposto (doppio clic)"),
            ("Rinomina…", self._rename, "Dai un nome al segnaposto"),
            ("Capitolo ⇄ indicatore", self._toggle_kind,
             "Un capitolo divide il video all'esportazione; un indicatore è solo un promemoria"),
            ("Elimina", self._delete, "Elimina il segnaposto scelto"),
            ("Copia capitoli", self._copy, "Copia l'elenco «00:00 Titolo» per la descrizione di YouTube"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.refresh()

    @staticmethod
    def _chip(color):
        pm = QPixmap(12, 12)
        pm.fill(QColor(color))
        return pm

    def refresh(self, project=None):
        if project is not None:
            self.project = project
        cur = self._current_id()
        self.tree.clear()
        fps = self.project.fps
        for m in self.project.markers:
            it = QTreeWidgetItem([tc(m["t"], fps), m.get("label") or "",
                                 "Capitolo" if self.project.is_chapter(m) else "Indicatore"])
            it.setData(0, Qt.UserRole, m["id"])
            it.setIcon(1, self._chip(m.get("color") or MARKER_COLORS[0]))
            self.tree.addTopLevelItem(it)
            if m["id"] == cur:
                self.tree.setCurrentItem(it)

    def _current_id(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _goto(self, it):
        if it is None:
            return
        m = self.project.marker(it.data(0, Qt.UserRole))
        if m:
            self.goto.emit(m["t"])

    def _rename(self):
        m = self.project.marker(self._current_id() or "")
        if m is None:
            return
        text, ok = QInputDialog.getText(self, "Segnaposto", "Nome del segnaposto:", text=m.get("label", ""))
        if ok:
            self.checkpoint.emit()
            m["label"] = text.strip()
            self.changed.emit()
            self.refresh()

    def _toggle_kind(self):
        m = self.project.marker(self._current_id() or "")
        if m is None:
            return
        self.checkpoint.emit()
        m["kind"] = "note" if self.project.is_chapter(m) else "chapter"
        self.changed.emit()
        self.refresh()

    def _delete(self):
        mid = self._current_id()
        if mid:
            self.checkpoint.emit()
            self.project.delete_marker(mid)
            self.changed.emit()
            self.refresh()

    def _copy(self):
        QGuiApplication.clipboard().setText(chapters_text(self.project))

    def _menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            return
        self.tree.setCurrentItem(it)
        m = QMenu(self)
        m.addAction("Vai", lambda: self._goto(it))
        m.addAction("Rinomina…", self._rename)
        cm = m.addMenu("Colore")
        for c, lab in zip(MARKER_COLORS, COLOR_NAMES):
            cm.addAction(QPixmap(0, 0), lab, lambda cc=c: self._color(cc))
        m.addAction("Trasforma in indicatore" if self.project.is_chapter(self.project.marker(
            self._current_id() or "") or {}) else "Trasforma in capitolo", self._toggle_kind)
        m.addAction("Elimina", self._delete)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _color(self, c):
        mk = self.project.marker(self._current_id() or "")
        if mk:
            self.checkpoint.emit()
            mk["color"] = c
            self.changed.emit()
            self.refresh()
