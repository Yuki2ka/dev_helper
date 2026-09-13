"""Verify the refactored ActionManager._compact is equivalent to the original.

Builds randomized folder trees on disk, derives mode datasets, and compares
old vs new _compact outputs (normalized-form dicts). Also exercises
_expand(_compact(data)) == data round-trips for the new implementation.
"""
import importlib.util
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, "/home/user/source")

import importlib
ex_new = importlib.import_module("dev_helper.new_file.2way_explorer")

spec = importlib.util.spec_from_file_location("ex_old", "/home/user/2way_explorer.original.py")
ex_old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ex_old)


def build_tree(root, rng):
    """Create a random nested tree; return (all_files, all_dirs)."""
    files, dirs = [], []
    def populate(d, depth):
        dirs.append(d)
        n = rng.randint(0, 5)
        for i in range(n):
            p = os.path.join(d, f"f{i}.txt")
            with open(p, "w") as fh:
                fh.write(f"content {p}")
            files.append(p)
        if depth < 4:
            for i in range(rng.randint(0, 3)):
                sub = os.path.join(d, f"d{depth}_{i}_{rng.randint(0,999)}")
                os.makedirs(sub, exist_ok=True)
                populate(sub, depth + 1)
    populate(root, 0)
    return files, dirs


def make_dataset(files, dirs, rng):
    """Random modes: some folders fully uniform (collapsible), some mixed."""
    data = {}
    for d in dirs:
        r = rng.random()
        if r < 0.35:  # whole subtree 'open'
            for f in files:
                if f.startswith(d + os.sep) or os.path.dirname(f) == d:
                    if os.path.dirname(f).startswith(d):
                        data[os.path.normpath(f)] = "open"
        elif r < 0.5:  # whole subtree 'signature'
            for f in files:
                if os.path.dirname(f).startswith(d):
                    data[os.path.normpath(f)] = "signature"
    # Sprinkle mixed modes to break uniformity in places
    for f in files:
        r = rng.random()
        if r < 0.15:
            data[os.path.normpath(f)] = "signature"
        elif r < 0.2:
            data.pop(os.path.normpath(f), None)
    return {p: m for p, m in data.items() if os.path.isfile(p)}


def main():
    rng = random.Random(42)
    cases = 0
    for trial in range(60):
        tmp = tempfile.mkdtemp(prefix="cmp_")
        try:
            files, dirs = build_tree(tmp, rng)
            if not files:
                continue
            dataset = make_dataset(files, dirs, rng)
            old = ex_old.ActionManager._compact(dict(dataset))
            new = ex_new.ActionManager._compact(dict(dataset))
            # Keys must be compared in normalized form
            old_n = {os.path.normpath(k): v for k, v in old.items()}
            new_n = {os.path.normpath(k): v for k, v in new.items()}
            if old_n != new_n:
                print(f"TRIAL {trial}: MISMATCH")
                print("  old only:", sorted(set(old_n.items()) - set(new_n.items()))[:5])
                print("  new only:", sorted(set(new_n.items()) - set(old_n.items()))[:5])
                return 1
            # New implementation: expand(compact(data)) must return every file
            expanded = ex_new.ActionManager._expand(new_n)
            exp_n = {os.path.normpath(k) for k in expanded}
            lost = [p for p, m in dataset.items()
                    if os.path.normpath(p) not in exp_n]
            if lost:
                print(f"TRIAL {trial}: expand(compact) lost {lost[:3]}")
                return 1
            cases += 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # Deep-chain case (>100 levels): old caps at 100 iterations, new must finish.
    # Needs >= 2 files so _compact's len(result) <= 1 short-circuit doesn't hit.
    tmp = tempfile.mkdtemp(prefix="deep_")
    try:
        deep = tmp
        for i in range(120):
            deep = os.path.join(deep, f"lvl{i}")
        os.makedirs(deep)
        leaf = os.path.join(deep, "leaf.txt")
        with open(leaf, "w") as fh:
            fh.write("x")
        top = os.path.join(tmp, "lvl0")
        side = os.path.join(top, "side.txt")
        with open(side, "w") as fh:
            fh.write("y")
        dataset = {os.path.normpath(leaf): "open", os.path.normpath(side): "open"}
        new = ex_new.ActionManager._compact(dict(dataset))
        # Collapse propagates past lvl0 up to the tree root itself (its sole
        # on-disk child is lvl0, so it is uniform too).
        root_key = os.path.normpath(tmp)
        assert new == {root_key: "open"}, (
            f"deep chain did not fully collapse ({len(new)} entries): {list(new)[:3]}")
        old = ex_old.ActionManager._compact(dict(dataset))
        print(f"  (deep chain: new -> {len(new)} entries, old(capped) -> {len(old)} entries)")
        cases += 1
    finally:
        # shutil.rmtree struggles with very deep paths; walk up from the bottom
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except OSError:
            pass

    print(f"COMPACT EQUIVALENCE OK ({cases} cases, incl. 120-deep chain)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
