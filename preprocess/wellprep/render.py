"""Rendering of circular ROIs: normalised crops, masks, overlays and polar maps."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

from .imaging import to_uint8


def _anti_alias(rgb: np.ndarray, scale: float) -> np.ndarray:
    sigma = 0.5 * (scale - 1.0)
    if sigma > 0.25:
        return ndi.gaussian_filter(rgb, sigma=(sigma, sigma, 0.0), mode="nearest")
    return rgb


def warp_circle(
    rgb: np.ndarray,
    cx: float,
    cy: float,
    r: float,
    margin: float,
    size: int,
) -> np.ndarray:
    """Map the disc to a fixed square: radius -> size/(2*margin) pixels.

    The sampling grid is defined in image coordinates, so the fitted circle is
    always centred and keeps a constant relative size regardless of the actual
    radius in the source image (non-fixed radius -> fixed geometry).
    """
    scale = 2.0 * r * margin / float(size)
    src = _anti_alias(rgb, scale)
    u = (np.arange(size, dtype=np.float32) + 0.5 - size / 2.0) * scale
    xs = (cx + u).astype(np.float32)
    ys = (cy + u).astype(np.float32)
    xx, yy = np.meshgrid(xs, ys)
    out = np.empty((size, size, 3), dtype=np.float32)
    for c in range(3):
        out[..., c] = ndi.map_coordinates(
            src[..., c], [yy, xx], order=1, mode="constant", cval=0.0
        )
    return np.clip(out, 0.0, 1.0)


def raw_crop(rgb: np.ndarray, cx: float, cy: float, r: float, margin: float) -> np.ndarray:
    """Native-resolution square crop, padded with black when it leaves the frame."""
    side = max(8, int(round(2.0 * r * margin)))
    half = side / 2.0
    x0 = int(round(cx - half))
    y0 = int(round(cy - half))
    h, w = rgb.shape[:2]
    canvas = np.zeros((side, side, 3), dtype=np.float32)
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(w, x0 + side), min(h, y0 + side)
    if sx1 > sx0 and sy1 > sy0:
        canvas[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = rgb[sy0:sy1, sx0:sx1]
    return canvas


def circle_mask(size: int, margin: float) -> np.ndarray:
    """Binary mask (0/1) of the disc at its normalised position/size."""
    rad = size / (2.0 * margin)
    y, x = np.ogrid[:size, :size]
    return (((x + 0.5 - size / 2.0) ** 2 + (y + 0.5 - size / 2.0) ** 2) <= rad * rad).astype(np.float32)


def polar_unwrap(
    rgb: np.ndarray,
    cx: float,
    cy: float,
    r: float,
    n_theta: int = 720,
    n_r: int = 256,
    r_max_factor: float = 1.40,
) -> np.ndarray:
    """Unwrap the disc to angle x radius (rows = angle, columns = radius)."""
    theta = np.linspace(0.0, 2.0 * np.pi, n_theta, endpoint=False, dtype=np.float32)
    rr = np.linspace(0.0, r_max_factor * r, n_r, dtype=np.float32)
    xx = cx + np.cos(theta)[:, None] * rr[None, :]
    yy = cy + np.sin(theta)[:, None] * rr[None, :]
    out = np.empty((n_theta, n_r, 3), dtype=np.float32)
    for c in range(3):
        out[..., c] = ndi.map_coordinates(
            rgb[..., c], [yy.ravel(), xx.ravel()], order=1, mode="nearest"
        ).reshape(n_theta, n_r)
    return np.clip(out, 0.0, 1.0)


def draw_overlay(
    rgb: np.ndarray,
    cx: float,
    cy: float,
    r: float,
    max_side: int = 1600,
) -> np.ndarray:
    """Full-frame overlay with the fitted circle, centre and radius label."""
    img = Image.fromarray(to_uint8(rgb))
    draw = ImageDraw.Draw(img)
    width = max(2, int(round(r / 220)))
    box = [cx - r, cy - r, cx + r, cy + r]
    draw.ellipse(box, outline=(255, 64, 64), width=width)
    tick = max(6.0, 0.06 * r)
    draw.line([cx - tick, cy, cx + tick, cy], fill=(255, 64, 64), width=width)
    draw.line([cx, cy - tick, cx, cy + tick], fill=(255, 64, 64), width=width)
    # small marker at the 12 o'clock edge to show where the fit starts
    draw.line([cx, cy - r, cx, cy - r + 0.10 * r], fill=(255, 220, 0), width=width)
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return np.asarray(img).astype(np.float32) / 255.0


def make_thumb(rgb: np.ndarray, max_side: int = 360) -> np.ndarray:
    img = Image.fromarray(to_uint8(rgb))
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    return np.asarray(img).astype(np.float32) / 255.0


def downscale(rgb: np.ndarray, max_side: int = 1600) -> np.ndarray:
    img = Image.fromarray(to_uint8(rgb))
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return np.asarray(img).astype(np.float32) / 255.0


def draw_label(rgb: np.ndarray, text: str) -> np.ndarray:
    img = Image.fromarray(to_uint8(rgb))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 8 + 6 * len(text), 16], fill=(0, 0, 0))
    draw.text((4, 3), text, fill=(255, 255, 255))
    return np.asarray(img).astype(np.float32) / 255.0
