"""Frecce ← / →: un tocco = un fotogramma; tenendole premute la testina scorre
da sola, sempre più veloce fino a un limite. Vale per il visore attivo
(clip o timeline)."""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit

from .shortcuts import combo_text

HOLD_DELAY = 0.25  # secondi prima che parta lo scorrimento continuo
START_SPEED = 0.5  # volte il tempo reale
MAX_SPEED = 8.0
RAMP = 2.0  # quanto accelera ogni secondo


def _typing(widget) -> bool:
    if isinstance(widget, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)):
        return True
    return isinstance(widget, QComboBox) and widget.isEditable()


class ArrowScrubber(QObject):
    def __init__(self, window, engine, status=None):
        super().__init__(window)
        self.window = window
        self.engine = engine
        self.status = status
        self.dir = 0
        self.t0 = 0.0
        self.last = 0.0
        self.pos = 0.0
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, ev):
        et = ev.type()
        if et not in (QEvent.KeyPress, QEvent.KeyRelease):
            return False
        key = ev.key()
        reg = getattr(self.window, "shortcuts", None)
        if reg is None or "frame_back" not in reg.entries:
            return False
        if et == QEvent.KeyPress:
            combo = combo_text(ev)
            d = -1 if combo in reg.keys("frame_back") else 1 if combo in reg.keys("frame_fwd") else 0
        else:           # al rilascio basta il tasto principale (i modificatori possono essere già stati lasciati)
            d = -1 if key in reg.main_keys("frame_back") else 1 if key in reg.main_keys("frame_fwd") else 0
        if not d or not self.window.isActiveWindow():
            return False
        if _typing(QApplication.focusWidget()):
            return False
        if et == QEvent.KeyPress:
            if ev.isAutoRepeat():
                return True  # la ripetizione la gestiamo noi, con l'accelerazione
            if self.engine.playing:
                self.engine.stop()
            self.dir = d
            self.engine.step(d)
            self.pos = self.engine.position
            self.t0 = self.last = time.monotonic()
            self.timer.start()
            return True
        if not ev.isAutoRepeat() and self.dir == d:
            self.stop()
        return True

    def stop(self):
        self.timer.stop()
        if self.dir and self.status:
            self.status("", 0)
        self.dir = 0
        self.engine.refresh()

    def _tick(self):
        now = time.monotonic()
        dt, self.last = now - self.last, now
        held = now - self.t0
        if held < HOLD_DELAY or not self.dir:
            return
        speed = min(MAX_SPEED, START_SPEED + RAMP * (held - HOLD_DELAY))
        self.pos += self.dir * speed * dt
        dur = self.engine.duration()
        if self.pos <= 0 or self.pos >= dur:
            self.pos = max(0.0, min(self.pos, dur))
            self.engine.seek(self.pos)
            return
        self.engine.seek(self.pos)
        if self.status:
            self.status(f"Scorrimento {'◀' if self.dir < 0 else '▶'} {speed:.1f}×", 800)
