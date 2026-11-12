"""Pipeline: analyse one image, write normalised ROIs and QC artefacts."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from .circle import FitResult, initial_circle, refine_circle
from .config import Config
from .imaging import (
    IMAGE_EXTS,
    feature_map,
    load_rgb,
    save_gray,
    save_rgb,
    stretch_for_display,
)
from .render import (
    circle_mask,
    downscale,
    draw_overlay,
    make_thumb,
    polar_unwrap,
    raw_crop,
    warp_circle,
)
from .segment import Segmentation, segment

CSV_FIELDS = [
    "file",
    "rel_path",
    "status",
    "note",
    "width",
    "height",
    "cx",
    "cy",
    "radius",
    "diameter",
    "r_area",
    "r_min",
    "r_max",
    "radial_cv",
    "coverage",
    "contrast",
    "snr",
    "edge_width",
    "residual_rms",
    "saturation",
    "touches_border",
    "threshold",
    "threshold_method",
    "feature",
    "n_objects",
    "object_index",
    "roi_norm",
    "roi_raw",
    "mask",
    "polar",
    "overlay",
    "thumb",
    "elapsed_ms",
]


@dataclass
class Analysis:
    width: int
    height: int
    segmentation: Segmentation
    fits: list[tuple[FitResult, float]] = field(default_factory=list)  # (fit, blob area)
    saturation: list[float] = field(default_factory=list)


def _saturation(rgb: np.ndarray, fit: FitResult) -> float:
    h, w = rgb.shape[:2]
    x0 = max(0, int(math.floor(fit.cx - fit.r)))
    x1 = min(w, int(math.ceil(fit.cx + fit.r)) + 1)
    y0 = max(0, int(math.floor(fit.cy - fit.r)))
    y1 = min(h, int(math.ceil(fit.cy + fit.r)) + 1)
    if x1 <= x0 or y1 <= y0:
        return float("nan")
    sub = rgb[y0:y1, x0:x1]
    yy, xx = np.ogrid[y0:y1, x0:x1]
    inside = (xx - fit.cx) ** 2 + (yy - fit.cy) ** 2 <= (0.95 * fit.r) ** 2
    if not np.any(inside):
        return float("nan")
    sat = np.max(sub, axis=2) >= 0.98
    return float(np.count_nonzero(sat & inside) / np.count_nonzero(inside))


def analyze(rgb: np.ndarray, cfg: Config) -> Analysis:
    score = feature_map(rgb, cfg.feature)
    score_s = ndi.gaussian_filter(score, cfg.smooth_sigma, mode="nearest")
    seg = segment(score_s, cfg)
    fits: list[tuple[FitResult, float]] = []
    sats: list[float] = []
    for blob in seg.blobs:
        cx0, cy0, r0 = initial_circle(seg.mask, blob.cx, blob.cy, blob.r_eq, blob.bbox)
        fit = refine_circle(
            score_s,
            cx0,
            cy0,
            r0,
            cfg,
            threshold=seg.threshold,
            r_area=blob.r_eq,
        )
        if not (np.isfinite(fit.cx) and np.isfinite(fit.cy) and np.isfinite(fit.r)):
            continue
        if fit.r < 2:
            continue
        if not np.isfinite(fit.contrast) or fit.contrast < cfg.min_contrast:
            continue
        try:
            sat = _saturation(rgb, fit)
        except Exception:
            sat = float("nan")
        fits.append((fit, float(blob.area)))
        sats.append(sat)
    return Analysis(rgb.shape[0], rgb.shape[1], seg, fits, sats)


def _status(fit: FitResult, cfg: Config) -> tuple[str, list[str]]:
    notes: list[str] = []
    hard: list[str] = []
    if fit.method != "radial":
        hard.append("edge_fit_failed")
    if np.isfinite(fit.coverage) and fit.coverage < cfg.min_coverage:
        hard.append("low_coverage")
    if np.isfinite(fit.snr) and fit.snr < cfg.min_snr:
        hard.append("low_snr")
    if np.isfinite(fit.residual_rms) and fit.residual_rms / max(fit.r, 1e-6) > cfg.max_residual_rel:
        hard.append("irregular_edge")
    if np.isfinite(fit.edge_width) and fit.edge_width / max(fit.r, 1e-6) > cfg.max_edge_width_frac:
        hard.append("blurred_edge")
    if fit.r < cfg.min_radius_px:
        hard.append("small_radius")
    if fit.touches_border:
        notes.append("touches_border")
    if math.isnan(fit.snr):
        notes.append("snr_na")
    return ("ok" if not hard else "review"), hard + notes


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def process_image(
    path: str | Path,
    cfg: Config,
    out_root: str | Path,
    rel_dir: str = "",
) -> list[dict]:
    """Analyse one image and write every configured artefact.

    Returns one record per detected disc (empty geometry + status="not_found"
    if nothing is found).
    """
    path = Path(path)
    out_root = Path(out_root)
    t0 = time.perf_counter()
    rgb, info = load_rgb(path)
    result = analyze(rgb, cfg)
    h, w = rgb.shape[:2]
    stem = path.stem or "image"
    n = len(result.fits)

    sub = Path(rel_dir) if rel_dir else Path()
    dirs = {
        "roi_norm": out_root / "roi_norm" / sub,
        "roi_raw": out_root / "roi_raw" / sub,
        "mask": out_root / "mask" / sub,
        "polar": out_root / "polar" / sub,
        "overlay": out_root / "overlay" / sub,
        "thumb": out_root / "thumb" / sub,
    }

    records: list[dict] = []
    if n == 0:
        ov = downscale(rgb, cfg.overlay_max)
        ov_path = dirs["overlay"] / f"{stem}.jpg"
        th_path = dirs["thumb"] / f"{stem}.jpg"
        save_rgb(ov_path, ov, cfg.jpeg_quality)
        save_rgb(th_path, make_thumb(ov), 88)
        records.append(
            {
                "file": str(path.resolve()),
                "rel_path": (Path(rel_dir) / path.name).as_posix(),
                "status": "not_found",
                "note": "no_green_blob",
                "width": w,
                "height": h,
                "feature": cfg.feature,
                "threshold": result.segmentation.threshold,
                "threshold_method": result.segmentation.method,
                "n_objects": 0,
                "object_index": 0,
                "overlay": _rel(ov_path, out_root),
                "thumb": _rel(th_path, out_root),
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            }
        )
        return records

    for i, (fit, area) in enumerate(result.fits):
        suffix = "" if n == 1 else f"__w{i + 1:02d}"
        name = f"{stem}{suffix}"
        status, notes = _status(fit, cfg)

        norm = warp_circle(rgb, fit.cx, fit.cy, fit.r, cfg.margin, cfg.out_size)
        norm = stretch_for_display(norm, cfg.stretch)
        norm_path = dirs["roi_norm"] / f"{name}.png"
        save_rgb(norm_path, norm, cfg.jpeg_quality)

        raw_path = None
        if cfg.save_raw:
            raw = raw_crop(rgb, fit.cx, fit.cy, fit.r, cfg.margin)
            raw = stretch_for_display(raw, cfg.stretch)
            raw_path = dirs["roi_raw"] / f"{name}.jpg"
            save_rgb(raw_path, raw, cfg.jpeg_quality)

        mask_path = None
        if cfg.save_mask:
            mask = circle_mask(cfg.out_size, cfg.margin)
            mask_path = dirs["mask"] / f"{name}.png"
            save_gray(mask_path, mask)

        polar_path = None
        if cfg.save_polar:
            pol = polar_unwrap(rgb, fit.cx, fit.cy, fit.r, cfg.polar_theta, cfg.polar_r)
            polar_path = dirs["polar"] / f"{name}.png"
            save_rgb(polar_path, pol, cfg.jpeg_quality)

        overlay = draw_overlay(rgb, fit.cx, fit.cy, fit.r, cfg.overlay_max)
        ov_path = dirs["overlay"] / f"{name}.jpg"
        save_rgb(ov_path, overlay, cfg.jpeg_quality)
        th_path = dirs["thumb"] / f"{name}.jpg"
        save_rgb(th_path, make_thumb(overlay), 88)

        records.append(
            {
                "file": str(path.resolve()),
                "rel_path": (Path(rel_dir) / path.name).as_posix(),
                "status": status,
                "note": ";".join(notes),
                "width": w,
                "height": h,
                "cx": round(fit.cx, 3),
                "cy": round(fit.cy, 3),
                "radius": round(fit.r, 3),
                "diameter": round(2.0 * fit.r, 3),
                "r_area": round(fit.r_area, 3),
                "r_min": round(fit.r_min, 3),
                "r_max": round(fit.r_max, 3),
                "radial_cv": round(fit.radial_cv, 4),
                "coverage": round(fit.coverage, 4),
                "contrast": round(fit.contrast, 5),
                "snr": round(fit.snr, 2) if np.isfinite(fit.snr) else "",
                "edge_width": round(fit.edge_width, 2) if np.isfinite(fit.edge_width) else "",
                "residual_rms": round(fit.residual_rms, 3),
                "saturation": round(result.saturation[i], 4) if i < len(result.saturation) else "",
                "touches_border": int(fit.touches_border),
                "threshold": round(fit.threshold, 5) if np.isfinite(fit.threshold) else "",
                "threshold_method": result.segmentation.method,
                "feature": cfg.feature,
                "n_objects": n,
                "object_index": i + 1,
                "roi_norm": _rel(norm_path, out_root),
                "roi_raw": _rel(raw_path, out_root) if raw_path else "",
                "mask": _rel(mask_path, out_root) if mask_path else "",
                "polar": _rel(polar_path, out_root) if polar_path else "",
                "overlay": _rel(ov_path, out_root),
                "thumb": _rel(th_path, out_root),
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            }
        )
    return records


def collect_images(
    inputs: list[str | Path],
    recursive: bool = True,
    exclude: str | Path | None = None,
) -> list[tuple[Path, str]]:
    """Expand files/directories into (path, relative directory) pairs."""
    out: list[tuple[Path, str]] = []
    seen: set[str] = set()
    exclude_res = Path(exclude).resolve() if exclude else None

    def add(p: Path, rel_dir: str) -> None:
        key = str(p.resolve()).lower()
        if key in seen:
            return
        if exclude_res is not None:
            try:
                p.resolve().relative_to(exclude_res)
                return
            except ValueError:
                pass
        seen.add(key)
        out.append((p, rel_dir))

    for item in inputs:
        p = Path(item)
        if p.is_file():
            if p.suffix.lower() in IMAGE_EXTS:
                add(p, "")
            continue
        if not p.is_dir():
            continue
        it = p.rglob("*") if recursive else p.glob("*")
        for f in it:
            if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
                try:
                    rel_dir = f.parent.relative_to(p).as_posix()
                except ValueError:
                    rel_dir = ""
                add(f, rel_dir)
    return out
