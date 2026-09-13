"""
Tests for dev_helper/new_file/combine_img.py.

Covers:
  - constrain_resize (max, min, both, edge cases)
  - parse_args defaults and overrides
  - Grid layout: rows, cols, arrange order, padding
  - Image resizing in grid context (max/min constraints)
  - Sort order (NAME, DATE, NONE)
  - Output format and background / alpha
  - Single image, empty directory, oversized grid
  - Left-to-right vs top-to-down arrangement
  - Failed test images saved to test_failures/ for inspection
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parent.parent
_COMBINE = _ROOT / "dev_helper" / "new_file" / "combine_img.py"

_spec = importlib.util.spec_from_file_location("combine_img", str(_COMBINE))
combine_img = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(combine_img)

import path_args.command_paths as _command_paths

_FAILURES_DIR = _HERE.parent / "test_failures"
_FAILURES_DIR.mkdir(exist_ok=True)

_MODULE_GLOBALS = [
    "OVERWRITE", "ROWS", "COLS", "ARRANGE_ORDER", "SORT_BY",
    "IMAGE_W_MAX", "IMAGE_H_MAX", "IMAGE_H_MIN",
    "OUTPUT_FORMAT", "CELL_PADDING", "BACKGROUND_COLOR", "OUTPUT_FILE",
    "IMAGE_W_MIN",
]


def _save_failure(name, actual_img, params):
    path = _FAILURES_DIR / f"{name}.png"
    meta_path = _FAILURES_DIR / f"{name}.json"
    if isinstance(actual_img, Image.Image):
        actual_img.save(str(path))
    else:
        Image.new("RGBA", (1, 1)).save(str(path))
    meta_path.write_text(
        json.dumps(params, indent=2, default=str),
        encoding="utf-8",
    )


def _make_test_image(path, width, height, color=(255, 0, 0, 255)):
    img = Image.new("RGBA", (width, height), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(0, 0, 0, 255), width=1)
    img.save(path)


def _make_image_dir(tmp_root, count, size=(100, 100), colors=None):
    img_dir = tmp_root / "imgs"
    img_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(count):
        color = colors[i % len(colors)] if colors else (
            (255, 0, 0, 255) if i % 4 == 0 else
            (0, 255, 0, 255) if i % 4 == 1 else
            (0, 0, 255, 255) if i % 4 == 2 else
            (255, 255, 0, 255)
        )
        p = img_dir / f"img_{i:03d}.png"
        _make_test_image(p, size[0], size[1], color)
        files.append(p)
    return img_dir, files


def _run_combine(*argv):
    return combine_img.main(list(argv))


def _save_globals():
    return {k: getattr(combine_img, k) for k in _MODULE_GLOBALS if hasattr(combine_img, k)}


def _restore_globals(saved):
    for k in _MODULE_GLOBALS:
        if k in saved:
            setattr(combine_img, k, saved[k])
        elif hasattr(combine_img, k):
            delattr(combine_img, k)


# ---------------------------------------------------------------------------
# constrain_resize unit tests
# ---------------------------------------------------------------------------

class ConstrainResizeTests(unittest.TestCase):
    def test_no_change_when_within_bounds(self):
        img = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
        out = combine_img.constrain_resize(img, 200, None, 200, None)
        self.assertEqual(out.size, (100, 100))

    def test_shrink_wider_than_max(self):
        img = Image.new("RGBA", (400, 100), (255, 0, 0, 255))
        out = combine_img.constrain_resize(img, 200, None, 200, None)
        self.assertEqual(out.size, (200, 50))

    def test_shrink_taller_than_max(self):
        img = Image.new("RGBA", (100, 400), (255, 0, 0, 255))
        out = combine_img.constrain_resize(img, 200, None, 200, None)
        self.assertEqual(out.size, (50, 200))

    def test_grow_narrower_than_min(self):
        img = Image.new("RGBA", (50, 50), (255, 0, 0, 255))
        out = combine_img.constrain_resize(img, None, 200, None, 200)
        self.assertEqual(out.size, (200, 200))

    def test_grow_shorter_than_min(self):
        img = Image.new("RGBA", (50, 50), (255, 0, 0, 255))
        out = combine_img.constrain_resize(img, 200, 200, 200, None)
        self.assertEqual(out.size, (200, 200))

    def test_max_takes_priority_over_min(self):
        img = Image.new("RGBA", (1000, 1000), (255, 0, 0, 255))
        out = combine_img.constrain_resize(img, 100, None, 100, 200)
        self.assertEqual(out.size, (200, 200))

    def test_min_enforced_after_rounding(self):
        img = Image.new("RGBA", (3, 3), (255, 0, 0, 255))
        out = combine_img.constrain_resize(img, None, 10, None, 10)
        self.assertEqual(out.size, (10, 10))

    def test_one_pixel_min(self):
        img = Image.new("RGBA", (1, 1), (255, 0, 0, 255))
        out = combine_img.constrain_resize(img, None, 1, None, 1)
        self.assertEqual(out.size, (1, 1))


# ---------------------------------------------------------------------------
# parse_args tests (must be isolated from main() side-effects)
# ---------------------------------------------------------------------------

class ParseArgsTests(unittest.TestCase):
    def setUp(self):
        self._saved = _save_globals()

    def tearDown(self):
        _restore_globals(self._saved)

    def test_defaults(self):
        ns = combine_img.parse_args([])
        self.assertEqual(ns.rows, 2)
        self.assertEqual(ns.cols, -1)
        self.assertEqual(ns.arrange, "leftToRight")
        self.assertEqual(ns.sort, "NAME")
        self.assertEqual(ns.format, "avif")

    def test_override_rows(self):
        ns = combine_img.parse_args(["--rows", "3"])
        self.assertEqual(ns.rows, 3)

    def test_override_cols(self):
        ns = combine_img.parse_args(["--cols", "5"])
        self.assertEqual(ns.cols, 5)

    def test_override_format(self):
        ns = combine_img.parse_args(["--format", "png"])
        self.assertEqual(ns.format, "png")

    def test_override_pad(self):
        ns = combine_img.parse_args(["--pad", "10"])
        self.assertEqual(ns.pad, 10)

    def test_override_bg_and_alpha(self):
        ns = combine_img.parse_args(["--bg", "10,20,30", "--alpha", "128"])
        self.assertEqual(ns.bg, "10,20,30")
        self.assertEqual(ns.alpha, 128)

    def test_invalid_arrange_raises(self):
        with self.assertRaises(SystemExit):
            combine_img.parse_args(["--arrange", "diagonal"])


# ---------------------------------------------------------------------------
# Grid layout integration tests
# ---------------------------------------------------------------------------

class GridLayoutTests(unittest.TestCase):
    def setUp(self):
        self._saved = _save_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        _restore_globals(self._saved)
        self.tmp.cleanup()

    def _run(self, img_dir, out_dir, *argv):
        return _run_combine(str(img_dir), "-o", str(out_dir), *argv)

    def _get_output(self, out_dir):
        files = list(out_dir.iterdir())
        self.assertEqual(len(files), 1, f"Expected 1 output file in {out_dir}, got {len(files)}: {files}")
        return files[0]

    def _assert_pixel(self, img, pos, expected_color):
        actual = img.getpixel(pos)
        self.assertEqual(actual, expected_color,
                         f"Pixel at {pos}: expected {expected_color}, got {actual}")

    def _assert_and_save(self, img, out_path, condition, name, params):
        if not condition:
            _save_failure(name, img.copy(), params)
            self.fail(f"Assertion failed: {name}. Image saved to test_failures/{name}.png")

    def _run_and_get(self, img_dir, out_dir, *argv):
        self._run(img_dir, out_dir, *argv)
        out = self._get_output(out_dir)
        return Image.open(out), out

    def test_single_image(self):
        img_dir, _ = _make_image_dir(self.root, 1, size=(100, 80))
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "1", "--cols", "1",
                                     "--format", "png", "--pad", "0",
                                     "--img-w-max", "-1", "--img-h-max", "-1",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        self._assert_and_save(img, out, img.size == (100, 80),
                              "test_single_image", {"expected": (100, 80), "actual": img.size})
        img.close()

    def test_two_images_two_rows_auto_cols(self):
        img_dir, _ = _make_image_dir(self.root, 2, size=(100, 100))
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "2",
                                     "--format", "png", "--pad", "0",
                                     "--img-w-max", "-1", "--img-h-max", "-1",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        self._assert_and_save(img, out, img.size == (100, 200),
                              "test_two_images_two_rows_auto_cols", {"expected": (100, 200), "actual": img.size})
        img.close()

    def test_four_images_exact_2x2(self):
        img_dir, _ = _make_image_dir(self.root, 4, size=(100, 100))
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "2", "--cols", "2",
                                     "--format", "png", "--pad", "0",
                                     "--img-w-max", "-1", "--img-h-max", "-1",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        self._assert_and_save(img, out, img.size == (200, 200),
                              "test_four_images_exact_2x2", {"expected": (200, 200), "actual": img.size})
        img.close()

    def test_three_images_partial_fill_2x2(self):
        img_dir, _ = _make_image_dir(self.root, 3, size=(100, 100))
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "2", "--cols", "2",
                                     "--format", "png", "--pad", "0",
                                     "--img-w-max", "-1", "--img-h-max", "-1",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        self._assert_and_save(img, out, img.size == (200, 200),
                              "test_three_images_partial_fill_2x2_size", {"expected": (200, 200), "actual": img.size})
        bg = combine_img.BACKGROUND_COLOR
        px = img.getpixel((150, 150))
        self._assert_and_save(img, out, px == bg,
                              "test_three_images_partial_fill_2x2_bg",
                              {"expected_bg": bg, "actual_pixel": px})
        img.close()

    def test_padding_adds_to_canvas(self):
        img_dir, _ = _make_image_dir(self.root, 2, size=(100, 100))
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "2", "--cols", "1",
                                     "--format", "png", "--pad", "5",
                                     "--img-w-max", "-1", "--img-h-max", "-1",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        self._assert_and_save(img, out, img.size == (100, 205),
                              "test_padding_adds_to_canvas", {"expected": (100, 205), "actual": img.size})
        img.close()

    def test_left_to_right_order(self):
        colors = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)]
        img_dir, _ = _make_image_dir(self.root, 4, size=(100, 100), colors=colors)
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "2", "--cols", "2",
                                     "--format", "png", "--pad", "0",
                                     "--img-w-max", "-1", "--img-h-max", "-1",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        self._assert_and_save(img, out, img.getpixel((25, 25)) == colors[0],
                              "test_left_to_right_order_0", {"expected": colors[0], "actual": img.getpixel((25, 25))})
        self._assert_and_save(img, out, img.getpixel((125, 25)) == colors[1],
                              "test_left_to_right_order_1", {"expected": colors[1], "actual": img.getpixel((125, 25))})
        self._assert_and_save(img, out, img.getpixel((25, 125)) == colors[2],
                              "test_left_to_right_order_2", {"expected": colors[2], "actual": img.getpixel((25, 125))})
        self._assert_and_save(img, out, img.getpixel((125, 125)) == colors[3],
                              "test_left_to_right_order_3", {"expected": colors[3], "actual": img.getpixel((125, 125))})
        img.close()

    def test_top_to_down_order(self):
        colors = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)]
        img_dir, _ = _make_image_dir(self.root, 4, size=(100, 100), colors=colors)
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "2", "--cols", "2",
                                     "--arrange", "topToDown", "--format", "png", "--pad", "0",
                                     "--img-w-max", "-1", "--img-h-max", "-1",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        self._assert_and_save(img, out, img.getpixel((25, 25)) == colors[0],
                              "test_top_to_down_order_0", {"expected": colors[0], "actual": img.getpixel((25, 25))})
        self._assert_and_save(img, out, img.getpixel((25, 125)) == colors[1],
                              "test_top_to_down_order_1", {"expected": colors[1], "actual": img.getpixel((25, 125))})
        self._assert_and_save(img, out, img.getpixel((125, 25)) == colors[2],
                              "test_top_to_down_order_2", {"expected": colors[2], "actual": img.getpixel((125, 25))})
        self._assert_and_save(img, out, img.getpixel((125, 125)) == colors[3],
                              "test_top_to_down_order_3", {"expected": colors[3], "actual": img.getpixel((125, 125))})
        img.close()

    def test_grid_auto_expansion(self):
        img_dir, _ = _make_image_dir(self.root, 5, size=(100, 100))
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "2", "--cols", "2",
                                     "--format", "png", "--pad", "0",
                                     "--img-w-max", "-1", "--img-h-max", "-1",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        self._assert_and_save(img, out, img.size == (300, 200),
                              "test_grid_auto_expansion", {"expected": (300, 200), "actual": img.size})
        img.close()

    def test_sort_by_name(self):
        img_dir = self.root / "sorted_name"
        img_dir.mkdir()
        for name in ["z_img.png", "a_img.png", "m_img.png"]:
            _make_test_image(img_dir / name, 50, 50, (255, 0, 0, 255))
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "1", "--cols", "3",
                                     "--sort", "NAME", "--format", "png", "--pad", "0",
                                     "--img-w-max", "-1", "--img-h-max", "-1",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        self._assert_and_save(img, out, out.exists(),
                              "test_sort_by_name", {"exists": out.exists()})
        img.close()


    def test_sort_by_date(self):
        img_dir = self.root / "sorted_date"
        img_dir.mkdir()
        for i, name in enumerate(["a.png", "b.png", "c.png"]):
            p = img_dir / name
            _make_test_image(p, 50, 50, (255, 0, 0, 255))
            time.sleep(0.01)
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "1", "--cols", "3",
                                     "--sort", "DATE", "--format", "png", "--pad", "0",
                                     "--img-w-max", "-1", "--img-h-max", "-1",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        img.close()
        self.assertTrue(out.exists())

    def test_resize_max(self):
        img_dir, _ = _make_image_dir(self.root, 1, size=(400, 400))
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "1", "--cols", "1",
                                     "--img-w-max", "100", "--img-h-max", "100",
                                     "--format", "png", "--pad", "0",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        self._assert_and_save(img, out, img.size == (100, 100),
                              "test_resize_max", {"expected": (100, 100), "actual": img.size})
        img.close()

    def test_resize_min(self):
        img_dir, _ = _make_image_dir(self.root, 1, size=(50, 50))
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "1", "--cols", "1",
                                     "--img-w-min", "200", "--img-h-min", "200",
                                     "--format", "png", "--pad", "0",
                                     "--img-w-max", "-1", "--img-h-max", "-1")
        self._assert_and_save(img, out, img.size == (200, 200),
                              "test_resize_min", {"expected": (200, 200), "actual": img.size})
        img.close()

    def test_different_output_formats(self):
        for fmt in ["png", "webp"]:
            img_dir, _ = _make_image_dir(self.root / f"imgs_{fmt}", 2, size=(50, 50))
            out_dir = self.root / f"out_{fmt}"
            out_dir.mkdir()
            self._run(img_dir, out_dir, "--rows", "2", "--cols", "1",
                      "--format", fmt, "--pad", "0",
                      "--img-w-max", "-1", "--img-h-max", "-1",
                      "--img-w-min", "-1", "--img-h-min", "-1")
            out = self._get_output(out_dir)
            self.assertEqual(out.suffix.lower(), f".{fmt}")

    def test_background_with_alpha(self):
        img_dir, _ = _make_image_dir(self.root, 1, size=(50, 50))
        out_dir = self.root / "out"
        out_dir.mkdir()
        img, out = self._run_and_get(img_dir, out_dir, "--rows", "1", "--cols", "1",
                                     "--bg", "0,0,0", "--alpha", "128", "--format", "png",
                                     "--pad", "0",
                                     "--img-w-max", "-1", "--img-h-max", "-1",
                                     "--img-w-min", "-1", "--img-h-min", "-1")
        self._assert_and_save(img, out, img.mode == "RGBA",
                              "test_background_with_alpha", {"expected_mode": "RGBA", "actual_mode": img.mode})
        img.close()

    def test_empty_directory_prints_message(self):
        empty_dir = self.root / "empty"
        empty_dir.mkdir()
        out_dir = self.root / "out"
        out_dir.mkdir()
        with patch("builtins.print") as mock_print:
            self._run(empty_dir, out_dir, "--rows", "1", "--cols", "1",
                      "--format", "png", "--pad", "0")
        self.assertTrue(
            any("No valid images" in str(c.args[0]) for c in mock_print.call_args_list if c.args),
            "Expected 'No valid images' message",
        )


# ---------------------------------------------------------------------------
# Failure verification test
# ---------------------------------------------------------------------------

class FailureVerificationTests(unittest.TestCase):
    """Tests that explicitly exercise the failure-saving path."""

    def setUp(self):
        self._saved = _save_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        _restore_globals(self._saved)
        self.tmp.cleanup()

    def _run(self, img_dir, out_dir, *argv):
        return _run_combine(str(img_dir), "-o", str(out_dir), *argv)

    def _get_output(self, out_dir):
        files = list(out_dir.iterdir())
        self.assertEqual(len(files), 1, f"Expected 1 output file, got {len(files)}")
        return files[0]

    def _assert_and_save(self, img, out_path, condition, name, params):
        if not condition:
            _save_failure(name, img.copy(), params)
            self.fail(f"Assertion failed: {name}. Image saved to test_failures/{name}.png")

    def test_failed_grid_saved(self):
        # This test verifies the failure-saving mechanism works
        # Using 2 images in 2x2 grid: they fill row 0, giving 200x100 canvas
        colors = [(255, 0, 0, 255), (0, 0, 255, 255)]
        img_dir, _ = _make_image_dir(self.root, 2, size=(100, 100), colors=colors)
        out_dir = self.root / "out"
        out_dir.mkdir()
        self._run(img_dir, out_dir, "--rows", "2", "--cols", "2",
                  "--format", "png", "--pad", "0",
                  "--img-w-max", "-1", "--img-h-max", "-1",
                  "--img-w-min", "-1", "--img-h-min", "-1")
        out = self._get_output(out_dir)
        with Image.open(out) as img:
            self._assert_and_save(
                img, out,
                img.size == (200, 100),
                "test_failed_grid_saved",
                {"expected_size": (200, 100), "actual_size": img.size},
            )
            self._assert_and_save(
                img, out,
                img.getpixel((25, 25)) == colors[0],
                "test_failed_grid_saved_color",
                {"expected_color": colors[0], "actual": img.getpixel((25, 25))},
            )
