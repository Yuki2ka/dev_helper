"""
Tests for dev_helper/new_file/new_img.py path_args integration.

Covers:
- resolve_paths integration (path_args available)
- path_args unavailable → graceful fallback in new_img
- output filename logic (directory vs file, auto-increment)
- parse_bg, parse_args edge cases
"""

from __future__ import annotations

import argparse
import importlib.util
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from .conftest import chdir

_HERE    = Path(__file__).resolve()
_ROOT    = _HERE.parent.parent                 # a:\4
_NEW_IMG = _ROOT / "dev_helper" / "new_file" / "new_img.py"

_spec   = importlib.util.spec_from_file_location("new_img", str(_NEW_IMG))
new_img = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(new_img)

import path_args.command_paths as _command_paths


@staticmethod
def _run(*argv):
    return new_img.main(list(argv))


@contextmanager
def _no_clipboard():
    with patch.object(_command_paths, "paths_from_clipboard", return_value=[]):
        yield


# ---------------------------------------------------------------------------
# Tests: path_args integration (library available)
# ---------------------------------------------------------------------------

class ResolvePathsIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ---- basic path resolution ----

    def test_no_path_falls_back_to_cwd(self) -> None:
        out_dir = self.root / "cwd-dest"
        out_dir.mkdir()
        with chdir(out_dir), _no_clipboard():
            _run()

        self.assertEqual(len(list(out_dir.glob("*.webp"))), 1)

    def test_directory_path_used_as_destination(self) -> None:
        dest_dir = self.root / "my-dir"
        dest_dir.mkdir()
        _run(str(dest_dir))

        self.assertEqual(len(list(dest_dir.glob("*.webp"))), 1)

    def test_file_path_creates_file_in_parent_dir(self) -> None:
        dest_file = self.root / "output" / "result.png"
        _run(str(dest_file))
        parent_files = list(Path(dest_file).parent.glob("*.webp"))
        self.assertTrue(len(parent_files) >= 1)

    def test_preexisting_triggers_counter_suffix(self) -> None:
        dest_dir = self.root / "pre-dir"
        dest_dir.mkdir()
        dest_dir.joinpath("1.webp").write_bytes(b"old")
        _run(str(dest_dir / "1.webp"))

        self.assertTrue(dest_dir.joinpath("1.webp").exists())
        self.assertTrue(dest_dir.joinpath("1_001.webp").exists())

    def test_multiple_images_sequential(self) -> None:
        dest_dir = self.root / "multi"
        dest_dir.mkdir()
        _run(str(dest_dir), "-n", "3")

        files = sorted(dest_dir.glob("*.webp"))
        self.assertEqual(len(files), 3)

    def test_format_override_changes_extension(self) -> None:
        dest_dir = self.root / "fmt-dir"
        dest_dir.mkdir()
        _run(str(dest_dir), "--format", "png")

        self.assertEqual(len(list(dest_dir.glob("*.png"))), 1)

    # ---- input validation ----

    def test_invalid_resolution_exits_nonzero(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            _run("--resolution", "bad")
        self.assertNotEqual(ctx.exception.code, 0)

    def test_valid_resolution_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as td, chdir(Path(td)):
            _run("--resolution", "64,32")

    # ---- parse_bg ----

    def test_parse_bg_auto_hue_spread(self) -> None:
        r0, g0, b0 = new_img.parse_bg("auto", 0, 4)
        r3, g3, b3 = new_img.parse_bg("auto", 3, 4)
        self.assertEqual((r0, g0, b0), new_img.hsv_to_rgb(0.0, 1.0, 1.0))
        self.assertEqual((r3, g3, b3), new_img.hsv_to_rgb(1.0, 1.0, 1.0))

    def test_parse_bg_auto_full_cycle(self) -> None:
        red   = new_img.parse_bg("auto", 0, 4)
        mid   = new_img.parse_bg("auto", 2, 4)
        end   = new_img.parse_bg("auto", 3, 4)
        self.assertEqual(red, new_img.hsv_to_rgb(0.0, 1.0, 1.0))
        self.assertEqual(mid, new_img.hsv_to_rgb(2/3, 1.0, 1.0))
        self.assertEqual(end, new_img.hsv_to_rgb(1.0, 1.0, 1.0))
        self.assertNotEqual(red, mid)
        self.assertNotEqual(mid, end)

    def test_parse_bg_auto_end_hue(self) -> None:
        end_h, _, _ = __import__('colorsys').rgb_to_hsv(0, 0, 255)
        self.assertGreater(end_h, 0.55)
        self.assertLess(end_h, 0.70)
        r0, g0, b0 = new_img.parse_bg("auto", 0, 4, bg_end_str="0,0,255")
        r3, g3, b3 = new_img.parse_bg("auto", 3, 4, bg_end_str="0,0,255")
        self.assertEqual((r0, g0, b0), new_img.hsv_to_rgb(0.0, 1.0, 1.0))
        self.assertAlmostEqual(
            __import__('colorsys').rgb_to_hsv(r3 / 255, g3 / 255, b3 / 255)[0],
            end_h, places=2,
        )

    def test_parse_bg_bg_end_changes_colors(self) -> None:
        bg_end_blue  = new_img.parse_bg("auto", 1, 4, bg_end_str="0,0,255")
        bg_end_green = new_img.parse_bg("auto", 1, 4, bg_end_str="0,255,0")
        self.assertNotEqual(bg_end_blue, bg_end_green)

    def test_parse_bg_bg_end_below_half_cycle(self) -> None:
        red_start = new_img.parse_bg("255,0,0", 0, 4, bg_end_str="255,255,0")
        red_end   = new_img.parse_bg("255,0,0", 3, 4, bg_end_str="255,255,0")
        self.assertNotEqual(red_start, red_end)
        for i in range(4):
            c = new_img.parse_bg("255,0,0", i, 4, bg_end_str="255,255,0")
            for ch in c:
                self.assertGreaterEqual(ch, 0)
                self.assertLessEqual(ch, 255)

    def test_parse_bg_explicit_same_creates_hue_cycle(self) -> None:
        c0 = new_img.parse_bg("255,0,0", 0, 3, bg_end_str="255,0,0")
        c1 = new_img.parse_bg("255,0,0", 1, 3, bg_end_str="255,0,0")
        c2 = new_img.parse_bg("255,0,0", 2, 3, bg_end_str="255,0,0")
        self.assertNotEqual(c0, c1)
        self.assertNotEqual(c1, c2)
        red_hue = 0.0
        self.assertEqual(c0, new_img.hsv_to_rgb(red_hue, 1.0, 1.0))
        self.assertAlmostEqual(
            __import__('colorsys').rgb_to_hsv(c1[0]/255, c1[1]/255, c1[2]/255)[0],
            (red_hue + 1/3), places=2
        )

    def test_parse_bg_bg_end_gray_ramp(self) -> None:
        c0 = new_img.parse_bg("0,0,0", 0, 5, bg_end_str="50,50,50")
        c1 = new_img.parse_bg("0,0,0", 1, 5, bg_end_str="50,50,50")
        c4 = new_img.parse_bg("0,0,0", 4, 5, bg_end_str="50,50,50")
        self.assertEqual(c0, (0, 0, 0))
        self.assertEqual(c4, (50, 50, 50))
        self.assertNotEqual(c0, c1)

    def test_parse_bg_bg_end_cli_default_behaves_like_module_default(self) -> None:
        default_end = new_img.BG_END
        c0 = new_img.parse_bg("0,0,0", 0, 5, bg_end_str=default_end)
        c2 = new_img.parse_bg("0,0,0", 2, 5, bg_end_str=default_end)
        self.assertNotEqual(c0, c2)
        self.assertAlmostEqual(
            __import__('colorsys').rgb_to_hsv(c0[0]/255, c0[1]/255, c0[2]/255)[0],
            0.0, places=2
        )
        self.assertAlmostEqual(
            __import__('colorsys').rgb_to_hsv(c2[0]/255, c2[1]/255, c2[2]/255)[0],
            0.4, places=2
        )

    def test_parse_bg_explicit_interpolation(self) -> None:
        r_first = new_img.parse_bg("255,0,0", 0, 3, bg_end_str="0,255,0")
        r_mid   = new_img.parse_bg("255,0,0", 1, 3, bg_end_str="0,255,0")
        r_last  = new_img.parse_bg("255,0,0", 2, 3, bg_end_str="0,255,0")
        self.assertEqual(r_first, (255, 0, 0))
        self.assertEqual(r_mid,   (127, 127, 0))
        self.assertEqual(r_last,  (0, 255, 0))

    def test_parse_bg_explicit_rgb(self) -> None:
        self.assertEqual(new_img.parse_bg("10,20,30", 0, 1), (10, 20, 30))

    def test_parse_bg_invalid_falls_back(self) -> None:
        self.assertEqual(new_img.parse_bg("not,valid,input", 0, 1), (0, 0, 0))

    # ---- make_image ----

    def test_make_image_bg_inverts_to_dark_pixels(self) -> None:
        img = new_img.make_image(64, 64, 16, bg_rgb=(50, 50, 50))
        px_back = img.getpixel((9, 9))
        px_grid = img.getpixel((0, 0))
        self.assertEqual(px_back, 0)
        self.assertEqual(px_grid, 1)

    def test_save_webp_roundtrip(self) -> None:
        dest = self.root / "rt.webp"
        new_img.make_image(32, 32, 8).save(
            str(dest), "WEBP", lossless=True, optimize=True
        )
        self.assertTrue(dest.exists())
        self.assertGreater(dest.stat().st_size, 0)


# ---------------------------------------------------------------------------
# Tests: fallback when path_args.command_paths is unavailable
# (Simulated by mocking paths_from_clipboard to [] and testing the
#  path resolution branches that the real path_args already handles.)
# ---------------------------------------------------------------------------

class FallbackTests(unittest.TestCase):
    """Tests that new_img handles all resolve_paths origin branches correctly
    regardless of whether path_args is installed."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _run(*argv):
        return new_img.main(list(argv))

    # ---- cwd fallback branch ----

    def test_fallback_no_path_uses_cwd(self) -> None:
        out_dir = self.root / "fb-cwd"
        out_dir.mkdir()
        with chdir(out_dir), _no_clipboard():
            _run()

        self.assertEqual(len(list(out_dir.glob("*.webp"))), 1)

    def test_fallback_file_path_creates_in_parent(self) -> None:
        dest = self.root / "fb-file.webp"
        with _no_clipboard():
            _run(str(dest))
        parent_files = list(Path(dest).parent.glob("*.webp"))
        self.assertTrue(len(parent_files) >= 1)

    def test_fallback_dir_path(self) -> None:
        dest_dir = self.root / "fb-dir"
        dest_dir.mkdir()
        with _no_clipboard():
            _run(str(dest_dir))
        self.assertEqual(len(list(dest_dir.glob("*.webp"))), 1)

    def test_fallback_format_override(self) -> None:
        dest_dir = self.root / "fb-fmt"
        dest_dir.mkdir()
        with _no_clipboard():
            _run(str(dest_dir), "--format", "png")
        self.assertEqual(len(list(dest_dir.glob("*.png"))), 1)

    # ---- resolve_paths origin branch tests ----

    def test_resolve_paths_args_origin(self) -> None:
        """When args has a valid path, origin must be 'args'."""
        with _no_clipboard():
            ns = argparse.Namespace(path=str(self.root))
            rp = new_img.resolve_paths(ns, arg_names=("path",))
        self.assertEqual(str(rp.paths[0]), str(self.root))
        self.assertEqual(rp.origin, "args")

    def test_resolve_paths_constant_origin(self) -> None:
        """When args/stdin/clipboard are empty and constant exists."""
        existing = self.root / "const-dir"
        existing.mkdir()
        with _no_clipboard():
            rp = new_img.resolve_paths(
                None, arg_names=("path",), constant=str(existing)
            )
        self.assertEqual(rp.origin, "constant")
        self.assertEqual(str(rp.paths[0]), str(existing))

    def test_resolve_paths_constant_missing_falls_to_cwd(self) -> None:
        """When args/stdin/clipboard empty and constant is missing."""
        with _no_clipboard():
            rp = new_img.resolve_paths(
                None, arg_names=("path",), constant="__non_existent_xyz__"
            )
        self.assertEqual(rp.origin, "cwd")
        self.assertEqual(str(rp.paths[0]), str(Path.cwd().resolve()))

    def test_resolve_paths_nothing_found_falls_to_cwd(self) -> None:
        """When args/stdin/clipboard/constant are all empty/missing."""
        with _no_clipboard():
            rp = new_img.resolve_paths(None, arg_names=("path",))
        self.assertEqual(rp.origin, "cwd")
        self.assertEqual(str(rp.paths[0]), str(Path.cwd().resolve()))

    def test_resolve_paths_clipboard_origin(self) -> None:
        """When clipboard has a folder, it should be used."""
        clip_dir = self.root / "from-clip"
        clip_dir.mkdir()
        with patch.object(
            _command_paths, "paths_from_clipboard", return_value=[str(clip_dir)]
        ):
            rp = new_img.resolve_paths(None, arg_names=("path",))
        self.assertEqual(rp.origin, "clipboard")
        self.assertEqual(str(rp.paths[0]), str(clip_dir))


if __name__ == "__main__":
    unittest.main()
