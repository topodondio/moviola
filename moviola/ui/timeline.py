"""Timeline: tracce video (in alto) e audio (in basso).

Trascina un pezzo per spostarlo (anche su un'altra traccia), i bordi per
allungarlo o accorciarlo. Alt mentre trascini = muovi solo video o solo
audio. Ctrl/Cmd+clic = selezione multipla. Rotella = scorri, Ctrl/Cmd+rotella
= zoom.
"""
from __future__ import annotations

import json

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QPushButton, QScrollBar, QSlider, QVBoxLayout,
                               QWidget)

from .. import color
from .bin_panel import MIME

HEAD = 92
RULER = 26
VH = 52
AH = 44
GAP = 8
EDGE = 7

C_BG = QColor(26, 27, 29)
C_ROW = QColor(33, 34, 37)
C_ROW2 = QColor(30, 31, 34)
C_HEAD = QColor(38, 40, 43)
C_VIDEO = QColor(70, 98, 138)
C_AUDIO = QColor(52, 112, 98)
C_SEL = QColor(232, 163, 61)
C_PLAY = QColor(235, 70, 60)


def tc(t, fps):
    fps_i = max(1, int(round(fps)))
    f = int(round(t * fps))
    s, fr = divmod(f, fps_i)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{fr:02d}"


def mod_multi(e):
    m = e.modifiers()
    return bool(m & Qt.ControlModifier) or bool(m & Qt.MetaModifier)


class TimelineView(QWidget):
    seek = Signal(float)
    selection_changed = Signal(list)
    changed = Signal()
    activated = Signal()
    action = Signal(str, list)

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMinimumHeight(RULER + 3 * VH + 3 * AH + GAP)
        self.pps = 60.0
        self.scroll_t = 0.0
        self.scroll_y = 0
        self.playhead = 0.0
        self.selection: set[str] = set()
        self.snapping = True
        self._drag = None
        self._ghost = None
        self._rubber = None
        self._wave_cache = {}

    # ----------------------------------------------------------- geometria
    @property
    def project(self):
        return self.host.project

    def rows(self):
        pr = self.project
        out = []
        y = RULER - self.scroll_y
        nv = len(pr.tracks("video"))
        for k in range(nv - 1, -1, -1):
            out.append({"kind": "video", "track": k, "y": y, "h": VH})
            y += VH
        y += GAP
        for k in range(len(pr.tracks("audio"))):
            out.append({"kind": "audio", "track": k, "y": y, "h": AH})
            y += AH
        return out

    def content_height(self):
        pr = self.project
        return RULER + len(pr.tracks("video")) * VH + GAP + len(pr.tracks("audio")) * AH

    def row_at(self, y):
        for r in self.rows():
            if r["y"] <= y < r["y"] + r["h"]:
                return r
        return None

    def row_of(self, kind, track):
        for r in self.rows():
            if r["kind"] == kind and r["track"] == track:
                return r
        return None

    def x_of(self, t):
        return HEAD + (t - self.scroll_t) * self.pps

    def t_of(self, x):
        return max(0.0, self.scroll_t + (x - HEAD) / self.pps)

    def item_rect(self, it):
        r = self.row_of(it["kind"], it["track"])
        if r is None:
            return QRectF()
        x0 = self.x_of(it["start"])
        x1 = self.x_of(self.project.item_end(it))
        return QRectF(x0, r["y"] + 2, max(2.0, x1 - x0), r["h"] - 4)

    def item_at(self, pos):
        if pos.x() < HEAD or pos.y() < RULER:
            return None
        for it in reversed(self.project.items):
            if self.item_rect(it).contains(pos):
                return it
        return None

    # -------------------------------------------------------------- vista
    def set_playhead(self, t, follow=False):
        old = self.x_of(self.playhead)
        self.playhead = t
        new = self.x_of(t)
        if follow and (new > self.width() - 30 or new < HEAD):
            self.scroll_t = max(0.0, t - (self.width() - HEAD) * 0.1 / self.pps)
            self.host.timeline_scrolled()
            self.update()
            return
        self.update(QRectF(old - 8, 0, 16, self.height()).toRect())
        self.update(QRectF(new - 8, 0, 16, self.height()).toRect())

    def zoom(self, factor, anchor_x=None):
        anchor_x = self.x_of(self.playhead) if anchor_x is None else anchor_x
        ta = self.t_of(anchor_x)
        self.pps = max(2.0, min(800.0, self.pps * factor))
        self.scroll_t = max(0.0, ta - (anchor_x - HEAD) / self.pps)
        self.host.timeline_scrolled()
        self.update()

    def zoom_fit(self):
        end = max(self.project.timeline_end(), 5.0)
        self.pps = max(2.0, min(800.0, (self.width() - HEAD - 30) / end))
        self.scroll_t = 0.0
        self.host.timeline_scrolled()
        self.update()

    def set_selection(self, ids, emit=True):
        self.selection = set(ids)
        self.update()
        if emit:
            self.selection_changed.emit(list(self.selection))

    # ------------------------------------------------------------- disegno
    def paintEvent(self, e):
        p = QPainter(self)
        pr = self.project
        p.fillRect(self.rect(), C_BG)
        fps = pr.fps
        rows = self.rows()
        clip = e.rect()
        # righe
        for i, r in enumerate(rows):
            col = C_ROW if i % 2 == 0 else C_ROW2
            p.fillRect(QRectF(HEAD, r["y"], self.width() - HEAD, r["h"]), col)
        # pezzi
        t_left = self.t_of(HEAD)
        t_right = self.t_of(self.width())
        small = QFont(p.font())
        small.setPointSizeF(8.5)
        p.setFont(small)
        for it in pr.items:
            s = it["start"]
            en = pr.item_end(it)
            if en < t_left or s > t_right:
                continue
            rect = self.item_rect(it)
            if not rect.intersects(QRectF(clip)):
                continue
            self._paint_item(p, it, rect)
        # fantasma di trascinamento dalla lista clip
        if self._ghost:
            p.setPen(QPen(C_SEL, 1, Qt.DashLine))
            p.setBrush(QColor(232, 163, 61, 50))
            for kind, track, a, b in self._ghost:
                r = self.row_of(kind, track)
                if r:
                    p.drawRect(QRectF(self.x_of(a), r["y"] + 2, (b - a) * self.pps, r["h"] - 4))
        if self._rubber:
            p.setPen(QPen(QColor(200, 200, 200), 1, Qt.DashLine))
            p.setBrush(QColor(255, 255, 255, 25))
            p.drawRect(self._rubber)
        # intestazioni tracce
        p.fillRect(QRectF(0, RULER, HEAD, self.height()), C_HEAD)
        f = QFont(p.font())
        f.setPointSizeF(10)
        f.setBold(True)
        for r in rows:
            tr = pr.tracks(r["kind"])[r["track"]]
            p.setPen(QColor(210, 210, 210) if not tr.get("muted") else QColor(110, 110, 110))
            p.setFont(f)
            p.drawText(QRectF(10, r["y"], 40, r["h"]), Qt.AlignVCenter | Qt.AlignLeft, tr["name"])
            box = self._mute_rect(r)
            p.setPen(QPen(QColor(120, 120, 120), 1))
            p.setBrush(QColor(60, 160, 90) if not tr.get("muted") else QColor(60, 60, 60))
            p.drawRoundedRect(box, 3, 3)
            p.setFont(small)
            p.setPen(QColor(235, 235, 235))
            p.drawText(box, Qt.AlignCenter, "ON" if not tr.get("muted") else "OFF")
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.drawLine(0, int(r["y"] + r["h"]), HEAD, int(r["y"] + r["h"]))
        # righello
        p.fillRect(QRectF(0, 0, self.width(), RULER), QColor(22, 23, 25))
        self._paint_ruler(p, fps)
        p.fillRect(QRectF(0, 0, HEAD, RULER), QColor(22, 23, 25))
        p.setFont(small)
        for i, (lab, _) in enumerate((("+V", "addv"), ("+A", "adda"))):
            br = self._add_btn_rect(i)
            p.setPen(QPen(QColor(90, 92, 96), 1))
            p.setBrush(QColor(45, 47, 51))
            p.drawRoundedRect(br, 3, 3)
            p.setPen(QColor(220, 220, 220))
            p.drawText(br, Qt.AlignCenter, lab)
        # testina
        x = self.x_of(self.playhead)
        if x >= HEAD:
            p.setPen(QPen(C_PLAY, 1.5))
            p.drawLine(QPointF(x, RULER - 6), QPointF(x, self.height()))
            p.setBrush(C_PLAY)
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF([QPointF(x - 6, 6), QPointF(x + 6, 6), QPointF(x, RULER - 4)]))
        p.end()

    def _mute_rect(self, r):
        return QRectF(52, r["y"] + r["h"] / 2 - 9, 32, 18)

    def _add_btn_rect(self, i):
        return QRectF(8 + i * 40, 4, 34, RULER - 8)

    def _paint_ruler(self, p, fps):
        px_s = self.pps
        steps = [1 / fps, 2 / fps, 5 / fps, 10 / fps, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
        step = next((s for s in steps if s * px_s >= 70), 600)
        t0 = int(self.scroll_t / step) * step
        t = t0
        p.setPen(QPen(QColor(110, 112, 116), 1))
        f = QFont(p.font())
        f.setPointSizeF(8)
        p.setFont(f)
        while True:
            x = self.x_of(t)
            if x > self.width():
                break
            if x >= HEAD:
                p.drawLine(QPointF(x, RULER - 9), QPointF(x, RULER))
                p.drawText(QPointF(x + 3, 11), tc(t, fps))
                for k in range(1, 5):
                    xs = x + k * step * px_s / 5
                    if HEAD <= xs <= self.width():
                        p.drawLine(QPointF(xs, RULER - 4), QPointF(xs, RULER))
            t += step

    def _paint_item(self, p, it, rect):
        pr = self.project
        sel = it["id"] in self.selection
        base = C_VIDEO if it["kind"] == "video" else C_AUDIO
        src = pr.source(it["source"])
        missing = src is None
        p.save()
        p.setClipRect(rect.intersected(QRectF(HEAD, RULER, self.width() - HEAD, self.height())))
        p.setPen(Qt.NoPen)
        p.setBrush(base if not missing else QColor(120, 50, 50))
        p.drawRoundedRect(rect, 3, 3)
        if it["kind"] == "video":
            pm = self.host.thumb_for(it["source"])
            if pm is not None and rect.width() > 40:
                th = rect.height() - 16
                tw = th * 16 / 9
                p.drawPixmap(QRectF(rect.left() + 2, rect.top() + 14, tw, th).toRect(), pm)
            if not color.is_identity(it.get("grade")):
                p.setBrush(C_SEL)
                p.drawEllipse(QPointF(rect.right() - 8, rect.top() + 7), 3.5, 3.5)
        else:
            self._paint_wave(p, it, rect)
            if abs(it.get("gain_db", 0.0)) > 0.05:
                p.setPen(QColor(230, 230, 230))
                p.drawText(rect.adjusted(0, 1, -5, 0), Qt.AlignRight | Qt.AlignTop, f"{it['gain_db']:+.1f} dB")
        p.setPen(QColor(245, 245, 245))
        lab = it.get("origin") or it["source"]
        if it.get("link") is None and src is not None and src.get("has_audio") and src.get("has_video"):
            lab += "  (scollegato)"
        p.drawText(rect.adjusted(5, 1, -16, 0), Qt.AlignLeft | Qt.AlignTop, lab)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(C_SEL if sel else base.darker(150), 2 if sel else 1))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        p.restore()

    def _paint_wave(self, p, it, rect):
        pk = self.host.peaks_for(it["source"])
        if pk is None or len(pk) == 0:
            return
        x0 = max(rect.left(), HEAD)
        x1 = min(rect.right(), self.width())
        if x1 <= x0:
            return
        mid = rect.center().y() + 5
        amp = (rect.height() - 16) / 2
        n = int(x1 - x0)
        xs = np.arange(n, dtype=np.float32) + x0
        ts = it["src_in"] + (xs - rect.left()) / self.pps
        idx = np.clip((ts * 100).astype(np.int64), 0, len(pk) - 1)
        pkf = np.asarray(pk, dtype=np.float32)
        if 100 / self.pps > 1.5 and n > 1:
            vals = np.maximum.reduceat(pkf, idx)  # massimo tra un pixel e il successivo
            vals[-1] = pkf[idx[-1]]
        else:
            vals = pkf[idx]
        gain = 10 ** (it.get("gain_db", 0.0) / 20)
        vals = np.clip(np.sqrt(vals * gain), 0, 1) * amp
        path = QPainterPath()
        path.moveTo(x0, mid)
        for x, v in zip(xs, vals):
            path.lineTo(float(x), mid - float(v))
        for x, v in zip(xs[::-1], vals[::-1]):
            path.lineTo(float(x), mid + float(v))
        path.closeSubpath()
        p.fillPath(path, QColor(150, 220, 200, 150))

    # ------------------------------------------------------------- mouse
    def _snap(self, t, exclude=(), moving_edges=None):
        """Aggancio ai bordi dei pezzi e alla testina."""
        if not self.snapping:
            return t if moving_edges is None else 0.0
        pts = self.project.snap_points(exclude) + [self.playhead]
        tol = 10 / self.pps
        if moving_edges is None:
            best = min(pts, key=lambda q: abs(q - t), default=t)
            return best if abs(best - t) <= tol else t
        best_d = None
        for e in moving_edges:
            for q in pts:
                d = q - (e + t)
                if abs(d) <= tol and (best_d is None or abs(d) < abs(best_d)):
                    best_d = d
        return best_d or 0.0

    def mousePressEvent(self, e):
        self.activated.emit()
        pos = e.position()
        pr = self.project
        if pos.y() < RULER:
            if pos.x() < HEAD:
                for i, act in enumerate(("add_video_track", "add_audio_track")):
                    if self._add_btn_rect(i).contains(pos):
                        self.action.emit(act, [])
                return
            if e.button() == Qt.LeftButton:
                self._drag = ("scrub",)
                self.seek.emit(pr.snap(self.t_of(pos.x())))
            return
        if pos.x() < HEAD:
            r = self.row_at(pos.y())
            if r is None:
                return
            if e.button() == Qt.RightButton:
                self._header_menu(r, e.globalPosition().toPoint())
            elif self._mute_rect(r).contains(pos):
                pr.checkpoint()
                tr = pr.tracks(r["kind"])[r["track"]]
                tr["muted"] = not tr.get("muted")
                self.changed.emit()
                self.update()
            return
        it = self.item_at(pos)
        alt = bool(e.modifiers() & Qt.AltModifier)
        if it is None:
            if e.button() == Qt.LeftButton:
                if not mod_multi(e):
                    self.set_selection([])
                self._drag = ("rubber", pos, set(self.selection))
            elif e.button() == Qt.RightButton:
                self._empty_menu(e.globalPosition().toPoint(), self.t_of(pos.x()))
            return
        group = {it["id"]} if alt else {i["id"] for i in pr.linked(it)}
        if mod_multi(e):
            if it["id"] in self.selection:
                self.set_selection(self.selection - group)
            else:
                self.set_selection(self.selection | group)
            return
        if it["id"] not in self.selection:
            self.set_selection(group)
        if e.button() == Qt.RightButton:
            self._item_menu(e.globalPosition().toPoint(), self.t_of(pos.x()))
            return
        rect = self.item_rect(it)
        origin = json.dumps(pr.data["items"])
        pr.checkpoint()
        if pos.x() - rect.left() <= EDGE:
            self._drag = ("trim", it["id"], "left", origin, alt)
        elif rect.right() - pos.x() <= EDGE:
            self._drag = ("trim", it["id"], "right", origin, alt)
        else:
            r = self.row_at(pos.y())
            ids = set(self.selection)
            if alt:
                ids = {it["id"]} | {i for i in ids if i != it["id"] and pr.item(i) and pr.item(i)["kind"] == it["kind"]}
            self._drag = ("move", self.t_of(pos.x()), r["kind"], r["track"], ids, origin, alt, 0, 0)

    def mouseMoveEvent(self, e):
        pos = e.position()
        pr = self.project
        if self._drag is None:
            it = self.item_at(pos)
            if it is not None:
                rect = self.item_rect(it)
                if pos.x() - rect.left() <= EDGE or rect.right() - pos.x() <= EDGE:
                    self.setCursor(Qt.SizeHorCursor)
                else:
                    self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            return
        kind = self._drag[0]
        if kind == "scrub":
            self.seek.emit(pr.snap(self.t_of(pos.x())))
            return
        if kind == "rubber":
            start, base = self._drag[1], self._drag[2]
            self._rubber = QRectF(start, pos).normalized()
            hit = {i["id"] for i in pr.items if self.item_rect(i).intersects(self._rubber)}
            self.selection = base | hit
            self.update()
            return
        if kind == "trim":
            _, iid, edge, origin, alt = self._drag
            pr.data["items"] = json.loads(origin)
            t = self._snap(self.t_of(pos.x()), exclude=pr.expand_linked([iid]))
            pr.trim_item(iid, edge, t, linked=not alt)
            self.changed.emit()
            self.update()
            return
        if kind == "move":
            _, t0, rkind, rtrack, ids, origin, alt, ldv, lda = self._drag
            pr.data["items"] = json.loads(origin)
            moving = pr.expand_linked(ids) if not alt else set(ids)
            dt = self.t_of(pos.x()) - t0
            edges = []
            for i in pr.items:
                if i["id"] in moving:
                    edges += [i["start"], pr.item_end(i)]
            dt += self._snap(dt, exclude=moving, moving_edges=edges)
            r = self.row_at(pos.y())
            dv, da = ldv, lda
            if r is not None and r["kind"] == rkind:
                if rkind == "video":
                    dv = r["track"] - rtrack
                else:
                    da = r["track"] - rtrack
            self._drag = ("move", t0, rkind, rtrack, ids, origin, alt, dv, da)
            pr.move_items(list(ids), dt, dv, da, linked=not alt)
            self.changed.emit()
            self.update()

    def mouseReleaseEvent(self, e):
        d = self._drag
        self._drag = None
        if d is None:
            return
        if d[0] == "rubber":
            self._rubber = None
            self.selection_changed.emit(list(self.selection))
            self.update()
        elif d[0] in ("trim", "move"):
            origin = d[3] if d[0] == "trim" else d[5]
            if json.dumps(self.project.data["items"]) == origin:
                self.project.drop_checkpoint()
            self.changed.emit()
            self.host.timeline_edit_done()

    def wheelEvent(self, e):
        dx = e.angleDelta().x()
        dy = e.angleDelta().y()
        if mod_multi(e):
            self.zoom(1.15 if dy > 0 else 1 / 1.15, e.position().x())
            return
        if e.modifiers() & Qt.ShiftModifier and dx == 0:
            dx, dy = dy, 0
        pd = e.pixelDelta()
        if not pd.isNull():
            move = -(pd.x() if abs(pd.x()) > abs(pd.y()) else pd.y())
        else:
            move = -(dx if abs(dx) > abs(dy) else dy) / 120 * 60
        self.scroll_t = max(0.0, self.scroll_t + move / self.pps)
        self.host.timeline_scrolled()
        self.update()

    # ------------------------------------------------------- trascinamento
    def _drop_target(self, pos):
        r = self.row_at(pos.y())
        nv = len(self.project.tracks("video"))
        na = len(self.project.tracks("audio"))
        if r is None:
            return 0, 0
        if r["kind"] == "video":
            return r["track"], min(r["track"], na - 1)
        return min(r["track"], nv - 1), r["track"]

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME) or e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        md = e.mimeData()
        if not md.hasFormat(MIME):
            e.acceptProposedAction()
            return
        ids = json.loads(md.data(MIME).data().decode())
        pos = e.position()
        t = self._snap(self.t_of(pos.x()))
        vt, at = self._drop_target(pos)
        ghost = []
        for rid in ids:
            src, a, b = self.project.range_of(rid)
            if src is None:
                continue
            d = b - a
            if src["has_video"]:
                ghost.append(("video", vt, t, t + d))
            if src["has_audio"]:
                ghost.append(("audio", at, t, t + d))
            t += d
        self._ghost = ghost
        self.update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._ghost = None
        self.update()

    def dropEvent(self, e):
        self._ghost = None
        md = e.mimeData()
        if md.hasFormat(MIME):
            ids = json.loads(md.data(MIME).data().decode())
            pos = e.position()
            vt, at = self._drop_target(pos)
            self.host.add_refs_at(ids, self._snap(self.t_of(pos.x())), vt, at)
            e.acceptProposedAction()
        elif md.hasUrls():
            self.host.import_paths([u.toLocalFile() for u in md.urls() if u.isLocalFile()])
            e.acceptProposedAction()
        self.update()

    # -------------------------------------------------------------- menu
    def _item_menu(self, gp, t):
        ids = list(self.selection)
        pr = self.project
        its = [pr.item(i) for i in ids if pr.item(i)]
        m = QMenu(self)
        m.addAction("Taglia alla testina\tB", lambda: self.action.emit("split", ids))
        m.addAction("Elimina\tCanc", lambda: self.action.emit("delete", ids))
        m.addAction("Elimina e chiudi lo spazio\tMaiusc+Canc", lambda: self.action.emit("ripple_delete", ids))
        m.addSeparator()
        if any(i.get("link") for i in its):
            m.addAction("Scollega audio e video", lambda: self.action.emit("unlink", ids))
        if len(its) >= 2 and not all(i.get("link") for i in its):
            m.addAction("Collega audio e video", lambda: self.action.emit("link", ids))
        if any(i["kind"] == "audio" for i in its):
            m.addAction("Volume…", lambda: self.action.emit("gain", ids))
        if any(i["kind"] == "video" for i in its):
            m.addSeparator()
            m.addAction("Copia regolazioni colore", lambda: self.action.emit("copy_grade", ids))
            m.addAction("Incolla regolazioni colore", lambda: self.action.emit("paste_grade", ids))
            m.addAction("Azzera colore del pezzo", lambda: self.action.emit("reset_grade", ids))
        m.addSeparator()
        m.addAction("Mostra nella lista clip", lambda: self.action.emit("reveal_in_bin", ids))
        m.exec(gp)

    def _empty_menu(self, gp, t):
        m = QMenu(self)
        m.addAction("Aggiungi traccia video", lambda: self.action.emit("add_video_track", []))
        m.addAction("Aggiungi traccia audio", lambda: self.action.emit("add_audio_track", []))
        m.addAction("Adatta alla finestra\tMaiusc+Z", self.zoom_fit)
        m.exec(gp)

    def _header_menu(self, r, gp):
        m = QMenu(self)
        m.addAction("Aggiungi traccia video", lambda: self.action.emit("add_video_track", []))
        m.addAction("Aggiungi traccia audio", lambda: self.action.emit("add_audio_track", []))
        m.addSeparator()
        m.addAction(f"Rimuovi traccia {self.project.tracks(r['kind'])[r['track']]['name']}",
                    lambda: self.action.emit("remove_track", [r["kind"], r["track"]]))
        m.exec(gp)


class TimelinePanel(QWidget):
    """Timeline con barra strumenti e barre di scorrimento."""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        self.title = QLabel("TIMELINE")
        self.title.setObjectName("modeLabel")
        bar.addWidget(self.title)
        bar.addSpacing(12)
        self.btn_split = QPushButton("✂ Taglia (B)")
        self.btn_snap = QPushButton("Aggancio (N)")
        self.btn_snap.setCheckable(True)
        self.btn_snap.setChecked(True)
        self.btn_fit = QPushButton("Adatta")
        for b in (self.btn_split, self.btn_snap, self.btn_fit):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(QLabel("Zoom"))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(0, 1000)
        self.zoom.setFixedWidth(160)
        bar.addWidget(self.zoom)
        lay.addLayout(bar)
        row = QHBoxLayout()
        row.setSpacing(0)
        self.view = TimelineView(host)
        self.vbar = QScrollBar(Qt.Vertical)
        row.addWidget(self.view, 1)
        row.addWidget(self.vbar)
        lay.addLayout(row, 1)
        self.hbar = QScrollBar(Qt.Horizontal)
        lay.addWidget(self.hbar)
        self._sync = False
        self.hbar.valueChanged.connect(self._hscroll)
        self.vbar.valueChanged.connect(self._vscroll)
        self.zoom.valueChanged.connect(self._zoomed)
        self.btn_snap.toggled.connect(self._snap)
        self.btn_fit.clicked.connect(self.view.zoom_fit)
        self.btn_split.clicked.connect(lambda: self.view.action.emit("split", list(self.view.selection)))

    def _snap(self, on):
        self.view.snapping = on

    def _hscroll(self, v):
        if self._sync:
            return
        self.view.scroll_t = v / self.view.pps
        self.view.update()

    def _vscroll(self, v):
        if self._sync:
            return
        self.view.scroll_y = v
        self.view.update()

    def _zoomed(self, v):
        if self._sync:
            return
        pps = 2.0 * (400.0 ** (v / 1000.0))
        self.view.zoom(pps / self.view.pps)

    def sync(self):
        """Aggiorna barre di scorrimento e zoom dallo stato della vista."""
        self._sync = True
        v = self.view
        end = max(v.project.timeline_end(), v.playhead) + 30
        total = int(end * v.pps)
        page = max(1, v.width() - HEAD)
        self.hbar.setRange(0, max(0, total - page))
        self.hbar.setPageStep(page)
        self.hbar.setValue(int(v.scroll_t * v.pps))
        ch = v.content_height()
        self.vbar.setRange(0, max(0, ch - v.height()))
        self.vbar.setPageStep(max(1, v.height()))
        self.vbar.setVisible(ch > v.height())
        if v.scroll_y > self.vbar.maximum():
            v.scroll_y = self.vbar.maximum()
        self.vbar.setValue(v.scroll_y)
        import math

        self.zoom.setValue(int(1000 * math.log(v.pps / 2.0) / math.log(400.0)))
        self._sync = False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.sync()
