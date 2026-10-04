#!/usr/bin/env python3
# === SETTINGS START ===
PATH = None                 # Hardcoded fallback input file/folder (used only if it exists)
OUTPUT_PREFIX = "animation"
FRAME_DURATION = 1000       # Duration of newly added frames, in milliseconds
LOOP = 0                    # 0 = infinite loop
QUALITY = 90                # 0..100 = lossy WebP, any other value = lossless WebP
RESIZE_HEIGHT = 512         # Target frame height; < 1 = use the first image size
METHOD = 6                  # 0 = fastest, 6 = slowest / best compression
OVERWRITE = False           # Allow overwriting existing output file
RECURSIVE = True            # Include images from subfolders when a folder is the input
# === SETTINGS END ===
"""
Create an animated WebP from image files.

Inputs use the standard path_args resolution order:
CLI args -> piped stdin paths -> clipboard file references -> PATH constant -> cwd.
So you can copy images in a file manager and just run the script, or pass
files/folders as arguments.

If any input is an animated WebP, its existing frames are preserved via
webpmux (put webpmux next to this script or in PATH:
https://github.com/webmproject/libwebp/releases).
Otherwise the whole animation is built with Pillow alone.

Output: animation_<timestamp>.webp next to the first input file
(or to the exact path given with -o).
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))
from path_args import choose_output_file, iter_existing_files, resolve_paths

SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".webp",
}


def parse_args(argv=None):
    g = globals()
    parser = argparse.ArgumentParser(
        description="Create an animated WebP from image files"
    )
    parser.add_argument("paths", nargs="*", default=None,
                        help="Image files and/or folders containing images")
    parser.add_argument("-o", "--output", default=None,
                        help="Output file or folder (default: next to the first input image)")
    parser.add_argument("--frame-duration", type=int, default=g.get("FRAME_DURATION", 1000),
                        help="Duration of newly added frames, in milliseconds")
    parser.add_argument("--loop", type=int, default=g.get("LOOP", 0),
                        help="0 = infinite loop")
    parser.add_argument("--quality", type=int, default=g.get("QUALITY", 90),
                        help="0..100 = lossy WebP, any other value = lossless WebP")
    parser.add_argument("--resize-height", type=int, default=g.get("RESIZE_HEIGHT", 512),
                        help="Target frame height, < 1 = use the first image size")
    parser.add_argument("--method", type=int, default=g.get("METHOD", 6),
                        help="0 = fastest, 6 = slowest / best compression")
    parser.add_argument("--prefix", default=g.get("OUTPUT_PREFIX", "animation"),
                        help="Output file name prefix")
    return parser.parse_args(argv)


def find_webpmux():
    """Find webpmux next to this script or in PATH."""
    executable = "webpmux.exe" if sys.platform == "win32" else "webpmux"

    local_path = Path(__file__).resolve().parent / executable

    if local_path.is_file():
        return local_path

    system_path = shutil.which(executable)

    if system_path:
        return Path(system_path)

    return None


def run_webpmux(*arguments):
    """Run webpmux."""
    webpmux_path = find_webpmux()

    if webpmux_path is None:
        raise RuntimeError(
            "webpmux is required because an animated WebP is being used.\n\n"
            "Put webpmux in the same folder as this script:\n\n"
            "    new_animated_webP_from_files.py\n"
            "    webpmux\n\n"
            "Or add webpmux to PATH.\n\n"
            "Download:\n"
            "https://github.com/webmproject/libwebp/releases"
        )

    command = [str(webpmux_path), *map(str, arguments)]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"webpmux error:\n{message}")

    return result.stdout + result.stderr


def natural_sort_key(path):
    """Sort 1.png, 2.webp, 10.jpg in natural order."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


def is_animated_webp(file_path):
    """Check whether a WebP contains more than one frame."""
    try:
        with Image.open(file_path) as image:
            return getattr(image, "n_frames", 1) > 1
    except Exception:
        return False


def use_lossy():
    """True when QUALITY selects lossy compression."""
    return isinstance(QUALITY, (int, float)) and 0 <= QUALITY <= 100


def quality_options():
    """Return Pillow WebP compression options."""
    options = {
        "format": "WEBP",
        "method": METHOD,
    }

    if use_lossy():
        options["quality"] = int(QUALITY)
    else:
        options["lossless"] = True

    return options


def get_canvas_size(first_file):
    """
    Determine the output canvas size.

    If RESIZE_HEIGHT >= 1, the first image is scaled to that height.
    Otherwise, the original first image size is used.
    """
    with Image.open(first_file) as image:
        if RESIZE_HEIGHT >= 1:
            width = round(image.width * RESIZE_HEIGHT / image.height)
            return width, RESIZE_HEIGHT

        return image.width, image.height


def prepare_image(image, canvas_size):
    """Fit an image into the output canvas while preserving its aspect ratio."""
    image = image.convert("RGBA")
    image.thumbnail(canvas_size, Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", canvas_size, (255, 255, 255, 0))

    x = (canvas.width - image.width) // 2
    y = (canvas.height - image.height) // 2

    canvas.alpha_composite(image, (x, y))

    return canvas


def encode_static_image(source_path, output_path, canvas_size):
    """Convert a normal image into one WebP frame."""
    with Image.open(source_path) as image:
        image = prepare_image(image, canvas_size)
        image.save(output_path, **quality_options())


def read_webp_frame_durations(file_path):
    """Read existing animated WebP frame durations."""
    info = run_webpmux("-info", file_path)

    match = re.search(
        r"Number of frames:\s*(\d+)",
        info,
        re.IGNORECASE,
    )

    if not match:
        return []

    frame_count = int(match.group(1))

    if frame_count <= 1:
        return []

    durations = {}

    # Standard "webpmux -info" table rows:
    # "  1:   320   240    no        0        0      500   ..."
    for row in re.finditer(
        r"^\s*(\d+):(?:\s+-?\d+){2}\s+\S+\s+-?\d+\s+-?\d+\s+(\d+)",
        info,
        re.MULTILINE,
    ):
        frame_number = int(row.group(1))
        if 1 <= frame_number <= frame_count and frame_number not in durations:
            durations[frame_number] = int(row.group(2))

    # Fallback for the "Frame #N ... duration D" output layout.
    if len(durations) < frame_count:
        for frame_number in range(1, frame_count + 1):
            if frame_number in durations:
                continue

            match = re.search(
                rf"Frame\s*#{frame_number}.*?duration\s+(\d+)",
                info,
                re.IGNORECASE | re.DOTALL,
            )

            if match:
                durations[frame_number] = int(match.group(1))

    return [
        durations.get(frame_number, FRAME_DURATION)
        for frame_number in range(1, frame_count + 1)
    ]


def extract_animated_webp(source_path, temp_dir, index):
    """
    Extract existing frames without re-encoding them.
    """
    durations = read_webp_frame_durations(source_path)
    frames = []

    for frame_number, duration in enumerate(durations, start=1):
        frame_path = Path(temp_dir) / (
            f"existing_{index:06d}_{frame_number:06d}.webp"
        )

        run_webpmux(
            "-get",
            "frame",
            frame_number,
            source_path,
            "-o",
            frame_path,
        )

        frames.append((frame_path, duration))

    return frames


def create_with_pillow(input_files, output_path, canvas_size):
    """
    Create an animation using Pillow.

    This path is used when no animated WebP is present.
    Therefore, webpmux is not needed.
    """
    frames = []

    with tempfile.TemporaryDirectory(prefix="webp_frames_") as temp_dir:
        for index, source_path in enumerate(input_files):
            frame_path = Path(temp_dir) / f"frame_{index:06d}.webp"

            encode_static_image(
                source_path,
                frame_path,
                canvas_size,
            )

            with Image.open(frame_path) as frame:
                frames.append(frame.copy())

        if not frames:
            raise RuntimeError("No frames were created.")

        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=FRAME_DURATION,
            loop=LOOP,
            **quality_options(),
        )


def create_with_webpmux(input_files, output_path, canvas_size):
    """
    Create an animation while preserving existing animated WebP frames.
    """
    all_frames = []

    with tempfile.TemporaryDirectory(prefix="webp_frames_") as temp_dir:
        for index, source_path in enumerate(input_files):
            if (
                source_path.suffix.lower() == ".webp"
                and is_animated_webp(source_path)
            ):
                frames = extract_animated_webp(
                    source_path,
                    temp_dir,
                    index,
                )
            else:
                frame_path = Path(temp_dir) / f"new_{index:06d}.webp"

                encode_static_image(
                    source_path,
                    frame_path,
                    canvas_size,
                )

                frames = [(frame_path, FRAME_DURATION)]

            all_frames.extend(frames)

        command = []

        for frame_path, duration in all_frames:
            command.extend([
                "-frame",
                frame_path,
                f"+{duration}",
            ])

        command.extend([
            "-loop",
            LOOP,
            "-o",
            output_path,
        ])

        run_webpmux(*command)

    return len(all_frames)


def main(argv=None):
    global FRAME_DURATION, LOOP, QUALITY, RESIZE_HEIGHT, METHOD, OUTPUT_PREFIX
    args = parse_args(argv)

    FRAME_DURATION = args.frame_duration
    LOOP = args.loop
    QUALITY = args.quality
    RESIZE_HEIGHT = args.resize_height
    METHOD = args.method
    OUTPUT_PREFIX = args.prefix

    resolved = resolve_paths(
        args,
        arg_names=("paths",),
        constant=PATH,
        create="none",
        fallback_to_cwd=True,
    )

    files = [
        f for f in iter_existing_files(resolved.paths, recursive=RECURSIVE)
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not files:
        print("❌ No supported image files found.")
        print("   Supported formats: PNG, JPG, BMP, GIF, TIFF, and WebP.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    if args.output:
        out_location = Path(args.output)
    else:
        first = resolved.first
        out_location = first if first.is_dir() else first.parent

    out_path = choose_output_file(
        location=out_location,
        default_stem=f"{OUTPUT_PREFIX}_{timestamp}",
        default_suffix=".webp",
        overwrite=OVERWRITE,
    )

    # Never feed a previous output back in as an input frame.
    files = [f for f in files if f.resolve() != out_path.resolve()]

    if not files:
        print("❌ No supported image files found (only the output file is present).")
        return

    files.sort(key=natural_sort_key)

    canvas_size = get_canvas_size(files[0])

    has_animated_webp = any(
        file.suffix.lower() == ".webp"
        and is_animated_webp(file)
        for file in files
    )

    if has_animated_webp:
        processing = "webpmux; existing animated frames preserved"
    else:
        processing = "Pillow; webpmux was not needed"

    try:
        if has_animated_webp:
            frame_count = create_with_webpmux(
                files,
                out_path,
                canvas_size,
            )
        else:
            create_with_pillow(
                files,
                out_path,
                canvas_size,
            )
            frame_count = len(files)
    except Exception as error:
        print(f"❌ Error:\n{error}")
        return

    compression = f"lossy, quality {QUALITY}" if use_lossy() else "lossless"

    print()
    print("✅ Animation created successfully.")
    print(f"Input files: {len(files)} (source: {resolved.origin})")
    print(f"Output frames: {frame_count}")
    print(f"Canvas size: {canvas_size[0]} x {canvas_size[1]}")
    print(f"Compression: {compression}")
    print(f"Encoding method: {METHOD}")
    print(f"Processing: {processing}")
    print("Output file:")
    print(out_path)


if __name__ == "__main__":
    main()
