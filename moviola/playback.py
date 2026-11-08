"""Motore di riproduzione: un thread decodifica i fotogrammi in anticipo,
l'audio fa da orologio e l'interfaccia mostra il fotogramma giusto
nell'istante giusto (scartando quelli in ritardo: l'anteprima resta
fluida e a sincrono anche su un computer lento)."""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict, deque

import numpy as np
from PySide6.QtCore import QObject, QTimer, Qt, Signal

from . import audio, color
from .decode import SourcePool

# risoluzione dell'anteprima rispetto a quella del progetto (l'export usa sempre quella piena)
PREVIEW_SCALES = {"piena": 1.0, "meta": 0.5, "terzo": 1 / 3, "quarto": 0.25}


def preview_height(project_h: int, quality: str) -> int:
    h = int(round(project_h * PREVIEW_SCALES.get(quality, 0.5)))
    return max(144, h - h % 2)


class Frame:
    __slots__ = ("t", "img", "lut", "lut_key", "graded", "hist", "label", "fade", "fade_color", "xf", "texts",
                 "item_id", "bars", "layers", "mode")

    def __init__(self, t, img=None, lut=None, lut_key="identity", graded=False, hist=None, label=""):
        self.t = t
        self.img = img
        self.lut = lut
        self.lut_key = lut_key
        self.graded = graded
        self.hist = hist
        self.label = label
        self.fade = 0.0  # dissolvenza 0..1 verso fade_color
        self.fade_color = (0.0, 0.0, 0.0)
        self.xf = (1.0, 0.0, 0.0)  # zoom, spostamento x, y (frazioni del fotogramma)
        self.texts = []  # [(scritta, opacità)]
        self.item_id = None
        self.bars = (0.0, 0.0)  # bande nere: (del pezzo, generali)
        self.layers = None  # transizioni: due immagini da comporre
        self.mode = "timeline"  # quale visore deve mostrarlo


def luma_hist(img: np.ndarray, step: int = 4) -> np.ndarray:
    small = img[::step, ::step]
    y = (small[..., 0].astype(np.uint16) * 54 + small[..., 1].astype(np.uint16) * 183
         + small[..., 2].astype(np.uint16) * 19) >> 8
    return np.bincount(y.ravel(), minlength=256).astype(np.float32)


class Engine(QObject):
    frame_ready = Signal(object)
    position_changed = Signal(float)
    playing_changed = Signal(bool)

    QMAX = 8

    def __init__(self, project_getter, store: audio.AudioStore, path_for):
        super().__init__()
        self.get_project = project_getter
        self.store = store
        self.path_for = path_for  # sorgente -> percorso (proxy o originale)
        self.pool = SourcePool()
        self.player = audio.Player()
        self.player.render = self._render_audio

        self.mode = "timeline"  # oppure "source"
        self.source_id = None
        self.position = 0.0
        self.source_positions: dict[str, float] = {}
        self.speed = 0.0
        self.quality = "meta"
        self.cpu_grade = False  # True se la scheda video non è disponibile
        self.bypass = False  # prima/dopo
        self.fx_off = False  # correzione colore spenta nell'anteprima (pulsante FX)

        self._lut_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._cpu_cache: OrderedDict[str, color.CpuLut] = OrderedDict()
        self._queue: deque[Frame] = deque()
        self._qlock = threading.Lock()
        self._cond = threading.Condition()
        self._still_req = None
        self._drops: list[str] = []
        self._play_gen = 0
        self._next_t = 0.0
        self._running = True
        self._last_emit = 0.0
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(4)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------ comandi
    @property
    def playing(self):
        return self.speed != 0.0

    def set_mode(self, mode, source_id=None):
        if self.playing:
            self.stop()
        if self.mode == "source" and self.source_id:
            self.source_positions[self.source_id] = self.position
        self.mode = mode
        self.source_id = source_id
        if mode == "source":
            self.position = self.source_positions.get(source_id, 0.0)
        else:
            self.position = self.source_positions.get("__timeline__", 0.0)
        self.position_changed.emit(self.position)
        self.refresh()

    def remember_timeline_pos(self):
        if self.mode == "timeline":
            self.source_positions["__timeline__"] = self.position

    def duration(self):
        pr = self.get_project()
        if self.mode == "source":
            src = pr.source(self.source_id)
            return src["duration"] if src else 0.0
        return pr.timeline_end()

    def fps(self):
        pr = self.get_project()
        if self.mode == "source":
            src = pr.source(self.source_id)
            if src:
                return src["fps"] or pr.fps
        return pr.fps

    def frame_step(self):
        return 1.0 / max(self.fps(), 1.0)

    def seek(self, t, refresh=True):
        dur = self.duration()
        fr = self.frame_step()
        t = max(0.0, min(t, max(0.0, dur - fr * 0.5) if self.mode == "source" else max(dur, 0.0)))
        t = round(t / fr) * fr
        if self.playing:
            spd = self.speed
            self.stop()
            self.position = t
            self.play(spd)
            return
        self.position = t
        if self.mode == "timeline":
            self.source_positions["__timeline__"] = t
        self.position_changed.emit(t)
        if refresh:
            self.refresh()

    def step(self, frames: int):
        self.seek(self.position + frames * self.frame_step())

    def refresh(self):
        """Ridisegna il fotogramma corrente (es. dopo una modifica colore)."""
        if self.playing:
            return
        with self._cond:
            self._still_req = self.position
            self._cond.notify()

    def play(self, speed=1.0):
        dur = self.duration()
        if dur <= 0:
            return
        if speed > 0 and self.position >= dur - self.frame_step():
            self.position = 0.0
        with self._qlock:
            self._queue.clear()
        with self._cond:
            self._play_gen += 1
            self._next_t = self.position
            self.speed = speed
            self._still_req = None
            self._cond.notify()
        self.player.start(self.position, muted=(speed != 1.0), speed=speed)
        self.timer.start()
        self.playing_changed.emit(True)

    def stop(self):
        if not self.playing:
            return
        self.position = max(0.0, min(self.player.clock(), self.duration()))
        self.player.stop()
        with self._cond:
            self.speed = 0.0
            self._play_gen += 1
        self.timer.stop()
        with self._qlock:
            self._queue.clear()
        fr = self.frame_step()
        self.position = round(self.position / fr) * fr
        if self.mode == "timeline":
            self.source_positions["__timeline__"] = self.position
        self.position_changed.emit(self.position)
        self.playing_changed.emit(False)
        self.refresh()

    def toggle(self):
        if self.playing:
            self.stop()
        else:
            self.play(1.0)

    def shuttle(self, direction: int):
        """J / L: ogni pressione aumenta la velocità (1x, 2x, 4x, 8x)."""
        if direction == 0:
            self.stop()
            return
        cur = self.speed
        if cur == 0 or (cur > 0) != (direction > 0):
            spd = 1.0 * direction
        else:
            spd = min(abs(cur) * 2, 8.0) * direction
        if self.playing:
            self.stop()
        self.play(spd)

    def shutdown(self):
        self._running = False
        self.player.stop()
        with self._cond:
            self._cond.notify()
        self.pool.clear()

    def invalidate_source(self, path=None):
        """Da chiamare quando cambia il file usato (es. proxy pronto)."""
        with self._cond:
            self._drops.append(path or "*")
        self.refresh()

    # ------------------------------------------------------------ orologio
    def _tick(self):
        t = self.player.clock()
        dur = self.duration()
        frame = None
        sign = 1 if self.speed > 0 else -1
        with self._qlock:
            while self._queue and (self._queue[0].t - t) * sign <= 1e-4:
                frame = self._queue.popleft()
        if frame is not None:
            self.frame_ready.emit(frame)
        self.position = t
        now = time.perf_counter()
        if now - self._last_emit > 0.03:
            self._last_emit = now
            self.position_changed.emit(max(0.0, min(t, dur)))
        if (self.speed > 0 and t >= dur) or (self.speed < 0 and t <= 0):
            self.position = max(0.0, min(t, dur))
            self.stop()
            if self.speed == 0 and self.position >= dur:
                self.seek(dur - self.frame_step() if self.mode == "source" else dur)

    def _render_audio(self, pos, nframes):
        pr = self.get_project()
        if self.mode == "source":
            return audio.mix_source(self.store, self.source_id, pos, nframes)
        return audio.mix_timeline(pr, self.store, pos, nframes)

    # ------------------------------------------------------------- worker
    def _worker(self):
        while self._running:
            with self._cond:
                while self._running and self._still_req is None and not self._drops and not (
                        self.speed != 0 and len(self._queue) < self.QMAX):
                    self._cond.wait(0.05)
                if not self._running:
                    return
                drops, self._drops = self._drops, []
                still = self._still_req
                self._still_req = None
                gen = self._play_gen
                speed = self.speed
            for d in drops:
                if d == "*":
                    self.pool.clear()
                else:
                    self.pool.drop(d)
            try:
                if still is not None and speed == 0:
                    f = self._render(still, still=True)
                    if f is not None and self.speed == 0:
                        self.frame_ready.emit(f)
                    continue
                if speed == 0:
                    continue
                step = speed / max(self.fps(), 1.0)
                t = self._next_t
                clock = self.player.clock()
                # se siamo in ritardo saltiamo avanti invece di accumulare ritardo
                if (clock - t) * (1 if speed > 0 else -1) > abs(step) * 1.5:
                    t = clock + 2 * step
                f = self._render(t)
                with self._cond:
                    if gen != self._play_gen:
                        continue
                    self._next_t = t + step
                if f is not None:
                    with self._qlock:
                        self._queue.append(f)
            except Exception:
                import traceback

                traceback.print_exc()
                time.sleep(0.05)

    def _lut_for(self, grades):
        key = color.grade_key(grades)
        if key == "identity" or self.bypass or self.fx_off:
            return None, "identity"
        lut = self._lut_cache.get(key)
        if lut is None:
            lut = color.build_lut(grades, 33)
            self._lut_cache[key] = lut
            while len(self._lut_cache) > 48:
                self._lut_cache.popitem(last=False)
        else:
            self._lut_cache.move_to_end(key)
        return lut, key

    def _render(self, t, still=False):
        mode = self.mode
        f = self._render_base(t, still)
        if f is not None:
            f.mode = mode
        if f is not None and self.mode == "timeline":
            pr = self.get_project()
            f.texts = [(dict(x), pr.opacity_at(x, t)) for x in pr.texts_at(t)]
            it = pr.item(f.item_id) if f.item_id else None
            f.bars = (float(it.get("bars", 0.0) or 0.0) if it else 0.0,
                      float(pr.data["settings"].get("bars", 0.0) or 0.0))

        return f

    def _grade(self, img, grades):
        """(immagine, lut, chiave, già_corretta): sulla scheda video la LUT la applica
        il visore; senza scheda video la applica qui il processore."""
        lut, key = self._lut_for(grades)
        if lut is not None and self.cpu_grade:
            cl = self._cpu_cache.get(key)
            if cl is None:
                cl = color.CpuLut(lut)
                self._cpu_cache[key] = cl
                while len(self._cpu_cache) > 6:
                    self._cpu_cache.popitem(last=False)
            return cl.apply(img), None, "identity", True
        return img, lut, key, False

    def _item_image(self, pr, it, t, h, slot=0):
        """Fotogramma di un pezzo della timeline al tempo t (anche appena fuori dal
        pezzo, per i margini delle transizioni)."""
        src = pr.source(it["source"])
        if src is None or not src.get("has_video"):
            return None
        fs = self.pool.get(self.path_for(src, h), slot)
        if fs is None:
            return None
        st = float(pr.src_time(it, t))
        st = min(max(0.0, st), max(0.0, float(src.get("duration") or 0.0) - 0.5 / max(fs.fps, 1.0)))
        img = fs.get(st, h)
        return None if img is None else np.ascontiguousarray(img)

    def _xf(self, it):
        return (float(it.get("zoom", 1.0) or 1.0), float(it.get("pos_x", 0.0) or 0.0),
                float(it.get("pos_y", 0.0) or 0.0))

    def _hist(self, img, graded, lut):
        if graded or lut is None:
            return luma_hist(img, 4)
        return luma_hist(color.apply_lut_image(np.ascontiguousarray(img[::4, ::4]), lut), 1)

    def _render_transition(self, pr, tr, t, h, still):
        p = (t - tr["t0"]) / max(tr["t1"] - tr["t0"], 1e-9)
        p = min(1.0, max(0.0, p))
        kind = tr["type"]
        a, b = tr["a"], tr["b"]
        mid = (tr["t0"] + tr["t1"]) / 2  # colore animato: fisso durante la transizione (come nell'export)
        if kind in ("dip_black", "dip_white"):
            first = t < tr["cut"]
            it = a if first else b
            img = self._item_image(pr, it, t, h)
            if img is None:
                return Frame(t, None)
            img, lut, key, graded = self._grade(img, pr.grades_at(it, mid))
            fr = Frame(t, img, lut, key, graded, self._hist(img, graded, lut) if still else None,
                       it.get("origin", it["source"]))
            if first:
                fr.fade = (t - tr["t0"]) / max(tr["cut"] - tr["t0"], 1e-9)
            else:
                fr.fade = 1.0 - (t - tr["cut"]) / max(tr["t1"] - tr["cut"], 1e-9)
            fr.fade = min(1.0, max(0.0, fr.fade))
            fr.fade_color = (0.0, 0.0, 0.0) if kind == "dip_black" else (1.0, 1.0, 1.0)
            fr.xf = self._xf(it)
            fr.item_id = it["id"]
            return fr
        layers = []
        for k, it in enumerate((a, b)):
            img = self._item_image(pr, it, t, h, slot=k)
            if img is None:
                continue
            img, lut, key, graded = self._grade(img, pr.grades_at(it, mid))
            ly = {"img": img, "lut": lut, "lut_key": key, "xf": self._xf(it), "alpha": 1.0, "clip": None,
                  "dx": 0.0}
            if k == 1:
                if kind == "dissolve":
                    ly["alpha"] = p
                elif kind == "wipe_l":
                    ly["clip"] = (1.0 - p, 1.0)
                elif kind == "wipe_r":
                    ly["clip"] = (0.0, p)
                elif kind == "push_l":
                    ly["dx"] = 1.0 - p
                elif kind == "push_r":
                    ly["dx"] = -(1.0 - p)
            else:
                if kind == "push_l":
                    ly["dx"] = -p
                elif kind == "push_r":
                    ly["dx"] = p
            layers.append(ly)
        if not layers:
            return Frame(t, None)
        top = layers[-1]
        fr = Frame(t, top["img"], top["lut"], top["lut_key"], False, None, b.get("origin", b["source"]))
        fr.layers = layers
        fr.item_id = (b if t >= tr["cut"] else a)["id"]
        if still:
            fr.hist = self._hist(top["img"], top["lut"] is None, top["lut"])
        return fr

    def _render_base(self, t, still=False):
        pr = self.get_project()
        h = preview_height(pr.size[1], self.quality)
        if self.mode == "timeline":
            it = pr.video_item_at(t)
            if it is None:
                return Frame(t, None, label="")
            tr = pr.transition_at(t)
            if tr is not None:
                return self._render_transition(pr, tr, t, h, still)
            label = it.get("origin", it["source"])
            img = self._item_image(pr, it, t, h)
            if img is None:
                src = pr.source(it["source"])
                miss = src is not None and not os.path.exists(src["path"])
                return Frame(t, None, label=label + (" (file non trovato)" if miss else ""))
            a, b = pr.anim_interval(it, t)
            ga, gb = pr.grades_at(it, a), pr.grades_at(it, b)
            if color.grade_key(ga) == color.grade_key(gb):
                img, lut, key, graded = self._grade(img, ga)
                fr = Frame(t, img, lut, key, graded, self._hist(img, graded, lut) if still else None, label)
            else:
                # colore animato: miscela tra il colore all'inizio e alla fine dell'intervallo
                w = min(1.0, max(0.0, (t - a) / max(b - a, 1e-9)))
                ia, la, ka, gra = self._grade(img, ga)
                ib, lb, kb, grb = self._grade(img, gb)
                if self.cpu_grade:  # senza scheda video: miscela fatta qui
                    mixed = (ia.astype(np.float32) * (1 - w) + ib.astype(np.float32) * w + 0.5).astype(np.uint8)
                    fr = Frame(t, mixed, None, "identity", True, luma_hist(mixed, 4) if still else None, label)
                else:
                    fr = Frame(t, img, la, ka, False, self._hist(img, False, la) if still else None, label)
                    fr.layers = [{"img": img, "lut": la, "lut_key": ka, "xf": self._xf(it), "alpha": 1.0,
                                  "clip": None, "dx": 0.0},
                                 {"img": img, "lut": lb, "lut_key": kb, "xf": self._xf(it), "alpha": w,
                                  "clip": None, "dx": 0.0}]
            fr.fade, fr.fade_color = pr.fade_at(it, t)
            fr.xf = self._xf(it)
            fr.item_id = it["id"]
            return fr
        src = pr.source(self.source_id)
        if src is None or not src.get("has_video"):
            return Frame(t, None, label=src["id"] if src else "")
        fs = self.pool.get(self.path_for(src, h))
        if fs is None:
            return Frame(t, None, label=src["id"] + " (file non trovato)")
        img = fs.get(max(0.0, t), h)
        if img is None:
            return Frame(t, None, label=src["id"])
        img, lut, key, graded = self._grade(np.ascontiguousarray(img), pr.grades_for_source(src["id"]))
        return Frame(t, img, lut, key, graded, self._hist(img, graded, lut) if still else None, src["id"])
