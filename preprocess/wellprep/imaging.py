"""Image loading, feature maps and Otsu thresholding (numpy/scipy only)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage as ndi

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".ppm"}


@dataclass
class ImageInfo:
    path: str
    width: int
    height: int
    mode: str
    bit_depth: int


def _to_unit_float(arr: np.ndarray) -> np.ndarray:
    """Scale integer image data to 0..1 using a plausible nominal range."""
    if arr.dtype == np.uint8:
        return arr.astype(np.float32) / 255.0
    if arr.dtype == np.uint16:
        mx = float(arr.max()) if arr.size else 0.0
        nominal = 4095.0 if 0 < mx <= 4095 else 65535.0
        return arr.astype(np.float32) / nominal
    if arr.dtype in (np.int16, np.int32):
        arr = arr.astype(np.float32)
        mx = float(arr.max()) if arr.size else 0.0
        nominal = 255.0 if mx <= 255 else (4095.0 if mx <= 4095 else 65535.0)
        return np.clip(arr, 0, None) / nominal
    arr = arr.astype(np.float32)
    mx = float(np.nanmax(arr)) if arr.size else 0.0
    if mx <= 1.5:
        return np.clip(arr, 0.0, 1.0)
    return np.clip(arr / mx, 0.0, 1.0)


def load_rgb(path: str | Path) -> tuple[np.ndarray, ImageInfo]:
    """Load an image as float32 RGB in 0..1, honouring EXIF orientation.

    Supports 8-bit, 16-bit and floating point TIFF/PNG.  Grayscale images are
    replicated into three channels so the rest of the pipeline stays uniform.
    """
    path = Path(path)
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        mode = im.mode
        bit_depth = 8
        if mode in ("I;16", "I;16B", "I;16L", "I", "F"):
            arr = np.asarray(im)
            bit_depth = 16 if (arr.dtype == np.uint16 or float(arr.max(initial=0)) > 255) else 8
            rgb = np.repeat(_to_unit_float(arr)[..., None], 3, axis=2)
        elif mode in ("L", "LA", "1"):
            arr = np.asarray(im.convert("L"))
            gray = _to_unit_float(arr)
            rgb = np.repeat(gray[..., None], 3, axis=2)
        else:
            if mode != "RGB":
                im = im.convert("RGB")
            rgb = np.asarray(im).astype(np.float32) / 255.0
        width, height = im.size
    rgb = np.nan_to_num(np.clip(rgb, 0.0, 1.0), nan=0.0)
    info = ImageInfo(str(path), int(width), int(height), mode, bit_depth)
    return rgb.astype(np.float32, copy=False), info


def feature_map(rgb: np.ndarray, feature: str = "green_excess") -> np.ndarray:
    """Return a single-channel float map where the target disc is bright."""
    rgb = rgb.astype(np.float32, copy=False)
    if feature == "green_excess":
        # Negative for blue/purple objects, ~0 for neutral background.
        return rgb[..., 1] - 0.5 * (rgb[..., 0] + rgb[..., 2])
    if feature == "gray":
        return (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])
    if feature == "max_rgb":
        return rgb.max(axis=2)
    raise ValueError(f"unknown feature: {feature}")


def otsu_threshold(values: np.ndarray, nbins: int = 512) -> float:
    """Otsu threshold computed on a robust percentile window."""
    v = np.asarray(values, dtype=np.float64).ravel()
    v = v[np.isfinite(v)]
    if v.size == 0:
        return 0.0
    lo, hi = np.percentile(v, [0.1, 99.9])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return float(np.median(v))
    hist, edges = np.histogram(v, bins=nbins, range=(lo, hi))
    centers = 0.5 * (edges[:-1] + edges[1:])
    w0 = np.cumsum(hist).astype(np.float64)
    w1 = float(hist.sum()) - w0
    s0 = np.cumsum(hist * centers)
    s1 = float((hist * centers).sum()) - s0
    mu0 = np.divide(s0, w0, out=np.zeros_like(s0), where=w0 > 0)
    mu1 = np.divide(s1, w1, out=np.zeros_like(s1), where=w1 > 0)
    between = w0 * w1 * (mu0 - mu1) ** 2
    if not np.any(between > 0):
        return float(np.median(v))
    return float(centers[int(np.argmax(between))])


def stretch_for_display(rgb: np.ndarray, enable: bool = False) -> np.ndarray:
    """Optionally stretch a preview to use the full 0..1 range."""
    if not enable:
        return rgb
    hi = float(np.percentile(rgb, 99.5))
    if hi <= 1e-6:
        return rgb
    return np.clip(rgb / hi, 0.0, 1.0)


def to_uint8(rgb: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.uint8)


def save_rgb(path: str | Path, rgb: np.ndarray, quality: int = 92) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(to_uint8(rgb))
    if path.suffix.lower() in (".jpg", ".jpeg"):
        img.save(path, quality=quality, subsampling=1)
    else:
        img.save(path, compress_level=4)


def save_gray(path: str | Path, gray: np.ndarray) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.clip(np.rint(np.asarray(gray) * 255.0), 0, 255).astype(np.uint8)
    Image.fromarray(arr, mode="L").save(path, compress_level=4)


def disk(radius: int) -> np.ndarray:
    r = max(1, int(radius))
    y, x = np.ogrid[-r : r + 1, -r : r + 1]
    return (x * x + y * y) <= r * r
