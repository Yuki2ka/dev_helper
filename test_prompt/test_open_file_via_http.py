from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from .conftest import chdir

try:
    from path_args import command_paths, resolve_paths
except ImportError:
    command_paths = None
    resolve_paths = None

try:
    from dev_helper.view.open_file_via_http import (
        _build_url,
        _dir_key,
        _entry_alive,
        _find_reusable_server,
        _is_subpath,
        _load_registry,
        _save_registry,
        _start_http_server,
        main,
        resolve_target,
    )
    MODULE_AVAILABLE = True
except ImportError:
    MODULE_AVAILABLE = False


@unittest.skipUnless(MODULE_AVAILABLE, "dev_helper.view.open_file_via_http not importable")
class IsSubpathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "sub").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_dir_is_subpath(self):
        self.assertTrue(_is_subpath(self.root, self.root))

    def test_nested_dir_is_subpath(self):
        self.assertTrue(_is_subpath(self.root / "sub", self.root))

    def test_unrelated_dir_is_not_subpath(self):
        with tempfile.TemporaryDirectory() as other:
            self.assertFalse(_is_subpath(Path(other), self.root))

    def test_parent_is_not_subpath_of_child(self):
        self.assertFalse(_is_subpath(self.root, self.root / "sub"))


@unittest.skipUnless(MODULE_AVAILABLE, "dev_helper.view.open_file_via_http not importable")
class RegistryRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.tmp.name) / "registry.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_loads_empty_dict(self):
        self.assertEqual(_load_registry(self.registry_path), {})

    def test_save_then_load_round_trips(self):
        data = {"/a/b": {"port": 1234, "pid": 99, "started": 1.0}}
        _save_registry(self.registry_path, data)
        self.assertEqual(_load_registry(self.registry_path), data)

    def test_corrupt_file_loads_empty_dict(self):
        self.registry_path.write_text("not json", encoding="utf-8")
        self.assertEqual(_load_registry(self.registry_path), {})


@unittest.skipUnless(MODULE_AVAILABLE, "dev_helper.view.open_file_via_http not importable")
class FindReusableServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "sub").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_matches_exact_dir(self):
        registry = {_dir_key(self.root): {"port": 1111, "pid": 1}}
        with patch("dev_helper.view.open_file_via_http._entry_alive", return_value=True):
            match = _find_reusable_server(registry, "127.0.0.1", self.root)
        self.assertIsNotNone(match)
        self.assertEqual(match[1]["port"], 1111)

    def test_matches_subdirectory_of_served_root(self):
        registry = {_dir_key(self.root): {"port": 2222, "pid": 1}}
        with patch("dev_helper.view.open_file_via_http._entry_alive", return_value=True):
            match = _find_reusable_server(registry, "127.0.0.1", self.root / "sub")
        self.assertIsNotNone(match)
        self.assertEqual(match[0], _dir_key(self.root))

    def test_prefers_most_specific_match(self):
        registry = {
            _dir_key(self.root): {"port": 1, "pid": 1},
            _dir_key(self.root / "sub"): {"port": 2, "pid": 2},
        }
        with patch("dev_helper.view.open_file_via_http._entry_alive", return_value=True):
            match = _find_reusable_server(registry, "127.0.0.1", self.root / "sub")
        self.assertEqual(match[1]["port"], 2)

    def test_dead_entries_are_dropped(self):
        registry = {_dir_key(self.root): {"port": 1111, "pid": 1}}
        with patch("dev_helper.view.open_file_via_http._entry_alive", return_value=False):
            match = _find_reusable_server(registry, "127.0.0.1", self.root)
        self.assertIsNone(match)
        self.assertEqual(registry, {})

    def test_unrelated_dir_does_not_match(self):
        registry = {_dir_key(self.root): {"port": 1111, "pid": 1}}
        with tempfile.TemporaryDirectory() as other:
            with patch("dev_helper.view.open_file_via_http._entry_alive", return_value=True):
                match = _find_reusable_server(registry, "127.0.0.1", Path(other))
        self.assertIsNone(match)


@unittest.skipUnless(MODULE_AVAILABLE, "dev_helper.view.open_file_via_http not importable")
class EntryAliveTests(unittest.TestCase):
    def test_no_port_is_not_alive(self):
        self.assertFalse(_entry_alive("127.0.0.1", {}))

    def test_dead_pid_is_not_alive(self):
        with patch("dev_helper.view.open_file_via_http._pid_alive", return_value=False):
            self.assertFalse(_entry_alive("127.0.0.1", {"port": 1234, "pid": 99999999}))

    def test_alive_pid_and_open_port_is_alive(self):
        with patch("dev_helper.view.open_file_via_http._pid_alive", return_value=True), \
             patch("dev_helper.view.open_file_via_http._port_open", return_value=True):
            self.assertTrue(_entry_alive("127.0.0.1", {"port": 1234, "pid": 1}))


@unittest.skipUnless(MODULE_AVAILABLE, "dev_helper.view.open_file_via_http not importable")
class BuildUrlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "sub").mkdir()
        self.file = self.root / "sub" / "page.html"
        self.file.write_text("hi", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_url_for_nested_file(self):
        url = _build_url("127.0.0.1", 8000, self.root, self.file)
        self.assertEqual(url, "http://127.0.0.1:8000/sub/page.html")

    def test_url_for_root_dir_itself(self):
        url = _build_url("127.0.0.1", 8000, self.root, self.root)
        self.assertEqual(url, "http://127.0.0.1:8000/")


@unittest.skipUnless(MODULE_AVAILABLE, "dev_helper.view.open_file_via_http not importable")
class ServerHeadersIntegrationTests(unittest.TestCase):
    """Actually binds a real (ephemeral-port) server and checks the headers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "index.html").write_text("hello", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_response_has_coop_coep_and_no_store_headers(self):
        httpd = _start_http_server("127.0.0.1", self.root, None)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            port = httpd.server_port
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Cross-Origin-Opener-Policy"), "same-origin")
                self.assertEqual(resp.headers.get("Cross-Origin-Embedder-Policy"), "require-corp")
                self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


@unittest.skipUnless(MODULE_AVAILABLE, "dev_helper.view.open_file_via_http not importable")
class ResolveTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.file = self.root / "a.txt"
        self.file.write_text("hi", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_cli_arg_path_is_used(self):
        import argparse
        args = argparse.Namespace(path=str(self.file))
        with patch.object(command_paths, "paths_from_clipboard", return_value=[]):
            target = resolve_target(args)
        self.assertEqual(target, self.file.resolve())

    def test_falls_back_to_cwd_when_nothing_else_available(self):
        import argparse
        args = argparse.Namespace(path=None)
        with patch.object(command_paths, "paths_from_clipboard", return_value=[]):
            with chdir(self.root):
                target = resolve_target(args)
        self.assertEqual(target, self.root)


@unittest.skipUnless(MODULE_AVAILABLE, "dev_helper.view.open_file_via_http not importable")
class MainEndToEndTests(unittest.TestCase):
    """Drives main() end-to-end: start, reuse-for-subdir, and separate port for a
    different directory - using an isolated registry file so this never touches
    (or is confused by) a real system-wide registry."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.dir_a = self.root / "a"
        self.dir_b = self.root / "b"
        self.dir_a.mkdir()
        self.dir_b.mkdir()
        (self.dir_a / "sub").mkdir()
        self.file_a = self.dir_a / "index.html"
        self.file_a.write_text("A", encoding="utf-8")
        self.file_a_nested = self.dir_a / "sub" / "nested.html"
        self.file_a_nested.write_text("A-nested", encoding="utf-8")
        self.file_b = self.dir_b / "other.html"
        self.file_b.write_text("B", encoding="utf-8")

        self.registry_path = self.root / "registry.json"
        self._patcher = patch(
            "dev_helper.view.open_file_via_http._registry_path",
            return_value=self.registry_path,
        )
        self._patcher.start()
        self._servers_to_close = []
        self._threads = []

    def tearDown(self):
        self._patcher.stop()
        for httpd in self._servers_to_close:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
        # Wait for each main() thread to finish its post-shutdown registry
        # cleanup *before* the temp dir (and its registry file) disappears.
        for thread in self._threads:
            thread.join(timeout=5)
        self.tmp.cleanup()

    def _run_blocking_server(self, path: Path):
        """Call main() for a path that has no existing server; main() blocks in
        serve_forever(), so run it on a background thread and stop it via the
        registry-tracked httpd once the test is done inspecting state."""
        import dev_helper.view.open_file_via_http as mod

        real_start = mod._start_http_server

        def capturing_start(host, root_dir, preferred_port):
            httpd = real_start(host, root_dir, preferred_port)
            self._servers_to_close.append(httpd)
            return httpd

        initial_count = len(self._servers_to_close)
        with patch.object(mod, "_start_http_server", side_effect=capturing_start):
            thread = threading.Thread(
                target=main, args=([str(path), "--no-browser"],), daemon=True
            )
            thread.start()
            self._threads.append(thread)
            for _ in range(100):
                if len(self._servers_to_close) > initial_count:
                    break
                threading.Event().wait(0.05)
        return self._servers_to_close[-1]

    def test_second_call_same_dir_reuses_server_without_blocking(self):
        httpd = self._run_blocking_server(self.file_a)
        port = httpd.server_port

        with patch("builtins.print"):
            main([str(self.file_a_nested), "--no-browser"])  # returns immediately (reuse path)

        registry = _load_registry(self.registry_path)
        self.assertEqual(len(registry), 1)
        self.assertEqual(list(registry.values())[0]["port"], port)

    def test_different_dir_gets_its_own_port(self):
        httpd_a = self._run_blocking_server(self.file_a)
        port_a = httpd_a.server_port

        httpd_b = self._run_blocking_server(self.file_b)
        port_b = httpd_b.server_port

        self.assertNotEqual(port_a, port_b)
        registry = _load_registry(self.registry_path)
        self.assertEqual(len(registry), 2)


if __name__ == "__main__":
    unittest.main()
