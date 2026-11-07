"""Velocità dei pezzi: costante oppure variabile secondo una curva.

La curva è una lista di punti [u, v]: u va da 0 (inizio del pezzo) a 1
(fine), v è la velocità (1 = normale, 0.5 = rallentato, 2 = accelerato).
La parte di file mostrata resta la stessa: cambia solo il ritmo, e quindi
la durata in timeline (= durata sorgente / velocità media).
Nessuna dipendenza da Qt.
"""
from __future__ import annotations

from collections import OrderedDict

import numpy as np

from .color import _pchip

V_MIN, V_MAX = 0.1, 8.0
N = 1024
_cache: OrderedDict = OrderedDict()

PRESETS = {
    "Rampa: lento al centro": [[0.0, 2.0], [0.35, 2.0], [0.5, 0.3], [0.65, 2.0], [1.0, 2.0]],
    "Rallenta": [[0.0, 1.0], [0.5, 1.0], [1.0, 0.3]],
    "Accelera": [[0.0, 0.5], [0.5, 1.0], [1.0, 3.0]],
    "Veloce poi normale": [[0.0, 3.0], [0.5, 3.0], [0.7, 1.0], [1.0, 1.0]],
}


def is_static(it) -> bool:
    """Scritte, riquadri e fermi immagine: la durata è libera (src_out - src_in)
    e non scorre nessun file."""
    return it.get("kind") in ("text", "shape") or it.get("freeze") is not None


def curve_points(it):
    pts = it.get("speed_curve")
    if pts and len(pts) >= 2:
        return pts
    return None


def _key(pts):
    return tuple((round(float(u), 6), round(float(v), 6)) for u, v in pts)


def table(pts):
    """(u, v, integrale cumulato di v) campionati su N punti."""
    k = _key(pts)
    tab = _cache.get(k)
    if tab is not None:
        _cache.move_to_end(k)
        return tab
    pts = sorted(([float(u), min(V_MAX, max(V_MIN, float(v)))] for u, v in pts), key=lambda p: p[0])
    u = np.linspace(0.0, 1.0, N)
    lv = _pchip([p[0] for p in pts], [np.log2(p[1]) for p in pts], u)
    v = np.clip(2.0 ** lv, V_MIN, V_MAX)
    cum = np.concatenate([[0.0], np.cumsum((v[1:] + v[:-1]) * 0.5 * np.diff(u))])
    tab = (u, v, cum)
    _cache[k] = tab
    while len(_cache) > 256:
        _cache.popitem(last=False)
    return tab


def const_speed(it) -> float:
    return float(it.get("speed", 1.0) or 1.0)


def mean_speed(it) -> float:
    if is_static(it):
        return 1.0
    pts = curve_points(it)
    if pts is None:
        return const_speed(it)
    return float(table(pts)[2][-1])


def item_dur(it) -> float:
    if is_static(it):
        return it["src_out"] - it["src_in"]
    return (it["src_out"] - it["src_in"]) / mean_speed(it)


def src_time(it, t):
    """Tempo sorgente al tempo t della timeline (t può essere un array)."""
    if it.get("freeze") is not None:
        f = float(it["freeze"])
        return np.full(np.shape(t), f) if isinstance(t, np.ndarray) else f
    pts = curve_points(it)
    if pts is None:
        return it["src_in"] + (np.asarray(t) - it["start"]) * const_speed(it) if isinstance(t, np.ndarray) \
            else it["src_in"] + (t - it["start"]) * const_speed(it)
    T = item_dur(it)
    uk, vk, cum = table(pts)
    u = (np.asarray(t, dtype=np.float64) - it["start"]) / max(T, 1e-9)
    uc = np.clip(u, 0.0, 1.0)
    base = np.interp(uc, uk, cum)
    # fuori dal pezzo si prosegue alla velocità dell'estremo
    extra = np.where(u < 0, u * vk[0], np.where(u > 1, (u - 1) * vk[-1], 0.0))
    res = it["src_in"] + T * (base + extra)
    return float(res) if np.ndim(res) == 0 else res


def tl_time(it, s):
    """Inverso di src_time: tempo in timeline in cui si vede il tempo sorgente s."""
    if is_static(it):
        return it["start"]
    pts = curve_points(it)
    if pts is None:
        return it["start"] + (s - it["src_in"]) / const_speed(it)
    knots = inverse_knots(it)
    ss = [k[0] for k in knots]
    xs = [k[1] for k in knots]
    return it["start"] + float(np.interp(s - it["src_in"], ss, xs))


def speed_at(it, t) -> float:
    pts = curve_points(it)
    if pts is None:
        return const_speed(it)
    uk, vk, _ = table(pts)
    return float(np.interp((t - it["start"]) / max(item_dur(it), 1e-9), uk, vk))


def sub_curve(pts, u0, u1, n=24):
    """La parte [u0, u1] di una curva, riportata su [0, 1] (per tagli e
    sovrascritture: il movimento resta identico)."""
    uk, vk, _ = table(pts)
    span = max(u1 - u0, 1e-9)
    us = set(np.linspace(u0, u1, n).tolist())
    us.update(float(p[0]) for p in pts if u0 < p[0] < u1)
    out = []
    for u in sorted(us):
        out.append([round((u - u0) / span, 6), round(float(np.interp(u, uk, vk)), 5)])
    out[0][0] = 0.0
    out[-1][0] = 1.0
    return out


def inverse_knots(it, n=64):
    """Coppie (tempo sorgente, tempo timeline) relative all'inizio del pezzo,
    per ricostruire nell'export il tempo di uscita di ogni fotogramma."""
    T = item_dur(it)
    xs = np.linspace(0.0, T, n + 1)
    ss = np.asarray(src_time(it, it["start"] + xs)) - it["src_in"]
    return list(zip(ss.tolist(), xs.tolist()))
