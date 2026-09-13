from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from dev_helper.to_clipboard.to_clipboard import concatenate_files, _format_block


class TestContentMarkWithSignature(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_signature_format_uses_content_mark_format_markdown(self):
        """When CONTENT_TO_SIGNATURE=True, signatures should be formatted with CONTENT_MARK_FORMAT."""
        captured = {}

        def capture_set(text):
            captured["text"] = text

        # Create test files with Python code that extracts signatures
        (self.root / "file1.py").write_text("def foo():\n    pass\n\ndef bar():\n    pass")
        (self.root / "file2.py").write_text("def baz():\n    return 1")
        (self.root / "file3.txt").write_text("plain text")

        with patch(
            "dev_helper.to_clipboard.to_clipboard.copy_to_clipboard",
            side_effect=capture_set,
        ):
            with patch(
                "dev_helper.to_clipboard.to_clipboard._get_paths_from_clipboard",
                return_value=[str(self.root / "file1.py"), str(self.root / "file2.py"), str(self.root / "file3.txt")],
            ):
                with patch(
                    "dev_helper.to_clipboard.to_clipboard.CONTENT_TO_SIGNATURE",
                    True,
                ):
                    with patch(
                        "dev_helper.to_clipboard.to_clipboard.CONTENT_MARK_FORMAT",
                        "markdown",
                    ):
                        concatenate_files()

        result = captured.get("text", "")

        # Should have file path markers between content
        self.assertIn("file1.py", result)
        self.assertIn("file2.py", result)
        
        # Should have markdown code fences
        self.assertIn("```python", result)
        
        # Should have signature content
        self.assertIn("def foo()", result)
        self.assertIn("def bar()", result)
        self.assertIn("def baz()", result)

        # Verify that content is properly separated (not just newlines)
        parts = result.split("\n\n")
        # Each file should have its own section with path marker
        has_file1_marker = any("file1.py" in p for p in parts)
        has_file2_marker = any("file2.py" in p for p in parts)
        self.assertTrue(has_file1_marker, f"file1.py marker not found. Result: {result}")
        self.assertTrue(has_file2_marker, f"file2.py marker not found. Result: {result}")

    def test_signature_format_uses_content_mark_format_default(self):
        """When CONTENT_TO_SIGNATURE=True with default format, should use ====== markers."""
        captured = {}

        def capture_set(text):
            captured["text"] = text

        (self.root / "a.py").write_text("def test_func():\n    pass")
        (self.root / "b.py").write_text("class TestClass:\n    pass")

        with patch(
            "dev_helper.to_clipboard.to_clipboard.copy_to_clipboard",
            side_effect=capture_set,
        ):
            with patch(
                "dev_helper.to_clipboard.to_clipboard._get_paths_from_clipboard",
                return_value=[str(self.root / "a.py"), str(self.root / "b.py")],
            ):
                with patch(
                    "dev_helper.to_clipboard.to_clipboard.CONTENT_TO_SIGNATURE",
                    True,
                ):
                    with patch(
                        "dev_helper.to_clipboard.to_clipboard.CONTENT_MARK_FORMAT",
                        "default",
                    ):
                        concatenate_files()

        result = captured.get("text", "")

        # Should have default format markers (======)
        self.assertIn("====== a.py", result)
        self.assertIn("====== b.py", result)

    def test_format_signature_chunk_markdown(self):
        """_format_block should format with markdown when CONTENT_MARK_FORMAT=markdown."""
        with patch(
            "dev_helper.to_clipboard.to_clipboard.CONTENT_MARK_FORMAT",
            "markdown",
        ):
            result = _format_block(["test.py"], "def foo():\n    pass")
        
        self.assertIn("test.py", result)
        self.assertIn("```python", result)
        self.assertIn("def foo()", result)

    def test_format_signature_chunk_default(self):
        """_format_block should format with ====== when CONTENT_MARK_FORMAT=default."""
        with patch(
            "dev_helper.to_clipboard.to_clipboard.CONTENT_MARK_FORMAT",
            "default",
        ):
            result = _format_block(["test.py"], "def foo():\n    pass")
        
        self.assertIn("====== test.py", result)
        self.assertIn("def foo()", result)


if __name__ == "__main__":
    unittest.main()