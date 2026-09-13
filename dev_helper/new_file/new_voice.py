#!/usr/bin/env python3
# === SETTINGS START ===
PATH = None
NUMBER = 1
FORMAT = "wav"
TEXT = "Voice sample {n}"
RATE = 180
VOLUME = 0.5
# === SETTINGS END ===
import sys
from pathlib import Path

# Ensure project root is importable for path_args package
_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

from path_args import resolve_paths, choose_output_file, resolve_input_text

import argparse
import os
import re
import subprocess

try:
    import pyttsx3
except ImportError:
    print("Missing dependency: pyttsx3", file=sys.stderr)
    print("Install with: pip install pyttsx3", file=sys.stderr)
    sys.exit(1)



def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Create numbered voice files with auto-increment & Opus support"
    )
    parser.add_argument("path", nargs="?", default=PATH, help="Output filename or folder")
    parser.add_argument("-n", "--number", type=int, default=NUMBER, help=f"Number of files (default: {NUMBER})")
    parser.add_argument("-t", "--text", default=None, help='Text to speak. If omitted, uses stdin/clipboard.')
    parser.add_argument("--format", default=FORMAT, choices=["wav", "opus"], help="Audio format (default: opus)")
    parser.add_argument("--rate", type=int, default=RATE, help=f"Speech rate (default: {RATE})")
    parser.add_argument("-v", "--volume", type=float, default=VOLUME, help=f"Volume 0.0-1.0 (default: {VOLUME})")
    return parser.parse_args(argv)


def save_tts_to_format(engine, text, out_path_str, fmt):
    """Saves TTS to WAV first, then converts to Opus if requested."""
    fmt = fmt.lower()
    tmp_wav = out_path_str + ".tmp.wav" if fmt != "wav" else out_path_str
    
    try:
        engine.save_to_file(text, tmp_wav)
        engine.runAndWait()
    except Exception as e:
        print(f"TTS generation failed for {out_path_str}: {e}", file=sys.stderr)
        return None

    if fmt == "opus":
        success = False
        for cmd in [
            ["ffmpeg", "-y", "-i", tmp_wav, "-c:a", "libopus", out_path_str],
            ["opusenc", tmp_wav, out_path_str]
        ]:
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                success = True
                break
            except (subprocess.SubprocessError, FileNotFoundError):
                continue
        
        os.remove(tmp_wav)
        
        if not success:
            fallback_path = os.path.splitext(out_path_str)[0] + ".wav"
            os.rename(tmp_wav, fallback_path)
            print(f"Warning: Opus encoder missing. Saved as WAV instead: {fallback_path}", file=sys.stderr)
            return fallback_path
            
    return out_path_str


def main(argv=None):
    args = parse_args(argv)

    # 1. Resolve text using command_paths (handles args -> stdin -> clipboard automatically)
    resolved_text = resolve_input_text(args, arg_names=("text",))
    if not resolved_text.text.strip():
        print("Error: No text provided. Pass --text '...', pipe via stdin, or copy to clipboard.", file=sys.stderr)
        sys.exit(1)
    
    base_text = resolved_text.text

    # 2. Resolve output location using command_paths
    resolved = resolve_paths(args, arg_names=("path",), create="auto")
    dir_path = resolved.first  # Path object

    # Determine base name for auto-increment logic
    if dir_path.is_dir():
        base_name = "voice"
    else:
        base_name = dir_path.stem

    m = re.match(r"^(.*?)(\d+)$", base_name)
    prefix = m.group(1) if m else base_name
    counter = int(m.group(2)) if m else 1

    vol = max(0.0, min(1.0, args.volume))
    engine = pyttsx3.init()
    engine.setProperty("rate", args.rate)
    engine.setProperty("volume", vol)

    for i in range(args.number):
        idx = counter + i
        stem = f"{prefix}{idx}"
        ext = f".{args.format}"
        
        candidate_path = dir_path / f"{stem}{ext}"
        
        # Safe collision resolution using command_paths.choose_output_file
        final_path = choose_output_file(
            location=candidate_path,
            default_stem=stem,
            default_suffix=ext
        )

        # Apply template formatting only if placeholders exist
        file_text = base_text
        try:
            if "{" in base_text and "}" in base_text:
                file_text = base_text.format(n=i + 1, name=final_path.name, index=i)
        except KeyError:
            pass

        print(f"Generating: {final_path}...")
        success_path = save_tts_to_format(engine, file_text, str(final_path), args.format)
        
        if success_path:
            print(f"Created: {success_path}")
        else:
            print(f"Skipped: {final_path}", file=sys.stderr)

    try:
        engine.stop()
    except Exception:
        pass


if __name__ == "__main__":
    main()
