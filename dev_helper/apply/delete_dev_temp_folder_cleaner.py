from __future__ import annotations

# === SETTINGS START ===
USE_TRASH_BY_DEFAULT = True

PATTERNS_TO_REMOVE = {
    # Python
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".Python",
    "build/",
    "develop-eggs/",
    "dist/",
    "downloads/",
    "eggs/",
    ".eggs/",
    "lib/",
    "lib64/",
    "parts/",
    "sdist/",
    "var/",
    "wheels/",
    "*.egg-info/",
    ".pytest_cache/",
    ".coverage",
    "htmlcov/",
    ".tox/",
    ".venv/",
    "venv/",
    ".env",
    ".mypy_cache/",
    ".dmypy.json",
    "dmypy.json",
    
    # JavaScript / Node.js
    "node_modules/",
    "npm-debug.log",
    "yarn-error.log",
    ".npm/",
    ".next/",
    ".nuxt/",
    "out/",
    ".cache/",
    ".parcel-cache/",
    ".eslintcache",
    ".stylelintcache",
    
    # Rust / Cargo
    "target/",
    "Cargo.lock",
    ".cargo/",
    "*.rlib",
    "*.rmeta",
    
    # Unity
    "Library/",
    "Temp/",
    "Logs/",
    "UserSettings/",
    "*.unitypackage",
    
    # General / IDE
    ".DS_Store",
    "Thumbs.db",
    ".idea/",
    ".vscode/settings.json",
    ".vs/",
    ".gradle/",
    "*.swp",
    "*.swo",
    "*~",
    ".env.local",
    ".env.*.local",
}
# === SETTINGS END ===
"""
Clean development project folders by removing cache, build, and temporary files.
Supports Python, JavaScript, Rust, and Unity projects.
By default sends files to recycle bin  
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

try:
    from path_args import resolve_paths
except ImportError:
    resolve_paths = None


def matches_pattern(name: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return name == pattern.rstrip("/")
    import fnmatch
    return fnmatch.fnmatch(name, pattern)


def delete_item(path: Path) -> bool:
    try:
        if path.is_dir():
            if USE_TRASH_BY_DEFAULT:
                import send2trash
                send2trash.send2trash(str(path))
                print(f"[TRASH] {path}")
            else:
                shutil.rmtree(path)
                print(f"[DEL]  {path}")
        else:
            if USE_TRASH_BY_DEFAULT:
                import send2trash
                send2trash.send2trash(str(path))
                print(f"[TRASH] {path}")
            else:
                os.remove(path)
                print(f"[DEL]  {path}")
        return True
    except Exception as e:
        print(f"[-] Error deleting {path}: {e}", file=sys.stderr)
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Clean development project folders by removing cache, build, and temporary files")
    parser.add_argument("paths", nargs="*", help="Directory paths to clean")
    args = parser.parse_args(argv)

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

    total_removed = 0

    for path in target_paths:
        if not path.is_dir():
            print(f"[-] Skipping {path} (not a directory)", file=sys.stderr)
            continue

        candidates = []
        for root, dirs, files in os.walk(path, topdown=False):
            for d in dirs:
                candidates.append(Path(root) / d)
            for f in files:
                candidates.append(Path(root) / f)
        candidates.append(path)

        for candidate in candidates:
            name = candidate.name
            for pattern in PATTERNS_TO_REMOVE:
                if matches_pattern(name, pattern):
                    if delete_item(candidate):
                        total_removed += 1
                    break

    print(f"\nDone. Removed {total_removed} items.")


if __name__ == "__main__":
    main()
