"""Scheda COLORE: monitor grande, striscia dei pezzi della timeline e controlli fotografici."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget

from .. import color

TILE_W, TILE_H, GAP = 132, 74, 8
ACCENT = QColor(232, 163, 61)


class _StripBody(QWidget):
    clicked = Signal(str, object)     # id del pezzo, modificatori
    context = Signal(str, object)     # id, posizione globale

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.items = []
        self.setMouseTracking(True)
        self.setFixedHeight(TILE_H + 34)

    def set_items(self, items):
        self.items = items
        self.setFixedWidth(max(10, len(items) * (TILE_W + GAP) + GAP))
        self.update()

    def _rect(self, n):
        return QRectF(GAP + n * (TILE_W + GAP), 6, TILE_W, TILE_H)

    def _hit(self, pos):
        n = int((pos.x() - GAP / 2) // (TILE_W + GAP))
        if 0 <= n < len(self.items) and self._rect(n).adjusted(-GAP / 2, 0, GAP / 2, 40).contains(pos):
            return n
        return None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        sel = self.host.timeline.view.selection
        pr = self.host.project
        f = QFont(self.font())
        f.setPointSizeF(max(8.0, f.pointSizeF() - 1))
        p.setFont(f)
        for n, it in enumerate(self.items):
            r = self._rect(n)
            pm = self.host.thumb_for(it.get("origin")) or self.host.thumb_for(it["source"])
            p.fillRect(r, QColor(24, 25, 27))
            if pm is not None:
                s = pm.scaled(int(r.width()), int(r.height()), Qt.KeepAspectRatioByExpanding,
                              Qt.SmoothTransformation)
                p.drawPixmap(r.toRect(), s, QRectF((s.width() - r.width()) / 2, (s.height() - r.height()) / 2,
                                                   r.width(), r.height()).toRect())
            on = it["id"] in sel
            p.setPen(QPen(ACCENT if on else QColor(60, 62, 66), 2.5 if on else 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            # pastiglie: numero, correzione del pezzo / della clip
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(QRectF(r.left() + 4, r.top() + 4, 22, 15), 3, 3)
            p.setPen(QColor(235, 235, 235))
            p.drawText(QRectF(r.left() + 4, r.top() + 4, 22, 15), Qt.AlignCenter, str(n + 1))
            src = pr.source(it["source"]) or {}
            dots = []
            if not color.is_identity(color.normalize(it.get("grade"))):
                dots.append(ACCENT)
            if not color.is_identity(color.normalize(src.get("grade"))):
                dots.append(QColor(110, 190, 255))
            for k, c in enumerate(dots):
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawEllipse(QPointF(r.right() - 10 - k * 13, r.top() + 11), 4.5, 4.5)
            p.setPen(QColor(200, 200, 200) if on else QColor(150, 150, 150))
            name = it.get("origin") or it["source"]
            fm = p.fontMetrics()
            p.drawText(QRectF(r.left(), r.bottom() + 3, r.width(), 14), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(name, Qt.ElideRight, int(r.width())))
            p.setPen(QColor(120, 120, 120))
            p.drawText(QRectF(r.left(), r.bottom() + 16, r.width(), 14), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{pr.item_dur(it):.1f} s")
        p.end()

    def mousePressEvent(self, e):
        n = self._hit(e.position())
        if n is None:
            return
        it = self.items[n]
        if e.button() == Qt.RightButton:
            self.context.emit(it["id"], e.globalPosition().toPoint())
        elif e.button() == Qt.LeftButton:
            self.clicked.emit(it["id"], e.modifiers())


class ClipStrip(QScrollArea):
    """Striscia dei pezzi video della timeline, in ordine di tempo."""

    action = Signal(str, str)         # nome azione, id del pezzo

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.body = _StripBody(host)
        self.setWidget(self.body)
        self.setWidgetResizable(False)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(TILE_H + 34 + 16)
        self.body.clicked.connect(self._clicked)
        self.body.context.connect(self._context)
        self._last = None

    def items(self):
        pr = self.host.project
        return sorted((i for i in pr.items if i["kind"] == "video" and pr.source(i.get("source", ""))),
                      key=lambda i: (i["start"], i["track"]))

    def refresh(self):
        self.body.set_items(self.items())
        self._scroll_to_selection()

    def repaint_tiles(self):
        self.body.update()

    def _scroll_to_selection(self):
        sel = self.host.timeline.view.selection
        for n, it in enumerate(self.body.items):
            if it["id"] in sel:
                self.ensureVisible(int(self.body._rect(n).center().x()), 20, TILE_W, 10)
                break

    def _clicked(self, iid, mods):
        view = self.host.timeline.view
        ids = [i["id"] for i in self.body.items]
        sel = set(view.selection)
        if mods & Qt.ShiftModifier and self._last in ids and iid in ids:
            a, b = sorted((ids.index(self._last), ids.index(iid)))
            sel = set(ids[a:b + 1])
        elif mods & (Qt.ControlModifier | Qt.MetaModifier):
            sel = sel ^ {iid}
        else:
            sel = {iid}
        self._last = iid
        self.host.active_panel = "timeline"
        view.set_selection(sel)
        it = self.host.project.item(iid)
        if it is not None and (len(sel) == 1 or iid in sel):
            self.host._timeline_seek(it["start"] + min(0.1, self.host.project.item_dur(it) / 2))
        self.body.update()

    def _context(self, iid, pos):
        view = self.host.timeline.view
        if iid not in view.selection:
            view.set_selection({iid})
        n = len(view.selection)
        m = QMenu(self)
        m.addAction("Copia la correzione", lambda: self.action.emit("copy", iid))
        a = m.addAction(f"Incolla su {n} selezionati" if n > 1 else "Incolla la correzione",
                        lambda: self.action.emit("paste", iid))
        a.setEnabled(self.host.grade_clip is not None)
        m.addAction("Azzera la correzione", lambda: self.action.emit("reset", iid))
        m.exec(pos)


class ColorPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 4)
        lay.setSpacing(4)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)
        t = QLabel("MONITOR")
        t.setStyleSheet("color:#8d8d8d; font-weight:700; letter-spacing:2px; padding:0 0 2px 2px;")
        lv.addWidget(t)
        self.view_holder = QVBoxLayout()
        self.view_holder.setContentsMargins(0, 0, 0, 0)
        lv.addLayout(self.view_holder, 1)
        self.info = QLabel("")
        self.info.setStyleSheet("color:#9a9a9a;")
        lv.addWidget(self.info)
        tr = QHBoxLayout()
        tr.setSpacing(4)

        def tb(text, tip, slot):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(slot)
            tr.addWidget(b)
            return b

        e = host.engine
        tb("⏮", "Inizio (Home)", lambda: e.seek(0))
        tb("◀", "Pezzo precedente (↑)", lambda: self.step(-1))
        self.play_btn = tb("▶", "Play / pausa (Spazio)", e.toggle)
        self.play_btn.setFixedWidth(46)
        tb("▶", "Pezzo successivo (↓)", lambda: self.step(1)).setText("▶|")
        tr.addSpacing(10)
        self.tc_label = QLabel("00:00:00:00")
        self.tc_label.setObjectName("timecode")
        tr.addWidget(self.tc_label)
        tr.addStretch(1)
        lv.addLayout(tr)
        self.strip = ClipStrip(host)
        lv.addWidget(self.strip)
        split.addWidget(left)
        split.addWidget(host.color_panel)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([1000, 420])
        host.color_panel.setMinimumWidth(340)
        host.color_panel.setMaximumWidth(560)
        self.split = split
        lay.addWidget(split, 1)
        self.strip.action.connect(self._strip_action)

    def step(self, d):
        items = self.strip.items()
        if not items:
            return
        t = self.host.playhead_time()
        cur = max((n for n, i in enumerate(items) if i["start"] <= t + 1e-6), default=0)
        n = max(0, min(len(items) - 1, cur + d))
        self.strip._clicked(items[n]["id"], Qt.NoModifier)

    def _strip_action(self, name, _iid):
        {"copy": self.host.copy_grade, "paste": self.host.paste_grade, "reset": self.host.reset_grade}[name]()
        self.strip.repaint_tiles()

    def sizeHint(self):
        return QSize(1200, 700)
