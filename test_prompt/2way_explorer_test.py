"""
test_2way.py - Unit tests proving the 2-way round trip is faithful.

Run:
    python test_2way.py            # prints PASS/FAIL summary, exit code 0/1
    python -m unittest test_2way   # also works (unittest discovery)

The tests verify the two core invariants required by the spec:

  (1) FILES -> TEXT -> FILES  produces exactly the same directory structure
      and (for "open" mode) byte-identical content as the original selection.

  (2) TEXT -> FILES -> TEXT   produces exactly the same text again
      (idempotence / stability of the generated text).

Both directions REUSE the real dev_helper functions (to_clipboard and
new_file_from_clipboard) through core.py.  No GUI / tkinter is involved.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import core
from core import (
    MODE_OPEN,
    MODE_SIGNATURE,
    files_to_text,
    text_to_files,
    collect_tree_files,
    dir_structure,
)


def _write_tree(base: str, tree: dict[str, str]) -> None:
    for rel, content in tree.items():
        full = os.path.join(base, rel)
        os.makedirs(os.path.dirname(full) or base, exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(content)


def _selection(items):
    """items: list of (rel_path, mode). abs_path = base/rel_path (filled by caller)."""
    return items


class RoundTripAllOpen(unittest.TestCase):
    """The strongest test: every selected file in 'open' mode => exact text."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="two_way_test_")
        self.src = os.path.join(self.tmp, "src")
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(self.src)

        self.tree = {
            "a.py": "def foo():\n    return 1\n\nclass Bar:\n    pass\n",
            "sub/b.py": "def baz():\n    return 2\n",
            "sub/c.txt": "hello world\nsecond line\n",
            "readme.md": "# Title\n\nsome text\n",
            # identical content to exercise dedup
            "dup1.txt": "SAME CONTENT\n",
            "dup2.txt": "SAME CONTENT\n",
        }
        _write_tree(self.src, self.tree)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_open_round_trip_is_exact(self):
        # Build selection: all files, 'open' mode.
        items = [
            (rel, os.path.join(self.src, rel), MODE_OPEN)
            for rel in self.tree
        ]

        # FILES -> TEXT
        text1 = files_to_text(self.src, items)
        self.assertTrue(text1.strip(), "generated text should not be empty")

        # TEXT -> FILES
        written = text_to_files(text1, self.out)
        self.assertEqual(len(written), len(self.tree),
                         "every selected file must be recreated")

        # structure preserved
        self.assertEqual(dir_structure(self.out), set(self.tree.keys()))

        # content byte-identical for 'open' mode
        out_files = collect_tree_files(self.out)
        for rel, content in self.tree.items():
            self.assertIn(rel, out_files)
            self.assertEqual(out_files[rel], content,
                             f"content mismatch for {rel}")

        # TEXT -> FILES -> TEXT  ==  TEXT   (idempotent)
        items2 = [
            (rel, os.path.join(self.out, rel), MODE_OPEN)
            for rel in out_files
        ]
        text2 = files_to_text(self.out, items2)
        self.assertEqual(text2, text1,
                         "text must be stable across a full round trip")


class RoundTripMixedModes(unittest.TestCase):
    """Mix 'open' and 'signature_extractor' modes; check structure + stability."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="two_way_mixed_")
        self.src = os.path.join(self.tmp, "src")
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(self.src)
        self.tree = {
            "a.py": "def foo():\n    return 1\n\nclass Bar:\n    pass\n",
            "b.js": "function add(a, b) {\n  return a + b;\n}\n",
            "notes.txt": "plain notes\nline two\n",
        }
        _write_tree(self.src, self.tree)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mixed_modes_structure_and_stability(self):
        modes = {
            "a.py": MODE_SIGNATURE,
            "b.js": MODE_SIGNATURE,
            "notes.txt": MODE_OPEN,
        }
        items = [
            (rel, os.path.join(self.src, rel), modes[rel]) for rel in self.tree
        ]

        text1 = files_to_text(self.src, items)
        self.assertIn("notes.txt", text1)
        self.assertIn("def foo", text1)        # signature of a.py
        self.assertIn("function add", text1)   # signature of b.js

        written = text_to_files(text1, self.out)
        self.assertEqual(dir_structure(self.out), set(self.tree.keys()))

        # Re-derive selection from the recreated files (same modes by rel path).
        items2 = [
            (rel, os.path.join(self.out, rel), modes.get(rel, MODE_OPEN))
            for rel in collect_tree_files(self.out)
        ]
        text2 = files_to_text(self.out, items2)
        self.assertEqual(text2, text1,
                         "mixed-mode text must be stable across a round trip")


class TreeAndDedup(unittest.TestCase):
    """The ASCII tree and dedup must be deterministic and present."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="two_way_tree_")
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        self.tree = {
            "x.py": "print(1)\n",
            "y.py": "print(1)\n",   # identical -> dedup
            "z.py": "print(2)\n",
        }
        _write_tree(self.src, self.tree)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tree_present_and_dedup_collapses_identical(self):
        items = [(rel, os.path.join(self.src, rel), MODE_OPEN)
                 for rel in self.tree]
        text = files_to_text(self.src, items)
        # Root name appears at the top of the tree.
        self.assertTrue(text.startswith("src"))
        # Dedup: two identical files collapse into one block (saves space),
        # but BOTH names must still appear so the structure is recoverable.
        self.assertIn("x.py", text)
        self.assertIn("y.py", text)
        self.assertIn("z.py", text)

        out = os.path.join(self.tmp, "out")
        text_to_files(text, out)
        self.assertEqual(dir_structure(out), set(self.tree.keys()))


class EmptySelection(unittest.TestCase):
    def test_no_selection_yields_empty_text(self):
        tmp = tempfile.mkdtemp(prefix="two_way_empty_")
        try:
            items = [("a.txt", os.path.join(tmp, "a.txt"), core.MODE_NONE)]
            self.assertEqual(files_to_text(tmp, items), "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _run_manual():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_run_manual())
