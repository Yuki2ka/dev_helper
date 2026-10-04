from __future__ import annotations

# === SETTINGS START ===
USE_TRASH_BY_DEFAULT = True      # Send deleted files/folders to Trash/Recycle Bin
CONFIRM = "ask"                  # "ask" | 1 / "y" / True (auto-confirm) | any other (abort)
USE_GIT_AND_HG_IGNORE = True     # Enable both .gitignore and .hgignore filtering
ADDITIONAL_IGNORE_PATTERNS: list[str] = []  # Extra patterns to treat as ignored (e.g. "*.tmp")
PATH = None                      # Path resolution: argparse -> PATH -> CWD
# === SETTINGS END ===
"""
Clean folders by removing files and directories matching .gitignore and .hgignore patterns.

Features:
- Reads .gitignore and .hgignore rules from the target folder(s), subfolders, and repository roots.
- Reuses git and hg ignore parsers.
- Preview matching files/folders before deletion.
- Interactive confirmation prompt (default = ask; 1 or y = yes, other key = no).
- By default sends files to recycle bin / trash (configurable via USE_TRASH_BY_DEFAULT or CLI).
- Compatible with path_args for CLI, clipboard, stdin, and CWD resolution.
"""

import argparse
import fnmatch
import os
import re
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

try:
    import pathspec
except ImportError:
    pathspec = None  # type: ignore[assignment]

try:
    from path_args import resolve_paths
except ImportError:
    resolve_paths = None  # type: ignore[assignment]

try:
    from dev_helper.to_clipboard.to_clipboard import (
        is_hg_ignored as _tc_is_hg_ignored,
    )
    from dev_helper.to_clipboard.to_clipboard import (
        load_hgignore_spec as _tc_load_hgignore_spec,
    )
except ImportError:
    _tc_load_hgignore_spec = None  # type: ignore[assignment]
    _tc_is_hg_ignored = None  # type: ignore[assignment]


PROTECTED_NAMES = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".gitignore",
    ".hgignore",
})

_gitignore_cache: dict[tuple[str, tuple[str, ...]], pathspec.PathSpec | None] = {}
_hgignore_cache: dict[str, list[re.Pattern[str]]] = {}


def is_protected(path: Path | str) -> bool:
    """Return True if path is or contains a protected version-control / ignore metadata name."""
    p = Path(path)
    return any(part in PROTECTED_NAMES for part in p.parts)


def parse_gitignore_lines(lines: Sequence[str]) -> pathspec.PathSpec | None:
    """Compile lines of gitignore patterns into a PathSpec."""
    if not lines or pathspec is None:
        return None
    try:
        return pathspec.PathSpec.from_lines("gitignore", lines)
    except (TypeError, ValueError, AttributeError):
        try:
            return pathspec.PathSpec.from_lines(
                pathspec.patterns.GitWildMatchPattern, lines
            )
        except (TypeError, ValueError, re.error) as e:
            print(f"Warning: Could not compile gitignore patterns: {e}", file=sys.stderr)
            return None


def load_gitignore_spec(
    root_dir: str | Path,
    additional_patterns: Sequence[str] | None = None,
) -> pathspec.PathSpec | None:
    """
    Looks for a .gitignore file in root_dir.
    Combines file content with additional_patterns.
    Returns a PathSpec object if patterns exist, or None.
    """
    root_str = str(Path(root_dir).resolve())
    extra_tuple = tuple(additional_patterns or ())
    cache_key = (root_str, extra_tuple)
    if cache_key in _gitignore_cache:
        return _gitignore_cache[cache_key]

    lines: list[str] = []
    if USE_GIT_AND_HG_IGNORE:
        gitignore_path = Path(root_str) / ".gitignore"
        if gitignore_path.is_file():
            try:
                with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except OSError as e:
                print(f"Warning: Could not read .gitignore in {root_str}: {e}", file=sys.stderr)

    extra = additional_patterns if additional_patterns is not None else ADDITIONAL_IGNORE_PATTERNS
    if extra:
        lines.extend(p.rstrip("\n") + "\n" for p in extra)

    spec = parse_gitignore_lines(lines) if lines else None
    _gitignore_cache[cache_key] = spec
    return spec


def load_hgignore_spec(root_dir: str | Path) -> list[re.Pattern[str]]:
    """
    Looks for a .hgignore file in root_dir.
    Parses and returns a list of compiled regex matchers.
    Reuses to_clipboard implementation when available.
    """
    root_str = str(Path(root_dir).resolve())
    if root_str in _hgignore_cache:
        return _hgignore_cache[root_str]

    if not USE_GIT_AND_HG_IGNORE:
        _hgignore_cache[root_str] = []
        return []

    if _tc_load_hgignore_spec is not None:
        try:
            matchers = _tc_load_hgignore_spec(root_str)
            if matchers is not None:
                _hgignore_cache[root_str] = matchers
                return matchers
        except (TypeError, ValueError, OSError):
            pass

    hgignore_path = Path(root_str) / ".hgignore"
    if not hgignore_path.is_file():
        _hgignore_cache[root_str] = []
        return []

    matchers = []
    current_syntax = "regexp"
    try:
        with open(hgignore_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("syntax:"):
                    syntax_type = line.split(":", 1)[1].strip()
                    if syntax_type in ["regexp", "glob"]:
                        current_syntax = syntax_type
                    continue
                try:
                    if current_syntax == "regexp":
                        matchers.append(re.compile(line))
                    elif current_syntax == "glob":
                        matchers.append(re.compile(fnmatch.translate(line)))
                except re.error as pattern_err:
                    print(
                        f"Warning: Skipping invalid pattern '{line}' in .hgignore: {pattern_err}",
                        file=sys.stderr,
                    )
        _hgignore_cache[root_str] = matchers
        return matchers
    except OSError as e:
        print(f"Warning: Could not read .hgignore in {root_str}: {e}", file=sys.stderr)

    _hgignore_cache[root_str] = []
    return []


def is_hg_ignored(rel_path: str, hg_matchers: Sequence[re.Pattern[str]]) -> bool:
    """Checks if a relative path matches any compiled .hgignore regex rules."""
    if not hg_matchers:
        return False
    if _tc_is_hg_ignored is not None:
        try:
            return bool(_tc_is_hg_ignored(rel_path, hg_matchers))
        except (TypeError, ValueError, AttributeError, re.error):
            pass
    normalized = rel_path.replace("\\", "/")
    parts = normalized.split("/")
    for i in range(len(parts)):
        prefix = "/".join(parts[: i + 1])
        for matcher in hg_matchers:
            if matcher.search(prefix):
                return True
    return False


def find_ignore_files_in_tree(target_dir: Path) -> dict[str, list[Path]]:
    """
    Find all .gitignore and .hgignore files in target_dir, subdirectories,
    and parent directories up to repo/filesystem root.
    """
    found: dict[str, list[Path]] = {"gitignore": [], "hgignore": []}

    # 1. Check target directory and parent directories up to repo boundary or filesystem root
    curr = target_dir.resolve()
    if curr.is_file():
        curr = curr.parent

    while True:
        gi = curr / ".gitignore"
        if gi.is_file() and gi not in found["gitignore"]:
            found["gitignore"].append(gi)
        hg = curr / ".hgignore"
        if hg.is_file() and hg not in found["hgignore"]:
            found["hgignore"].append(hg)

        if (curr / ".git").exists() or (curr / ".hg").exists():
            break
        if curr.parent == curr:
            break
        curr = curr.parent

    # 2. Check subdirectories inside target_dir
    if target_dir.is_dir():
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in PROTECTED_NAMES]
            root_p = Path(root)
            if ".gitignore" in files:
                gi = root_p / ".gitignore"
                if gi not in found["gitignore"]:
                    found["gitignore"].append(gi)
            if ".hgignore" in files:
                hg = root_p / ".hgignore"
                if hg not in found["hgignore"]:
                    found["hgignore"].append(hg)

    return found


def collect_ignored_items(
    target_path: Path,
    additional_patterns: Sequence[str] | None = None,
) -> tuple[list[Path], bool]:
    """
    Collect all files and directories inside target_path that match ignore rules.

    Returns:
        (candidates, has_ignore_files)
        candidates: List of Paths (files or directories) to delete.
        has_ignore_files: True if at least one .gitignore/.hgignore file or additional pattern exists.
    """
    target = target_path.resolve()
    if not target.exists():
        print(f"[-] Path does not exist: {target_path}", file=sys.stderr)
        return [], False

    if is_protected(target):
        print(f"[-] Cannot clean protected path: {target_path}", file=sys.stderr)
        return [], False

    ignore_files = find_ignore_files_in_tree(target)
    has_gi = bool(ignore_files["gitignore"])
    has_hg = bool(ignore_files["hgignore"])
    extra = additional_patterns if additional_patterns is not None else ADDITIONAL_IGNORE_PATTERNS
    has_extra = bool(extra)

    has_ignore = (USE_GIT_AND_HG_IGNORE and (has_gi or has_hg)) or has_extra
    if not has_ignore:
        return [], False

    git_specs: list[tuple[Path, pathspec.PathSpec]] = []
    if USE_GIT_AND_HG_IGNORE:
        for gi_path in ignore_files["gitignore"]:
            spec = load_gitignore_spec(
                gi_path.parent,
                additional_patterns=extra if gi_path.parent == target else None,
            )
            if spec:
                git_specs.append((gi_path.parent, spec))

    if has_extra and not any(p == target for p, _ in git_specs):
        spec = parse_gitignore_lines(extra)
        if spec:
            git_specs.append((target, spec))

    hg_specs: list[tuple[Path, list[re.Pattern[str]]]] = []
    if USE_GIT_AND_HG_IGNORE:
        for hg_path in ignore_files["hgignore"]:
            matchers = load_hgignore_spec(hg_path.parent)
            if matchers:
                hg_specs.append((hg_path.parent, matchers))

    def _matches_any(item_path: Path) -> bool:
        if is_protected(item_path):
            return False
        is_dir = item_path.is_dir()
        for dir_path, spec in git_specs:
            try:
                rel = item_path.relative_to(dir_path).as_posix()
            except ValueError:
                continue
            if is_dir:
                if spec.match_file(rel) or spec.match_file(rel + "/"):
                    return True
            else:
                if spec.match_file(rel):
                    return True

        for dir_path, matchers in hg_specs:
            try:
                rel = item_path.relative_to(dir_path).as_posix()
            except ValueError:
                continue
            if is_dir:
                if is_hg_ignored(rel, matchers) or is_hg_ignored(rel + "/", matchers):
                    return True
            else:
                if is_hg_ignored(rel, matchers):
                    return True

        return False

    candidates: list[Path] = []

    if target.is_file():
        if _matches_any(target):
            candidates.append(target)
        return candidates, True

    # Target is a directory: traverse topdown
    for root, dirs, files in os.walk(target, topdown=True):
        dirs[:] = [d for d in dirs if d not in PROTECTED_NAMES]
        root_path = Path(root)

        for d in list(dirs):
            dir_candidate = root_path / d
            if _matches_any(dir_candidate):
                candidates.append(dir_candidate)
                dirs.remove(d)

        for f in files:
            if f in PROTECTED_NAMES:
                continue
            file_candidate = root_path / f
            if _matches_any(file_candidate):
                candidates.append(file_candidate)

    return candidates, True


def delete_item(path: Path, use_trash: bool = USE_TRASH_BY_DEFAULT) -> bool:
    """Delete a single file or directory via trash or permanent removal."""
    try:
        if path.is_dir():
            if use_trash:
                import send2trash
                send2trash.send2trash(str(path))
                print(f"[TRASH] {path}")
            else:
                shutil.rmtree(path)
                print(f"[DEL]   {path}")
        else:
            if use_trash:
                import send2trash
                send2trash.send2trash(str(path))
                print(f"[TRASH] {path}")
            else:
                os.remove(path)
                print(f"[DEL]   {path}")
        return True
    except (OSError, RuntimeError) as e:
        print(f"[-] Error deleting {path}: {e}", file=sys.stderr)
        return False


def ask_confirmation(
    prompt_text: str = "Proceed with deletion? [1/y = Yes, other key = No] ",
) -> bool:
    """Prompt the user for interactive confirmation. 1 or y = Yes, any other key = No."""
    try:
        response = input(prompt_text).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return response in ("1", "y", "yes")


def clean_paths(
    paths: Sequence[Path | str],
    *,
    confirm: str | bool = CONFIRM,
    no_confirm: bool = False,
    dry_run: bool = False,
    use_trash: bool | None = None,
    additional_patterns: Sequence[str] | None = None,
) -> int:
    """
    Clean the given paths according to .gitignore/.hgignore rules.

    Returns:
        Number of items successfully removed (or matched in dry-run mode).
    """
    effective_trash = USE_TRASH_BY_DEFAULT if use_trash is None else use_trash
    total_removed = 0

    for raw_path in paths:
        path = Path(raw_path) if isinstance(raw_path, (str, os.PathLike)) else raw_path
        candidates, has_ignore = collect_ignored_items(
            path, additional_patterns=additional_patterns
        )

        if not has_ignore:
            print(f"No .gitignore or .hgignore files found in {path}")
            continue

        if not candidates:
            print(f"No matching ignored files found in {path}")
            continue

        print(f"\nFound {len(candidates)} item(s) to delete in {path}:")
        for item in candidates:
            if item.is_dir():
                print(f"  [dir]  {item}")
            else:
                print(f"  [file] {item}")

        if dry_run:
            print(f"\nDry run mode: {len(candidates)} item(s) would be deleted.")
            total_removed += len(candidates)
            continue

        should_delete = False
        if no_confirm or confirm in (True, 1, "1", "y", "yes", "true", "True"):
            should_delete = True
        elif confirm in (False, 0, "0", "n", "no", "false", "False"):
            print("Aborted.")
            continue
        elif str(confirm).lower() == "ask":
            if ask_confirmation("Proceed with deletion? [1/y = Yes, other key = No] "):
                should_delete = True
            else:
                print("Aborted.")
                continue
        else:
            print("Aborted.")
            continue

        if should_delete:
            removed_for_path = 0
            for item in candidates:
                if delete_item(item, use_trash=effective_trash):
                    removed_for_path += 1
            total_removed += removed_for_path

    print(f"\nDone. Removed {total_removed} item(s).")
    return total_removed


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Clean folders by removing files and directories matching .gitignore and .hgignore"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Directory or file paths to clean (default: current working directory or clipboard)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        "--no-confirm",
        dest="no_confirm",
        action="store_true",
        help="Skip confirmation prompt and proceed with deletion",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview matching files/directories without deleting them",
    )
    parser.add_argument(
        "--trash",
        dest="use_trash",
        action="store_const",
        const=True,
        default=None,
        help="Send deleted files/directories to trash / recycle bin",
    )
    parser.add_argument(
        "--permanent",
        "--no-trash",
        dest="use_trash",
        action="store_const",
        const=False,
        help="Permanently delete files/directories instead of sending to trash",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if resolve_paths is not None:
        resolved = resolve_paths(
            args,
            arg_names=("paths",),
            constant=PATH,
            clipboard=True,
            fallback_to_cwd=True,
            create="none",
        )
        target_paths = list(resolved)
    else:
        if args.paths:
            target_paths = [Path(p) if isinstance(p, (str, os.PathLike)) else p for p in args.paths]
        else:
            target_paths = [Path(PATH) if PATH else Path.cwd()]

    clean_paths(
        target_paths,
        confirm=CONFIRM,
        no_confirm=args.no_confirm,
        dry_run=args.dry_run,
        use_trash=args.use_trash,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
