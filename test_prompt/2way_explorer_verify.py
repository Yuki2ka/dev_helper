"""
verify.py - Headless self-check (no GUI).

Runs the same round-trip checks as test_2way.py and writes a report to
verify_report.txt so it can be inspected without a terminal.  Also reports
whether optional deps (tkinter, pathspec, magika, pyperclip) imported OK.

Run:  python verify.py
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

lines = []
def log(msg=""):
    lines.append(str(msg))

# --- dependency probe ---
for mod in ("tkinter", "pathspec", "pyperclip", "magika"):
    try:
        importlib.import_module(mod)
        log(f"[dep] {mod}: OK")
    except Exception as e:
        log(f"[dep] {mod}: MISSING ({e.__class__.__name__})")

# --- core import ---
try:
    import core
    log("[core] import OK")
except Exception as e:
    log(f"[core] import FAILED: {e}")
    open(os.path.join(ROOT, "verify_report.txt"), "w").write("\n".join(lines))
    raise

# --- round trip check (mirrors test_2way) ---
import core as C

tmp = tempfile.mkdtemp(prefix="verify_")
src = os.path.join(tmp, "src")
out = os.path.join(tmp, "out")
os.makedirs(src)

tree = {
    "a.py": "def foo():\n    return 1\n\nclass Bar:\n    pass\n",
    "sub/b.py": "def baz():\n    return 2\n",
    "sub/c.txt": "hello world\nsecond line\n",
    "readme.md": "# Title\n\nsome text\n",
    "dup1.txt": "SAME CONTENT\n",
    "dup2.txt": "SAME CONTENT\n",
}

for rel, content in tree.items():
    full = os.path.join(src, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w", encoding="utf-8", newline="").write(content)

items = [(rel, os.path.join(src, rel), C.MODE_OPEN) for rel in tree]
try:
    text1 = C.files_to_text(src, items)
    written = C.text_to_files(text1, out)
    out_files = C.collect_tree_files(out)
    same_struct = set(out_files.keys()) == set(tree.keys())
    same_content = all(out_files[r] == tree[r] for r in tree)
    items2 = [(r, os.path.join(out, r), C.MODE_OPEN) for r in out_files]
    text2 = C.files_to_text(out, items2)
    stable = text2 == text1
    log(f"[roundtrip] text1 length: {len(text1)}")
    log(f"[roundtrip] files written: {len(written)} (expected {len(tree)})")
    log(f"[roundtrip] structure preserved: {same_struct}")
    log(f"[roundtrip] content byte-identical: {same_content}")
    log(f"[roundtrip] text stable across round trip: {stable}")
    log("[roundtrip] RESULT: " + ("PASS" if (same_struct and same_content and stable) else "FAIL"))
except Exception as e:
    log(f"[roundtrip] ERROR: {e}")
    import traceback
    log(traceback.format_exc())

shutil.rmtree(tmp, ignore_errors=True)

report = "\n".join(lines)
open(os.path.join(ROOT, "verify_report.txt"), "w").write(report + "\n")
print(report)
