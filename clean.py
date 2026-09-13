import shutil
from pathlib import Path

FOLDERS_TO_CLEAN = [
    "_standalone_dst_do_not_edit",
    "_terminal_scripts_dst_do_not_edit",
    Path("test_prompt") / "test_failures",
    Path("test_prompt") / "test_result",
]

def _clean_folder(target):
    target = Path(target)
    if not target.exists():
        print(f"Folder not found: {target}")
        return
    for entry in target.iterdir():
        if entry.is_file():
            entry.unlink()
            print(f"Removed file: {entry}")
        elif entry.is_dir():
            shutil.rmtree(entry)
            print(f"Removed directory: {entry}")

def remove_pycache(root="."):
    root = Path(root)
    for pattern in ["__pycache__", ".pytest_cache"]:
        for path in root.rglob(pattern):
            shutil.rmtree(path)
            print(f"Removed: {path}")
    for ext in ["*.pyc", "*.pyo"]:
        for path in root.rglob(ext):
            path.unlink()
            print(f"Removed: {path}")

if __name__ == "__main__":
    remove_pycache()
    for folder in FOLDERS_TO_CLEAN:
        _clean_folder(folder)
