# === SETTINGS START ===
PATH = None
NUMBER = 1
FORMAT = "wav"
TEXT = "Voice sample {n}"
RATE = 180
# === SETTINGS END ===

import sys
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

from path_args import resolve_paths, choose_output_file, resolve_input_text

try:
    import pyttsx3
except ImportError:
    print("Missing dependency: pyttsx3", file=sys.stderr)
    print("Install with: pip install pyttsx3", file=sys.stderr)
    sys.exit(1)

import argparse
import re

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Create numbered voice files with auto-increment"
    )

    parser.add_argument(
        "path",
        nargs="?",
        default=PATH,
        help="Output filename or folder",
    )

    parser.add_argument(
        "-n",
        "--number",
        type=int,
        default=NUMBER,
        help=f"Number of files (default: {NUMBER})",
    )

    parser.add_argument(
        "--text",
        default=TEXT,
        help=f'Text template (default: "{TEXT}")',
    )

    parser.add_argument(
        "--rate",
        type=int,
        default=RATE,
        help=f"Speech rate (default: {RATE})",
    )

    parser.add_argument(
        "--format",
        default=FORMAT,
        choices=["wav"],
        help="Audio format (wav)",
    )

    return parser.parse_args(argv)


def create_voice_file(engine, text, out_path):
    engine.save_to_file(text, out_path)
    engine.runAndWait()


def main(argv=None):
    global FORMAT

    args = parse_args(argv)
    FORMAT = args.format

    resolved_text = resolve_input_text(args, arg_names=("text",), constant=TEXT)
    if not resolved_text.text.strip():
        print("Error: No text provided.", file=sys.stderr)
        sys.exit(1)
    base_text = resolved_text.text

    resolved = resolve_paths(
        args,
        arg_names=("path",),
        create="auto",
    )
    dir_path = resolved.first

    if dir_path.is_dir():
        base_name = str(args.number)
    else:
        base_name = dir_path.stem or str(args.number)

    m = re.match(r"^(.*?)(\d+)$", base_name)
    prefix = m.group(1) if m else base_name
    counter = int(m.group(2)) if m else 1

    engine = pyttsx3.init()
    engine.setProperty("rate", args.rate)

    for i in range(args.number):
        idx = counter + i
        stem = f"{prefix}{idx}"
        ext = f".{FORMAT}"

        candidate_path = dir_path / f"{stem}{ext}"

        final_path = choose_output_file(
            location=candidate_path,
            default_stem=stem,
            default_suffix=ext,
        )

        text = base_text.format(
            n=i + 1,
            name=final_path.name,
            index=i,
        )

        create_voice_file(engine, text, str(final_path))
        print(f"Created: {final_path}")

    try:
        engine.stop()
    except Exception:
        pass


if __name__ == "__main__":
    main()