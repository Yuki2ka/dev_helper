"""
Example writer command.

This module uses the same role-neutral resolver as copy_concat.py. It only
changes the argument names and the fallback constant:

    args -> stdin paths -> clipboard path refs -> DEFAULT_DESTINATION if exists -> cwd

Input text resolution:
    args (--text) -> stdin -> clipboard text -> hardcoded constant

Overwrite behavior is deliberately NOT a CLI option. It is a hardcoded constant.

Output:
    File is always written.  Additionally, --stdout prints the written text
    to stdout (useful for piping to other commands).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from path_args import (
    add_common_path_arguments,
    choose_output_file,
    resolve_input_text,
    resolve_paths,
)

DEFAULT_DESTINATION = ""      # empty/non-existing => fallback to cwd
OVERWRITE_EXISTING = False    # hardcoded default requested by policy

DESTINATION_ARG_NAMES = (
    "destination",
    "destinations",
    "dest",
    "dst",
    "output",
    "outputs",
    "out",
    "path",
    "paths",
)

INPUT_TEXT_ARG_NAMES = ("text", "content", "data")


@dataclass(frozen=True)
class WriteTextResult:
    dest_origin: str
    dest_paths: tuple[Path, ...]
    text_origin: str
    output_file: Path
    text: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write text to a resolved destination")
    add_common_path_arguments(parser, source=False)
    parser.add_argument(
        "-o",
        "--output",
        dest="outputs",
        action="append",
        help="Alias for --destination. Can be a file or directory.",
    )
    parser.add_argument(
        "--name",
        default="note",
        help="Default filename stem when destination is a directory",
    )
    parser.add_argument(
        "--ext",
        default="txt",
        help="Default extension when destination is a directory",
    )
    parser.add_argument(
        "--text",
        default=None,
        help="Text to write. If omitted, stdin is used, then clipboard, then empty string.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="After writing the file, also print its content to stdout.",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace, *, text: str | None = None) -> WriteTextResult:
    # --- resolve destination path ---
    dest = resolve_paths(
        args,
        arg_names=DESTINATION_ARG_NAMES,
        constant=DEFAULT_DESTINATION,
        create="auto",
    )

    # --- resolve input text ---
    if text is not None:
        # Caller explicitly passed text; it takes highest priority.
        input_text = resolve_input_text(
            # Build a mini-namespace so the explicit text acts as an "args" source.
            argparse.Namespace(text=text),
            arg_names=INPUT_TEXT_ARG_NAMES,
            use_stdin=False,
            use_clipboard=False,
        )
    else:
        input_text = resolve_input_text(
            args,
            arg_names=INPUT_TEXT_ARG_NAMES,
            use_stdin=True,
            use_clipboard=True,
        )

    content = input_text.text

    # --- write file ---
    output_file = choose_output_file(
        dest,
        default_stem=getattr(args, "name", "note"),
        default_suffix=getattr(args, "ext", "txt"),
        overwrite=OVERWRITE_EXISTING,
    )
    output_file.write_text(content, encoding="utf-8", newline="\n")

    return WriteTextResult(
        dest_origin=dest.origin,
        dest_paths=dest.paths,
        text_origin=input_text.origin,
        output_file=output_file,
        text=content,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(args)

    # --stdout flag: print content to stdout
    if getattr(args, "stdout", False):
        sys.stdout.write(result.text)
        if not result.text.endswith("\n"):
            sys.stdout.write("\n")

    print(
        f"Created: {result.output_file} "
        f"(destination from {result.dest_origin}, text from {result.text_origin})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
