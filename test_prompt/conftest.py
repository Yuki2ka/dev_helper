import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

dev_helper_dir = project_root / "dev_helper"
sys.path.insert(0, str(dev_helper_dir))

import path_args.command_paths as _command_paths


@contextmanager
def chdir(path: Path):
    old = Path.cwd()
    import os
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


@contextmanager
def _no_clipboard():
    with patch.object(_command_paths, "paths_from_clipboard", return_value=[]):
        yield


collect_ignore = [p.name for p in Path(__file__).parent.iterdir() if p.suffix not in {".py", ""} or (p.suffix == ".txt" and p.name != "pytest.ini")]
