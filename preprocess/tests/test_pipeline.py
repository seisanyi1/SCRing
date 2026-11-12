"""End-to-end pipeline and YOLO-export smoke tests."""

from __future__ import annotations

import csv
import json
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
PREPROCESS = HERE.parent
sys.path.insert(0, str(PREPROCESS))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PREPROCESS / "tools"))

from synth import make_disc  # noqa: E402
from wellprep.config import Config  # noqa: E402
from wellprep.imaging import to_uint8  # noqa: E402
from wellprep.pipeline import collect_images, process_image  # noqa: E402

TMP = PREPROCESS / "_scratch" / "test_pipeline"


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TMP.mkdir(parents=True, exist_ok=True)
        cls.img_dir = TMP / "imgs" / "sub"
        cls.img_dir.mkdir(parents=True, exist_ok=True)
        rgb, _ = make_disc(width=800, height=600, cx=400.0, cy=300.0, r=160.0, seed=21)
        cls.path = cls.img_dir / "well.png"
        Image.fromarray(to_uint8(rgb)).save(cls.path)
        cls.out = TMP / "out"
        cls.cfg = Config(out_size=256, margin=1.4, workers=1)
        cls.records = process_image(cls.path, cls.cfg, cls.out, "sub")

    def test_record_and_files(self):
        self.assertEqual(len(self.records), 1)
        rec = self.records[0]
        self.assertEqual(rec["status"], "ok")
        self.assertAlmostEqual(float(rec["radius"]), 160.0, delta=4.0)
        self.assertTrue((self.out / rec["roi_norm"]).exists())
        self.assertTrue((self.out / rec["overlay"]).exists())
        self.assertTrue((self.out / rec["mask"]).exists())
        self.assertTrue((self.out / rec["roi_raw"]).exists())
        with Image.open(self.out / rec["roi_norm"]) as im:
            self.assertEqual(im.size, (256, 256))
        mask = np.asarray(Image.open(self.out / rec["mask"])).astype(float) / 255.0
        self.assertAlmostEqual(float(mask.mean()), np.pi / (4 * 1.4**2), delta=0.01)

    def test_collect_images_recursive(self):
        items = collect_images([TMP / "imgs"], recursive=True)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][1], "sub")

    def test_yolo_export(self):
        import yolo_export

        csv_path = self.out / "tables" / "wells.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["file", "rel_path", "status", "width", "height", "cx", "cy", "radius"])
            writer.writeheader()
            writer.writerow(
                dict(
                    file=str(self.path.resolve()),
                    rel_path="sub/well.png",
                    status="ok",
                    width=800,
                    height=600,
                    cx=400.0,
                    cy=300.0,
                    radius=160.0,
                )
            )
        ds = TMP / "yolo"
        rc = yolo_export.main(["--csv", str(csv_path), "--out", str(ds), "--mode", "seg", "--val-pct", "0"])
        self.assertEqual(rc, 0)
        labels = list((ds / "labels" / "train").glob("*.txt"))
        self.assertEqual(len(labels), 1)
        parts = labels[0].read_text(encoding="utf-8").split()
        self.assertEqual(parts[0], "0")
        self.assertEqual(len(parts) - 1, 72 * 2)
        vals = np.array([float(v) for v in parts[1:]])
        self.assertTrue(np.all(vals >= 0.0) and np.all(vals <= 1.0))
        yaml_text = (ds / "data.yaml").read_text(encoding="utf-8")
        self.assertIn("images/train", yaml_text)
        stats = json.loads((ds / "stats.json").read_text(encoding="utf-8"))
        self.assertEqual(stats["images"]["train"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
