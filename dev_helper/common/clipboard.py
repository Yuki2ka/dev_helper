from __future__ import annotations

import platform
import subprocess

try:
    import win32clipboard
except ImportError:
    win32clipboard = None  # type: ignore


def _on_windows() -> bool:
    return bool(win32clipboard) and platform.system() == "Windows"


def copy_to_clipboard(text: str) -> None:
    """Copy text to the OS clipboard (cross-platform, no third-party deps)."""
    if _on_windows():
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return

    system = platform.system()
    if system == "Darwin":
        _run(["pbcopy"], text)
    elif system == "Linux":
        if _run(["xclip", "-selection", "clipboard"], text):
            return
        _run(["xsel", "--clipboard", "--input"], text)


def paste_clipboard() -> str:
    """Read plain text from the OS clipboard (cross-platform)."""
    if _on_windows():
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) or ""
        finally:
            win32clipboard.CloseClipboard()
        return ""

    system = platform.system()
    if system == "Darwin":
        return _read(["pbpaste"])
    if system == "Linux":
        out = _read(["xclip", "-selection", "clipboard", "-o"])
        if out is not None:
            return out
        return _read(["xsel", "--clipboard", "--output"]) or ""
    return ""


def paste_clipboard_files() -> list[str]:
    """Return file/folder paths copied to the clipboard (Windows CF_HDROP).

    On Windows, copying files/folders in Explorer places a file-drop list on the
    clipboard. This returns those paths. Returns an empty list on other platforms
    or when no file-drop data is present.
    """
    if _on_windows():
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_HDROP)
                return [p for p in (data or []) if p]
        finally:
            win32clipboard.CloseClipboard()
    return []


def _run(cmd: list[str], text: str) -> bool:
    """Run a clipboard command feeding `text` on stdin. Returns success."""
    try:
        subprocess.run(
            cmd, input=text.encode("utf-8", "ignore"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
        return True
    except Exception:
        return False


def _read(cmd: list[str]) -> str | None:
    """Run a clipboard command and return its stdout, or None on failure."""
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        )
        return result.stdout.decode("utf-8", "ignore")
    except Exception:
        return None
