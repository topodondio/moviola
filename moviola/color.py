"""Motore colore "fotografico" di Moviola.

Tutte le regolazioni (bilanciamento del bianco, esposizione, toni, curve,
saturazione, vividezza, HSL per colore) vengono "cotte" in una LUT 3D.
La LUT si ricalcola solo quando cambia un cursore; poi viene applicata a
ogni fotogramma dalla scheda video (anteprima) o da FFmpeg (export).

Il modulo non dipende da Qt: si può usare e testare da solo.
"""
from __future__ import annotations

import copy
import hashlib
import json

import numpy as np

# --------------------------------------------------------------------------
# Parametri
# --------------------------------------------------------------------------

HSL_BANDS = ["Rossi", "Arancioni", "Gialli", "Verdi", "Acqua", "Blu", "Viola", "Magenta"]
HSL_CENTERS = np.array([0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0], dtype=np.float32)
HSL_COLORS = ["#e0443e", "#f08a2c", "#e9cf35", "#4cbf4a", "#3fc6c6", "#3d6fe0", "#8a52d6", "#d64fb4"]

# chiave -> (etichetta, minimo, massimo, passo, sezione)
SLIDERS = {
    "temperature": ("Temperatura", -100, 100, 1, "Bilanciamento del bianco"),
    "tint": ("Tinta", -100, 100, 1, "Bilanciamento del bianco"),
    "exposure": ("Esposizione", -4.0, 4.0, 0.05, "Tono"),
    "contrast": ("Contrasto", -100, 100, 1, "Tono"),
    "highlights": ("Luci", -100, 100, 1, "Tono"),
    "shadows": ("Ombre", -100, 100, 1, "Tono"),
    "whites": ("Bianchi", -100, 100, 1, "Tono"),
    "blacks": ("Neri", -100, 100, 1, "Tono"),
    "brightness": ("Luminosità", -100, 100, 1, "Tono"),
    "vibrance": ("Vividezza", -100, 100, 1, "Presenza"),
    "saturation": ("Saturazione", -100, 100, 1, "Presenza"),
}

CURVE_CHANNELS = ["master", "r", "g", "b"]
IDENTITY_CURVE = [[0.0, 0.0], [1.0, 1.0]]


def default_grade() -> dict:
    g = {k: 0.0 for k in SLIDERS}
    g["curves"] = {c: copy.deepcopy(IDENTITY_CURVE) for c in CURVE_CHANNELS}
    g["hsl"] = {"hue": [0.0] * 8, "sat": [0.0] * 8, "lum": [0.0] * 8}
    return g


def normalize(grade: dict | None) -> dict:
    """Restituisce una regolazione completa (riempie le chiavi mancanti)."""
    out = default_grade()
    if not grade:
        return out
    for k in SLIDERS:
        if k in grade:
            out[k] = float(grade[k])
    for c in CURVE_CHANNELS:
        pts = (grade.get("curves") or {}).get(c)
        if pts and len(pts) >= 2:
            out["curves"][c] = [[float(x), float(y)] for x, y in pts]
    for k in ("hue", "sat", "lum"):
        vals = (grade.get("hsl") or {}).get(k)
        if vals and len(vals) == 8:
            out["hsl"][k] = [float(v) for v in vals]
    anim = {}
    for k, pts in (grade.get("anim") or {}).items():
        if k in ANIM_KEYS and pts:
            anim[k] = sorted(([float(t), float(v)] for t, v in pts), key=lambda q: q[0])
    if anim:
        out["anim"] = anim
    return out


# ------------------------------------------------------------------------
# Regolazioni animate nel tempo (pezzi e timeline)
# ------------------------------------------------------------------------
# ogni cursore (e ogni cursore HSL "hsl:sat:3") può avere una lista di punti
# [tempo, valore]: tempo in secondi dall'inizio del pezzo (o della timeline).
# Tra un punto e l'altro il valore cambia in modo lineare; prima del primo e
# dopo l'ultimo resta fermo.

ANIM_KEYS = set(SLIDERS) | {f"hsl:{k}:{i}" for k in ("hue", "sat", "lum") for i in range(8)}


def slider_range(key):
    if key.startswith("hsl:"):
        return -100.0, 100.0
    lab, lo, hi, step, sec = SLIDERS[key]
    return float(lo), float(hi)


def value_at(points, t):
    if not points:
        return 0.0
    if len(points) == 1:
        return float(points[0][1])
    return float(np.interp(t, [p[0] for p in points], [p[1] for p in points]))


def is_animated(grade) -> bool:
    return bool(grade and grade.get("anim"))


def anim_times(grade):
    """Tempi (relativi) dei punti di tutte le animazioni della regolazione."""
    out = set()
    for pts in ((grade or {}).get("anim") or {}).values():
        out.update(float(p[0]) for p in pts)
    return sorted(out)


def resolve(grade, t):
    """Regolazione fissa con i valori animati calcolati al tempo t (relativo)."""
    g = normalize(grade)
    anim = g.pop("anim", None)
    if not anim:
        return g
    for k, pts in anim.items():
        v = value_at(pts, t)
        if k.startswith("hsl:"):
            _, part, i = k.split(":")
            g["hsl"][part][int(i)] = v
        else:
            g[k] = v
    return g


def is_identity(grade: dict | None) -> bool:
    if not grade:
        return True
    g = normalize(grade)
    if g.get("anim"):
        return False
    if any(abs(g[k]) > 1e-6 for k in SLIDERS):
        return False
    for c in CURVE_CHANNELS:
        pts = g["curves"][c]
        if any(abs(x - y) > 1e-6 for x, y in pts):
            return False
    if any(abs(v) > 1e-6 for vals in g["hsl"].values() for v in vals):
        return False
    return True


def grade_key(grades) -> str:
    """Impronta stabile di una lista di regolazioni (per le cache)."""
    active = [normalize(g) for g in grades if not is_identity(g)]
    if not active:
        return "identity"
    s = json.dumps(active, sort_keys=True)
    return hashlib.sha1(s.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Funzioni di base
# --------------------------------------------------------------------------

def _to_linear(x):
    x = np.maximum(x, 0.0)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _to_display(x):
    x = np.maximum(x, 0.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def _luma(x):
    return x[:, 0] * 0.2126 + x[:, 1] * 0.7152 + x[:, 2] * 0.0722


def _pchip(px, py, xs):
    """Interpolazione cubica monotona (niente "rimbalzi" tra i punti)."""
    px = np.asarray(px, dtype=np.float64)
    py = np.asarray(py, dtype=np.float64)
    n = len(px)
    if n == 2:
        return np.interp(xs, px, py)
    h = np.diff(px)
    h[h <= 1e-9] = 1e-9
    delta = np.diff(py) / h
    d = np.zeros(n)
    for k in range(1, n - 1):
        if delta[k - 1] * delta[k] <= 0:
            d[k] = 0.0
        else:
            w1 = 2 * h[k] + h[k - 1]
            w2 = h[k] + 2 * h[k - 1]
            d[k] = (w1 + w2) / (w1 / delta[k - 1] + w2 / delta[k])
    d[0] = delta[0]
    d[-1] = delta[-1]
    xs = np.clip(xs, px[0], px[-1])
    idx = np.clip(np.searchsorted(px, xs) - 1, 0, n - 2)
    t = (xs - px[idx]) / h[idx]
    h00 = (1 + 2 * t) * (1 - t) ** 2
    h10 = t * (1 - t) ** 2
    h01 = t ** 2 * (3 - 2 * t)
    h11 = t ** 2 * (t - 1)
    return h00 * py[idx] + h10 * h[idx] * d[idx] + h01 * py[idx + 1] + h11 * h[idx] * d[idx + 1]


def curve_table(points, size=1024):
    pts = sorted(([float(x), float(y)] for x, y in points), key=lambda p: p[0])
    xs = np.linspace(0, 1, size)
    ys = _pchip([p[0] for p in pts], [p[1] for p in pts], xs)
    # fuori dagli estremi la curva resta piatta
    return np.clip(ys, 0, 1).astype(np.float32)


def _apply_curve(v, points):
    if all(abs(x - y) < 1e-6 for x, y in points):
        return v
    table = curve_table(points)
    return np.interp(np.clip(v, 0, 1), np.linspace(0, 1, len(table)), table)


def _rgb_to_hsv(x):
    r, g, b = x[:, 0], x[:, 1], x[:, 2]
    mx = np.max(x, axis=1)
    mn = np.min(x, axis=1)
    c = mx - mn
    h = np.zeros_like(mx)
    nz = c > 1e-7
    rc = np.where(nz, (mx - r) / np.where(nz, c, 1), 0)
    gc = np.where(nz, (mx - g) / np.where(nz, c, 1), 0)
    bc = np.where(nz, (mx - b) / np.where(nz, c, 1), 0)
    h = np.where(r == mx, bc - gc, np.where(g == mx, 2.0 + rc - bc, 4.0 + gc - rc))
    h = np.where(nz, (h / 6.0) % 1.0, 0.0) * 360.0
    s = np.where(mx > 1e-7, c / np.where(mx > 1e-7, mx, 1), 0.0)
    return h, s, mx


def _hsv_to_rgb(h, s, v):
    h = (h % 360.0) / 60.0
    i = np.floor(h).astype(np.int32) % 6
    f = h - np.floor(h)
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=1)


def _band_value(h, values):
    """Valore di un parametro HSL per ogni tonalità (interpolato sul cerchio)."""
    vals = np.asarray(values, dtype=np.float32)
    cx = np.concatenate([HSL_CENTERS - 360.0, HSL_CENTERS, HSL_CENTERS + 360.0])
    cv = np.concatenate([vals, vals, vals])
    return np.interp(h, cx, cv)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def apply_grade(rgb: np.ndarray, grade: dict) -> np.ndarray:
    """Applica una regolazione a un array (N, 3) di colori in [0, 1]."""
    g = normalize(grade)
    x = rgb.astype(np.float32, copy=True)

    # 1) Bilanciamento del bianco ed esposizione (in luce lineare)
    t = g["temperature"] / 100.0
    tn = g["tint"] / 100.0
    ev = g["exposure"]
    if abs(t) > 1e-6 or abs(tn) > 1e-6 or abs(ev) > 1e-6:
        lin = _to_linear(x)
        gains = np.array([2 ** (0.35 * t), 2 ** (-0.25 * tn), 2 ** (-0.35 * t)], dtype=np.float32)
        gains /= (0.2126 * gains[0] + 0.7152 * gains[1] + 0.0722 * gains[2])
        lin = lin * gains * (2.0 ** ev)
        x = _to_display(lin)
    x = np.clip(x, 0.0, 1.0)

    # 2) Bianchi e neri (punti di bianco e nero)
    bp = -g["blacks"] / 100.0 * 0.10
    wp = 1.0 - g["whites"] / 100.0 * 0.15
    if abs(bp) > 1e-6 or abs(wp - 1.0) > 1e-6:
        x = np.clip((x - bp) / max(wp - bp, 1e-3), 0.0, 1.0)

    # 3) Luci e ombre (sulla luminanza, senza spostare i colori)
    sh = g["shadows"] / 100.0
    hi = g["highlights"] / 100.0
    if abs(sh) > 1e-6 or abs(hi) > 1e-6:
        L = np.clip(_luma(x), 0.0, 1.0)
        dl = sh * 0.30 * (L * (1 - L) ** 3) / 0.1055 + hi * 0.30 * (L ** 3 * (1 - L)) / 0.1055
        Ln = np.clip(L + dl, 0.0, 1.0)
        ratio = np.where(L > 1e-4, Ln / np.maximum(L, 1e-4), 1.0)
        x = np.where((L > 1e-4)[:, None], x * ratio[:, None], Ln[:, None])
        x = np.clip(x, 0.0, 1.0)

    # 4) Luminosità (toni medi)
    br = g["brightness"] / 100.0
    if abs(br) > 1e-6:
        x = np.power(x, 2.0 ** (-br * 0.8))

    # 5) Contrasto (curva a S attorno al grigio medio)
    c = g["contrast"] / 100.0
    if abs(c) > 1e-6:
        gm = 2.0 ** (c * 0.9)
        lo = 0.5 * np.power(np.clip(2 * x, 0, 1), gm)
        hi_ = 1.0 - 0.5 * np.power(np.clip(2 * (1 - x), 0, 1), gm)
        x = np.where(x < 0.5, lo, hi_)

    # 6) Curve
    curves = g["curves"]
    x = _apply_curve(x, curves["master"])
    for i, ch in enumerate("rgb"):
        if not all(abs(a - b) < 1e-6 for a, b in curves[ch]):
            x[:, i] = _apply_curve(x[:, i], curves[ch])

    # 7) Saturazione e vividezza
    sat = g["saturation"] / 100.0
    vib = g["vibrance"] / 100.0
    if abs(sat) > 1e-6 or abs(vib) > 1e-6:
        L = _luma(x)[:, None]
        mx = np.max(x, axis=1)
        mn = np.min(x, axis=1)
        s_now = np.where(mx > 1e-5, (mx - mn) / np.maximum(mx, 1e-5), 0.0)
        if vib >= 0:
            fv = 1.0 + vib * (1.0 - s_now) ** 2
        else:
            fv = 1.0 + vib * (0.5 + 0.5 * s_now)
        factor = (1.0 + sat) * fv
        x = np.clip(L + (x - L) * factor[:, None], 0.0, 1.0)

    # 8) HSL per colore
    hsl = g["hsl"]
    if any(abs(v) > 1e-6 for vals in hsl.values() for v in vals):
        h, s, v = _rgb_to_hsv(x)
        dh = _band_value(h, hsl["hue"]) / 100.0 * 30.0
        ds = _band_value(h, hsl["sat"]) / 100.0
        dlum = _band_value(h, hsl["lum"]) / 100.0
        h2 = h + dh
        s2 = np.clip(s * (1.0 + ds), 0.0, 1.0)
        v2 = np.clip(v * (1.0 + dlum * 0.6 * s), 0.0, 1.0)
        x = _hsv_to_rgb(h2, s2, v2)

    return np.clip(x, 0.0, 1.0).astype(np.float32)


# --------------------------------------------------------------------------
# LUT 3D
# --------------------------------------------------------------------------

_GRID_CACHE: dict[int, np.ndarray] = {}


def identity_grid(n: int) -> np.ndarray:
    """Griglia di colori (n^3, 3) ordinata con R più veloce, poi G, poi B
    (lo stesso ordine dei file .cube e delle texture 3D OpenGL)."""
    if n not in _GRID_CACHE:
        v = np.linspace(0, 1, n, dtype=np.float32)
        b, g, r = np.meshgrid(v, v, v, indexing="ij")
        _GRID_CACHE[n] = np.stack([r.ravel(), g.ravel(), b.ravel()], axis=1)
    return _GRID_CACHE[n]


def build_lut(grades, n: int = 33) -> np.ndarray:
    """LUT (n, n, n, 3) float32 indicizzata [b, g, r]; applica le regolazioni
    in ordine (es. clip intera -> pezzo -> timeline)."""
    x = identity_grid(n)
    for gr in grades:
        if not is_identity(gr):
            x = apply_grade(x, gr)
    return np.ascontiguousarray(x.reshape(n, n, n, 3), dtype=np.float32)


def write_cube(path: str, lut: np.ndarray, title: str = "Moviola"):
    n = lut.shape[0]
    flat = lut.reshape(-1, 3)
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in flat]
    with open(path, "w", encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")


class CpuLut:
    """Applicazione della LUT senza scheda video (ripiego).

    Usa una tabella 64^3 a ricerca diretta: costa una sola lettura per
    pixel. La resa è leggermente meno fine della scheda video, ma basta
    come anteprima; l'export usa sempre la LUT completa.
    """

    SIZE = 64

    def __init__(self, lut: np.ndarray):
        n = self.SIZE
        lut8 = build_from_lut(lut, n)
        self.table = (np.clip(lut8, 0, 1) * 255 + 0.5).astype(np.uint8).reshape(-1, 3)

    def apply(self, img: np.ndarray) -> np.ndarray:
        r = (img[..., 0] >> 2).astype(np.int32)
        g = (img[..., 1] >> 2).astype(np.int32)
        b = (img[..., 2] >> 2).astype(np.int32)
        return self.table[(b << 12) | (g << 6) | r]


def build_from_lut(lut: np.ndarray, n: int) -> np.ndarray:
    """Ricampiona una LUT a una griglia n^3 (interpolazione trilineare)."""
    pts = identity_grid(n)
    return sample_lut(lut, pts).reshape(n, n, n, 3)


def sample_lut(lut: np.ndarray, pts: np.ndarray) -> np.ndarray:
    m = lut.shape[0]
    flat = lut.reshape(-1, 3)
    p = np.clip(pts, 0, 1) * (m - 1)
    i0 = np.minimum(p.astype(np.int32), m - 2)
    f = (p - i0).astype(np.float32)
    r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
    fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]

    def at(db, dg, dr):
        return flat[((b0 + db) * m + (g0 + dg)) * m + (r0 + dr)]

    c00 = at(0, 0, 0) + (at(0, 0, 1) - at(0, 0, 0)) * fr
    c01 = at(0, 1, 0) + (at(0, 1, 1) - at(0, 1, 0)) * fr
    c10 = at(1, 0, 0) + (at(1, 0, 1) - at(1, 0, 0)) * fr
    c11 = at(1, 1, 0) + (at(1, 1, 1) - at(1, 1, 0)) * fr
    c0 = c00 + (c01 - c00) * fg
    c1 = c10 + (c11 - c10) * fg
    return c0 + (c1 - c0) * fb


def apply_lut_image(img: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Applicazione esatta (trilineare) su un'immagine uint8 HxWx3; lenta,
    usata solo per test e miniature."""
    h, w, _ = img.shape
    out = sample_lut(lut, img.reshape(-1, 3).astype(np.float32) / 255.0)
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8).reshape(h, w, 3)
