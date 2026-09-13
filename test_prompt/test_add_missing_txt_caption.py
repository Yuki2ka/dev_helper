from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from .conftest import chdir

try:
    from dev_helper.new_file.add_missing_txt_caption import (
        _collect_image_files,
        _process_images,
        _set_file_date_1980,
    )
    MODULE_AVAILABLE = True
except ImportError:
    MODULE_AVAILABLE = False


class AddMissingTxtCaptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_file_date_1980_modification_time(self):
        if not MODULE_AVAILABLE:
            self.skipTest("Module not available")
        target = self.root / "test.txt"
        target.touch()
        _set_file_date_1980(target)
        mtime = datetime.fromtimestamp(target.stat().st_mtime)
        self.assertEqual(mtime.year, 1980)
        self.assertEqual(mtime.month, 1)
        self.assertEqual(mtime.day, 1)

    def test_set_file_date_1980_creation_time_windows(self):
        if not MODULE_AVAILABLE:
            self.skipTest("Module not available")
        target = self.root / "test.txt"
        target.touch()
        _set_file_date_1980(target)
        ctime = datetime.fromtimestamp(target.stat().st_ctime)
        self.assertEqual(ctime.year, 1980)
        self.assertEqual(ctime.month, 1)
        self.assertEqual(ctime.day, 1)

    def test_create_missing_caption_for_jpg(self):
        if not MODULE_AVAILABLE:
            self.skipTest("Module not available")
        (self.root / "photo.jpg").touch()
        count = _process_images([self.root], no_confirm=True)
        self.assertEqual(count, 1)
        caption = self.root / "photo.txt"
        self.assertTrue(caption.exists())
        self.assertEqual(caption.read_text(), "")

    def test_create_missing_caption_for_png(self):
        if not MODULE_AVAILABLE:
            self.skipTest("Module not available")
        (self.root / "image.png").touch()
        count = _process_images([self.root], no_confirm=True)
        self.assertEqual(count, 1)
        caption = self.root / "image.txt"
        self.assertTrue(caption.exists())

    def test_create_missing_caption_for_webp(self):
        if not MODULE_AVAILABLE:
            self.skipTest("Module not available")
        (self.root / "photo.webp").touch()
        count = _process_images([self.root], no_confirm=True)
        self.assertEqual(count, 1)
        caption = self.root / "photo.txt"
        self.assertTrue(caption.exists())

    def test_skip_existing_caption(self):
        if not MODULE_AVAILABLE:
            self.skipTest("Module not available")
        (self.root / "photo.jpg").touch()
        caption = self.root / "photo.txt"
        caption.write_text("existing", encoding="utf-8")
        original_mtime = caption.stat().st_mtime
        count = _process_images([self.root], no_confirm=True)
        self.assertEqual(count, 0)
        self.assertEqual(caption.read_text(encoding="utf-8"), "existing")
        self.assertEqual(caption.stat().st_mtime, original_mtime)

    def test_recursive_subdirectory(self):
        if not MODULE_AVAILABLE:
            self.skipTest("Module not available")
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "nested.jpg").touch()
        count = _process_images([self.root], no_confirm=True)
        self.assertEqual(count, 1)
        caption = sub / "nested.txt"
        self.assertTrue(caption.exists())

    def test_collect_image_files(self):
        if not MODULE_AVAILABLE:
            self.skipTest("Module not available")
        (self.root / "a.jpg").touch()
        (self.root / "b.png").touch()
        (self.root / "c.webp").touch()
        (self.root / "d.txt").touch()
        files = list(_collect_image_files([self.root]))
        names = sorted(p.name for p in files)
        self.assertEqual(names, ["a.jpg", "b.png", "c.webp"])
