from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script("two_way_cache_test", "dev_helper/new_file/2way_explorer.py")

    def test_chunk_cache_uses_real_path_for_file_version(self):
        module = self.module
        module._chunk_cache.clear()
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.txt"
            source.write_text("hello", encoding="utf-8")
            result = ("hash", "formatted")
            with patch.object(module.tc, "_read_file_content", return_value=result) as read:
                self.assertEqual(module._get_cached_chunk(str(source), "sample.txt"), result)
                self.assertEqual(module._get_cached_chunk(str(source), "sample.txt"), result)
                self.assertEqual(read.call_count, 1)

    def test_chunk_cache_accounts_for_format_setting(self):
        module = self.module
        module._chunk_cache.clear()
        original = module.CONTENT_MARK_FORMAT
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.txt"
            source.write_text("hello", encoding="utf-8")
            with patch.object(module.tc, "_read_file_content", return_value=("hash", "block")) as read:
                module.CONTENT_MARK_FORMAT = "default"
                module._get_cached_chunk(str(source), "sample.txt")
                module.CONTENT_MARK_FORMAT = "markdown"
                module._get_cached_chunk(str(source), "sample.txt")
                self.assertEqual(read.call_count, 2)
        module.CONTENT_MARK_FORMAT = original


class ComfyWaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script("comfy_wait_test", "dev_helper/new_file/ComfyUI_workflow_api.py")

    def test_failed_multi_image_upload_does_not_shift_node_assignments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir) / "first.png", Path(temp_dir) / "second.png"]
            calls = []

            def upload(path):
                calls.append(path)
                return None if path == paths[0] else "second-upload"

            result = self.module.plan_image_jobs(paths, "combine", ["1", "2"], upload)

            self.assertEqual(result, [None])
            self.assertEqual(calls, [paths[0]])

    def test_already_completed_job_is_found_via_history(self):
        module = self.module
        history = {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"1": {"images": [{"filename": "x.png"}]}},
        }
        with (
            patch.object(module.websocket, "WebSocket", side_effect=OSError("no websocket")),
            patch.object(module, "_get_prompt_history", return_value=history),
            patch.object(module, "_download_images_for_prompt", return_value=True) as download,
        ):
            self.assertTrue(module._wait_for_prompts_and_download(["done-id"], "."))
        download.assert_called_once_with("done-id", ".", history=history)

    def test_failed_history_entry_finishes_without_waiting_forever(self):
        module = self.module
        history = {"status": {"completed": True, "status_str": "error"}, "outputs": {}}
        with (
            patch.object(module.websocket, "WebSocket", side_effect=OSError("no websocket")),
            patch.object(module, "_get_prompt_history", return_value=history),
        ):
            self.assertFalse(module._wait_for_prompts_and_download(["failed-id"], "."))


class DiskIsoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script("new_disk_review_test", "dev_helper/new_file/new_disk_from_files.py")

    def test_output_file_is_not_reused_as_an_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "disk.iso"
            output.write_bytes(b"old image")
            source = root / "source.txt"
            source.write_text("source", encoding="utf-8")
            file_map = {"disk.iso": output, "source.txt": source}

            filtered = self.module.exclude_output_file(file_map, output)

            self.assertEqual(filtered, {"source.txt": source})

    def test_external_iso_tool_uses_sources_without_staging_copy(self):
        module = self.module
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source file.txt"
            source.write_text("data", encoding="utf-8")
            output = root / "output.iso"
            captured = {}

            def fake_run(command, **kwargs):
                captured["command"] = command
                path_list = Path(command[command.index("-path-list") + 1])
                captured["path_list"] = path_list.read_text(encoding="utf-8")
                return SimpleNamespace(returncode=0, stderr="", stdout="")

            with (
                patch.object(module, "find_tool", return_value=Path("/tools/xorriso")),
                patch.object(module.subprocess, "run", side_effect=fake_run),
                patch.object(module.shutil, "copy2") as copy,
            ):
                module.create_iso_tool("xorriso", {"folder/file.txt": source}, output, "LABEL")

            self.assertIn("-graft-points", captured["command"])
            self.assertIn("folder/file.txt=", captured["path_list"])
            self.assertIn(str(source.resolve()), captured["path_list"])
            copy.assert_not_called()


class BoundedInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script("llm_input_review_test", "dev_helper/new_file/new_file_from_LLM.py")

    def test_project_path_resolver_is_used(self):
        self.assertEqual(self.module.resolve_paths.__module__, "path_args.command_paths")

    def test_text_context_is_truncated_with_a_bounded_prefix(self):
        module = self.module
        old_limit = module.TEXT_SIZE_MAX
        module.TEXT_SIZE_MAX = 10
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                source = Path(temp_dir) / "large.txt"
                source.write_text("x" * 100, encoding="utf-8")
                context = module.extract_text_from_files([source])
            self.assertIn("x" * 10, context)
            self.assertNotIn("x" * 11, context)
            self.assertIn("TRUNCATED at 10 chars", context)
        finally:
            module.TEXT_SIZE_MAX = old_limit


class AudioFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.voice = load_script("new_voice_review_test", "dev_helper/new_file/new_voice.py")
        cls.audio = load_script("new_audio_review_test", "dev_helper/new_file/new_audio.py")

    def test_voice_opus_fallback_keeps_generated_wav(self):
        class FakeEngine:
            def save_to_file(self, text, path):
                Path(path).write_bytes(b"wav")

            def runAndWait(self):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "voice.opus"
            with patch.object(self.voice.subprocess, "run", side_effect=FileNotFoundError):
                saved = Path(self.voice.save_tts_to_format(FakeEngine(), "hello", str(output), "opus"))
            self.assertEqual(saved.suffix, ".wav")
            self.assertEqual(saved.read_bytes(), b"wav")
            self.assertFalse(output.exists())

    def test_generated_audio_reports_wav_fallback_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "tone.opus"
            with patch.object(self.audio.subprocess, "run", side_effect=FileNotFoundError):
                saved = Path(self.audio.save_audio(str(output), b"\x00\x00" * 10, "opus", 8000))
            self.assertEqual(saved.suffix, ".wav")
            self.assertTrue(saved.exists())
            self.assertFalse(output.exists())
