from __future__ import annotations

# === SETTINGS START ===
CONTENT_MARK_FORMAT = "markdown"  # "default" | "markdown"

CONVERT_TO_LF = True              # Convert all line endings (CRLF, CR) to Unix LF (\n)
TRIM_TRAIL_SPACES = True          # remove space and tabs in the end of lines
CONVERT_TO_TABS = True            # Automatically convert spaces to 1 tab indentation

SKIP_BINARY_FILES_PATH_TREE = False
USE_GIT_AND_HG_IGNORE = True      # Enable both .gitignore and .hgignore filtering

SKIP_DUPLICATE_CONTENT_FILES = False  # keep dupes in ascii-tree but emit content only once (skip extra copies)

ASCII_TREE_SHOW = True
ASCII_TREE_SHOW_IGNORED = True
ASCII_TREE_SHOW_SIZE_THRESHOLD = 0.1  # None - do not show |  10 means show if file > 10MB

SHOW_ROOT_IN_TREE = False         # omit root folder name so the tree is relative & invertible

PERF_LOG_ENABLED = True           # Master switch for performance logging
PERF_LOG_THRESHOLD_MS = 50        # Only log operations slower than this (ms)
PERF_LOG_TO_FILE = True          # Also write perf log to disk (perf_log.jsonl)
CANCEL_TIMEOUT_MS = 300           # Show cancel button after this many ms
# === SETTINGS END ===

"""
2-way explorer.

A GUI that bridges a filesystem selection and a single text representation:

  * "To Text"  : takes the checkboxed files/folders and produces the same kind of
                 concatenated text that `to_clipboard.py` produces (ascii tree + content
                 blocks in the `======` format).
  * "To Files" : parses that text and recreates the exact file structure it came from.

The two operations are inverses:

    text  = to_text(selected_files)
    files = to_files(text, out_dir)         # recreates the same file structure
    text2 = to_text(files)                  # equals `text` byte for byte
    files2 = to_files(text2, out_dir2)      # equals `files`

All of the actual work is delegated to the existing helpers so there is a single
source of truth:

  * `dev_helper.to_clipboard.to_clipboard`
        - `_read_file_content`  reads + normalizes + formats one file block
        - `_format_block`       formats a (merged) content block
        - `_deduplicate_chunks` merges identical files
        - `generate_ascii_tree` renders the tree
        - `copy_to_clipboard`   pushes the result to the clipboard
  * `dev_helper.new_file.new_file_from_clipboard_paste`
        - `extract_code_blocks_with_names` parses the text back into (path, content)
        - `save_text_file`                 writes each file back to disk

This property is verified by `self_test()` (run `python 2way_explorer.py --selftest`).
"""

import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback
from collections import defaultdict, deque
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

import dev_helper.common.text_utils as text_utils  # noqa: E402

import dev_helper.to_clipboard.to_clipboard as tc  # noqa: E402
from dev_helper.common.signature_extractor import extract_signatures  # noqa: E402

# `dev_helper.new_file.__init__` rebinds the attribute `new_file_from_clipboard_paste` to
# `main`, so stickytape's rewrite of `import ... as nf` resolves to the *function*, not
# the submodule. A plain static import lets stickytape bundle the submodule; we then grab
# the real submodule object from sys.modules, which always holds it despite the shadowing.
import dev_helper.new_file.new_file_from_clipboard_paste
nf = sys.modules["dev_helper.new_file.new_file_from_clipboard_paste"]

MODES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "explorer_modes.json")
PERF_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "perf_log.jsonl")


# =============================================================================
#  PERFORMANCE LOGGING
# =============================================================================

class PerfCollector:
    """Collects timing data for operations. Singleton per process."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    # Ring buffer: caps memory growth across long sessions; the
                    # lock also guards snapshots (deque iteration is not safe
                    # during concurrent appends -- it raises RuntimeError).
                    cls._instance._records = deque(maxlen=10_000)
                    cls._instance._records_lock = threading.Lock()
                    cls._instance._file_lock = threading.Lock()
                    cls._instance._active_timers = {}
        return cls._instance

    def start(self, name: str) -> float:
        """Begin timing an operation. Returns the start time."""
        t = time.perf_counter()
        self._active_timers[name] = t
        return t

    def stop(self, name: str) -> float | None:
        """End timing and record. Returns elapsed seconds, or None if not started."""
        t = time.perf_counter()
        start = self._active_timers.pop(name, None)
        if start is None:
            return None
        elapsed = t - start
        elapsed_ms = elapsed * 1000
        with self._records_lock:
            self._records.append((name, elapsed_ms, time.time()))
        if PERF_LOG_ENABLED and elapsed_ms >= PERF_LOG_THRESHOLD_MS:
            self._emit(name, elapsed_ms)
        if PERF_LOG_TO_FILE:
            self._write_to_file(name, elapsed_ms)
        return elapsed

    def _emit(self, name: str, elapsed_ms: float):
        """Print a performance warning to stderr."""
        print(f"[perf] {name}: {elapsed_ms:.1f}ms", file=sys.stderr)

    def _write_to_file(self, name: str, elapsed_ms: float):
        # Background threads call stop() concurrently; the lock keeps whole
        # JSONL lines from interleaving/corrupting on disk.
        with self._file_lock:
            try:
                with open(PERF_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "op": name,
                        "elapsed_ms": round(elapsed_ms, 3),
                        "timestamp": time.time(),
                    }) + "\n")
            except OSError:
                pass

    def get_summary(self) -> dict[str, list[float]]:
        """Return aggregated timing data grouped by operation name."""
        # Snapshot under the lock: a deque being iterated while a background
        # thread appends would raise RuntimeError.
        with self._records_lock:
            records = list(self._records)
        groups = defaultdict(list)
        for name, elapsed_ms, _ts in records:
            groups[name].append(elapsed_ms)
        return dict(groups)

    def reset(self):
        """Clear all collected records."""
        with self._records_lock:
            self._records.clear()
            self._active_timers.clear()


perf = PerfCollector()


# =============================================================================
#  CANCELLATION
# =============================================================================

class CancelledError(Exception):
    """Raised when a cancellable operation is interrupted."""
    pass


class CancellationToken:
    """Thread-safe primitive for cooperative cancellation."""

    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self):
        """Signal cancellation."""
        with self._lock:
            self._event.set()

    def reset(self):
        """Clear the cancellation signal for reuse."""
        with self._lock:
            self._event.clear()

    def check(self):
        """Raise CancelledError if cancelled (for use in tight loops)."""
        if self._event.is_set():
            raise CancelledError("Operation was cancelled")


# =============================================================================
#  FILE CONTENT CACHE
# =============================================================================

class _FileCache:
    """mtime-based cache for file contents and derived data."""

    def __init__(self):
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, path: str) -> object | None:
        key = os.path.normcase(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] == mtime:
                return cached[1]
        return None

    def put(self, path: str, value: object):
        key = os.path.normcase(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        with self._lock:
            self._cache[key] = (mtime, value)

    def clear(self):
        with self._lock:
            self._cache.clear()


_chunk_cache = _FileCache()
_sig_cache = _FileCache()


def _get_cached_chunk(fp: str, rel: str) -> str | None:
    key = os.path.normcase(fp) + "|" + rel.replace("\\", "/")
    cached = _chunk_cache.get(key)
    if cached is not None:
        return cached
    result = tc._read_file_content(fp, rel)
    if result is not None:
        _chunk_cache.put(key, result)
    return result


def _get_cached_sig(fp: str) -> str | None:
    cached = _sig_cache.get(fp)
    if cached is not None:
        return cached
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return None
    ext = os.path.splitext(fp)[1].lstrip(".").lower()
    sig_text, _ = extract_signatures(content, ext)
    if sig_text:
        _sig_cache.put(fp, sig_text)
    return sig_text


# =============================================================================
#  ACTION MANAGER (optimized)
# =============================================================================

class Action:
    OPEN = "open"
    SIGNATURE = "signature"
    MIXED = "..."


def _atomic_write_json(path: str, payload: dict) -> None:
    """Write JSON atomically: dump to a sibling .tmp file, then os.replace().

    Prevents a half-written config if the process crashes mid-write.
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent="\t", ensure_ascii=False)
    os.replace(tmp_path, path)


class ActionManager:
    """Persists the per-path mode ("", open, signature).

    On disk the data is stored *compactly*: when every child of a folder shares
    the same mode, only the folder is written instead of each individual file
    (see `_compact`). The compact form is expanded back into concrete per-file
    entries on load (see `_expand`) so the rest of the app keeps working with
    real file paths.
    """

    _SAVE_DEBOUNCE_MS = 500

    def __init__(self):
        self.data = self.load()
        self._dirty = False
        self._save_timer_id = None
        self._tk_root = None
        self._data_lock = threading.Lock()
        self._save_thread: threading.Thread | None = None
        self._save_generation = 0

    def _bind_tk(self, root):
        """Give the manager a reference to the Tk root for debounced save."""
        self._tk_root = root

    def load(self):
        perf.start("action_mgr.load")
        if os.path.exists(MODES_PATH):
            try:
                with open(MODES_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, OSError):
                perf.stop("action_mgr.load")
                return {}
            result = self._expand(raw)
            perf.stop("action_mgr.load")
            return result
        perf.stop("action_mgr.load")
        return {}

    def _schedule_save(self):
        """Debounced save: only flush to disk after a quiet period."""
        self._dirty = True
        self._save_generation += 1
        if self._tk_root is None:
            self._flush_save()
            return
        if self._save_timer_id is not None:
            try:
                self._tk_root.after_cancel(self._save_timer_id)
            except Exception:
                pass
        self._save_timer_id = self._tk_root.after(
            self._SAVE_DEBOUNCE_MS, self._flush_save
        )

    def _flush_save(self):
        """Actually write to disk."""
        if not self._dirty:
            return
        self._save_timer_id = None
        generation = self._save_generation

        def work():
            perf.start("ActionManager.save")
            try:
                with self._data_lock:
                    snapshot = dict(self.data)
                compacted = self._compact(snapshot)
                expanded = {k.replace("\\", "/"): v for k, v in compacted.items()}
                if not expanded:
                    with self._data_lock:
                        if generation == self._save_generation:
                            self._dirty = False
                    return
                _atomic_write_json(MODES_PATH, expanded)
            except OSError:
                pass
            finally:
                perf.stop("ActionManager.save")
                with self._data_lock:
                    if generation == self._save_generation:
                        self._dirty = False

        self._save_thread = threading.Thread(target=work, daemon=True)
        self._save_thread.start()

    def flush(self):
        """Force immediate write to disk (call before app exit)."""
        if self._save_timer_id is not None:
            try:
                self._tk_root.after_cancel(self._save_timer_id)
            except Exception:
                pass
            self._save_timer_id = None
        if self._save_thread and self._save_thread.is_alive():
            self._save_thread.join(timeout=5)
        self._dirty = True
        perf.start("ActionManager.save")
        try:
            with self._data_lock:
                snapshot = dict(self.data)
            compacted = self._compact(snapshot)
            expanded = {k.replace("\\", "/"): v for k, v in compacted.items()}
            if expanded:
                _atomic_write_json(MODES_PATH, expanded)
        except OSError:
            pass
        finally:
            perf.stop("ActionManager.save")
            self._dirty = False

    # ---- compact <-> expanded conversion -------------------------------
    @staticmethod
    def _expand(raw):
        """Expand the compact on-disk form into concrete per-path entries.

        A folder entry means "every descendant has this mode", so it is walked
        and applied to each subfolder and file. Stale paths (deleted on disk)
        are silently dropped.
        """
        data = {}
        for path, mode in raw.items():
            if not mode:
                continue
            ap = os.path.abspath(path)
            if os.path.isdir(ap):
                data[ap] = mode
                for dirpath, _dirnames, filenames in os.walk(ap):
                    data[os.path.abspath(dirpath)] = mode
                    for fn in filenames:
                        data[os.path.abspath(os.path.join(dirpath, fn))] = mode
            elif os.path.isfile(ap):
                data[ap] = mode
        return data

    @staticmethod
    def _folder_uniform_mode(folder, children):
        """Return the shared mode if *every* on-disk child of `folder` maps to it.

        `children` maps the folder's direct child paths (the ones currently in
        the working set) to their modes. A collapsed subfolder appears under its
        own path; a child missing from the map (unmarked file, or a subfolder
        that could not itself be collapsed) means the folder is not uniform and
        cannot be written as one entry. Returns "" for mixed modes and None when
        coverage cannot be confirmed.
        """
        try:
            entries = os.listdir(folder)
        except OSError:
            return None
        if not entries:
            return None
        modes = set()
        for name in entries:
            p = os.path.normpath(os.path.join(folder, name))
            if p not in children:
                return None
            modes.add(children[p])
            if len(modes) > 1:
                return ""
        return modes.pop() if len(modes) == 1 else ""

    @classmethod
    def _compact(cls, data):
        """Collapse fully-covered folders so only the folder is written to disk.

        Runs in a single bottom-up pass. A parent->children map is built once
        from the working set (instead of rebuilding the parent list on every
        iteration and re-scanning every key per collapse), and folders are
        processed deepest-first so each collapse propagates upward immediately.
        """
        result = {}
        children_of = defaultdict(dict)  # folder -> {direct child path: mode} in result
        for p, m in data.items():
            if not (m and os.path.isfile(p)):
                continue
            p = os.path.normpath(p)
            result[p] = m
            parent = os.path.dirname(p)
            if parent:
                children_of[parent][p] = m
        if len(result) <= 1:
            return result

        # The only folders that can ever be uniform are ancestors of covered files.
        candidates = set()
        for parent in children_of:
            d = parent
            while d and d not in candidates:
                candidates.add(d)
                upper = os.path.dirname(d)
                if upper == d:
                    break
                d = upper

        # Deepest first: every descendant path is strictly longer than its
        # ancestors, so children always collapse before the parents that
        # contain them are examined.
        for folder in sorted(candidates, key=len, reverse=True):
            children = children_of.get(folder)
            if not children:
                continue
            mode = cls._folder_uniform_mode(folder, children)
            if not mode:
                continue
            # Uniformity guarantees every entry under `folder` is a direct child
            # (a deeper straggler would have left an uncovered on-disk child),
            # so dropping the mapped children removes the whole subtree with no
            # full-map scan.
            for child in children:
                del result[child]
            children.clear()
            result[folder] = mode
            parent = os.path.dirname(folder)
            if parent:
                children_of.setdefault(parent, {})[folder] = mode
        return result

    def get(self, path):
        key = os.path.abspath(path)
        with self._data_lock:
            return self.data.get(key)

    def set(self, path, action):
        key = os.path.abspath(path)
        with self._data_lock:
            if action == "" or action is None:
                self.data.pop(key, None)
            else:
                self.data[key] = action
        self._schedule_save()

    def set_many(self, paths, action):
        with self._data_lock:
            for p in paths:
                key = os.path.abspath(p)
                if action == "" or action is None:
                    self.data.pop(key, None)
                else:
                    self.data[key] = action
        self._schedule_save()

    def clear(self):
        """Remove all modes (thread-safe)."""
        with self._data_lock:
            self.data.clear()
        self._schedule_save()

    def snapshot_selected(self, root_dir):
        """Return a list of selected files under root_dir at this moment.

        Takes the data lock and returns a *copy*, so background threads can
        iterate it without racing the main thread's updates.
        """
        if not root_dir:
            return []
        root = os.path.normcase(os.path.normpath(root_dir))
        prefix = root + os.sep
        with self._data_lock:
            return sorted(
                p for p, mode in self.data.items()
                if mode
                and os.path.isfile(p)
                and (lambda q: q == root or q.startswith(prefix))(
                    os.path.normcase(os.path.normpath(p))
                )
            )


def apply_settings() -> None:
    """Push the module-level SETTINGS into the reused modules (single source of truth).

    Settings that affect text generation / tree rendering live on `tc`
    (to_clipboard); settings that affect writing back to disk live on `nf`
    (new_file_from_clipboard_paste).
    """
    tc.CONTENT_MARK_FORMAT = CONTENT_MARK_FORMAT
    tc.CONVERT_TO_LF = CONVERT_TO_LF
    tc.CONVERT_TO_TABS = CONVERT_TO_TABS
    tc.SKIP_BINARY_FILES_PATH_TREE = SKIP_BINARY_FILES_PATH_TREE
    tc.USE_GIT_AND_HG_IGNORE = USE_GIT_AND_HG_IGNORE
    tc.ASCII_TREE_SHOW = ASCII_TREE_SHOW
    tc.ASCII_TREE_SHOW_IGNORED = ASCII_TREE_SHOW_IGNORED
    tc.ASCII_TREE_SHOW_SIZE_THRESHOLD = ASCII_TREE_SHOW_SIZE_THRESHOLD

    nf.CONVERT_TO_LF = CONVERT_TO_LF
    nf.TRIM_TRAIL_SPACES = TRIM_TRAIL_SPACES
    nf.CONVERT_TO_TABS = CONVERT_TO_TABS


apply_settings()


# =============================================================================
#  HELPERS
# Thin wrappers over the existing modules (core logic lives in to_clipboard /
# new_file_from_clipboard_paste -- nothing is reimplemented here).
# =============================================================================

def _rel_path(root: str, file_path: str) -> str:
    return os.path.relpath(os.path.normpath(file_path), os.path.normpath(root)).replace("\\", "/")


def normalize_content(text: str, ext: str) -> str:
    """Mirror the normalization `to_clipboard._read_file_content` applies (LF + tabs)."""
    if CONVERT_TO_LF:
        text = text_utils.normalize_line_endings(text)
    if CONVERT_TO_TABS:
        text = text_utils.normalize_indentation_to_tabs(text, ext, text_utils.PRESERVE_SPACE_EXTS)
    return text


def files_to_text(root: str, file_paths: list[str], mode_of=None,
                  cancel_token: CancellationToken | None = None,
                  ascii_tree: bool | None = None) -> str:
    """Build the concatenated text from selected files using `to_clipboard` helpers.

    `mode_of(path) -> Action.OPEN | Action.SIGNATURE | ""` decides how each file
    is rendered. Files whose mode is "" are skipped. When omitted, every file is
    rendered as full content (Action.OPEN).
    """
    root = os.path.normpath(root)
    chunks = []
    rel_paths_for_tree: list[str] = []

    for fp in file_paths:
        if cancel_token:
            cancel_token.check()
        if not os.path.isfile(fp):
            continue
        mode = mode_of(fp) if mode_of else Action.OPEN
        if not mode:
            continue
        rel = _rel_path(root, fp)
        rel_paths_for_tree.append(rel)
        if mode == Action.SIGNATURE:
            sig_text = _get_cached_sig(fp)
            if sig_text is None:
                continue
            if CONVERT_TO_LF:
                sig_text = text_utils.normalize_line_endings(sig_text)
            chunk = tc._format_block([rel], sig_text)
            chunks.append((hashlib.sha256(sig_text.encode("utf-8")).hexdigest(), chunk))
        else:
            result = _get_cached_chunk(fp, rel)
            if result is None:
                continue
            chunks.append(result)

    if cancel_token:
        cancel_token.check()

    if SKIP_DUPLICATE_CONTENT_FILES and chunks:
        from collections import Counter
        counts = Counter(h for h, _ in chunks)
        chunks = [(h, c) for h, c in chunks if counts[h] == 1]

    deduped = tc._deduplicate_chunks(chunks)

    tree = ""
    show_tree = ASCII_TREE_SHOW if ascii_tree is None else ascii_tree
    if show_tree and rel_paths_for_tree:
        tree = tc.generate_ascii_tree(
            base_path=root,
            paths=rel_paths_for_tree,
            show_tree=True,
            show_ignored=ASCII_TREE_SHOW_IGNORED,
            is_ignored_fn=None,
            show_root=SHOW_ROOT_IN_TREE,
        ) or ""

    elements = []
    if tree.strip():
        elements.append(tree.strip())
    if deduped:
        elements.append("\n\n".join(deduped))
    if not elements:
        return ""
    return "\n\n".join(elements)


def _expand_to_files(items: list[str], cancel_token: CancellationToken | None = None) -> list[str]:
    """Expand a list of files/folders into a flat, de-duplicated list of files."""
    files: list[str] = []
    seen: set[str] = set()
    for item in items:
        if cancel_token:
            cancel_token.check()
        ap = os.path.abspath(item)
        if os.path.isfile(ap):
            paths = [ap]
        elif os.path.isdir(ap):
            paths = []
            for dp, _dn, fns in os.walk(ap):
                if cancel_token:
                    cancel_token.check()
                for fn in fns:
                    paths.append(os.path.abspath(os.path.join(dp, fn)))
        else:
            continue
        for p in paths:
            key = os.path.normcase(os.path.normpath(p))
            if key not in seen:
                seen.add(key)
                files.append(p)
    return files


def _paths_from_text(text: str) -> list[str]:
    """Return existing file/folder paths mentioned (one per line) in *text*."""
    found: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().strip('"').strip("'").strip()
        if line and os.path.exists(line):
            found.append(line)
    return found


def _common_root(items: list[str]) -> str:
    """Pick a sensible root: the shared parent folder of the top-level items."""
    parents = [os.path.dirname(os.path.abspath(p)) for p in items if p]
    parents = [p for p in parents if p]
    if not parents:
        return os.getcwd()
    if len(parents) == 1:
        return parents[0]
    try:
        return os.path.commonpath(parents)
    except ValueError:
        return parents[0]


def clipboard_to_text() -> tuple[str, int]:
    """Build combined markdown text from the current clipboard contents.

    Handles Windows file/folder drops (CF_HDROP) and file/folder paths written
    as plain text (one per line). Returns (text, file_count). When the clipboard
    holds no resolvable files, falls back to the raw clipboard text (count 0).
    """
    from dev_helper.common.clipboard import paste_clipboard, paste_clipboard_files

    items = paste_clipboard_files()
    if not items:
        items = _paths_from_text(paste_clipboard())

    if not items:
        return paste_clipboard(), 0

    text, count = items_to_text(items)
    if not count:
        return paste_clipboard(), 0
    return text, count


def items_to_text(items: list[str], cancel_token: CancellationToken | None = None) -> tuple[str, int]:
    """Combine a list of files/folders into formatted markdown. Returns (text, count)."""
    files = _expand_to_files(items, cancel_token=cancel_token)
    if not files:
        return "", 0
    root = _common_root(items)
    return files_to_text(root, files, cancel_token=cancel_token), len(files)


def parse_dnd_paths(data: str) -> list[str]:
    """Parse a Tk drag-and-drop file list (paths, {braced paths with spaces})."""
    parts = re.findall(r"\{[^}]*\}|\S+", data or "")
    return [p[1:-1] if p.startswith("{") and p.endswith("}") else p for p in parts]


def text_to_files(text: str, output_dir: str, overwrite: bool = False,
                  cancel_token: CancellationToken | None = None,
                  progress_cb=None) -> list[str]:
    """Recreate the file structure described by `text` using `new_file` helpers.

    `cancel_token` (checked between blocks) lets a background task be cancelled;
    `progress_cb(done, total)` is invoked periodically while files are written.
    """
    output_dir = os.path.normpath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    original_overwrite = nf.OVERWRITE
    try:
        nf.OVERWRITE = overwrite
        blocks = nf.extract_code_blocks_with_names(text)
        profiler = nf.ExecutionProfiler()
        written: list[str] = []
        seen: set[str] = set()

        for index, block in enumerate(blocks):
            if cancel_token:
                cancel_token.check()
            if progress_cb and index % 25 == 0:
                progress_cb(index, len(blocks))
            filename = block.get("filename")
            content = block.get("content")
            if not filename or not content:
                continue
            rel = filename.replace("\\", "/").lstrip("./")
            if not rel or rel in (".", "..") or rel.startswith("/"):
                continue
            if rel in seen:
                continue
            seen.add(rel)
            ext = os.path.splitext(rel)[1].lstrip(".")
            prefix = os.path.splitext(os.path.basename(rel))[0] or "file"
            nf.save_text_file(content, ext, prefix, output_dir, profiler, full_filename=rel)
            written.append(rel)
    finally:
        nf.OVERWRITE = original_overwrite
    return written


# =============================================================================
#  GUI
# =============================================================================

# Internal marker for the "...loading" placeholder row of an unexpanded folder.
# Stored in the row's `values` (not `text`) so a real file literally named
# "...loading" can never be mistaken for the sentinel.
_LOADING_TAG = "__loading__"


def _make_root():
    """Create the Tk root, preferring a drag-and-drop-capable one when available."""
    try:
        from tkinterdnd2 import TkinterDnD
        return TkinterDnD.Tk(), "tkinterdnd2"
    except Exception:
        import tkinter as tk
        return tk.Tk(), None


def _build_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, font as tkfont, ttk

    class ExplorerApp:
        def __init__(self, root_win: tk.Tk, dnd_flavor=None):
            self.win = root_win
            self.dnd_flavor = dnd_flavor
            self.win.title("2-way Explorer")
            self.root_dir = ""
            self.manager = ActionManager()
            self.manager._bind_tk(root_win)
            self.selected_path = None

            # ---- Cancellation state (each background task gets its own token) ----
            self._cancel_token = CancellationToken()
            self._bg_thread: threading.Thread | None = None
            self._cancel_after_id: str | None = None

            # ---- Debounced refresh state ----
            # A generation counter marks the "latest" request so stale async
            # renders never overwrite newer ones when the user clicks rapidly.
            self._refresh_generation = 0
            self._refresh_scheduled_id: str | None = None

            # ---- Debounced stats state (keystroke-triggered) ----
            self._stats_timer: str | None = None

            self._loading_tree = False

            # GUI-bound mirror of the module-level SETTINGS (single source of truth).
            self.set_vars = {
                "CONVERT_TO_LF": tk.BooleanVar(value=CONVERT_TO_LF),
                "TRIM_TRAIL_SPACES": tk.BooleanVar(value=TRIM_TRAIL_SPACES),
                "CONVERT_TO_TABS": tk.BooleanVar(value=CONVERT_TO_TABS),
                "SKIP_BINARY_FILES_PATH_TREE": tk.BooleanVar(value=SKIP_BINARY_FILES_PATH_TREE),
                "USE_GIT_AND_HG_IGNORE": tk.BooleanVar(value=USE_GIT_AND_HG_IGNORE),
                "SKIP_DUPLICATE_CONTENT_FILES": tk.BooleanVar(value=SKIP_DUPLICATE_CONTENT_FILES),
                "ASCII_TREE_SHOW": tk.BooleanVar(value=ASCII_TREE_SHOW),
                "ASCII_TREE_SHOW_IGNORED": tk.BooleanVar(value=ASCII_TREE_SHOW_IGNORED),
                "ASCII_TREE_SHOW_SIZE_THRESHOLD": tk.StringVar(
                    value="" if ASCII_TREE_SHOW_SIZE_THRESHOLD is None else str(ASCII_TREE_SHOW_SIZE_THRESHOLD)
                ),
                "CONTENT_MARK_FORMAT": tk.StringVar(value=CONTENT_MARK_FORMAT),
            }

            top = ttk.Frame(root_win)
            top.pack(fill="x", padx=6, pady=6)

            ttk.Label(top, text="Source folder:").pack(side="left")
            self.path_var = tk.StringVar()
            ttk.Entry(top, textvariable=self.path_var, width=50).pack(side="left", padx=4, fill="x", expand=True)
            ttk.Button(top, text="Browse...", command=self.browse_source).pack(side="left")

            paned = ttk.PanedWindow(root_win, orient="horizontal")
            paned.pack(fill="both", expand=True, padx=6, pady=4)

            # -- left: explorer tree (lazy loaded, like explorer_example.py) --
            left = ttk.Frame(paned)
            self.tree = ttk.Treeview(left, columns=("mode",), show="tree headings")
            self.tree.heading("#0", text="Files")
            self.tree.column("#0", width=360)
            self.tree.heading("mode", text="Mode")
            self.tree.column("mode", width=150)
            self.tree.pack(side="left", fill="both", expand=True)
            self.tree.bind("<Button-1>", self.on_click)
            self.tree.bind("<<TreeviewOpen>>", self.on_expand)
            self.tree.bind("<Motion>", self.on_hover)
            self.tree.bind("<Leave>", lambda _e: self.clear_hover())
            self._hover_node = None
            self.tree.tag_configure(Action.OPEN, background="#d5f5d5")
            self.tree.tag_configure(Action.SIGNATURE, background="#fff4c2")
            self.tree.tag_configure(Action.MIXED, background="#e0e0e0")
            # Hover highlight uses foreground + bold (not background) so it renders
            # on the Windows "vista" theme, which ignores per-row tag backgrounds,
            # and so it composes with the OPEN/SIGNATURE background tags.
            style = ttk.Style()
            fname = style.lookup("Treeview", "font")
            base_font = tkfont.nametofont(fname) if fname else tkfont.Font()
            self._hover_font = tkfont.Font(font=base_font)
            self._hover_font.configure(size=base_font.cget("size") + 2, weight="normal")
            self.tree.tag_configure("hover", foreground="#1a5fb4", font=self._hover_font)
            vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
            hsb = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
            self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
            vsb.pack(side="right", fill="y")
            hsb.pack(side="bottom", fill="x")
            paned.add(left, weight=1)

            # -- right: text view --
            right = ttk.Frame(paned)
            text_frame = ttk.Frame(right)
            text_frame.pack(fill="both", expand=True)
            self.text = tk.Text(text_frame, wrap="none", undo=True)
            self.text_vsb = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
            self.text_hsb = ttk.Scrollbar(text_frame, orient="horizontal", command=self.text.xview)
            self.text.configure(yscrollcommand=self.text_vsb.set, xscrollcommand=self.text_hsb.set)
            self.text.grid(row=0, column=0, sticky="nsew")
            self.text_vsb.grid(row=0, column=1, sticky="ns")
            self.text_hsb.grid(row=1, column=0, sticky="ew")
            text_frame.rowconfigure(0, weight=1)
            text_frame.columnconfigure(0, weight=1)

            # Bind to text changes for live statistics
            self.text.bind("<<Modified>>", self._on_text_modified)
            self.text.bind("<Control-Key>", self._on_text_control)

            paned.add(right, weight=1)
            self._register_drop_target(self.text)

            sel_frame = ttk.LabelFrame(root_win, text="Selected")
            ttk.Button(sel_frame, text="Set: Open", command=lambda: self.set_action(Action.OPEN)).pack(side="left", padx=2)
            ttk.Button(sel_frame, text="Set: Signature", command=lambda: self.set_action(Action.SIGNATURE)).pack(side="left", padx=2)
            ttk.Button(sel_frame, text="Clear", command=lambda: self.set_action("")).pack(side="left", padx=2)
            ttk.Button(sel_frame, text="Open", command=self.open_selected).pack(side="left", padx=8)

            all_frame = ttk.LabelFrame(root_win, text="All files (whole tree)")
            ttk.Button(all_frame, text="Open all", command=lambda: self.set_action_all(Action.OPEN)).pack(side="left", padx=2)
            ttk.Button(all_frame, text="Signature all", command=lambda: self.set_action_all(Action.SIGNATURE)).pack(side="left", padx=2)
            ttk.Button(all_frame, text="Clear all", command=lambda: self.set_action_all("")).pack(side="left", padx=2)

            # Cancel button (hidden by default, shown during long operations)
            self.cancel_btn = ttk.Button(all_frame, text="■ Cancel", command=self._cancel_operation)
            self.cancel_btn.pack(side="left", padx=2)
            self.cancel_btn.pack_forget()  # hidden by default

            bottom = ttk.Frame(root_win)
            ttk.Button(bottom, text="To Text  ▶", command=self.to_text).pack(side="left", padx=3)
            ttk.Button(bottom, text="From Clipboard", command=self.from_clipboard).pack(side="left", padx=3)
            self.append_mode = tk.BooleanVar(value=False)
            self.output_mode_btn = ttk.Button(bottom, text="Output: Replace", command=self._toggle_output_mode)
            self.output_mode_btn.pack(side="left", padx=3)
            ttk.Button(bottom, text="◀  To Files", command=self.to_files).pack(side="left", padx=3)
            self.out_var = tk.StringVar()
            ttk.Label(bottom, text="Output folder:").pack(side="left", padx=(12, 3))
            ttk.Entry(bottom, textvariable=self.out_var, width=40).pack(side="left", fill="x", expand=True)
            ttk.Button(bottom, text="Browse...", command=self.browse_output).pack(side="left", padx=3)
            self.overwrite_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(bottom, text="Overwrite files", variable=self.overwrite_var).pack(side="left", padx=6)

            settings = ttk.LabelFrame(root_win, text="Settings")
            self._build_settings(settings)

            # Perf report button
            perf_frame = ttk.Frame(root_win)
            perf_frame.pack(side="bottom", fill="x", padx=6, pady=(2, 0))
            ttk.Button(perf_frame, text="📊 Perf Report", command=self._show_perf_report).pack(side="right")

            # Info/Stats bar (replaces single status bar with dual-line info area)
            info_frame = ttk.Frame(root_win)
            info_frame.pack(side="bottom", fill="x")

            self.stats_var = tk.StringVar(value="Length: 0 | Files: 0 | Encoding: N/A")
            stats_label = ttk.Label(info_frame, textvariable=self.stats_var, anchor="w", relief="sunken")
            stats_label.pack(fill="x")

            self.status_var = tk.StringVar(value="Ready")
            status_bar = ttk.Label(info_frame, textvariable=self.status_var, anchor="w", relief="sunken")
            status_bar.pack(fill="x")

            sel_frame.pack(side="bottom", fill="x", padx=6, pady=(2, 0))
            all_frame.pack(side="bottom", fill="x", padx=6, pady=(2, 0))
            settings.pack(side="bottom", fill="x", padx=6, pady=(2, 0))
            bottom.pack(side="bottom", fill="x", padx=6, pady=6)

            self.selected_path = None
            self.root_dir = ""

            # Ctrl+V (layout-independent on non-EN layouts) triggers "From Clipboard".
            self.win.bind_all("<Control-Key>", self._on_global_control)

            # Register cleanup on exit
            self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        # ---- cancellation ----
        def _cancel_operation(self):
            """User pressed Cancel button; signal cancellation."""
            self._cancel_token.cancel()
            self._status("Cancelling...")
            self._hide_cancel_button()

        def _show_cancel_button(self):
            """Display the cancel button during a long operation."""
            self.cancel_btn.pack(side="left", padx=2)

        def _hide_cancel_button(self):
            """Hide the cancel button."""
            self.cancel_btn.pack_forget()

        def _safe_after(self, ms, fn=None):
            """Like win.after(), but silently no-ops once the window is destroyed.

            Background workers finish after WM_DELETE_WINDOW occasionally; a plain
            win.after() would raise TclError inside the worker thread.
            """
            try:
                return self.win.after(ms, fn)
            except Exception:
                return None

        def _run_cancellable(self, task_name: str, bg_fn, *args, on_done=None, on_error=None):
            """Run *bg_fn* in a background thread with cancellation support.

            Threading rules:
              * `bg_fn` runs OFF the main thread -- it must never touch widgets
                (`self.text`, `self.tree`) or call `win.after()` directly; it
                only computes and returns data.
              * `on_done(result)` / `on_error(exc)` are marshalled back to the
                main thread via `win.after(0)`; all UI updates happen there.
              * Every task gets its own CancellationToken, so cancelling (or
                starting) a newer task can never resurrect a zombie one.

            Shows a cancel button after CANCEL_TIMEOUT_MS.
            """
            self._cancel_current()

            token = CancellationToken()
            self._cancel_token = token
            self._status(f"{task_name}...")

            # Schedule showing cancel button after a brief delay
            self._cancel_after_id = self._safe_after(
                CANCEL_TIMEOUT_MS, self._show_cancel_button
            )

            def _worker():
                try:
                    result = bg_fn(*args, cancel_token=token)
                    # CRITICAL: Jump back to main thread for UI updates
                    if on_done:
                        self._safe_after(0, lambda: on_done(result))
                except CancelledError:
                    self._safe_after(0, lambda: self._status(f"{task_name} cancelled."))
                except Exception as exc:
                    if on_error:
                        self._safe_after(0, lambda e=exc: on_error(e))
                    else:
                        self._safe_after(0, lambda e=exc: self._on_bg_error(task_name, e))
                finally:
                    self._safe_after(0, self._on_bg_done)

            self._bg_thread = threading.Thread(target=_worker, daemon=True)
            self._bg_thread.start()

        def _cancel_current(self):
            """Cancel any running background operation."""
            if self._cancel_token:
                self._cancel_token.cancel()
            if self._cancel_after_id is not None:
                try:
                    self.win.after_cancel(self._cancel_after_id)
                except Exception:
                    pass
                self._cancel_after_id = None
            if self._bg_thread and self._bg_thread.is_alive():
                self._bg_thread.join(timeout=0.3)
            self._bg_thread = None
            self._hide_cancel_button()

        def _on_bg_done(self):
            """Clean up after a background operation completes."""
            self._hide_cancel_button()
            if self._cancel_after_id is not None:
                try:
                    self.win.after_cancel(self._cancel_after_id)
                except Exception:
                    pass
                self._cancel_after_id = None
            self._bg_thread = None

        def _on_bg_error(self, task_name: str, exc: Exception):
            """Handle an error from a background thread."""
            self._on_bg_done()
            self._status(f"Error during {task_name}: {exc}")
            traceback.print_exc()

        # ---- drag & drop / clipboard helpers ----
        def _register_drop_target(self, widget):
            """Enable OS file drops onto *widget* when tkinterdnd2 is available."""
            if self.dnd_flavor != "tkinterdnd2":
                return
            try:
                from tkinterdnd2 import DND_FILES
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_dnd_drop)
            except Exception:
                pass

        def _on_dnd_drop(self, event):
            self._insert_dropped(parse_dnd_paths(event.data))
            return "break"

        def _insert_dropped(self, items):
            """Handle dropped files/folders; heavy rendering happens off-thread."""
            items = [p for p in items if p and os.path.exists(p)]
            if not items:
                return

            def bg_build(cancel_token=None):
                return items_to_text(items, cancel_token=cancel_token)

            def on_done(result):
                text, _count = result
                if not text.strip():
                    return
                self._emit(text)

            self._run_cancellable("Processing dropped items", bg_build, on_done=on_done)

        # ---- settings panel (GUI mirror of the module-level SETTINGS) ----
        def _build_settings(self, parent):
            sv = self.set_vars
            checks_col0 = [
                ("LF", "CONVERT_TO_LF"),
                ("trim", "TRIM_TRAIL_SPACES"),
                ("tab", "CONVERT_TO_TABS"),
            ]
            checks_col1 = [
                ("Skip binary", "SKIP_BINARY_FILES_PATH_TREE"),
                ("Skip duplicate content", "SKIP_DUPLICATE_CONTENT_FILES"),
            ]
            checks_col2 = [
                ("Use .gitignore/.hgignore", "USE_GIT_AND_HG_IGNORE"),
                ("Show ASCII tree", "ASCII_TREE_SHOW"),
                ("Show ignored in tree", "ASCII_TREE_SHOW_IGNORED"),
            ]
            for i, (label, key) in enumerate(checks_col0):
                ttk.Checkbutton(
                    parent, text=label, variable=sv[key],
                    command=self._apply_gui_settings,
                ).grid(row=i, column=0, sticky="w", padx=6, pady=2)
            for i, (label, key) in enumerate(checks_col1):
                ttk.Checkbutton(
                    parent, text=label, variable=sv[key],
                    command=self._apply_gui_settings,
                ).grid(row=i, column=1, sticky="w", padx=6, pady=2)
            for i, (label, key) in enumerate(checks_col2):
                ttk.Checkbutton(
                    parent, text=label, variable=sv[key],
                    command=self._apply_gui_settings,
                ).grid(row=i, column=2, sticky="w", padx=6, pady=2)

            threshold_frame = ttk.Frame(parent)
            threshold_frame.grid(row=max(len(checks_col1), 0), column=1, sticky="w", padx=6, pady=2)
            ttk.Label(threshold_frame, text="Tree size threshold (MB, blank=off):").pack(side="left")
            ttk.Entry(threshold_frame, textvariable=sv["ASCII_TREE_SHOW_SIZE_THRESHOLD"], width=8).pack(
                side="left", padx=(4, 0))
            sv["ASCII_TREE_SHOW_SIZE_THRESHOLD"].trace_add("write", lambda *_: self._apply_gui_settings())

            fmt_frame = ttk.Frame(parent)
            fmt_frame.grid(row=max(len(checks_col2), 0), column=2, sticky="w", padx=6, pady=2)
            ttk.Label(fmt_frame, text="Content mark format:").pack(side="left")
            fmt = ttk.Combobox(
                fmt_frame, textvariable=sv["CONTENT_MARK_FORMAT"],
                values=["default", "markdown"], state="readonly", width=12)
            fmt.pack(side="left", padx=(4, 0))
            fmt.bind("<<ComboboxSelected>>", lambda *_: self._apply_gui_settings())

        def _apply_gui_settings(self):
            global CONVERT_TO_LF, TRIM_TRAIL_SPACES, CONVERT_TO_TABS
            global SKIP_BINARY_FILES_PATH_TREE, USE_GIT_AND_HG_IGNORE, SKIP_DUPLICATE_CONTENT_FILES
            global ASCII_TREE_SHOW, ASCII_TREE_SHOW_IGNORED, ASCII_TREE_SHOW_SIZE_THRESHOLD, CONTENT_MARK_FORMAT
            sv = self.set_vars
            CONVERT_TO_LF = sv["CONVERT_TO_LF"].get()
            TRIM_TRAIL_SPACES = sv["TRIM_TRAIL_SPACES"].get()
            CONVERT_TO_TABS = sv["CONVERT_TO_TABS"].get()
            SKIP_BINARY_FILES_PATH_TREE = sv["SKIP_BINARY_FILES_PATH_TREE"].get()
            USE_GIT_AND_HG_IGNORE = sv["USE_GIT_AND_HG_IGNORE"].get()
            SKIP_DUPLICATE_CONTENT_FILES = sv["SKIP_DUPLICATE_CONTENT_FILES"].get()
            ASCII_TREE_SHOW = sv["ASCII_TREE_SHOW"].get()
            ASCII_TREE_SHOW_IGNORED = sv["ASCII_TREE_SHOW_IGNORED"].get()
            raw = sv["ASCII_TREE_SHOW_SIZE_THRESHOLD"].get().strip()
            try:
                ASCII_TREE_SHOW_SIZE_THRESHOLD = None if raw == "" else float(raw)
            except ValueError:
                # Keep the previous value; warn instead of crashing.
                self._status(f"Invalid threshold: {raw!r} (keeping {ASCII_TREE_SHOW_SIZE_THRESHOLD})")
            CONTENT_MARK_FORMAT = sv["CONTENT_MARK_FORMAT"].get()
            apply_settings()
            _chunk_cache.clear()
            _sig_cache.clear()
            if getattr(self, "root_dir", ""):
                self._schedule_refresh()

        # ---- tree handling (lazy loaded, batched) ----
        def browse_source(self):
            d = filedialog.askdirectory()
            if d:
                self.path_var.set(d)
                self.load_tree(d)

        def browse_output(self):
            d = filedialog.askdirectory()
            if d:
                self.out_var.set(d)

        def load_tree(self, root_dir: str):
            perf.start("gui.load_tree")
            self.tree.delete(*self.tree.get_children())
            self.root_dir = os.path.normpath(root_dir)
            self._loading_tree = True
            self._populate(self.root_dir, "")
            self._schedule_refresh()
            perf.stop("gui.load_tree")

        def _populate(self, abs_dir: str, parent_iid: str):
            try:
                entries = sorted(os.listdir(abs_dir))
            except OSError:
                return
            dirs = [e for e in entries if os.path.isdir(os.path.join(abs_dir, e))]
            files = [e for e in entries if os.path.isfile(os.path.join(abs_dir, e))]
            names = dirs + files
            abs_paths = [os.path.normpath(os.path.join(abs_dir, n)) for n in names]
            self._insert_tree_batch(parent_iid, abs_paths, 0)

        def _insert_tree_batch(self, parent_iid, abs_paths, start):
            try:
                alive = bool(self.win.winfo_exists())
            except Exception:
                alive = False
            if not alive:
                return
            perf.start("_insert_tree_batch")
            batch = 200
            end = min(start + batch, len(abs_paths))
            for i in range(start, end):
                abs_path = abs_paths[i]
                name = os.path.basename(abs_path)
                iid = abs_path
                self.tree.insert(
                    parent_iid, "end", iid=iid,
                    text=name,
                    values=(self.manager.get(abs_path) or "",),
                )
                if os.path.isdir(abs_path):
                    self.tree.insert(iid, "end", text="...loading", values=(_LOADING_TAG,))
                self._apply_tags(iid, self.manager.get(abs_path))
            perf.stop("_insert_tree_batch")
            if end < len(abs_paths):
                self._safe_after(1, lambda: self._insert_tree_batch(parent_iid, abs_paths, end))
            elif parent_iid == "" and getattr(self, "_loading_tree", False):
                self._loading_tree = False
                self._safe_after(50, self._refresh_all_folder_modes)

        def on_expand(self, event):
            iid = self.tree.focus()
            if not iid:
                return
            children = self.tree.get_children(iid)
            if children:
                # The placeholder row is recognized by its values sentinel --
                # never by a user-visible name that a real file could share.
                values = self.tree.item(children[0], "values")
                if values and values[0] == _LOADING_TAG:
                    self.tree.delete(children[0])
                    self._populate(iid, iid)

        # ---- hover highlight (foreground + bold; preserves mode background) ----
        def on_hover(self, event):
            row = self.tree.identify_row(event.y)
            if row == self._hover_node:
                return
            self._set_hover(self._hover_node, False)
            self._set_hover(row, True)
            self._hover_node = row

        def clear_hover(self):
            if self._hover_node:
                self._set_hover(self._hover_node, False)
                self._hover_node = None

        def _set_hover(self, node, on):
            if not node or not self.tree.exists(node):
                return
            tags = list(self.tree.item(node, "tags"))
            has = "hover" in tags
            if on and not has:
                tags.append("hover")
                self.tree.item(node, tags=tuple(tags))
            elif not on and has:
                tags.remove("hover")
                self.tree.item(node, tags=tuple(tags))

        def _apply_tags(self, node, mode):
            """Set the mode tag on *node* while preserving the hover highlight."""
            tags = [t for t in self.tree.item(node, "tags") if t == "hover"]
            if mode:
                tags.insert(0, mode)
            self.tree.item(node, tags=tuple(tags))

        def on_click(self, event):
            node = self.tree.identify_row(event.y)
            if not node:
                return
            region = self.tree.identify_region(event.x, event.y)
            col = self.tree.identify_column(event.x)
            abspath = os.path.abspath(node)
            if region == "cell" and col == "#1":
                if os.path.isdir(abspath):
                    self.cycle_action_for_folder(node, abspath)
                else:
                    self.cycle_action(node, abspath)
                return "break"
            if os.path.isdir(abspath):
                return
            self.selected_path = abspath

        def cycle_action(self, node, abspath):
            cycle = ("", Action.OPEN, Action.SIGNATURE)
            current = self.manager.get(abspath)
            idx = cycle.index(current) if current in cycle else 0
            nxt = cycle[(idx + 1) % len(cycle)]
            self.manager.set(abspath, nxt)
            self.tree.set(node, "mode", nxt or "")
            self._apply_tags(node, nxt)
            self._trigger_parent_update(abspath)
            self._schedule_refresh()

        def cycle_action_for_folder(self, node, abspath):
            """Cycle a folder's mode. The (potentially huge) os.walk runs off the
            main thread; manager and UI updates happen only in the callback."""
            cycle = ("", Action.OPEN, Action.SIGNATURE)
            current = self.manager.get(abspath)
            idx = cycle.index(current) if current in cycle else 0
            nxt = cycle[(idx + 1) % len(cycle)]

            def bg_walk(cancel_token=None):
                files = []
                for dirpath, _dirnames, filenames in os.walk(abspath):
                    if cancel_token:
                        cancel_token.check()
                    for fn in filenames:
                        files.append(os.path.abspath(os.path.join(dirpath, fn)))
                return files

            def on_done(files):
                self.manager.set_many(files, nxt)
                self.manager.set(abspath, nxt)
                if self.tree.exists(node):
                    self.tree.set(node, "mode", nxt or "")
                    self._apply_tags(node, nxt)
                    self._refresh_subtree(node, nxt)
                self._trigger_parent_update(abspath)
                self._schedule_refresh()

            self._run_cancellable(
                f"Setting folder to {nxt or 'cleared'}",
                bg_walk,
                on_done=on_done,
            )

        def walk_files(self, root):
            """Yield all files under *root* (generator, not list)."""
            for dirpath, _dirnames, filenames in os.walk(root):
                for fn in filenames:
                    yield os.path.abspath(os.path.join(dirpath, fn))

        def _refresh_subtree(self, node, mode):
            """Update the visual mode column for a subtree (recursive)."""
            for child in self.tree.get_children(node):
                values = self.tree.item(child, "values")
                if values and values[0] == _LOADING_TAG:
                    continue
                self.tree.set(child, "mode", mode or "")
                self._apply_tags(child, mode)
                if os.path.isdir(os.path.abspath(child)):
                    self._refresh_subtree(child, mode)

        def _update_folder_mode(self, folder_path: str):
            """
            Recalculate a folder's visual mode from its direct children on disk.

            Lists the folder and asks the manager for each child's mode, which is
            O(children-count), needs no extra index kept in memory, and avoids
            scanning (and locking) the whole ActionManager dataset on the UI
            thread. If the mode changes, update the UI and bubble up to the
            parent folder; uses after(0) to prevent freezing on deep trees.
            """
            try:
                entries = os.listdir(folder_path)
            except OSError:
                return

            modes = set()
            for name in entries:
                mode = self.manager.get(os.path.join(folder_path, name))
                if mode:
                    modes.add(mode)
                    if len(modes) > 1:
                        break

            if len(modes) == 0:
                new_mode_label = ""
            elif len(modes) == 1:
                new_mode_label = modes.pop()
            else:
                new_mode_label = Action.MIXED

            node_id = folder_path
            if self.tree.exists(node_id):
                current_values = self.tree.item(node_id, "values")
                current_val = current_values[0] if current_values else ""

                if current_val != new_mode_label:
                    self.tree.set(node_id, "mode", new_mode_label)
                    self._apply_tags(node_id, new_mode_label)

                    parent_path = os.path.dirname(folder_path)
                    if parent_path and parent_path != folder_path:
                        self._safe_after(0, lambda p=parent_path: self._update_folder_mode(p))

        def _trigger_parent_update(self, path: str):
            """Helper to start the bubble-up process from a specific file or folder."""
            parent = os.path.dirname(path)
            if parent and os.path.isdir(parent):
                self._safe_after(0, lambda p=parent: self._update_folder_mode(p))

        def _refresh_all_folder_modes(self):
            """Recompute mode displays for all folders in the tree."""
            def visit(node):
                if os.path.isdir(node):
                    self._update_folder_mode(node)
                for child in self.tree.get_children(node):
                    visit(child)
            visit("")

        def set_action_all(self, action):
            """Set mode for ALL files in the tree with cancellation support.

            For large directories, this runs in a background thread so the GUI
            stays responsive. The cancel button lets the user interrupt.
            """
            if not self.root_dir:
                self._status("No source folder loaded.")
                return

            if not action:
                perf.start("gui.clear_all")
                self.manager.clear()
                self._refresh_subtree("", "")
                self._refresh_all_folder_modes()
                self._schedule_refresh()
                self._status("Cleared all file modes.")
                perf.stop("gui.clear_all")
                return

            root = self.root_dir

            def _bg_set_all(cancel_token=None):
                """Walk the tree and collect files, checking cancellation periodically."""
                files = []
                for dirpath, _dirnames, filenames in os.walk(root):
                    if cancel_token:
                        cancel_token.check()
                    for fn in filenames:
                        files.append(os.path.abspath(os.path.join(dirpath, fn)))
                    if cancel_token and len(files) % 500 == 0:
                        cancel_token.check()
                return files

            def _on_files_collected(files):
                perf.start("gui.set_all.apply")
                self.manager.set_many(files, action)
                self._refresh_subtree("", action)
                self._refresh_all_folder_modes()
                self._schedule_refresh()
                self._status(f"Set {len(files)} files to {action}.")
                perf.stop("gui.set_all.apply")

            self._run_cancellable(
                f"Setting all files to {action}",
                _bg_set_all,
                on_done=_on_files_collected,
            )

        def set_action(self, action):
            if not self.selected_path:
                self._status("Select a file or folder first.")
                return
            if os.path.isdir(self.selected_path):
                files = list(self.walk_files(self.selected_path))
                self.manager.set_many(files, action)
                self.manager.set(self.selected_path, action)
                sel = self.tree.selection()
                if sel:
                    node = sel[0]
                    self.tree.set(node, "mode", action or "")
                    self._apply_tags(node, action)
                    self._refresh_subtree(node, action)
                self._trigger_parent_update(self.selected_path)
                self._schedule_refresh()
                return
            self.manager.set(self.selected_path, action)
            for node in self.tree.selection():
                self.tree.set(node, "mode", action or "")
                self._apply_tags(node, action)
            self._trigger_parent_update(self.selected_path)
            self._schedule_refresh()

        # ---- actions ----
        def _mode_of(self, path):
            return self.manager.get(path) or Action.OPEN

        def _status(self, msg):
            """Show a non-disruptive message in the status bar"""
            self.status_var.set(msg)

        def _toggle_output_mode(self):
            self.append_mode.set(not self.append_mode.get())
            self.output_mode_btn.config(
                text="Output: Concatenate" if self.append_mode.get() else "Output: Replace"
            )

        def _emit(self, text):
            """Write *text* to the right area, appending or replacing per the toggle."""
            text = text or ""
            if self.append_mode.get():
                existing = self.text.get("1.0", "end-1c")
                if existing.strip():
                    text = existing.rstrip("\n") + "\n\n" + text
            self.text.edit_modified(False)
            self.text.replace("1.0", "end-1c", text)
            self.text.edit_modified(True)
            self.text.edit_modified(False)

        def _schedule_refresh(self):
            """Debounced refresh: collapse a burst of requests into the latest one."""
            if self._refresh_scheduled_id:
                try:
                    self.win.after_cancel(self._refresh_scheduled_id)
                except Exception:
                    pass

            # Increment generation to mark this as the "latest" request
            self._refresh_generation += 1
            current_gen = self._refresh_generation

            self._refresh_scheduled_id = self._safe_after(100, lambda: self._do_refresh(current_gen))

        def _do_refresh(self, gen):
            self._refresh_scheduled_id = None

            # If a newer refresh was scheduled while we waited, bail out
            if gen != self._refresh_generation:
                return

            # In Concatenate mode we don't auto-overwrite the accumulated text;
            # use "To Text" / "Open" to add the selection explicitly.
            if self.append_mode.get():
                return

            selected = self.manager.snapshot_selected(self.root_dir)

            # Fast path: no selection -> clear and return immediately
            if not selected:
                self._apply_preview_text("")
                return

            def bg_render(cancel_token=None):
                return files_to_text(
                    self.root_dir, selected,
                    mode_of=self._mode_of, cancel_token=cancel_token,
                )

            def on_done(text):
                # Only apply if this is still the latest generation
                if gen == self._refresh_generation:
                    self._apply_preview_text(text)
                    if text.strip():
                        ascii_blocks = len(re.findall(r'^======\s+.*\s+======$', text, re.MULTILINE))
                        md_blocks = len(re.findall(r'^```[a-zA-Z0-9_./\\-]+$', text, re.MULTILINE))
                        file_count = max(ascii_blocks, md_blocks)
                        self._status(f"Preview: {file_count} file(s)")
                    else:
                        self._status("No files selected")

            self._run_cancellable("Refreshing preview", bg_render, on_done=on_done)

        def _apply_preview_text(self, text):
            """Write the live-preview render into the text widget (main thread only)."""
            self.text.edit_modified(False)
            self.text.replace("1.0", "end-1c", text)
            self.text.edit_modified(True)
            self.text.edit_modified(False)
            self._update_stats()

        # ---- Live Statistics Handler ----
        def _on_text_modified(self, event=None):
            """Triggered whenever the right text pane changes.

            _update_stats copies the whole buffer and runs regex passes over it,
            so it is debounced (200ms) instead of running on every keystroke --
            on multi-MB documents the synchronous version caused input lag.
            """
            self.text.edit_modified(False)  # Reset Tkinter's internal modified flag
            if self._stats_timer is not None:
                try:
                    self.win.after_cancel(self._stats_timer)
                except Exception:
                    pass
                self._stats_timer = None
            self._stats_timer = self._safe_after(200, self._flush_stats_timer)

        def _flush_stats_timer(self):
            self._stats_timer = None
            self._update_stats()

        def _on_text_control(self, event):
            kc = event.keycode
            if kc == 65:  # 'A' key
                self.text.tag_add("sel", "1.0", "end")
                return "break"
            if kc == 67:  # 'C' key
                self.text.event_generate("<<Copy>>")
                return "break"
            if kc == 88:  # 'X' key
                self.text.event_generate("<<Cut>>")
                return "break"
            if kc == 86:  # 'V' key
                return self.from_clipboard(event)
            return None

        def _update_stats(self):
            """Calculate and display length, file count, and encoding type."""
            text = self.text.get("1.0", "end-1c")
            if not text.strip():
                self.stats_var.set("Length: 0 | Files: 0 | Encoding: N/A")
                return

            char_len = len(text)

            # Count file blocks based on ====== headers or markdown fences with filenames
            ascii_blocks = len(re.findall(r'^======\s+.*\s+======$', text, re.MULTILINE))
            md_blocks = len(re.findall(r'^```[a-zA-Z0-9_./\\-]+$', text, re.MULTILINE))
            file_count = max(ascii_blocks, md_blocks)

            # Encoding detection
            enc_type = "ASCII" if text.isascii() else "UTF-8 (multi-byte)"

            self.stats_var.set(f"Length: {char_len} chars | Files: {file_count} | Encoding: {enc_type}")

        def to_text(self):
            """Render the selection in the background, then copy it to the clipboard.

            The live preview refreshes asynchronously, so the widget can no longer
            be read back synchronously; the render is kicked off here directly and
            the clipboard copy happens in the completion callback.
            """
            if not self.root_dir:
                self._status("No source folder loaded.")
                return
            selected = self.manager.snapshot_selected(self.root_dir)
            if not selected:
                self._status("No files selected (set a mode first).")
                return
            # Append mode historically renders without the ASCII tree; replace
            # mode matches the live preview (tree follows the current setting).
            ascii_tree = False if self.append_mode.get() else None

            def bg_render(cancel_token=None):
                return files_to_text(
                    self.root_dir, selected,
                    mode_of=self._mode_of, cancel_token=cancel_token,
                    ascii_tree=ascii_tree,
                )

            def on_done(text):
                if self.append_mode.get():
                    self._emit(text)
                    combined = self.text.get("1.0", "end").strip()
                else:
                    # Claim the latest generation so no stale preview can
                    # overwrite what we are about to show.
                    self._refresh_generation += 1
                    self._apply_preview_text(text)
                    combined = text.strip()
                if not combined:
                    self._status("No files selected (set a mode first).")
                    return
                try:
                    tc.copy_to_clipboard(combined)
                    status = " (copied to clipboard)"
                except Exception:
                    status = ""
                self._status(f"Generated text for {len(selected)} files.{status}")

            self._run_cancellable("Generating text", bg_render, on_done=on_done)

        def _on_global_control(self, event):
            if event.keycode == 86:  # 'V' key
                return self.from_clipboard(event)
            return None

        def from_clipboard(self, event=None):
            """Read files/folders (or paths) from the clipboard, combine them into
            formatted markdown, and put the result in the right text area."""
            try:
                text, count = clipboard_to_text()
            except Exception as exc:
                self._status(f"Could not read clipboard: {exc}")
                return "break"
            if not text.strip():
                self._status("Clipboard is empty or has no readable files.")
                return "break"
            self._emit(text)
            if count:
                self._status(f"Combined {count} file(s) from clipboard.")
            else:
                self._status("No files found; inserted raw clipboard text.")
            return "break"

        def open_selected(self):
            if not self.selected_path:
                self._status("Select a file or folder first.")
                return
            self.open_file(self.selected_path)

        def open_file(self, abspath):
            if os.path.isdir(abspath):
                files = list(self.walk_files(abspath))
            else:
                files = [abspath]
            text = files_to_text(self.root_dir, files, mode_of=self._mode_of)
            self._emit(text)
            if not text.strip():
                name = os.path.basename(abspath)
                self._status(f"{name}: nothing to show (no mode / no signatures).")

        def to_files(self):
            """Recreate files from the text; the disk writes run off the main thread."""
            text = self.text.get("1.0", "end")
            out = self.out_var.get().strip()
            if not out:
                out = filedialog.askdirectory(title="Select output folder")
                if not out:
                    return
                self.out_var.set(out)
            out = os.path.normpath(out)
            overwrite = self.overwrite_var.get()

            def bg_write(cancel_token=None):
                return text_to_files(
                    text, out, overwrite=overwrite,
                    cancel_token=cancel_token,
                    progress_cb=self._report_write_progress,
                )

            def on_done(written):
                if not written:
                    self._status("No file blocks found in the text.")
                    return
                self._status(f"Recreated {len(written)} files in: {out}")

            self._run_cancellable("Writing files", bg_write, on_done=on_done)

        def _report_write_progress(self, done, total):
            """Progress callback from the writer thread; hops to the main thread."""
            self._safe_after(0, lambda: self._status(f"Writing files... {done}/{total}"))

        # ---- perf report ----
        def _show_perf_report(self):
            """Display the performance report in a popup window."""
            import tkinter as tk
            from tkinter import ttk

            report_win = tk.Toplevel(self.win)
            report_win.title("Performance Report")
            report_win.geometry("700x400")

            text = tk.Text(report_win, wrap="none")
            vsb = ttk.Scrollbar(report_win, orient="vertical", command=text.yview)
            hsb = ttk.Scrollbar(report_win, orient="horizontal", command=text.xview)
            text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
            text.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")
            hsb.pack(side="bottom", fill="x")

            summary = perf.get_summary()
            if not summary:
                text.insert("1.0", "No performance data collected yet.\n")
                text.configure(state="disabled")
                return

            lines = ["=" * 70, " PERFORMANCE REPORT", "=" * 70, ""]
            lines.append(f"{'Operation':<42} {'n':>5} {'avg':>9} {'max':>9} {'tot':>9}")
            lines.append("-" * 70)

            for name in sorted(summary):
                times = summary[name]
                total = sum(times)
                avg = total / len(times)
                mx = max(times)
                lines.append(
                    f"{name:<42} {len(times):>5} "
                    f"{avg:>8.1f}ms {mx:>8.1f}ms {total:>8.1f}ms"
                )
            lines.append("=" * 70)

            text.insert("1.0", "\n".join(lines))
            text.configure(state="disabled")

        # ---- cleanup ----
        def _on_close(self):
            """Ensure pending saves are flushed before exit."""
            self._cancel_current()
            self.manager.flush()
            self.win.destroy()

    root_win, dnd_flavor = _make_root()
    app = ExplorerApp(root_win, dnd_flavor=dnd_flavor)
    app.win.mainloop()


# Unit test / self test live in `test_prompt/2way_explorer_roundtrip_test.py`
# ---------------------------------------------------------------------------
# CLI automation + GUI
# ---------------------------------------------------------------------------

def _run_headless(args):
    import argparse
    global PERF_LOG_ENABLED, PERF_LOG_TO_FILE
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="2-way explorer CLI")
    parser.add_argument("--folder", required=True, help="Root folder to load")
    parser.add_argument("--mode", choices=["open", "signature", "clear"], default="open", help="Mode to apply to all files")
    parser.add_argument("--text", action="store_true", help="Print combined text to stdout")
    parser.add_argument("--perf", action="store_true", help="Dump performance report to stderr")
    parser.add_argument("--perf-file", help="Also write performance data to this JSONL file")
    parser.add_argument("--ascii-tree", dest="ascii_tree", action="store_true", default=True, help="Include ASCII tree in output")
    parser.add_argument("--no-ascii-tree", dest="ascii_tree", action="store_false", help="Skip ASCII tree in output")

    parsed = parser.parse_args(args)
    perf.reset()
    PERF_LOG_ENABLED = parsed.perf
    PERF_LOG_TO_FILE = bool(parsed.perf_file)

    if parsed.perf_file:
        try:
            with open(parsed.perf_file, "w", encoding="utf-8") as f:
                f.write("")
        except OSError:
            pass

    root_dir = os.path.abspath(parsed.folder)
    if not os.path.isdir(root_dir):
        print(f"Error: folder not found: {root_dir}", file=sys.stderr)
        sys.exit(1)

    mgr = ActionManager()
    
    if parsed.mode == "open":
        action = Action.OPEN
    elif parsed.mode == "signature":
        action = Action.SIGNATURE
    else:
        action = ""
    
    if action:
        files = []
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for fn in filenames:
                files.append(os.path.abspath(os.path.join(dirpath, fn)))
        mgr.set_many(files, action)
        mgr.flush()
    
    if parsed.text:
        selected = sorted(
            p for p, a in mgr.data.items()
            if a and os.path.isfile(p) and p.startswith(root_dir)
        )
        print(files_to_text(root_dir, selected, mode_of=mgr.get, ascii_tree=parsed.ascii_tree))
    
    if parsed.perf:
        summary = perf.get_summary()
        if summary:
            print("=" * 70, file=sys.stderr)
            print(" PERFORMANCE REPORT", file=sys.stderr)
            print("=" * 70, file=sys.stderr)
            print(f"{'Operation':<42} {'n':>5} {'avg':>9} {'max':>9} {'tot':>9}", file=sys.stderr)
            print("-" * 70, file=sys.stderr)
            for name in sorted(summary):
                times = summary[name]
                total = sum(times)
                avg = total / len(times)
                mx = max(times)
                print(
                    f"{name:<42} {len(times):>5} "
                    f"{avg:>8.1f}ms {mx:>8.1f}ms {total:>8.1f}ms",
                    file=sys.stderr
                )
            print("=" * 70, file=sys.stderr)
        else:
            print("No performance data collected.", file=sys.stderr)


_CLI_FLAGS = frozenset({
    "--folder", "--mode", "--text", "--perf", "--perf-file",
    "--ascii-tree", "--no-ascii-tree", "-h", "--help",
})


def _should_run_gui():
    """Run the GUI unless a known headless CLI flag is present.

    Matches specific flags in sys.argv[1:] (exact match, including the
    --flag=value form) instead of loose substring checks.
    """
    for arg in sys.argv[1:]:
        if arg.split("=", 1)[0] in _CLI_FLAGS:
            return False
    return True


if __name__ == "__main__":
    if _should_run_gui():
        _build_gui()
    else:
        _run_headless(sys.argv[1:])
