#!/usr/bin/env python3
import argparse
import math
import sys
from pathlib import Path
from PIL import Image

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))
from path_args import resolve_paths, choose_output_file, iter_existing_files

# === SETTINGS START ===
OVERWRITE = True            # Allow overwriting existing output file
ROWS = 2
COLS = -1                 # -1 or None or comment out = auto-calculate
ARRANGE_ORDER = "leftToRight"  # "leftToRight" | "topToDown"
SORT_BY = "NAME"            # "NAME" | "DATE" | "NONE"
IMAGE_W_MAX = 512
IMAGE_W_MIN = 0 # non positive number is no limit
IMAGE_H_MAX = 256
IMAGE_H_MIN = 256
OUTPUT_FILE = "combined.avif"
OUTPUT_FORMAT = "avif"      # Format key from FORMAT_PRESETS
CELL_PADDING = 2            # Padding between cells (pixels)
BACKGROUND_COLOR = (255, 255, 255, 0)  # Transparent white background
FORMAT_PRESETS = {
    "avif":   {"quality": 90, "effort": 9},
    "webp":   {"quality": 95, "method": 6},
    "png":    {"compress_level": 9},
}
# === SETTINGS END ===

def constrain_resize(img: Image.Image, w_max, w_min, h_max, h_min) -> Image.Image:
    """Resize while preserving aspect ratio and respecting feasible bounds.

    Minimum dimensions define a lower scale and maximum dimensions define an
    upper scale.  If those ranges conflict, maximum dimensions win so a minimum
    cannot unexpectedly create an enormous output image.
    """
    w, h = img.size
    min_scale = max(
        w_min / w if w_min is not None else 0.0,
        h_min / h if h_min is not None else 0.0,
    )
    max_scale = min(
        w_max / w if w_max is not None else math.inf,
        h_max / h if h_max is not None else math.inf,
    )

    # Keep the original size when it already lies in the feasible interval.
    # In a conflicting interval, cap at the maximum rather than violating it.
    scale = min(max(1.0, min_scale), max_scale)
    new_w = max(1, round(w * scale))
    new_h = max(1, round(h * scale))

    if (new_w, new_h) != (w, h):
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        return img.resize((new_w, new_h), resample)
    return img


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def _nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def _alpha(value: str) -> int:
    number = int(value)
    if not 0 <= number <= 255:
        raise argparse.ArgumentTypeError("must be between 0 and 255")
    return number


def _rgb(value: str) -> tuple[int, int, int]:
    try:
        components = tuple(int(component.strip()) for component in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be R,G,B integers") from exc
    if len(components) != 3 or any(component < 0 or component > 255 for component in components):
        raise argparse.ArgumentTypeError("must contain three values between 0 and 255")
    return components


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Combine multiple images into a grid")
    g = globals()
    parser.add_argument("path", nargs="?", default=None,
                        help="Input directory or file containing source images")
    parser.add_argument("-o", "--output", default=None,
                        help="Output file path (default: combined.<format> in input dir)")
    parser.add_argument("--rows", type=_positive_int, default=g.get("ROWS", 2),
                        help="Number of rows in grid (must be positive)")
    parser.add_argument("--cols", type=int, default=g.get("COLS", -1),
                        help="Number of columns, non-positive to auto-calculate")
    parser.add_argument("--arrange", default=g.get("ARRANGE_ORDER", "leftToRight"),
                        choices=["leftToRight", "topToDown"],
                        help="Image arrangement order")
    parser.add_argument("--sort", default=g.get("SORT_BY", "NAME"),
                        choices=["NAME", "DATE", "NONE"],
                        help="Sort order for images")
    parser.add_argument("--img-w-max", type=int, default=g.get("IMAGE_W_MAX", -1),
                        help="Max image width, non-positive to disable")
    parser.add_argument("--img-w-min", type=int, default=g.get("IMAGE_W_MIN", -1),
                        help="Min image width, non-positive to disable")
    parser.add_argument("--img-h-max", type=int, default=g.get("IMAGE_H_MAX", -1),
                        help="Max image height, non-positive to disable")
    parser.add_argument("--img-h-min", type=int, default=g.get("IMAGE_H_MIN", -1),
                        help="Min image height, non-positive to disable")
    parser.add_argument("--format", default=g.get("OUTPUT_FORMAT", "avif"),
                        choices=sorted(FORMAT_PRESETS),
                        help="Output format")
    parser.add_argument("--pad", type=_nonnegative_int, default=g.get("CELL_PADDING", 2),
                        help="Cell padding in pixels (must be non-negative)")
    default_bg = g.get("BACKGROUND_COLOR", (255, 255, 255, 0))
    parser.add_argument("--bg", type=_rgb, default=tuple(default_bg[:3]),
                        help="Background color R,G,B")
    parser.add_argument("--alpha", type=_alpha, default=default_bg[3],
                        help="Alpha channel 0-255")
    return parser.parse_args(argv)

def main(argv=None):
    global ROWS, COLS, ARRANGE_ORDER, SORT_BY
    global IMAGE_W_MAX, IMAGE_W_MIN, IMAGE_H_MAX, IMAGE_H_MIN
    global OUTPUT_FORMAT, CELL_PADDING, BACKGROUND_COLOR
    args = parse_args(argv)

    ROWS = args.rows
    COLS = None if args.cols <= 0 else args.cols
    ARRANGE_ORDER = args.arrange
    SORT_BY = args.sort
    IMAGE_W_MAX = None if args.img_w_max <= 0 else args.img_w_max
    IMAGE_W_MIN = None if args.img_w_min <= 0 else args.img_w_min
    IMAGE_H_MAX = None if args.img_h_max <= 0 else args.img_h_max
    IMAGE_H_MIN = None if args.img_h_min <= 0 else args.img_h_min
    OUTPUT_FORMAT = args.format
    CELL_PADDING = args.pad
    BACKGROUND_COLOR = (*args.bg, args.alpha)

    resolved = resolve_paths(
        args,
        arg_names=("path",),
        create="none",
        fallback_to_cwd=True,
    )

    input_location = resolved.first
    if input_location.is_file():
        source_dir = input_location.parent
    else:
        source_dir = input_location

    if args.output:
        out_location = args.output
    else:
        out_location = source_dir if source_dir.is_dir() else Path(".")

    out_path = choose_output_file(
        location=out_location,
        default_stem="combined",
        default_suffix=f".{OUTPUT_FORMAT}",
        overwrite=OVERWRITE,
    )

    output_path_resolved = out_path.resolve()

    valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif", ".avif", ".jxl", ".heic", ".heif", ".svg"}
    all_files = [
        f for f in iter_existing_files(resolved.paths, recursive=True)
        if f.suffix.lower() in valid_exts
    ]
    files = [f for f in all_files if f.resolve() != output_path_resolved]
    
    if SORT_BY == "NAME":
        files.sort()
    elif SORT_BY == "DATE":
        files.sort(key=lambda x: x.stat().st_mtime)
    
    if not files:
        print("❌ No valid images found in current directory.")
        return

    print(f"📂 Found {len(files)} images. Sorting by: {SORT_BY}")

    # 3. Load & Resize
    images = []
    for f in files:
        try:
            with Image.open(f) as source:
                img = source.convert("RGBA")
            resized = constrain_resize(
                img, IMAGE_W_MAX, IMAGE_W_MIN, IMAGE_H_MAX, IMAGE_H_MIN
            )
            if resized is not img:
                img.close()
            images.append(resized)
        except Exception as e:
            print(f"⚠️ Skipping {f.name}: {e}")

    if not images:
        print("❌ No images could be loaded.")
        return

    # 4. Grid Setup
    n = len(images)
    rows = ROWS
    cols = COLS if (COLS is not None and COLS > 0) else math.ceil(n / rows)

    # Auto-expand columns if grid is too small to hold all images
    if n > rows * cols:
        cols = math.ceil(n / rows)
        print(f"⚠️ Grid too small — auto-expanded COLS to {cols} to fit all images.")

    grid = [[None for _ in range(cols)] for _ in range(rows)]

    for i, img in enumerate(images):
        if ARRANGE_ORDER == "leftToRight":
            r, c = divmod(i, cols)
        else:  # ARRANGE_ORDER == "topToDown"
            r, c = i % rows, i // rows

        if r < rows and c < cols:
            grid[r][c] = img

    # 5. Calculate Canvas Size
    col_widths = [0] * cols
    row_heights = [0] * rows

    for r in range(rows):
        for c in range(cols):
            if (img := grid[r][c]) is not None:
                w, h = img.size
                col_widths[c] = max(col_widths[c], w)
                row_heights[r] = max(row_heights[r], h)

    canvas_w = sum(col_widths) + (cols - 1) * CELL_PADDING
    canvas_h = sum(row_heights) + (rows - 1) * CELL_PADDING

    # 6. Create Canvas & Paste. Prefix offsets avoid repeatedly summing slices
    # for every cell in large grids.
    col_offsets = []
    offset = 0
    for width in col_widths:
        col_offsets.append(offset)
        offset += width + CELL_PADDING

    row_offsets = []
    offset = 0
    for height in row_heights:
        row_offsets.append(offset)
        offset += height + CELL_PADDING

    canvas = Image.new("RGBA", (canvas_w, canvas_h), BACKGROUND_COLOR)
    try:
        for r in range(rows):
            for c in range(cols):
                if (img := grid[r][c]) is not None:
                    canvas.paste(img, (col_offsets[c], row_offsets[r]), img)

        save_kwargs = FORMAT_PRESETS[OUTPUT_FORMAT]
        canvas.save(str(out_path), OUTPUT_FORMAT, **save_kwargs)
    finally:
        canvas.close()
        for img in images:
            img.close()

    print(f"✅ Successfully saved to {out_path} ({canvas_w}x{canvas_h}px)")

if __name__ == "__main__":
    main()
