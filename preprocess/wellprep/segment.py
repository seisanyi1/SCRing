"""Coarse segmentation: threshold the green-excess map into candidate discs."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi

from .config import Config
from .imaging import disk, otsu_threshold


@dataclass
class Blob:
    label: int
    area: int
    cx: float
    cy: float
    r_eq: float
    mean_score: float
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 (x1/y1 exclusive)

    @property
    def aspect(self) -> float:
        w = self.bbox[2] - self.bbox[0]
        h = self.bbox[3] - self.bbox[1]
        return max(w, h) / max(1, min(w, h))


@dataclass
class Segmentation:
    threshold: float
    method: str
    mask: np.ndarray
    blobs: list[Blob]


def _components(score: np.ndarray, mask: np.ndarray, min_area: float, max_area: float) -> list[Blob]:
    lab, n = ndi.label(mask, structure=np.ones((3, 3), dtype=bool))
    if n == 0:
        return []
    sizes = np.bincount(lab.ravel())
    keep = [i for i in range(1, n + 1) if min_area <= sizes[i] <= max_area]
    if not keep:
        return []
    centers = ndi.center_of_mass(mask, lab, keep)
    means = ndi.mean(score, lab, keep)
    slices = ndi.find_objects(lab)
    blobs: list[Blob] = []
    for k, idx in enumerate(keep):
        cy, cx = centers[k]
        sl = slices[idx - 1]
        if sl is None:
            continue
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        blobs.append(
            Blob(
                label=idx,
                area=int(sizes[idx]),
                cx=float(cx),
                cy=float(cy),
                r_eq=float(math.sqrt(sizes[idx] / math.pi)),
                mean_score=float(means[k]),
                bbox=(x0, y0, x1, y1),
            )
        )
    return blobs


def _dedupe(blobs: list[Blob]) -> list[Blob]:
    out: list[Blob] = []
    for b in sorted(blobs, key=lambda z: z.area, reverse=True):
        duplicate = False
        for o in out:
            d = math.hypot(b.cx - o.cx, b.cy - o.cy)
            if d < 0.5 * (b.r_eq + o.r_eq):
                duplicate = True
                break
        if not duplicate:
            out.append(b)
    return out


def segment(score: np.ndarray, cfg: Config) -> Segmentation:
    """Return candidate discs for one feature map.

    Otsu is tried first; if it produces a degenerate mask we fall back to a
    high percentile so that a single small disc still gets a seed region.
    """
    h, w = score.shape
    n = float(h * w)
    min_area = max(9.0, cfg.min_area_frac * n)
    max_area = cfg.max_area_frac * n

    candidates: list[tuple[str, float]] = []
    if cfg.threshold_mode == "otsu":
        candidates.append(("otsu", otsu_threshold(score)))
        percentiles = (99.5, 99.0, 98.0, 96.0)
    else:
        candidates.append(
            (f"p{cfg.threshold_percentile:g}", float(np.percentile(score, cfg.threshold_percentile)))
        )
        percentiles = (99.5, 98.0, 96.0)
    for p in percentiles:
        candidates.append((f"p{p:g}", float(np.percentile(score, p))))

    last = None
    for method, thr in candidates:
        if not np.isfinite(thr):
            continue
        mask = score > thr
        if cfg.open_radius > 0:
            mask = ndi.binary_opening(mask, structure=disk(cfg.open_radius))
        mask = ndi.binary_fill_holes(mask)
        frac = float(mask.mean())
        if frac <= 0.0 or frac > 0.98:
            continue
        blobs = _components(score, mask, min_area, max_area)
        if not blobs:
            continue
        if cfg.select == "largest":
            blobs = [max(blobs, key=lambda b: b.area)]
        elif cfg.select == "brightest":
            blobs = [max(blobs, key=lambda b: b.mean_score)]
        else:
            blobs = _dedupe(blobs)[: cfg.max_objects]
        last = Segmentation(threshold=float(thr), method=method, mask=mask, blobs=blobs)
        # A percentile fallback that only finds a tiny fragment is not better
        # than a good Otsu result; accept the first sensible segmentation.
        if method.startswith("otsu") or len(blobs) > 0:
            return last

    if last is not None:
        return last
    return Segmentation(threshold=float("nan"), method="none", mask=np.zeros_like(score, bool), blobs=[])
