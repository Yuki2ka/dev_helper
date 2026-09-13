# 2way_explorer.py — Performance Optimization & Cancellation

## Problem Summary

| Use Case | Root Cause | Fix |
|---|---|---|
| "Open all" / "Signature all" freezes app | `set_action_all` did everything synchronously on the main thread — `os.walk` + `set_many` + `compact` + `_refresh_text` all in one blocking call chain | Background thread + cancellation token + coalesced refresh |
| Unselecting 1 file → text clears but takes seconds | `_refresh_text` always re-reads and re-renders every selected file, even when the selection was now empty | Fast-path: empty selection returns instantly. Cached `_selected_files()`. |
| Cycling folder mode (full↔signature) is slow | Every `cycle_action` called `save()` which ran the full O(n²) `_compact` algorithm | Debounced save (500ms) batches writes; `_compact` has early-exit for small datasets |
| No visibility into what's slow | No instrumentation | `PerfCollector` singleton logs all operations >50ms to stderr and optional `.jsonl` file |
| No way to abort a hung operation | No cancellation mechanism | `CancellationToken` + Cancel button in GUI + thread interruption |

---

## New Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      ExplorerApp                             │
│                                                              │
│  ┌──────────────┐   ┌──────────────────┐   ┌─────────────┐ │
│  │ ActionManager │   │  _schedule_refresh│   │ _run_cancel-│ │
│  │               │   │  (after_idle)     │   │ lable()     │ │
│  │ debounced     │   │                   │   │             │ │
│  │ save (500ms)  │   │ coalesces multiple│   │ bg thread + │ │
│  │               │   │ refresh requests  │   │ cancel btn  │ │
│  └──────┬────────┘   └────────┬─────────┘   └──────┬──────┘ │
│         │                     │                     │        │
│    ┌────▼────┐          ┌─────▼──────┐       ┌─────▼─────┐ │
│    │ _compact│          │_refresh_text│       │CancelToken│ │
│    │ early   │          │ fast-path   │       │.cancel()  │ │
│    │ exit    │          │ empty sel   │       │.check()   │ │
│    └─────────┘          └────────────┘       └───────────┘ │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ PerfCollector (singleton)                             │   │
│  │  .start("op") / .stop("op") → logs if >50ms          │   │
│  │  .print_report() → aggregate summary                  │   │
│  │  .get_summary() → dict for programmatic use           │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Optimizations in Detail

### 1. Debounced ActionManager.save()

**Before:** Every `set()` and every item in `set_many()` triggered a full `save()` → `_compact()` → `json.dump()`.

**After:** 
- `set()` / `set_many()` mark the manager as dirty and schedule a save 500ms later via `tk.after()`.
- Multiple rapid changes coalesce into a single write.
- `_compact()` skips the iterative collapse loop when data has ≤20 entries (just does one pass).
- `_compact()` has a hard cap of 100 iterations to prevent infinite loops.

```python
# New flow:
self.manager.set(path, mode)    # marks dirty, schedules save in 500ms
self.manager.set(other, mode2)  # resets the 500ms timer
# ... 500ms of silence ...
self.manager._flush_save()      # one compact + one write
```

### 2. Coalesced Text Refresh

**Before:** Every click-to-cycle-mode called `_refresh_text()` immediately, which:
1. Enumerated all selected files
2. Read each file from disk
3. Normalized content (LF, tabs)
4. Extracted signatures (if in signature mode)
5. Generated ASCII tree
6. Deduplicated

**After:**
- `_schedule_refresh()` debounces via `after_idle` — only one refresh per idle cycle.
- `_selected_files()` result is cached and invalidated only when modes change.
- Empty selection → returns `""` instantly, bypassing the entire pipeline.

### 3. Background Thread for "Open All" / "Signature All"

**Before:** `set_action_all()` ran `os.walk()` + `set_many()` on the main thread, freezing the UI.

**After:**
- `_run_cancellable()` spawns a daemon thread to walk the filesystem.
- A `CancellationToken` is passed in; the walker checks it every 500 files.
- After `CANCEL_TIMEOUT_MS` (300ms default), a "✕ Cancel" button appears.
- The user can click Cancel → token is signalled → thread exits cleanly.
- Only the fast `set_many()` + `_refresh_subtree()` run on the main thread upon completion.

### 4. CancellationToken

Thread-safe primitive for cooperative cancellation:

```python
token = CancellationToken()
# In worker thread:
for item in huge_list:
    token.check()  # raises CancelledError if cancelled
    process(item)
# In main thread:
token.cancel()  # signals cancellation
```

Used in:
- `files_to_text()` — checks between each file
- `_bg_set_all()` — checks every 500 files during `os.walk`

### 5. PerfCollector

Instrumentation singleton that tracks operation timings:

```python
perf = PerfCollector()
perf.start("action_mgr.save")
# ... work ...
perf.stop("action_mgr.save")  # logs to stderr if >50ms, optionally to .jsonl
```

**Output to stderr:**
```
[perf] gui.refresh_text: 234.5ms
[perf] action_mgr.save: 89.2ms
```

**Aggregate report** (click "📊 Perf Report" button):
```
======================================================================
 PERFORMANCE REPORT
======================================================================
  action_mgr.save                           n=  42  avg=   45.3ms  max=  189.0ms  tot= 1902.6ms
  gui.refresh_text                          n=  23  avg=  120.1ms  max=  450.2ms  tot= 2762.3ms
  gui.load_tree                             n=   1  avg=   15.2ms  max=   15.2ms  tot=   15.2ms
======================================================================
```

**Settings:**
| Setting | Default | Description |
|---|---|---|
| `PERF_LOG_ENABLED` | `True` | Master switch |
| `PERF_LOG_THRESHOLD_MS` | `50` | Only log operations slower than this |
| `PERF_LOG_TO_FILE` | `False` | Also append to `perf_log.jsonl` |

### 6. Fast-path for Empty Selection

```python
def _refresh_text(self):
    selected = self._selected_files()
    if not selected:
        self.text.delete("1.0", "end")
        self._update_stats()
        return  # ← instant exit, no file I/O
    # ... normal rendering ...
```

This directly fixes the "unselect 1 file → text empty but takes seconds" use case.

### 7. Graceful Shutdown

`_on_close()` bound to `WM_DELETE_WINDOW`:
1. Cancels any running background operation
2. Calls `manager.flush()` to write pending changes immediately
3. Destroys the window

---

## How Cancellation Works (End-to-End)

```
User clicks "Open all" on a folder with 100,000 files
         │
         ▼
set_action_all(Action.OPEN)
         │
         ▼
_run_cancellable("Setting all files to open", _bg_set_all)
         │
         ├──► Creates CancellationToken
         ├──► Shows status: "Setting all files to open..."
         ├──► Schedules cancel button to appear in 300ms
         └──► Spawns daemon thread:
                  │
                  ▼
              os.walk(root)  ───► checks token.check() every 500 files
                  │
         ┌────────┴────────┐
         ▼                 ▼
    [Success]          [User clicks Cancel]
         │                 │
         ▼                 ▼
  on_done(files)      token.cancel()
         │                 │
         ▼                 ▼
  set_many(files)     CancelledError raised in thread
  refresh_subtree     _on_bg_done() hides cancel button
  schedule_refresh    Status: "Setting all files to open cancelled."
```

---

## File Changes

Only one file was modified: **`dev_helper/new_file/2way_explorer.py`**

All other modules (`to_clipboard.py`, `signature_extractor.py`, `text_utils.py`, `clipboard.py`, `new_file_from_clipboard_paste.py`) are unchanged — the optimization is entirely in the GUI layer and the ActionManager.

---

## New Settings (top of file)

```python
# === Performance Settings ===
PERF_LOG_ENABLED = True           # Master switch for performance logging
PERF_LOG_THRESHOLD_MS = 50        # Only log operations slower than this (ms)
PERF_LOG_TO_FILE = False          # Also write perf log to disk (perf_log.jsonl)
CANCEL_TIMEOUT_MS = 300           # Show cancel button after this many ms
```
