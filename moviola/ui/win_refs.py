"""Scheda COLORE: riferimenti salvati nel progetto, confronto (affiancato, tendina, lampeggio) e parade RGB."""
from __future__ import annotations

import base64
import time
import uuid

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QInputDialog

from .. import scopes
from .common import PAGE_COLOR

MAX_REFS = 24
REF_WIDTH = 640


def _qimage(arr):
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def _array(qimg):
    q = qimg.convertToFormat(QImage.Format_RGB888)
    w, h, bpl = q.width(), q.height(), q.bytesPerLine()
    buf = np.frombuffer(q.constBits(), np.uint8, count=bpl * h).reshape(h, bpl)
    return np.ascontiguousarray(buf[:, :w * 3].reshape(h, w, 3))


def encode_jpeg(arr, quality=88) -> str:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    _qimage(arr).save(buf, "JPEG", quality)
    buf.close()
    return base64.b64encode(bytes(ba)).decode("ascii")


def decode_jpeg(text):
    return QImage.fromData(base64.b64decode(text), "JPEG")


class RefsMixin:
    # ----------------------------------------------------------- stato
    def _refs(self):
        return self.project.data.setdefault("references", [])

    def _ref_entry(self, rid=None):
        rid = rid or self.color_page.current_ref_id()
        return next((r for r in self._refs() if r["id"] == rid), None)

    def _ref_qimage(self, entry):
        c = self.__dict__.setdefault("_ref_cache", {})
        got = c.get(entry["id"])
        if got is None:
            q = decode_jpeg(entry["jpg"])
            got = c[entry["id"]] = (q, _array(q))
        return got

    # ------------------------------------------------------- riferimenti
    def ref_capture(self):
        if getattr(self, "page", None) != PAGE_COLOR:
            self.set_page(PAGE_COLOR)
        img = self.viewer.graded_image(REF_WIDTH)
        if img is None:
            self.statusBar().showMessage("Niente da fissare: nel monitor non c'è nessun fotogramma.", 3000)
            return
        refs = self._refs()
        if len(refs) >= MAX_REFS:
            self.statusBar().showMessage(f"Al massimo {MAX_REFS} riferimenti: eliminane qualcuno.", 4000)
            return
        it = self.project.video_item_at(self.playhead_time())
        n = 1 + max([int(r["name"].split()[-1]) for r in refs if r["name"].startswith("Riferimento ")
                     and r["name"].split()[-1].isdigit()] or [0])
        entry = {"id": uuid.uuid4().hex[:8], "name": f"Riferimento {n}", "jpg": encode_jpeg(img),
                 "origin": (it or {}).get("origin", ""), "date": time.strftime("%d/%m/%Y")}
        refs.append(entry)
        self._mark_dirty()
        self.color_page.fill_refs(select=entry["id"])
        self._apply_compare()
        self.statusBar().showMessage(f"«{entry['name']}» fissato: ora puoi confrontarlo con le altre clip.", 4000)

    def ref_rename(self):
        e = self._ref_entry()
        if e is None:
            return
        name, ok = QInputDialog.getText(self, "Rinomina riferimento", "Nome:", text=e["name"])
        if ok and name.strip():
            e["name"] = name.strip()[:40]
            self._mark_dirty()
            self.color_page.fill_refs(select=e["id"])
            self._apply_compare()

    def ref_delete(self):
        e = self._ref_entry()
        if e is None:
            return
        self._refs().remove(e)
        self.__dict__.get("_ref_cache", {}).pop(e["id"], None)
        self._mark_dirty()
        self.color_page.fill_refs()
        self._apply_compare()

    def refs_loaded(self):
        """Dopo l'apertura di un progetto."""
        self.__dict__["_ref_cache"] = {}
        self.color_page.fill_refs()
        self._apply_compare()

    # ---------------------------------------------------------- confronto
    def set_compare_mode(self, mode):
        self.color_page.set_compare_button(mode)
        self._apply_compare()

    def cycle_compare(self):
        order = [None, "side", "wipe"]
        cur = self.color_page.compare_mode()
        self.set_compare_mode(order[(order.index(cur) + 1) % 3])

    def ref_flash(self, on):
        if self.viewer is None:
            return
        self.viewer.overlay.flash = bool(on) and self._ref_entry() is not None
        self.viewer.overlay.update()
        self._parade_update(force=True)

    def _apply_compare(self):
        """Mette sul monitor il riferimento scelto, o lo toglie (fuori dalla scheda Colore)."""
        cp = getattr(self, "color_page", None)
        if cp is None or self.viewer is None:
            return
        ov = self.viewer.overlay
        e = self._ref_entry() if getattr(self, "page", None) == PAGE_COLOR else None
        mode = cp.compare_mode()
        if e is not None and mode:
            ov.set_compare(self._ref_qimage(e)[0], e["name"], mode)
        else:
            ov.set_compare(None)
        if e is None:
            ov.flash = False
        self._parade_update(force=True)

    # ------------------------------------------------------------- parade
    def set_parade(self, on):
        self.cfg["parade"] = bool(on)
        self.color_page.parade_btn.setChecked(bool(on))
        self._parade_update(force=True)

    def toggle_parade(self):
        if getattr(self, "page", None) == PAGE_COLOR:
            self.set_parade(not self.color_page.parade_btn.isChecked())

    def _parade_update(self, force=False):
        """Ricalcola la parade: subito se il video è fermo, al massimo 4 volte al secondo mentre suona."""
        cp = getattr(self, "color_page", None)
        if cp is None:
            return
        in_color = getattr(self, "page", None) == PAGE_COLOR and self.viewer is not None
        on = in_color and cp.parade_btn.isChecked()
        cp.parade.setVisible(on)
        if not in_color:
            return
        now = time.monotonic()
        last = self.__dict__.get("_parade_t", 0.0)
        if not force and self.engine.playing and now - last < 0.25:
            if not self.__dict__.get("_parade_pending"):
                self.__dict__["_parade_pending"] = True
                QTimer.singleShot(260, self._parade_flush)
            return
        self.__dict__["_parade_t"] = now
        cur = self.viewer.graded_image(220)
        if cur is None:
            cp.parade.set_data(None, None, "", "")
            return
        self.color_panel.curve.set_hist(scopes.histograms(cur))     # istogramma sotto la curva
        if not on:
            return
        clip_p = scopes.parade(cur)
        ref = None
        e = self._ref_entry()
        mode = cp.compare_mode()
        if e is not None and (mode or self.viewer.overlay.flash):
            arr = self._ref_qimage(e)[1]
            st = max(1, arr.shape[1] // 220)
            ref = scopes.parade(np.ascontiguousarray(arr[::st, ::st]))
        lo, hi = scopes.clipping(cur)
        note = ""
        if lo > 0.02:
            note += f"neri tappati {lo * 100:.0f}%  "
        if hi > 0.02:
            note += f"bianchi bruciati {hi * 100:.0f}%"
        cp.parade.set_data(clip_p, ref, e["name"] if e else "", note)
        self.viewer.overlay.parade_img = None
        self.viewer.overlay.update()

    def _parade_flush(self):
        self.__dict__["_parade_pending"] = False
        self._parade_update(force=True)

    # ------------------------------------------------------------ tastiera
    def keyPressEvent(self, e):
        reg = getattr(self, "shortcuts", None)
        if reg is not None and not e.isAutoRepeat() and getattr(self, "page", None) == PAGE_COLOR \
                and "ref_flash" in reg.entries and reg.matches("ref_flash", e):
            self.ref_flash(True)
            self.color_page.flash_btn.setDown(True)
            e.accept()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        reg = getattr(self, "shortcuts", None)
        if reg is not None and not e.isAutoRepeat() and "ref_flash" in reg.entries \
                and e.key() in reg.main_keys("ref_flash"):
            self.ref_flash(False)
            self.color_page.flash_btn.setDown(False)
            e.accept()
            return
        super().keyReleaseEvent(e)
