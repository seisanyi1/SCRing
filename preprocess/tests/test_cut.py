"""Unit test for the circular-cut module."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
PREPROCESS = HERE.parent
sys.path.insert(0, str(PREPROCESS))
sys.path.insert(0, str(HERE))

from synth import make_disc  # noqa: E402
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

        self.assertTrue(store.delete(cut["id"]))
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
