#!/usr/bin/env python3
"""Create adjacent UTF-8 transcripts using audio.cpp's Qwen3-ASR CLI.

Inputs: CLI paths -> stdin -> clipboard file references -> PATH -> cwd.
Requires an audio.cpp-compatible Qwen3-ASR-1.7B GGUF and audiocpp_cli.
FFmpeg is needed unless input is already mono 16 kHz PCM16 WAV.
No models or executables are downloaded automatically.
"""

# === SETTINGS START ===
PATH = None
MODEL = "D:\LLM_software\audiocpp\models\Qwen3-ASR-1.7B-GGUF\qwen3-asr-1.7b-q8_0.gguf" # Local audio.cpp-compatible GGUF file or dir
AUDIOCPP_CLI = "D:\LLM_software\audiocpp\audiocpp_cli.exe"  # Executable path, next to script, or on PATH
FFMPEG = "ffmpeg"
BACKEND = "cuda"             # cpu, cuda, vulkan, metal, best
LANGUAGE = None             # None = automatic; e.g. English
RECURSIVE = True
OVERWRITE = False
# === SETTINGS END ===

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))
from path_args import iter_existing_files, resolve_paths

AUDIO_EXTENSIONS = {
    ".wav", ".wave", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac",
    ".wma", ".aiff", ".aif", ".aifc", ".alac", ".amr", ".mka", ".weba",
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Audio files and/or folders")
    parser.add_argument("--model", default=os.environ.get("QWEN3_ASR_MODEL") or MODEL,
                        help="GGUF file/model directory (or QWEN3_ASR_MODEL env var)")
    parser.add_argument("--cli", default=os.environ.get("AUDIOCPP_CLI") or AUDIOCPP_CLI,
                        help="Path/name of audiocpp_cli executable")
    parser.add_argument("--ffmpeg", default=FFMPEG, help="Path/name of ffmpeg executable")
    parser.add_argument("--backend", choices=("cpu", "cuda", "vulkan", "metal", "best"),
                        default=BACKEND)
    parser.add_argument("--language", default=LANGUAGE, help="Optional recognition language hint")
    parser.add_argument("--no-recursive", dest="recursive", action="store_false",
                        default=RECURSIVE, help="Do not scan subfolders")
    parser.add_argument("--overwrite", action="store_true", default=OVERWRITE,
                        help="Replace existing transcripts only after successful transcription")
    return parser.parse_args(argv)


def find_executable(value):
    """Accept explicit paths, a binary beside this script, or a PATH entry."""
    value = os.path.expanduser(str(value))
    candidate = Path(value)
    if candidate.is_file():
        return str(candidate.resolve())
    for name in (value, value + ".exe") if sys.platform == "win32" else (value,):
        local = Path(__file__).resolve().parent / name
        if local.is_file():
            return str(local)
    found = shutil.which(value)
    if found:
        return found
    raise RuntimeError(f"Executable not found: {value}. Put it on PATH, beside this script, "
                       "or specify its full path.")


def collect_audio_files(paths, recursive=True):
    seen = set()
    for path in iter_existing_files(paths, recursive=recursive):
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        key = os.path.normcase(str(path.resolve()))
        if key not in seen:
            seen.add(key)
            yield path.absolute()


def is_ready_wav(path):
    try:
        with wave.open(str(path), "rb") as audio:
            return (audio.getnchannels() == 1 and audio.getframerate() == 16000
                    and audio.getsampwidth() == 2 and audio.getcomptype() == "NONE")
    except (wave.Error, EOFError):
        return False


def run_command(command):
    # Logs stay out of the transcript. Inherit stdout/stderr for progress/errors.
    result = subprocess.run(command, stdin=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} exited with code {result.returncode}")


def transcribe(source, target, args, cli, model):
    with tempfile.TemporaryDirectory(prefix="dev_helper_asr_") as work:
        work = Path(work)
        audio = source
        if not is_ready_wav(source):
            audio = work / "input.wav"
            ffmpeg = find_executable(args.ffmpeg)
            run_command([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                         "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                         "-c:a", "pcm_s16le", str(audio)])
        transcript = work / "transcript.txt"
        command = [cli, "--task", "asr", "--family", "qwen3_asr", "--model", str(model),
                   "--backend", args.backend, "--audio", str(audio), "--text", "",
                   "--text-out", str(transcript)]
        if args.language:
            command.extend(["--language", args.language])
        run_command(command)
        if not transcript.is_file():
            raise RuntimeError("audio.cpp succeeded but did not create --text-out output")
        text = transcript.read_text(encoding="utf-8")
        # Empty transcripts are valid for silence. Never use stdout as a fallback.
        if args.overwrite:
            # Stage on the destination filesystem so replacing an old transcript is atomic.
            staged = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                                 dir=target.parent, delete=False) as out:
                    staged = Path(out.name)
                    out.write(text)
                os.replace(staged, target)
            finally:
                if staged is not None:
                    staged.unlink(missing_ok=True)
        else:
            # Exclusive creation also protects files created while inference was running.
            with target.open("x", encoding="utf-8", newline="\n") as out:
                out.write(text)


def main(argv=None):
    args = parse_args(argv)
    paths = list(resolve_paths(args, arg_names=("paths",), constant=PATH,
                               fallback_to_cwd=True, create="none"))
    missing = [p for p in paths if not p.exists()]
    for path in missing:
        print(f"Input does not exist: {path}", file=sys.stderr)
    files = sorted(collect_audio_files(paths, args.recursive), key=str)
    if not files:
        print("No supported audio files found.")
        return 1

    jobs = []
    claimed = set()
    errors = len(missing)
    skipped = 0
    for source in files:
        target = source.with_suffix(".txt")
        key = os.path.normcase(str(target.resolve()))
        if key in claimed:
            print(f"Conflicting companion path: {source} -> {target}; skipped", file=sys.stderr)
            errors += 1
            continue
        claimed.add(key)
        if target.exists() and not args.overwrite:
            print(f"Skip existing: {target}")
            skipped += 1
            continue
        jobs.append((source, target))

    if not jobs:
        print(f"Done. Created 0; skipped {skipped}; errors {errors}.")
        return 1 if errors else 0
    try:
        if not args.model:
            raise RuntimeError("Set --model, QWEN3_ASR_MODEL, or MODEL to your local "
                               "audio.cpp-compatible Qwen3-ASR-1.7B GGUF.")
        model = Path(args.model).expanduser().resolve()
        if not model.exists():
            raise RuntimeError(f"Model not found: {model}")
        cli = find_executable(args.cli)
    except (OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    created = 0
    for source, target in jobs:
        print(f"Transcribing: {source}", flush=True)
        try:
            transcribe(source, target, args, cli, model)
            created += 1
            print(f"Created: {target}")
        except (OSError, RuntimeError, UnicodeError) as exc:
            errors += 1
            print(f"Error processing {source}: {exc}", file=sys.stderr)
    print(f"Done. Created {created}; skipped {skipped}; errors {errors}.")
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
