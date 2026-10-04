#!/usr/bin/env python3
from __future__ import annotations

# === SETTINGS START ===
PATH = None                 # Path resolution: args - stdin - clipboard - PATH - cwd.
HOST = "127.0.0.1"          # Bind address. Keep local-only unless you know what you're doing.
OPEN_BROWSER = True         # Launch the OS default browser once the server is ready.
QUIET = False                # True = do not print one log line per HTTP request.
# === SETTINGS END ===

"""
open_file_via_http.py - serve a file's parent folder over local HTTP and open it
in the default browser.

Why: double-clicking an HTML/JS file opens it as a `file://` URL, which breaks
anything that needs a real origin (fetch(), modules, SharedArrayBuffer/WASM
threads, service workers, ...). This script instead serves the *folder that
contains the file* over `http://127.0.0.1:<port>/` and opens the exact file.

Typical use:
    1. Copy a file (or a folder) in your file manager - or just `cd` into it
       and copy nothing, the cwd is used as a fallback.
    2. Run this script (double-click / hotkey / terminal).
    3. The parent folder is served over HTTP and the file opens in your
       browser with the COOP/COEP headers needed for cross-origin isolation.

Server lifetime & port handling (no extra processes, no daemonization):
    * Each run that actually starts a server blocks in the foreground
      (`serve_forever`) - exactly like running `python -m http.server`. Leave
      that terminal open for as long as you need the page.
    * A small on-disk registry (JSON file in the OS temp dir) remembers which
      directory each *live* server is serving and on which port, so a second,
      independent run of this script (e.g. from a different terminal/session)
      can find it.
    * Same folder (or a sub-folder of an already-served folder) already has a
      live server -> we just open the browser against it; no new server, no
      new port is created.
    * Different folder -> a *new* OS-assigned free port is used automatically,
      so it never collides with a server already running for another folder.
      That old server (if its terminal was left open / forgotten, as in the
      "I forgot to close this" use case) is left completely alone and keeps
      working.
    * If a server's terminal was closed without a clean shutdown, its registry
      entry is detected as dead (port no longer accepting connections, and/or
      its pid is gone) the next time any folder is resolved, and is silently
      dropped - so stale entries do not pile up or get reused by mistake.

No third-party dependencies: only `http.server` / `socketserver` from the
standard library.
"""

import argparse
import json
import os
import socket
import sys
import tempfile
import time
import urllib.parse
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))
from path_args import resolve_paths

REGISTRY_NAME = "dev_helper_open_file_via_http.json"


# ---------------------------------------------------------------------------
#  HTTP handler - adds the headers requested for cross-origin isolation
# ---------------------------------------------------------------------------

class COOPHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with COOP/COEP + no-cache headers."""

    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if not QUIET:
            super().log_message(fmt, *args)


# ---------------------------------------------------------------------------
#  Registry (JSON file in the OS temp dir): dir -> {port, pid, started}
# ---------------------------------------------------------------------------

def _registry_path() -> Path:
    return Path(tempfile.gettempdir()) / REGISTRY_NAME


def _lock_path(registry_path: Path) -> Path:
    return registry_path.with_suffix(registry_path.suffix + ".lock")


class _RegistryLock:
    """Tiny cross-platform advisory lock using an exclusive-create file.

    Not bulletproof under extreme contention, but this tool only ever has a
    handful of local processes touching the registry, so a simple spin/
    timeout + stale-lock breakout is plenty - no fcntl/msvcrt required.
    """

    def __init__(self, path: Path, timeout: float = 5.0):
        self.path = path
        self.timeout = timeout

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    # Assume a previous run crashed before releasing it.
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        try:
            self.path.unlink()
        except OSError:
            pass
        return False


def _load_registry(registry_path: Path) -> dict:
    if not registry_path.exists():
        return {}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_registry(registry_path: Path, data: dict) -> None:
    tmp_path = registry_path.with_suffix(registry_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, registry_path)


def _dir_key(path: Path) -> str:
    return str(Path(path).resolve())


def _is_subpath(child: Path, parent: Path) -> bool:
    """True if `child` is `parent` or lives somewhere underneath it."""
    child_s = os.path.normcase(str(Path(child).resolve()))
    parent_s = os.path.normcase(str(Path(parent).resolve()))
    if child_s == parent_s:
        return True
    if not parent_s.endswith(os.sep):
        parent_s += os.sep
    return child_s.startswith(parent_s)


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        except Exception:
            return True  # can't check reliably -> don't be the reason we lose a server
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _entry_alive(host: str, info: dict) -> bool:
    port = info.get("port")
    if not isinstance(port, int):
        return False
    pid = info.get("pid")
    if pid is not None and not _pid_alive(pid):
        return False
    return _port_open(host, port)


def _find_reusable_server(registry: dict, host: str, root_dir: Path):
    """Return (dir_key, info) of the most specific live server covering
    `root_dir`, or None. Dead entries encountered along the way are removed
    from `registry` in place (caller is responsible for persisting it)."""

    best = None
    for dir_key in list(registry.keys()):
        info = registry[dir_key]
        if not _entry_alive(host, info):
            del registry[dir_key]
            continue
        if _is_subpath(root_dir, Path(dir_key)):
            if best is None or len(dir_key) > len(best[0]):
                best = (dir_key, info)
    return best


# ---------------------------------------------------------------------------
#  Server start / URL building
# ---------------------------------------------------------------------------

def _start_http_server(host: str, root_dir: Path, preferred_port: int | None):
    handler_cls = partial(COOPHandler, directory=str(root_dir))
    candidates = []
    if preferred_port:
        candidates.append(preferred_port)
    candidates.append(0)  # 0 = let the OS pick a free ephemeral port

    last_error = None
    for port in candidates:
        try:
            return ThreadingHTTPServer((host, port), handler_cls)
        except OSError as exc:
            last_error = exc
            continue
    raise RuntimeError(f"Could not bind an HTTP server on {host}: {last_error}")


def _build_url(host: str, port: int, serving_dir: Path, target: Path) -> str:
    serving_dir = serving_dir.resolve()
    target = target.resolve()
    try:
        rel = target.relative_to(serving_dir)
        rel_str = "" if str(rel) == "." else rel.as_posix()
    except ValueError:
        rel_str = ""
    url = f"http://{host}:{port}/"
    if rel_str:
        url += urllib.parse.quote(rel_str)
    return url


def _open_browser(url: str, disabled: bool) -> None:
    if disabled:
        return
    try:
        webbrowser.open(url, new=2)
    except Exception as exc:
        print(f"(could not launch a browser automatically: {exc})", file=sys.stderr)


# ---------------------------------------------------------------------------
#  Status helper (debugging / manual cleanup)
# ---------------------------------------------------------------------------

def print_status(host: str) -> None:
    registry_path = _registry_path()
    with _RegistryLock(_lock_path(registry_path)):
        registry = _load_registry(registry_path)
        changed = False
        if not registry:
            print("No tracked servers.")
            return
        for dir_key, info in sorted(registry.items()):
            alive = _entry_alive(host, info)
            state = "alive" if alive else "dead (will be dropped)"
            print(f"{dir_key}  ->  port {info.get('port')}  pid {info.get('pid')}  [{state}]")
            if not alive:
                del registry[dir_key]
                changed = True
        if changed:
            _save_registry(registry_path, registry)


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve a file's parent folder over local HTTP and open it in the browser."
    )
    parser.add_argument("path", nargs="?", default=PATH,
                         help="File or folder to open (default: clipboard, then cwd)")
    parser.add_argument("--host", default=HOST,
                         help=f"Interface to bind (default: {HOST})")
    parser.add_argument("--port", type=int, default=None,
                         help="Force this exact port for a newly-started server "
                              "(ignored when an existing server for this folder is reused)")
    parser.add_argument("--no-browser", action="store_true",
                         help="Do not launch the browser, just start/reuse the server")
    parser.add_argument("--status", action="store_true",
                         help="List tracked servers (alive/dead) and exit")
    return parser.parse_args(argv)


def resolve_target(args) -> Path:
    resolved = resolve_paths(args, arg_names=("path",), fallback_to_cwd=True, create="none")
    target = next((p for p in resolved.paths if p.exists()), None)
    if target is None:
        target = resolved.first
    return target


def main(argv=None):
    args = parse_args(argv)
    host = args.host

    if args.status:
        print_status(host)
        return

    target = resolve_target(args)
    if not target.exists():
        print(f"Path not found: {target}", file=sys.stderr)
        sys.exit(1)

    target = target.resolve()
    root_dir = target if target.is_dir() else target.parent

    registry_path = _registry_path()
    lock_path = _lock_path(registry_path)

    with _RegistryLock(lock_path):
        registry = _load_registry(registry_path)

        stale_port = None
        root_key = _dir_key(root_dir)
        existing = registry.get(root_key)
        if existing and not _entry_alive(host, existing):
            stale_port = existing.get("port")

        match = _find_reusable_server(registry, host, root_dir)
        _save_registry(registry_path, registry)  # persist any stale-entry cleanup

        if match:
            dir_key, info = match
            port = info["port"]
            url = _build_url(host, port, Path(dir_key), target)
            print(f"Reusing server for {dir_key} on port {port}")
            print(url)
            _open_browser(url, args.no_browser)
            return

        preferred_port = args.port or stale_port
        httpd = _start_http_server(host, root_dir, preferred_port)
        port = httpd.server_port
        registry[root_key] = {"port": port, "pid": os.getpid(), "started": time.time()}
        _save_registry(registry_path, registry)

    url = _build_url(host, port, root_dir, target)
    print(f"Serving {root_dir} at http://{host}:{port}/  (Ctrl+C to stop)")
    print(url)
    _open_browser(url, args.no_browser)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        httpd.server_close()
        with _RegistryLock(lock_path):
            registry = _load_registry(registry_path)
            current = registry.get(root_key)
            if current and current.get("pid") == os.getpid() and current.get("port") == port:
                del registry[root_key]
                _save_registry(registry_path, registry)


if __name__ == "__main__":
    main()
