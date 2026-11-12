"""Circular-domain cutting: keep only the extracted disc and split it by angle."""

from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi

from .imaging import load_rgb, save_rgb, to_uint8
from .render import circle_mask, warp_circle, _anti_alias

_FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def _font(size: int):
    for name in ("times.ttf", "timesbd.ttf", "georgia.ttf"):
        try:
            return ImageFont.truetype(str(_FONT_DIR / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def auto_center(rgb: np.ndarray, cx: float, cy: float, r: float) -> tuple[float, float, float]:
    """Intensity-weighted centroid inside the disc (graph-based centering check)."""
    h, w = rgb.shape[:2]
    x0 = max(0, int(math.floor(cx - r)))
    x1 = min(w, int(math.ceil(cx + r)) + 1)
    y0 = max(0, int(math.floor(cy - r)))
    y1 = min(h, int(math.ceil(cy + r)) + 1)
    if x1 <= x0 or y1 <= y0:
        return float(cx), float(cy), 0.0
    sub = rgb[y0:y1, x0:x1].astype(np.float32)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    inside = (xx - cx) ** 2 + (yy - cy) ** 2 <= (0.95 * r) ** 2
    score = sub[..., 1] - 0.5 * (sub[..., 0] + sub[..., 2])
    weights = np.clip(score, 0.0, None) * inside
    total = float(weights.sum())
    if total <= 1e-8:
        return float(cx), float(cy), 0.0
    ax = float((xx * weights).sum() / total)
    ay = float((yy * weights).sum() / total)
    return ax, ay, math.hypot(ax - cx, ay - cy)


def _sector_images(
    circle: np.ndarray,
    angle_step: float,
    out_dir: Path,
    prefix: str,
    font,
) -> tuple[list[dict], list[np.ndarray]]:
    size = circle.shape[0]
    yy, xx = np.mgrid[0:size, 0:size]
    cx = cy = size / 2.0
    ang = (np.degrees(np.arctan2(yy + 0.5 - cy, xx + 0.5 - cx)) + 360.0) % 360.0
    rad = np.hypot(xx + 0.5 - cx, yy + 0.5 - cy)
    disk = rad <= (size / 2.0 - 0.5)

    sectors: list[dict] = []
    patches: list[np.ndarray] = []
    n = int(math.ceil(360.0 / angle_step))
    green = circle[..., 1] - 0.5 * (circle[..., 0] + circle[..., 2])
    gray = 0.299 * circle[..., 0] + 0.587 * circle[..., 1] + 0.114 * circle[..., 2]
    for i in range(n):
        start = i * angle_step
        end = min(360.0, start + angle_step)
        wedge = disk & (ang >= start) & (ang < end)
        if not np.any(wedge):
            continue
        img = circle * wedge[..., None]
        def angle_label(value):
            return f"{int(value):03d}" if float(value).is_integer() else f"{value:010.6f}".rstrip("0").rstrip(".")
        name = f"{prefix}-{angle_label(start)}-{angle_label(end)}deg.png"
        save_rgb(out_dir / name, img)
        vals = green[wedge]
        gvals = gray[wedge]
        sectors.append(
            {
                "index": i + 1,
                "start": round(start, 2),
                "end": round(end, 2),
                "center": round((start + end) / 2.0, 2),
                "pixels": int(np.count_nonzero(wedge)),
                "area_frac": round(float(np.count_nonzero(wedge)) / max(1, np.count_nonzero(disk)), 6),
                "green_mean": round(float(np.mean(vals)), 5) if vals.size else None,
                "green_median": round(float(np.median(vals)), 5) if vals.size else None,
                "green_p10": round(float(np.percentile(vals, 10)), 5) if vals.size else None,
                "green_p90": round(float(np.percentile(vals, 90)), 5) if vals.size else None,
                "gray_mean": round(float(np.mean(gvals)), 5) if gvals.size else None,
                "file": name,
            }
        )
        patches.append(img)
    return sectors, patches


def aligned_sector(rgb, cx, cy, radius, size, start, end):
    """Inverse-map original RGB once; image-clockwise [start,end] -> Cartesian [0,width]."""
    if not (radius > 0 and size >= 2 and 0 < end - start <= 360):
        raise ValueError("Invalid sector geometry")
    center = (size - 1) / 2.0
    yy, xx = np.mgrid[:size, :size]
    dx, dy = xx - center, yy - center
    theta = np.mod(np.degrees(np.arctan2(-dy, dx)), 360.0)
    mask = (np.hypot(dx, dy) <= size / 2.0 - 0.5) & (theta < end - start)
    # Inverse of a Cartesian CCW rotation by end degrees, represented in y-down pixels.
    a = math.radians(end)
    scale = 2.0 * radius / size
    sx = cx + scale * (math.cos(a) * dx - math.sin(a) * dy)
    sy = cy + scale * (math.sin(a) * dx + math.cos(a) * dy)
    source = _anti_alias(rgb, scale)
    output = np.stack([ndi.map_coordinates(source[..., c], [sy, sx], order=1,
                       mode="constant", cval=0.0) for c in range(3)], axis=-1)
    return np.clip(output * mask[..., None], 0.0, 1.0)


def _contact_sheet(patches: list[np.ndarray], sectors: list[dict], path: Path) -> None:
    if not patches:
        return
    cell = 168
    cols = min(6, len(patches))
    rows = int(math.ceil(len(patches) / cols))
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 24)), (250, 250, 250))
    draw = ImageDraw.Draw(sheet)
    font = _font(13)
    for i, (patch, sector) in enumerate(zip(patches, sectors)):
        x = (i % cols) * cell
        y = (i // cols) * (cell + 24)
        img = Image.fromarray(to_uint8(patch)).resize((cell - 8, cell - 8), Image.LANCZOS)
        sheet.paste(img, (x + 4, y + 4))
        draw.text(
            (x + 6, y + cell + 2),
            f"{sector['start']:.0f}-{sector['end']:.0f} deg",
            fill=(40, 48, 60),
            font=font,
        )
    sheet.save(path)


def _overlay(circle: np.ndarray, angle_step: float, path: Path) -> None:
    size = circle.shape[0]
    img = Image.fromarray(to_uint8(circle))
    draw = ImageDraw.Draw(img)
    c = size / 2.0
    rad = size / 2.0 - 1
    draw.ellipse([c - rad, c - rad, c + rad, c + rad], outline=(230, 60, 60), width=2)
    n = int(math.ceil(360.0 / angle_step))
    font = _font(12)
    for i in range(n):
        a = math.radians(i * angle_step)
        draw.line(
            [c, c, c + rad * math.cos(a), c + rad * math.sin(a)],
            fill=(230, 60, 60),
            width=1,
        )
        if n <= 24 or i % max(1, n // 12) == 0:
            tx = c + (rad * 0.86) * math.cos(a + math.radians(angle_step / 2))
            ty = c + (rad * 0.86) * math.sin(a + math.radians(angle_step / 2))
            label = f"{int(round(i * angle_step))}"
            draw.text((tx - 7, ty - 6), label, fill=(255, 255, 255), font=font)
    img.save(path)


def make_cut(
    source: str | Path,
    cx: float,
    cy: float,
    radius: float,
    angle_step: float,
    out_size: int,
    center_mode: str,
    out_dir: str | Path,
    meta: dict,
) -> dict:
    """Create a masked circular cut plus angular sector images and statistics."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rgb, _ = load_rgb(source)
    ax, ay, offset = auto_center(rgb, cx, cy, radius)
    if center_mode == "auto":
        ux, uy = ax, ay
    else:
        ux, uy = cx, cy

    circle = warp_circle(rgb, ux, uy, radius, 1.0, int(out_size))
    disk = circle_mask(int(out_size), 1.0)
    circle = np.clip(circle * disk[..., None], 0.0, 1.0)
    save_rgb(out_dir / "circle.png", circle)

    sectors_dir = out_dir / "sectors"
    sectors_dir.mkdir(exist_ok=True)
    sectors, patches = _sector_images(circle, angle_step, sectors_dir, Path(source).stem, _font(12))
    aligned_dir = out_dir / "sectors_aligned"
    aligned_dir.mkdir(exist_ok=True)
    aligned_patches = []
    for row in sectors:
        patch = aligned_sector(rgb, ux, uy, radius, int(out_size), row["start"], row["end"])
        save_rgb(aligned_dir / row["file"], patch)
        row["aligned_file"] = "sectors_aligned/" + row["file"]
        row["rotation_ccw_deg"] = row["end"] % 360.0
        row["aligned_start_deg"] = 0.0
        row["aligned_end_deg"] = row["end"] - row["start"]
        aligned_patches.append(patch)
    _contact_sheet(aligned_patches, sectors, out_dir / "aligned_contact_sheet.png")
    contact_path = out_dir / "contact_sheet.png"
    _contact_sheet(patches, sectors, contact_path)
    overlay_path = out_dir / "overlay.png"
    _overlay(circle, angle_step, overlay_path)

    fields = [
        "index", "start", "end", "center", "pixels", "area_frac",
        "green_mean", "green_median", "green_p10", "green_p90", "gray_mean", "file", "aligned_file", "rotation_ccw_deg",
        "aligned_start_deg", "aligned_end_deg",
    ]
    with open(out_dir / "sectors.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, restval="")
        writer.writeheader()
        for row in sectors:
            writer.writerow(row)

    info = {
        **meta,
        "cx": round(cx, 3),
        "cy": round(cy, 3),
        "radius": round(radius, 3),
        "auto_cx": round(ax, 3),
        "auto_cy": round(ay, 3),
        "center_offset": round(offset, 3),
        "center_mode": center_mode,
        "angle_step": angle_step,
        "sectors": len(sectors),
        "out_size": int(out_size),
        "circle_file": "circle.png",
        "overlay_file": "overlay.png",
        "contact_file": "contact_sheet.png",
        "csv_file": "sectors.csv",
        "aligned_contact_file": "aligned_contact_sheet.png",
        "alignment": {
            "version": 1, "source_center_xy": [ux, uy],
            "output_center_pixel_xy": [(int(out_size) - 1) / 2.0] * 2,
            "source_pixels_per_output_pixel": 2.0 * radius / int(out_size),
            "source_angles": "clockwise from +x (image y down)",
            "output_angles": "counterclockwise from +x (Cartesian y up)",
            "sampling": "original RGB, antialias downsampling, bilinear, mask after sampling",
            "output_bit_depth": 8,
        },
    }
    with open(out_dir / "cut.json", "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=2)
    return {"info": info, "sectors": sectors}
