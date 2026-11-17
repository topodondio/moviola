"""Scheda COLORE: monitor grande, striscia dei pezzi della timeline e controlli fotografici."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                               QSplitter, QVBoxLayout, QWidget)

from .. import color

TILE_W, TILE_H, GAP = 132, 74, 8
ACCENT = QColor(232, 163, 61)


class _StripBody(QWidget):
    clicked = Signal(str, object, float)   # id del pezzo, modificatori, punto nel pezzo (0..1)
    context = Signal(str, object)     # id, posizione globale
    hovered = Signal(str, float)      # id del pezzo, punto nel pezzo (0..1)
    left = Signal()

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.items = []
        self.setMouseTracking(True)
        self.setFixedHeight(TILE_H + 34)
        self._hov = None            # (indice del pezzo, x)

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
        if self._hov is not None and self._hov[0] < len(self.items):
            r = self._rect(self._hov[0])
            x = min(max(self._hov[1], r.left()), r.right())
            p.setPen(QPen(QColor(255, 255, 255, 230), 1.5))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        p.end()

    def _frac(self, n, x):
        r = self._rect(n)
        return min(1.0, max(0.0, (x - r.left()) / r.width()))

    def mouseMoveEvent(self, e):
        n = self._hit(e.position())
        if n is None:
            self.leaveEvent(None)
            return
        self._hov = (n, e.position().x())
        self.update()
        self.hovered.emit(self.items[n]["id"], self._frac(n, e.position().x()))

    def leaveEvent(self, e):
        if self._hov is not None:
            self._hov = None
            self.update()
            self.left.emit()

    def mousePressEvent(self, e):
        n = self._hit(e.position())
        if n is None:
            return
        it = self.items[n]
        if e.button() == Qt.RightButton:
            self.context.emit(it["id"], e.globalPosition().toPoint())
        elif e.button() == Qt.LeftButton:
            self.clicked.emit(it["id"], e.modifiers(), self._frac(n, e.position().x()))


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
        self.body.hovered.connect(self._hover)
        self.body.left.connect(self._hover_end)
        self._restore = None
        self._pending = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(35)
        self._hover_timer.timeout.connect(self._hover_flush)
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

    def _time_at(self, iid, frac):
        it = self.host.project.item(iid)
        if it is None:
            return None
        d = self.host.project.item_dur(it)
        return it["start"] + min(frac * d, max(0.0, d - 0.04))

    def _hover(self, iid, frac):
        """Passando sopra a un pezzo il monitor mostra quel punto della timeline (la testina torna dov'era
        quando il mouse esce)."""
        if self.host.engine.playing:
            return
        if self._restore is None:
            self._restore = self.host.engine.position
        self._pending = self._time_at(iid, frac)
        if not self._hover_timer.isActive():
            self._hover_timer.start()

    def _hover_flush(self):
        if self._pending is not None and self._restore is not None:
            self.host._timeline_seek(self._pending)

    def _hover_end(self):
        self._hover_timer.stop()
        self._pending = None
        if self._restore is not None and not self.host.engine.playing:
            self.host._timeline_seek(self._restore)
        self._restore = None

    def _clicked(self, iid, mods, frac=0.0):
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
        self._hover_timer.stop()
        self._restore = None            # il clic fissa la testina nel punto scelto
        if it is not None and (len(sel) == 1 or iid in sel):
            self.host._timeline_seek(self._time_at(iid, frac))
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
        m.addSeparator()
        m.addAction("Mostra in Montaggio", lambda: self.action.emit("montage", iid))
        m.addAction("Mostra nella clip", lambda: self.action.emit("clip", iid))
        m.exec(pos)


class ParadeView(QWidget):
    """Parade RGB sotto il monitor. Con un riferimento scelto è divisa: a sinistra il riferimento,
    a destra la clip."""

    def __init__(self):
        super().__init__()
        self.clip = None
        self.ref = None
        self.ref_name = ""
        self.note = ""
        self.setFixedHeight(132)

    def set_data(self, clip, ref, ref_name, note):
        self.clip, self.ref, self.ref_name, self.note = clip, ref, ref_name, note
        self.update()

    @staticmethod
    def _qimg(a):
        import numpy as np
        a = np.ascontiguousarray(a)
        h, w = a.shape[:2]
        return QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(14, 15, 16))
        top, pad = 16, 4
        panels = ([(self.ref, f"RIFERIMENTO · {self.ref_name}")] if self.ref is not None else []) + \
                 [(self.clip, "CLIP")]
        pw = self.width() / len(panels)
        f = QFont(self.font())
        f.setPointSizeF(max(8.0, f.pointSizeF() - 1))
        p.setFont(f)
        for k, (arr, title) in enumerate(panels):
            r = QRectF(k * pw + pad, top, pw - 2 * pad, self.height() - top - pad)
            if arr is not None:
                p.drawImage(r, self._qimg(arr))
            p.setPen(QPen(QColor(255, 255, 255, 38), 1))
            for q in (0.0, 0.25, 0.5, 0.75, 1.0):
                y = r.top() + q * r.height()
                p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            for q in (1 / 3, 2 / 3):
                x = r.left() + q * r.width()
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QColor(150, 150, 150))
            p.drawText(QRectF(r.left(), 0, r.width(), top), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(title, Qt.ElideRight, int(r.width() * 0.6)))
            if k == len(panels) - 1 and self.note:
                p.setPen(ACCENT)
                p.drawText(QRectF(r.left(), 0, r.width(), top), Qt.AlignRight | Qt.AlignVCenter, self.note)
            for i, col in enumerate((QColor(255, 90, 90), QColor(90, 220, 110), QColor(100, 150, 255))):
                p.setPen(col)
                p.drawText(QRectF(r.left() + i * r.width() / 3 + 3, r.bottom() - 14, 14, 14), Qt.AlignCenter,
                           "RGB"[i])
        p.end()


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
        self.parade = ParadeView()
        self.parade.hide()
        lv.addWidget(self.parade)
        lv.addLayout(self._build_compare_row())
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

    def _build_compare_row(self):
        host = self.host
        row = QHBoxLayout()
        row.setSpacing(6)
        self.b_fix = QPushButton("Fissa riferimento")
        self.b_fix.setFocusPolicy(Qt.NoFocus)
        self.b_fix.setToolTip("Salva il fotogramma che vedi (con la sua correzione) per confrontarlo con altre clip.\n"
                              "Ctrl/Cmd+Maiusc+R")
        self.b_fix.clicked.connect(host.ref_capture)
        row.addWidget(self.b_fix)
        self.ref_combo = QComboBox()
        self.ref_combo.setMinimumWidth(150)
        self.ref_combo.setFocusPolicy(Qt.NoFocus)
        self.ref_combo.setToolTip("I riferimenti salvati in questo progetto")
        self.ref_combo.currentIndexChanged.connect(lambda _i: host._apply_compare())
        row.addWidget(self.ref_combo)
        more = QPushButton("Gestisci…")
        more.setToolTip("Rinomina o elimina il riferimento scelto")
        more.setFocusPolicy(Qt.NoFocus)
        mm = QMenu(more)
        mm.addAction("Rinomina…", host.ref_rename)
        mm.addAction("Elimina", host.ref_delete)
        more.setMenu(mm)
        row.addWidget(more)
        row.addSpacing(10)
        row.addWidget(QLabel("Confronto"))
        self.cmp_group = QButtonGroup(self)
        seg = ("QPushButton { border:1px solid #4a4c50; background:#2a2b2e; color:#a8a8a8; padding:5px 9px; %s }"
               "QPushButton:checked { background:#e8a33d; color:#1b1b1b; border-color:#e8a33d; font-weight:700; }")
        shape = ("border-top-left-radius:5px; border-bottom-left-radius:5px;", "border-radius:0; border-left:none;",
                 "border-top-right-radius:5px; border-bottom-right-radius:5px; border-left:none;")
        for i, (key, lab, tip) in enumerate(((None, "Spento", "Solo la clip"),
                                             ("side", "Affiancato", "Riferimento a sinistra, clip a destra"),
                                             ("wipe", "Tendina", "Trascina la linea sul monitor per scoprire una parte "
                                                                  "dell'altra immagine"))):
            b = QPushButton(lab)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setStyleSheet(seg % shape[i])
            b.setToolTip(tip + "  (tasto W: passa da uno all'altro)")
            b.setProperty("mode", key)
            self.cmp_group.addButton(b, i)
            row.addWidget(b)
        self.cmp_group.button(0).setChecked(True)
        self.cmp_group.idClicked.connect(lambda _i: host._apply_compare())
        self.flash_btn = QPushButton("Lampeggio")
        self.flash_btn.setFocusPolicy(Qt.NoFocus)
        self.flash_btn.setToolTip("Tieni premuto (o tieni premuto il tasto Q) per vedere il riferimento a tutto "
                                  "schermo; rilascia per tornare alla clip")
        self.flash_btn.pressed.connect(lambda: host.ref_flash(True))
        self.flash_btn.released.connect(lambda: host.ref_flash(False))
        row.addWidget(self.flash_btn)
        row.addStretch(1)
        self.parade_btn = QPushButton("Parade RGB")
        self.parade_btn.setCheckable(True)
        self.parade_btn.setFocusPolicy(Qt.NoFocus)
        self.parade_btn.setToolTip("Grafico dei tre canali rosso, verde e blu (tasto P). Con un riferimento "
                                   "scelto in confronto è diviso: riferimento e clip.")
        self.parade_btn.setChecked(bool(host.cfg.get("parade", False)))
        self.parade_btn.toggled.connect(host.set_parade)
        row.addWidget(self.parade_btn)
        return row

    def compare_mode(self):
        b = self.cmp_group.checkedButton()
        return b.property("mode") if b else None

    def set_compare_button(self, mode):
        for b in self.cmp_group.buttons():
            if b.property("mode") == mode:
                b.setChecked(True)

    def current_ref_id(self):
        return self.ref_combo.currentData()

    def fill_refs(self, select=None):
        keep = select or self.ref_combo.currentData()
        self.ref_combo.blockSignals(True)
        self.ref_combo.clear()
        for r in self.host.project.data.get("references", []):
            self.ref_combo.addItem(r["name"] + (f" · {r['origin']}" if r.get("origin") else ""), r["id"])
        if self.ref_combo.count() == 0:
            self.ref_combo.addItem("(nessun riferimento)", None)
        i = self.ref_combo.findData(keep)
        self.ref_combo.setCurrentIndex(max(0, i))
        self.ref_combo.blockSignals(False)

    def step(self, d):
        items = self.strip.items()
        if not items:
            return
        t = self.host.playhead_time()
        cur = max((n for n, i in enumerate(items) if i["start"] <= t + 1e-6), default=0)
        n = max(0, min(len(items) - 1, cur + d))
        self.strip._clicked(items[n]["id"], Qt.NoModifier)

    def _strip_action(self, name, iid):
        if name == "montage":
            return self.host.show_item_in_montage(iid)
        if name == "clip":
            return self.host.show_item_in_clip(iid)
        {"copy": self.host.copy_grade, "paste": self.host.paste_grade, "reset": self.host.reset_grade}[name]()
        self.strip.repaint_tiles()

    def sizeHint(self):
        return QSize(1200, 700)
