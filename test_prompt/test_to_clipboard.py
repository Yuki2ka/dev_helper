from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from dev_helper.to_clipboard.to_clipboard import concatenate_files
from dev_helper.common.clipboard import copy_to_clipboard


class TreeOrderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

        folders = [
            "path_args", "standalone", "test_prompt",
            ".git", ".kilo", "dev_helper",
        ]
        files = [
            ".gitignore", "build.py", "clean.py",
            "LICENSE", "README.md",
        ]
        for name in folders:
            (self.root / name).mkdir()
        for name in files:
            (self.root / name).write_text("x")

        (self.root / "path_args" / "some_file.txt").write_text("x")

        (self.root / "standalone" / "new_file").mkdir()
        (self.root / "standalone" / "new_file" / "check_hash.py").write_text("x")

        (self.root / "test_prompt" / "test_dir").mkdir()
        (self.root / "test_prompt" / "test_dir" / "1").mkdir()
        (self.root / "test_prompt" / "test_dir" / "1" / "a.txt").write_text("x")

        (self.root / ".git" / "HEAD").write_text("ref: refs/heads/main")
        (self.root / ".kilo" / "agent-manager.json").write_text("{}")

        (self.root / "dev_helper" / "common").mkdir()
        (self.root / "dev_helper" / "common" / "__init__.py").write_text("x")

        self.actual_root = self.root

    def tearDown(self):
        import shutil
        shutil.rmtree(self.actual_root, ignore_errors=True)

    def test_folders_before_files_when_clipboard_has_mixed_paths(self):
        """Reproduce the user's exact clipboard list and assert folder trees come before file trees."""
        mixed_paths = [
            str(self.actual_root / "path_args"),
            str(self.actual_root / "standalone"),
            str(self.actual_root / "test_prompt"),
            str(self.actual_root / ".gitignore"),
            str(self.actual_root / "build.py"),
            str(self.actual_root / "clean.py"),
            str(self.actual_root / "LICENSE"),
            str(self.actual_root / "README.md"),
            str(self.actual_root / ".git"),
            str(self.actual_root / ".kilo"),
            str(self.actual_root / "dev_helper"),
        ]

        captured_text = {}

        def capture_set(text):
            captured_text["text"] = text

        with patch(
            "dev_helper.to_clipboard.to_clipboard.copy_to_clipboard",
            side_effect=capture_set,
        ):
            with patch(
                "dev_helper.to_clipboard.to_clipboard._get_paths_from_clipboard",
                return_value=mixed_paths,
            ):
                concatenate_files()

        final_string = captured_text.get("text", "")

        self.assertIn("standalone", final_string)
        self.assertIn("test_prompt", final_string)
        self.assertIn("dev_helper", final_string)
        self.assertIn("path_args", final_string)
        self.assertIn(".gitignore", final_string)
        self.assertIn("clean.py", final_string)

        standalone_pos = final_string.index("standalone")
        test_prompt_pos = final_string.index("test_prompt")
        dev_helper_pos = final_string.index("dev_helper")

        gitignore_pos = final_string.index(".gitignore")
        build_pos = final_string.index("build.py")
        clean_pos = final_string.index("clean.py")
        license_pos = final_string.index("LICENSE")
        readme_pos = final_string.index("README.md")

        folder_positions = [standalone_pos, test_prompt_pos, dev_helper_pos]
        file_positions = [gitignore_pos, build_pos, clean_pos, license_pos, readme_pos]

        max_folder = max(folder_positions)
        for fpos in file_positions:
            self.assertGreater(
                fpos, max_folder,
                f"file tree at pos {fpos} appears before last folder tree at pos {max_folder}"
            )


if __name__ == "__main__":
    unittest.main()
