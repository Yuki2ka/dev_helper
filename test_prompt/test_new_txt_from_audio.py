"""Offline wrapper tests; no model or GPU required."""
import importlib.util
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "new_txt_from_audio",
    Path(__file__).resolve().parents[1] / "dev_helper/new_file/new_txt_from_audio.py",
)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class AudioTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "speech sample.wav"
        with wave.open(str(self.source), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\0\0" * 160)
        self.target = self.source.with_suffix(".txt")
        self.model = self.root / "model.gguf"
        self.model.touch()
        self.args = script.parse_args([])

    def fake_cli(self, command):
        self.assertEqual(command[command.index("--family") + 1], "qwen3_asr")
        Path(command[command.index("--text-out") + 1]).write_text("Hello 世界\n", encoding="utf-8")

    def test_collection_recursive_deduplicated_case_insensitive(self):
        nested = self.root / "nested"
        nested.mkdir()
        other = nested / "music.MP3"
        other.touch()
        (nested / "ignore.txt").touch()
        self.assertEqual(set(script.collect_audio_files([self.root, self.source])),
                         {self.source, other})
        self.assertEqual(list(script.collect_audio_files([self.root], False)), [self.source])

    def test_wav_direct_and_unicode_transcript(self):
        self.assertTrue(script.is_ready_wav(self.source))
        with patch.object(script, "run_command", side_effect=self.fake_cli) as run:
            script.transcribe(self.source, self.target, self.args, "cli", self.model)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "Hello 世界\n")

    def test_conversion_and_language(self):
        source = self.root / "compressed.mp3"
        source.touch()
        self.args.language = "English"
        commands = []

        def run(command):
            commands.append(command)
            if "--text-out" in command:
                self.fake_cli(command)
        with patch.object(script, "run_command", side_effect=run), \
                patch.object(script, "find_executable", return_value="ffmpeg"):
            script.transcribe(source, self.target, self.args, "cli", self.model)
        self.assertIn("pcm_s16le", commands[0])
        self.assertEqual(commands[1][-2:], ["--language", "English"])
        self.assertFalse(Path(commands[0][-1]).exists())

    def test_failure_preserves_existing_output(self):
        self.target.write_text("old")
        self.args.overwrite = True
        with patch.object(script, "run_command", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                script.transcribe(self.source, self.target, self.args, "cli", self.model)
        self.assertEqual(self.target.read_text(), "old")

    def test_missing_output_is_error(self):
        with patch.object(script, "run_command"):
            with self.assertRaisesRegex(RuntimeError, "did not create"):
                script.transcribe(self.source, self.target, self.args, "cli", self.model)
        self.assertFalse(self.target.exists())

    def test_skip_existing_without_dependencies(self):
        self.target.write_text("old")
        with patch.object(script, "find_executable", side_effect=AssertionError):
            self.assertEqual(script.main([str(self.source)]), 0)
        self.assertEqual(self.target.read_text(), "old")

    def test_overwrite(self):
        self.target.write_text("old")
        self.args.overwrite = True
        with patch.object(script, "run_command", side_effect=self.fake_cli):
            script.transcribe(self.source, self.target, self.args, "cli", self.model)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "Hello 世界\n")

    def test_same_stem_collision_even_with_overwrite(self):
        self.source.with_suffix(".mp3").touch()
        with patch.object(script, "find_executable", return_value="cli"), \
                patch.object(script, "transcribe") as transcribe:
            status = script.main([str(self.root), "--model", str(self.model), "--overwrite"])
        self.assertEqual(status, 1)
        self.assertEqual(transcribe.call_count, 1)

    def test_exclusive_output(self):
        self.target.write_text("old")
        with patch.object(script, "run_command", side_effect=self.fake_cli):
            with self.assertRaises(FileExistsError):
                script.transcribe(self.source, self.target, self.args, "cli", self.model)
        self.assertEqual(self.target.read_text(), "old")


if __name__ == "__main__":
    unittest.main()
