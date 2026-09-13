"""
2way_explorer_roundtrip_test.py

Round-trip tests moved out of ``dev_helper/new_file/2way_explorer.py`` so the
GUI module stays free of test code. The tests verify the 2-way round trip is
byte-for-byte invertible for both content-mark formats.

Run:
    python -m unittest 2way_explorer_roundtrip_test
    python 2way_explorer_roundtrip_test.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

# `2way_explorer.py` starts with a digit, so it cannot be imported by name.
# it is ok for standalone GUI entry point
# Load it by file path instead.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "two_way_explorer_mod",
    _REPO_ROOT / "dev_helper" / "new_file" / "2way_explorer.py",
)
we = importlib.util.module_from_spec(_spec)
sys.modules["two_way_explorer_mod"] = we
_spec.loader.exec_module(we)


class RoundTripTest(unittest.TestCase):
    def _make_sample(self, base: str) -> list[str]:
        files = {
            "a.py": "def main():\n    return 1\n",
            "b.txt": "hello world\n",
            "sub/c.py": "class C:\n    pass\n",
            "sub/deep/d.py": "x = 1\n",
            "sub/e.txt": "hello world\n",   # identical to b.txt -> exercises dedup merge
            "f.md": "just a note",           # no trailing newline
        }
        created = []
        for rel, content in files.items():
            p = os.path.join(base, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            created.append(p)
        return created

    def test_text_to_files_to_text(self):
        # Both the invertible ("default") and the pretty ("markdown") content
        # mark formats must round-trip byte-for-byte. The parser now recovers the
        # full relative path from the markdown layout, so structure is preserved.
        saved = {
            "CONTENT_MARK_FORMAT": we.CONTENT_MARK_FORMAT,
            "ASCII_TREE_SHOW_SIZE_THRESHOLD": we.ASCII_TREE_SHOW_SIZE_THRESHOLD,
            "SHOW_ROOT_IN_TREE": we.SHOW_ROOT_IN_TREE,
            "ASCII_TREE_SHOW_IGNORED": we.ASCII_TREE_SHOW_IGNORED,
        }
        try:
            we.ASCII_TREE_SHOW_SIZE_THRESHOLD = None
            we.SHOW_ROOT_IN_TREE = False
            we.ASCII_TREE_SHOW_IGNORED = False

            with tempfile.TemporaryDirectory() as td:
                src = os.path.join(td, "src")
                os.makedirs(src)
                created = self._make_sample(src)
                expected = {
                    "a.py", "b.txt", "sub/c.py", "sub/deep/d.py", "sub/e.txt", "f.md",
                }

                for fmt in ("default", "markdown"):
                    with self.subTest(format=fmt):
                        we.CONTENT_MARK_FORMAT = fmt
                        we.apply_settings()

                        text = we.files_to_text(src, sorted(created))
                        self.assertTrue(text.strip(), "produced text should not be empty")

                        # to_files recreates the structure
                        out1 = os.path.join(td, "out1_" + fmt)
                        written = we.text_to_files(text, out1)
                        self.assertEqual(set(written), expected)

                        # every recreated file matches the original (after normalization)
                        for rel in expected:
                            with open(os.path.join(src, *rel.split("/")), "r", encoding="utf-8") as f:
                                orig = f.read()
                            with open(os.path.join(out1, *rel.split("/")), "r", encoding="utf-8") as f:
                                new = f.read()
                            ext = os.path.splitext(rel)[1].lstrip(".")
                            self.assertEqual(we.normalize_content(orig, ext), we.normalize_content(new, ext),
                                             msg=f"content mismatch for {rel} ({fmt})")

                        # to_text(to_files(text)) reproduces the exact same text
                        text2 = we.files_to_text(out1, [os.path.join(out1, *r.split("/")) for r in sorted(expected)])
                        self.assertEqual(text, text2, f"text round-trip must be byte-for-byte identical ({fmt})")

                        # to_files(to_text(text2)) recreates an identical structure
                        out2 = os.path.join(td, "out2_" + fmt)
                        written2 = we.text_to_files(text2, out2)
                        self.assertEqual(set(written2), expected)
                        for rel in expected:
                            with open(os.path.join(out1, *rel.split("/")), "r", encoding="utf-8") as f:
                                a = f.read()
                            with open(os.path.join(out2, *rel.split("/")), "r", encoding="utf-8") as f:
                                b = f.read()
                            self.assertEqual(a, b)

        finally:
            for k, v in saved.items():
                setattr(we, k, v)
            we.apply_settings()

    def test_empty_selection(self):
        self.assertEqual(we.files_to_text("/tmp/does-not-matter", []), "")
        self.assertEqual(we.text_to_files("", "/tmp/x"), [])


def self_test() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(RoundTripTest)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(self_test())
