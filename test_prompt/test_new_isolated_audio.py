import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev_helper" / "new_file"))
import new_isolated_audio as tool


@pytest.fixture
def api(monkeypatch):
    api = Mock()
    api.load_audio.return_value = (object(), 44100)
    monkeypatch.setitem(sys.modules, "inworld", SimpleNamespace(voice_isolation=api))
    return api


def test_companion(tmp_path, api):
    source = tmp_path / "two words.wav"
    source.write_bytes(b"original")

    def save(path, *_args, **_kwargs):
        Path(path).write_bytes(b"isolated")

    api.save_audio.side_effect = save
    tool.main([str(source)])
    output = tmp_path / "two words.isolated.flac"
    assert output.read_bytes() == b"isolated"
    assert source.read_bytes() == b"original"
    api.VoiceIsolator.assert_called_once_with("inworld/voice-isolator")
    model = api.VoiceIsolator.return_value
    model.isolate.assert_called_once_with(*api.load_audio.return_value, output_sample_rate=48000)
    api.save_audio.assert_called_once_with(str(output), model.isolate.return_value, 48000, subtype="PCM_24")
    with pytest.raises(FileExistsError):
        tool.isolate_file(source)
    assert output.read_bytes() == b"isolated"


def test_failed_save_leaves_no_companion(tmp_path, api):
    source = tmp_path / "speech.wav"
    source.touch()
    api.save_audio.side_effect = OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        tool.isolate_file(source)
    assert list(tmp_path.iterdir()) == [source]


def test_missing_decoder(tmp_path, api, monkeypatch):
    source = tmp_path / "speech.m4a"
    source.touch()
    api.load_audio.side_effect = RuntimeError("unknown format")
    monkeypatch.setattr(tool.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="Install FFmpeg"):
        tool.isolate_file(source)
    api.VoiceIsolator.assert_not_called()
