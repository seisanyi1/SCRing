"""Synthetic fluorescence discs used to validate the circle fitter."""

from __future__ import annotations

import numpy as np
from scipy.special import erf


def make_disc(
    width: int = 1280,
    height: int = 960,
    cx: float = 640.0,
    cy: float = 480.0,
    r: float = 200.0,
    edge: float = 6.0,
    fg: float = 0.55,
    bg: float = 0.025,
    noise: float = 0.006,
    gradient: float = 0.0,
    notch_deg: float = 0.0,
    notch_depth: float = 0.55,
    blue_spot: bool = False,
    seed: int = 0,
) -> tuple[np.ndarray, dict]:
    """Render a green disc with a soft rim and return (rgb, truth).

    The 50 % point of the error-function rim lies exactly on radius ``r``.
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    d = np.hypot(xx - cx, yy - cy)
    sigma = max(edge, 1e-3)
    ramp = 0.5 * (1.0 - erf((d - r) / (np.sqrt(2.0) * sigma)))

    # Gentle radial brightness variation inside the disc (like the real data).
    radial = 0.88 + 0.12 * np.exp(-((d - 0.55 * r) ** 2) / (2.0 * (0.45 * r) ** 2))
    green = bg + (fg * radial - bg) * ramp
    if gradient:
        green = green + gradient * (xx / max(1, width - 1))
    if notch_deg > 0:
        ang = np.arctan2(yy - cy, xx - cx)
        near_top = np.abs(np.angle(np.exp(1j * (ang + np.pi / 2)))) < np.deg2rad(notch_deg) / 2
        band = (d > r - 1.5 * sigma) & (d < r + sigma)
        green = np.where(near_top & band, green * (1.0 - notch_depth), green)

    rgb = np.stack([0.10 * green, green, 0.30 * green], axis=2)
    if blue_spot:
        d2 = np.hypot(xx - width * 0.82, yy - height * 0.20)
        rgb[..., 2] += 0.75 * np.clip(1.0 - d2 / (0.12 * min(width, height)), 0.0, 1.0)
    if noise:
        rgb = rgb + rng.normal(0.0, noise, rgb.shape).astype(np.float32)
    rgb = np.clip(rgb, 0.0, 1.0)
    truth = {"cx": float(cx), "cy": float(cy), "r": float(r), "width": width, "height": height}
    return rgb, truth
