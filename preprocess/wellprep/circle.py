"""Sub-pixel, non-fixed-radius circle fitting for a bright disc.

The coarse mask gives a centre and an area-equivalent radius.  The precise
edge is then located along hundreds of rays as the 50 % level between the
local disc plateau and the local background, which is robust to illumination
gradients, JPEG haze and the fuzzy/dendritic rim seen in fluorescence images.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy import ndimage as ndi

from .config import Config


@dataclass
class FitResult:
    cx: float
    cy: float
    r: float
    r_area: float
    r_min: float
    r_max: float
    radial_cv: float
    coverage: float
    residual_rms: float
    contrast: float
    snr: float
    edge_width: float
    threshold: float
    n_rays: int
    touches_border: bool
    method: str


def _sample_profile(
    score: np.ndarray,
    cx: float,
    cy: float,
    r0: float,
    cfg: Config,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n_ang = int(cfg.rays)
    r_lo = cfg.search_inner * 0.55 * r0
    r_hi = cfg.search_outer * r0
    n_r = int(np.clip(max(cfg.profile_points, 1.5 * r0), 300, 4000))
    ang = np.linspace(0.0, 2.0 * np.pi, n_ang, endpoint=False, dtype=np.float32)
    rr = np.linspace(max(r_lo, 0.0), r_hi, n_r, dtype=np.float32)
    cos_a = np.cos(ang)[:, None]
    sin_a = np.sin(ang)[:, None]
    xx = cx + rr[None, :] * cos_a
    yy = cy + rr[None, :] * sin_a
    h, w = score.shape
    domain = (xx >= 0.0) & (xx <= w - 1.0) & (yy >= 0.0) & (yy <= h - 1.0)
    vals = ndi.map_coordinates(
        score.astype(np.float32, copy=False),
        [yy.ravel(), xx.ravel()],
        order=1,
        mode="nearest",
    )
    prof = vals.reshape(n_ang, n_r)
    prof = ndi.gaussian_filter1d(prof, 1.0, axis=1, mode="nearest")
    return ang.astype(np.float64), rr.astype(np.float64), prof, domain


def _edge_radii(
    prof: np.ndarray,
    rr: np.ndarray,
    r0: float,
    cfg: Config,
    domain: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return per-ray edge radii plus plateau/background levels."""
    in_sel = (rr >= 0.50 * r0) & (rr <= 0.85 * r0)
    out_sel = (rr >= 1.18 * r0) & (rr <= 1.48 * r0)
    if in_sel.sum() < 3 or out_sel.sum() < 3:
        n = prof.shape[0]
        nan = np.full(n, np.nan)
        return nan, np.zeros(n, bool), nan, nan, nan

    plateau = np.median(prof[:, in_sel], axis=1)
    bg = np.median(prof[:, out_sel], axis=1)
    contrast = plateau - bg
    level = bg + cfg.edge_level * contrast

    n_ang, n_r = prof.shape
    below = prof < level[:, None]
    h = max(2, int(cfg.hysteresis))
    if h >= n_r:
        raise ValueError("hysteresis too large for profile length")
    run = sliding_window_view(below, h, axis=1).all(axis=2)  # (n_ang, n_r-h+1)
    valid = np.zeros((n_ang, n_r), dtype=bool)
    valid[:, : run.shape[1]] = run

    start = int(np.searchsorted(rr, cfg.search_inner * r0))
    valid[:, : max(0, start)] = False
    if domain is not None:
        # Rays that leave the sensor before the local background cannot be
        # trusted (important for discs cut by the image border).
        end = int(np.searchsorted(rr, min(1.05 * r0, float(rr[-1]))))
        end = max(end, start + 1)
        domain_ok = domain[:, start:end].all(axis=1)
        valid &= domain_ok[:, None]

    # require the ray to stay clearly below the level a few samples later
    deep_off = min(h + 2, n_r - 1)
    deep_idx = np.clip(np.arange(n_r) + deep_off, 0, n_r - 1)
    deep = prof[:, deep_idx] < (level - 0.08 * contrast)[:, None]
    valid &= deep

    has = valid.any(axis=1)
    j = valid.argmax(axis=1)
    rows = np.arange(n_ang)
    j_safe = np.clip(j, 1, n_r - 1)
    y0 = prof[rows, j_safe - 1]
    y1 = prof[rows, j_safe]
    denom = y1 - y0
    frac = np.where(np.abs(denom) > 1e-9, (level - y0) / denom, 0.5)
    frac = np.clip(frac, 0.0, 1.0)
    radii = rr[j_safe - 1] + frac * (rr[j_safe] - rr[j_safe - 1])
    radii = np.where(has & np.isfinite(radii), radii, np.nan)
    return radii, has, plateau, bg, contrast


def fit_circle(points: np.ndarray) -> tuple[float, float, float]:
    """Algebraic (Kasa) least-squares circle fit, centred for conditioning."""
    p = np.asarray(points, dtype=np.float64)
    if len(p) < 3:
        raise ValueError("need at least 3 points")
    mx, my = p.mean(axis=0)
    q = p - (mx, my)
    a = np.column_stack([2.0 * q[:, 0], 2.0 * q[:, 1], np.ones(len(q))])
    b = (q * q).sum(axis=1)
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    cx = float(sol[0] + mx)
    cy = float(sol[1] + my)
    r2 = float(sol[2] + sol[0] ** 2 + sol[1] ** 2)
    r = math.sqrt(max(r2, 1e-12))
    return cx, cy, r


def initial_circle(
    mask: np.ndarray,
    blob_cx: float,
    blob_cy: float,
    blob_r: float,
    bbox: tuple[int, int, int, int],
) -> tuple[float, float, float]:
    """Circle through the mask boundary; much better than a centroid for
    discs that are cut by the image border (a centroid is then biased)."""
    h, w = mask.shape
    x0, y0, x1, y1 = bbox
    sub = np.asarray(mask[y0:y1, x0:x1], dtype=bool)
    if sub.size == 0:
        return blob_cx, blob_cy, blob_r
    eroded = ndi.binary_erosion(sub, structure=np.ones((3, 3), dtype=bool))
    edge = sub & ~eroded
    ys, xs = np.nonzero(edge)
    x = xs.astype(np.float64) + x0
    y = ys.astype(np.float64) + y0
    keep = (x >= 2) & (x <= w - 3) & (y >= 2) & (y <= h - 3)
    if np.count_nonzero(keep) < 12:
        return blob_cx, blob_cy, blob_r
    pts = np.column_stack([x[keep], y[keep]])
    try:
        cx, cy, r = fit_circle(pts)
    except (ValueError, np.linalg.LinAlgError):
        return blob_cx, blob_cy, blob_r
    for _ in range(3):
        d = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
        resid = d - r
        med = float(np.median(resid))
        mad = float(np.median(np.abs(resid - med)))
        tol = max(1.5, 3.0 * 1.4826 * mad)
        use = np.abs(resid - med) <= tol
        if np.count_nonzero(use) < 12:
            break
        try:
            cx2, cy2, r2 = fit_circle(pts[use])
        except (ValueError, np.linalg.LinAlgError):
            break
        if not (np.isfinite(cx2) and np.isfinite(cy2) and np.isfinite(r2) and r2 > 2):
            break
        cx, cy, r = cx2, cy2, r2
    if not (np.isfinite(cx) and np.isfinite(cy) and np.isfinite(r) and r > 2):
        return blob_cx, blob_cy, blob_r
    return float(cx), float(cy), float(r)


def _edge_width(mean_prof: np.ndarray, rr: np.ndarray, r: float, contrast: float) -> float:
    """Softness of the rim: contrast divided by the steepest radial slope.

    This is the width of an equivalent linear ramp and, unlike a 10–90 %
    crossing, it is not inflated by the slow brightness drift inside the disc.
    """
    if not np.isfinite(contrast) or contrast <= 0:
        return float("nan")
    sel = (rr >= 0.75 * r) & (rr <= 1.25 * r)
    if sel.sum() < 9:
        return float("nan")
    x = rr[sel]
    y = ndi.gaussian_filter1d(mean_prof[sel], 2.0, mode="nearest")
    grad = -np.gradient(y, x)
    k = int(np.argmax(grad))
    gmax = float(grad[k])
    if not np.isfinite(gmax) or gmax <= 1e-9:
        return float("nan")
    return float(contrast / gmax)


def _background_noise(prof: np.ndarray, rr: np.ndarray, r: float) -> float:
    sel = (rr >= 1.18 * r) & (rr <= 1.48 * r)
    if sel.sum() < 3:
        return float("nan")
    v = prof[:, sel]
    med = np.median(v, axis=1, keepdims=True)
    mad = np.median(np.abs(v - med), axis=1)
    return float(np.median(1.4826 * mad))


def refine_circle(
    score: np.ndarray,
    cx: float,
    cy: float,
    r0: float,
    cfg: Config,
    threshold: float = float("nan"),
    r_area: float | None = None,
    max_steps: int = 0,
) -> FitResult:
    """Iteratively refine a circle centre/radius from radial edge crossings."""
    h, w = score.shape
    r = float(r0)
    steps = max_steps or int(cfg.max_iter)

    radii = np.array([])
    valid = np.zeros(0, bool)
    plateau = np.array([])
    bg = np.array([])
    contrast = np.array([])
    ang = np.array([])
    rr = np.array([])
    prof = np.empty((0, 0))

    for _ in range(steps):
        ang, rr, prof, domain = _sample_profile(score, cx, cy, r, cfg)
        radii, valid, plateau, bg, contrast = _edge_radii(prof, rr, r, cfg, domain)
        if valid.sum() < 12:
            break
        med = float(np.median(radii[valid]))
        mad = float(np.median(np.abs(radii[valid] - med)))
        tol = max(0.75, cfg.trim_sigma * 1.4826 * mad)
        keep = valid & (np.abs(radii - med) <= tol)
        if keep.sum() < 12:
            keep = valid
        pts = np.column_stack(
            [cx + radii[keep] * np.cos(ang[keep]), cy + radii[keep] * np.sin(ang[keep])]
        )
        try:
            cx2, cy2, r2 = fit_circle(pts)
        except (ValueError, np.linalg.LinAlgError):
            break
        if not (np.isfinite(cx2) and np.isfinite(cy2) and np.isfinite(r2)) or r2 < 1.5:
            break
        shift = math.hypot(cx2 - cx, cy2 - cy)
        dr = abs(r2 - r)
        cx, cy, r = cx2, cy2, r2
        if shift < 0.02 and dr < 0.02:
            break

    # Final, consistent measurement at the converged circle.
    ang, rr, prof, domain = _sample_profile(score, cx, cy, r, cfg)
    radii, valid, plateau, bg, contrast = _edge_radii(prof, rr, r, cfg, domain)
    method = "radial"
    if valid.sum() < 12:
        method = "mask"
        radii = np.full(len(ang), r, dtype=float)
        valid = np.ones(len(ang), bool)
        plateau = np.full(len(ang), 1.0)
        bg = np.full(len(ang), 0.0)
        contrast = np.ones(len(ang))

    med = float(np.median(radii[valid]))
    mad = float(np.median(np.abs(radii[valid] - med)))
    tol = max(0.75, cfg.trim_sigma * 1.4826 * mad)
    keep = valid & (np.abs(radii - med) <= tol)
    if keep.sum() < 12:
        keep = valid
    pts = np.column_stack([cx + radii[keep] * np.cos(ang[keep]), cy + radii[keep] * np.sin(ang[keep])])
    dist = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
    residual = dist - r
    residual_rms = float(np.sqrt(np.mean(residual**2))) if len(residual) else float("nan")

    r_kept = radii[keep]
    r_area_eff = float(r_area) if r_area and np.isfinite(r_area) else float(r)
    noise = _background_noise(prof, rr, r)
    contrast_med = float(np.median(contrast[valid])) if np.any(valid) else float("nan")
    snr = contrast_med / noise if np.isfinite(noise) and noise > 1e-9 else float("inf")
    mean_prof = np.mean(prof, axis=0)
    edge_width = _edge_width(
        mean_prof,
        rr,
        r,
        float(np.median(contrast)) if contrast.size else float("nan"),
    )

    touches = bool(cx - r < 0 or cy - r < 0 or cx + r > w - 1 or cy + r > h - 1)
    return FitResult(
        cx=float(cx),
        cy=float(cy),
        r=float(r),
        r_area=float(r_area_eff),
        r_min=float(np.percentile(r_kept, 1)) if r_kept.size else float(r),
        r_max=float(np.percentile(r_kept, 99)) if r_kept.size else float(r),
        radial_cv=float(np.std(r_kept) / r) if r_kept.size and r > 0 else float("nan"),
        coverage=float(valid.mean()),
        residual_rms=residual_rms,
        contrast=contrast_med,
        snr=float(snr),
        edge_width=float(edge_width),
        threshold=float(threshold),
        n_rays=int(len(ang)),
        touches_border=touches,
        method=method,
    )
