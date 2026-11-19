#!/usr/bin/env python3
"""Write a voice-isolated FLAC beside a recording, without changing the source."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

from path_args import resolve_paths


def load_recording(api, source):
    try:
        return api.load_audio(str(source))
    except RuntimeError as error:  # libsndfile cannot open some formats (e.g. AAC).
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError(f"Cannot decode {source}: {error}. Install FFmpeg on PATH or supply a WAV file.") from error

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "decoded.wav"
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(source.absolute()),
                        "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-c:a", "pcm_f32le", str(wav)], check=True)
        return api.load_audio(str(wav))


def isolate_file(source):
    if not source.is_file():
        raise ValueError(f"Not an input file: {source}")
    destination = source.with_name(source.stem + ".isolated.flac")
    if os.path.lexists(destination):
        raise FileExistsError(f"Companion already exists: {destination}")

    try:
        from inworld import voice_isolation as api
    except ImportError as error:
        raise RuntimeError("Install the isolation dependency with Python 3.10+: "
                           "python -m pip install inworld-voice-isolation") from error

    samples, rate = load_recording(api, source)
    model = api.VoiceIsolator("inworld/voice-isolator")
    voice = model.isolate(samples, rate, output_sample_rate=48000)

    # Reserve the name exclusively even if another run finished during inference.
    # The upstream writer stages the encoded file before replacing the reservation.
    destination.touch(exist_ok=False)
    try:
        api.save_audio(str(destination), voice, 48000, subtype="PCM_24")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create a voice-isolated FLAC beside the input recording.")
    parser.add_argument("source", nargs="?", help="Audio file (or supply one path via stdin or clipboard)")
    args = parser.parse_args(argv)
    try:
        paths = resolve_paths(args, arg_names=("source",), fallback_to_cwd=False, create="none")
        if len(paths) != 1:
            raise ValueError("Select exactly one audio file.")
        print(isolate_file(paths.first))
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()
