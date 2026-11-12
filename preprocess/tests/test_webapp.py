"""Backend tests for the local UI: run history, results and browser API."""

from __future__ import annotations

import sys
import time
import unittest
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
PREPROCESS = HERE.parent
sys.path.insert(0, str(PREPROCESS))
sys.path.insert(0, str(HERE))

from synth import make_disc  # noqa: E402
from wellprep.imaging import to_uint8  # noqa: E402
from wellprep.webapp import RunManager  # noqa: E402

TMP = PREPROCESS / "_scratch" / "test_webapp"


class TestRunManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TMP.mkdir(parents=True, exist_ok=True)
        cls.imgs = TMP / "imgs"
        cls.imgs.mkdir(exist_ok=True)
        rgb, _ = make_disc(width=640, height=480, cx=320.0, cy=240.0, r=120.0, seed=31)
        Image.fromarray(to_uint8(rgb)).save(cls.imgs / "well.png")
        cls.runs = TMP / "runs"
        cls.mgr = RunManager(cls.runs)

    def test_01_scan_and_browse(self):
        scan = self.mgr.scan([str(self.imgs)])
        self.assertEqual(scan["count"], 1)
        self.assertEqual(scan["missing"], [])
        fs = self.mgr.browse(str(self.imgs))
        self.assertTrue(fs["path"].endswith("imgs"))
        self.assertEqual(fs["image_count"], 1)

    def test_02_start_wait_results_delete(self):
        run = self.mgr.start(
            {
                "name": "unit-run",
                "inputs": [str(self.imgs)],
                "config": {"workers": 1, "out_size": 256, "margin": 1.35},
            }
        )
        rid = run["id"]
        deadline = time.time() + 40
        status = None
        while time.time() < deadline:
            data = self.mgr.status(rid)
            status = data["run"]
            if status["status"] not in ("queued", "running"):
                break
            time.sleep(0.2)
        self.assertEqual(status["status"], "done", status.get("error"))
        self.assertEqual(status["counts"]["ok"], 1)
        self.assertTrue((Path(status["output"]) / "tables" / "wells.csv").exists())
        self.assertTrue((Path(status["output"]) / "report.html").exists())

        res = self.mgr.results(rid, {"status": "ok", "q": "well"})
        self.assertEqual(res["total"], 1)
        row = res["rows"][0]
        self.assertAlmostEqual(row["radius"], 120.0, delta=3.0)
        self.assertTrue(row["thumb_url"])

        listed = self.mgr.list()
        self.assertTrue(any(r["id"] == rid for r in listed))
        deleted = self.mgr.delete(rid, delete_files=True)
        self.assertIsNotNone(deleted)
        self.assertFalse(Path(status["output"]).exists())
        self.assertNotIn(rid, [r["id"] for r in self.mgr.list()])

    def test_03_reject_empty_input(self):
        with self.assertRaises(ValueError):
            self.mgr.start({"inputs": [str(TMP / "does-not-exist")], "config": {}})

    def test_04_preview_and_custom_output(self):
        prev = self.mgr.preview({"input": str(self.imgs), "config": {"out_size": 256}})
        self.assertIsNotNone(prev["fit"])
        self.assertGreater(prev["fit"]["r"], 50)
        self.assertIn("/previews/", prev["overlay_url"])
        self.assertIn("/previews/", prev["roi_url"])

        custom = TMP / "custom_out"
        custom.mkdir(exist_ok=True)
        run = self.mgr.start(
            {
                "name": "custom-run",
                "inputs": [str(self.imgs)],
                "output_root": str(custom),
                "config": {"workers": 1, "out_size": 256},
            }
        )
        rid = run["id"]
        deadline = time.time() + 40
        status = None
        while time.time() < deadline:
            status = self.mgr.status(rid)["run"]
            if status["status"] not in ("queued", "running"):
                break
            time.sleep(0.2)
        self.assertEqual(status["status"], "done", status.get("error"))
        self.assertEqual(status["output_parent"], str(custom.resolve()))
        self.assertEqual(Path(status["output"]).parent, custom.resolve())
        self.assertTrue((Path(status["output"]) / "tables" / "wells.csv").exists())

        self.mgr.delete(rid, delete_files=True)
        self.assertFalse(Path(status["output"]).exists())
        self.assertTrue(custom.exists())

    def test_05_exclude_and_delete_image(self):
        two = TMP / "two"
        two.mkdir(exist_ok=True)
        shutil.copy2(self.imgs / "well.png", two / "a.png")
        shutil.copy2(self.imgs / "well.png", two / "b.png")
        run = self.mgr.start(
            {
                "name": "exclude-delete-run",
                "inputs": [str(two)],
                "exclude": [str((two / "a.png").resolve())],
                "config": {"workers": 1, "out_size": 256},
            }
        )
        rid = run["id"]
        deadline = time.time() + 40
        status = None
        while time.time() < deadline:
            status = self.mgr.status(rid)["run"]
            if status["status"] not in ("queued", "running"):
                break
            time.sleep(0.2)
        self.assertEqual(status["status"], "done", status.get("error"))
        self.assertEqual(status["total"], 1)
        self.assertEqual(status["records"], 1)

        res = self.mgr.results(rid, {})
        self.assertEqual(res["total"], 1)
        row = res["rows"][0]
        removed = self.mgr.delete_image(rid, {"file": row["file"], "rel_path": row["rel_path"]})
        self.assertEqual(removed["deleted"], 1)
        after = self.mgr.results(rid, {})
        self.assertEqual(after["summary"]["records"], 0)
        self.assertEqual(after["summary"]["counts"]["ok"], 0)
        self.mgr.delete(rid, delete_files=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
