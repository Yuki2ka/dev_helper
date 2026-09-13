#!/usr/bin/env python3
"""Universal project backup tool (Windows & Linux).

Two modes:
  full        Create a compressed archive (tar.gz or zip) of the source tree
               then copy it to the destination. Does NOT exclude .git or
               benchmark_results/ (benchmark JSON output).
  mirror      Copy the source tree into a timestamped folder at the
               destination, skipping .git, benchmark_results/, benchmark_*.json,
               plus the default regenerable/build-artifact paths.

Usage:
  backup.py [--mode {full,mirror}] [--source DIR] [--dest DIR]
            [--name NAME] [--no-default-excludes] [--exclude PATTERN ...]

Defaults:
  source  = directory containing this script
  dest    = A: on Windows, /mnt/backup (or ./backup_out) otherwise
  name    = basename of the source directory

Full-mode exclusions (no .git, no benchmark_results):
  .kilo .svn .hg
  target node_modules vendor
  __pycache__ .pytest_cache .venv venv env
  build dist .cache .idea .vscode
  Cargo.lock package-lock.json
  *.exe *.pyc *.o *.obj *.so *.dll *.bin *.zip *.tar.gz

Mirror adds to the above:
  .git benchmark_results benchmark_*.json

Exclusion patterns are matched against any path component and as a suffix
(e.g. "*.exe").
"""

import argparse
import datetime as _dt
import fnmatch
import os
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

FULL_MODE_EXCLUDES = [
    # build / metadata / regenerable artifacts kept in full archives
    ".kilo", ".svn", ".hg",
    "target", "node_modules", "vendor",
    "__pycache__", ".pytest_cache", ".venv", "venv", "env",
    "build", "dist", ".cache", ".idea", ".vscode",
    "Cargo.lock", "package-lock.json",
    "*.exe", "*.pyc", "*.o", "*.obj", "*.so", "*.dll",
    "*.bin", "*.zip", "*.tar.gz",
]

MIN_MODE_EXCLUDES = FULL_MODE_EXCLUDES + [
    # min/mirror skips VCS history and regenerable benchmark output
    ".git",
    "benchmark_results",
    "benchmark_*.json",
]


def timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def iter_files(source: Path, excludes):
    """Yield files under source that are not excluded."""
    for root, dirs, files in os.walk(source):
        # prune excluded directories in place
        kept_dirs = []
        for d in dirs:
            if is_excluded(Path(root) / d, excludes):
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs

        for f in files:
            p = Path(root) / f
            if is_excluded(p, excludes):
                continue
            yield p


def is_excluded(path: Path, excludes) -> bool:
    parts = path.parts
    for pat in excludes:
        # glob/suffix match against full path string
        if fnmatch.fnmatch(str(path), pat):
            return True
        if fnmatch.fnmatch(path.name, pat):
            return True
        # match any path component
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def default_dest() -> Path:
    if os.name == "nt":
        return Path("A:\\")
    for cand in (Path("/mnt/backup"), Path("/backup")):
        if cand.exists():
            return cand
    return Path.cwd() / "backup_out"


def make_archive(source: Path, dest_dir: Path, name: str, ts: str, excludes) -> Path:
    stem = f"{name}_full_{ts}"
    # Prefer zip on Windows (native tooling friendly), tar.gz elsewhere.
    if os.name == "nt":
        archive = dest_dir / f"{stem}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in iter_files(source, excludes):
                try:
                    zf.write(f, f.relative_to(source))
                except OSError as e:
                    print(f"  warn: skip {f}: {e}", file=sys.stderr)
    else:
        archive = dest_dir / f"{stem}.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            for f in iter_files(source, excludes):
                try:
                    tf.add(f, arcname=str(f.relative_to(source)), recursive=False)
                except OSError as e:
                    print(f"  warn: skip {f}: {e}", file=sys.stderr)
    return archive


def mirror(source: Path, dest_dir: Path, name: str, ts: str, excludes) -> Path:
    dst = dest_dir / f"{name}_{ts}"
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in iter_files(source, excludes):
        rel = f.relative_to(source)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(f, target)
            copied += 1
        except OSError as e:
            print(f"  warn: skip {f}: {e}", file=sys.stderr)
    print(f"  copied {copied} files")
    return dst


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Universal project backup tool")
    ap.add_argument("--mode", choices=("full", "mirror"), default="full",
                    help="full=archive, mirror=copy tree (default: full)")
    ap.add_argument("--source", type=Path, default=Path(__file__).resolve().parent,
                    help="source directory (default: this script's dir)")
    ap.add_argument("--dest", type=Path, default=None,
                    help="destination directory (default: platform-specific)")
    ap.add_argument("--name", type=str, default=None,
                    help="base name for the backup (default: source dir name)")
    ap.add_argument("--no-default-excludes", action="store_true",
                    help="do not apply the built-in exclusion list")
    ap.add_argument("--exclude", action="append", default=[],
                    help="extra exclusion pattern (repeatable)")
    args = ap.parse_args(argv)

    source = args.source.resolve()
    dest = (args.dest or default_dest()).resolve()
    name = args.name or source.name or "backup"
    ts = timestamp()

    excludes = list(args.exclude)
    if not args.no_default_excludes:
        if args.mode == "full":
            base = FULL_MODE_EXCLUDES
        else:
            base = MIN_MODE_EXCLUDES
        excludes = list(base) + excludes

    if not source.is_dir():
        print(f"Error: source not found: {source}", file=sys.stderr)
        return 1
    if not dest.exists():
        print(f"Error: destination not found: {dest}", file=sys.stderr)
        return 1

    print(f"Source: {source}")
    print(f"Dest  : {dest}")
    print(f"Mode  : {args.mode}")
    print(f"Exclude: {', '.join(excludes) if excludes else '(none)'}")

    if args.mode == "full":
        out = make_archive(source, dest, name, ts, excludes)
        print(f"Backup complete: {out}")
    else:
        out = mirror(source, dest, name, ts, excludes)
        print(f"Backup complete: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
