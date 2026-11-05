"""Offline tests for the companion editor's file lists and sync estimates."""
import importlib.util
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
NEW_FILE = ROOT / "dev_helper/new_file"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, NEW_FILE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gui = _load("new_txt_from_audio_GUI", "new_txt_from_audio_GUI.py")
console = _load("new_txt_from_audio_console", "new_txt_from_audio.py")


def write_wav(path, frames=160, channels=1, rate=16000):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * frames)


class FileListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_gui_never_hides_a_console_supported_extension(self):
        missing = console.AUDIO_EXTENSIONS - gui.AUDIO_EXTENSIONS
        self.assertEqual(missing, set())

    def test_dialog_lists_every_supported_extension(self):
        patterns = set()
        for _label, value in gui.audio_filetypes():
            patterns.update(value.split())
        for extension in gui.AUDIO_EXTENSIONS:
            self.assertIn("*" + extension, patterns)
        self.assertIn("*.*", patterns)

    def test_dialog_groups_only_offer_supported_extensions(self):
        for label, value in gui.audio_filetypes():
            if label == "All files":
                continue
            for pattern in value.split():
                self.assertIn(pattern[1:], gui.AUDIO_EXTENSIONS, label)

    def test_dialog_has_a_group_for_mpeg_and_3gpp(self):
        labels = [label for label, _value in gui.audio_filetypes()]
        self.assertIn("MP3 and MPEG audio", labels)
        self.assertIn("3GPP phone recordings", labels)

    def test_extensionless_recording_is_found_by_signature(self):
        write_wav(self.root / "dictation")
        (self.root / "notes.txt").write_text("hello", encoding="utf-8")
        found = [path.name for path in gui.scan_audio(self.root)]
        self.assertEqual(found, ["dictation"])

    def test_uppercase_extension_is_listed(self):
        write_wav(self.root / "Voice.FLAC")
        self.assertEqual([p.name for p in gui.scan_audio(self.root)], ["Voice.FLAC"])

    def test_scan_is_natural_sorted(self):
        for name in ("track10.wav", "track2.wav", "track1.wav"):
            write_wav(self.root / name)
        names = [path.name for path in gui.scan_audio(self.root)]
        self.assertEqual(names, ["track1.wav", "track2.wav", "track10.wav"])

    def test_recursive_scan_uses_relative_labels(self):
        sub = self.root / "day 2"
        sub.mkdir()
        write_wav(sub / "talk.3gp")
        self.assertEqual(gui.scan_audio(self.root), [])
        found = gui.scan_audio(self.root, recursive=True)
        self.assertEqual([p.name for p in found], ["talk.3gp"])
        self.assertEqual(gui._relative_text(found[0], self.root), str(Path("day 2/talk.3gp")))

    def test_symlink_loop_does_not_recurse_forever(self):
        sub = self.root / "sub"
        sub.mkdir()
        write_wav(sub / "note.wav")
        try:
            (sub / "loop").symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available")
        names = [path.name for path in gui.scan_audio(self.root, recursive=True)]
        self.assertEqual(names, ["note.wav"])

    def test_signature_does_not_accept_plain_text(self):
        target = self.root / "readme"
        target.write_text("just words", encoding="utf-8")
        self.assertFalse(gui.has_audio_signature(target))
        self.assertFalse(gui.looks_like_audio(target))


class SignatureParityTests(unittest.TestCase):
    """The GUI must sniff exactly the way the console transcriber does."""

    SAMPLES = {
        "wave": b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 8,
        "rf64": b"rf64\xff\xff\xff\xffWAVEds64" + b"\x00" * 8,
        "RF64": b"RF64\xff\xff\xff\xffWAVEds64" + b"\x00" * 8,
        "BW64": b"BW64\xff\xff\xff\xffWAVEds64" + b"\x00" * 8,
        "wave64": bytes.fromhex("3666666611cf11a0") + b"\x00" * 8,
        "flac": b"fLaC\x00\x00\x00\x22" + b"\x00" * 8,
        "ogg": b"OggS\x00\x02\x00\x00" + b"\x00" * 8,
        "aiff": b"FORM\x00\x00\x00\x10AIFF" + b"\x00" * 8,
        "m4a": b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 8,
        "3gp": b"\x00\x00\x00\x18ftyp3gp4" + b"\x00" * 8,
        "amr": b"#!AMR\n" + b"\x00" * 10,
        "asf": bytes.fromhex("3026b2758e66cf11") + b"\x00" * 8,
        "mp3": b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 6,
        "mpeg_audio": b"\xff\xfb\x90\x00" + b"\x00" * 12,
        "mpeg_stream": b"\x00\x00\x01\xba" + b"\x00" * 12,
        "text": b"hello there, this is not audio",
        "empty": b"",
    }

    def test_sniffing_matches_the_console_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name, head in self.SAMPLES.items():
                target = folder / name
                target.write_bytes(head)
                with self.subTest(sample=name):
                    self.assertEqual(
                        gui.has_audio_signature(target),
                        console.has_audio_signature(target),
                    )

    def test_known_containers_are_recognised(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name in ("wave", "RF64", "BW64", "flac", "ogg", "aiff", "m4a",
                         "3gp", "amr", "asf", "mp3", "mpeg_audio", "mpeg_stream"):
                target = folder / name
                target.write_bytes(self.SAMPLES[name])
                self.assertTrue(gui.has_audio_signature(target), name)
            for name in ("text", "empty"):
                target = folder / name
                target.write_bytes(self.SAMPLES[name])
                self.assertFalse(gui.has_audio_signature(target), name)


class EstimateTests(unittest.TestCase):
    def test_plain_estimate_stays_inside_the_audio(self):
        text = "one two three four five six"
        marks = gui.estimate_marks(text, 10.0, lead=0.3, tail=0.5)
        self.assertEqual(len(marks), 6)
        times = [time for time, _offset in marks]
        self.assertGreaterEqual(times[0], 0.3)
        self.assertLess(times[-1], 10.0)
        self.assertEqual(times, sorted(times))

    def test_punctuation_adds_a_pause(self):
        self.assertGreater(
            gui.word_weight("end.", 0, 4), gui.word_weight("end", 0, 3)
        )

    def test_estimate_skips_detected_silence(self):
        text = " ".join(["word"] * 10)
        marks = gui.estimate_marks_in_segments(text, [(0.0, 2.0), (8.0, 10.0)])
        self.assertEqual(len(marks), 10)
        times = [time for time, _offset in marks]
        self.assertTrue(all(not 2.0 <= time < 8.0 for time in times), times)
        self.assertLess(times[0], 2.0)
        self.assertGreaterEqual(times[-1], 8.0)

    def test_estimate_in_segments_needs_segments(self):
        self.assertEqual(gui.estimate_marks_in_segments("one two", []), [])

    def test_anchors_are_kept_and_every_word_is_filled(self):
        text = "alpha beta gamma delta epsilon zeta eta theta"
        words = gui.text_words(text)
        anchors = [(1.0, words[1][0]), (6.0, words[6][0])]
        marks = gui.estimate_marks_from_anchors(text, 8.0, anchors)
        self.assertEqual(len(marks), len(words))
        by_offset = {offset: time for time, offset in marks}
        self.assertEqual(by_offset[words[1][0]], 1.0)
        self.assertEqual(by_offset[words[6][0]], 6.0)
        times = [time for time, _offset in marks]
        self.assertEqual(times, sorted(times))
        for index in range(1, 6):
            self.assertGreaterEqual(by_offset[words[index][0]], 1.0)
            self.assertLessEqual(by_offset[words[index][0]], 6.0)

    def test_anchor_refine_falls_back_to_plain_estimate(self):
        text = "one two three"
        plain = gui.estimate_marks(text, 5.0)
        self.assertEqual(gui.estimate_marks_from_anchors(text, 5.0, []), plain)

    def test_anchor_after_the_last_word_still_fills_the_tail(self):
        text = "one two three four"
        words = gui.text_words(text)
        anchors = [(1.0, words[0][0])]
        marks = gui.estimate_marks_from_anchors(text, 6.0, anchors)
        self.assertEqual(len(marks), 4)
        self.assertLess(marks[-1][0], 6.0)
        self.assertGreater(marks[-1][0], 1.0)


class IndexTests(unittest.TestCase):
    def test_line_offsets(self):
        text = "one\ntwo\n\nthree"
        self.assertEqual(gui.line_offsets(text), [0, 4, 8, 9])

    def test_line_offsets_of_single_line(self):
        self.assertEqual(gui.line_offsets("no newline"), [0])
        self.assertEqual(gui.line_offsets(""), [0])

    def test_offset_from_index(self):
        starts = gui.line_offsets("one\ntwo\nthree")
        self.assertEqual(gui.offset_from_index("1.0", starts), 0)
        self.assertEqual(gui.offset_from_index("2.2", starts), 6)
        self.assertEqual(gui.offset_from_index("3.5", starts), 13)
        self.assertIsNone(gui.offset_from_index("9.0", starts))
        self.assertIsNone(gui.offset_from_index("nonsense", starts))


class SilenceTests(unittest.TestCase):
    def test_parse_silencedetect_output(self):
        output = (
            "[silencedetect @ 0x1] silence_start: 1.25\n"
            "[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 1.25\n"
            "[silencedetect @ 0x1] silence_start: 7\n"
            "[silencedetect @ 0x1] silence_end: 9.5 | silence_duration: 2.5\n"
        )
        self.assertEqual(gui.parse_silences(output), [(1.25, 2.5), (7.0, 9.5)])

    def test_segments_are_the_complement_of_silence(self):
        segments = gui.speech_segments_from_silences([(2.0, 4.0)], 10.0)
        self.assertEqual(segments, [(0.0, 2.0), (4.0, 10.0)])

    def test_short_gaps_are_merged(self):
        segments = gui.speech_segments_from_silences(
            [(2.0, 2.1), (2.2, 2.3)], 10.0
        )
        self.assertEqual(segments, [(0.0, 10.0)])

    def test_full_silence_leaves_no_speech(self):
        self.assertEqual(gui.speech_segments_from_silences([(0.0, 10.0)], 10.0), [])

    def test_detect_speech_segments_parses_ffmpeg_output(self):
        output = (
            "silence_start: 0\n"
            "silence_end: 1.5 | silence_duration: 1.5\n"
            "silence_start: 4\n"
            "silence_end: 6 | silence_duration: 2\n"
        )
        completed = mock.Mock(returncode=0, stderr=output)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.wav"
            write_wav(target)
            with mock.patch.object(gui.subprocess, "run", return_value=completed) as run:
                segments = gui.detect_speech_segments(
                    target, duration=8.0, ffmpeg="ffmpeg"
                )
        self.assertEqual(segments, [(1.5, 4.0), (6.0, 8.0)])
        command = run.call_args[0][0]
        self.assertIn("silencedetect=noise=-35dB:d=0.35", command)
        self.assertIn("-vn", command)

    def test_detect_speech_segments_ignores_a_failing_ffmpeg(self):
        completed = mock.Mock(returncode=1, stderr="boom")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.wav"
            write_wav(target)
            with mock.patch.object(gui.subprocess, "run", return_value=completed):
                self.assertEqual(
                    gui.detect_speech_segments(target, duration=8.0, ffmpeg="ffmpeg"),
                    [],
                )

    def test_detect_speech_segments_without_ffmpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.wav"
            write_wav(target)
            self.assertEqual(gui.detect_speech_segments(target, ffmpeg="", duration=1.0), [])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "settings.json"

    def test_defaults_when_missing(self):
        self.assertEqual(gui.load_gui_settings(self.path), gui.DEFAULT_GUI_SETTINGS)

    def test_roundtrip_and_clamping(self):
        gui.save_gui_settings(
            {"last_folder": "/audio", "lead_seconds": 0.4, "follow": False,
             "recursive": True}, self.path)
        loaded = gui.load_gui_settings(self.path)
        self.assertEqual(loaded["last_folder"], "/audio")
        self.assertAlmostEqual(loaded["lead_seconds"], 0.4)
        self.assertFalse(loaded["follow"])
        self.assertTrue(loaded["recursive"])

        self.path.write_text(json.dumps({"lead_seconds": 99}), encoding="utf-8")
        self.assertEqual(gui.load_gui_settings(self.path)["lead_seconds"], 2.0)

    def test_broken_file_is_ignored(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(gui.load_gui_settings(self.path), gui.DEFAULT_GUI_SETTINGS)


if __name__ == "__main__":
    unittest.main()
