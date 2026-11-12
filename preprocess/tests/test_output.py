import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wellprep.output import create_run_output

class OutputTests(unittest.TestCase):
    def test_quoted_unicode_path(self):
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/"中文 输出"
            parent,out=create_run_output('"'+str(dest)+'"',Path(d),"run")
            self.assertEqual(parent,dest.resolve())
            self.assertTrue(out.is_dir())
            self.assertEqual(list(out.iterdir()),[])

    def test_write_denied_cleans_empty_run(self):
        with tempfile.TemporaryDirectory() as d:
            with patch("wellprep.output.tempfile.TemporaryFile",side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(ValueError,"系统拒绝写入"):
                    create_run_output(d,Path(d),"run")
            self.assertFalse((Path(d)/"run").exists())

    def test_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"file";p.write_text("keep")
            with self.assertRaisesRegex(ValueError,"占用"):
                create_run_output(str(p),Path(d),"run")
            self.assertEqual(p.read_text(),"keep")
