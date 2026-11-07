"""Schermata iniziale: progetti recenti, nuovo progetto, apri."""
from __future__ import annotations

import datetime
import json
import os

from PySide6.QtCore import QSize, Qt
from .. import speed as spd
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout)


def _project_info(path):
    """Breve descrizione di un progetto: numero di clip e durata del montaggio."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        n = len(data.get("sources", []))
        end = 0.0
        for it in data.get("items", []):
            d = (it["src_out"] - it["src_in"]) if it.get("kind") in ("text", "shape") else spd.item_dur(it)
            end = max(end, it["start"] + d)
        m, s = divmod(int(end), 60)
        st = data.get("settings", {})
        return f"{n} clip · montaggio {m}:{s:02d} · {st.get('width', '?')}×{st.get('height', '?')} " \
               f"{float(st.get('fps', 25)):g} fps"
    except Exception:
        return ""


class WelcomeDialog(QDialog):
    """Al termine: self.choice è ('open', percorso), ('new', None) o ('browse', None)."""

    def __init__(self, recent, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Moviola")
        self.setMinimumSize(620, 460)
        self.choice = ("new", None)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        title = QLabel("Moviola")
        title.setStyleSheet("font-size: 26px; font-weight: 700; color: #e8a33d;")
        lay.addWidget(title)
        sub = QLabel("Progetti recenti")
        sub.setStyleSheet("font-size: 14px; color: #bbb; margin-top: 6px;")
        lay.addWidget(sub)
        self.list = QListWidget()
        self.list.setIconSize(QSize(1, 1))
        self.list.setStyleSheet("QListWidget::item { padding: 8px 6px; border-bottom: 1px solid #2a2b2e; }")
        lay.addWidget(self.list, 1)
        shown = 0
        for p in recent:
            if not os.path.exists(p):
                continue
            when = datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%d/%m/%Y %H:%M")
            name = os.path.splitext(os.path.basename(p))[0]
            info = _project_info(p)
            it = QListWidgetItem(f"{name}\n{info}\n{os.path.dirname(p)}  ·  modificato il {when}")
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            f = it.font()
            f.setPointSizeF(f.pointSizeF() + 0.5)
            it.setFont(f)
            self.list.addItem(it)
            shown += 1
        if shown == 0:
            empty = QListWidgetItem("Nessun progetto recente: crea un nuovo progetto.")
            empty.setFlags(Qt.NoItemFlags)
            self.list.addItem(empty)
        else:
            self.list.setCurrentRow(0)
        row = QHBoxLayout()
        b_new = QPushButton("+ Nuovo progetto")
        b_browse = QPushButton("Apri altro progetto…")
        self.b_open = QPushButton("Apri")
        self.b_open.setDefault(True)
        self.b_open.setEnabled(shown > 0)
        row.addWidget(b_new)
        row.addWidget(b_browse)
        row.addStretch(1)
        row.addWidget(self.b_open)
        lay.addLayout(row)
        b_new.clicked.connect(lambda: self._done(("new", None)))
        b_browse.clicked.connect(lambda: self._done(("browse", None)))
        self.b_open.clicked.connect(self._open_current)
        self.list.itemDoubleClicked.connect(lambda it: self._open_item(it))

    def _open_item(self, it):
        p = it.data(Qt.UserRole)
        if p:
            self._done(("open", p))

    def _open_current(self):
        it = self.list.currentItem()
        if it is not None:
            self._open_item(it)

    def _done(self, choice):
        self.choice = choice
        self.accept()
