"""
Example reader/copy command.

Resolution order is not implemented here. This module only declares which CLI
attributes count as source/path attributes, then delegates to command_paths.py:

    args -> stdin paths -> clipboard path refs -> DEFAULT_SOURCE if it exists -> cwd

The command reads files/dirs from the resolved location and returns/copies one
concatenated text blob. Tests inject a fake copy function, so no pyperclip
package is required.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from path_args import add_common_path_arguments, iter_existing_files, resolve_paths

DEFAULT_SOURCE = ""  # empty/non-existing => fallback to cwd

SOURCE_ARG_NAMES = (
    "source",
    "sources",
    "src",
    "input",
    "inputs",
    "path",
    "paths",
)


@dataclass(frozen=True)
class CopyConcatResult:
    origin: str
    paths: tuple[Path, ...]
    files: tuple[Path, ...]
    text: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy concatenated text from resolved files/dirs")
    add_common_path_arguments(parser, destination=False)
    parser.add_argument(
        "-i",
        "--input",
        dest="inputs",
        action="append",
        help="Alias for --source. Can be repeated.",
    )
    parser.add_argument(
        "--no-recursive", action="store_true", help="Do not recurse into directories"
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print concatenated text to stdout instead of just summary.",
    )
    return parser.parse_args(argv)


def build_text(files: Sequence[Path]) -> str:
    chunks: list[str] = []
    for i, file in enumerate(files):
        if i > 0:
            chunks.append("\n\n")
        chunks.append(f"===== {file} =====\n")
        chunks.append(file.read_text(encoding="utf-8", errors="replace"))
    return "".join(chunks)


def run(
    args: argparse.Namespace,
    *,
    copy: Callable[[str], None] | None = None,
) -> CopyConcatResult:
    resolved = resolve_paths(
        args,
        arg_names=SOURCE_ARG_NAMES,
        constant=DEFAULT_SOURCE,
        create="auto",  # always create missing dirs/parents; resolver stays role-neutral
    )

    files = tuple(
        sorted(
            iter_existing_files(
                resolved.paths, recursive=not getattr(args, "no_recursive", False)
            ),
            key=lambda p: str(p),
        )
    )
    text = build_text(files)

    if copy is not None:
        copy(text)

    return CopyConcatResult(origin=resolved.origin, paths=resolved.paths, files=files, text=text)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(args)

    if getattr(args, "stdout", False):
        sys.stdout.write(result.text)
        sys.stdout.write("\n")

    print(f"[copy_concat] origin={result.origin}; files={len(result.files)}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
