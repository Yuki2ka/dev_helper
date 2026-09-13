"""
Shared path/argument resolver for small Python command modules.

Resolution order:
    1. Command line arguments: source/destination/path/etc. configured by caller.
    2. Standard input (sys.stdin) — piped/redirected text.
    3. OS clipboard file references: Explorer/Finder/file-manager copied files/dirs.
    4. Hardcoded script constant passed by caller, only if it exists.
    5. Current working directory.

The resolver intentionally does not know whether a command is a reader or writer.
The caller only tells it which argument names to inspect and how selected paths
should be prepared (no directory creation / create as directories / create parents
/ auto).
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Sequence

try:  # Optional dependency; only useful on Windows.
    if platform.system() == "Windows":
        import win32clipboard  # type: ignore
    else:
        win32clipboard = None  # type: ignore
except ImportError:  # pragma: no cover - depends on host OS/packages
    win32clipboard = None  # type: ignore

CreateMode = Literal["none", "dirs", "parents", "auto"]

DEFAULT_ARG_NAMES: tuple[str, ...] = (
    "source",
    "sources",
    "src",
    "input",
    "inputs",
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


@dataclass(frozen=True)
class ResolvedPaths:
    """Result returned by resolve_paths()."""

    paths: tuple[Path, ...]
    origin: Literal["args", "stdin", "clipboard", "constant", "cwd"]

    @property
    def first(self) -> Path:
        return self.paths[0]

    def __iter__(self):
        return iter(self.paths)

    def __len__(self) -> int:
        return len(self.paths)


@dataclass(frozen=True)
class ResolvedText:
    """
    Result returned by resolve_input_text().

    This is for commands that need *text content* (e.g. to write to a file,
    or to process directly), not file paths to read from.
    """

    text: str
    origin: Literal["args", "stdin", "clipboard", "constant"]


def add_common_path_arguments(
    parser: argparse.ArgumentParser,
    *,
    positional: bool = True,
    source: bool = True,
    destination: bool = True,
    positional_name: str = "paths",
) -> argparse.ArgumentParser:
    """
    Add common path arguments to a command parser.

    Use all of these for generic one-location commands, or disable source/dest
    for commands that already have specific arguments.

    Important: overwrite is intentionally NOT added here, because the requested
    policy is a hardcoded per-script constant, usually OVERWRITE_EXISTING = False.
    """

    if positional:
        parser.add_argument(
            positional_name,
            nargs="*",
            help="Path(s). Meaning is command-specific; resolver treats them generically.",
        )
    if source:
        parser.add_argument("-s", "--source", action="append", help="Source path. Can be repeated.")
    if destination:
        parser.add_argument(
            "-d", "--destination", action="append", help="Destination path. Can be repeated."
        )
    return parser


# ---------------------------------------------------------------------------
#  Input text resolution  (args -> stdin -> clipboard -> constant)
# ---------------------------------------------------------------------------

def _read_stdin_if_available() -> str | None:
    """
    Read all of sys.stdin if data is being piped/redirected.

    Returns None when stdin is connected to a terminal (no data), so we can
    fall through to clipboard / constant. Returns None for empty input too.
    """
    if sys.stdin.isatty():
        return None
    try:
        data = sys.stdin.read()
    except OSError:
        return None
    if not data.strip():
        return None
    return data


def resolve_input_text(
    args: argparse.Namespace | object | None = None,
    *,
    arg_names: Sequence[str] = ("text", "content", "data"),
    use_stdin: bool = True,
    use_clipboard: bool = True,
    constant: str | None = None,
) -> ResolvedText:
    """
    Resolve *text content* using the shared priority chain:

        1. CLI args  (e.g. --text, --content)
        2. sys.stdin  (piped/redirected input)
        3. OS clipboard text
        4. Hardcoded constant

    This is separate from resolve_paths(). Use this when your command needs
    the actual text content to process (or to write), not a file-path to read.

    Returns ResolvedText(text=..., origin=...).  If nothing is found, returns
    an empty string with origin="constant" (since the constant default is "").
    """

    # 1. CLI args
    cli_values = _collect_arg_values(args, arg_names)
    if cli_values:
        return ResolvedText(text="\n".join(str(v) for v in cli_values), origin="args")

    # 2. sys.stdin
    if use_stdin:
        stdin_text = _read_stdin_if_available()
        if stdin_text is not None:
            return ResolvedText(text=stdin_text, origin="stdin")

    # 3. clipboard text
    if use_clipboard:
        clip_text = _clipboard_text()
        if clip_text:
            return ResolvedText(text=clip_text, origin="clipboard")

    # 4. hardcoded constant
    if constant:
        return ResolvedText(text=constant, origin="constant")

    return ResolvedText(text="", origin="constant")


# ---------------------------------------------------------------------------
#  Clipboard helpers
# ---------------------------------------------------------------------------

def _clipboard_text() -> str:
    """Read plain text from the OS clipboard (cross-platform fallback)."""
    sys_platform = platform.system()

    if sys_platform == "Windows" and win32clipboard:
        try:
            win32clipboard.OpenClipboard()
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
                return text or ""
            win32clipboard.CloseClipboard()
        except Exception:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    elif sys_platform == "Linux":
        for tool, args_list in (
            ("xclip", ["-selection", "clipboard", "-o"]),
            ("xsel", ["-b", "-o"]),
        ):
            try:
                proc = subprocess.Popen(
                    [tool, *args_list],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, _ = proc.communicate(timeout=2)
                if proc.returncode == 0 and stdout:
                    return stdout.decode("utf-8", errors="replace")
            except Exception:
                continue

    elif sys_platform == "Darwin":
        try:
            proc = subprocess.Popen(
                ["pbpaste"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, _ = proc.communicate(timeout=2)
            if proc.returncode == 0 and stdout:
                return stdout.decode("utf-8", errors="replace")
        except Exception:
            pass

    return ""


def paths_from_clipboard() -> list[str]:
    """
    Extract existing filesystem paths from the OS clipboard.

    Supports:
      - Windows native copied file selection: CF_HDROP.
      - Windows text fallback: Copy as Path / newline-separated paths.
      - Linux file-manager copied files: text/uri-list via xclip.
      - Linux text fallback: xclip/xsel clipboard text.
      - macOS text fallback: pbpaste.

    Returns only paths that currently exist on this machine.
    """

    paths: list[str] = []
    sys_platform = platform.system()

    # --- WINDOWS ---
    if sys_platform == "Windows" and win32clipboard:
        try:
            win32clipboard.OpenClipboard()

            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
                files = win32clipboard.GetClipboardData(win32clipboard.CF_HDROP)
                paths = list(files)

            elif win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                paths = _lines_to_path_strings(text)

        except Exception as exc:  # pragma: no cover - OS-specific
            print(f"[Clipboard Error] Windows subsystem failure: {exc}", file=sys.stderr)
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    # --- LINUX ---
    elif sys_platform == "Linux":
        # Native file-manager copied files usually expose text/uri-list.
        try:
            proc = subprocess.Popen(
                ["xclip", "-selection", "clipboard", "-t", "text/uri-list", "-o"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, _ = proc.communicate(timeout=2)
            if proc.returncode == 0 and stdout:
                for line in stdout.decode("utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("file://"):
                        paths.append(_file_uri_to_path(line))
        except Exception:
            pass

        # Text fallback: copied path text, quoted paths, newline-separated paths.
        if not paths:
            for tool, args_list in (
                ("xclip", ["-selection", "clipboard", "-o"]),
                ("xsel", ["-b", "-o"]),
            ):
                try:
                    proc = subprocess.Popen(
                        [tool, *args_list],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    stdout, _ = proc.communicate(timeout=2)
                    if proc.returncode == 0 and stdout:
                        text = stdout.decode("utf-8", errors="ignore")
                        paths = _lines_to_path_strings(text)
                        break
                except Exception:
                    continue

    # --- MACOS ---
    elif sys_platform == "Darwin":
        try:
            proc = subprocess.Popen(
                ["pbpaste"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, _ = proc.communicate(timeout=2)
            if proc.returncode == 0 and stdout:
                text = stdout.decode("utf-8", errors="ignore")
                paths = _lines_to_path_strings(text)
        except Exception:
            pass

    cleaned_paths: list[str] = []
    for item in paths:
        item = _clean_path_string(item)
        if item.startswith("file://"):
            item = _file_uri_to_path(item)
        if item:
            cleaned_paths.append(item)

    # Strictly keep existing filesystem paths only.
    valid_paths = [
        str(Path(p).expanduser().resolve()) for p in cleaned_paths if Path(p).expanduser().exists()
    ]
    return _dedupe_keep_order(valid_paths)


# ---------------------------------------------------------------------------
#  Path resolution  (args -> stdin -> clipboard paths -> constant -> cwd)
# ---------------------------------------------------------------------------

def _paths_from_stdin_lines() -> list[str] | None:
    """
    Attempt to read newline-separated paths from stdin.

    Returns None when stdin is a terminal (so we skip this step).
    Returns a list of valid existing filesystem paths, or empty list if the
    piped text contained no valid paths.
    """
    if sys.stdin.isatty():
        return None

    try:
        raw = sys.stdin.read()
    except Exception:
        return None

    if not raw or not raw.strip():
        return []

    candidates = _lines_to_path_strings(raw)
    valid = [
        str(Path(p).expanduser().resolve())
        for p in candidates
        if Path(p).expanduser().exists()
    ]
    return _dedupe_keep_order(valid)


def resolve_paths(
    args: argparse.Namespace | object | None = None,
    *,
    arg_names: Sequence[str] = DEFAULT_ARG_NAMES,
    use_stdin: bool = True,
    constant: str | os.PathLike[str] | Iterable[str | os.PathLike[str]] | None = None,
    clipboard: bool = True,
    fallback_to_cwd: bool = True,
    create: CreateMode = "auto",
) -> ResolvedPaths:
    """
    Resolve paths using the shared priority chain.

    Priority:
        args -> stdin paths -> clipboard file references -> existing hardcoded constant -> cwd

    Args:
        args: argparse Namespace or any object with path-like attributes.
        arg_names: Attribute names to inspect on args. This is how the same
            resolver can be used for source, destination, path, output, etc.
        use_stdin: Whether to inspect stdin for newline-separated paths.
        constant: Per-script hardcoded fallback. Empty/non-existing constants are
            ignored and cwd is used instead.
        clipboard: Whether to inspect copied file/dir references.
        fallback_to_cwd: Whether to return cwd if all other sources are empty.
        create: Directory creation policy for the selected result:
            "none"    - create nothing.
            "dirs"    - every selected path is a directory; mkdir -p each.
            "parents" - every selected path is a file-like path; mkdir -p parent.
            "auto"    - existing dirs stay dirs, existing files create parent,
                        missing paths with suffix create parent, missing paths
                        without suffix are treated as dirs.

    The function does not know/care if paths will later be read or written.
    """

    # 1. CLI args
    cli_values = _collect_arg_values(args, arg_names)
    if cli_values:
        paths = tuple(_to_path(v) for v in cli_values)
        _create_missing_directories(paths, create)
        return ResolvedPaths(paths=paths, origin="args")

    # 2. stdin (newline-separated paths)
    if use_stdin:
        stdin_paths = _paths_from_stdin_lines()
        if stdin_paths:
            paths = tuple(_to_path(v) for v in stdin_paths)
            _create_missing_directories(paths, create)
            return ResolvedPaths(paths=paths, origin="stdin")

    # 3. clipboard
    if clipboard:
        clip_values = paths_from_clipboard()
        if clip_values:
            paths = tuple(_to_path(v) for v in clip_values)
            _create_missing_directories(paths, create)
            return ResolvedPaths(paths=paths, origin="clipboard")

    # 4. hardcoded constant (only if it exists)
    const_values = _flatten_paths(constant)
    const_paths = []
    for v in const_values:
        p = _to_path(v)
        if p.exists():
            const_paths.append(p)
    const_paths = tuple(const_paths)
    if const_paths:
        _create_missing_directories(const_paths, create)
        return ResolvedPaths(paths=const_paths, origin="constant")

    # 5. cwd
    if fallback_to_cwd:
        cwd = Path.cwd().resolve()
        _create_missing_directories((cwd,), create)
        return ResolvedPaths(paths=(cwd,), origin="cwd")

    raise ValueError("No path found in args, stdin, clipboard, constant, or cwd fallback.")


# ---------------------------------------------------------------------------
#  Output helpers
# ---------------------------------------------------------------------------

def choose_output_file(
    location: str | os.PathLike[str] | ResolvedPaths,
    *,
    default_stem: str,
    default_suffix: str,
    overwrite: bool = False,
) -> Path:
    """
    Convert a resolved location into a concrete output file path.

    Rules:
      - Existing directory => directory/default_stem.default_suffix
      - Missing path with no suffix => mkdir as directory, then default filename
      - Path with suffix => exact file path
      - overwrite=False => do not replace; append _001, _002, ... if needed
    """

    if isinstance(location, ResolvedPaths):
        path = location.first
    else:
        path = _to_path(location)

    suffix = _normalize_suffix(default_suffix)

    if path.exists():
        if path.is_dir():
            candidate = path / f"{default_stem}{suffix}"
        else:
            candidate = path
    elif path.suffix:
        candidate = path
    else:
        path.mkdir(parents=True, exist_ok=True)
        candidate = path / f"{default_stem}{suffix}"

    candidate.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        return candidate
    return next_available_path(candidate)


def next_available_path(path: str | os.PathLike[str]) -> Path:
    """Return path if free, otherwise path with _001, _002, ... before suffix."""

    candidate = _to_path(path)
    if not candidate.exists():
        return candidate

    parent = candidate.parent
    stem = candidate.stem
    suffix = candidate.suffix
    i = 1
    while True:
        numbered = parent / f"{stem}_{i:03d}{suffix}"
        if not numbered.exists():
            return numbered
        i += 1


def iter_existing_files(
    paths: Iterable[str | os.PathLike[str]],
    *,
    recursive: bool = True,
    include_hidden: bool = False,
) -> Iterable[Path]:
    """
    Generic helper for reader-like commands: expand files and directories.

    This is separate from resolution. The resolver returns locations; this helper
    decides how to enumerate readable files from those locations.
    """

    for raw in paths:
        path = _to_path(raw)
        if path.is_file():
            if include_hidden or not _is_hidden(path):
                yield path
        elif path.is_dir():
            iterator = path.rglob("*") if recursive else path.iterdir()
            for item in iterator:
                if item.is_file() and (include_hidden or not _is_hidden(item)):
                    yield item


# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------

def _collect_arg_values(
    args: argparse.Namespace | object | None, arg_names: Sequence[str]
) -> list[str | os.PathLike[str]]:
    if args is None:
        return []

    values: list[str | os.PathLike[str]] = []
    for name in arg_names:
        if not hasattr(args, name):
            continue
        raw = getattr(args, name)
        values.extend(_flatten_paths(raw))
    return [v for v in values if str(v).strip()]


def _flatten_paths(value) -> list[str | os.PathLike[str]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, os.PathLike)):
        text = os.fsdecode(value).strip()
        return [text] if text else []
    try:
        out: list[str | os.PathLike[str]] = []
        for item in value:
            out.extend(_flatten_paths(item))
        return out
    except TypeError:
        text = str(value).strip()
        return [text] if text else []


def _to_path(value: str | os.PathLike[str]) -> Path:
    text = _clean_path_string(os.fsdecode(value))
    if text.startswith("file://"):
        text = _file_uri_to_path(text)
    text = os.path.expandvars(text)
    return Path(text).expanduser().resolve(strict=False)


def _create_missing_directories(paths: Iterable[Path], create: CreateMode) -> None:
    if create == "none":
        return

    for path in paths:
        if create == "dirs":
            path.mkdir(parents=True, exist_ok=True)
        elif create == "parents":
            path.parent.mkdir(parents=True, exist_ok=True)
        elif create == "auto":
            if path.exists():
                directory = path if path.is_dir() else path.parent
            else:
                directory = path.parent if path.suffix else path
            directory.mkdir(parents=True, exist_ok=True)
        else:
            raise ValueError(f"Unknown create mode: {create!r}")


def _file_uri_to_path(uri: str) -> str:
    parsed = urllib.parse.urlparse(uri)
    path = urllib.parse.unquote(parsed.path)

    # file://server/share/path on Windows is a UNC path.
    if platform.system() == "Windows":
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        # file:///C:/Users/... parses as /C:/Users/...
        if re.match(r"^/[A-Za-z]:/", path):
            path = path[1:]

    return path


def _lines_to_path_strings(text: str | None) -> list[str]:
    if not text:
        return []
    return [cleaned for line in text.splitlines() if (cleaned := _clean_path_string(line))]


def _clean_path_string(value: str) -> str:
    return value.strip().strip('"\'')


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = os.path.normcase(os.path.normpath(value))
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _normalize_suffix(suffix: str) -> str:
    if not suffix:
        return ""
    return suffix if suffix.startswith(".") else f".{suffix}"


def _is_hidden(path: Path) -> bool:
    return any(
        part.startswith(".") for part in path.parts if part not in (path.anchor, ".", "..")
    )
