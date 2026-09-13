
# if hash-sum found - print is all ok
# else create hash-sum near files


import hashlib
from pathlib import Path
import sys

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

try:
    from path_args import resolve_paths, iter_existing_files
except ImportError:
    resolve_paths = None
    iter_existing_files = None

import argparse


def get_file_hash(file_path):
    hash_md5 = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def check_or_create_hash_in_directory(base_path):
    script_name = Path(__file__).name
    changed = False

    if iter_existing_files is not None:
        files = sorted(iter_existing_files([base_path], include_hidden=True))
    else:
        if base_path.is_file():
            files = [base_path]
        else:
            files = sorted(base_path.rglob("*"))

    for file_path in files:
        if file_path.name == script_name or file_path.suffix == ".md5":
            continue

        hash_file_path = file_path.with_suffix(file_path.suffix + ".md5")
        current_hash = get_file_hash(file_path)

        if hash_file_path.exists():
            stored_hash = hash_file_path.read_text().strip()
            if stored_hash == current_hash:
                print(f"{file_path.name} ok")
            else:
                print(f"{file_path.name} changed")
                changed = True
        else:
            hash_file_path.write_text(current_hash)
            print(f"for {file_path.name} created: {hash_file_path}")

    return changed


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?")
    args = parser.parse_args(argv)

    if resolve_paths is not None:
        resolved = resolve_paths(args, arg_names=("path",), fallback_to_cwd=True)
        any_changed = False
        for base_path in resolved.paths:
            if check_or_create_hash_in_directory(base_path):
                any_changed = True
    else:
        base_path = Path(args.path) if args.path else Path.cwd()
        if not base_path.exists():
            base_path = Path.cwd()

        any_changed = check_or_create_hash_in_directory(base_path)

    if any_changed:
        input("Press any key to close...")


if __name__ == "__main__":
    main()