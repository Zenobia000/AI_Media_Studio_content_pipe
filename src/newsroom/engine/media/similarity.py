"""Structural similarity (SSIM) of two same-sized pictures, on luma in 8×8 windows (4 px apart): the measure that holds an AVIF variant to at least the sharpness
of the rendition it stands in for. Port of media/similarity.ts."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass
class Luma:
    values: np.ndarray  # float32, height × width
    width: int
    height: int


def luma(image: bytes | Image.Image) -> Luma:
    img = Image.open(io.BytesIO(image)) if isinstance(image, (bytes, bytearray)) else image
    rgb = np.asarray(img.convert("RGB"), dtype=np.float64)
    values = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float32)
    return Luma(values, values.shape[1], values.shape[0])


def ssim(a: Luma, b: Luma) -> float | None:
    """Mean SSIM of b against the reference a (1 is identical); None when the sizes differ."""
    if a.width != b.width or a.height != b.height or a.width < 8 or a.height < 8:
        return None
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    wa = np.lib.stride_tricks.sliding_window_view(a.values.astype(np.float64), (8, 8))[::4, ::4]
    wb = np.lib.stride_tricks.sliding_window_view(b.values.astype(np.float64), (8, 8))[::4, ::4]
    ma = wa.mean(axis=(2, 3))
    mb = wb.mean(axis=(2, 3))
    da = wa - ma[..., None, None]
    db = wb - mb[..., None, None]
    va = (da * da).sum(axis=(2, 3))
    vb = (db * db).sum(axis=(2, 3))
    cov = (da * db).sum(axis=(2, 3))
    score = ((2 * ma * mb + c1) * (2 * cov / 63 + c2)) / ((ma * ma + mb * mb + c1) * ((va + vb) / 63 + c2))
    return float(score.mean())
