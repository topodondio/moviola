"""Parade RGB: tre grafici affiancati (rosso, verde, blu) che mostrano, colonna per colonna
dell'immagine, quanti pixel cadono a ogni livello. Serve a vedere dominanti di colore e
clipping. Funzioni pure (numpy), calcolate su un'immagine ridotta."""
from __future__ import annotations

import numpy as np

LEVELS = 128


def parade(img: np.ndarray, cols: int = 160, levels: int = LEVELS) -> np.ndarray:
    """img: HxWx3 uint8. Restituisce un'immagine uint8 (levels, 3*cols, 3): 255 in alto."""
    h, w = img.shape[:2]
    xs = np.linspace(0, w - 1, min(cols, w)).astype(np.int32)
    sub = img[:: max(1, h // 90)][:, xs]
    rows, n = sub.shape[:2]
    idx = (sub.astype(np.int32) * (levels - 1) + 127) // 255
    out = np.zeros((levels, 3 * cols, 3), np.float32)
    colidx = np.broadcast_to(np.arange(n), (rows, n))
    for c in range(3):
        counts = np.zeros((levels, n), np.float32)
        np.add.at(counts, (idx[:, :, c], colidx), 1.0)
        v = np.clip(np.log1p(counts) / np.log1p(max(2.0, rows * 0.35)), 0.0, 1.0)
        out[:, c * cols:c * cols + n, c] = v
    # un po' di bianco dove il canale è intenso, così le tracce si leggono sul nero
    out = np.minimum(1.0, out + 0.35 * out ** 3 * out.max(axis=2, keepdims=True))
    # ispessisce un poco le tracce (una linea sola sparirebbe a monitor ridotto)
    out = np.maximum(out, np.maximum(np.roll(out, 1, axis=0), np.roll(out, -1, axis=0)) * 0.6)
    out = out[::-1]
    return (out * 255).astype(np.uint8)


def histograms(img: np.ndarray) -> np.ndarray:
    """Istogrammi 4x256: luminosità, rosso, verde, blu."""
    y = (img[..., 0].astype(np.uint16) * 54 + img[..., 1].astype(np.uint16) * 183
         + img[..., 2].astype(np.uint16) * 19) >> 8
    out = [np.bincount(y.ravel(), minlength=256)]
    out += [np.bincount(img[..., c].ravel(), minlength=256) for c in range(3)]
    return np.stack(out).astype(np.float32)


def clipping(img: np.ndarray) -> tuple[float, float]:
    """Frazione di pixel con un canale a 0 (neri tappati) e a 255 (bianchi bruciati)."""
    lo = float(np.mean(img.min(axis=2) <= 1))
    hi = float(np.mean(img.max(axis=2) >= 254))
    return lo, hi
