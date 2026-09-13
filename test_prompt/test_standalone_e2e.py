"""
End-to-end tests for standalone bundles.

Copies each variant to a shallow temp dir and runs it via subprocess,
simulating a user dragging the script to any folder on any PC.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
STANDALONE = ROOT / "_standalone_dst_do_not_edit"


def _shallow_run(script: str, args: list[str]):
    """Copy script to a fresh shallow temp dir and run it."""
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        dst = work / script
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(STANDALONE / script, dst)
        result = subprocess.run(
            [sys.executable, str(dst)] + args,
            cwd=str(work),
            capture_output=True,
            text=True,
        )
        return result, work


@unittest.skipUnless(
    (STANDALONE / "to_clipboard" / "to_clipboard_ascii_path_tree.py").exists(),
    "to_clipboard_ascii_path_tree.py not built",
)
class TestToClipboardAsciiPathTreeE2E(unittest.TestCase):
    def test_runs_without_path_args(self):
        result, _ = _shallow_run("to_clipboard/to_clipboard_ascii_path_tree.py", [])
        self.assertEqual(result.returncode, 0, result.stderr[:500])


@unittest.skipUnless(
    (STANDALONE / "new_file" / "new_img_9green.py").exists(),
    "new_img_9green.py not built",
)
class TestNewImg5GreenE2E(unittest.TestCase):
    def test_no_path_args_creates_5_images(self):
        work = Path(tempfile.mkdtemp())
        try:
            shutil.copy2(STANDALONE / "new_file" / "new_img_9green.py", work / "new_img_9green.py")
            out_dir = work / "out"
            result = subprocess.run(
                [sys.executable, str(work / "new_img_9green.py"), str(out_dir)],
                cwd=str(work),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr[:500])
            webp = sorted(out_dir.glob("*.webp"))
            self.assertEqual(len(webp), 5, [f.name for f in webp])
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_all_images_have_green_background(self):
        work = Path(tempfile.mkdtemp())
        try:
            shutil.copy2(STANDALONE / "new_file" / "new_img_9green.py", work / "new_img_9green.py")
            out_dir = work / "out"
            result = subprocess.run(
                [sys.executable, str(work / "new_img_9green.py"), str(out_dir)],
                cwd=str(work),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr[:500])
            webp = sorted(out_dir.glob("*.webp"))
            self.assertEqual(len(webp), 5)
            for f in webp:
                img = Image.open(f)
                self.assertEqual(img.mode, "RGB")
                r, g, b = img.getpixel((1, 1))
                self.assertGreater(g, b)
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
