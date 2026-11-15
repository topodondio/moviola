"""Schermata dei progetti: quelli nella cartella dei progetti (anche su un disco
esterno) e quelli aperti di recente. Nuovo, apri, duplica, rinomina, cestino."""
from __future__ import annotations

import datetime
import json
import os
import shutil

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .. import media
from .. import speed as spd

EXT = ".moviola"


def _scan(folder, depth=2):
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    base_depth = folder.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "Moviola Cache"]
        if root.count(os.sep) - base_depth >= depth:
            dirs[:] = []
        out += [os.path.join(root, f) for f in files if f.endswith(EXT) and not f.startswith("._")]
    return out


def _project_info(path):
    """(testo, miniatura o None, file mancanti) di un progetto."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return "progetto non leggibile", None, 0
    base = os.path.dirname(os.path.abspath(path))
    srcs = data.get("sources", [])
    missing = 0
    thumb = None
    for s in srcs:
        p = s.get("path", "")
        if not os.path.exists(p) and s.get("rel"):
            p = os.path.normpath(os.path.join(base, *s["rel"].split("/")))
        if not os.path.exists(p):
            missing += 1
            continue
        if thumb is None and s.get("has_video"):
            t = media.thumb_path(p)
            if os.path.exists(t):
                thumb = t
    end = 0.0
    for it in data.get("items", []):
        try:
            end = max(end, it["start"] + spd.item_dur(it))
        except Exception:
            pass
    m, sec = divmod(int(end), 60)
    st = data.get("settings", {})
    txt = f"{len(srcs)} clip · montaggio {m}:{sec:02d} · {st.get('width', '?')}×{st.get('height', '?')} " \
          f"{float(st.get('fps', 25)):g} fps"
    return txt, thumb, missing


_LOGO = {}


def _logo_pixmap(w=112, h=63):
    """Logo di Moviola al centro di un riquadro 16:9 scuro (uguale per tutti i progetti)."""
    if (w, h) not in _LOGO:
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QPainter

        pm = QPixmap(w, h)
        pm.fill(QColor(30, 31, 33))
        logo = QPixmap(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "risorse", "icona.png"))
        if not logo.isNull():
            logo = logo.scaled(h - 8, h - 8, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p = QPainter(pm)
            p.drawPixmap(QPoint((w - logo.width()) // 2, (h - logo.height()) // 2), logo)
            p.end()
        _LOGO[(w, h)] = pm
    return _LOGO[(w, h)]


class ProjectsPanel(QWidget):
    """Scheda PROGETTI. Emette chosen(tipo, percorso): ('open', percorso),
    ('new', percorso da creare), ('browse', '')."""

    chosen = Signal(str, str)
    changed = Signal()  # cartella, rinomine, eliminazioni: impostazioni da salvare

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.current = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 24, 40, 20)
        head = QHBoxLayout()
        title = QLabel("Moviola")
        title.setStyleSheet("font-size: 26px; font-weight: 700; color: #e8a33d;")
        head.addWidget(title)
        head.addStretch(1)
        self.folder_lab = QLabel()
        self.folder_lab.setStyleSheet("color:#9a9a9a;")
        head.addWidget(self.folder_lab)
        b = QPushButton("Cartella…")
        b.setToolTip("Scegli la cartella dei progetti (ad esempio sul disco esterno)")
        b.clicked.connect(self._pick_folder)
        head.addWidget(b)
        lay.addLayout(head)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background:#3a2a1a; color:#f0c080; padding:6px 8px; border-radius:4px;")
        self.banner.hide()
        lay.addWidget(self.banner)
        self.list = QListWidget()
        self.list.setIconSize(QSize(112, 63))
        self.list.setStyleSheet("QListWidget::item { padding: 6px; border-bottom: 1px solid #2a2b2e; }")
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        b_new = QPushButton("+ Nuovo progetto")
        b_browse = QPushButton("Apri altro…")
        self.b_dup = QPushButton("Duplica")
        self.b_ren = QPushButton("Rinomina")
        self.b_del = QPushButton("Elimina")
        self.b_arch = QPushButton("Archivia…")
        self.b_arch.setToolTip("Crea una cartella con il progetto e i video usati, da conservare o spostare")
        self.b_show = QPushButton("Mostra")
        self.b_show.setToolTip("Mostra il file nel Finder / in Esplora risorse")
        self.b_open = QPushButton("Apri")
        self.b_open.setDefault(True)
        for w in (b_new, b_browse, self.b_dup, self.b_ren, self.b_del, self.b_arch, self.b_show):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.b_open)
        lay.addLayout(row)
        b_new.clicked.connect(self._new)
        b_browse.clicked.connect(lambda: self._done(("browse", "")))
        self.b_open.clicked.connect(self._open_current)
        self.b_dup.clicked.connect(self._duplicate)
        self.b_ren.clicked.connect(self._rename)
        self.b_del.clicked.connect(self._delete)
        self.b_show.clicked.connect(self._show)
        self.b_arch.clicked.connect(self._archive)
        self.list.itemDoubleClicked.connect(lambda it: self._open_item(it))
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())
        self._fill()

    # ------------------------------------------------------------ elenco
    @property
    def folder(self):
        return self.cfg.get("projects_dir") or ""

    def _fill(self, select=None):
        self.list.clear()
        folder = self.folder
        self.folder_lab.setText(os.path.basename(folder.rstrip("/\\")) if folder else "nessuna cartella scelta")
        self.folder_lab.setToolTip(folder)
        reachable = bool(folder) and os.path.isdir(folder)
        if folder and not reachable:
            self.banner.setText("La cartella dei progetti non è raggiungibile: se è sul disco esterno, "
                                "collegalo e riapri questa finestra. Intanto vedi i progetti recenti.")
            self.banner.show()
        elif not folder:
            self.banner.setText("Scegli una cartella dei progetti («Cartella…»), ad esempio sul disco esterno "
                                "dove tieni i video: qui vedrai tutti i progetti che contiene.")
            self.banner.show()
        else:
            self.banner.hide()
        in_folder = sorted(_scan(folder), key=lambda p: -os.path.getmtime(p)) if reachable else []
        seen = {os.path.abspath(p) for p in in_folder}
        recent = [p for p in self.cfg.get("recent", []) if os.path.exists(p) and os.path.abspath(p) not in seen]
        if in_folder:
            self._section("NELLA CARTELLA DEI PROGETTI")
            for p in in_folder:
                self._add(p)
        if recent:
            self._section("APERTI DI RECENTE (ALTROVE)")
            for p in recent:
                self._add(p)
        if not in_folder and not recent:
            it = QListWidgetItem("Ancora nessun progetto: crea un nuovo progetto.")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
        target = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            p = it.data(Qt.UserRole)
            if p and (target is None or (select and os.path.abspath(p) == os.path.abspath(select))):
                target = it
                if not select:
                    break
        if target is not None:
            self.list.setCurrentItem(target)
        self._update_buttons()

    def _section(self, text):
        it = QListWidgetItem(text)
        it.setFlags(Qt.NoItemFlags)
        it.setForeground(QColor(232, 163, 61))
        f = it.font()
        f.setBold(True)
        it.setFont(f)
        self.list.addItem(it)

    def _add(self, p):
        info, thumb, missing = _project_info(p)
        when = datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%d/%m/%Y %H:%M")
        name = os.path.splitext(os.path.basename(p))[0]
        is_open = self.current and os.path.abspath(p) == self.current
        warn = f"\n⚠ {missing} file non trovati (disco scollegato?)" if missing else ""
        arch = self.cfg.get("archived", {}).get(os.path.abspath(p))
        tag = f"\n📦 archiviato il {arch['date']} in {arch['path']}" if arch else ""
        it = QListWidgetItem(f"{name}{'  (aperto)' if is_open else ''}\n{info}\nmodificato il {when}{warn}{tag}")
        it.setData(Qt.UserRole, p)
        it.setToolTip(p)
        it.setIcon(QIcon(_logo_pixmap()))   # in ogni progetto il logo di Moviola, non un fotogramma
        f = it.font()
        f.setPointSizeF(f.pointSizeF() + 0.5)
        it.setFont(f)
        self.list.addItem(it)

    def _sel_path(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def _update_buttons(self):
        p = self._sel_path()
        is_open = bool(p) and self.current == os.path.abspath(p)
        for b in (self.b_open, self.b_dup, self.b_show, self.b_arch):
            b.setEnabled(bool(p))
        for b in (self.b_ren, self.b_del):
            b.setEnabled(bool(p) and not is_open)
            b.setToolTip("Non si può mentre il progetto è aperto" if is_open else "")

    # ------------------------------------------------------------ azioni
    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Cartella dei progetti", self.folder or os.path.expanduser("~"))
        if d:
            self.cfg["projects_dir"] = d
            self.changed.emit()
            self._fill()

    def _ask_name(self, title, default):
        name, ok = QInputDialog.getText(self, title, "Nome del progetto:", text=default)
        name = "".join(ch for ch in (name or "") if ch not in '/\\:*?"<>|').strip()
        return name if ok and name else None

    def _new(self):
        folder = self.folder if self.folder and os.path.isdir(self.folder) else ""
        if not folder:
            d = QFileDialog.getExistingDirectory(self, "Dove creare il progetto (cartella dei progetti)",
                                                 os.path.expanduser("~"))
            if not d:
                return
            self.cfg["projects_dir"] = folder = d
            self.changed.emit()
        name = self._ask_name("Nuovo progetto", datetime.date.today().strftime("Montaggio %Y-%m-%d"))
        if not name:
            return
        path = os.path.join(folder, name + EXT)
        if os.path.exists(path):
            QMessageBox.warning(self, "Nuovo progetto", "Esiste già un progetto con questo nome.")
            return
        self._done(("new", path))

    def _open_item(self, it):
        p = it.data(Qt.UserRole)
        if p:
            self._done(("open", p))

    def _open_current(self):
        it = self.list.currentItem()
        if it is not None:
            self._open_item(it)

    def _duplicate(self):
        p = self._sel_path()
        if not p:
            return
        base = os.path.splitext(os.path.basename(p))[0]
        name = self._ask_name("Duplica progetto", base + " copia")
        if not name:
            return
        dst = os.path.join(os.path.dirname(p), name + EXT)
        if os.path.exists(dst):
            QMessageBox.warning(self, "Duplica", "Esiste già un progetto con questo nome.")
            return
        shutil.copy2(p, dst)
        self._fill(select=dst)

    def _rename(self):
        p = self._sel_path()
        if not p:
            return
        name = self._ask_name("Rinomina progetto", os.path.splitext(os.path.basename(p))[0])
        if not name:
            return
        dst = os.path.join(os.path.dirname(p), name + EXT)
        if os.path.exists(dst):
            QMessageBox.warning(self, "Rinomina", "Esiste già un progetto con questo nome.")
            return
        os.rename(p, dst)
        self.cfg["recent"] = [dst if os.path.abspath(r) == os.path.abspath(p) else r
                              for r in self.cfg.get("recent", [])]
        self.changed.emit()
        self._fill(select=dst)

    def _delete(self):
        from PySide6.QtCore import QFile

        p = self._sel_path()
        if not p:
            return
        name = os.path.basename(p)
        if QMessageBox.question(self, "Elimina progetto",
                                f"Spostare «{name}» nel Cestino?\n\nI video originali non vengono toccati.") \
                != QMessageBox.Yes:
            return
        if not QFile.moveToTrash(p):
            QMessageBox.warning(self, "Elimina", "Non sono riuscito a spostarlo nel Cestino.")
            return
        self.cfg["recent"] = [r for r in self.cfg.get("recent", []) if os.path.abspath(r) != os.path.abspath(p)]
        self.changed.emit()
        self._fill()

    def _archive(self):
        import time

        from .archive_dialog import ArchiveDialog
        p = self._sel_path()
        if not p:
            return
        dlg = ArchiveDialog(p, self.current == os.path.abspath(p), self)
        if dlg.exec() and dlg.result_path:
            self.cfg.setdefault("archived", {})[os.path.abspath(p)] = {
                "path": dlg.result_path, "date": time.strftime("%d/%m/%Y")}
            self.changed.emit()
            self._fill(select=p)
            QMessageBox.information(self, "Archivia", "Archivio creato:\n" + dlg.result_path)
            from .export_page import _reveal
            _reveal(dlg.result_path)

    def _show(self):
        p = self._sel_path()
        if p:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))

    def set_current(self, path):
        self.current = os.path.abspath(path) if path else None

    def refresh(self):
        sel = self._sel_path()
        self._fill(select=sel)

    def _done(self, choice):
        self.chosen.emit(choice[0], choice[1] or "")
