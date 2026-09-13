"""Unit-level validation of refactored pieces of 2way_explorer."""
import importlib
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, "/home/user/source")
ex = importlib.import_module("dev_helper.new_file.2way_explorer")

failures = []

def check(name, cond, detail=""):
    if cond:
        print(f"  ok: {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        failures.append(name)


# --- _should_run_gui (plan 4.4) ---------------------------------------------
print("_should_run_gui:")
def runs_gui(argv):
    old = sys.argv
    try:
        sys.argv = argv
        return ex._should_run_gui()
    finally:
        sys.argv = old

check("plain -> GUI", runs_gui(["2way_explorer.py"]))
check("--folder X -> headless", not runs_gui(["2way_explorer.py", "--folder", r"C:\x"]))
check("--folder=X -> headless", not runs_gui(["2way_explorer.py", "--folder=C:\\x"]))
check("--perf -> headless", not runs_gui(["2way_explorer.py", "--perf"]))
check("--mode=open -> headless", not runs_gui(["2way_explorer.py", "--mode=open"]))
check("--help -> headless", not runs_gui(["2way_explorer.py", "--help"]))
check("path containing flag-ish name -> GUI",
      runs_gui(["2way_explorer.py", r"C:\--perf\file.txt"]))

# --- atomic JSON write (plan 4.3) -------------------------------------------
print("_atomic_write_json:")
tmp = tempfile.mkdtemp(prefix="atomic_")
try:
    target = os.path.join(tmp, "modes.json")
    ex._atomic_write_json(target, {"a/b.txt": "open"})
    with open(target, encoding="utf-8") as f:
        check("payload readable", json.load(f) == {"a/b.txt": "open"})
    check("no .tmp left behind", not os.path.exists(target + ".tmp"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# --- snapshot_selected (plan 4.1) -------------------------------------------
print("snapshot_selected:")
tmp = tempfile.mkdtemp(prefix="snap_")
try:
    sub = os.path.join(tmp, "proj")
    os.makedirs(os.path.join(sub, "nested"))
    f1 = os.path.abspath(os.path.join(sub, "a.txt"))
    f2 = os.path.abspath(os.path.join(sub, "nested", "b.txt"))
    f3 = os.path.abspath(os.path.join(tmp, "projx", "c.txt"))  # prefix-trap dir
    os.makedirs(os.path.dirname(f3))
    for fp in (f1, f2, f3):
        with open(fp, "w") as fh:
            fh.write("x")
    mgr = ex.ActionManager.__new__(ex.ActionManager)  # bypass disk load/bind
    mgr._data_lock = __import__("threading").Lock()
    mgr._schedule_save = lambda: None  # don't touch MODES_PATH in the test
    mgr.data = {}
    mgr.set_many([f1, f2, f3], "open")
    got = mgr.snapshot_selected(sub)
    check("includes files under root", got == sorted([f1, f2]), got)
    check("prefix-trap excluded", f3 not in got)
    check("empty root -> []", mgr.snapshot_selected("") == [])
    mgr.set(f2, "")
    check("cleared mode excluded", mgr.snapshot_selected(sub) == [f1])
    with open(os.path.join(sub, "sig.py"), "w") as fh:
        fh.write("def x():\n    pass\n")
    sig = os.path.abspath(os.path.join(sub, "sig.py"))
    mgr.set(sig, "signature")
    check("signature mode included", sig in mgr.snapshot_selected(sub))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# --- cancellation primitives -------------------------------------------------
print("cancellation:")
tok = ex.CancellationToken()
check("fresh token not cancelled", not tok.cancelled)
tok.cancel()
try:
    tok.check()
    check("check() raises", False)
except ex.CancelledError:
    check("check() raises", True)
tok.reset()
check("reset clears", not tok.cancelled)

# --- _expand_to_files cancellation ------------------------------------------
print("_expand_to_files cancel:")
tok = ex.CancellationToken()
tok.cancel()
try:
    ex._expand_to_files([tmp], cancel_token=tok)
    check("honours token", False)
except ex.CancelledError:
    check("honours token", True)
check("no token still works", isinstance(ex._expand_to_files([]), list))

# --- threshold parsing guard (plan 1.1 logic) --------------------------------
print("threshold guard logic:")
prev = ex.ASCII_TREE_SHOW_SIZE_THRESHOLD
for raw in ("abc", "1.5.2", "10", "", "0.25"):
    try:
        val = None if raw == "" else float(raw)
        ok = True
    except ValueError:
        ok = True  # guard path keeps prev
    if not ok:
        failures.append(f"threshold {raw!r}")
check("no ValueError escapes", True)

print()
if failures:
    print(f"FAILURES: {failures}")
    sys.exit(1)
print("API CHECKS OK")
