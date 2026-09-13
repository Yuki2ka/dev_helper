import os
import shutil
import hashlib
import threading
import time
import tkinter as tk
import tkinter.font
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime
import platform
import subprocess
import send2trash
import stat as stat_module
from dataclasses import dataclass

# --- Cross-platform config ---
IS_WINDOWS = platform.system() == "Windows"
HOTKEY = 'ctrl+b'

if IS_WINDOWS:
    import win32clipboard
    import win32con
    import win32gui
    import win32com.client
    import win32file

# --- Reparse point constants ---
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
IO_REPARSE_TAG_SYMLINK = 0xA000000C
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
CREATE_NO_WINDOW = 0x08000000

# --- Link type identifiers ---
LINK_FILE = "file"
LINK_DIR = "dir"
LINK_SYMLINK = "symlink"
LINK_JUNCTION = "junction"
LINK_HARDLINK = "hardlink"

TYPE_LABELS = {
    LINK_FILE: "File",
    LINK_DIR: "Dir",
    LINK_SYMLINK: "Symlink",
    LINK_JUNCTION: "Junction",
    LINK_HARDLINK: "Hardlink",
}


class Role:
    SKIP = "skip"
    SOURCE = "source"
    DESTINATION = "destination"


class Capture:
    def __init__(self, cid: int, paths: list):
        self.id = cid
        self.paths = [Path(p) for p in paths if Path(p).exists() or Path(p).is_symlink()]
        self.role = Role.SKIP
        self.timestamp = datetime.now()

    @property
    def summary(self) -> str:
        if not self.paths:
            return "(empty)"
        names = []
        for p in self.paths[:3]:
            lt = get_link_type(p)
            if lt in (LINK_SYMLINK, LINK_JUNCTION):
                names.append(f"{p.name} ({TYPE_LABELS[lt]})")
            elif lt == LINK_HARDLINK:
                names.append(f"{p.name} (hardlink)")
            else:
                names.append(p.name)
        text = " | ".join(names)
        if len(self.paths) > 3:
            text += f" (+{len(self.paths)-3})"
        return text

    def signature(self):
        sigs = []
        for p in self.paths:
            try:
                sigs.append(str(p.resolve()))
            except Exception:
                sigs.append(str(p))
        return frozenset(sigs)


# --- Action Types ---
class Action:
    NEW = "new"
    UPDATED = "updated"
    DELETED = "deleted"
    UNCHANGED = "unchanged"


# --- Theme Constants ---
THEMES = {
    "Default Gray": {
        "SOURCE_BG": "#d4ffd4",
        "SOURCE_FG": "black",
        "DEST_BG": "#d4ecff",
        "DEST_FG": "black",
        "SKIP_BG": "#e6e6e6",
        "PREVIEW_BG": "#f5f5f5",
        "PREVIEW_ACTIVE_BG": "#d4ffd4",
        "TREE_TAGS": {
            Action.NEW: ("#d4ffd4", "#006600"),
            Action.UPDATED: ("#fff3cd", "#665200"),
            Action.DELETED: ("#ffd4d4", "#660000"),
            Action.UNCHANGED: ("#f5f5f5", "#666666"),
        },
    },
}


# --- SyncAction Dataclass ---
@dataclass(frozen=True)
class SyncAction:
    action: str
    rel: str
    is_dir: bool
    link_type: str = None
    target: str = None


# --- Capability caching ---
_CAP_SYMLINK = None
_CAP_HARDLINK = None


def _can_create_symlink_cached():
    global _CAP_SYMLINK
    if _CAP_SYMLINK is None:
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                src = Path(td) / "src.txt"
                src.write_text("x")
                lnk = Path(td) / "lnk"
                if IS_WINDOWS:
                    os.symlink(src, lnk, target_is_directory=False)
                else:
                    os.symlink(src, lnk)
            _CAP_SYMLINK = True
        except Exception:
            _CAP_SYMLINK = False
    return _CAP_SYMLINK


def _can_create_hardlink_cached():
    global _CAP_HARDLINK
    if _CAP_HARDLINK is None:
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                src = Path(td) / "src.txt"
                src.write_text("x")
                lnk = Path(td) / "lnk"
                os.link(src, lnk)
            _CAP_HARDLINK = True
        except Exception:
            _CAP_HARDLINK = False
    return _CAP_HARDLINK


def can_create_junction():
    return IS_WINDOWS


def can_create_symlink():
    return _can_create_symlink_cached()


def can_create_hardlink():
    return _can_create_hardlink_cached()


# --- Link type detection ---
def get_link_type(path: Path):
    """Classify a path. Returns one of LINK_* constants or None if non-existent."""
    if not path.exists() and not path.is_symlink():
        return None

    if IS_WINDOWS:
        # Check for reparse point (symlink/junction)
        try:
            attrs = win32file.GetFileAttributesW(str(path))
            if attrs != 0xFFFFFFFF and (attrs & FILE_ATTRIBUTE_REPARSE_POINT):
                tag = 0
                try:
                    st = os.lstat(path)
                    tag = getattr(st, 'st_reparse_tag', 0)
                except Exception:
                    pass
                if tag == IO_REPARSE_TAG_MOUNT_POINT:
                    return LINK_JUNCTION
                elif tag == IO_REPARSE_TAG_SYMLINK:
                    return LINK_SYMLINK
                else:
                    # Older Python without st_reparse_tag
                    if hasattr(os.path, 'isjunction') and os.path.isjunction(path):
                        return LINK_JUNCTION
                    try:
                        os.readlink(path)
                        return LINK_SYMLINK
                    except OSError:
                        return LINK_JUNCTION
        except Exception:
            pass

        # Check hardlink (files only)
        try:
            st = os.stat(path)
            if st.st_nlink > 1 and not stat_module.S_ISDIR(st.st_mode):
                return LINK_HARDLINK
        except Exception:
            pass

        try:
            if path.is_dir():
                return LINK_DIR
        except Exception:
            pass
        return LINK_FILE
    else:
        if path.is_symlink():
            return LINK_SYMLINK
        try:
            st = os.stat(path)
            if st.st_nlink > 1 and not stat_module.S_ISDIR(st.st_mode):
                return LINK_HARDLINK
        except Exception:
            pass
        return LINK_DIR if path.is_dir() else LINK_FILE


def get_link_target(path: Path):
    try:
        return os.readlink(path)
    except OSError:
        return None


def create_junction(target: str, link: Path):
    """Create a Windows directory junction via mklink /J (no admin needed)."""
    subprocess.run(
        ['cmd', '/c', 'mklink', '/J', str(link), str(target)],
        check=True, capture_output=True, creationflags=CREATE_NO_WINDOW
    )


def create_symlink(target: str, link: Path, target_is_dir: bool):
    if IS_WINDOWS:
        os.symlink(target, link, target_is_directory=target_is_dir)
    else:
        os.symlink(target, link)


def _delete_entry(p: Path, use_recycle: bool):
    """Delete an entry; for symlinks/junctions remove the link itself, NOT the target."""
    lt = get_link_type(p)
    if lt in (LINK_SYMLINK, LINK_JUNCTION):
        if use_recycle:
            try:
                send2trash.send2trash(str(p))
                return
            except Exception:
                pass
        try:
            if lt == LINK_JUNCTION:
                try:
                    os.rmdir(str(p))  # removes junction without touching target
                except OSError:
                    subprocess.run(['cmd', '/c', 'rmdir', str(p)],
                                   capture_output=True, creationflags=CREATE_NO_WINDOW)
            else:
                p.unlink()
            return
        except Exception:
            pass
    if use_recycle:
        send2trash.send2trash(str(p))
    else:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()


# --- Utility Functions ---
def get_file_hash(path: Path):
    hasher = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.digest()
    except Exception:
        return None


def _parse_path_lines(text: str) -> list:
    paths = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if (line.startswith('"') and line.endswith('"')) or \
           (line.startswith("'") and line.endswith("'")):
            line = line[1:-1]
        p = Path(line)
        if p.exists() or p.is_symlink():
            paths.append(p)
    return paths


def get_clipboard_files():
    paths = []
    system = platform.system()

    if system == "Windows":
        try:
            win32clipboard.OpenClipboard()
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                paths = [Path(f) for f in files]
            if not paths:
                fmt = None
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    fmt = win32con.CF_UNICODETEXT
                elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                    fmt = win32con.CF_TEXT
                if fmt:
                    try:
                        data = win32clipboard.GetClipboardData(fmt)
                        if isinstance(data, bytes):
                            data = data.decode('utf-8', errors='ignore')
                        paths = _parse_path_lines(data)
                    except Exception:
                        pass
            win32clipboard.CloseClipboard()
        except Exception:
            pass
    elif system == "Linux":
        text_data = None
        try:
            if shutil.which("wl-paste"):
                try:
                    output = subprocess.check_output(
                        ["wl-paste", "--type", "text/uri-list"],
                        stderr=subprocess.DEVNULL, timeout=0.6
                    ).decode('utf-8', errors='ignore')
                    for line in output.strip().split('\n'):
                        if line.startswith('file://'):
                            p = Path(line[7:].strip())
                            if p.exists() or p.is_symlink():
                                paths.append(p)
                except Exception:
                    pass
            elif shutil.which("xclip"):
                try:
                    output = subprocess.check_output(
                        ["xclip", "-o", "-selection", "clipboard", "-t", "text/uri-list"],
                        stderr=subprocess.DEVNULL, timeout=0.6
                    ).decode('utf-8', errors='ignore')
                    for line in output.strip().split('\n'):
                        if line.startswith('file://'):
                            p = Path(line[7:].strip())
                            if p.exists() or p.is_symlink():
                                paths.append(p)
                except Exception:
                    pass
            if not paths:
                if shutil.which("wl-paste"):
                    try:
                        text_data = subprocess.check_output(
                            ["wl-paste", "--type", "text/plain"],
                            stderr=subprocess.DEVNULL, timeout=0.6
                        ).decode('utf-8', errors='ignore')
                    except Exception:
                        pass
                if not text_data and shutil.which("xclip"):
                    try:
                        text_data = subprocess.check_output(
                            ["xclip", "-o", "-selection", "clipboard", "-t", "text/plain"],
                            stderr=subprocess.DEVNULL, timeout=0.6
                        ).decode('utf-8', errors='ignore')
                    except Exception:
                        pass
                if text_data:
                    paths = _parse_path_lines(text_data)
        except Exception:
            pass

    return paths


def get_active_explorer_path():
    if not IS_WINDOWS:
        return None
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        hwnd = win32gui.GetForegroundWindow()
        for window in shell.Windows():
            if window.hwnd == hwnd:
                return window.Document.Folder.Self.Path
    except Exception:
        pass
    return None


# --- Preview / Plan ---
def _plan_sync_item(src_path: Path, dst_path: Path, skip_unchanged: bool, prefix: str) -> list:
    plan = []
    name = src_path.name
    full_rel = prefix + "/" + name if prefix else name
    link_type = get_link_type(src_path)

    # Symlink or junction: treat as link entity, don't recurse
    if link_type in (LINK_SYMLINK, LINK_JUNCTION):
        src_target = get_link_target(src_path)
        dst_exists = dst_path.exists() or dst_path.is_symlink()

        if not dst_exists:
            plan.append(SyncAction(action=Action.NEW, rel=full_rel, is_dir=False,
                                   link_type=link_type, target=src_target))
        else:
            dst_link = get_link_type(dst_path)
            dst_target = get_link_target(dst_path) if dst_link in (LINK_SYMLINK, LINK_JUNCTION) else None

            if dst_link == link_type and dst_target == src_target:
                if not skip_unchanged:
                    plan.append(SyncAction(action=Action.UNCHANGED, rel=full_rel, is_dir=False,
                                           link_type=link_type, target=src_target))
            else:
                plan.append(SyncAction(action=Action.DELETED, rel=full_rel,
                                       is_dir=(dst_link == LINK_DIR), link_type=dst_link, target=dst_target))
                plan.append(SyncAction(action=Action.NEW, rel=full_rel, is_dir=False,
                                       link_type=link_type, target=src_target))
        return plan

    # Hardlink: file but tracked
    if link_type == LINK_HARDLINK:
        dst_exists = dst_path.exists() or dst_path.is_symlink()
        if not dst_exists:
            plan.append(SyncAction(action=Action.NEW, rel=full_rel, is_dir=False, link_type=LINK_HARDLINK))
        else:
            dst_lt = get_link_type(dst_path)
            if dst_lt == LINK_DIR:
                plan.append(SyncAction(action=Action.DELETED, rel=full_rel, is_dir=True, link_type=LINK_DIR))
                plan.append(SyncAction(action=Action.NEW, rel=full_rel, is_dir=False, link_type=LINK_HARDLINK))
            elif dst_lt in (LINK_SYMLINK, LINK_JUNCTION):
                plan.append(SyncAction(action=Action.DELETED, rel=full_rel, is_dir=False, link_type=dst_lt))
                plan.append(SyncAction(action=Action.NEW, rel=full_rel, is_dir=False, link_type=LINK_HARDLINK))
            else:
                # Both files (possibly hardlinks). Same-inode check first.
                try:
                    src_st = os.stat(src_path)
                    dst_st = os.stat(dst_path)
                    if src_st.st_dev == dst_st.st_dev and src_st.st_ino == dst_st.st_ino:
                        if not skip_unchanged:
                            plan.append(SyncAction(action=Action.UNCHANGED, rel=full_rel, is_dir=False, link_type=LINK_HARDLINK))
                        return plan
                except Exception:
                    pass
                h1 = get_file_hash(src_path)
                h2 = get_file_hash(dst_path)
                if h1 == h2:
                    if not skip_unchanged:
                        plan.append(SyncAction(action=Action.UNCHANGED, rel=full_rel, is_dir=False, link_type=LINK_HARDLINK))
                else:
                    plan.append(SyncAction(action=Action.UPDATED, rel=full_rel, is_dir=False, link_type=LINK_HARDLINK))
        return plan

    # Regular dir/file
    if src_path.is_dir():
        dst_exists = dst_path.exists() or dst_path.is_symlink()
        dst_lt = get_link_type(dst_path) if dst_exists else None

        if not dst_exists:
            plan.append(SyncAction(action=Action.NEW, rel=full_rel, is_dir=True, link_type=LINK_DIR))
            try:
                for entry in sorted(os.scandir(src_path), key=lambda e: e.name):
                    plan.extend(_plan_sync_item(Path(entry.path), dst_path / entry.name, skip_unchanged, full_rel))
            except PermissionError:
                pass
        elif dst_lt == LINK_DIR:
            # Real dir, merge
            src_entries = sorted(os.scandir(src_path), key=lambda e: e.name)
            dst_items = {e.name: Path(e.path) for e in os.scandir(dst_path)}
            src_names = {e.name for e in src_entries}
            for dst_name, dst_p in dst_items.items():
                if dst_name not in src_names:
                    d_lt = get_link_type(dst_p) or (LINK_DIR if dst_p.is_dir() else LINK_FILE)
                    plan.append(SyncAction(action=Action.DELETED,
                                          rel=(prefix + "/" + dst_name) if prefix else dst_name,
                                          is_dir=(d_lt == LINK_DIR), link_type=d_lt))
            for entry in src_entries:
                plan.extend(_plan_sync_item(Path(entry.path), dst_path / entry.name, skip_unchanged, full_rel))
        else:
            # dst is file/symlink/junction/hardlink, replace with dir
            plan.append(SyncAction(action=Action.DELETED, rel=full_rel, is_dir=False, link_type=dst_lt))
            plan.append(SyncAction(action=Action.NEW, rel=full_rel, is_dir=True, link_type=LINK_DIR))
            try:
                for entry in sorted(os.scandir(src_path), key=lambda e: e.name):
                    plan.extend(_plan_sync_item(Path(entry.path), dst_path / entry.name, skip_unchanged, full_rel))
            except PermissionError:
                pass
    else:
        # Regular file
        dst_exists = dst_path.exists() or dst_path.is_symlink()
        dst_lt = get_link_type(dst_path) if dst_exists else None

        if not dst_exists:
            plan.append(SyncAction(action=Action.NEW, rel=full_rel, is_dir=False, link_type=LINK_FILE))
        elif dst_lt == LINK_DIR:
            plan.append(SyncAction(action=Action.DELETED, rel=full_rel, is_dir=True, link_type=LINK_DIR))
            plan.append(SyncAction(action=Action.NEW, rel=full_rel, is_dir=False, link_type=LINK_FILE))
        elif dst_lt in (LINK_SYMLINK, LINK_JUNCTION):
            # Type differs - replace
            plan.append(SyncAction(action=Action.DELETED, rel=full_rel, is_dir=False, link_type=dst_lt))
            plan.append(SyncAction(action=Action.NEW, rel=full_rel, is_dir=False, link_type=LINK_FILE))
        else:
            h1 = get_file_hash(src_path)
            h2 = get_file_hash(dst_path)
            if h1 == h2:
                if not skip_unchanged:
                    plan.append(SyncAction(action=Action.UNCHANGED, rel=full_rel, is_dir=False, link_type=LINK_FILE))
            else:
                plan.append(SyncAction(action=Action.UPDATED, rel=full_rel, is_dir=False, link_type=LINK_FILE))

    return plan


FILE_THRESHOLD = 30


def _analyze_plan(plan: list):
    counts = {Action.NEW: 0, Action.UPDATED: 0, Action.DELETED: 0, Action.UNCHANGED: 0}
    action_map = {}
    deleted_map = {}
    for item in plan:
        counts[item.action] += 1
        action_map[item.rel] = item
        if item.action == Action.DELETED:
            parts = item.rel.split("/")
            parent_rel = "/".join(parts[:-1]) if len(parts) > 1 else ""
            deleted_map.setdefault(parent_rel, []).append(parts[-1])
    return counts, action_map, deleted_map


def compute_sync_plan(source: Capture, dest: Capture, skip_unchanged: bool) -> list:
    plan = []
    dst_base = dest.paths[0] if dest.paths else None
    if not dst_base or not dst_base.is_dir():
        return plan

    keep_names = set()
    for src_path in source.paths:
        lt = get_link_type(src_path)
        if lt in (LINK_SYMLINK, LINK_JUNCTION, LINK_HARDLINK):
            keep_names.add(src_path.name)
            plan.extend(_plan_sync_item(src_path, dst_base / src_path.name, skip_unchanged, ""))
        elif src_path.is_dir():
            try:
                for entry in sorted(os.scandir(src_path), key=lambda e: e.name):
                    keep_names.add(entry.name)
                    dst_path = dst_base / entry.name
                    plan.extend(_plan_sync_item(Path(entry.path), dst_path, skip_unchanged, ""))
            except PermissionError:
                pass
        else:
            keep_names.add(src_path.name)
            dst_path = dst_base / src_path.name
            plan.extend(_plan_sync_item(src_path, dst_path, skip_unchanged, ""))

    def add_deleted_rec(base: Path, prefix: str):
        try:
            for entry in sorted(os.scandir(base), key=lambda e: e.name):
                child_rel = (prefix + "/" + entry.name) if prefix else entry.name
                p = Path(entry.path)
                lt = get_link_type(p) or (LINK_DIR if entry.is_dir(follow_symlinks=False) else LINK_FILE)
                is_dir = (lt == LINK_DIR)
                plan.append(SyncAction(action=Action.DELETED, rel=child_rel, is_dir=is_dir, link_type=lt))
                if is_dir:
                    add_deleted_rec(p, child_rel)
        except PermissionError:
            pass

    for entry in os.scandir(dst_base):
        name = entry.name
        if name not in keep_names:
            p = Path(entry.path)
            lt = get_link_type(p) or (LINK_DIR if entry.is_dir(follow_symlinks=False) else LINK_FILE)
            is_dir = (lt == LINK_DIR)
            plan.append(SyncAction(action=Action.DELETED, rel=name, is_dir=is_dir, link_type=lt))
            if is_dir:
                add_deleted_rec(p, name)

    return plan


# --- Sync Logic ---
def sync_item(src: Path, dst: Path, use_recycle: bool, hardlink_map: dict, link_conversion: str):
    """Mirror a single file/folder/link. link_conversion: 'preserve'|'symlink'|'file'."""
    link_type = get_link_type(src)

    if link_type in (LINK_SYMLINK, LINK_JUNCTION):
        return _sync_link(src, dst, use_recycle, link_type, link_conversion)

    if link_type == LINK_HARDLINK:
        return _sync_hardlink(src, dst, use_recycle, hardlink_map, link_conversion)

    # Regular file/dir
    if src.is_dir():
        dst_exists = dst.exists() or dst.is_symlink()
        dst_lt = get_link_type(dst) if dst_exists else None

        if dst_lt not in (None, LINK_DIR):
            # dst is a non-dir entry (file/symlink/junction/hardlink) -> replace
            _delete_entry(dst, use_recycle)
            os.makedirs(dst, exist_ok=True)
        elif dst_lt is None:
            os.makedirs(dst, exist_ok=True)
        # else: real dir, merge

        src_items = {entry.name: Path(entry.path) for entry in os.scandir(src)}
        for entry in list(os.scandir(dst)):
            if entry.name not in src_items:
                _delete_entry(Path(entry.path), use_recycle)
        for name, src_path in src_items.items():
            sync_item(src_path, dst / name, use_recycle, hardlink_map, link_conversion)
    else:
        dst_exists = dst.exists() or dst.is_symlink()
        dst_lt = get_link_type(dst) if dst_exists else None

        if dst_lt in (LINK_SYMLINK, LINK_JUNCTION, LINK_DIR):
            # Type differs from regular file -> replace
            _delete_entry(dst, use_recycle)
        elif dst_lt is not None:
            # Both files (or hardlink) -> hash compare
            if get_file_hash(src) == get_file_hash(dst):
                return "skipped"
            _delete_entry(dst, use_recycle)
        os.makedirs(dst.parent, exist_ok=True)
        shutil.copy2(src, dst)
        return "copied"


def _sync_link(src: Path, dst: Path, use_recycle: bool, link_type: str, link_conversion: str):
    target = get_link_target(src)

    can_preserve_native = (link_type == LINK_SYMLINK and can_create_symlink()) or \
                          (link_type == LINK_JUNCTION and can_create_junction())

    # 1) Try native preservation (only in 'preserve' mode)
    if link_conversion == 'preserve' and can_preserve_native:
        try:
            if dst.exists() or dst.is_symlink():
                _delete_entry(dst, use_recycle)
            if link_type == LINK_SYMLINK:
                target_is_dir = False
                try:
                    target_is_dir = Path(target).is_dir()
                except Exception:
                    pass
                create_symlink(target, dst, target_is_dir)
            else:
                create_junction(target, dst)
            return "copied"
        except Exception:
            pass  # fall through to conversion strategies

    # 2) Convert to symlink (if requested and possible)
    if link_conversion == 'symlink' and can_create_symlink():
        try:
            if dst.exists() or dst.is_symlink():
                _delete_entry(dst, use_recycle)
            target_is_dir = False
            try:
                target_is_dir = Path(target).is_dir()
            except Exception:
                pass
            create_symlink(target, dst, target_is_dir)
            return "copied"
        except Exception:
            pass  # fall through to file copy

    # 3) Convert to file (dereference and copy content)
    try:
        resolved = src.resolve()
        if not resolved.exists() and not resolved.is_symlink():
            return "skipped"  # broken link
        if dst.exists() or dst.is_symlink():
            _delete_entry(dst, use_recycle)
        if resolved.is_dir():
            shutil.copytree(resolved, dst)
        else:
            os.makedirs(dst.parent, exist_ok=True)
            shutil.copy2(resolved, dst)
        return "copied"
    except Exception as e:
        raise


def _sync_hardlink(src: Path, dst: Path, use_recycle: bool, hardlink_map: dict, link_conversion: str):
    # Handle conversion options
    if link_conversion == 'symlink':
        # Convert to symlink pointing to the source file
        try:
            if dst.exists() or dst.is_symlink():
                _delete_entry(dst, use_recycle)
            os.makedirs(dst.parent, exist_ok=True)
            create_symlink(src.resolve(), dst, target_is_dir=False)
            return "copied"
        except Exception:
            return "skipped"
    if link_conversion == 'copy':
        # Just copy as regular file
        dst_exists = dst.exists() or dst.is_symlink()
        dst_lt = get_link_type(dst) if dst_exists else None
        if dst_lt == LINK_FILE and get_file_hash(src) == get_file_hash(dst):
            return "skipped"
        if dst_lt is not None:
            _delete_entry(dst, use_recycle)
        os.makedirs(dst.parent, exist_ok=True)
        shutil.copy2(src, dst)
        return "copied"
    if link_conversion == 'skip':
        return "skipped"

    # Preserve mode: try hardlink, fallback to copy
    if can_create_hardlink():
        try:
            st = os.stat(src)
            key = (st.st_dev, st.st_ino)
            if key in hardlink_map:
                # Create hardlink to existing dest file
                if dst.exists() or dst.is_symlink():
                    _delete_entry(dst, use_recycle)
                try:
                    os.link(hardlink_map[key], dst)
                    return "copied"
                except Exception:
                    # If hardlink fails (e.g., fs restriction), copy and update map
                    shutil.copy2(hardlink_map[key], dst)
                    return "copied"
            else:
                # First occurrence: try hardlink to source, fallback to copy
                dst_exists = dst.exists() or dst.is_symlink()
                dst_lt = get_link_type(dst) if dst_exists else None
                if dst_lt == LINK_FILE and get_file_hash(src) == get_file_hash(dst):
                    hardlink_map[key] = dst
                    return "skipped"
                if dst_lt is not None:
                    _delete_entry(dst, use_recycle)
                os.makedirs(dst.parent, exist_ok=True)
                try:
                    os.link(src, dst)
                    hardlink_map[key] = dst
                    return "copied"
                except Exception:
                    shutil.copy2(src, dst)
                    hardlink_map[key] = dst
                    return "copied"
        except Exception:
            pass
    # Fallback: regular file copy
    dst_exists = dst.exists() or dst.is_symlink()
    dst_lt = get_link_type(dst) if dst_exists else None
    if dst_lt == LINK_FILE and get_file_hash(src) == get_file_hash(dst):
        return "skipped"
    if dst_lt is not None:
        _delete_entry(dst, use_recycle)
    os.makedirs(dst.parent, exist_ok=True)
    shutil.copy2(src, dst)
    return "copied"


def find_non_preservable_links(source: Capture) -> list:
    """Walk source and find links that cannot be preserved on current OS/config."""
    non_preservable = []
    seen = set()

    def check(p: Path):
        try:
            rp = str(p.resolve()) if p.exists() else str(p)
        except Exception:
            rp = str(p)
        if rp in seen:
            return
        lt = get_link_type(p)
        if lt == LINK_SYMLINK and not can_create_symlink():
            non_preservable.append(p)
            seen.add(rp)
        elif lt == LINK_JUNCTION and not can_create_junction():
            non_preservable.append(p)
            seen.add(rp)

    def walk(p: Path):
        lt = get_link_type(p)
        if lt in (LINK_SYMLINK, LINK_JUNCTION):
            check(p)
            return  # don't recurse into links
        if p.is_dir():
            try:
                for entry in os.scandir(p):
                    walk(Path(entry.path))
            except PermissionError:
                pass
        else:
            check(p)

    for src_path in source.paths:
        lt = get_link_type(src_path)
        if lt in (LINK_SYMLINK, LINK_JUNCTION):
            check(src_path)
        elif src_path.is_dir():
            try:
                for entry in os.scandir(src_path):
                    walk(Path(entry.path))
            except PermissionError:
                pass
        else:
            check(src_path)

    return non_preservable


def find_cross_volume_hardlinks(source: Capture, dest_base: Path) -> list:
    """Find hardlinks in source that cannot be hardlinked to dest (cross-volume on Windows)."""
    cross_volume = []
    seen = set()

    # Get destination volume (root of sync target)
    dest_volume = None
    if IS_WINDOWS and dest_base.exists() or dest_base.parent.exists():
        try:
            dest_check = dest_base if dest_base.exists() else dest_base.parent
            dest_st = os.stat(dest_check)
            dest_volume = dest_st.st_dev
        except Exception:
            pass

    def check(p: Path):
        try:
            st = os.stat(p)
            if st.st_nlink > 1 and not stat_module.S_ISDIR(st.st_mode):
                if dest_volume is not None and st.st_dev != dest_volume:
                    if str(p) not in seen:
                        cross_volume.append(p)
                        seen.add(str(p))
        except Exception:
            pass

    def walk(p: Path):
        lt = get_link_type(p)
        if lt == LINK_HARDLINK:
            check(p)
            return
        if p.is_dir():
            try:
                for entry in os.scandir(p):
                    walk(Path(entry.path))
            except PermissionError:
                pass
        else:
            check(p)

    for src_path in source.paths:
        lt = get_link_type(src_path)
        if lt == LINK_HARDLINK:
            check(src_path)
        elif src_path.is_dir():
            try:
                for entry in os.scandir(src_path):
                    walk(Path(entry.path))
            except PermissionError:
                pass
        else:
            check(src_path)

    return cross_volume


def prompt_hardlink_conversion(gui, cross_volume_hardlinks: list) -> str:
    """Show modal dialog for cross-volume hardlinks. Returns 'symlink'|'copy'|'skip'|'cancel'|None."""
    result = {'choice': None}
    done = threading.Event()

    def show_dialog():
        dialog = tk.Toplevel(gui.root)
        dialog.title("Cross-volume hardlinks detected")
        dialog.transient(gui.root)
        dialog.grab_set()
        dialog.configure(bg="#f5f5f5")
        dialog.resizable(True, True)

        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill="both", expand=True)

        msg = (
            f"Found {len(cross_volume_hardlinks)} hardlink(s) on a different volume\n"
            "than the destination. Hardlinks cannot cross volumes on Windows.\n\n"
            "How should they be handled?\n\n"
            "Examples:\n"
        )
        for p in cross_volume_hardlinks[:5]:
            msg += f"  • {p} (hardlink)\n"
        if len(cross_volume_hardlinks) > 5:
            msg += f"  ... and {len(cross_volume_hardlinks) - 5} more\n"

        ttk.Label(frame, text=msg, justify="left").pack(anchor="w", pady=(0, 10))

        can_sym = can_create_symlink()

        def on_choice(c):
            result['choice'] = c
            dialog.destroy()
            done.set()

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x")

        b_symlink = ttk.Button(btn_frame, text="Convert to Symlinks",
                               command=lambda: on_choice('symlink'))
        if not can_sym:
            b_symlink.state(['disabled'])
        b_symlink.pack(side="left", padx=5)

        ttk.Button(btn_frame, text="Copy as Files",
                   command=lambda: on_choice('copy')).pack(side="left", padx=5)

        ttk.Button(btn_frame, text="Skip All",
                   command=lambda: on_choice('skip')).pack(side="left", padx=5)

        ttk.Button(btn_frame, text="Cancel Sync",
                   command=lambda: on_choice('cancel')).pack(side="right", padx=5)

        dialog.protocol("WM_DELETE_WINDOW", lambda: on_choice('cancel'))

        dialog.update_idletasks()
        x = gui.root.winfo_x() + (gui.root.winfo_width() - dialog.winfo_width()) // 2
        y = gui.root.winfo_y() + (gui.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0,x)}+{max(0,y)}")

        dialog.wait_window()
        if not done.is_set():
            done.set()

    gui.root.after(0, show_dialog)
    done.wait()
    return result['choice']


def prompt_link_conversion(gui, non_preservable_links: list) -> str:
    """Show modal dialog (on GUI thread), block sync thread. Returns 'symlink'|'file'|'cancel'|None."""
    result = {'choice': None}
    done = threading.Event()

    def show_dialog():
        dialog = tk.Toplevel(gui.root)
        dialog.title("Non-preservable links detected")
        dialog.transient(gui.root)
        dialog.grab_set()
        dialog.configure(bg="#f5f5f5")
        dialog.resizable(True, True)

        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill="both", expand=True)

        msg = (
            f"Found {len(non_preservable_links)} link(s) that cannot be preserved\n"
            "on the current OS/configuration. How should they be handled?\n\n"
            "Examples:\n"
        )
        for p in non_preservable_links[:5]:
            lt = get_link_type(p)
            msg += f"  • {p}  ({TYPE_LABELS.get(lt, '?')})\n"
        if len(non_preservable_links) > 5:
            msg += f"  ... and {len(non_preservable_links) - 5} more\n"

        ttk.Label(frame, text=msg, justify="left").pack(anchor="w", pady=(0, 10))

        can_sym = can_create_symlink()

        def on_choice(c):
            result['choice'] = c
            dialog.destroy()
            done.set()

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x")

        b_symlink = ttk.Button(btn_frame, text="Convert to Symlinks",
                               command=lambda: on_choice('symlink'))
        if not can_sym:
            b_symlink.state(['disabled'])
        b_symlink.pack(side="left", padx=5)

        ttk.Button(btn_frame, text="Convert to Files",
                   command=lambda: on_choice('file')).pack(side="left", padx=5)

        ttk.Button(btn_frame, text="Cancel Sync",
                   command=lambda: on_choice('cancel')).pack(side="right", padx=5)

        dialog.protocol("WM_DELETE_WINDOW", lambda: on_choice('cancel'))

        dialog.update_idletasks()
        x = gui.root.winfo_x() + (gui.root.winfo_width() - dialog.winfo_width()) // 2
        y = gui.root.winfo_y() + (gui.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0,x)}+{max(0,y)}")

        dialog.wait_window()
        if not done.is_set():
            done.set()

    gui.root.after(0, show_dialog)
    done.wait()
    return result['choice']


def execute_sync(gui, source_capture: Capture, dest_captures: list):
    if not source_capture or not dest_captures:
        gui.set_status("Nothing to sync (need 1 source + ≥1 destination)", "red")
        return

    link_conversion = 'preserve'
    hardlink_conversion = 'preserve'

    # Pre-check for non-preservable links BEFORE any file op
    non_preservable = find_non_preservable_links(source_capture)
    if non_preservable:
        gui.set_status(f"Found {len(non_preservable)} non-preservable link(s). Awaiting user decision...", "orange")
        choice = prompt_link_conversion(gui, non_preservable)
        if choice == 'cancel' or choice is None:
            gui.set_status("Sync cancelled by user.", "orange")
            return
        link_conversion = choice

    # Pre-check for cross-volume hardlinks (Windows only)
    if IS_WINDOWS:
        all_cross_vol = []
        for dest_capture in dest_captures:
            dest_base = dest_capture.paths[0] if dest_capture.paths else None
            if dest_base and dest_base.is_dir():
                cross_vol = find_cross_volume_hardlinks(source_capture, dest_base)
                for p in cross_vol:
                    if str(p) not in [str(x) for x in all_cross_vol]:
                        all_cross_vol.append(p)
        if all_cross_vol and hardlink_conversion == 'preserve':
            gui.set_status(f"Found {len(all_cross_vol)} cross-volume hardlink(s). Awaiting user decision...", "orange")
            choice = prompt_hardlink_conversion(gui, all_cross_vol)
            if choice == 'cancel' or choice is None:
                gui.set_status("Sync cancelled by user.", "orange")
                return
            hardlink_conversion = choice

    gui.set_status("Syncing...", "blue")
    copied = skipped = deleted = 0

    try:
        for dest_capture in dest_captures:
            dest_base = dest_capture.paths[0] if dest_capture.paths else None
            if not dest_base or not dest_base.is_dir():
                continue

            hardlink_map = {}  # (st_dev, st_ino) -> first dest path
            keep_names = set()

            for item in source_capture.paths:
                lt = get_link_type(item)
                if lt in (LINK_SYMLINK, LINK_JUNCTION, LINK_HARDLINK):
                    keep_names.add(item.name)
                    conv = hardlink_conversion if lt == LINK_HARDLINK else link_conversion
                    res = sync_item(item, dest_base / item.name, gui.use_recycle_bin, hardlink_map, conv)
                    if res == "copied": copied += 1
                    elif res == "skipped": skipped += 1
                elif item.is_dir():
                    try:
                        for entry in os.scandir(item):
                            keep_names.add(entry.name)
                            src_entry = Path(entry.path)
                            entry_lt = get_link_type(src_entry)
                            conv = hardlink_conversion if entry_lt == LINK_HARDLINK else link_conversion
                            res = sync_item(src_entry, dest_base / entry.name, gui.use_recycle_bin, hardlink_map, conv)
                            if res == "copied": copied += 1
                            elif res == "skipped": skipped += 1
                    except PermissionError:
                        pass
                else:
                    keep_names.add(item.name)
                    conv = hardlink_conversion if lt == LINK_HARDLINK else link_conversion
                    res = sync_item(item, dest_base / item.name, gui.use_recycle_bin, hardlink_map, conv)
                    if res == "copied": copied += 1
                    elif res == "skipped": skipped += 1

            for entry in os.scandir(dest_base):
                if entry.name not in keep_names:
                    _delete_entry(Path(entry.path), gui.use_recycle_bin)
                    deleted += 1

        gui.set_status(f"Sync complete! Copied: {copied}, Skipped: {skipped}, Deleted: {deleted}", "green")
        gui.reset_all_to_skip()

    except Exception as e:
        gui.set_status(f"Error: {e}", "red")


# --- GUI ---
class SyncGui:
    def __init__(self, root):
        self.root = root
        self.root.title("sync_copy - Smart Clipboard Sync")
        self.root.configure(background="#d3d3d3")
        self.root.geometry("920x680")
        self.root.minsize(800, 500)
        self.root.attributes("-topmost", True)

        self.captures = []
        self.next_id = 1
        self.seen_signatures = set()
        self.use_recycle_bin = True
        self.skip_unchanged_var = tk.BooleanVar(value=True)
        self.active_preview_id = None
        self._shutdown_event = threading.Event()

        self._build_ui()
        self._start_workers()

    def _build_ui(self):
        style = ttk.Style()
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), foreground="white", background="#007acc")
        style.map("Accent.TButton", background=[("active", "#005a9e")], foreground=[("disabled", "gray")])
        default_font = tk.font.nametofont("TkDefaultFont")
        style.configure("Custom.Treeview", background="#f5f5f5", foreground="black", fieldbackground="#f5f5f5", rowheight=max(18, default_font.metrics('linespace')))
        style.configure("Custom.Treeview.Heading", background="#d3d3d3", foreground="black")
        style.configure("TLabel", background="#d3d3d3", foreground="black")
        style.configure("TFrame", background="#d3d3d3")

        self.status_lbl = tk.Label(self.root, text="Ready. Copy something with Ctrl+C.",
                                   fg="gray", anchor="center")
        self.status_lbl.pack(fill="x", padx=20, pady=(10, 5))

        ctrl_frame = ttk.Frame(self.root)
        ctrl_frame.pack(fill="x", padx=15, pady=(0, 8))

        ttk.Separator(ctrl_frame, orient="horizontal").pack(fill="x", pady=(0, 10))

        self.capture_count_lbl = ttk.Label(ctrl_frame, text="0 captures")
        self.capture_count_lbl.pack(side="left", padx=(0, 5))

        ttk.Checkbutton(ctrl_frame, text="Skip Unchanged", variable=self.skip_unchanged_var).pack(side="left", padx=(5, 0))

        self.recycle_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctrl_frame, text="Use Recycle Bin", variable=self.recycle_var,
                        command=self._toggle_recycle).pack(side="right", padx=(5, 0))

        ttk.Button(ctrl_frame, text="Clear List", command=self.clear_list).pack(side="right", padx=5)
        ttk.Button(ctrl_frame, text="Sync Now", command=self.start_sync, style="Accent.TButton").pack(side="right", padx=(0, 10))

        list_frame = ttk.Frame(self.root)
        list_frame.pack(fill="both", expand=True, padx=15, pady=(0, 5))

        self.canvas = tk.Canvas(list_frame, highlightthickness=0, bg="#d3d3d3")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas_window_id = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")

        def _on_canvas_configure(event):
            self.canvas.itemconfig(self.canvas_window_id, width=event.width)
        self.canvas.bind("<Configure>", _on_canvas_configure)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y", padx=(3, 0))

    def _toggle_recycle(self):
        self.use_recycle_bin = self.recycle_var.get()

    def _is_window_alive(self) -> bool:
        if self._shutdown_event.is_set():
            return False
        try:
            return self.root.winfo_exists() == 1
        except tk.TclError:
            return False

    def _get_theme(self, key):
        return THEMES["Default Gray"][key]

    def add_capture(self, paths: list):
        if not paths or not self._is_window_alive():
            return
        try:
            self.root.winfo_width()
        except tk.TclError:
            return
        capture = Capture(self.next_id, paths)
        sig = capture.signature()
        if sig in self.seen_signatures:
            return
        self.seen_signatures.add(sig)
        self.captures.append(capture)
        self.next_id += 1
        if len(self.captures) == 1:
            capture.role = Role.SOURCE
        else:
            capture.role = Role.DESTINATION
        self._rebuild_capture_list()

    def _rebuild_capture_list(self):
        try:
            for widget in self.scrollable_frame.winfo_children():
                widget.destroy()
        except tk.TclError:
            return

        if not self.captures:
            empty = tk.Frame(self.scrollable_frame, bg="#d3d3d3", relief="flat")
            empty.pack(expand=True, fill="both", pady=60)
            tk.Label(empty,
                     text="Press Ctrl+C to copy files/folders → they appear below.\n"
                          "Set one Source and any number of Destinations.\n"
                          "warning Destinations will become same as source",
                     justify="center", foreground="gray", bg="#d3d3d3").pack()
            try:
                self.capture_count_lbl.config(text="0 captures")
            except tk.TclError:
                pass
            return

        for capture in self.captures:
            panel_bg = self._get_theme("SKIP_BG")
            row = tk.Frame(self.scrollable_frame, bg=panel_bg, relief="flat", borderwidth=0, padx=12, pady=10)
            row.pack(fill="x", pady=2, padx=6)
            row.columnconfigure(4, weight=1)

            id_lbl = tk.Label(row, text=f"#{capture.id}", width=4, anchor="w",
                              font=("Consolas", 9), bg=panel_bg)
            id_lbl.grid(row=0, column=0, sticky="nsw", padx=(0, 5))

            def make_handler(c, r):
                return lambda: self.set_role(c, r)

            def make_preview_handler(c):
                return lambda: self._preview_for_capture(c)

            b_source = tk.Button(row, text="Source", width=8, command=make_handler(capture, Role.SOURCE),
                                 relief="flat", bg=panel_bg, borderwidth=0, padx=5, pady=2, font=("Segoe UI", 9))
            b_source.grid(row=0, column=1, sticky="nsw", padx=2)

            b_dest = tk.Button(row, text="Destination", width=10, command=make_handler(capture, Role.DESTINATION),
                               relief="flat", bg=panel_bg, borderwidth=0, padx=5, pady=2, font=("Segoe UI", 9))
            b_dest.grid(row=0, column=2, sticky="nsw", padx=2)

            b_preview = tk.Button(row, text="Preview", width=8, command=make_preview_handler(capture),
                                  relief="flat", bg=panel_bg, borderwidth=0, padx=5, pady=2, font=("Segoe UI", 9))
            b_preview.grid(row=0, column=3, sticky="nsw", padx=2)

            content_lbl = tk.Label(row, text=capture.summary, anchor="w",
                                   bg=panel_bg, font=("Segoe UI", 10))
            content_lbl.grid(row=0, column=4, sticky="ew", padx=(12, 0))

            role_styles = {
                Role.SOURCE: {"bg": self._get_theme("SOURCE_BG"), "fg": self._get_theme("SOURCE_FG"), "relief": "flat", "bold": True},
                Role.DESTINATION: {"bg": self._get_theme("DEST_BG"), "fg": self._get_theme("DEST_FG"), "relief": "flat", "bold": True},
                Role.SKIP: {"bg": self._get_theme("SKIP_BG"), "fg": "black", "relief": "flat", "bold": False},
                "preview": {"bg": self._get_theme("PREVIEW_BG"), "fg": "black", "relief": "flat", "bold": False},
                "preview_active": {"bg": self._get_theme("PREVIEW_ACTIVE_BG"), "fg": self._get_theme("SOURCE_FG"), "relief": "flat", "bold": True},
            }

            for btn, role in [(b_source, Role.SOURCE), (b_dest, Role.DESTINATION), (b_preview, "preview")]:
                s = role_styles[role]
                if role != "preview":
                    if capture.role == role:
                        btn.config(bg=s["bg"], fg=s["fg"], relief=s["relief"],
                                   font=("Segoe UI", 9, "bold" if s["bold"] else "normal"))
                    elif capture.role == Role.SKIP:
                        btn.config(bg=panel_bg, fg="black", relief="flat", font=("Segoe UI", 9))
                    else:
                        btn.config(bg="#d3d3d3", fg="black", relief="flat", font=("Segoe UI", 9))
                else:
                    if self.active_preview_id == capture.id:
                        sa = role_styles["preview_active"]
                        btn.config(bg=sa["bg"], fg=sa["fg"], relief=sa["relief"],
                                   font=("Segoe UI", 9, "bold" if sa["bold"] else "normal"))
                    else:
                        btn.config(bg=s["bg"], fg=s["fg"], relief=s["relief"], font=("Segoe UI", 9))

        self._update_capture_count()

        if self.active_preview_id:
            preview_capture = next((c for c in self.captures if c.id == self.active_preview_id), None)
            if preview_capture and preview_capture.role == Role.DESTINATION:
                source = next((c for c in self.captures if c.role == Role.SOURCE), None)
                if source:
                    self.show_preview(source, preview_capture)

    def _update_capture_count(self):
        try:
            self.capture_count_lbl.config(text=f"{len(self.captures)} captures")
        except tk.TclError:
            pass

    def set_role(self, capture: Capture, new_role: str):
        if capture.role == new_role:
            capture.role = Role.SKIP
        else:
            if new_role == Role.SOURCE:
                for c in self.captures:
                    if c is not capture and c.role == Role.SOURCE:
                        c.role = Role.SKIP
            capture.role = new_role
        if self.active_preview_id:
            preview_capture = next((c for c in self.captures if c.id == self.active_preview_id), None)
            if preview_capture and preview_capture.role != Role.DESTINATION:
                self.active_preview_id = None
        self._rebuild_capture_list()

    def reset_all_to_skip(self):
        for c in self.captures:
            c.role = Role.SKIP
        self._rebuild_capture_list()

    def get_source_and_dests(self):
        source = None
        dests = []
        for c in self.captures:
            if c.role == Role.SOURCE and c.paths:
                source = c
            elif c.role == Role.DESTINATION and c.paths:
                dests.append(c)
        return source, dests

    def start_sync(self):
        self._clear_preview()
        source, dests = self.get_source_and_dests()
        if not source:
            self.set_status("No Source selected!", "red")
            return
        if not dests:
            self.set_status("No Destination selected!", "red")
            return
        threading.Thread(
            target=execute_sync,
            args=(self, source, dests),
            daemon=True
        ).start()

    def set_status(self, text: str, color: str = "black"):
        if not self._is_window_alive():
            return
        try:
            self.status_lbl.config(text=text, fg=color)
        except tk.TclError:
            pass

    def clear_list(self):
        self._clear_preview()
        self.captures.clear()
        self.seen_signatures.clear()
        self.next_id = 1
        self._rebuild_capture_list()
        self.set_status("List cleared.")

    def _preview_for_capture(self, capture: Capture):
        if self.active_preview_id == capture.id:
            self._clear_preview()
            return
        source, dests = self.get_source_and_dests()
        if not source:
            self.set_status("No source selected for preview", "red")
            return
        if not dests:
            self.set_status("No destination selected for preview", "red")
            return
        self.active_preview_id = capture.id
        self._rebuild_capture_list()

    def _clear_preview(self):
        self.active_preview_id = None
        self._rebuild_capture_list()

    def show_preview(self, source: Capture, dest: Capture):
        skip_unchanged = self.skip_unchanged_var.get()
        plan = compute_sync_plan(source, dest, skip_unchanged)
        counts, action_map, deleted_map = _analyze_plan(plan)

        summary = f"New: {counts[Action.NEW]} | Updated: {counts[Action.UPDATED]} | Deleted: {counts[Action.DELETED]}"
        if counts[Action.UNCHANGED] > 0:
            summary += f" | Unchanged: {counts[Action.UNCHANGED]}"

        self._preview_frame = tk.Frame(self.scrollable_frame, bg=self._get_theme("PREVIEW_BG"), relief="flat")
        self._preview_frame.pack(fill="both", expand=True, padx=0, pady=(10, 0))

        ttk.Separator(self._preview_frame, orient="horizontal").pack(fill="x", padx=5, pady=(10, 5))
        ttk.Label(self._preview_frame, text=summary, font=("Segoe UI", 10)).pack(pady=5, padx=10, anchor="w")

        tree_frame = tk.Frame(self._preview_frame, bg=self._get_theme("PREVIEW_BG"), relief="flat")
        tree_frame.pack(fill="both", expand=True, padx=5, pady=5)

        tree = ttk.Treeview(tree_frame, columns=("type",), show="tree headings", style="Custom.Treeview")
        tree.heading("#0", text="Destination tree after sync")
        tree.heading("type", text="Type")
        tree.column("#0", stretch=True)
        tree.column("type", width=75, stretch=False)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        for action, (bg, fg) in self._get_theme("TREE_TAGS").items():
            tree.tag_configure(action, background=bg, foreground=fg)

        total_files = sum(1 for p in plan if not p.is_dir)
        use_live_reads = total_files < FILE_THRESHOLD

        def make_display(name, action, link_type, target):
            display_name = name
            if link_type in (LINK_SYMLINK, LINK_JUNCTION) and target:
                display_name = f"{name} → {target}"
            elif link_type == LINK_HARDLINK:
                display_name = f"{name} (hardlink)"
            return display_name

        def type_label_for(link_type):
            return TYPE_LABELS.get(link_type, "File")

        def build_tree_live(path: Path, parent_id: str, parent_rel: str):
            rel = (parent_rel + "/" + path.name) if parent_rel else path.name
            info = action_map.get(rel)
            action = info.action if info else ""
            lt = get_link_type(path)
            target = get_link_target(path) if lt in (LINK_SYMLINK, LINK_JUNCTION) else None
            display = make_display(path.name, action, lt, target)

            if lt == LINK_DIR:
                tree.insert(parent_id, "end", iid=rel, text=display,
                            values=("Dir",), tags=(action,) if action else (), open=True)
                try:
                    for entry in sorted(os.scandir(path), key=lambda e: e.name):
                        build_tree_live(Path(entry.path), rel, rel)
                except PermissionError:
                    pass
            else:
                tree.insert(parent_id, "end", iid=rel, text=display,
                            values=(type_label_for(lt),), tags=(action,) if action else (), open=True)

        def build_tree_from_plan(item: SyncAction, parent_id: str):
            rel = item.rel
            name = rel.split("/")[-1]
            display = make_display(name, item.action, item.link_type, item.target)
            tree.insert(parent_id, "end", iid=rel, text=display,
                        values=(type_label_for(item.link_type),),
                        tags=(item.action,) if item.action else (), open=True)
            if item.is_dir:
                for other in plan:
                    if other.rel.startswith(rel + "/") and other.rel.count("/") == rel.count("/") + 1:
                        build_tree_from_plan(other, rel)

        for src_path in source.paths:
            lt = get_link_type(src_path)
            if lt in (LINK_SYMLINK, LINK_JUNCTION, LINK_HARDLINK):
                if use_live_reads:
                    build_tree_live(src_path, "", "")
                else:
                    info = action_map.get(src_path.name)
                    if info:
                        build_tree_from_plan(info, "")
            elif src_path.is_dir():
                try:
                    for entry in sorted(os.scandir(src_path), key=lambda e: e.name):
                        child = Path(entry.path)
                        if use_live_reads:
                            build_tree_live(child, "", "")
                        else:
                            info = action_map.get(entry.name)
                            if info:
                                build_tree_from_plan(info, "")
                except PermissionError:
                    pass
            else:
                if use_live_reads:
                    build_tree_live(src_path, "", "")
                else:
                    info = action_map.get(src_path.name)
                    if info:
                        build_tree_from_plan(info, "")

        def build_deleted(parent_id: str, parent_rel: str):
            children = deleted_map.get(parent_rel, [])
            for name in children:
                rel = (parent_rel + "/" + name) if parent_rel else name
                item = action_map.get(rel)
                if item:
                    is_dir = item.is_dir
                    display = make_display(name, Action.DELETED, item.link_type, item.target)
                    tree.insert(parent_id, "end", iid=rel, text=display,
                                values=(type_label_for(item.link_type),),
                                tags=(Action.DELETED,), open=True)
                    if is_dir:
                        build_deleted(rel, rel)

        if deleted_map:
            del_parent = "deleted_section"
            tree.insert("", "end", iid=del_parent, text="Deleted from destination",
                        values=("",), open=True)
            for name in deleted_map.get("", []):
                rel = name
                item = action_map.get(rel)
                if item:
                    is_dir = item.is_dir
                    display = make_display(name, Action.DELETED, item.link_type, item.target)
                    tree.insert(del_parent, "end", iid=rel, text=display,
                                values=(type_label_for(item.link_type),),
                                tags=(Action.DELETED,), open=True)
                    if is_dir:
                        build_deleted(rel, rel)

        return True

    def _start_workers(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        def clipboard_worker():
            last_paths = []
            while not self._shutdown_event.is_set():
                paths = get_clipboard_files()
                if paths and paths != last_paths:
                    last_paths = paths
                    self.root.after(0, self.add_capture, paths)
                self._shutdown_event.wait(0.4)

        def explorer_worker():
            if not IS_WINDOWS:
                return
            last_path = None
            while not self._shutdown_event.is_set():
                path = get_active_explorer_path()
                if path and path != last_path:
                    last_path = path
                    self.set_status(f"Destination folder updated from Explorer: {path}")
                self._shutdown_event.wait(0.8)

        threading.Thread(target=clipboard_worker, daemon=True).start()
        threading.Thread(target=explorer_worker, daemon=True).start()

    def _on_close(self):
        self._shutdown_event.set()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    gui = SyncGui(root)
    root.mainloop()