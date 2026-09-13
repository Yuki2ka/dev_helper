from __future__ import annotations

import argparse
import io
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from .conftest import chdir

from path_args import command_paths
from path_args.example_commands import copy_concat, write_text_file


ORIGIN_ARGS = "args"
ORIGIN_STDIN = "stdin"
ORIGIN_CLIPBOARD = "clipboard"
ORIGIN_CONSTANT = "constant"
ORIGIN_CWD = "cwd"



@contextmanager
def mock_stdin(text: str | None):
    """Simulate piped/redirected stdin."""
    if text is None:
        yield
        return

    old_stdin = sys.stdin
    old_isatty = sys.stdin.isatty

    try:
        stream = io.StringIO(text)
        stream.isatty = lambda: False
        sys.stdin = stream
        yield
    finally:
        sys.stdin = old_stdin
        sys.stdin.isatty = old_isatty


class ExampleCommandResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def mkdir_with_file(self, relative_dir: str, filename: str, text: str) -> Path:
        directory = self.root / relative_dir
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).write_text(text, encoding="utf-8")
        return directory

    def empty_reader_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            paths=[], source=None, inputs=None, no_recursive=False, stdout=False
        )

    def empty_writer_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            paths=[],
            destination=None,
            outputs=None,
            name="note",
            ext="txt",
            text=None,
            stdout=False,
        )

    # ------------------------------------------------------------------
    #  copy_concat – path resolution tests
    # ------------------------------------------------------------------

    def test_reader_source_arg_wins_over_stdin_clipboard_and_constant(self) -> None:
        arg_dir = self.mkdir_with_file("arg-src", "a.txt", "ARG")
        stdin_dir = self.mkdir_with_file("stdin-src", "s.txt", "STDIN")
        clip_dir = self.mkdir_with_file("clip-src", "b.txt", "CLIP")
        const_dir = self.mkdir_with_file("const-src", "c.txt", "CONST")

        args = self.empty_reader_args()
        args.source = [str(arg_dir)]
        captured: list[str] = []

        def capture(text: str) -> None:
            captured.append(text)

        with mock_stdin(str(stdin_dir) + "\n"), patch.object(
            command_paths, "paths_from_clipboard", return_value=[str(clip_dir)]
        ), patch.object(copy_concat, "DEFAULT_SOURCE", str(const_dir)):
            result = copy_concat.run(args, copy=capture)

        self.assertEqual(result.origin, ORIGIN_ARGS)
        self.assertEqual(result.paths, (arg_dir,))
        self.assertIn("ARG", result.text)
        self.assertNotIn("STDIN", result.text)
        self.assertNotIn("CLIP", result.text)
        self.assertNotIn("CONST", result.text)
        self.assertEqual(captured, [result.text])

    def test_reader_uses_stdin_paths_when_no_args(self) -> None:
        stdin_dir = self.mkdir_with_file("stdin-src", "stdin.txt", "FROM STDIN")
        clip_dir = self.mkdir_with_file("clip-src", "clip.txt", "FROM CLIP")
        const_dir = self.mkdir_with_file("const-src", "const.txt", "FROM CONST")
        args = self.empty_reader_args()

        with mock_stdin(str(stdin_dir) + "\n"), patch.object(
            command_paths, "paths_from_clipboard", return_value=[str(clip_dir)]
        ), patch.object(copy_concat, "DEFAULT_SOURCE", str(const_dir)):
            result = copy_concat.run(args)

        self.assertEqual(result.origin, ORIGIN_STDIN)
        self.assertEqual(result.paths, (stdin_dir,))
        self.assertIn("FROM STDIN", result.text)
        self.assertNotIn("FROM CLIP", result.text)
        self.assertNotIn("FROM CONST", result.text)

    def test_reader_stdin_multiple_path_lines(self) -> None:
        d1 = self.mkdir_with_file("multi1", "a.txt", "AAA")
        d2 = self.mkdir_with_file("multi2", "b.txt", "BBB")
        args = self.empty_reader_args()

        with mock_stdin(f"{d1}\n{d2}\n"), patch.object(
            command_paths, "paths_from_clipboard", return_value=[]
        ):
            result = copy_concat.run(args)

        self.assertEqual(result.origin, ORIGIN_STDIN)
        self.assertEqual(len(result.paths), 2)
        self.assertIn("AAA", result.text)
        self.assertIn("BBB", result.text)

    def test_reader_stdin_no_valid_paths_falls_through_to_clipboard(self) -> None:
        """When stdin has text but no valid paths, fall through."""
        clip_dir = self.mkdir_with_file("clip-src", "clip.txt", "FROM CLIP")
        args = self.empty_reader_args()

        with mock_stdin("not/a/real/path\n"), patch.object(
            command_paths, "paths_from_clipboard", return_value=[str(clip_dir)]
        ):
            result = copy_concat.run(args)

        self.assertEqual(result.origin, ORIGIN_CLIPBOARD)
        self.assertIn("FROM CLIP", result.text)

    def test_reader_stdin_empty_falls_through_to_clipboard(self) -> None:
        """Empty stdin should fall through, not block."""
        clip_dir = self.mkdir_with_file("clip-src", "clip.txt", "FROM CLIP")
        args = self.empty_reader_args()

        with mock_stdin("   \n"), patch.object(
            command_paths, "paths_from_clipboard", return_value=[str(clip_dir)]
        ):
            result = copy_concat.run(args)

        self.assertEqual(result.origin, ORIGIN_CLIPBOARD)

    def test_reader_input_alias_is_treated_as_source_arg(self) -> None:
        input_dir = self.mkdir_with_file("input-alias", "input.txt", "INPUT ALIAS")
        args = copy_concat.parse_args(["--input", str(input_dir)])

        with mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[]
        ):
            result = copy_concat.run(args)

        self.assertEqual(result.origin, ORIGIN_ARGS)
        self.assertEqual(result.paths, (input_dir,))
        self.assertIn("INPUT ALIAS", result.text)

    def test_reader_uses_clipboard_when_no_args_and_no_stdin(self) -> None:
        clip_dir = self.mkdir_with_file("clip-src", "clip.txt", "FROM CLIPBOARD PATH")
        const_dir = self.mkdir_with_file("const-src", "const.txt", "FROM CONSTANT")
        args = self.empty_reader_args()

        with mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[str(clip_dir)]
        ), patch.object(copy_concat, "DEFAULT_SOURCE", str(const_dir)):
            result = copy_concat.run(args)

        self.assertEqual(result.origin, ORIGIN_CLIPBOARD)
        self.assertEqual(result.paths, (clip_dir,))
        self.assertIn("FROM CLIPBOARD PATH", result.text)
        self.assertNotIn("FROM CONSTANT", result.text)

    def test_reader_uses_existing_constant_when_args_stdin_and_clipboard_empty(self) -> None:
        const_dir = self.mkdir_with_file("const-src", "const.txt", "FROM CONSTANT")
        args = self.empty_reader_args()

        with mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[]
        ), patch.object(copy_concat, "DEFAULT_SOURCE", str(const_dir)):
            result = copy_concat.run(args)

        self.assertEqual(result.origin, ORIGIN_CONSTANT)
        self.assertEqual(result.paths, (const_dir,))
        self.assertIn("FROM CONSTANT", result.text)

    def test_reader_falls_back_to_cwd_when_constant_is_empty_or_missing(self) -> None:
        cwd_dir = self.mkdir_with_file("cwd", "cwd.txt", "FROM CWD")
        args = self.empty_reader_args()

        with chdir(cwd_dir), mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[]
        ), patch.object(copy_concat, "DEFAULT_SOURCE", str(self.root / "does-not-exist")):
            result = copy_concat.run(args)

        self.assertEqual(result.origin, ORIGIN_CWD)
        self.assertEqual(result.paths, (cwd_dir,))
        self.assertIn("FROM CWD", result.text)

    # ------------------------------------------------------------------
    #  write_text_file – destination path resolution tests
    # ------------------------------------------------------------------

    def test_writer_destination_arg_wins_and_creates_missing_directory(self) -> None:
        dest_dir = self.root / "new" / "nested"
        stdin_dir = self.root / "stdin-dest"
        clip_dir = self.root / "clip-dest"
        const_dir = self.root / "const-dest"
        stdin_dir.mkdir()
        clip_dir.mkdir()
        const_dir.mkdir()

        args = self.empty_writer_args()
        args.destination = [str(dest_dir)]
        args.text = "HELLO FROM ARGS"

        with mock_stdin(str(stdin_dir) + "\n"), patch.object(
            command_paths, "paths_from_clipboard", return_value=[str(clip_dir)]
        ), patch.object(write_text_file, "DEFAULT_DESTINATION", str(const_dir)):
            result = write_text_file.run(args)

        self.assertEqual(result.dest_origin, ORIGIN_ARGS)
        self.assertEqual(result.text_origin, ORIGIN_ARGS)
        self.assertEqual(result.dest_paths, (dest_dir,))
        self.assertTrue(dest_dir.is_dir(), "missing destination dirs should be created")
        self.assertEqual(result.output_file, dest_dir / "note.txt")
        self.assertEqual(result.output_file.read_text(encoding="utf-8"), "HELLO FROM ARGS")
        self.assertFalse((stdin_dir / "note.txt").exists())
        self.assertFalse((clip_dir / "note.txt").exists())
        self.assertFalse((const_dir / "note.txt").exists())

    def test_writer_destination_from_stdin(self) -> None:
        """Destination path comes from stdin when no args given."""
        dest_dir = self.root / "stdin-dest"
        dest_dir.mkdir()
        clip_dir = self.root / "clip-dest"
        clip_dir.mkdir()
        args = self.empty_writer_args()
        args.text = "FROM STDIN DEST"

        with mock_stdin(str(dest_dir) + "\n"), patch.object(
            command_paths, "paths_from_clipboard", return_value=[str(clip_dir)]
        ):
            result = write_text_file.run(args)

        self.assertEqual(result.dest_origin, ORIGIN_STDIN)
        self.assertTrue(dest_dir.is_dir())
        self.assertEqual(result.output_file, dest_dir / "note.txt")
        self.assertEqual(result.output_file.read_text(encoding="utf-8"), "FROM STDIN DEST")
        self.assertFalse((clip_dir / "note.txt").exists())

    # ------------------------------------------------------------------
    #  write_text_file – input text resolution tests
    # ------------------------------------------------------------------

    def test_writer_text_from_stdin_second_priority(self) -> None:
        """--text not given; stdin provides the content."""
        dest_dir = self.root / "text-stdin"
        dest_dir.mkdir()
        args = self.empty_writer_args()
        args.destination = [str(dest_dir)]

        with mock_stdin("STDIN CONTENT\nline2"), patch.object(
            command_paths, "_clipboard_text", return_value=""
        ):
            result = write_text_file.run(args)

        self.assertEqual(result.text_origin, ORIGIN_STDIN)
        self.assertEqual(result.text, "STDIN CONTENT\nline2")
        self.assertEqual(result.output_file.read_text(encoding="utf-8"), "STDIN CONTENT\nline2")

    def test_writer_text_from_clipboard_when_no_args_and_no_stdin(self) -> None:
        """When --text is omitted and stdin is a terminal, clipboard is used."""
        dest_dir = self.root / "text-clip"
        dest_dir.mkdir()
        args = self.empty_writer_args()
        args.destination = [str(dest_dir)]

        # stdin is a tty => skipped. Clipboard has content.
        with mock_stdin(None), patch.object(
            command_paths, "_clipboard_text", return_value="FROM CLIPBOARD"
        ):
            result = write_text_file.run(args)

        self.assertEqual(result.text_origin, ORIGIN_CLIPBOARD)
        self.assertEqual(result.output_file.read_text(encoding="utf-8"), "FROM CLIPBOARD")

    def test_writer_text_from_args_wins_over_stdin(self) -> None:
        """--text argument always wins over stdin."""
        dest_dir = self.root / "text-args-win"
        dest_dir.mkdir()
        args = self.empty_writer_args()
        args.destination = [str(dest_dir)]
        args.text = "ARG TEXT"

        with mock_stdin("STDIN IGNORED"):
            result = write_text_file.run(args)

        self.assertEqual(result.text_origin, ORIGIN_ARGS)
        self.assertEqual(result.output_file.read_text(encoding="utf-8"), "ARG TEXT")

    def test_writer_output_alias_can_be_exact_file_path(self) -> None:
        out_file = self.root / "exact" / "answer.md"
        args = write_text_file.parse_args(["--output", str(out_file), "--text", "markdown"])

        with mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[]
        ):
            result = write_text_file.run(args)

        self.assertEqual(result.dest_origin, "args")
        self.assertEqual(result.output_file, out_file)
        self.assertTrue(out_file.exists())
        self.assertEqual(out_file.read_text(encoding="utf-8"), "markdown")

    def test_writer_uses_clipboard_destination_when_no_args(self) -> None:
        clip_dir = self.root / "clip-dest"
        const_dir = self.root / "const-dest"
        clip_dir.mkdir()
        const_dir.mkdir()
        args = self.empty_writer_args()

        with mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[str(clip_dir)]
        ), patch.object(write_text_file, "DEFAULT_DESTINATION", str(const_dir)):
            result = write_text_file.run(args, text="FROM CLIP DEST")

        self.assertEqual(result.dest_origin, ORIGIN_CLIPBOARD)
        self.assertEqual(result.output_file, clip_dir / "note.txt")
        self.assertEqual(result.output_file.read_text(encoding="utf-8"), "FROM CLIP DEST")
        self.assertFalse((const_dir / "note.txt").exists())

    def test_writer_uses_existing_constant_when_args_and_clipboard_empty(self) -> None:
        const_dir = self.root / "const-dest"
        const_dir.mkdir()
        args = self.empty_writer_args()

        with mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[]
        ), patch.object(write_text_file, "DEFAULT_DESTINATION", str(const_dir)):
            result = write_text_file.run(args, text="FROM CONST DEST")

        self.assertEqual(result.dest_origin, ORIGIN_CONSTANT)
        self.assertEqual(result.output_file, const_dir / "note.txt")
        self.assertEqual(result.output_file.read_text(encoding="utf-8"), "FROM CONST DEST")

    def test_writer_falls_back_to_cwd_when_constant_empty_or_missing(self) -> None:
        cwd_dir = self.root / "cwd-dest"
        cwd_dir.mkdir()
        args = self.empty_writer_args()

        with chdir(cwd_dir), mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[]
        ), patch.object(write_text_file, "DEFAULT_DESTINATION", ""):
            result = write_text_file.run(args, text="FROM CWD DEST")

        self.assertEqual(result.dest_origin, ORIGIN_CWD)
        self.assertEqual(result.output_file, cwd_dir / "note.txt")
        self.assertEqual(result.output_file.read_text(encoding="utf-8"), "FROM CWD DEST")

    def test_writer_does_not_overwrite_when_hardcoded_constant_is_false(self) -> None:
        dest_dir = self.root / "overwrite-check"
        dest_dir.mkdir()
        original = dest_dir / "note.txt"
        original.write_text("OLD", encoding="utf-8")
        args = self.empty_writer_args()
        args.destination = [str(dest_dir)]
        args.text = "NEW"

        with mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[]
        ), patch.object(write_text_file, "OVERWRITE_EXISTING", False):
            result = write_text_file.run(args)

        self.assertEqual(original.read_text(encoding="utf-8"), "OLD")
        self.assertEqual(result.output_file, dest_dir / "note_001.txt")
        self.assertEqual(result.output_file.read_text(encoding="utf-8"), "NEW")

    def test_writer_overwrites_when_hardcoded_constant_is_true(self) -> None:
        dest_dir = self.root / "overwrite-true"
        dest_dir.mkdir()
        original = dest_dir / "note.txt"
        original.write_text("OLD", encoding="utf-8")
        args = self.empty_writer_args()
        args.destination = [str(dest_dir)]
        args.text = "NEW"

        with mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[]
        ), patch.object(write_text_file, "OVERWRITE_EXISTING", True):
            result = write_text_file.run(args)

        self.assertEqual(original.read_text(encoding="utf-8"), "NEW")
        self.assertEqual(result.output_file, original)

    def test_reader_no_recursive_flag_excludes_nested_files(self) -> None:
        top = self.mkdir_with_file("nested-top", "top.txt", "TOP")
        nested = top / "sub"
        nested.mkdir()
        (nested / "nested.txt").write_text("NESTED", encoding="utf-8")
        args = self.empty_reader_args()
        args.source = [str(top)]
        args.no_recursive = True

        with mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[]
        ):
            result = copy_concat.run(args)

        self.assertEqual(result.origin, ORIGIN_ARGS)
        self.assertIn("TOP", result.text)
        self.assertNotIn("NESTED", result.text)

    # ------------------------------------------------------------------
    #  --stdout flag tests
    # ------------------------------------------------------------------

    def test_writer_stdout_flag_prints_text(self) -> None:
        """--stdout causes the written text to also appear on stdout."""
        dest_dir = self.root / "stdout-test"
        dest_dir.mkdir()
        args = self.empty_writer_args()
        args.destination = [str(dest_dir)]
        args.text = "STDOUT CONTENT"
        args.stdout = True

        with mock_stdin(None):
            result = write_text_file.run(args)

        self.assertEqual(result.text, "STDOUT CONTENT")
        # File is still written
        self.assertTrue((dest_dir / "note.txt").exists())

    def test_reader_stdout_flag_prints_concat_text(self) -> None:
        """--stdout on copy_concat prints the concatenated text."""
        src = self.mkdir_with_file("stdout-src", "f.txt", "STDOUT READER")
        args = self.empty_reader_args()
        args.source = [str(src)]
        args.stdout = True

        with mock_stdin(None):
            result = copy_concat.run(args)

        self.assertIn("STDOUT READER", result.text)

    # ------------------------------------------------------------------
    #  resolve_input_text unit tests
    # ------------------------------------------------------------------

    def test_resolve_input_text_args_first(self) -> None:
        args = argparse.Namespace(text="explicit text", content=None)
        result = command_paths.resolve_input_text(
            args, arg_names=("text", "content"), use_stdin=False, use_clipboard=False
        )
        self.assertEqual(result.text, "explicit text")
        self.assertEqual(result.origin, ORIGIN_ARGS)

    def test_resolve_input_text_stdin_second(self) -> None:
        args = argparse.Namespace(text=None, content=None)
        with mock_stdin("piped data\n"):
            result = command_paths.resolve_input_text(
                args, arg_names=("text", "content"), use_stdin=True, use_clipboard=False
            )
        self.assertEqual(result.text, "piped data\n")
        self.assertEqual(result.origin, ORIGIN_STDIN)

    def test_resolve_input_text_skips_stdin_when_tty(self) -> None:
        args = argparse.Namespace(text=None, content=None)
        # mock_stdin(None) means sys.stdin.isatty() returns True
        with mock_stdin(None), patch.object(
            command_paths, "_clipboard_text", return_value="clip"
        ):
            result = command_paths.resolve_input_text(
                args, arg_names=("text", "content"), use_stdin=True, use_clipboard=True
            )
        self.assertEqual(result.text, "clip")
        self.assertEqual(result.origin, ORIGIN_CLIPBOARD)

    def test_resolve_input_text_clipboard_third(self) -> None:
        args = argparse.Namespace(text=None, content=None)
        with mock_stdin(None), patch.object(
            command_paths, "_clipboard_text", return_value="clipboard data"
        ):
            result = command_paths.resolve_input_text(
                args, arg_names=("text", "content"), use_stdin=True, use_clipboard=True
            )
        self.assertEqual(result.text, "clipboard data")
        self.assertEqual(result.origin, ORIGIN_CLIPBOARD)

    def test_resolve_input_text_constant_fallback(self) -> None:
        args = argparse.Namespace(text=None, content=None)
        with mock_stdin(None), patch.object(
            command_paths, "_clipboard_text", return_value=""
        ):
            result = command_paths.resolve_input_text(
                args,
                arg_names=("text", "content"),
                use_stdin=True,
                use_clipboard=True,
                constant="hardcoded fallback",
            )
        self.assertEqual(result.text, "hardcoded fallback")
        self.assertEqual(result.origin, ORIGIN_CONSTANT)

    def test_resolve_input_text_empty_when_nothing_available(self) -> None:
        args = argparse.Namespace(text=None, content=None)
        with mock_stdin(None), patch.object(
            command_paths, "_clipboard_text", return_value=""
        ):
            result = command_paths.resolve_input_text(
                args, arg_names=("text", "content"), use_stdin=True, use_clipboard=True
            )
        self.assertEqual(result.text, "")
        self.assertEqual(result.origin, ORIGIN_CONSTANT)

    # ------------------------------------------------------------------
    #  resolve_paths stdin path tests
    # ------------------------------------------------------------------

    def test_resolve_paths_stdin_before_clipboard(self) -> None:
        """stdin paths come before clipboard paths."""
        stdin_dir = self.mkdir_with_file("rp-stdin", "s.txt", "STDIN")
        clip_dir = self.mkdir_with_file("rp-clip", "c.txt", "CLIP")
        args = argparse.Namespace(paths=[], source=None)

        with mock_stdin(str(stdin_dir) + "\n"), patch.object(
            command_paths, "paths_from_clipboard", return_value=[str(clip_dir)]
        ):
            result = command_paths.resolve_paths(
                args, arg_names=("source", "paths"), use_stdin=True, clipboard=True
            )

        self.assertEqual(result.origin, ORIGIN_STDIN)
        self.assertEqual(result.paths, (stdin_dir,))

    def test_resolve_paths_stdin_tty_skipped(self) -> None:
        """When stdin is a terminal, skip to clipboard."""
        clip_dir = self.mkdir_with_file("rp-clip2", "c.txt", "CLIP")
        args = argparse.Namespace(paths=[], source=None)

        with mock_stdin(None), patch.object(
            command_paths, "paths_from_clipboard", return_value=[str(clip_dir)]
        ):
            result = command_paths.resolve_paths(
                args, arg_names=("source", "paths"), use_stdin=True, clipboard=True
            )

        self.assertEqual(result.origin, ORIGIN_CLIPBOARD)
        self.assertEqual(result.paths, (clip_dir,))


if __name__ == "__main__":
    unittest.main()
