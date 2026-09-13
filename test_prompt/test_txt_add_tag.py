from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .conftest import chdir

try:
    from path_args import command_paths, resolve_paths, resolve_input_text
except ImportError:
    command_paths = None
    resolve_paths = None
    resolve_input_text = None

try:
    from dev_helper.apply.txt_add_tag import (
        _add_tag_to_content,
        _contains_tag,
        _process_files,
    )
    MODULE_AVAILABLE = True
except ImportError:
    MODULE_AVAILABLE = False


class TxtAddTagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_contains_tag_case_sensitive_true(self):
        content = "hello world qwe1 goodbye"
        self.assertTrue(_contains_tag(content, "qwe1", True))
        self.assertFalse(_contains_tag(content, "QWE1", True))
        self.assertFalse(_contains_tag(content, "Qwe1", True))

    def test_contains_tag_case_sensitive_false(self):
        content = "hello world qwe1 goodbye"
        self.assertTrue(_contains_tag(content, "qwe1", False))
        self.assertTrue(_contains_tag(content, "QWE1", False))
        self.assertTrue(_contains_tag(content, "Qwe1", False))

    def test_add_tag_at_start(self):
        content = "existing content"
        result = _add_tag_to_content(content, "TAG", True)
        self.assertEqual(result, "TAGexisting content")

    def test_add_tag_at_end(self):
        content = "existing content"
        result = _add_tag_to_content(content, "TAG", False)
        self.assertEqual(result, "existing contentTAG")

    def test_tag_added_to_txt_file_without_tag(self):
        test_file = self.root / "test.txt"
        test_file.write_text("original content", encoding="utf-8")
        
        count = _process_files([self.root], "NEWTAG", no_confirm=True)
        
        self.assertEqual(count, 1)
        self.assertEqual(test_file.read_text(encoding="utf-8"), "NEWTAGoriginal content")

    def test_skip_txt_file_already_has_tag(self):
        test_file = self.root / "skip.txt"
        test_file.write_text("qwe1existing content", encoding="utf-8")
        
        count = _process_files([self.root], "qwe1", no_confirm=True)
        
        self.assertEqual(count, 0)
        self.assertEqual(test_file.read_text(encoding="utf-8"), "qwe1existing content")

    def test_multiple_files_mixed_tags(self):
        has_tag = self.root / "has_tag.txt"
        has_tag.write_text("qwe1content", encoding="utf-8")
        
        no_tag_1 = self.root / "no_tag_1.txt"
        no_tag_1.write_text("file1", encoding="utf-8")
        
        no_tag_2 = self.root / "no_tag_2.txt"
        no_tag_2.write_text("file2", encoding="utf-8")
        
        count = _process_files([self.root], "qwe1", no_confirm=True)
        
        self.assertEqual(count, 2)
        self.assertEqual(has_tag.read_text(encoding="utf-8"), "qwe1content")
        self.assertEqual(no_tag_1.read_text(encoding="utf-8"), "qwe1file1")
        self.assertEqual(no_tag_2.read_text(encoding="utf-8"), "qwe1file2")

    def test_nested_directory_processing(self):
        nested_dir = self.root / "nested"
        nested_dir.mkdir()
        
        nested_file = nested_dir / "nested.txt"
        nested_file.write_text("nested content", encoding="utf-8")
        
        root_file = self.root / "root.txt"
        root_file.write_text("root content", encoding="utf-8")
        
        count = _process_files([self.root], "TAG", no_confirm=True)
        
        self.assertEqual(count, 2)
        self.assertEqual(nested_file.read_text(encoding="utf-8"), "TAGnested content")
        self.assertEqual(root_file.read_text(encoding="utf-8"), "TAGroot content")

    def test_non_txt_file_ignored(self):
        other_file = self.root / "other.md"
        other_file.write_text("markdown content", encoding="utf-8")
        
        txt_file = self.root / "readme.txt"
        txt_file.write_text("text content", encoding="utf-8")
        
        count = _process_files([self.root], "TAG", no_confirm=True)
        
        self.assertEqual(count, 1)
        self.assertEqual(other_file.read_text(encoding="utf-8"), "markdown content")
        self.assertEqual(txt_file.read_text(encoding="utf-8"), "TAGtext content")


if MODULE_AVAILABLE and command_paths is not None:
    class TxtAddTagArgparseTests(unittest.TestCase):
        def setUp(self):
            self.tmp = tempfile.TemporaryDirectory()
            self.root = Path(self.tmp.name).resolve()

        def tearDown(self):
            self.tmp.cleanup()

        def test_argparse_tag_argument_used(self):
            """--tag argument should be used when provided."""
            from dev_helper.apply.txt_add_tag import parse_args
            args = parse_args(["--tag", "CUSTOM_TAG"])
            self.assertEqual(args.tag, "CUSTOM_TAG")

        def test_argparse_no_confirm_flag(self):
            """--no-confirm flag should be parsed correctly."""
            from dev_helper.apply.txt_add_tag import parse_args
            args = parse_args(["--no-confirm"])
            self.assertTrue(args.no_confirm)

        def test_main_uses_path_resolution(self):
            """main() should use resolve_paths when available."""
            from dev_helper.apply.txt_add_tag import main
            
            test_file = self.root / "resolved.txt"
            test_file.write_text("content", encoding="utf-8")
            
            with chdir(self.root), patch.object(
                command_paths, "paths_from_clipboard", return_value=[]
            ), patch.object(
                command_paths, "_clipboard_text", return_value=""
            ):
                exit_code = main(["--no-confirm", str(self.root)])
            
            self.assertEqual(exit_code, 0)
            self.assertEqual(test_file.read_text(encoding="utf-8"), "qwe1content")

    class TxtAddTagConstantFallbackTests(unittest.TestCase):
        """Tests for constant PATH/TAG fallback behavior."""

        def setUp(self):
            self.tmp = tempfile.TemporaryDirectory()
            self.root = Path(self.tmp.name).resolve()

        def tearDown(self):
            self.tmp.cleanup()

        def test_resolve_paths_constant_origin(self):
            """When args/stdin/clipboard are empty and constant PATH exists."""
            import argparse
            from dev_helper.apply.txt_add_tag import resolve_paths
            const_dir = self.root / "const-dir"
            const_dir.mkdir()
            with patch.object(
                command_paths, "paths_from_clipboard", return_value=[]
            ), patch.object(
                command_paths, "_clipboard_text", return_value=""
            ):
                rp = resolve_paths(
                    None, arg_names=("paths",), constant=str(const_dir)
                )
            self.assertEqual(rp.origin, "constant")
            self.assertEqual(str(rp.paths[0]), str(const_dir))

        def test_resolve_paths_constant_missing_falls_to_cwd(self):
            """When args/stdin/clipboard empty and constant PATH doesn't exist."""
            from dev_helper.apply.txt_add_tag import resolve_paths
            cwd_dir = self.root / "cwd-test"
            cwd_dir.mkdir()
            with chdir(cwd_dir), patch.object(
                command_paths, "paths_from_clipboard", return_value=[]
            ):
                rp = resolve_paths(None, arg_names=("paths",), constant="__non_existent__")
            self.assertEqual(rp.origin, "cwd")
            self.assertEqual(str(rp.paths[0]), str(cwd_dir.resolve()))

        def test_resolve_input_text_constant_origin(self):
            """When args missing and constant TAG exists, it should be used."""
            import argparse
            from dev_helper.apply.txt_add_tag import resolve_input_text
            ns = argparse.Namespace(tag=None)
            result = resolve_input_text(
                ns, arg_names=("tag",), use_stdin=False, use_clipboard=False, constant="CONSTANT_TAG"
            )
            self.assertEqual(result.origin, "constant")
            self.assertEqual(result.text, "CONSTANT_TAG")

        def test_main_uses_constant_tag_when_no_args(self):
            """main() should use constant TAG when --tag not provided and no clipboard."""
            from dev_helper.apply.txt_add_tag import main
            
            test_file = self.root / "const-tag.txt"
            test_file.write_text("content", encoding="utf-8")
            
            with patch.object(
                command_paths, "paths_from_clipboard", return_value=[]
            ), patch.object(
                command_paths, "_clipboard_text", return_value=""
            ):
                exit_code = main(["--no-confirm", str(self.root)])
            
            self.assertEqual(exit_code, 0)
            self.assertEqual(test_file.read_text(encoding="utf-8"), "qwe1content")


if __name__ == "__main__":
    unittest.main()