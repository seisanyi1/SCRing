"""Unit test for the circular-cut module."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PIL import Image
import numpy as np
import zipfile

HERE = Path(__file__).resolve().parent
PREPROCESS = HERE.parent
sys.path.insert(0, str(PREPROCESS))
sys.path.insert(0, str(HERE))

from synth import make_disc  # noqa: E402
from wellprep.cutter import aligned_sector
from wellprep.cuts import CutStore  # noqa: E402
from wellprep.imaging import to_uint8  # noqa: E402

TMP = PREPROCESS / "_scratch" / "test_cut"


class TestCut(unittest.TestCase):
    def test_create_and_delete(self):
        TMP.mkdir(parents=True, exist_ok=True)
        img_dir = TMP / "imgs"
        img_dir.mkdir(exist_ok=True)
        rgb, truth = make_disc(width=640, height=480, cx=320.0, cy=240.0, r=120.0, seed=41)
        path = img_dir / "well.png"
        Image.fromarray(to_uint8(rgb)).save(path)

        store = CutStore(TMP / "cuts")
        cut = store.create(
            {"id": "run-1", "name": "unit"},
            {
                "file": str(path),
                "rel_path": "well.png",
                "cx": truth["cx"],
                "cy": truth["cy"],
                "radius": truth["r"],
            },
            {"angle_step": 45, "out_size": 256, "center_mode": "geometric"},
        )
        self.assertEqual(cut["sectors"], 8)
        self.assertEqual(len(cut["sector_rows"]), 8)
        self.assertLess(cut["center_offset"], 5.0)
        out = Path(cut["output"])
        self.assertTrue((out / "circle.png").exists())
        self.assertTrue((out / "overlay.png").exists())
        self.assertTrue((out / "contact_sheet.png").exists())
        self.assertTrue((out / "sectors.csv").exists())
        self.assertEqual(len(list((out / "sectors").glob("*.png"))), 8)

        self.assertEqual(len(list((out / "sectors_aligned").glob("*.png"))), 8)
        self.assertTrue((out / "aligned_contact_sheet.png").is_file())
        with zipfile.ZipFile(store.zip_sectors(cut["id"])) as z:
            self.assertEqual(len([n for n in z.namelist() if n.startswith("sectors_aligned/")]), 8)
        self.assertTrue(store.delete(cut["id"]))
        self.assertFalse(out.exists())

    def test_alignment_not_mirrored(self):
        yy, xx = np.mgrid[:201, :201]
        rgb = np.stack([xx / 200, yy / 200, np.ones_like(xx)*0.7], axis=-1).astype(np.float32)
        # Asymmetric coordinate ramps reveal rotation direction and reflection.
        for end in (45, 90, 180, 270, 360):
            out = aligned_sector(rgb, 100, 100, 80, 160, end-45, end)
            x,y=110,65
            dx,dy=x-79.5,y-79.5
            a=np.radians(end)
            sx=100+np.cos(a)*dx-np.sin(a)*dy
            sy=100+np.sin(a)*dx+np.cos(a)*dy
            np.testing.assert_allclose(out[y,x], [sx/200,sy/200,0.7],atol=1e-6)
            self.assertEqual(float(out[81:,:,:].sum()),0.0)
            self.assertEqual(float(out[:,:79,:].sum()),0.0)

    def test_last_partial_sector(self):
        rgb=np.ones((201,201,3),dtype=np.float32)
        out=aligned_sector(rgb,100,100,80,160,350,360)
        self.assertGreater(float(out[75,120].sum()),0)
        self.assertEqual(float(out[50,120].sum()),0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
