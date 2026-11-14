"""Stabilizzazione: smorza vibrazioni e movimenti bruschi della camera.

Come funziona, in tre passi:

1. **Analisi** (una volta per clip, in background): per ogni fotogramma si
   misura di quanto l'immagine si è spostata e ruotata rispetto al
   precedente, confrontando una griglia di riquadri (correlazione di fase).
   I riquadri che si muovono diversamente dagli altri (persone, auto) vengono
   scartati. Il risultato è il *percorso* della camera, salvato in cache.
2. **Percorso liscio**: il percorso viene ammorbidito (più o meno, secondo
   l'intensità); la differenza tra percorso liscio e percorso reale è la
   correzione da applicare a ogni fotogramma.
3. **Correzione**: ogni fotogramma viene spostato e ruotato di quel tanto, e
   ingrandito un po' (il «ritaglio») per non mostrare i bordi.

Anteprima ed export usano gli stessi numeri: quello che si vede è quello
che si esporta. I file originali non vengono toccati.
"""
from __future__ import annotations

import math
import os
import threading
from collections import OrderedDict

import numpy as np

from . import paths

ANALYSIS_W = 480  # larghezza dei fotogrammi usati per l'analisi
GRID = (4, 3)  # riquadri in orizzontale e verticale
VERSION = 1

DEFAULT_STRENGTH = 0.5
DEFAULT_CROP = 0.10
# quota del margine di ritaglio riservata a spostamenti e a rotazione
_SHIFT_SHARE = 0.60
_ROT_SHARE = 0.30


def stab_path(src_path: str) -> str:
    return os.path.join(paths.cache_dir("stabilizzazione"), paths.file_key(src_path) + ".npz")


def has_analysis(src_path: str) -> bool:
    return os.path.exists(stab_path(src_path))


# ------------------------------------------------------------------ analisi
def _hann(h, w):
    return np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)


def _phase_shift(a, b, win):
    """Di quanto b è spostato rispetto ad a (dx, dy in pixel, con decimali) e
    quanto è netta la misura (0..1)."""
    a = (a - a.mean()) * win
    b = (b - b.mean()) * win
    fa = np.fft.rfft2(a)
    fb = np.fft.rfft2(b)
    r = np.conj(fa) * fb
    r /= np.abs(r) + 1e-6
    c = np.fft.irfft2(r, s=a.shape)
    h, w = c.shape
    iy, ix = np.unravel_index(int(np.argmax(c)), c.shape)
    peak = float(c[iy, ix])

    def sub(m1, m0, p1):  # vertice della parabola per i tre valori attorno al massimo
        d = m1 - 2 * m0 + p1
        return 0.0 if abs(d) < 1e-9 else float(np.clip(0.5 * (m1 - p1) / d, -0.5, 0.5))

    fx = sub(c[iy, (ix - 1) % w], peak, c[iy, (ix + 1) % w])
    fy = sub(c[(iy - 1) % h, ix], peak, c[(iy + 1) % h, ix])
    dx = ix + fx
    dy = iy + fy
    if dx > w / 2:
        dx -= w
    if dy > h / 2:
        dy -= h
    return dx, dy, peak


class _Estimator:
    """Movimento tra due fotogrammi in scala di grigi (stessa dimensione)."""

    def __init__(self, h, w):
        self.h, self.w = h, w
        self.h2, self.w2 = (h // 2) & ~1, (w // 2) & ~1
        self.win_g = _hann(self.h2, self.w2)
        nx, ny = GRID
        self.bw = (w // (nx + 1)) & ~1
        self.bh = (h // (ny + 1)) & ~1
        self.win_b = _hann(self.bh, self.bw)
        mx, my = self.bw // 2, self.bh // 2
        xs = np.linspace(mx, w - mx - self.bw, nx).astype(int)
        ys = np.linspace(my, h - my - self.bh, ny).astype(int)
        self.blocks = [(int(x), int(y)) for y in ys for x in xs]
        self.cx = np.array([x + self.bw / 2 - w / 2 for x, y in self.blocks], np.float64)
        self.cy = np.array([y + self.bh / 2 - h / 2 for x, y in self.blocks], np.float64)

    def _small(self, f):
        f = f[:self.h2 * 2, :self.w2 * 2]
        return (f[0::2, 0::2] + f[1::2, 0::2] + f[0::2, 1::2] + f[1::2, 1::2]) * 0.25

    def motion(self, f0, f1):
        """(dx, dy, rotazione): di quanto il contenuto di f1 è spostato (pixel) e
        ruotato (radianti, positivo = senso orario) rispetto a f0."""
        gx, gy, gq = _phase_shift(self._small(f0), self._small(f1), self.win_g)
        gx *= 2
        gy *= 2
        if gq < 0.03:  # stacco, nero, immagine piatta: misura inaffidabile
            gx = gy = 0.0
        ox, oy = int(round(gx)), int(round(gy))
        dxs, dys, ws = [], [], []
        for (x, y) in self.blocks:
            x0 = min(max(x - ox, 0), self.w - self.bw)
            y0 = min(max(y - oy, 0), self.h - self.bh)
            b0 = f0[y0:y0 + self.bh, x0:x0 + self.bw]
            b1 = f1[y:y + self.bh, x:x + self.bw]
            if b1.std() < 2.0 or b0.std() < 2.0:  # cielo, muro liscio: niente da misurare
                dxs.append(0.0), dys.append(0.0), ws.append(0.0)
                continue
            rx, ry, q = _phase_shift(b0, b1, self.win_b)
            dxs.append((x - x0) + rx)
            dys.append((y - y0) + ry)
            ws.append(max(0.0, q))
        dxs, dys, ws = np.array(dxs), np.array(dys), np.array(ws)
        ok = ws > 0.05
        if ok.sum() < 3:
            return gx, gy, 0.0
        tx = ty = th = 0.0
        for _ in range(3):  # minimi quadrati, scartando i riquadri che non seguono gli altri
            w_ = ws * ok
            sw = w_.sum()
            # dx = tx - th*y ; dy = ty + th*x
            a = np.zeros((3, 3))
            v = np.zeros(3)
            a[0, 0] = a[1, 1] = sw
            a[0, 2] = a[2, 0] = -(w_ * self.cy).sum()
            a[1, 2] = a[2, 1] = (w_ * self.cx).sum()
            a[2, 2] = (w_ * (self.cx ** 2 + self.cy ** 2)).sum()
            v[0] = (w_ * dxs).sum()
            v[1] = (w_ * dys).sum()
            v[2] = (w_ * (dys * self.cx - dxs * self.cy)).sum()
            try:
                tx, ty, th = np.linalg.solve(a, v)
            except np.linalg.LinAlgError:
                return gx, gy, 0.0
            err = np.hypot(dxs - (tx - th * self.cy), dys - (ty + th * self.cx))
            lim = max(0.75, 2.5 * float(np.median(err[ok])))
            new_ok = ok & (err <= lim)
            if new_ok.sum() < 3 or (new_ok == ok).all():
                break
            ok = new_ok
        return float(tx), float(ty), float(th)


def analyze(video_path: str, out_path: str | None = None, progress=None, cancel=None, src_path=None):
    """Misura il percorso della camera in tutto il file e lo salva in cache.
    video_path può essere il proxy (più veloce da leggere): i tempi sono gli stessi.
    Restituisce il percorso del file salvato ("" se annullato)."""
    import av

    out_path = out_path or stab_path(src_path or video_path)
    with av.open(video_path) as c:
        st = c.streams.video[0]
        try:
            st.thread_type = "AUTO"
        except Exception:
            pass
        tb = float(st.time_base)
        t0 = float(st.start_time * st.time_base) if st.start_time is not None else 0.0
        sw, sh = st.codec_context.width, st.codec_context.height
        aw = ANALYSIS_W
        ah = max(32, int(round(aw * sh / max(sw, 1) / 2)) * 2)
        total = float(st.duration * st.time_base) if st.duration else (float(c.duration) / 1e6 if c.duration else 0)
        est = _Estimator(ah, aw)
        times, steps = [], []
        prev = None
        n = 0
        for frame in c.decode(st):
            if cancel is not None and cancel.is_set():
                return ""
            t = (frame.pts * tb - t0) if frame.pts is not None else ((frame.time or 0.0) - t0)
            g = frame.reformat(width=aw, height=ah, format="gray").to_ndarray().astype(np.float32)
            steps.append((0.0, 0.0, 0.0) if prev is None else est.motion(prev, g))
            times.append(t)
            prev = g
            n += 1
            if progress and total > 0 and n % 10 == 0:
                progress(min(1.0, max(0.0, t / total)))
    if not times:
        raise ValueError("nessun fotogramma da analizzare")
    steps = np.array(steps, np.float64)
    path = np.cumsum(steps, axis=0)
    path[:, 0] /= aw  # frazioni di larghezza e altezza: valgono a ogni risoluzione
    path[:, 1] /= ah
    tmp = out_path + ".part.npz"
    np.savez_compressed(tmp, version=VERSION, t=np.array(times, np.float64), path=path.astype(np.float32),
                        aspect=float(sw) / max(sh, 1))
    os.replace(tmp, out_path)
    with _lock:
        _tracks.pop(out_path, None)
    if progress:
        progress(1.0)
    return out_path


# ----------------------------------------------------------- percorso liscio
def sigma_seconds(strength: float) -> float:
    """Quanto si ammorbidisce: da 0,15 s (solo vibrazioni) a 3 s (camera quasi ferma)."""
    return 0.15 * 20.0 ** min(1.0, max(0.0, float(strength)))


def _smooth(path, sigma_frames):
    r = int(max(1, math.ceil(sigma_frames * 3)))
    x = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (x / max(sigma_frames, 1e-3)) ** 2)
    k /= k.sum()
    n = len(path)
    out = np.empty_like(path)
    for j in range(path.shape[1]):
        col = path[:, j]
        pad = min(r, n - 1)
        p = np.pad(col, pad, mode="reflect", reflect_type="odd") if pad > 0 else col
        if pad < r:
            p = np.pad(p, r - pad, mode="edge")
        out[:, j] = np.convolve(p, k, mode="valid")
    return out


def _limit(c, m):
    """Limite morbido: fino al 60% del margine non tocca nulla, poi frena.
    m può cambiare da un fotogramma all'altro."""
    m = np.maximum(np.asarray(m, np.float64), 1e-9)
    u = np.asarray(c, np.float64) / m
    x = np.abs(u)
    g = np.where(x > 0.6, np.sign(u) * (0.6 + 0.4 * np.tanh((x - 0.6) / 0.4)), u)
    return g * m


def _envelope(x, radius):
    """Massimo di |x| in una finestra attorno a ogni fotogramma, ammorbidito."""
    x = np.abs(x)
    n = len(x)
    p = np.pad(x, radius, mode="edge")
    out = x.copy()
    for k in range(2 * radius + 1):
        np.maximum(out, p[k:k + n], out=out)
    return _smooth(out[:, None], max(1.0, radius / 2))[:, 0]


def margins(crop: float, aspect: float):
    """Zoom e margini concessi: (zoom, spostamento max in frazione, rotazione max in rad)."""
    z = 1.0 + min(0.5, max(0.02, float(crop)))
    ar = max(aspect, 1.0 / max(aspect, 1e-6))
    return z, _SHIFT_SHARE * (z - 1) / (2 * z), _ROT_SHARE * (z - 1) / ar


class Track:
    """Analisi di una clip: tempi dei fotogrammi e percorso della camera."""

    def __init__(self, t, path, aspect):
        self.t = np.asarray(t, np.float64)
        self.path = np.asarray(path, np.float64)
        self.aspect = float(aspect)
        d = np.diff(self.t)
        d = d[d > 0]
        self.fps = 1.0 / float(np.median(d)) if len(d) else 25.0
        self._corr: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def corrections(self, strength=DEFAULT_STRENGTH, crop=DEFAULT_CROP):
        """Per ogni fotogramma: (dx, dy, rotazione) da applicare, e lo zoom.
        dx, dy in frazioni di larghezza/altezza del fotogramma *prima* dello zoom."""
        key = (round(float(strength), 3), round(float(crop), 3))
        with self._lock:
            hit = self._corr.get(key)
            if hit is not None:
                self._corr.move_to_end(key)
                return hit
        z, m, mth = margins(crop, self.aspect)
        c = _smooth(self.path, sigma_seconds(strength) * self.fps) - self.path
        # se la correzione non sta nel margine si rinuncia alla parte lenta
        # (la camera che si sposta davvero), non al tremolio: la parte lenta
        # viene frenata dolcemente, quella rapida resta tolta per intero
        slow = _smooth(c, 0.2 * self.fps)
        fast = c - slow
        rad = max(2, int(round(self.fps * 0.6)))
        for j, lim in ((0, m), (1, m), (2, mth)):
            f = _limit(fast[:, j], 0.85 * lim)
            room = np.maximum(lim - _envelope(f, rad), 0.15 * lim)  # quel che resta per la parte lenta
            c[:, j] = np.clip(f + _limit(slow[:, j], room), -lim, lim)
        res = (c, z)
        with self._lock:
            self._corr[key] = res
            while len(self._corr) > 6:
                self._corr.popitem(last=False)
        return res

    def index_at(self, t: float) -> int:
        i = int(np.searchsorted(self.t, t))
        if i <= 0:
            return 0
        if i >= len(self.t):
            return len(self.t) - 1
        return i if (self.t[i] - t) < (t - self.t[i - 1]) else i - 1

    def at(self, t: float, strength=DEFAULT_STRENGTH, crop=DEFAULT_CROP):
        """(dx, dy, rotazione, zoom) per il fotogramma al tempo t (secondi dall'inizio del file)."""
        c, z = self.corrections(strength, crop)
        dx, dy, th = c[self.index_at(t)]
        return float(dx), float(dy), float(th), z


_tracks: OrderedDict = OrderedDict()
_lock = threading.Lock()


def track_for(src_path: str) -> Track | None:
    """Analisi già fatta per questo file (None se manca)."""
    p = stab_path(src_path)
    with _lock:
        tr = _tracks.get(p)
        if tr is not None:
            _tracks.move_to_end(p)
            return tr
    if not os.path.exists(p):
        return None
    try:
        with np.load(p) as d:
            if int(d["version"]) != VERSION:
                return None
            tr = Track(d["t"], d["path"], float(d["aspect"]))
    except Exception:
        return None
    paths.touch(p)
    with _lock:
        _tracks[p] = tr
        while len(_tracks) > 24:
            _tracks.popitem(last=False)
    return tr


def params(item) -> tuple[float, float]:
    s = item.get("stab_strength")
    c = item.get("stab_crop")
    return (DEFAULT_STRENGTH if s is None else float(s)), (DEFAULT_CROP if c is None else float(c))


def item_correction(item, src, t_src):
    """Correzione per un pezzo della timeline al tempo t_src del suo file,
    oppure None (stabilizzazione spenta o analisi non ancora pronta)."""
    if not item.get("stab") or src is None:
        return None
    tr = track_for(src["path"])
    if tr is None:
        return None
    return tr.at(t_src, *params(item))


# ---------------------------------------------------------------- anteprima
def warp(img: np.ndarray, dx: float, dy: float, theta: float, zoom: float) -> np.ndarray:
    """Applica la correzione a un fotogramma RGB (stessa dimensione in uscita):
    ingrandisce dal centro, ruota e sposta."""
    from PySide6.QtGui import QImage, QPainter

    h, w = img.shape[:2]
    src = QImage(img.data, w, h, 3 * w, QImage.Format_RGB888)
    out = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.setCompositionMode(QPainter.CompositionMode_Source)
    p.translate(w / 2 + dx * zoom * w, h / 2 + dy * zoom * h)
    p.rotate(math.degrees(theta))
    p.scale(zoom, zoom)
    p.translate(-w / 2, -h / 2)
    p.drawImage(0, 0, src)
    p.end()
    buf = np.frombuffer(out.constBits(), np.uint8, count=h * out.bytesPerLine())
    bgra = buf.reshape(h, out.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
    return np.ascontiguousarray(bgra[:, :, 2::-1])


# -------------------------------------------------------------------- export
def video_lead(src_path: str) -> float:
    """Ritardo del primo fotogramma video rispetto all'inizio del file (di solito 0):
    FFmpeg conta i tempi dall'inizio del file, l'analisi dal primo fotogramma."""
    try:
        import av

        with av.open(src_path) as c:
            st = c.streams.video[0]
            v0 = float(st.start_time * st.time_base) if st.start_time is not None else 0.0
            c0 = float(c.start_time) / 1e6 if c.start_time is not None else 0.0
            return v0 - c0
    except Exception:
        return 0.0


def supersample(width: int) -> int:
    """Di quanto si ingrandisce per lavorare (lo spostamento va al pixel intero:
    ingrandendo si ottiene la precisione di una frazione di pixel)."""
    return int(min(4, max(1, round(3840 / max(width, 1)))))


def export_filters(track: Track, item, name: str, w: int, h: int, src_t: float, src_end: float, lead: float,
                   cmd_path: str):
    """Filtri FFmpeg che stabilizzano un tratto di clip e file dei comandi
    (uno per fotogramma). Vanno messi subito dopo l'ingresso, prima di
    cambiare velocità: i tempi sono quelli del file. L'uscita è larga w×h."""
    strength, crop = params(item)
    c, z = track.corrections(strength, crop)
    s = supersample(w)
    aw = int(round(w * s * z / 2)) * 2
    ah = int(round(h * s * z / 2)) * 2
    cw, ch = w * s, h * s
    x0, y0 = (aw - cw) / 2.0, (ah - ch) / 2.0
    k0 = max(0, track.index_at(src_t) - 2)
    k1 = min(len(track.t) - 1, track.index_at(src_end) + 2)
    lines = []
    for k in range(k0, k1 + 1):
        a = 0.0 if k == k0 else (track.t[k - 1] + track.t[k]) / 2 + lead - src_t
        b = None if k == k1 else (track.t[k] + track.t[k + 1]) / 2 + lead - src_t
        if b is not None and b <= 0:
            continue
        a = max(0.0, a)
        dx, dy, th = c[k]
        x = int(round(min(max(x0 - dx * z * cw, 0), aw - cw)))
        y = int(round(min(max(y0 - dy * z * ch, 0), ah - ch)))
        rng = f"{a:.6f}" if b is None else f"{a:.6f}-{b:.6f}"
        lines.append(f"{rng} rotate@{name} a {th:.6f}, crop@{name} x {x}, crop@{name} y {y};")
    with open(cmd_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return (f"sendcmd=f={os.path.basename(cmd_path)},scale={aw}:{ah}:flags=bicubic,format=rgb24,"
            f"rotate@{name}=a=0:ow=iw:oh=ih:bilinear=1,"
            f"crop@{name}=w={cw}:h={ch}:x={int(round(x0))}:y={int(round(y0))},"
            f"scale={w}:{h}:flags=bicubic")
