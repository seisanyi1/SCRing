"""Synthetic validation of the non-fixed-radius circle extractor."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
PREPROCESS = HERE.parent
sys.path.insert(0, str(PREPROCESS))
sys.path.insert(0, str(HERE))

from synth import make_disc  # noqa: E402
from wellprep.config import Config  # noqa: E402
from wellprep.imaging import load_rgb  # noqa: E402
from wellprep.pipeline import analyze  # noqa: E402
from wellprep.render import circle_mask, warp_circle  # noqa: E402


class TestCircleFit(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Config()
        self.cfg.workers = 1

    def assert_close(self, fit, truth, tol_r_frac=0.02, tol_c=1.5):
        self.assertLessEqual(abs(fit.cx - truth["cx"]), tol_c, "cx error")
        self.assertLessEqual(abs(fit.cy - truth["cy"]), tol_c, "cy error")
        self.assertLessEqual(abs(fit.r - truth["r"]) / truth["r"], tol_r_frac, "radius error")

    def test_varying_radius(self):
        for r in (30.0, 120.0, 400.0):
            rgb, truth = make_disc(
                width=1280, height=960, cx=600.0, cy=470.0, r=r,
                edge=max(2.0, 0.03 * r), seed=int(r),
            )
            result = analyze(rgb, self.cfg)
            self.assertEqual(len(result.fits), 1, f"r={r}")
            fit = result.fits[0][0]
            self.assert_close(fit, truth, tol_r_frac=0.02, tol_c=2.0)
            self.assertGreater(fit.coverage, 0.8)

    def test_border_disc(self):
        rgb, truth = make_disc(width=900, height=700, cx=120.0, cy=160.0, r=250.0, seed=7)
        result = analyze(rgb, self.cfg)
        self.assertEqual(len(result.fits), 1)
        fit = result.fits[0][0]
        self.assert_close(fit, truth, tol_r_frac=0.02, tol_c=2.5)
        self.assertTrue(fit.touches_border)

    def test_notch_and_gradient(self):
        rgb, truth = make_disc(
            width=1200, height=900, cx=620.0, cy=430.0, r=260.0,
            edge=8.0, gradient=0.03, notch_deg=45.0, seed=11,
        )
        result = analyze(rgb, self.cfg)
        self.assertEqual(len(result.fits), 1)
        self.assert_close(result.fits[0][0], truth, tol_r_frac=0.025, tol_c=2.0)

    def test_blue_distractor_and_two_discs(self):
        rgb, truth = make_disc(width=1400, height=900, cx=420.0, cy=450.0, r=180.0,
                               blue_spot=True, seed=3)
        rgb2, truth2 = make_disc(width=1400, height=900, cx=1080.0, cy=440.0, r=110.0, seed=4)
        rgb = np.clip(rgb + rgb2 * 0.9, 0.0, 1.0)
        cfg = Config(select="all")
        result = analyze(rgb, cfg)
        self.assertEqual(len(result.fits), 2, "should find exactly the two green discs")
        found = sorted([(f.cx, f.cy, f.r) for f, _ in result.fits])
        want = sorted([(truth["cx"], truth["cy"], truth["r"]), (truth2["cx"], truth2["cy"], truth2["r"])])
        for got, exp in zip(found, want):
            self.assertLessEqual(abs(got[0] - exp[0]), 2.0)
            self.assertLessEqual(abs(got[2] - exp[2]) / exp[2], 0.02)

    def test_gray_image(self):
        rgb, truth = make_disc(width=1000, height=800, cx=500.0, cy=400.0, r=170.0, seed=5)
        gray = np.asarray(Image.fromarray((rgb[..., 1] * 255).astype(np.uint8)))
        rgb_gray = np.repeat(gray[..., None].astype(np.float32) / 255.0, 3, axis=2)
        cfg = Config(feature="gray")
        result = analyze(rgb_gray, cfg)
        self.assertEqual(len(result.fits), 1)
        fit = result.fits[0][0]
        self.assertLessEqual(abs(fit.r - truth["r"]) / truth["r"], 0.02)

    def test_sixteen_bit_tiff(self):
        rgb, truth = make_disc(width=900, height=700, cx=450.0, cy=350.0, r=160.0, seed=6)
        arr = (rgb[..., 1] * 65535.0).round().astype(np.uint16)
        tmp = PREPROCESS / "_scratch" / "test_tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        path = tmp / "sixteen.tif"
        Image.fromarray(arr).save(path)
        loaded, info = load_rgb(path)
        self.assertEqual(info.bit_depth, 16)
        cfg = Config(feature="gray")
        result = analyze(loaded, cfg)
        self.assertEqual(len(result.fits), 1)
        fit = result.fits[0][0]
        self.assertLessEqual(abs(fit.r - truth["r"]) / truth["r"], 0.02)

    def test_warp_geometry(self):
        rgb, truth = make_disc(width=1000, height=800, cx=500.0, cy=400.0, r=150.0, seed=8)
        cfg = Config(out_size=256, margin=1.35)
        out = warp_circle(rgb, truth["cx"], truth["cy"], truth["r"], cfg.margin, cfg.out_size)
        self.assertEqual(out.shape, (256, 256, 3))
        # The disc's 50 % level must land at out_size/(2*margin) from the centre.
        profile = out.mean(axis=2)
        inside = profile[128, 128]
        outside = profile[128, 2]
        half = (inside + outside) / 2
        row = profile[128]
        idx = np.flatnonzero(row[128:] < half)
        self.assertTrue(idx.size > 0)
        edge = float(idx[0])
        self.assertLessEqual(abs(edge - 256 / (2 * 1.35)), 2.0)
        mask = circle_mask(256, 1.35)
        self.assertAlmostEqual(float(mask.mean()), np.pi / (4 * 1.35**2), places=2)

    def test_no_disc(self):
        rgb = np.clip(np.random.default_rng(0).normal(0.02, 0.005, (400, 500, 3)), 0, 1).astype(np.float32)
        result = analyze(rgb, Config())
        self.assertEqual(len(result.fits), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
