from __future__ import annotations

# === SETTINGS START ===
PATH = None # Path resolution: argparse - PATH - CWD.
TAG = "qwe1" # TAG resolution arg - clipboard (not a path-like) - TAG
EXT = ["txt"]
CASE_SENSITIVE = True
ADD_AT_START = True # False mean add after file content +", "+TAG
# === SETTINGS END ===

import argparse
import os
import sys
from pathlib import Path

try:
    from path_args import resolve_paths, resolve_input_text
except ImportError:
    resolve_paths = None
    resolve_input_text = None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Add a tag string to TXT files")
    parser.add_argument("paths", nargs="*", help="Directory or file paths to process")
    parser.add_argument(
        "--tag",
        default=None,
        help="Tag string to add. Falls back to clipboard or default constant.",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip confirmation prompt",
    )
    return parser.parse_args(argv)


def _collect_txt_files(paths):
    """Yield TXT files from given paths (files or directories)."""
    for raw in paths:
        path = Path(raw) if isinstance(raw, (str, os.PathLike)) else raw
        if path.is_file() and path.suffix.lstrip(".") in EXT:
            yield path
        elif path.is_dir():
            for ext in EXT:
                yield from path.rglob(f"*.{ext}")


def _contains_tag(content: str, tag: str, case_sensitive: bool) -> bool:
    if case_sensitive:
        return tag in content
    return tag.lower() in content.lower()


def _add_tag_to_content(content: str, tag: str, add_at_start: bool) -> str:
    if add_at_start:
        return tag + content
    return content + tag


def _process_files(paths, tag, no_confirm=False):
    """Process files and add tag. Returns count of modified files."""
    to_modify = []

    for file_path in _collect_txt_files(paths):
        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        if _contains_tag(content, tag, CASE_SENSITIVE):
            continue

        to_modify.append(file_path)

    if not to_modify:
        print("No files need modification.")
        return 0

    if not no_confirm:
        print(f"Will add tag to {len(to_modify)} file(s):")
        for fp in to_modify:
            print(f"  {fp}")
        response = input("Proceed? [y/N] ")
        if response.lower() != "y":
            print("Aborted.")
            return 0

    for fp in to_modify:
        content = fp.read_text(encoding="utf-8")
        new_content = _add_tag_to_content(content, tag, ADD_AT_START)
        fp.write_text(new_content, encoding="utf-8")

    return len(to_modify)


def main(argv=None) -> int:
    args = parse_args(argv)

    if resolve_paths is not None:
        resolved_paths = resolve_paths(
            args,
            arg_names=("paths",),
            constant=PATH,
            clipboard=True,
            fallback_to_cwd=True,
            create="none",
        )
        target_paths = list(resolved_paths)
    else:
        if not args.paths:
            args.paths = [os.getcwd()]
        target_paths = [Path(p) if isinstance(p, (str, os.PathLike)) else p for p in args.paths]

    if resolve_input_text is not None:
        resolved_text = resolve_input_text(
            args,
            arg_names=("tag",),
            use_stdin=False,
            use_clipboard=True,
            constant=TAG,
        )
        tag = resolved_text.text
    else:
        tag = args.tag if args.tag is not None else TAG

    if not tag:
        print("No tag provided and no clipboard content available.", file=sys.stderr)
        return 1

    modified = _process_files(target_paths, tag, args.no_confirm)
    print(f"\nDone. Added to {modified} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())