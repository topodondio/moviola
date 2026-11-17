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
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QPushButton, QScrollBar, QSlider, QVBoxLayout,
                               QWidget)

from .. import color
from .. import speed as spd
from ..project import MARKER_COLORS, TRANSITIONS
from . import icons
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

# sfumature per traccia: video = blu (V1 il più scuro), audio = verde (A1 il più scuro)
VIDEO_SHADES = [QColor(52, 84, 132), QColor(68, 108, 166), QColor(92, 134, 192), QColor(120, 160, 212),
                QColor(150, 184, 226), QColor(176, 204, 236)]
AUDIO_SHADES = [QColor(40, 104, 82), QColor(56, 128, 100), QColor(78, 152, 120), QColor(106, 174, 142),
                QColor(136, 194, 164), QColor(164, 212, 186)]


def track_color(kind, track):
    shades = AUDIO_SHADES if kind == "audio" else VIDEO_SHADES
    return QColor(shades[min(track, len(shades) - 1)])
C_TEXT = QColor(122, 84, 150)
C_SHAPE = QColor(150, 110, 60)
C_GAIN = QColor(240, 210, 70)
DB_MIN, DB_MAX = -40.0, 6.0
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
        self.ripple = False
        self.slip_mode = False  # ritaglio dinamico: trascinare il pezzo ne scorre il contenuto
        self._drag = None
        self._ghost = None
        self._rubber = None
        self._snap_line = None
        self._hover_id = None
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
        r = self.row_of("audio" if it["kind"] == "audio" else "video", it["track"])
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
            col = QColor(C_ROW if i % 2 == 0 else C_ROW2)
            tc_ = track_color(r["kind"], r["track"])  # velatura del colore della traccia
            col = QColor((col.red() * 9 + tc_.red()) // 10, (col.green() * 9 + tc_.green()) // 10,
                         (col.blue() * 9 + tc_.blue()) // 10)
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
        self._paint_transitions(p)
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
            p.fillRect(QRectF(0, r["y"] + 1, 5, r["h"] - 2), track_color(r["kind"], r["track"]))
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
        self._paint_range(p)
        self._paint_markers(p)
        p.fillRect(QRectF(0, 0, HEAD, RULER), QColor(22, 23, 25))
        p.setFont(small)
        for i, (lab, _) in enumerate((("+V", "addv"), ("+A", "adda"))):
            br = self._add_btn_rect(i)
            p.setPen(QPen(QColor(90, 92, 96), 1))
            p.setBrush(QColor(45, 47, 51))
            p.drawRoundedRect(br, 3, 3)
            p.setPen(QColor(220, 220, 220))
            p.drawText(br, Qt.AlignCenter, lab)
        # guida dell'aggancio magnetico
        if self._snap_line is not None and (self._drag is not None or self._ghost):
            xs = self.x_of(self._snap_line)
            if xs >= HEAD:
                p.setPen(QPen(C_SEL, 1.5, Qt.DashLine))
                p.drawLine(QPointF(xs, RULER), QPointF(xs, self.height()))
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

    # ------------------------------------------------------- segni I/O
    def _paint_range(self, p):
        a, b = self.project.tl_range
        if a is None and b is None:
            return
        col = QColor("#e8a33d")
        xa = self.x_of(a) if a is not None else None
        xb = self.x_of(b) if b is not None else None
        lo = max(HEAD, xa if xa is not None else HEAD)
        hi = min(self.width(), xb if xb is not None else self.width())
        if hi > lo and (a is not None and b is not None):
            p.fillRect(QRectF(lo, 0, hi - lo, RULER), QColor(col.red(), col.green(), col.blue(), 55))
            p.fillRect(QRectF(lo, RULER, hi - lo, self.height() - RULER), QColor(col.red(), col.green(), col.blue(), 14))
        f = QFont(p.font())
        f.setPointSizeF(8)
        f.setBold(True)
        p.setFont(f)
        for x, lab, left in ((xa, "I", True), (xb, "O", False)):
            if x is None or x < HEAD - 2 or x > self.width() + 2:
                continue
            p.setPen(QPen(col, 2))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            tab = QRectF(x if left else x - 12, 0, 12, 12)
            p.drawRect(tab)
            p.setPen(QColor(20, 20, 20))
            p.drawText(tab, Qt.AlignCenter, lab)

    # ------------------------------------------------------- segnaposti
    def _paint_markers(self, p):
        f = QFont(p.font())
        f.setPointSizeF(8)
        p.setFont(f)
        for m in self.project.markers:
            x = self.x_of(m["t"])
            if x < HEAD - 6 or x > self.width() + 6:
                continue
            c = QColor(m.get("color") or MARKER_COLORS[0])
            p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 70), 1, Qt.DashLine))
            p.drawLine(QPointF(x, RULER), QPointF(x, self.height()))
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            if self.project.is_chapter(m):      # capitolo: bandierina grande
                p.drawPolygon(QPolygonF([QPointF(x - 5, RULER - 11), QPointF(x + 5, RULER - 11),
                                         QPointF(x + 5, RULER - 5), QPointF(x, RULER), QPointF(x - 5, RULER - 5)]))
            else:                               # indicatore generico: piccolo rombo
                p.drawPolygon(QPolygonF([QPointF(x, RULER - 9), QPointF(x + 3.5, RULER - 5.5),
                                         QPointF(x, RULER - 2), QPointF(x - 3.5, RULER - 5.5)]))
            if m.get("label"):
                p.setPen(c)
                p.drawText(QPointF(x + 7, RULER - 4), m["label"])

    def marker_at(self, pos):
        if pos.y() >= RULER:
            return None
        best = None
        for m in self.project.markers:
            d = abs(self.x_of(m["t"]) - pos.x())
            if d <= 6 and (best is None or d < best[0]):
                best = (d, m)
        return best[1] if best else None

    # ------------------------------------------------------- transizioni
    def _paint_transitions(self, p):
        for tr in self.project.transitions():
            r = self.row_of("video", tr["b"]["track"])
            if r is None:
                continue
            x0, x1 = self.x_of(tr["t0"]), self.x_of(tr["t1"])
            if x1 < HEAD or x0 > self.width():
                continue
            box = QRectF(x0, r["y"] + 2, max(3.0, x1 - x0), r["h"] - 4)
            p.save()
            p.setClipRect(QRectF(HEAD, RULER, self.width() - HEAD, self.height()))
            p.setPen(QPen(QColor(255, 255, 255, 200), 1))
            p.setBrush(QColor(255, 255, 255, 45))
            p.drawRect(box)
            p.drawLine(box.topLeft(), box.bottomRight())
            p.drawLine(box.bottomLeft(), box.topRight())
            if box.width() > 60:
                p.setPen(QColor(255, 255, 255))
                p.drawText(box.adjusted(3, 0, -3, -2), Qt.AlignBottom | Qt.AlignHCenter,
                           TRANSITIONS.get(tr["type"], "").split()[0])
            p.restore()

    # --------------------------------------------------- volume disegnato
    def _gain_area(self, rect):
        return rect.top() + 14, rect.bottom() - 3

    def _y_of_db(self, rect, db):
        top, bot = self._gain_area(rect)
        u = (min(DB_MAX, max(DB_MIN, db)) - DB_MIN) / (DB_MAX - DB_MIN)
        return bot - u * (bot - top)

    def _db_of_y(self, rect, y):
        top, bot = self._gain_area(rect)
        u = (bot - y) / max(bot - top, 1)
        db = DB_MIN + min(1.0, max(0.0, u)) * (DB_MAX - DB_MIN)
        return 0.0 if abs(db) < 0.6 else round(db, 1)  # aggancio a 0 dB

    def _gain_points_px(self, it, rect):
        out = []
        for st, db in self.project.gain_points(it):
            out.append(QPointF(self.x_of(spd.tl_time(it, st)), self._y_of_db(rect, db)))
        return out

    def _gain_y_at(self, it, rect, x):
        st = float(self.project.src_time(it, self.t_of(x)))
        return self._y_of_db(rect, self.project.gain_at(it, st))

    def _paint_gain(self, p, it, rect):
        pts = self._gain_points_px(it, rect)
        p.setPen(QPen(C_GAIN, 1.5))
        if not pts:
            y = self._y_of_db(rect, 0.0)
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            return
        path = QPainterPath(QPointF(rect.left(), pts[0].y()))
        for q in pts:
            path.lineTo(q)
        path.lineTo(QPointF(rect.right(), pts[-1].y()))
        p.drawPath(path)
        p.setBrush(C_GAIN)
        p.setPen(QPen(QColor(30, 30, 30), 1))
        for q in pts:
            if rect.left() - 4 <= q.x() <= rect.right() + 4:
                p.drawEllipse(q, 3.5, 3.5)

    def _gain_hit(self, it, pos):
        if it is None or it["kind"] != "audio":
            return None
        rect = self.item_rect(it)
        if pos.x() - rect.left() <= EDGE or rect.right() - pos.x() <= EDGE:
            return None
        for k, q in enumerate(self._gain_points_px(it, rect)):
            if abs(q.x() - pos.x()) <= 6 and abs(q.y() - pos.y()) <= 6:
                return ("point", k)
        if abs(self._gain_y_at(it, rect, pos.x()) - pos.y()) <= 4:
            return ("line", None)
        return None

    # ------------------------------------- maniglie: dissolvenze e opacità
    GRIP_H = 10

    def _fade_grip_x(self, it, rect, key):
        d = float(it.get(key, 0.0) or 0.0) * self.pps
        if key == "fade_in":
            return min(rect.right() - 5, rect.left() + max(5.0, d))
        return max(rect.left() + 5, rect.right() - max(5.0, d))

    def _fade_grip_hit(self, it, pos):
        if it is None:
            return None
        rect = self.item_rect(it)
        if rect.width() < 24 or not (rect.top() <= pos.y() <= rect.top() + self.GRIP_H):
            return None
        for key in ("fade_in", "fade_out"):
            if abs(self._fade_grip_x(it, rect, key) - pos.x()) <= 6:
                return key
        return None

    def _opacity_y(self, rect, op):
        top, bot = self._gain_area(rect)
        return bot - max(0.0, min(1.0, op)) * (bot - top)

    def _opacity_hit(self, it, pos):
        if it is None or it["kind"] not in ("text", "shape"):
            return False
        rect = self.item_rect(it)
        if pos.x() - rect.left() <= EDGE or rect.right() - pos.x() <= EDGE:
            return False
        return abs(self._opacity_y(rect, float(it.get("opacity", 1.0))) - pos.y()) <= 4

    def _paint_handles(self, p, it, rect, hover):
        """Maniglie delle dissolvenze (quadratini in alto) e linea di opacità."""
        if it["kind"] in ("text", "shape"):
            y = self._opacity_y(rect, float(it.get("opacity", 1.0)))
            p.setPen(QPen(QColor(120, 200, 255), 1.5))
            p.drawLine(QPointF(rect.left() + 2, y), QPointF(rect.right() - 2, y))
        if rect.width() < 24:
            return
        for key in ("fade_in", "fade_out"):
            x = self._fade_grip_x(it, rect, key)
            on = float(it.get(key, 0.0) or 0.0) > 0
            if not (on or hover):
                continue
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.setBrush(QColor(255, 255, 255) if on else QColor(255, 255, 255, 140))
            p.drawRect(QRectF(x - 3.5, rect.top() + 1, 7, 7))

    def _paint_item(self, p, it, rect):
        pr = self.project
        sel = it["id"] in self.selection
        kind = it["kind"]
        base = {"video": track_color("video", it["track"]), "audio": track_color("audio", it["track"]),
                "text": C_TEXT, "shape": C_SHAPE}[kind]
        if pr.tracks("audio" if kind == "audio" else "video")[it["track"]].get("muted"):
            base = QColor(base.red() // 2 + 30, base.green() // 2 + 30, base.blue() // 2 + 30)
        overlay = kind in ("text", "shape")
        src = pr.source(it["source"]) if not overlay else None
        missing = not overlay and src is None
        p.save()
        p.setClipRect(rect.intersected(QRectF(HEAD, RULER, self.width() - HEAD, self.height())))
        p.setPen(Qt.NoPen)
        p.setBrush(base if not missing else QColor(120, 50, 50))
        p.drawRoundedRect(rect, 3, 3)
        if kind == "video":
            pm = self.host.thumb_for(it["source"])
            if pm is not None and rect.width() > 40:
                th = rect.height() - 16
                tw = th * 16 / 9
                p.drawPixmap(QRectF(rect.left() + 2, rect.top() + 14, tw, th).toRect(), pm)
            if not color.is_identity(it.get("grade")):
                p.setBrush(C_SEL)
                p.drawEllipse(QPointF(rect.right() - 8, rect.top() + 7), 3.5, 3.5)
        elif kind == "audio":
            self._paint_wave(p, it, rect)
            self._paint_gain(p, it, rect)
        if it.get("freeze") is not None:
            p.setPen(QColor(235, 235, 235))
            p.drawText(rect.adjusted(5, 14, -5, 0), Qt.AlignLeft | Qt.AlignTop, "❚❚ fermo")
        for mt in pr.item_marker_times(it):
            x = self.x_of(mt)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(232, 163, 61))
            p.drawPolygon(QPolygonF([QPointF(x - 4, rect.top()), QPointF(x + 4, rect.top()),
                                     QPointF(x, rect.top() + 6)]))
            p.setPen(QPen(QColor(232, 163, 61, 160), 1))
            p.drawLine(QPointF(x, rect.top() + 6), QPointF(x, rect.bottom()))
        # dissolvenze: triangoli agli estremi
        for key, left in (("fade_in", True), ("fade_out", False)):
            d = float(it.get(key, 0.0) or 0.0)
            if d <= 0:
                continue
            w = min(d * self.pps, rect.width())
            white = it.get(key + "_color") == "white"
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 110) if white else QColor(0, 0, 0, 120))
            if left:
                poly = [QPointF(rect.left(), rect.top()), QPointF(rect.left() + w, rect.top()),
                        QPointF(rect.left(), rect.bottom())]
            else:
                poly = [QPointF(rect.right(), rect.top()), QPointF(rect.right() - w, rect.top()),
                        QPointF(rect.right(), rect.bottom())]
            p.drawPolygon(QPolygonF(poly))
        # etichetta colore della clip o della parte da cui viene il pezzo
        tag = None
        if not overlay:
            ref = pr.subclip(it.get("origin") or "") or pr.source(it["source"])
            tag = (ref or {}).get("tag") or ((src or {}).get("tag"))
        if tag:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(tag))
            p.drawRect(QRectF(rect.left(), rect.top(), rect.width(), 4))
        badges = []
        if it.get("speed_curve"):
            badges.append("velocità ∿")
        else:
            sp = float(it.get("speed", 1.0) or 1.0)
            if abs(sp - 1.0) > 1e-3:
                badges.append(f"{sp * 100:.0f}%")
        if kind == "video" and float(it.get("bars", 0.0) or 0.0) > 0:
            badges.append("▭")
        if kind == "video" and (abs(float(it.get("zoom", 1.0) or 1.0) - 1) > 1e-3 or it.get("pos_x") or it.get("pos_y")):
            badges.append("⤢")
        if kind == "audio" and abs(it.get("gain_db", 0.0)) > 0.05:
            badges.append(f"{it['gain_db']:+.1f} dB")
        if badges:
            p.setPen(QColor(235, 235, 235))
            p.drawText(rect.adjusted(0, rect.height() - 16, -6, 0), Qt.AlignRight | Qt.AlignVCenter,
                       "  ".join(badges))
        p.setPen(QColor(245, 245, 245))
        if kind == "shape":
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.setBrush(QColor(it.get("color", "#000000")))
            p.drawRect(QRectF(rect.left() + 5, rect.top() + 18, 14, 10))
            lab = "▭  Riquadro"
        elif kind == "text":
            lab = "T  " + ((it.get("text") or "").split("\n")[0] or "(vuota)")
        else:
            lab = it.get("origin") or it["source"]
            if it.get("link") is None and src is not None and src.get("has_audio") and src.get("has_video"):
                lab += "  (scollegato)"
        p.drawText(rect.adjusted(5, 1, -16, 0), Qt.AlignLeft | Qt.AlignTop, lab)
        self._paint_handles(p, it, rect, sel or it["id"] == self._hover_id)
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
        if it.get("reverse"):
            ts = it["src_out"] - (xs - rect.left()) / self.pps
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
        """Aggancio magnetico ai bordi dei pezzi di tutte le tracce, alla testina
        e all'inizio. Restituisce il tempo agganciato (o lo spostamento da
        aggiungere se moving_edges è dato) e memorizza dove disegnare la guida."""
        self._snap_line = None
        suspended = bool(QGuiApplication.keyboardModifiers() & Qt.ShiftModifier)
        if not self.snapping or suspended:
            return t if moving_edges is None else 0.0
        pts = self.project.snap_points(exclude) + [self.playhead]
        tol = 12 / self.pps
        if moving_edges is None:
            best = min(pts, key=lambda q: abs(q - t), default=t)
            if abs(best - t) <= tol:
                self._snap_line = best
                return best
            return t
        best_d, best_q = None, None
        for e in moving_edges:
            for q in pts:
                d = q - (e + t)
                if abs(d) <= tol and (best_d is None or abs(d) < abs(best_d)):
                    best_d, best_q = d, q
        self._snap_line = best_q
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
            m = self.marker_at(pos)
            if e.button() == Qt.RightButton:
                self._marker_menu(m, e.globalPosition().toPoint(), self.t_of(pos.x()))
                return
            if e.button() == Qt.LeftButton:
                if m is not None:
                    pr.checkpoint()
                    self._drag = ("marker", m["id"], m["t"])
                    return
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
        mods = e.modifiers()
        on_edge = pos.x() - rect.left() <= EDGE or rect.right() - pos.x() <= EDGE
        if (self.slip_mode and not on_edge and e.button() == Qt.LeftButton and not spd.is_static(it)) or (
                alt and (mods & Qt.ControlModifier or mods & Qt.MetaModifier) and not spd.is_static(it)):
            # ritaglio dinamico (modalità attiva, o Ctrl/Cmd+Alt+trascina): scorre il
            # contenuto, posizione e durata restano
            pr.checkpoint()
            self._drag = ("slip", it["id"], self.t_of(pos.x()), origin)
            self.setCursor(Qt.SizeHorCursor)
            self.host.slip_preview(it["id"])
            return
        fg = self._fade_grip_hit(it, pos)
        if fg is not None:
            pr.checkpoint()
            self._drag = ("fade", it["id"], fg, origin)
            return
        if self._opacity_hit(it, pos):
            pr.checkpoint()
            self._drag = ("opacity", it["id"], origin)
            return
        gh = self._gain_hit(it, pos) if not alt else None
        if gh is not None:
            pr.checkpoint()
            pts = it.setdefault("gain_pts", [])
            if gh[0] == "line":
                st = float(pr.src_time(it, self.t_of(pos.x())))
                db = round(pr.gain_at(it, st), 1)
                pts.append([round(st, 4), db])
                pts.sort(key=lambda q: q[0])
                k = next(i for i, q in enumerate(pts) if q[0] == round(st, 4))
            else:
                k = gh[1]
            self._drag = ("gain", it["id"], k, origin)
            self.update()
            return
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
            if self.marker_at(pos) is not None:
                self.setCursor(Qt.SizeHorCursor)
                return
            it = self.item_at(pos)
            hid = it["id"] if it is not None else None
            if hid != self._hover_id:
                self._hover_id = hid
                self.update()
            if it is not None:
                rect = self.item_rect(it)
                fg = self._fade_grip_hit(it, pos)
                if fg is not None:
                    self.setCursor(Qt.SizeHorCursor)
                    self.setToolTip("Trascina per la dissolvenza " + ("in entrata" if fg == "fade_in" else "in uscita"))
                    return
                if self._opacity_hit(it, pos):
                    self.setCursor(Qt.SizeVerCursor)
                    self.setToolTip(f"Opacità {float(it.get('opacity', 1.0)) * 100:.0f}% (trascina su o giù)")
                    return
                gh = self._gain_hit(it, pos)
                if gh is not None:
                    self.setCursor(Qt.PointingHandCursor if gh[0] == "point" else Qt.CrossCursor)
                    self.setToolTip("Volume: clic per aggiungere un punto, trascina, doppio clic per togliere")
                    return
                self.setToolTip("")
                if pos.x() - rect.left() <= EDGE or rect.right() - pos.x() <= EDGE:
                    self.setCursor(Qt.SizeHorCursor)
                elif self.slip_mode:
                    self.setCursor(Qt.SizeHorCursor)
                    self.setToolTip("Ritaglio dinamico: trascina per scorrere il contenuto\n"
                                    "(la clip resta dov'è e lunga uguale)")
                else:
                    self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            return
        kind = self._drag[0]
        if kind == "scrub":
            self.seek.emit(pr.snap(self.t_of(pos.x())))
            return
        if kind == "marker":
            m = pr.marker(self._drag[1])
            if m is not None:
                m["t"] = pr.snap(max(0.0, self._snap(self.t_of(pos.x()), exclude=())))
                self.update()
            return
        if kind == "fade":
            _, iid, key, origin = self._drag
            it = pr.item(iid)
            if it is None:
                return
            rect = self.item_rect(it)
            dur = pr.item_dur(it)
            other = float(it.get("fade_out" if key == "fade_in" else "fade_in", 0.0) or 0.0)
            if key == "fade_in":
                v = (pos.x() - rect.left()) / self.pps
            else:
                v = (rect.right() - pos.x()) / self.pps
            v = max(0.0, min(dur - other, v))
            v = 0.0 if v * self.pps < 4 else round(v, 2)
            it[key] = v
            self.setToolTip(f"Dissolvenza {v:.2f} s")
            self.changed.emit()
            self.update()
            return
        if kind == "opacity":
            _, iid, origin = self._drag
            it = pr.item(iid)
            if it is None:
                return
            top, bot = self._gain_area(self.item_rect(it))
            op = max(0.0, min(1.0, (bot - pos.y()) / max(bot - top, 1)))
            it["opacity"] = round(op, 3)
            self.setToolTip(f"Opacità {op * 100:.0f}%")
            self.changed.emit()
            self.update()
            return
        if kind == "slip":
            _, iid, t0, origin = self._drag
            pr.data["items"] = json.loads(origin)
            it = pr.item(iid)
            if it is None:
                return
            d = -(self.t_of(pos.x()) - t0) * pr.speed(it)
            got = pr.slip([iid], d)
            it = pr.item(iid)
            self.setToolTip(f"Contenuto spostato di {got:+.2f} s  ·  inizio a {it['src_in']:.2f} s del file")
            self.changed.emit()
            self.host.slip_preview(iid)
            self.update()
            return
        if kind == "gain":
            _, iid, k, origin = self._drag
            it = pr.item(iid)
            pts = (it or {}).get("gain_pts") or []
            if not (0 <= k < len(pts)):
                return
            rect = self.item_rect(it)
            st = float(pr.src_time(it, self.t_of(pos.x())))
            lo = pts[k - 1][0] + 1e-3 if k > 0 else it["src_in"]
            hi = pts[k + 1][0] - 1e-3 if k + 1 < len(pts) else it["src_out"]
            pts[k] = [round(min(max(st, lo), hi), 4), self._db_of_y(rect, pos.y())]
            self.setToolTip(f"{pts[k][1]:+.1f} dB")
            self.changed.emit()
            self.update()
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
            pr.trim_item(iid, edge, t, linked=not alt, ripple=self.ripple)
            self.changed.emit()
            self.host.trim_preview(iid, edge)
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
        self._snap_line = None
        if d is None:
            return
        if d[0] == "rubber":
            self._rubber = None
            self.selection_changed.emit(list(self.selection))
            self.update()
        elif d[0] == "marker":
            m = self.project.marker(d[1])
            if m is not None and abs(m["t"] - d[2]) < 1e-9:
                self.project.drop_checkpoint()
            self.project.markers.sort(key=lambda x: x["t"])
            self.host.timeline_edit_done()
        elif d[0] in ("trim", "move", "gain", "fade", "opacity", "slip"):
            origin = d[5] if d[0] == "move" else d[-1] if d[0] in ("fade", "opacity", "slip") else d[3]
            if json.dumps(self.project.data["items"]) == origin:
                # semplice clic, nulla è cambiato: niente da salvare
                self.project.drop_checkpoint()
                self.update()
                return
            self.changed.emit()
            self.host.timeline_edit_done()

    def mouseDoubleClickEvent(self, e):
        pos = e.position()
        m = self.marker_at(pos)
        if m is not None:
            self.action.emit("rename_marker", [m["id"]])
            return
        it = self.item_at(pos)
        gh = self._gain_hit(it, pos)
        if gh is not None and gh[0] == "point":
            pts = it.get("gain_pts") or []
            if 0 <= gh[1] < len(pts):
                self.project.checkpoint()
                del pts[gh[1]]
                self._drag = None
                self.changed.emit()
                self.host.timeline_edit_done()
                self.update()
            return
        if it is not None:
            self.action.emit("open_color" if it["kind"] == "video" else "open_effects", [it["id"]])

    def leaveEvent(self, e):
        if self._hover_id is not None:
            self._hover_id = None
            self.update()

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

    def _drop_time(self, ids, pos):
        """Punto di rilascio: si aggancia sia l'inizio sia la fine di ciò che si trascina."""
        total = 0.0
        for rid in ids:
            src, a, b = self.project.range_of(rid)
            if src is not None:
                total += b - a
        t0 = self.t_of(pos.x())
        return max(0.0, t0 + self._snap(t0, moving_edges=[0.0, total]))

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
        t = self._drop_time(ids, pos)
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
                on_audio = (self.row_at(pos.y()) or {}).get("kind") == "audio"
                track = at if (src["has_video"] or on_audio) else len(self.project.tracks("audio")) - 1
                ghost.append(("audio", track, t, t + d))
            t += d
        self._ghost = ghost
        self.update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._ghost = None
        self._snap_line = None
        self.update()

    def dropEvent(self, e):
        self._ghost = None
        md = e.mimeData()
        if md.hasFormat(MIME):
            ids = json.loads(md.data(MIME).data().decode())
            pos = e.position()
            vt, at = self._drop_target(pos)
            r = self.row_at(pos.y())
            self.host.add_refs_at(ids, self._drop_time(ids, pos), vt, at,
                                  audio_row=bool(r and r["kind"] == "audio"))
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
        m.addAction("Velocità ed effetti…", lambda: self.action.emit("open_effects", ids))
        if any(i["kind"] == "video" for i in its):
            m.addAction("Apri in Colore", lambda: self.action.emit("open_color", ids))
        if any(i["kind"] == "video" for i in its):
            tm = m.addMenu("Transizione con la clip precedente")
            tm.addAction("Nessuna", lambda: self.action.emit("transition:", ids))
            for k, lab in TRANSITIONS.items():
                tm.addAction(lab, lambda kk=k: self.action.emit("transition:" + kk, ids))
            m.addAction("Fermo immagine alla testina\tMaiusc+F", lambda: self.action.emit("freeze", ids))
        m.addAction("Segnaposto sul pezzo alla testina\tMaiusc+M", lambda: self.action.emit("item_marker", ids))
        if any(i.get("markers") for i in its):
            m.addAction("Elimina i segnaposti del pezzo", lambda: self.action.emit("clear_item_markers", ids))
        if any(i.get("gain_pts") for i in its):
            m.addAction("Azzera volume disegnato", lambda: self.action.emit("clear_gain", ids))
        m.addSeparator()
        if any(not spd.is_static(i) for i in its):
            m.addAction("Scorri contenuto di 1 fotogramma ←\t,", lambda: self.action.emit("slip:-1", ids))
            m.addAction("Scorri contenuto di 1 fotogramma →\t.", lambda: self.action.emit("slip:1", ids))
        m.addAction("Taglia alla testina\tC", lambda: self.action.emit("split", ids))
        m.addAction("Duplica\tCtrl+D", lambda: self.action.emit("duplicate", ids))
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
        if any(i["kind"] == "video" for i in its):
            m.addAction("Mostra nella clip", lambda: self.action.emit("show_in_clip", ids))
        m.exec(gp)

    def _marker_menu(self, mk, gp, t):
        m = QMenu(self)
        if mk is None:
            m.addAction("Aggiungi indicatore qui", lambda: self.action.emit("add_marker_at", [t, "note"]))
            m.addAction("Aggiungi capitolo qui", lambda: self.action.emit("add_marker_at", [t, "chapter"]))
        else:
            chap = self.project.is_chapter(mk)
            m.addAction("Trasforma in indicatore" if chap else "Trasforma in capitolo",
                        lambda: self.action.emit("toggle_marker_kind", [mk["id"]]))
            m.addAction("Rinomina segnaposto…", lambda: self.action.emit("rename_marker", [mk["id"]]))
            cm = m.addMenu("Colore")
            for c, lab in zip(MARKER_COLORS, ("Arancione", "Rosso", "Verde", "Blu", "Viola", "Bianco")):
                cm.addAction(lab, lambda cc=c: self.action.emit("color_marker", [mk["id"], cc]))
            m.addAction("Elimina segnaposto", lambda: self.action.emit("delete_marker", [mk["id"]]))
        if self.project.markers:
            m.addSeparator()
            m.addAction("Elimina tutti i segnaposti", lambda: self.action.emit("delete_all_markers", []))
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


class TimelineOverview(QWidget):
    """Tutto il montaggio in miniatura: il riquadro chiaro è la parte visibile
    della timeline; cliccando o trascinando ci si sposta."""

    def __init__(self, view, panel):
        super().__init__(panel)
        self.view = view
        self.panel = panel
        self.setFixedHeight(24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Panoramica del montaggio: clicca o trascina per spostarti")

    def _total(self):
        v = self.view
        visible = (v.width() - HEAD) / max(v.pps, 1e-6)
        return max(v.project.timeline_end(), v.scroll_t + visible, 5.0) * 1.02

    def _x(self, t):
        return HEAD + t / self._total() * max(1.0, self.width() - HEAD - 6)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(22, 23, 25))
        pr = self.view.project
        nv = max(1, len(pr.tracks("video")))
        na = max(1, len(pr.tracks("audio")))
        top, bot = 3.0, self.height() - 3.0
        mid = (top + bot) / 2
        for it in pr.items:
            kind = "audio" if it["kind"] == "audio" else "video"
            if kind == "video":
                hh = (mid - top) / nv
                y = mid - (it["track"] + 1) * hh
            else:
                hh = (bot - mid) / na
                y = mid + it["track"] * hh
            col = track_color(kind, it["track"]) if it["kind"] in ("video", "audio") else \
                (C_TEXT if it["kind"] == "text" else C_SHAPE)
            x0, x1 = self._x(it["start"]), self._x(pr.item_end(it))
            p.fillRect(QRectF(x0, y + 0.5, max(1.0, x1 - x0), max(1.0, hh - 1)), col)
        v = self.view
        visible = (v.width() - HEAD) / max(v.pps, 1e-6)
        r = QRectF(self._x(v.scroll_t), 1, max(4.0, self._x(v.scroll_t + visible) - self._x(v.scroll_t)),
                   self.height() - 2)
        p.setPen(QPen(QColor(235, 235, 235, 200), 1))
        p.setBrush(QColor(255, 255, 255, 28))
        p.drawRect(r)
        x = self._x(v.playhead)
        p.setPen(QPen(C_PLAY, 1))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setPen(QColor(120, 120, 120))
        f = QFont(p.font())
        f.setPointSizeF(8)
        p.setFont(f)
        p.drawText(QRectF(8, 0, HEAD - 12, self.height()), Qt.AlignVCenter | Qt.AlignLeft, "Panoramica")
        p.end()

    def _jump(self, x):
        v = self.view
        t = (x - HEAD) / max(1.0, self.width() - HEAD - 6) * self._total()
        visible = (v.width() - HEAD) / max(v.pps, 1e-6)
        v.scroll_t = max(0.0, t - visible / 2)
        v.update()
        self.panel.sync()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._jump(e.position().x())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._jump(e.position().x())


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
        bar.addSpacing(8)
        self.btn_settings = QPushButton("25 fps · 1920×1080")
        self.btn_settings.setToolTip("Impostazioni del progetto: fps, risoluzione, bande nere (Ctrl/Cmd+,)")
        bar.addWidget(self.btn_settings)
        bar.addSpacing(12)
        self.btn_split = QPushButton("✂ Taglia (C)")
        self.btn_snap = QPushButton(" Aggancio")
        self.btn_snap.setIcon(icons.icon("magnet", 16))
        self.btn_snap.setCheckable(True)
        self.btn_snap.setChecked(True)
        self.btn_snap.setToolTip("Aggancio magnetico (N): trascinando o rifilando, i bordi si attaccano a quelli\n"
                                 "delle altre clip su qualsiasi traccia, alla testina e all'inizio.\n"
                                 "Una linea arancione mostra dove si è agganciato. Tieni premuto Maiusc\n"
                                 "mentre trascini per sospenderlo un attimo.")
        self.btn_snap.setStyleSheet("QPushButton:checked { background: #5a4520; border-color: #e8a33d; }")
        self.btn_ripple = QPushButton(" Ripple")
        self.btn_ripple.setIcon(icons.icon("ripple", 16))
        self.btn_ripple.setCheckable(True)
        self.btn_ripple.setToolTip("Ripple (Maiusc+R): eliminando o accorciando un pezzo, quello che viene dopo\n"
                                   "si richiude da solo; allungandolo, fa spazio. Se sotto c'è la musica che\n"
                                   "continua, si richiudono solo le tracce del pezzo.")
        self.btn_ripple.setStyleSheet("QPushButton:checked { background: #5a4520; border-color: #e8a33d; }")
        self.btn_ripple.toggled.connect(lambda on: setattr(self.view, "ripple", on))
        self.btn_slip = QPushButton(" Ritaglio dinamico")
        self.btn_slip.setIcon(icons.icon("slip", 16))
        self.btn_slip.setCheckable(True)
        self.btn_slip.setToolTip("Ritaglio dinamico (Y): con la modalità accesa, tieni premuto il sinistro su una clip\n"
                                 "e trascina: la clip resta dov'è e lunga uguale, ma cambiano il punto di inizio e\n"
                                 "di fine dentro alla clip originale. Il bordo si rifila come sempre.\n"
                                 "Senza la modalità: Ctrl/Cmd+Alt+trascina, oppure i tasti , e .")
        self.btn_slip.setStyleSheet("QPushButton:checked { background: #5a4520; border-color: #e8a33d; }")
        self.btn_slip.toggled.connect(self._slip_toggled)
        self.btn_fit = QPushButton("Adatta")
        self.btn_text = QPushButton(" Scritta")
        self.btn_text.setIcon(icons.icon("text", 16))
        self.btn_text.setToolTip("Aggiungi una scritta alla testina (T)")
        self.btn_box = QPushButton(" Riquadro")
        self.btn_box.setIcon(icons.icon("box", 16))
        self.btn_box.setToolTip("Aggiungi un riquadro colorato alla testina (R): sfondo per le scritte,\n"
                                "fascia, schermo pieno colorato…")
        for b in (self.btn_split, self.btn_snap, self.btn_ripple, self.btn_slip, self.btn_text, self.btn_box):
            bar.addWidget(b)
        bar.addStretch(1)
        self.btn_fit.setToolTip("Mostra tutta la timeline (Maiusc+Z)")
        bar.addWidget(self.btn_fit)
        bar.addSpacing(14)
        self.zoom_label = QLabel("Zoom")
        bar.addWidget(self.zoom_label)
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(0, 1000)
        self.zoom.setMinimumWidth(60)
        self.zoom.setMaximumWidth(160)
        bar.addWidget(self.zoom)
        lay.addLayout(bar)
        row = QHBoxLayout()
        row.setSpacing(0)
        self.view = TimelineView(host)
        self.overview = TimelineOverview(self.view, self)
        lay.addWidget(self.overview)
        self.vbar = QScrollBar(Qt.Vertical)
        row.addWidget(self.view, 1)
        row.addWidget(self.vbar)
        lay.addLayout(row, 1)
        self.hbar = QScrollBar(Qt.Horizontal)
        lay.addWidget(self.hbar)
        self._sync = False
        # barra stretta: solo icone, così la colonna di sinistra si può allargare a piacere
        self.setMinimumWidth(380)
        self._compact = None
        self._full_text = {b: b.text() for b in (self.btn_split, self.btn_snap, self.btn_ripple, self.btn_slip,
                                                   self.btn_text, self.btn_box)}
        self.hbar.valueChanged.connect(self._hscroll)
        self.vbar.valueChanged.connect(self._vscroll)
        self.zoom.valueChanged.connect(self._zoomed)
        self.btn_snap.toggled.connect(self._snap)
        self.btn_fit.clicked.connect(self.view.zoom_fit)
        self.btn_split.clicked.connect(lambda: self.view.action.emit("split", list(self.view.selection)))

    def _set_compact(self, on):
        if on == self._compact:
            return
        self._compact = on
        short = {self.btn_split: "✂"}
        for b, text in self._full_text.items():
            b.setText(short.get(b, "") if on else text)
        self.title.setVisible(not on)
        self.zoom_label.setVisible(not on)

    def _snap(self, on):
        self.view.snapping = on

    def _slip_toggled(self, on):
        self.view.slip_mode = on
        self.view.setCursor(Qt.ArrowCursor)

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
        self.overview.update()
        import math

        self.zoom.setValue(int(1000 * math.log(v.pps / 2.0) / math.log(400.0)))
        self._sync = False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._set_compact(self.width() < 1060)
        self.sync()
