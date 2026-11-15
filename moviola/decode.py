"""Decodifica dei fotogrammi con PyAV (FFmpeg), solo in lettura.

Ogni FrameSource tiene aperto un file e decodifica in avanti finché può:
per la riproduzione basta proseguire; per i salti lunghi o all'indietro
fa una ricerca sul fotogramma chiave più vicino.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

import numpy as np


class FrameSource:
    def __init__(self, path: str):
        import av

        self.path = path
        self.container = av.open(path)
        self.stream = self.container.streams.video[0]
        try:
            self.stream.thread_type = "AUTO"
        except Exception:
            pass
        self.tb = float(self.stream.time_base)
        st = self.stream.start_time
        self.t_offset = float(st * self.stream.time_base) if st is not None else 0.0
        rate = self.stream.average_rate or self.stream.guessed_rate or 25
        self.fps = float(rate) if rate else 25.0
        self.half = 0.5 / self.fps
        self.width = self.stream.codec_context.width
        self.height = self.stream.codec_context.height
        self._iter = None
        self._pos = -1e9  # tempo dell'ultimo fotogramma decodificato
        self._pending = None  # fotogramma letto ma non ancora usato
        self._last = None  # (t, h, array)
        self.last_time = 0.0  # tempo reale dell'ultimo fotogramma restituito
        self._back = ([], [], 0)  # (tempi, fotogrammi, altezza) del gruppo letto per andare all'indietro

    def close(self):
        try:
            self.container.close()
        except Exception:
            pass

    def _seek(self, t: float):
        target = int(max(0.0, t + self.t_offset - 0.05) / self.tb)
        self.container.seek(target, stream=self.stream, backward=True, any_frame=False)
        self._iter = self.container.decode(self.stream)
        self._pos = -1e9
        self._pending = None

    def _frame_time(self, frame) -> float:
        if frame.pts is not None:
            return frame.pts * self.tb - self.t_offset
        return (frame.time or 0.0) - self.t_offset

    def _convert(self, frame, height: int) -> np.ndarray:
        h = min(height, self.height) if height else self.height
        h -= h % 2
        w = int(round(self.width * h / max(self.height, 1)))
        w -= w % 2
        if (w, h) == (frame.width, frame.height):
            return frame.to_ndarray(format="rgb24")
        return frame.reformat(width=w, height=h, format="rgb24").to_ndarray()

    def get_back(self, t: float, height: int = 540) -> np.ndarray | None:
        """Come get(), per la riproduzione all'indietro: si decodifica in avanti dal
        fotogramma chiave fino a t e si tengono tutti i fotogrammi del gruppo, così i
        fotogrammi che servono subito dopo (più indietro) sono già pronti."""
        times, frames, h0 = self._back
        if h0 != height:
            times, frames = [], []
        i = _nearest_back(times, frames, t, self.half)
        if i >= 0:
            return frames[i]
        times, frames = [], []
        self._seek(t)
        limit = 0
        while True:
            try:
                frame = next(self._iter)
            except Exception:
                break
            ft = self._frame_time(frame)
            if ft - self.half > t:
                break
            arr = self._convert(frame, height)
            if not limit:
                limit = max(30, int(120e6 / max(arr.nbytes, 1)))
            times.append(ft)
            frames.append(arr)
            if len(frames) > limit:
                del times[0], frames[0]
        self._iter = None  # la lettura normale riparte da zero
        self._back = (times, frames, height)
        if not frames:
            return None
        self.last_time = times[-1]
        i = _nearest_back(times, frames, t, self.half)
        return frames[i if i >= 0 else len(frames) - 1]

    def get(self, t: float, height: int = 540) -> np.ndarray | None:
        """Fotogramma (RGB uint8) visibile al tempo t (secondi dall'inizio)."""
        if self._last is not None and abs(self._last[0] - t) < self.half and self._last[1] == height:
            return self._last[2]
        if self._iter is None or t < self._pos - 1e-4 or t > self._pos + 2.0:
            self._seek(t)
        prev = None
        while True:
            if self._pending is not None:
                frame, self._pending = self._pending, None
            else:
                try:
                    frame = next(self._iter)
                except StopIteration:
                    frame = None
                except Exception:
                    # errore di decodifica: si riparte da un fotogramma chiave
                    self._seek(t + 1.0 / self.fps)
                    try:
                        frame = next(self._iter)
                    except Exception:
                        frame = None
            if frame is None:  # fine del file: tengo l'ultimo
                if prev is not None:
                    arr = self._convert(prev, height)
                    self._last = (t, height, arr)
                    self.last_time = self._frame_time(prev)
                    return arr
                return self._last[2] if self._last else None
            ft = self._frame_time(frame)
            self._pos = ft
            if ft + self.half > t:
                # il fotogramma precedente era quello giusto?
                if ft - self.half > t and prev is not None:
                    self._pending = frame
                    frame = prev
                    ft = self._frame_time(prev)
                arr = self._convert(frame, height)
                self._last = (t, height, arr)
                self.last_time = ft
                return arr
            prev = frame


def _nearest_back(times, frames, t, half):
    """Il fotogramma più vicino a t, se c'è davvero (entro mezzo fotogramma)."""
    import bisect

    i = bisect.bisect_right(times, t + half) - 1
    if i >= 0 and abs(times[i] - t) <= half * 1.01 + 1e-6:
        return i
    return -1


class SourcePool:
    """Tiene aperti gli ultimi file usati (apertura costosa)."""

    def __init__(self, size: int = 6):
        self.size = size
        self._open: OrderedDict[str, FrameSource] = OrderedDict()
        self.lock = threading.Lock()
        self.errors: dict[str, str] = {}

    def get(self, path: str, slot: int = 0) -> FrameSource | None:
        """slot > 0 apre un secondo lettore dello stesso file (transizioni tra due
        parti della stessa clip, senza saltare avanti e indietro)."""
        key = (path, slot)
        fs = self._open.get(key)
        if fs is not None:
            self._open.move_to_end(key)
            return fs
        try:
            fs = FrameSource(path)
        except Exception as e:
            self.errors[path] = str(e)
            return None
        self._open[key] = fs
        while len(self._open) > self.size:
            _, old = self._open.popitem(last=False)
            old.close()
        return fs

    def drop(self, path: str):
        for key in [k for k in self._open if k[0] == path]:
            self._open.pop(key).close()

    def clear(self):
        for fs in self._open.values():
            fs.close()
        self._open.clear()
