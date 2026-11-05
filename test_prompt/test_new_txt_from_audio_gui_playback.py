"""Offline tests for the companion editor's playback of formats pygame cannot
decode (3GP/AMR/AAC/WMA...: "modplug_load failed") and related fixes.

Tests that need FFmpeg or pygame skip themselves when those are missing.
"""
import importlib.util
import io
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
GUI_FILE = ROOT / "dev_helper/new_file/new_txt_from_audio_GUI.py"

spec = importlib.util.spec_from_file_location("new_txt_from_audio_GUI_playback", GUI_FILE)
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)


def write_wav(path, frames=8000, rate=8000, channels=1):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * frames * channels)
    return Path(path)


def wave_header(format_tag, extra_chunk=b""):
    fmt = (format_tag.to_bytes(2, "little") + (1).to_bytes(2, "little")
           + (8000).to_bytes(4, "little") + (16000).to_bytes(4, "little")
           + (2).to_bytes(2, "little") + (16).to_bytes(2, "little"))
    body = b"WAVE" + extra_chunk + b"fmt " + len(fmt).to_bytes(4, "little") + fmt
    body += b"data" + (4).to_bytes(4, "little") + b"\0\0\0\0"
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def extensible_header(subformat_tag):
    fmt = ((0xFFFE).to_bytes(2, "little") + (1).to_bytes(2, "little")
           + (8000).to_bytes(4, "little") + (16000).to_bytes(4, "little")
           + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
           + (22).to_bytes(2, "little") + (16).to_bytes(2, "little")
           + (4).to_bytes(4, "little") + subformat_tag.to_bytes(2, "little")
           + bytes(14))
    body = b"WAVE" + b"fmt " + len(fmt).to_bytes(4, "little") + fmt
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def id3(payload, size=20):
    syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x03\x00\x00" + syncsafe + b"\x00" * size + payload


def ogg(packet):
    return b"OggS\x00\x02" + b"\x00" * 20 + b"\x01" + b"\x1e" + packet + b"\x00" * 16


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            return stat.read_text().split()[2] != "Z"
        except (OSError, IndexError):
            return False
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                            stdout=subprocess.PIPE, text=True, check=False)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


class SniffTests(unittest.TestCase):
    SAMPLES = {
        "pcm.wav": (wave_header(1), "wav"),
        "float.wav": (wave_header(3), "wav"),
        "junk_first.wav": (wave_header(1, b"JUNK" + (6).to_bytes(4, "little") + b"\0" * 6), "wav"),
        "extensible_pcm.wav": (extensible_header(1), "wav"),
        "extensible_mp3.wav": (extensible_header(0x55), "wav-codec"),
        "mp3_in.wav": (wave_header(0x55), "wav-codec"),
        "adpcm.wav": (wave_header(2), "wav-codec"),
        "rf64.wav": (b"RF64\xff\xff\xff\xffWAVEds64" + b"\0" * 8, "wav-codec"),
        "phone.3gpp": (b"\x00\x00\x00\x18ftyp3gp4" + b"\0" * 8, "3gp"),
        "phone.3g2": (b"\x00\x00\x00\x18ftyp3g2a" + b"\0" * 8, "3gp"),
        "voice.m4a": (b"\x00\x00\x00\x20ftypM4A " + b"\0" * 8, "mp4"),
        "old.m4a": (b"\x00\x00\x01\x00moov" + b"\0" * 8, "mp4"),
        "memo.amr": (b"#!AMR\n" + b"\0" * 10, "amr"),
        "memo_wb.amr": (b"#!AMR-WB\n" + b"\0" * 10, "amr"),
        "talk.wma": (bytes.fromhex("3026b2758e66cf11") + b"\0" * 8, "asf"),
        "clip.webm": (b"\x1aE\xdf\xa3" + b"\0" * 12, "matroska"),
        "raw.aac": (b"\xff\xf1\x50\x80" + b"\0" * 12, "adts"),
        "frame.mp3": (b"\xff\xfb\x90\x00" + b"\0" * 12, "mp3"),
        "layer2.mp2": (b"\xff\xfd\x90\x00" + b"\0" * 12, "mpeg-audio"),
        "tagged.mp3": (id3(b"\xff\xfb\x90\x00" + b"\0" * 8), "mp3"),
        "tagged.mp2": (id3(b"\xff\xfd\x90\x00" + b"\0" * 8), "mpeg-audio"),
        "tagged.aac": (id3(b"\xff\xf1\x50\x80" + b"\0" * 8), "adts"),
        "tagged.flac": (id3(b"fLaC" + b"\0" * 8), "flac"),
        "padded_tag.mp3": (id3(b"\0\0\0\xff\xfb\x90\x00" + b"\0" * 8), "mp3"),
        "sound.flac": (b"fLaC\x00\x00\x00\x22" + b"\0" * 8, "flac"),
        "sound.aiff": (b"FORM\x00\x00\x00\x10AIFF" + b"\0" * 8, "aiff"),
        "sound.aifc": (b"FORM\x00\x00\x00\x10AIFC" + b"\0" * 8, "aifc"),
        "vorbis.ogg": (ogg(b"\x01vorbis"), "ogg-vorbis"),
        "voice.opus": (ogg(b"OpusHead"), "ogg-opus"),
        "flac.oga": (ogg(b"\x7fFLAC"), "ogg-flac"),
        "speex.spx": (ogg(b"Speex   "), "ogg-speex"),
        "dvd.mpg": (b"\x00\x00\x01\xba" + b"\0" * 12, "mpeg-ps"),
        "notes.txt": (b"hello there, this is not audio", None),
        "empty": (b"", None),
    }

    def test_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, (data, expected) in self.SAMPLES.items():
                target = Path(tmp) / name
                target.write_bytes(data)
                with self.subTest(sample=name):
                    self.assertEqual(gui.sniff_audio_kind(target), expected)

    def test_missing_file(self):
        self.assertIsNone(gui.sniff_audio_kind(Path(tempfile.gettempdir()) / "no_such_file.3gpp"))

    def test_describe_kind(self):
        self.assertEqual(gui.describe_audio_kind("3gp", "call.3GPP"), "3GPP (.3gpp)")
        self.assertEqual(gui.describe_audio_kind(None), "this audio format")


class ConversionPolicyTests(unittest.TestCase):
    def engine(self, native, strict=False):
        return types.SimpleNamespace(native_kinds=native, strict_formats=strict)

    def test_pygame_converts_what_sdl_mixer_cannot_decode(self):
        pygame_like = self.engine(gui.PYGAME_NATIVE_KINDS)
        for kind in ("3gp", "amr", "mp4", "adts", "asf", "matroska", "mpeg-ps",
                     "mpeg-audio", "wav-codec", "ogg-flac", "ogg-speex", "aifc"):
            self.assertTrue(gui.engine_needs_conversion(pygame_like, kind), kind)
        for kind in ("wav", "mp3", "ogg-vorbis", "ogg-opus", "flac", "aiff", None):
            self.assertFalse(gui.engine_needs_conversion(pygame_like, kind), kind)

    def test_ffplay_and_mpv_read_everything(self):
        self.assertFalse(gui.engine_needs_conversion(self.engine(None), "3gp"))

    def test_players_that_fail_late_convert_unknown_files(self):
        aplay_like = self.engine(frozenset({"wav", "au"}), strict=True)
        self.assertTrue(gui.engine_needs_conversion(aplay_like, None))
        self.assertFalse(gui.engine_needs_conversion(aplay_like, "wav"))

    def test_no_conversion_for_players_without_wav(self):
        mpg123_like = self.engine(frozenset({"mp3", "mpeg-audio"}), strict=True)
        self.assertFalse(gui.engine_needs_conversion(mpg123_like, "3gp"))

    def test_engines_declare_their_formats(self):
        self.assertEqual(gui.PygameEngine.native_kinds, gui.PYGAME_NATIVE_KINDS)
        self.assertEqual(gui.MciEngine.native_kinds, gui.MCI_NATIVE_KINDS)
        self.assertIsNone(gui.NullEngine.native_kinds)


class FakeEngine:
    """Behaves like pygame: plays WAV only, anything else fails to load."""

    name = "fake"
    supports_seek = True
    supports_pause = True
    native_kinds = frozenset({"wav", "mp3"})

    def __init__(self):
        self.calls = []
        self.busy = False
        self.released = 0
        self.error = None

    def play(self, path, offset=0.0):
        self.calls.append((Path(path), offset))
        if Path(path).suffix.lower() != ".wav":
            raise gui.PlaybackError("pygame could not play this file: modplug_load failed")
        self.busy = True

    def pause(self):
        self.busy = False

    def resume(self):
        self.busy = True

    def stop(self):
        self.busy = False

    def release(self):
        self.released += 1
        self.busy = False

    def finished(self):
        return not self.busy

    def take_error(self):
        error, self.error = self.error, None
        return error

    def close(self):
        self.busy = False


class FakeConverter:
    def __init__(self, folder, available=True):
        self.folder = Path(folder)
        self.available = available
        self.cache = {}
        self.converted = []
        self.cancels = 0
        self.closed = False

    def cached(self, source):
        return self.cache.get(Path(source))

    def begin(self):
        return 0

    def convert(self, source, token=None, progress=None):
        self.converted.append(Path(source))
        target = write_wav(self.folder / (Path(source).stem + "-copy.wav"), frames=16000)
        self.cache[Path(source)] = target
        return target

    def cancel(self):
        self.cancels += 1

    def close(self):
        self.closed = True


class PlayerConversionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.phone = self.folder / "call.3gpp"
        self.phone.write_bytes(b"\x00\x00\x00\x18ftyp3gp4" + b"\0" * 64)
        self.engine = FakeEngine()
        self.converter = FakeConverter(self.folder)
        self.player = gui.AudioPlayer(self.engine, self.converter)
        # Keep the tests independent of ffprobe/mutagen on this machine.
        patcher = mock.patch.object(gui, "detect_duration", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_3gpp_requires_conversion_instead_of_failing(self):
        self.player.load(self.phone)
        self.assertEqual(self.player.kind, "3gp")
        self.assertTrue(self.player.needs_conversion)
        with self.assertRaises(gui.ConversionRequired) as caught:
            self.player.play(convert=False)
        self.assertIn("3GPP (.3gpp)", str(caught.exception))
        self.assertEqual(self.engine.calls, [])        # pygame never sees it
        self.assertEqual(self.converter.converted, [])
        self.assertFalse(self.player.is_playing)

    def test_play_converts_and_plays_the_copy(self):
        self.player.load(self.phone)
        self.player.play()
        copy = self.converter.cache[self.phone]
        self.assertEqual(self.engine.calls, [(copy, 0.0)])
        self.assertTrue(self.player.is_playing)
        self.assertEqual(self.player.play_path, copy)
        self.assertAlmostEqual(self.player.duration, 2.0)   # filled from the WAV

    def test_seek_and_resume_use_the_copy(self):
        self.player.load(self.phone)
        self.player.play()
        self.player.seek(1.5)
        copy = self.converter.cache[self.phone]
        self.assertEqual(self.engine.calls[-1], (copy, 1.5))
        self.assertEqual(self.converter.converted, [self.phone])

    def test_rejected_unknown_file_falls_back_to_conversion(self):
        odd = self.folder / "recording.bin"
        odd.write_bytes(b"\x12\x34" * 64)
        self.player.load(odd)
        self.assertIsNone(self.player.kind)
        self.assertFalse(self.player.needs_conversion)     # try it directly first
        with self.assertRaises(gui.ConversionRequired):
            self.player.play(convert=False)
        self.assertTrue(self.player.needs_conversion)
        self.player.play()
        self.assertTrue(self.player.is_playing)
        self.assertEqual(self.player.play_path.suffix, ".wav")

    def test_without_ffmpeg_the_error_explains_the_fix(self):
        self.converter.available = False
        self.player.load(self.phone)
        self.assertFalse(self.player.needs_conversion)
        with self.assertRaises(gui.PlaybackError) as caught:
            self.player.play()
        message = str(caught.exception)
        self.assertIn("modplug_load failed", message)
        self.assertIn("Install FFmpeg", message)
        self.assertIn("3GPP (.3gpp)", message)

    def test_native_files_play_directly(self):
        song = write_wav(self.folder / "song.wav")
        self.player.load(song)
        self.player.play()
        self.assertEqual(self.engine.calls, [(song, 0.0)])
        self.assertIsNone(self.player.play_path)

    def test_stale_conversion_result_is_ignored(self):
        self.player.load(self.phone)
        other = write_wav(self.folder / "other.wav")
        self.player.load(other)
        copy = write_wav(self.folder / "late.wav")
        self.assertFalse(self.player.use_converted(self.phone, copy))
        self.assertIsNone(self.player.play_path)

    def test_load_reuses_an_existing_copy(self):
        copy = write_wav(self.folder / "earlier.wav")
        self.converter.cache[self.phone] = copy
        self.player.load(self.phone)
        self.assertFalse(self.player.needs_conversion)
        self.player.play(convert=False)
        self.assertEqual(self.engine.calls, [(copy, 0.0)])

    def test_load_releases_the_backend_and_cancels_conversions(self):
        self.player.load(self.phone)
        self.assertEqual(self.engine.released, 1)
        self.assertEqual(self.converter.cancels, 1)

    def test_unload_forgets_the_file(self):
        self.player.load(self.phone)
        self.player.unload()
        self.assertIsNone(self.player.path)
        self.assertEqual(self.engine.released, 2)

    def test_close_deletes_the_copies(self):
        self.player.close()
        self.assertTrue(self.converter.closed)


class ClockTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.song = write_wav(Path(tmp.name) / "song.wav")
        self.engine = FakeEngine()
        self.player = gui.AudioPlayer(self.engine, FakeConverter(tmp.name))
        self.player.load(self.song)

    def test_a_short_duration_estimate_does_not_cut_playback(self):
        self.player.duration = 1.0
        self.player.play()
        self.player._started -= 2.0          # two seconds of playback later
        self.assertFalse(self.player.update())
        self.assertTrue(self.engine.busy)    # the audio keeps playing
        self.assertGreaterEqual(self.player.duration, 2.0)
        self.engine.busy = False             # the backend reaches the end
        self.assertTrue(self.player.update())
        self.assertFalse(self.player.is_playing)

    def test_a_player_failure_is_reported(self):
        self.player.seek(0.5)
        self.player.play()
        self.engine.busy = False
        self.engine.error = "aplay could not play the file: read error"
        self.assertTrue(self.player.update())
        self.assertEqual(self.player.last_error, "aplay could not play the file: read error")
        self.assertAlmostEqual(self.player.position, 0.5)

    def test_backend_without_state_falls_back_to_the_duration(self):
        self.player.duration = 1.0
        self.player.play()
        self.engine.finished = mock.Mock(side_effect=RuntimeError("mixer gone"))
        self.player._started -= 1.5
        self.assertTrue(self.player.update())


class FakePopen:
    """Stands in for FFmpeg: writes a WAV to the output and reports progress."""

    instances = []
    returncode_value = 0
    error_text = b""

    def __init__(self, command, stdin=None, stdout=None, stderr=None, creationflags=0):
        type(self).instances.append(self)
        self.command = command
        self.returncode = type(self).returncode_value
        if self.returncode == 0:
            write_wav(Path(command[-1]), frames=4000)
        if stderr is not None and type(self).error_text:
            stderr.write(type(self).error_text)
        self.stdout = io.BytesIO(
            b"out_time_us=250000\nprogress=continue\nout_time_us=N/A\n"
            b"out_time_ms=500000\nprogress=end\n"
        )

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        pass

    def kill(self):
        pass


class ConverterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.ffmpeg = self.folder / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        self.ffmpeg.write_text("stub", encoding="utf-8")
        self.source = self.folder / "call.3gpp"
        self.source.write_bytes(b"\x00\x00\x00\x18ftyp3gp4" + b"\0" * 64)
        FakePopen.instances = []
        FakePopen.returncode_value = 0
        FakePopen.error_text = b""
        patcher = mock.patch.object(gui.subprocess, "Popen", FakePopen)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.converter = gui.PlaybackConverter(ffmpeg=str(self.ffmpeg), max_files=2)
        self.addCleanup(self.converter.close)

    def test_command_decodes_the_first_audio_track_to_pcm_wav(self):
        progress = []
        copy = self.converter.convert(self.source, progress=progress.append)
        command = FakePopen.instances[0].command
        self.assertEqual(command[0], str(self.ffmpeg))
        for expected in (["-map", "0:a:0"], ["-c:a", "pcm_s16le"], ["-f", "wav"],
                         ["-progress", "pipe:1"], ["-i", str(self.source)]):
            index = command.index(expected[0])
            self.assertEqual(command[index:index + 2], expected)
        self.assertIn("-nostdin", command)
        self.assertIn("-vn", command)
        self.assertTrue(command[-1].endswith(".wav.part"))
        self.assertEqual(progress, [0.25, 0.5])
        self.assertEqual(copy.suffix, ".wav")
        self.assertTrue(copy.parent.name.startswith(gui.PlaybackConverter.PREFIX))
        self.assertFalse(list(copy.parent.glob("*.part")))
        self.assertEqual(self.converter.cached(self.source), copy)

    def test_copy_is_reused_until_the_source_changes(self):
        first = self.converter.convert(self.source)
        self.assertEqual(self.converter.convert(self.source), first)
        self.assertEqual(len(FakePopen.instances), 1)
        self.source.write_bytes(self.source.read_bytes() + b"more")
        self.assertIsNone(self.converter.cached(self.source))
        self.assertNotEqual(self.converter.convert(self.source), first)
        self.assertEqual(len(FakePopen.instances), 2)

    def test_only_recent_copies_are_kept(self):
        copies = []
        for name in ("a.3gpp", "b.3gpp", "c.3gpp"):
            source = self.folder / name
            source.write_bytes(self.source.read_bytes())
            copies.append(self.converter.convert(source))
        self.assertFalse(copies[0].exists())
        self.assertTrue(copies[1].exists() and copies[2].exists())

    def test_ffmpeg_errors_are_reported(self):
        FakePopen.returncode_value = 1
        FakePopen.error_text = b"call.3gpp: Invalid data found when processing input\n"
        with self.assertRaises(gui.PlaybackError) as caught:
            self.converter.convert(self.source)
        self.assertIn("Invalid data found", str(caught.exception))
        self.assertFalse(list(self.converter.folder.glob("*")))

    def test_file_without_audio(self):
        FakePopen.returncode_value = 1
        FakePopen.error_text = b"Stream map '0:a:0' matches no streams.\n"
        with self.assertRaises(gui.PlaybackError) as caught:
            self.converter.convert(self.source)
        self.assertIn("has no audio track", str(caught.exception))
        self.assertEqual(len(FakePopen.instances), 1)

    def test_cancel_before_the_worker_starts(self):
        token = self.converter.begin()
        self.converter.cancel()        # another file was selected meanwhile
        with self.assertRaises(gui.PlaybackCancelled):
            self.converter.convert(self.source, token=token)
        self.assertEqual(FakePopen.instances, [])

    def test_missing_ffmpeg(self):
        converter = gui.PlaybackConverter(ffmpeg=str(self.folder / "nope" / "ffmpeg"))
        self.assertFalse(converter.available)
        with self.assertRaises(gui.PlaybackError) as caught:
            converter.convert(self.source)
        self.assertIn("FFmpeg", str(caught.exception))

    def test_close_deletes_the_folder(self):
        copy = self.converter.convert(self.source)
        self.converter.close()
        self.assertFalse(copy.parent.exists())
        self.assertIsNone(self.converter.cached(self.source))

    def test_progress_lines(self):
        self.assertEqual(gui.parse_progress_seconds(b"out_time_us=1500000\n"), 1.5)
        self.assertEqual(gui.parse_progress_seconds("out_time_ms=2000000"), 2.0)
        self.assertIsNone(gui.parse_progress_seconds("out_time_us=N/A"))
        self.assertIsNone(gui.parse_progress_seconds("progress=continue"))

    def test_sweep_removes_only_stale_playback_folders(self):
        root = self.folder / "tmp"
        root.mkdir()
        stale = root / (gui.PlaybackConverter.PREFIX + "old")
        fresh = root / (gui.PlaybackConverter.PREFIX + "new")
        other = root / "someone_else"
        for folder in (stale, fresh, other):
            folder.mkdir()
        old = time.time() - 3 * 24 * 3600
        os.utime(stale, (old, old))
        os.utime(other, (old, old))
        self.assertEqual(gui.sweep_stale_playback_folders(max_age=3600, root=root), 1)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists() and other.exists())


class ToolLookupTests(unittest.TestCase):
    def test_ffmpeg_beside_the_script_and_ffprobe_beside_ffmpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            suffix = ".exe" if sys.platform == "win32" else ""
            (folder / ("ffmpeg" + suffix)).write_text("stub", encoding="utf-8")
            (folder / ("ffprobe" + suffix)).write_text("stub", encoding="utf-8")
            with mock.patch.object(gui, "APP_DIR", folder):
                self.assertEqual(gui.find_tool("ffmpeg"), str(folder / ("ffmpeg" + suffix)))
            explicit = str(folder / ("ffmpeg" + suffix))
            with mock.patch.multiple(gui, FFMPEG=explicit, FFPROBE="no_such_ffprobe_tool"):
                self.assertEqual(gui.find_ffprobe(), str(folder / ("ffprobe" + suffix)))

    def test_parse_ffmpeg_duration(self):
        output = "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'call.3gpp':\n  Duration: 01:02:03.50, start: 0.0"
        self.assertAlmostEqual(gui.parse_ffmpeg_duration(output), 3723.5)
        self.assertIsNone(gui.parse_ffmpeg_duration("  Duration: N/A, bitrate: N/A"))
        self.assertIsNone(gui.parse_ffmpeg_duration(""))

    def test_implausible_durations_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "adpcm.wav"
            target.write_bytes(wave_header(2) + b"\0" * 200_000)
            probes = dict(
                _duration_wave=lambda path: 0.001,   # compressed data read as PCM
                _duration_mutagen=lambda path, kind=None: 0.002,
                _duration_ffprobe=lambda path: None,
                _duration_ffmpeg=lambda path: 12.5,
                _duration_mci=lambda path: None,
                _duration_pygame=lambda path: None,
            )
            with mock.patch.multiple(gui, **probes):
                self.assertEqual(gui.detect_duration(target), 12.5)


class TextFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def test_newlines_are_normalised(self):
        target = self.folder / "a.txt"
        target.write_bytes("one\r\ntwo\rthree\n\u00e9".encode("utf-8"))
        self.assertEqual(gui.read_text_with_encoding(target), ("one\ntwo\nthree\n\u00e9", "utf-8"))

    def test_legacy_code_page_is_decoded_not_replaced(self):
        target = self.folder / "ru.txt"
        target.write_bytes("Привет мир".encode("cp1251"))
        with mock.patch.object(gui.locale, "getpreferredencoding", return_value="cp1251"):
            self.assertEqual(gui.read_text_with_encoding(target), ("Привет мир", "cp1251"))

    def test_undecodable_bytes_are_flagged(self):
        target = self.folder / "bad.txt"
        target.write_bytes(b"ok \xff\xfe")
        with mock.patch.object(gui.locale, "getpreferredencoding", return_value="UTF-8"):
            text, encoding = gui.read_text_with_encoding(target)
        self.assertIn("\ufffd", text)
        self.assertNotEqual(encoding, "utf-8")

    def test_write_text_replaces_the_file_with_lf_utf8(self):
        target = self.folder / "out.txt"
        target.write_text("old", encoding="utf-8")
        gui.write_text(target, "line one\nline \u00e9")
        self.assertEqual(target.read_bytes(), "line one\nline \u00e9".encode("utf-8"))
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["out.txt"])


@unittest.skipIf(sys.platform == "win32", "POSIX process groups")
class ProcessTreeTests(unittest.TestCase):
    def test_stop_process_tree_ends_grandchildren(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "grandchild.pid"
            script = (
                "import subprocess, sys\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
                "child.wait()\n"
            )
            process = subprocess.Popen([sys.executable, "-c", script], **gui.tree_popen_options())
            deadline = time.monotonic() + 10
            while not pid_file.exists() or not pid_file.read_text():
                self.assertLess(time.monotonic(), deadline, "child did not start")
                time.sleep(0.05)
            grandchild = int(pid_file.read_text())
            gui.stop_process_tree(process, grace=1.0, wait=True)
            process.wait(timeout=5)
            deadline = time.monotonic() + 5
            while process_alive(grandchild) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(process_alive(grandchild))


class Recorder:
    def __init__(self):
        self.options = {}
        self.bindings = {}

    def config(self, **kwargs):
        self.options.update(kwargs)

    def bind(self, sequence, func=None, add=None):
        self.bindings[sequence] = func


class BlockingConverter(FakeConverter):
    """convert() waits until cancel(), like a long FFmpeg run."""

    def __init__(self, folder):
        super().__init__(folder)
        self.cancelled = threading.Event()

    def convert(self, source, token=None, progress=None):
        if progress is not None:
            progress(1.0)
        if self.cancelled.wait(10):
            raise gui.PlaybackCancelled("cancelled")
        return super().convert(source)

    def cancel(self):
        super().cancel()
        self.cancelled.set()


class GuiPlaybackFlowTests(unittest.TestCase):
    """AudioTextGUI's background conversion, without a real Tk window."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.phone = self.folder / "call.3gpp"
        self.phone.write_bytes(b"\x00\x00\x00\x18ftyp3gp4" + b"\0" * 64)
        for name, value in (("detect_duration", mock.Mock(return_value=None)),
                            ("messagebox", mock.Mock())):
            patcher = mock.patch.object(gui, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_app(self, converter):
        app = gui.AudioTextGUI.__new__(gui.AudioTextGUI)
        app.player = gui.AudioPlayer(FakeEngine(), converter)
        app.player.load(self.phone)
        app.current_audio = self.phone
        app.worker_queue = queue.Queue()
        app._prepare_path = None
        app._play_when_ready = False
        app._prepare_started = 0.0
        app.play_button = Recorder()
        app.engine_label = Recorder()
        app.statuses = []
        app.logs = []
        app.set_status = app.statuses.append
        app.log = app.logs.append
        app._update_highlight = lambda position: None
        app._update_playback_widgets = lambda: None
        return app

    def drain_until_idle(self, app, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app._drain_worker_queue()
            if app._prepare_path is None and app.worker_queue.empty():
                return
            time.sleep(0.02)
        self.fail("conversion did not finish")

    def test_play_converts_in_the_background_then_starts(self):
        app = self.make_app(FakeConverter(self.folder))
        app.toggle_play()
        self.assertEqual(app.play_button.options["text"], "Cancel")
        self.drain_until_idle(app)
        self.assertTrue(app.player.is_playing)
        self.assertEqual(app.player.engine.calls[-1][0].suffix, ".wav")
        self.assertEqual(app.play_button.options["text"], "Pause")
        gui.messagebox.showerror.assert_not_called()

    def test_second_click_cancels_the_preparation(self):
        converter = BlockingConverter(self.folder)
        app = self.make_app(converter)
        app.toggle_play()
        app.toggle_play()               # the button reads "Cancel" now
        self.assertTrue(converter.cancelled.is_set())
        self.assertEqual(app.play_button.options["text"], "Play")
        time.sleep(0.1)
        self.drain_until_idle(app)
        self.assertFalse(app.player.is_playing)

    def test_result_for_a_previous_file_is_ignored(self):
        app = self.make_app(BlockingConverter(self.folder))
        app.toggle_play()
        other = write_wav(self.folder / "other.wav")
        app._cancel_prepare()
        app.player.load(other)
        app.current_audio = other
        app._on_prepare_done(self.phone, write_wav(self.folder / "late.wav"), None)
        self.assertFalse(app.player.is_playing)
        self.assertIsNone(app.player.play_path)

    def test_conversion_error_is_shown(self):
        converter = FakeConverter(self.folder)
        converter.convert = mock.Mock(
            side_effect=gui.PlaybackError("FFmpeg could not decode call.3gpp: Invalid data")
        )
        app = self.make_app(converter)
        app.toggle_play()
        self.drain_until_idle(app)
        gui.messagebox.showerror.assert_called_once()
        self.assertIn("Invalid data", gui.messagebox.showerror.call_args[0][1])
        self.assertFalse(app.player.is_playing)

    def test_shortcuts_are_bound_on_the_editor_too(self):
        app = gui.AudioTextGUI.__new__(gui.AudioTextGUI)
        app.root = Recorder()
        app.editor = Recorder()
        app.auto_align_var = None
        for name in ("save_text", "choose_folder", "choose_audio_file",
                     "toggle_play", "refine_from_marks"):
            setattr(app, name, mock.Mock())
        fake_tk = types.SimpleNamespace(Menu=mock.MagicMock())
        with mock.patch.object(gui, "tk", fake_tk):
            app.build_menu()
        for sequence, _command in app.shortcuts():
            self.assertIn(sequence, app.root.bindings)
            self.assertIn(sequence, app.editor.bindings)
        # Ctrl+O in the editor opens the folder and stops Tk's Text class
        # binding, which would insert a newline into the transcript.
        self.assertEqual(app.editor.bindings["<Control-o>"](None), "break")
        app.choose_folder.assert_called_once_with()


def find_ffmpeg_for_tests():
    return gui.find_ffmpeg()


@unittest.skipUnless(find_ffmpeg_for_tests(), "FFmpeg is not installed")
class RealFFmpegTests(unittest.TestCase):
    """End to end with the FFmpeg on this machine (skipped without it)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name)
        cls.phone = cls.folder / "call.3gpp"
        command = [find_ffmpeg_for_tests(), "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
                   "-ar", "8000", "-ac", "1", "-c:a", "aac", "-f", "3gp", str(cls.phone)]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False, creationflags=gui.hidden_process_flags())
        if result.returncode != 0 or not cls.phone.exists():
            cls.tmp.cleanup()
            raise unittest.SkipTest("this FFmpeg cannot write a 3GP test file")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_3gpp_is_sniffed_and_measured(self):
        self.assertEqual(gui.sniff_audio_kind(self.phone), "3gp")
        self.assertAlmostEqual(gui.detect_duration(self.phone), 1.5, delta=0.15)
        self.assertAlmostEqual(gui._duration_ffmpeg(self.phone), 1.5, delta=0.15)

    def test_converter_writes_a_playable_wav(self):
        converter = gui.PlaybackConverter()
        try:
            copy = converter.convert(self.phone)
            self.assertEqual(gui.sniff_audio_kind(copy), "wav")
            self.assertAlmostEqual(gui.wave_duration(copy), 1.5, delta=0.15)
        finally:
            converter.close()
        self.assertFalse(copy.exists())

    def test_pygame_plays_3gpp_through_the_copy(self):
        try:
            import pygame  # noqa: F401
        except ImportError:
            self.skipTest("pygame is not installed")
        with mock.patch.dict(os.environ, {"SDL_AUDIODRIVER": "dummy"}):
            try:
                engine = gui.PygameEngine()
            except Exception as error:   # no usable audio driver
                self.skipTest(f"pygame mixer unavailable: {error}")
            player = gui.AudioPlayer(engine, gui.PlaybackConverter())
            try:
                player.load(self.phone)
                self.assertTrue(player.needs_conversion)
                player.play()          # used to raise "modplug_load failed"
                self.assertTrue(player.is_playing)
                self.assertEqual(player.play_path.suffix, ".wav")
                player.seek(0.5)
                self.assertTrue(player.is_playing)
            finally:
                player.close()


if __name__ == "__main__":
    unittest.main()
