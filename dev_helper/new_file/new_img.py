#!/usr/bin/env python3
# === SETTINGS START ===
PATH = None
NUMBER = 1
RESOLUTION = "256,256"
FORMAT = 'webp'
GRID = 128
BG = "0,0,0"
BG_END = BG # BG for 360 hue cycle, other color to make NUMBER of steps between
BASE_NAME = ""
# === SETTINGS END ===

import argparse
import colorsys
import os
import re
import sys
from PIL import Image, ImageDraw
from pathlib import Path

from path_args import resolve_paths, choose_output_file


def hsv_to_rgb(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def _color_to_hue(color_str):
    try:
        rgb = tuple(int(v) for v in color_str.split(","))
        h, _, _ = colorsys.rgb_to_hsv(*rgb)
        return h
    except Exception:
        return 0.0


_DEFAULT_BG_END = object()
_BG_END_DEFAULT = BG_END


def parse_bg(bg_str, index, total, bg_end_str=_DEFAULT_BG_END):
    is_auto = bg_str.strip().lower() == "auto"

    if bg_end_str is _DEFAULT_BG_END:
        if is_auto:
            steps = max(1, total - 1)
            return hsv_to_rgb(index / steps, 1.0, 1.0)
        try:
            return tuple(int(v) for v in bg_str.split(","))
        except Exception:
            return (0, 0, 0)

    end_is_auto = bg_end_str.strip().lower() == "auto"

    if is_auto:
        if end_is_auto:
            steps = max(1, total - 1)
            return hsv_to_rgb(index / steps, 1.0, 1.0)
        try:
            return hsv_to_rgb(
                (index / max(1, total - 1)) * _color_to_hue(bg_end_str),
                1.0, 1.0,
            )
        except Exception:
            steps = max(1, total - 1)
            return hsv_to_rgb(index / steps, 1.0, 1.0)

    try:
        start_rgb = tuple(int(v) for v in bg_str.split(","))
    except Exception:
        start_rgb = (0, 0, 0)

    if end_is_auto or bg_end_str.strip().lower() == bg_str.strip().lower():
        start_hue = _color_to_hue(bg_str)
        steps = max(1, total)
        return hsv_to_rgb((index / steps + start_hue) % 1.0, 1.0, 1.0)

    try:
        end_rgb = tuple(int(v) for v in bg_end_str.split(","))
    except Exception:
        return start_rgb

    factor = index / max(1, total - 1)
    return tuple(
        int(start_rgb[c] + (end_rgb[c] - start_rgb[c]) * factor)
        for c in range(3)
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Create numbered images with auto-increment"
    )

    parser.add_argument("path", nargs="?", default=PATH,
                        help="Output filename or folder")
    parser.add_argument("-n", "--number", type=int, default=NUMBER,
                        help=f"Number of images (default: {NUMBER})")
    parser.add_argument("-r", "--resolution", default=RESOLUTION,
                        help=f"W,H (default: {RESOLUTION})")
    parser.add_argument("--format", default=FORMAT,
                        help="Format override (e.g. webp, png)")
    parser.add_argument("--grid", type=int, default=GRID,
                        help=f"Grid cell size (default: {GRID})")
    parser.add_argument("--bg", default=BG,
                        help=f"Background color R,G,B or 'auto' (default: {BG})")
    parser.add_argument("--bg-end", default=BG_END,
                        help=f"End background color R,G,B or 'auto' (default: {BG_END})")

    return parser.parse_args(argv)


def make_image(width, height, grid, label=None, bg_rgb=None):
    bg = tuple(bg_rgb) if bg_rgb else (0, 0, 0)
    fg = tuple(255 - c for c in bg)
    img = Image.new("P", (width, height), color=0)
    draw = ImageDraw.Draw(img)
    palette = list(bg) + list(fg) + [255, 255, 255] * 254
    img.putpalette(palette)
    for x in range(0, width, grid):
        draw.line([(x, 0), (x, height)], fill=1)
    for y in range(0, height, grid):
        draw.line([(0, y), (width, y)], fill=1)
    cx, cy = width // 2, height // 2
    arm = max(2, min(width, height) // 32)
    draw.line([(cx - arm, cy), (cx + arm, cy)], fill=1)
    draw.line([(cx, cy - arm), (cx, cy + arm)], fill=1)
    if label:
        margin = max(2, width // 64)
        draw.text((margin, margin), label, fill=1)
    return img


def main(argv=None):
    global BASE_NAME
    args = parse_args(argv)

    try:
        width, height = map(int, args.resolution.split(","))
    except Exception:
        print("Invalid resolution. Use W,H like 256,256", file=sys.stderr)
        sys.exit(1)

    format_ = args.format
    grid = args.grid
    bg = args.bg
    bg_end = args.bg_end
    number = args.number

    resolved = resolve_paths(
        args,
        arg_names=("path",),
        create="auto",
    )
    dir_path = resolved.paths[0]

    if dir_path.is_dir():
        output_dir = dir_path
    else:
        output_dir = dir_path.parent
        BASE_NAME = dir_path.stem or BASE_NAME

    m = re.match(r'^(.*?)(\d+)$', BASE_NAME)
    prefix = m.group(1) if m else BASE_NAME
    counter = int(m.group(2)) if m else 0

    for i in range(number):
        idx = counter + i
        stem = f"{prefix}{idx}"
        ext = f".{format_}"

        candidate_path = output_dir / f"{stem}{ext}"

        final_path = choose_output_file(
            location=candidate_path,
            default_stem=stem,
            default_suffix=ext,
        )

        label = str(i + 1) if min(width, height) < 32 else final_path.stem
        bg_rgb = parse_bg(bg, i, number, bg_end_str=bg_end)
        img = make_image(width, height, grid, label=label, bg_rgb=bg_rgb)
        save_kwargs = {"format": format_.upper(), "optimize": True}
        if format_.lower() == "webp":
            save_kwargs["lossless"] = True
        img.save(str(final_path), **save_kwargs)
        print(f"Created: {final_path}")


if __name__ == "__main__":
    main()
