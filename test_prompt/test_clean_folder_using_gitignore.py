from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .conftest import chdir

try:
    from path_args import command_paths, resolve_paths
except ImportError:
    command_paths = None
    resolve_paths = None

try:
    from dev_helper.apply.clean_folder_using_gitignore import (
        clean_paths,
        collect_ignored_items,
        is_hg_ignored,
        is_protected,
        load_hgignore_spec,
        main,
        parse_args,
        parse_gitignore_lines,
    )
    MODULE_AVAILABLE = True
except ImportError:
    MODULE_AVAILABLE = False


class CleanFolderUsingGitignoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_is_protected(self):
        self.assertTrue(is_protected(".git"))
        self.assertTrue(is_protected(Path(".git") / "config"))
        self.assertTrue(is_protected(".hg"))
        self.assertTrue(is_protected(Path(".hg") / "dirstate"))
        self.assertTrue(is_protected(".gitignore"))
        self.assertTrue(is_protected(Path("sub") / ".gitignore"))
        self.assertTrue(is_protected(".hgignore"))
        self.assertTrue(is_protected(".svn"))
        self.assertFalse(is_protected("build"))
        self.assertFalse(is_protected("main.pyc"))
        self.assertFalse(is_protected("src/app.py"))

    def test_parse_gitignore_lines(self):
        spec = parse_gitignore_lines(["*.pyc", "build/", "!keep.pyc"])
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertTrue(spec.match_file("test.pyc"))
        self.assertFalse(spec.match_file("keep.pyc"))
        self.assertTrue(spec.match_file("build/temp.o"))
        self.assertFalse(spec.match_file("main.py"))

    def test_hgignore_glob_and_regex(self):
        hgignore_content = "syntax: glob\n*.bak\nbuild/*\nsyntax: regexp\n^temp_.*\n"
        hg_file = self.root / ".hgignore"
        hg_file.write_text(hgignore_content, encoding="utf-8")

        matchers = load_hgignore_spec(self.root)
        self.assertTrue(is_hg_ignored("file.bak", matchers))
        self.assertTrue(is_hg_ignored("sub/file.bak", matchers))
        self.assertTrue(is_hg_ignored("build/out.bin", matchers))
        self.assertTrue(is_hg_ignored("temp_123.txt", matchers))
        self.assertFalse(is_hg_ignored("normal.txt", matchers))

    def test_no_ignore_files_returns_empty(self):
        folder = self.root / "no_ignore"
        folder.mkdir()
        (folder / "file.txt").write_text("hello", encoding="utf-8")
        (folder / "temp.o").write_text("binary", encoding="utf-8")

        candidates, has_ignore = collect_ignored_items(folder)
        self.assertFalse(has_ignore)
        self.assertEqual(len(candidates), 0)

        # clean_paths should report and do nothing
        removed = clean_paths([folder], no_confirm=True, use_trash=False)
        self.assertEqual(removed, 0)
        self.assertTrue((folder / "temp.o").exists())

    def test_clean_files_matching_gitignore(self):
        (self.root / ".gitignore").write_text("*.pyc\n*.log\nbuild/\n!keep.log\n", encoding="utf-8")
        (self.root / "app.py").write_text("print('hi')", encoding="utf-8")
        (self.root / "app.pyc").write_text("bytecode", encoding="utf-8")
        (self.root / "debug.log").write_text("logs", encoding="utf-8")
        (self.root / "keep.log").write_text("important logs", encoding="utf-8")

        build_dir = self.root / "build"
        build_dir.mkdir()
        (build_dir / "output.o").write_text("obj", encoding="utf-8")

        candidates, has_ignore = collect_ignored_items(self.root)
        self.assertTrue(has_ignore)
        candidate_names = {c.name for c in candidates}
        self.assertIn("app.pyc", candidate_names)
        self.assertIn("debug.log", candidate_names)
        self.assertIn("build", candidate_names)
        self.assertNotIn("app.py", candidate_names)
        self.assertNotIn("keep.log", candidate_names)
        self.assertNotIn(".gitignore", candidate_names)

        # Run clean_paths
        removed = clean_paths([self.root], no_confirm=True, use_trash=False)
        self.assertEqual(removed, 3)

        self.assertTrue((self.root / "app.py").exists())
        self.assertTrue((self.root / "keep.log").exists())
        self.assertTrue((self.root / ".gitignore").exists())
        self.assertFalse((self.root / "app.pyc").exists())
        self.assertFalse((self.root / "debug.log").exists())
        self.assertFalse(build_dir.exists())

    def test_clean_files_matching_hgignore(self):
        (self.root / ".hgignore").write_text("syntax: glob\n*.tmp\ncache/*\n", encoding="utf-8")
        (self.root / "main.rs").write_text("fn main() {}", encoding="utf-8")
        (self.root / "main.tmp").write_text("temp", encoding="utf-8")

        cache_dir = self.root / "cache"
        cache_dir.mkdir()
        (cache_dir / "item.dat").write_text("dat", encoding="utf-8")

        _, has_ignore = collect_ignored_items(self.root)
        self.assertTrue(has_ignore)

        removed = clean_paths([self.root], no_confirm=True, use_trash=False)
        self.assertEqual(removed, 2)
        self.assertTrue((self.root / "main.rs").exists())
        self.assertTrue((self.root / ".hgignore").exists())
        self.assertFalse((self.root / "main.tmp").exists())
        self.assertFalse(cache_dir.exists())

    def test_nested_gitignore_in_subfolder(self):
        (self.root / ".gitignore").write_text("*.root_ignore\n", encoding="utf-8")
        sub_dir = self.root / "subdir"
        sub_dir.mkdir()
        (sub_dir / ".gitignore").write_text("*.sub_ignore\n", encoding="utf-8")

        (self.root / "a.root_ignore").write_text("del", encoding="utf-8")
        (sub_dir / "b.sub_ignore").write_text("del", encoding="utf-8")
        (sub_dir / "c.root_ignore").write_text("del", encoding="utf-8")
        (sub_dir / "keep.txt").write_text("keep", encoding="utf-8")

        removed = clean_paths([self.root], no_confirm=True, use_trash=False)
        self.assertEqual(removed, 3)
        self.assertTrue((sub_dir / "keep.txt").exists())
        self.assertTrue((self.root / ".gitignore").exists())
        self.assertTrue((sub_dir / ".gitignore").exists())
        self.assertFalse((self.root / "a.root_ignore").exists())
        self.assertFalse((sub_dir / "b.sub_ignore").exists())
        self.assertFalse((sub_dir / "c.root_ignore").exists())

    def test_protected_dirs_and_files_never_deleted(self):
        (self.root / ".gitignore").write_text(".git/\n.gitignore\n*.git\n", encoding="utf-8")
        git_dir = self.root / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("git config", encoding="utf-8")

        candidates, has_ignore = collect_ignored_items(self.root)
        self.assertTrue(has_ignore)
        self.assertEqual(len(candidates), 0)

        removed = clean_paths([self.root], no_confirm=True, use_trash=False)
        self.assertEqual(removed, 0)
        self.assertTrue((self.root / ".gitignore").exists())
        self.assertTrue((git_dir / "config").exists())

    def test_dry_run_does_not_delete(self):
        (self.root / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        tmp_file = self.root / "data.tmp"
        tmp_file.write_text("temp", encoding="utf-8")

        removed = clean_paths([self.root], dry_run=True, use_trash=False)
        self.assertEqual(removed, 1)
        self.assertTrue(tmp_file.exists())

    def test_confirm_menu_yes_inputs(self):
        for yes_input in ["1", "y", "Y", "yes", "YES"]:
            (self.root / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
            tmp_file = self.root / f"test_{yes_input}.tmp"
            tmp_file.write_text("temp", encoding="utf-8")

            with patch("builtins.input", return_value=yes_input):
                removed = clean_paths([self.root], confirm="ask", use_trash=False)
            self.assertEqual(removed, 1)
            self.assertFalse(tmp_file.exists())

    def test_confirm_menu_no_or_other_key_aborts(self):
        for no_input in ["n", "N", "no", "2", "q", "", "abort"]:
            (self.root / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
            tmp_file = self.root / f"keep_{no_input}.tmp"
            tmp_file.write_text("temp", encoding="utf-8")

            with patch("builtins.input", return_value=no_input):
                removed = clean_paths([self.root], confirm="ask", use_trash=False)
            self.assertEqual(removed, 0)
            self.assertTrue(tmp_file.exists())

    def test_single_file_target(self):
        (self.root / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        tmp_file = self.root / "single.tmp"
        tmp_file.write_text("temp", encoding="utf-8")

        removed = clean_paths([tmp_file], no_confirm=True, use_trash=False)
        self.assertEqual(removed, 1)
        self.assertFalse(tmp_file.exists())


class CleanFolderUsingGitignoreCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_args_defaults(self):
        args = parse_args([])
        self.assertEqual(args.paths, [])
        self.assertFalse(args.no_confirm)
        self.assertFalse(args.dry_run)
        self.assertIsNone(args.use_trash)

    def test_parse_args_flags(self):
        args = parse_args(["--yes", "--dry-run", "--permanent", "dir1", "dir2"])
        self.assertEqual(args.paths, ["dir1", "dir2"])
        self.assertTrue(args.no_confirm)
        self.assertTrue(args.dry_run)
        self.assertFalse(args.use_trash)

    def test_parse_args_trash_flag(self):
        args = parse_args(["-y", "--trash", "dir1"])
        self.assertTrue(args.no_confirm)
        self.assertTrue(args.use_trash)

    def test_main_cli_execution(self):
        (self.root / ".gitignore").write_text("*.cache\n", encoding="utf-8")
        cache_file = self.root / "app.cache"
        cache_file.write_text("cached", encoding="utf-8")

        with chdir(self.root):
            exit_code = main(["--no-confirm", "--permanent", str(self.root)])

        self.assertEqual(exit_code, 0)
        self.assertFalse(cache_file.exists())

    def test_main_cli_cwd_fallback(self):
        (self.root / ".gitignore").write_text("*.cache\n", encoding="utf-8")
        cache_file = self.root / "test.cache"
        cache_file.write_text("cached", encoding="utf-8")

        with chdir(self.root), patch.object(
            command_paths, "paths_from_clipboard", return_value=[]
        ) if command_paths else patch("builtins.print"):
            exit_code = main(["--no-confirm", "--permanent"])

        self.assertEqual(exit_code, 0)
        self.assertFalse(cache_file.exists())


if __name__ == "__main__":
    unittest.main()
