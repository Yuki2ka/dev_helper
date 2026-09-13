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
IMAGE_W_MIN = -1
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
    "webPll": {"lossless": True}
}
# === SETTINGS END ===

def constrain_resize(img: Image.Image, w_max, w_min, h_max, h_min) -> Image.Image:
    """Resize image keeping aspect ratio, respecting min/max bounds."""
    w, h = img.size
    scale = 1.0

    # Apply MAX constraints (shrink if needed)
    if w_max is not None: scale = min(scale, w_max / w)
    if h_max is not None: scale = min(scale, h_max / h)

    # Apply MIN constraints (grow if needed; max takes priority)
    if w_min is not None and (w * scale) < w_min:
        scale = max(scale, w_min / w)
    if h_min is not None and (h * scale) < h_min:
        scale = max(scale, h_min / h)

    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))

    # Force minimum dimensions after rounding to handle edge cases
    if w_min is not None and new_w < w_min:
        ratio = w_min / new_w
        new_w = w_min
        new_h = max(1, int(new_h * ratio))
    if h_min is not None and new_h < h_min:
        ratio = h_min / new_h
        new_h = h_min
        new_w = max(1, int(new_w * ratio))

    if (new_w, new_h) != (w, h):
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        return img.resize((new_w, new_h), resample)
    return img

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Combine multiple images into a grid")
    g = globals()
    parser.add_argument("path", nargs="?", default=None,
                        help="Input directory or file containing source images")
    parser.add_argument("-o", "--output", default=None,
                        help="Output file path (default: combined.<format> in input dir)")
    parser.add_argument("--rows", type=int, default=g.get("ROWS", 2),
                        help="Number of rows in grid")
    parser.add_argument("--cols", type=int, default=g.get("COLS", -1),
                        help="Number of columns, -1 for auto calculation")
    parser.add_argument("--arrange", default=g.get("ARRANGE_ORDER", "leftToRight"),
                        choices=["leftToRight", "topToDown"],
                        help="Image arrangement order")
    parser.add_argument("--sort", default=g.get("SORT_BY", "NAME"),
                        choices=["NAME", "DATE", "NONE"],
                        help="Sort order for images")
    parser.add_argument("--img-w-max", type=int, default=g.get("IMAGE_W_MAX", 512),
                        help="Max image width, -1 for none")
    parser.add_argument("--img-w-min", type=int, default=g.get("IMAGE_W_MIN", -1),
                        help="Min image width, -1 for none")
    parser.add_argument("--img-h-max", type=int, default=g.get("IMAGE_H_MAX", 256),
                        help="Max image height, -1 for none")
    parser.add_argument("--img-h-min", type=int, default=g.get("IMAGE_H_MIN", 256),
                        help="Min image height, -1 for none")
    parser.add_argument("--format", default=g.get("OUTPUT_FORMAT", "avif"),
                        help="Output format key from FORMAT_PRESETS")
    parser.add_argument("--pad", type=int, default=g.get("CELL_PADDING", 2),
                        help="Cell padding in pixels")
    parser.add_argument("--bg", default="255,255,255",
                        help="Background color R,G,B")
    parser.add_argument("--alpha", type=int, default=0,
                        help="Alpha channel 0-255")
    return parser.parse_args(argv)

def main(argv=None):
    global ROWS, COLS, ARRANGE_ORDER, SORT_BY
    global IMAGE_W_MAX, IMAGE_W_MIN, IMAGE_H_MAX, IMAGE_H_MIN
    global OUTPUT_FORMAT, CELL_PADDING, BACKGROUND_COLOR
    args = parse_args(argv)

    ROWS = args.rows
    COLS = None if args.cols == -1 else args.cols
    ARRANGE_ORDER = args.arrange
    SORT_BY = args.sort
    IMAGE_W_MAX = None if args.img_w_max == -1 else args.img_w_max
    IMAGE_W_MIN = None if args.img_w_min == -1 else args.img_w_min
    IMAGE_H_MAX = None if args.img_h_max == -1 else args.img_h_max
    IMAGE_H_MIN = None if args.img_h_min == -1 else args.img_h_min
    OUTPUT_FORMAT = args.format
    CELL_PADDING = args.pad
    BACKGROUND_COLOR = (*tuple(int(v) for v in args.bg.split(",")), args.alpha)

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

    output_filename = out_path.name

    valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif", ".avif", ".jxl", ".heic", ".heif", ".svg"}
    all_files = [
        f for f in iter_existing_files(resolved.paths, recursive=True)
        if f.suffix.lower() in valid_exts
    ]
    files = [f for f in all_files if f.name != output_filename]
    
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
            img = Image.open(f).convert("RGBA")
            img = constrain_resize(img, IMAGE_W_MAX, IMAGE_W_MIN, IMAGE_H_MAX, IMAGE_H_MIN)
            images.append(img)
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

    # 5. Calculate Exact Row and Column Dimensions
    col_widths = [0] * cols
    row_heights = [0] * rows

    for r in range(rows):
        for c in range(cols):
            if (img := grid[r][c]) is not None:
                w, h = img.size
                col_widths[c] = max(col_widths[c], w)
                row_heights[r] = max(row_heights[r], h)

    # Compute clean total canvas footprint
    canvas_w = sum(col_widths) + max(0, (cols - 1) * CELL_PADDING)
    canvas_h = sum(row_heights) + max(0, (rows - 1) * CELL_PADDING)

    # 6. Create Canvas & Center-Align Images Inside Cells
    canvas = Image.new("RGBA", (canvas_w, canvas_h), BACKGROUND_COLOR)

    for r in range(rows):
        for c in range(cols):
            if (img := grid[r][c]) is not None:
                w, h = img.size

                # Base grid cell coordinates
                cell_x = sum(col_widths[:c]) + c * CELL_PADDING
                cell_y = sum(row_heights[:r]) + r * CELL_PADDING

                # Center placement offset within the cell bounding box
                offset_x = (col_widths[c] - w) // 2
                offset_y = (row_heights[r] - h) // 2

                canvas.paste(img, (cell_x + offset_x, cell_y + offset_y), img)

    save_kwargs = FORMAT_PRESETS.get(OUTPUT_FORMAT, {})
    canvas.save(str(out_path), OUTPUT_FORMAT.upper(), **save_kwargs)

    print(f"✅ Successfully saved to {out_path} ({canvas_w}x{canvas_h}px)")

if __name__ == "__main__":
    main()
