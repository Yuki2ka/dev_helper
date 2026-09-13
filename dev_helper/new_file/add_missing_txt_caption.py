from __future__ import annotations

# === SETTINGS START ===
PATH = None # Path resolution: argparse - PATH - CWD.
EXT = ["jpg", "png", "webp"]
DATE_YEAR = 1980
# === SETTINGS END ===
"""
get path
looks for images in it and subfolders

if no txt with same name  - add  empty txt caption near it. set date of file as 0. (1980)

print path what added
"""
import argparse
import datetime
import os
import platform
import sys
from pathlib import Path

try:
    from path_args import resolve_paths
except ImportError:
    resolve_paths = None

if platform.system() == "Windows":
    import win32file
    import pywintypes


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Add missing .txt caption files for images")
    parser.add_argument("paths", nargs="*", help="Directory or file paths to process")
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip confirmation prompt",
    )
    return parser.parse_args(argv)


def _collect_image_files(paths):
    """Yield image files from given paths (files or directories)."""
    for raw in paths:
        path = Path(raw) if isinstance(raw, (str, os.PathLike)) else raw
        if path.is_file() and path.suffix.lstrip(".").lower() in EXT:
            yield path
        elif path.is_dir():
            for ext in EXT:
                yield from path.rglob(f"*.{ext}")


def _set_file_date_1980(path: Path):
    """Set file creation, access, and modification time to Jan 1, 1980, 00:00:00."""
    dt = datetime.datetime(DATE_YEAR, 1, 1, 0, 0, 0)
    timestamp = dt.timestamp()

    if platform.system() == "Windows":
        try:
            handle = win32file.CreateFile(
                str(path),
                win32file.GENERIC_WRITE,
                win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
            try:
                win_time = pywintypes.Time(dt)
                win32file.SetFileTime(handle, win_time, win_time, win_time)
            finally:
                win32file.CloseHandle(handle)
        except Exception:
            os.utime(path, (timestamp, timestamp))
    else:
        os.utime(path, (timestamp, timestamp))


def _process_images(paths, no_confirm=False):
    """Process images and create missing .txt caption files. Returns count of created files."""
    to_create = []

    for img_path in _collect_image_files(paths):
        txt_path = img_path.with_suffix(".txt")
        if txt_path.exists():
            continue
        to_create.append(txt_path)

    if not to_create:
        print("No missing caption files found.")
        return 0

    if not no_confirm:
        print(f"Will create {len(to_create)} caption file(s):")
        for fp in to_create:
            print(f"  {fp}")
        response = input("Proceed? [y/N] ")
        if response.lower() != "y":
            print("Aborted.")
            return 0

    for txt_path in to_create:
        txt_path.touch()
        _set_file_date_1980(txt_path)
        print(f"Created: {txt_path}")

    return len(to_create)


def main(argv=None) -> int:
    args = parse_args(argv)

    if resolve_paths is not None:
        resolved_paths = resolve_paths(
            args,
            arg_names=("paths",),
            constant=PATH,
            clipboard=False,
            fallback_to_cwd=True,
            create="none",
        )
        target_paths = list(resolved_paths)
    else:
        if not args.paths:
            args.paths = [os.getcwd()]
        target_paths = [Path(p) if isinstance(p, (str, os.PathLike)) else p for p in args.paths]

    created = _process_images(target_paths, args.no_confirm)
    print(f"\nDone. Created {created} caption file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())