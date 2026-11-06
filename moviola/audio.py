"""Audio: cache decodificata delle sorgenti, mixer delle tracce e
riproduzione. L'audio fa da "orologio" della riproduzione: il video lo
segue, così restano sempre a sincrono."""
from __future__ import annotations

import os
import threading
import time
import wave

import numpy as np

RATE = 48000
CHANNELS = 2


def db_to_gain(db: float) -> float:
    return float(10 ** (db / 20.0))


class AudioStore:
    """Tiene aperti (in memoria virtuale) i file audio decodificati."""

    def __init__(self):
        self._maps: dict[str, np.ndarray] = {}
        self._paths: dict[str, str] = {}
        self._lock = threading.Lock()

    def register(self, source_id: str, raw_path: str):
        with self._lock:
            self._paths[source_id] = raw_path
            self._maps.pop(source_id, None)

    def get(self, source_id: str):
        with self._lock:
            m = self._maps.get(source_id)
            if m is not None:
                return m
            p = self._paths.get(source_id)
            if not p or not os.path.exists(p) or os.path.getsize(p) < 4:
                return None
            n = os.path.getsize(p) // (2 * CHANNELS)
            m = np.memmap(p, dtype=np.int16, mode="r", shape=(n, CHANNELS))
            self._maps[source_id] = m
            return m

    def ready(self, source_id: str) -> bool:
        return self.get(source_id) is not None


def _add_segment(out, data, out_from, src_from, n, gain, fade_in=0, fade_out=0):
    """Somma n campioni di data (da src_from) in out (da out_from)."""
    if data is None or n <= 0:
        return
    if src_from < 0:
        out_from -= src_from
        n += src_from
        src_from = 0
    n = min(n, data.shape[0] - src_from, out.shape[0] - out_from)
    if n <= 0:
        return
    chunk = data[src_from:src_from + n].astype(np.float32) * (gain / 32768.0)
    out[out_from:out_from + n] += chunk


def mix_timeline(project, store: AudioStore, t0: float, nframes: int, rate: int = RATE) -> np.ndarray:
    """Mixa tutte le tracce audio non mute a partire dal tempo t0."""
    out = np.zeros((nframes, CHANNELS), dtype=np.float32)
    t1 = t0 + nframes / rate
    tracks = project.tracks("audio")
    for it in project.items:
        if it["kind"] != "audio":
            continue
        if it["track"] >= len(tracks) or tracks[it["track"]].get("muted"):
            continue
        s = it["start"]
        e = s + (it["src_out"] - it["src_in"])
        if e <= t0 or s >= t1:
            continue
        data = store.get(it["source"])
        if data is None:
            continue
        a = max(s, t0)
        b = min(e, t1)
        out_from = int(round((a - t0) * rate))
        src_from = int(round((it["src_in"] + (a - s)) * rate))
        n = int(round((b - a) * rate))
        _add_segment(out, data, out_from, src_from, n, db_to_gain(it.get("gain_db", 0.0)))
    return out


def mix_source(store: AudioStore, source_id: str, t0: float, nframes: int, rate: int = RATE) -> np.ndarray:
    out = np.zeros((nframes, CHANNELS), dtype=np.float32)
    data = store.get(source_id)
    if data is not None:
        _add_segment(out, data, 0, int(round(t0 * rate)), nframes, 1.0)
    return out


def write_wav(path: str, project, store: AudioStore, start: float, end: float, progress=None):
    """Esporta il mix della timeline in un WAV 16 bit stereo."""
    total = max(0, int(round((end - start) * RATE)))
    block = RATE * 5
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(RATE)
        done = 0
        while done < total:
            n = min(block, total - done)
            buf = mix_timeline(project, store, start + done / RATE, n)
            pcm = (np.clip(buf, -1.0, 1.0) * 32767).astype("<i2")
            w.writeframes(pcm.tobytes())
            done += n
            if progress:
                progress(done / max(total, 1))


def peaks(raw: np.ndarray, per_second: int = 100) -> np.ndarray:
    """Picchi della forma d'onda (per disegnarla nella timeline)."""
    if raw is None or raw.shape[0] == 0:
        return np.zeros(0, dtype=np.float16)
    step = RATE // per_second
    n = raw.shape[0] // step
    out = np.empty(n, dtype=np.float16)
    for i in range(0, n, 200):
        part = np.asarray(raw[i * step:min(n, i + 200) * step])
        m = np.abs(part.astype(np.int32)).max(axis=1)
        k = m.shape[0] // step
        out[i:i + k] = m[:k * step].reshape(k, step).max(axis=1) / 32768.0
    return out


class Player:
    """Riproduzione audio con orologio preciso.

    render(pos_seconds, nframes) -> array (nframes, 2) è fornito dal motore.
    Se la scheda audio non è disponibile, l'orologio usa il tempo di sistema.
    """

    def __init__(self):
        self._sd = None
        self._stream = None
        self.render = None
        self.playing = False
        self.muted = False
        self._pos = 0  # campioni dall'inizio della riproduzione
        self._start_t = 0.0
        self._dac_pos = None
        self._dac_wall = 0.0
        self._wall_start = 0.0
        self.volume = 1.0
        self.speed = 1.0
        self.error = None
        try:
            import sounddevice as sd

            self._sd = sd
        except Exception as e:  # pragma: no cover
            self.error = f"Audio non disponibile: {e}"

    @property
    def available(self):
        return self._sd is not None

    def _callback(self, outdata, frames, tinfo, status):
        if not self.playing or self.render is None or self.muted:
            outdata.fill(0)
        else:
            try:
                buf = self.render(self._start_t + self._pos / RATE, frames)
                outdata[:] = np.clip(buf * self.volume, -1, 1)
            except Exception:
                outdata.fill(0)
        # momento in cui il primo campione di questo blocco esce dalle casse
        try:
            lat = max(0.0, tinfo.outputBufferDacTime - tinfo.currentTime)
        except Exception:
            lat = 0.0
        self._dac_pos = self._pos
        self._dac_wall = time.perf_counter() + lat
        self._pos += frames

    def start(self, t0: float, muted=False, speed=1.0):
        self.stop()
        self._start_t = t0
        self._pos = 0
        self._dac_pos = None
        self.muted = muted
        self.speed = speed
        self._wall_start = time.perf_counter()
        self.playing = True
        if self._sd is not None and speed == 1.0:
            try:
                self._stream = self._sd.OutputStream(samplerate=RATE, channels=CHANNELS, dtype="float32",
                                                     blocksize=1024, latency="low", callback=self._callback)
                self._stream.start()
            except Exception as e:
                self.error = f"Audio non disponibile: {e}"
                self._stream = None

    def stop(self):
        self.playing = False
        st = self._stream
        self._stream = None
        if st is not None:
            try:
                st.stop()
                st.close()
            except Exception:
                pass

    def clock(self) -> float:
        """Tempo (in secondi) di ciò che si sta ascoltando in questo istante."""
        if self.speed == 1.0 and self._stream is not None and self._dac_pos is not None:
            t = self._start_t + self._dac_pos / RATE + (time.perf_counter() - self._dac_wall)
            return max(self._start_t, t)
        return self._start_t + (time.perf_counter() - self._wall_start) * self.speed
