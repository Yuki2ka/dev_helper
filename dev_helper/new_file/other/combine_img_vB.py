#!/usr/bin/env python3
import os
from pathlib import Path
from PIL import Image
from math import ceil

# === SETTINGS START ===
ROWS = 2
COLS = 0  # None or 0 = auto
ARRANGE_ORDER_PRIORITY = "leftToRight"
SORT_BY = "NAME"
CANVAS_SIZE = None
image_W_max = 512
image_W_min = None
image_H_max = 256
image_H_min = 256
OUTPUT_FILE = "combined.webp"
# === SETTINGS END ===

IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".jxl",
    ".bmp", ".gif", ".webp", ".avif",
    ".tif", ".tiff", ".heic", ".heif", ".svg"
}


def resize_with_limits(img):
    w, h = img.size

    scale_down = min(
        image_W_max / w if image_W_max else 999999,
        image_H_max / h if image_H_max else 999999,
        1e9
    )

    if scale_down < 1:
        w = max(1, int(w * scale_down))
        h = max(1, int(h * scale_down))

    scale_up = 1.0

    if image_W_min and w < image_W_min:
        scale_up = max(scale_up, image_W_min / w)

    if image_H_min and h < image_H_min:
        scale_up = max(scale_up, image_H_min / h)

    if scale_up > 1:
        w = max(1, int(w * scale_up))
        h = max(1, int(h * scale_up))

    w = min(w, image_W_max) if image_W_max else w
    h = min(h, image_H_max) if image_H_max else h

    return img.resize((w, h), Image.LANCZOS)


def get_images():
    files = [
        p for p in Path(".").iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ]

    if SORT_BY == "NAME":
        files.sort(key=lambda x: x.name.lower())

    elif SORT_BY == "DATE":
        files.sort(key=lambda x: x.stat().st_mtime)

    return files


def calc_grid(n):
    if ARRANGE_ORDER_PRIORITY == "FillWithBestSize":
        cols = ceil(n ** 0.5)
        rows = ceil(n / cols)
        return rows, cols

    rows = ROWS if ROWS else None
    cols = COLS if COLS else None

    if rows and not cols:
        cols = ceil(n / rows)

    elif cols and not rows:
        rows = ceil(n / cols)

    elif not rows and not cols:
        cols = ceil(n ** 0.5)
        rows = ceil(n / cols)

    return rows, cols


def build_canvas(images):
    rows, cols = calc_grid(len(images))

    col_widths = [0] * cols
    row_heights = [0] * rows

    positions = []

    for idx, img in enumerate(images):

        if ARRANGE_ORDER_PRIORITY == "topToDown":
            r = idx % rows
            c = idx // rows
        else:
            r = idx // cols
            c = idx % cols

        if r >= rows or c >= cols:
            continue

        col_widths[c] = max(col_widths[c], img.width)
        row_heights[r] = max(row_heights[r], img.height)

        positions.append((r, c))

    canvas_w = sum(col_widths)
    canvas_h = sum(row_heights)

    if CANVAS_SIZE:
        canvas_w, canvas_h = CANVAS_SIZE

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 0))

    x_offsets = []
    x = 0
    for w in col_widths:
        x_offsets.append(x)
        x += w

    y_offsets = []
    y = 0
    for h in row_heights:
        y_offsets.append(y)
        y += h

    for img, (r, c) in zip(images, positions):
        x = x_offsets[c]
        y = y_offsets[r]
        canvas.paste(img, (x, y))

    return canvas


def main():
    files = get_images()

    if not files:
        print("No images found.")
        return

    images = []

    for f in files:
        try:
            img = Image.open(f).convert("RGBA")
            img = resize_with_limits(img)
            images.append(img)
            print("Added:", f.name, img.size)
        except Exception as e:
            print("Skipped:", f, e)

    result = build_canvas(images)

    result.save(
        OUTPUT_FILE,
        "WEBP",
        quality=95,
        method=6
    )

    print("Saved:", OUTPUT_FILE)
    print("Canvas:", result.size)


if __name__ == "__main__":
    main()