from __future__ import annotations

# === SETTINGS START ===
TARGET_EXTENSIONS = {"py", "htm", "html", "txt", "c", "cs", "itc"}
RECURSIVE = True
CONVERT_TO_LF = True      # Convert all line endings (CRLF, CR) to Unix LF (\n)
TRIM_TRAIL_SPACES = True  # remove space and tabs in the end of lines
CONVERT_TO_TABS = True    # Automatically convert spaces to 1 tab indentation
# === SETTINGS END ===

import argparse
import os
import sys
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

try:
    from path_args import resolve_paths, iter_existing_files
except ImportError:
    resolve_paths = None  # type: ignore
    iter_existing_files = None  # type: ignore

from dev_helper.common.text_utils import (
    normalize_indentation_to_tabs,
    normalize_line_endings,
    PRESERVE_SPACE_EXTS,
    trim_trailing_whitespace,
)


def main():
    parser = argparse.ArgumentParser(description="Convert line endings to Unix LF for specified file types")
    parser.add_argument("paths", nargs="*", help="File or directory paths")
    args = parser.parse_args()
    
    if resolve_paths is not None:
        resolved = resolve_paths(
            args,
            arg_names=("paths",),
            constant=None,
            clipboard=True,
            fallback_to_cwd=True,
            create="none",
        )
    else:
        resolved = None
        if not args.paths:
            args.paths = [os.getcwd()]
    
    if resolved is not None:
        target_paths = list(resolved)
    else:
        target_paths = [Path(p) if isinstance(p, (str, os.PathLike)) else p for p in args.paths]
    
    files_to_process = []
    for path in target_paths:
        if path.is_file():
            if path.suffix.lstrip(".").lower() in TARGET_EXTENSIONS:
                files_to_process.append(path)
        elif path.is_dir():
            if iter_existing_files is not None:
                for f in iter_existing_files(
                    [path],
                    recursive=RECURSIVE,
                    include_hidden=False,
                ):
                    if f.suffix.lstrip(".").lower() in TARGET_EXTENSIONS:
                        files_to_process.append(f)
            else:
                for root, _, filenames in os.walk(path):
                    for filename in filenames:
                        f = Path(root) / filename
                        if f.suffix.lstrip(".").lower() in TARGET_EXTENSIONS:
                            files_to_process.append(f)
    
    if not files_to_process:
        print("No matching files found.")
        return
    
    modified_count = 0
    for file_path in files_to_process:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                original = f.read()
        except Exception as e:
            print(f"[-] Error reading {file_path}: {e}", file=sys.stderr)
            continue

        text = original

        if CONVERT_TO_LF:
            text = normalize_line_endings(text)

        if TRIM_TRAIL_SPACES:
            text = trim_trailing_whitespace(text)

        ext = file_path.suffix.lstrip(".").lower()

        if CONVERT_TO_TABS:
            text = normalize_indentation_to_tabs(text, ext, PRESERVE_SPACE_EXTS)

        if text != original:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(text)
                print(f"[+] {file_path}")
                modified_count += 1
            except Exception as e:
                print(f"[-] Error writing {file_path}: {e}", file=sys.stderr)
    
    print(f"\nDone. Modified {modified_count} of {len(files_to_process)} files.")


if __name__ == "__main__":
    main()