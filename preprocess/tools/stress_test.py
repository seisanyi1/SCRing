"""Stress-test the extractor on transformed copies of a real image.

Run:  python preprocess/tools/stress_test.py
It writes validation/stress_results.csv and validation/stress_sheet.png.
"""

from __future__ import annotations

import csv
import io
import math
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi

HERE = Path(__file__).resolve().parent
PREPROCESS = HERE.parent
sys.path.insert(0, str(PREPROCESS))

from wellprep.config import Config  # noqa: E402
from wellprep.imaging import load_rgb  # noqa: E402
from wellprep.pipeline import analyze  # noqa: E402
from wellprep.render import draw_overlay, make_thumb  # noqa: E402

SAMPLE = PREPROCESS / "samples" / "26-3-662.jpg"
OUT = PREPROCESS / "validation"


def _resize(rgb: np.ndarray, s: float) -> np.ndarray:
    img = Image.fromarray(np.clip(rgb * 255, 0, 255).astype(np.uint8))
    img = img.resize((max(8, round(img.width * s)), max(8, round(img.height * s))), Image.LANCZOS)
    return np.asarray(img).astype(np.float32) / 255.0


def _jpeg(rgb: np.ndarray, q: int) -> np.ndarray:
    buf = io.BytesIO()
    Image.fromarray(np.clip(rgb * 255, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=q)
    buf.seek(0)
    return np.asarray(Image.open(buf)).astype(np.float32) / 255.0


def _add_disc(rgb: np.ndarray, cx: float, cy: float, r: float) -> np.ndarray:
    from scipy.special import erf

    yy, xx = np.mgrid[0 : rgb.shape[0], 0 : rgb.shape[1]].astype(np.float32)
    d = np.hypot(xx - cx, yy - cy)
    ramp = 0.5 * (1.0 - erf((d - r) / (np.sqrt(2.0) * 6.0)))
    out = rgb.copy()
    out[..., 1] = np.clip(out[..., 1] + 0.42 * ramp, 0, 1)
    out[..., 0] = np.clip(out[..., 0] + 0.05 * ramp, 0, 1)
    out[..., 2] = np.clip(out[..., 2] + 0.14 * ramp, 0, 1)
    return out


def variants(rgb: np.ndarray):
    h, w = rgb.shape[:2]
    yield "original", rgb, (lambda x, y: (x, y)), 1.0
    for s in (0.5, 0.75, 1.25):
        yield f"scale_{s:g}", _resize(rgb, s), (lambda x, y, s=s: (x * s, y * s)), s
    yield "rot90", np.rot90(rgb, 1), (lambda x, y: (y, w - 1 - x)), 1.0
    yield "rot180", np.rot90(rgb, 2), (lambda x, y: (w - 1 - x, h - 1 - y)), 1.0
    yield "bright_0.6", np.clip(rgb * 0.6, 0, 1), (lambda x, y: (x, y)), 1.0
    yield "bright_1.4", np.clip(rgb * 1.4, 0, 1), (lambda x, y: (x, y)), 1.0
    yield "gamma_0.8", np.clip(rgb, 0, 1) ** 0.8, (lambda x, y: (x, y)), 1.0
    yield "gamma_1.2", np.clip(rgb, 0, 1) ** 1.2, (lambda x, y: (x, y)), 1.0
    yield "jpeg_q35", _jpeg(rgb, 35), (lambda x, y: (x, y)), 1.0
    yield "blur_s2", ndi.gaussian_filter(rgb, (2, 2, 0)), (lambda x, y: (x, y)), 1.0
    rng = np.random.default_rng(42)
    yield "noise_0.03", np.clip(rgb + rng.normal(0, 0.03, rgb.shape), 0, 1), (lambda x, y: (x, y)), 1.0
    yield "two_discs", _add_disc(rgb, 2150.0, 320.0, 150.0), (lambda x, y: (x, y)), 1.0


def _font(size: int):
    candidates = [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path("arial.ttf"),
        Path("DejaVuSans.ttf"),
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(str(p), size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rgb, info = load_rgb(SAMPLE)
    h, w = rgb.shape[:2]
    base_cfg = Config()
    base_cfg.workers = 1
    rows = []
    thumbs = []

    for name, img, map_xy, scale in variants(rgb):
        cfg = Config() if name != "two_discs" else Config(select="all")
        cfg.workers = 1
        t0 = time.perf_counter()
        result = analyze(img, cfg)
        elapsed = time.perf_counter() - t0
        # Expected centre in the transformed frame.
        ex, ey = map_xy(1428.496, 740.878)
        exp_r = 479.283 * scale
        fits = [f for f, _ in result.fits]
        if not fits:
            rows.append(dict(variant=name, status="not_found", n=0, elapsed=round(elapsed, 3)))
            continue
        if name == "two_discs":
            main_fit = min(fits, key=lambda f: math.hypot(f.cx - ex, f.cy - ey))
        else:
            main_fit = min(fits, key=lambda f: math.hypot(f.cx - ex, f.cy - ey))
        dc = math.hypot(main_fit.cx - ex, main_fit.cy - ey)
        dr = (main_fit.r - exp_r) / exp_r
        rows.append(
            dict(
                variant=name,
                status="ok" if len(fits) == (2 if name == "two_discs" else 1) else "extra",
                n=len(fits),
                cx=round(main_fit.cx, 2),
                cy=round(main_fit.cy, 2),
                radius=round(main_fit.r, 2),
                expected_r=round(exp_r, 2),
                center_err_px=round(dc, 2),
                radius_err_pct=round(100 * dr, 2),
                coverage=round(main_fit.coverage, 3),
                snr=round(main_fit.snr, 1),
                edge_width=round(main_fit.edge_width, 1),
                elapsed=round(elapsed, 3),
            )
        )
        ov = draw_overlay(img, main_fit.cx, main_fit.cy, main_fit.r, 1200)
        th = make_thumb(ov, 420)
        thumbs.append((name, th, dc, 100 * dr))

    fields = [
        "variant", "status", "n", "cx", "cy", "radius", "expected_r",
        "center_err_px", "radius_err_pct", "coverage", "snr", "edge_width", "elapsed",
    ]
    with open(OUT / "stress_results.csv", "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields, restval="")
        wr.writeheader()
        for r in rows:
            wr.writerow(r)

    # Contact sheet
    cols = 4
    cell_w, cell_h = 420, 288
    rows_n = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * cell_w, rows_n * cell_h), (250, 250, 252))
    draw = ImageDraw.Draw(sheet)
    font = _font(17)
    small = _font(14)
    for i, (name, th, dc, dr) in enumerate(thumbs):
        cx = (i % cols) * cell_w
        cy = (i // cols) * cell_h
        im = Image.fromarray(np.clip(th * 255, 0, 255).astype(np.uint8))
        im.thumbnail((cell_w - 8, 232), Image.LANCZOS)
        px = cx + 4 + (cell_w - 8 - im.width) // 2
        py = cy + 4 + (232 - im.height) // 2
        sheet.paste(im, (px, py))
        draw.text((cx + 6, cy + 242), name, fill=(20, 30, 45), font=font)
        draw.text((cx + 6, cy + 264), f"dC={dc:.1f}px  dR={dr:+.2f}%", fill=(80, 90, 105), font=small)
        draw.rectangle([cx, cy, cx + cell_w - 1, cy + cell_h - 1], outline=(220, 224, 230))
    sheet.save(OUT / "stress_sheet.png")

    print(f"{'variant':14s} {'n':>2s} {'dC(px)':>7s} {'dR(%)':>7s} {'cov':>5s} {'SNR':>6s} {'ms':>6s}")
    for r in rows:
        if r.get("status") == "not_found":
            print(f"{r['variant']:14s}  0  not found")
            continue
        print(
            f"{r['variant']:14s} {r['n']:2d} {r['center_err_px']:7.2f} {r['radius_err_pct']:7.2f} "
            f"{r['coverage']:5.2f} {r['snr']:6.1f} {r['elapsed']*1000:6.0f}"
        )
    errs_c = [r["center_err_px"] for r in rows if "center_err_px" in r]
    errs_r = [abs(r["radius_err_pct"]) for r in rows if "radius_err_pct" in r]
    print(f"\ncenter error: max {max(errs_c):.2f} px, mean {np.mean(errs_c):.2f} px")
    print(f"radius error: max {max(errs_r):.2f} %, mean {np.mean(errs_r):.2f} %")
    print(f"sheet: {OUT / 'stress_sheet.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
