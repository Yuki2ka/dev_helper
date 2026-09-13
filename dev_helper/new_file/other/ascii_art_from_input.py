#!/usr/bin/env python3
"""
ascii_art_from_input.py

Input resolution order (first hit wins, then we collect everything useful):
    1. CLI positional path(s)  (- positional args, may be file or directory)
    2. Clipboard bitmap         (a copied image on the clipboard, Windows + PIL)
    3. Clipboard file references (Explorer/Finder copied image files)
    4. Stdin paths             (newline-separated image paths piped in)
    5. Current working directory (scanned for images only when nothing else)

Output:
    * The ASCII art is always printed to the console.
    * It is also written to a file:
        - If the image came from a real file/path  -> a COMPANION file next to
          it, named "<stem>.ascii.txt".
        - Otherwise (clipboard bitmap, stdin text, or no file) -> a file in the
          current working directory, named "ascii_art_YYYYMMDD_HHMMSS.txt".
"""

# === SETTINGS START ===
OUTPUT_SUFFIX = ".ascii.txt"   # Companion file suffix appended to the source stem
WIDTH = 100                    # Output width in characters (height auto-scaled)

# --- Grayscale character ramps (dark -> light) ---------------------------
# Each ramp is a sequence of characters ordered from DARKEST (index 0) to
# LIGHTEST (last index). The conversion maps a pixel's luminance
# (0 = black .. 255 = white) onto the matching character.
#
# Sources / rationale for the "proper" maps:
#   * "standard" (70 levels) and "short" (10 levels) are Paul Bourke's
#     classic ramps (http://paulbourke.net/dataformats/asciiart/) that span
#     a smooth tonal range and read well on a light background.
#   * "density" is a 70-char ramp re-ordered by *measured ink coverage*
#     (proportion of black pixels when the glyph is rendered in a typical
#     monospaced terminal font). It gives a more perceptually even gradient
#     than Bourke's because it accounts for real glyph weight.
#   * "blocks" uses the Unicode shade-block ramp (█▓▒░) for a coarse,
#     high-contrast look.
RAMPS = {
    "standard": r"$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\|()1{}[]?-_+~<>i!lI;:,\"'`^. ",
    "short":    "@%#*+=-:. ",
    "density":  " .'`^\",:;Il!i><~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
    "blocks":   "█▓▒░ ",
}

DEFAULT_RAMP = "blocks"     # Ramp used when nothing else is selected.

HALFTONE_CHARSET = "█▓▒░"      # Dark -> light ramp of Unicode shade blocks
                                  #   █ = full (darkest) ... ░ = light (lightest)

CHARSET = RAMPS[DEFAULT_RAMP]  # Dark -> light ramp (first char = darkest)

FONT_SIZE = 32                  # Glyph render size (px) used only to MEASURE
                                  # each candidate character's ink coverage (tone)
                                  # for the dynamic "Count"/"Start" sliders. This
                                  # is independent of the on-screen ASCII size.
INVERT = False                 # True: invert brightness mapping
CHAR_ASPECT = 0.5             # Terminal cell height/width ratio (chars are taller)
COMPRESSED = False              # True: collapse 1px per char exactly (no aspect fix)
OVERWRITE = False              # False -> rename on collision (_001,_002,...)

# --- Temporal dithering (live animated "random seed" rendering) ----------
TEMPORAL_DITHERING = True    # True: animate in the console, never write a file.
TEMPORAL_FPS = 15.0           # Frames per second for the animation.
TEMPORAL_FRAMES = 0           # 0 = run until Ctrl+C (otherwise stop after N frames).
DITHER_STRENGTH = 0.1         # Per-frame random jitter as a fraction of the
                              #   FULL 0..255 luminance range (0 = none,
                              #   0.5 = +/-128 noise; strong shimmer).
# === SETTINGS END ===

import argparse
import datetime
import os
import platform
import random
import shutil
import sys
import time
import unicodedata
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk  # noqa: F401  (kept for possible future widgets)
    HAS_TK = True
except Exception:  # pragma: no cover - tkinter may be absent on some installs
    tk = None
    ttk = None
    HAS_TK = False

# ----------------------------------------------------
# Add the project root (A:/dev_helper) to sys.path so we can import
# the shared `path_args` module and the `dev_helper` package.
# ----------------------------------------------------
_resolved = Path(__file__).resolve()
# Project convention: the `path_args` package (holding command_paths.py) is
# importable as a top-level module, so put A:/dev_helper/path_args on the path.
_PROJECT_ROOT = _resolved.parents[2] if len(_resolved.parents) >= 3 else None
if _PROJECT_ROOT is not None:
    _path_args_dir = _PROJECT_ROOT / "path_args"
    if str(_path_args_dir) not in sys.path:
        sys.path.insert(0, str(_path_args_dir))
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from command_paths import resolve_paths, iter_existing_files
except ImportError:
    try:
        from path_args.command_paths import resolve_paths, iter_existing_files  # type: ignore
    except ImportError:
        resolve_paths = None  # type: ignore[assignment]
        iter_existing_files = None  # type: ignore[assignment]

try:
    from PIL import Image, ImageGrab, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageGrab = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]
    HAS_PIL = False

IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif",
}


# ==================================================
#  Input gathering
# ==================================================
def get_clipboard_bitmap():
    """Return a PIL Image from the clipboard, or None.

    Mirrors new_file_from_LLM.get_clipboard_bitmap() but returns the PIL Image
    directly (we need the pixels, not encoded bytes).
    """
    if not HAS_PIL or platform.system() != "Windows":
        return None
    try:
        img = ImageGrab.grabclipboard()
        if img is None or not hasattr(img, "save"):
            return None
        return img
    except Exception as e:
        print(f"[!] Clipboard bitmap error: {e}")
        return None


def _scan_images(path: Path):
    """Yield image Paths found in a file (itself) or a directory."""
    if path.is_file():
        if path.suffix.lower() in IMAGE_EXTS:
            yield path
    elif path.is_dir():
        for item in sorted(path.iterdir()):
            if item.is_file() and item.suffix.lower() in IMAGE_EXTS:
                yield item


def collect_sources(args) -> list:
    """Return a list of (kind, value) input sources.

    kind is either 'file' (value: Path to the image) or 'clipboard'
    (value: PIL Image from the clipboard).

    Resolution order (mirrors new_file_from_LLM.py):
        1. CLI positional path(s)  (file or directory, explicit = highest priority)
        2. Clipboard bitmap         (a copied image on the clipboard)
        3. resolve_paths -> iter_existing_files:
             clipboard file refs -> stdin paths -> hardcoded constant -> cwd scan
    """
    sources = []

    # 1. CLI positional path(s) (file or directory).
    for raw in getattr(args, "paths", []) or []:
        p = Path(raw).expanduser()
        if p.exists():
            for img in _scan_images(p):
                sources.append(("file", img))
        elif p.suffix.lower() in IMAGE_EXTS:
            print(f"[-] Path not found, skipped: {p}")

    # 2. Clipboard bitmap (only if no explicit CLI path was given, so the
    #    user's explicit argument always wins).
    if not sources:
        clip = get_clipboard_bitmap()
        if clip is not None:
            sources.append(("clipboard", clip))

    # 3. resolve_paths -> iter_existing_files (clipboard refs -> stdin -> cwd).
    if not sources and resolve_paths is not None and iter_existing_files is not None:
        try:
            resolved = resolve_paths(
                args,
                arg_names=("path", "paths"),
                clipboard=True,
                fallback_to_cwd=True,
            )
            all_files = list(
                iter_existing_files(resolved.paths, recursive=True, include_hidden=False)
            )
            for f in all_files:
                if f.suffix.lower() in IMAGE_EXTS:
                    sources.append(("file", f))
        except Exception as e:
            print(f"[-] Path resolution failed: {e}")

    # Deduplicate file sources by resolved path.
    seen = set()
    deduped = []
    for kind, val in sources:
        key = val if kind == "file" else id(val)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((kind, val))
    return deduped


# ==================================================
#  ASCII conversion
# ==================================================
def image_to_luminance(image, width: int, char_aspect: float, compressed: bool,
                        max_h=None):
    """Resize an image to grayscale and return (pixel_luminances, new_w, new_h).

    The result is cached by the caller so the (expensive) resize happens once
    and per-frame re-mapping only re-maps luminance -> characters.

    max_h (optional): if the computed height would exceed this (e.g. the
        terminal's row count), the width is shrunk to keep the aspect ratio so
        the whole art fits on one screen and never scrolls.
    """
    img = image.convert("L")  # grayscale
    w, h = img.size

    if compressed:
        new_w = width
        new_h = max(1, int(h * new_w / w))
    else:
        # Terminal characters are taller than wide, so we shrink the height by
        # char_aspect to keep the picture from looking stretched.
        new_w = width
        new_h = max(1, int(h * new_w / w * char_aspect))

    if max_h and new_h > max_h:
        new_w = max(1, int(new_w * max_h / new_h))
        new_h = max_h

    img = img.resize((new_w, new_h))
    return list(img.getdata()), new_w, new_h


def render_ascii(lum_data, new_w: int, new_h: int, charset: str, invert: bool,
                 dither: float = 0.0, rng=None) -> str:
    """Map cached luminance values onto the ramp, producing ASCII art text.

    dither (0..1): amount of per-pixel random jitter, expressed as a fraction
        of the FULL 0..255 luminance range (not a single ramp step). When > 0
        the characters "shimmer" and visibly change every frame; used for
        temporal (animated) dithering where `rng` is re-seeded every frame.
    """
    ramp = charset if not invert else charset[::-1]
    n = len(ramp) - 1

    lines = []
    for lum in lum_data:
        # lum: 0 (black) .. 255 (white). Brighter -> later (denser) char.
        if dither and rng is not None:
            lum = lum + rng.uniform(-dither, dither) * 255.0
        idx = int(round(lum / 255.0 * n))
        idx = 0 if idx < 0 else (n if idx > n else idx)
        lines.append(ramp[idx])

    # Re-join into rows of width new_w.
    rows = []
    for r in range(new_h):
        rows.append("".join(lines[r * new_w:(r + 1) * new_w]))
    return "\n".join(rows)


def image_to_ascii(image, width: int, charset: str, invert: bool,
                   char_aspect: float, compressed: bool) -> str:
    """Convert a PIL Image into an ASCII-art string (convenience wrapper)."""
    lum_data, new_w, new_h = image_to_luminance(image, width, char_aspect, compressed)
    return render_ascii(lum_data, new_w, new_h, charset, invert)


# ==================================================
#  Output helpers
# ==================================================
def _next_available_path(filepath: str) -> str:
    path = Path(filepath)
    if not path.exists():
        return filepath
    parent = path.parent
    stem = path.stem
    suffix = path.suffix
    i = 1
    while True:
        numbered = parent / f"{stem}_{i:03d}{suffix}"
        if not numbered.exists():
            return str(numbered)
        i += 1


def resolve_output_path(kind: str, value, cwd: str) -> Path:
    """Decide where to write the ASCII art.

    - 'file' source      -> companion file "<stem>.ascii.txt" next to the image.
    - anything else      -> "<cwd>/ascii_art_YYYYMMDD_HHMMSS.txt".
    """
    if kind == "file":
        src = Path(value)
        out = src.with_suffix("")  # drop original extension
        out = out.parent / (out.name + OUTPUT_SUFFIX)
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(cwd) / f"ascii_art_{stamp}{OUTPUT_SUFFIX}"

    if not OVERWRITE:
        out = Path(_next_available_path(str(out)))
    return out


def apply_levels(lum_data, black: int, white: int, gamma: float):
    """Apply an image-editor "Levels" adjustment to cached luminance.

    black/white are input level points (0..255). gamma is the midtone curve
    (1.0 = linear). Returns a new list of luminance values in 0..255.
    """
    if white <= black:
        white = black + 1  # guard against divide-by-zero / inverted range
    span = float(white - black)
    out = []
    for lum in lum_data:
        v = (lum - black) / span
        if v < 0.0:
            v = 0.0
        elif v > 1.0:
            v = 1.0
        # gamma correction: exponent < 1 brightens, > 1 darkens midtones.
        out.append((v ** gamma) * 255.0)
    return out


# ==================================================
#  Temporal (animated) dithering
# ==================================================
def enable_vt_mode():
    """Enable ANSI/VT escape processing on the Windows console.

    Python's stdout on Windows usually has virtual-terminal processing
    DISABLED, so escape sequences such as cursor moves and screen clears are
    silently ignored. The animation then just appends frame after frame and
    the console scrolls forever. Enabling VT mode makes the clears actually
    work. No-op on other platforms and if it fails for any reason.
    """
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        STD_OUTPUT_HANDLE = ctypes.c_uint32(-11 & 0xFFFFFFFF)

        kernel32.GetStdHandle.restype = ctypes.c_void_p
        kernel32.GetStdHandle.argtypes = [ctypes.c_uint32]
        kernel32.GetConsoleMode.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.c_uint32(0)
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        if not (mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING):
            kernel32.SetConsoleMode(
                handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    except Exception:
        pass


def draw_frame(text):
    """Redraw the frame in place without a full-screen black flash.

    We home the cursor and overwrite line by line, erasing each line's tail
    with ``\\033[K`` and clearing any leftovers below with ``\\033[J``. This
    avoids the black frames you get from erasing the whole screen (``\\033[2J``)
    and, because the art is sized to one screen, never scrolls — so the
    terminal history does not grow either.
    """
    buf = ["\033[H"]
    for line in text.split("\n"):
        buf.append(line)
        buf.append("\033[K\n")
    buf.append("\033[J")
    sys.stdout.write("".join(buf))
    sys.stdout.flush()


def fit_to_terminal(width, char_aspect, compressed, fit=True):
    """Return (eff_width, max_height).

    When `fit` is True (default) the size is capped to the current terminal so
    the animation stays on one screen and never scrolls. When `fit` is False,
    `width` is honored exactly (eff_w == width, no height cap) so the WIDTH
    setting always takes effect; the art may then be wider/taller than the
    terminal and wrap or scroll.
    """
    cols, rows = shutil.get_terminal_size((80, 24))
    if fit:
        eff_w = max(20, min(int(width), cols - 1))
        # Reserve rows for the header line(s) and margins so the whole frame
        # fits on one screen and never scrolls.
        max_h = max(5, rows - 6)
    else:
        eff_w = max(1, int(width))
        max_h = None  # no vertical cap
    return eff_w, max_h


# ==================================================
#  Dynamic tone-ordered Unicode charset
# ==================================================
# Build a ramp of `count` single-cell characters starting at code point
# `start_cp`, ordered DARK -> LIGHT by *measured* ink coverage (tone). Each
# candidate glyph is rendered with a monospace font and its ink fraction is
# measured, so the ramp is perceptually even regardless of which Unicode range
# the "Start" slider lands on. This is what makes the live "Count"/"Start"
# sliders produce sensible art in real time.

# Cache measured ink coverage per character (expensive to recompute per frame).
_TONE_CACHE = {}


def _load_mono_font(size=FONT_SIZE):
    """Locate a usable monospace TTF and return an ImageFont, or None.

    Falls back to PIL's bitmap default font if no TTF is found; the default
    font only covers ASCII, so Unicode ranges will simply yield no glyphs.
    """
    candidates = [
        "consola.ttf", "consolab.ttf", "cour.ttf", "courbd.ttf",
        "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf",
        "LiberationMono-Regular.ttf", "Menlo.ttc", "Monaco.ttf",
        "NotoSansMono-Regular.ttf", "FreeMono.ttf",
    ]
    dirs = []
    windir = os.environ.get("WINDIR")
    if windir:
        dirs.append(os.path.join(windir, "Fonts"))
    dirs += [
        "/usr/share/fonts", "/usr/share/fonts/truetype",
        "/Library/Fonts", os.path.expanduser("~/.fonts"),
        os.path.expanduser("~/Library/Fonts"),
    ]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in candidates:
            p = os.path.join(d, name)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
    try:
        return ImageFont.load_default()
    except Exception:
        return None


_MISSING_BYTES = {}


def _glyph_is_missing(font, ch: str, size: int) -> bool:
    """True if `font` has no real glyph for `ch` (renders a .notdef tofu box).

    We render `ch` and compare its bitmap to a reference unassigned code point
    (U+10FFFD), which every font renders as the same missing-glyph placeholder.
    If they match, `ch` has no glyph of its own. The reference is cached per
    (font, size) so the comparison is cheap.
    """
    fid = (id(font), size)
    if fid not in _MISSING_BYTES:
        ref = Image.new("L", (size, size), 255)
        try:
            ImageDraw.Draw(ref).text((0, 0), "\U0010FFFD", fill=0, font=font)
        except Exception:
            pass
        _MISSING_BYTES[fid] = ref.tobytes()
    img = Image.new("L", (size, size), 255)
    try:
        ImageDraw.Draw(img).text((0, 0), ch, fill=0, font=font)
    except Exception:
        return True
    return img.tobytes() == _MISSING_BYTES[fid]


def _char_renderable(ch: str) -> bool:
    """True if `ch` is a single, monospaced, printable code point.

    We skip controls, surrogates, combining marks, format chars (ZWJ,
    variation selectors), private-use and full/half-width glyphs so the
    resulting ramp stays on a 1-cell-per-character grid.
    """
    o = ord(ch)
    if o < 0x20 or o == 0x7F:
        return False
    if 0xD800 <= o <= 0xDFFF:
        return False
    cat = unicodedata.category(ch)
    if cat[0] == 'M' or cat == 'Cf' or cat == 'Co':
        return False
    if unicodedata.east_asian_width(ch) in ('W', 'F'):
        return False
    if o in (0x200B, 0x200C, 0x200D, 0xFE0F, 0xFEFF):
        return False
    return True


def _char_ink(font, ch: str, size: int):
    """Return ink coverage (0 = blank .. 1 = fully filled) for glyph `ch`.

    Returns None when the font has no glyph for `ch` (so it can be skipped).
    Results are cached in _TONE_CACHE.
    """
    if ch in _TONE_CACHE:
        return _TONE_CACHE[ch]
    if ch == ' ':
        _TONE_CACHE[ch] = 0.0
        return 0.0
    if font is None:
        _TONE_CACHE[ch] = None
        return None
    # Skip code points the font cannot actually draw (renders a .notdef tofu
    # box). Without this, placeholder boxes get measured as real characters
    # and end up in the ramp -- which then print as boxes in the terminal.
    if _glyph_is_missing(font, ch, size):
        _TONE_CACHE[ch] = None
        return None
    try:
        mask = font.getmask(ch)
    except Exception:
        _TONE_CACHE[ch] = None
        return None
    bbox = mask.getbbox()
    if bbox is None:
        _TONE_CACHE[ch] = None
        return None
    img = Image.new("L", (size, size), 255)
    d = ImageDraw.Draw(img)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (size - w) // 2 - bbox[0]
    y = (size - h) // 2 - bbox[1]
    d.text((x, y), ch, fill=0, font=font)
    dark = 0
    for v in img.getdata():
        if v < 128:
            dark += 1
    ink = dark / max(1, size * size)
    _TONE_CACHE[ch] = ink
    return ink


def build_tone_ramp(start_cp, count, font, size=FONT_SIZE, max_scan=60000):
    """Build a dark->light ramp of `count` tone-ordered glyphs.

    Characters are sampled forward from code point `start_cp`, keeping only
    renderable single-cell glyphs, then sorted by measured ink coverage so the
    DARKEST glyph is at index 0 (matching render_ascii's expectation that
    lum=0 -> ramp[0]).

    If `font` is None (no TTF available), falls back to a static built-in ramp
    truncated/padded to `count`.
    """
    if font is None:
        base = " .'`^\",:;Il!i><~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"
        if count > len(base):
            base = base * (count // len(base) + 1)
        return base[:count]

    chars = []
    cp = max(0x20, int(start_cp))
    scanned = 0
    while len(chars) < count and cp <= 0x10FFFF and scanned < max_scan:
        ch = chr(cp)
        cp += 1
        scanned += 1
        if not _char_renderable(ch):
            continue
        ink = _char_ink(font, ch, size)
        if ink is None:
            continue
        chars.append((ch, ink))

    chars.sort(key=lambda x: -x[1])  # dark (high ink) -> light (low ink)
    return "".join(c for c, _ in chars)


def _collect_valid_cps(font, size=FONT_SIZE, lo=0x20, hi=0xFFFF):
    """Return a sorted list of code points the font can actually render.

    A code point qualifies only if it is a single-cell, printable glyph AND
    the font has a real glyph for it (not a .notdef tofu box). This list lets
    the GUI's "Start" slider step across *real* glyphs only, so dragging it
    produces continuous change instead of long dead zones.
    """
    cps = []
    if font is None:
        return cps
    for cp in range(lo, hi + 1):
        ch = chr(cp)
        if not _char_renderable(ch):
            continue
        if _glyph_is_missing(font, ch, size):
            continue
        cps.append(cp)
    return cps


def _build_ramp_from_window(window, font, size=FONT_SIZE):
    """Build a dark->light tone ramp from a window of valid code points.

    `window` is a slice of code points (e.g. from _collect_valid_cps). Each is
    measured for ink coverage (cached) and the window is sorted dark -> light.
    """
    chars = []
    for cp in window:
        ch = chr(cp)
        ink = _char_ink(font, ch, size)
        if ink is None:
            continue
        chars.append((ch, ink))
    chars.sort(key=lambda x: -x[1])  # dark (high ink) -> light (low ink)
    return "".join(c for c, _ in chars)


def _build_best_ramp(valid_cps, count, font, size=FONT_SIZE):
    """Build an optimally even grayscale ramp of `count` glyphs.

    All valid glyphs are measured for ink coverage and sorted dark -> light.
    Then `count` glyphs are taken at evenly spaced positions across that sorted
    list, so the result spans the full tonal range as evenly as possible --
    e.g. count=3 picks the darkest, the mid-tone, and the lightest glyph. This
    ignores the "Start" window and instead chooses the BEST N glyphs globally.
    """
    measured = []
    for cp in valid_cps:
        ink = _char_ink(font, chr(cp), size)
        if ink is None:
            continue
        measured.append((chr(cp), ink))
    measured.sort(key=lambda x: -x[1])  # dark (high ink) -> light (low ink)
    n = len(measured)
    if n == 0:
        return ""
    if count >= n:
        return "".join(c for c, _ in measured)
    out = []
    for i in range(count):
        # Evenly spaced rank across the sorted list (0 = darkest .. n-1 = lightest).
        pos = int(round(i * (n - 1) / (count - 1))) if count > 1 else 0
        out.append(measured[pos][0])
    return "".join(out)


def animate_ascii(img, width, charset, invert, char_aspect, compressed,
                  fps, frames, dither, label):
    """Live-render the image with a fresh random seed each frame.

    The console is cleared and re-drawn every frame so the characters shimmer
    (temporal dithering). Nothing is written to a file.
    """
    eff_w, max_h = fit_to_terminal(width, char_aspect, compressed)
    lum_data, new_w, new_h = image_to_luminance(
        img, eff_w, char_aspect, compressed, max_h=max_h)
    rng = random.Random()

    # Hide the cursor for the duration of the animation (VT/ANSI escape).
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    try:
        f = 0
        while frames == 0 or f < frames:
            rng.seed()  # fresh OS entropy each frame -> different chars
            art = render_ascii(lum_data, new_w, new_h, charset, invert,
                               dither=dither, rng=rng)
            draw_frame(
                f" ASCII ART (temporal dithering) | source: {label} | "
                f"frame {f}\n\n{art}\n"
            )
            time.sleep(1.0 / fps)
            f += 1
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h")  # restore cursor
        sys.stdout.flush()


def run_temporal_gui(img, width, charset, invert, char_aspect, compressed,
                     fps, frames, dither, label):
    """Temporal dithering plus a GUI with three "Levels" sliders.

    A small tkinter window (black point, white point, gamma) lets you tune the
    image in real time; the console ASCII art re-renders every frame using the
    current slider values. Nothing is written to a file.

    The re-render is driven by the tkinter event loop itself (``root.after``),
    NOT a background thread. This is deliberate: tkinter widgets are not
    thread-safe, and reading a ``DoubleVar`` from another thread (or relying on
    the Scale ``command`` callback to push values across threads) is unreliable.
    Reading the live slider values inside the timer, which runs on the GUI
    thread, makes the sliders always reflect instantly.
    """
    if not HAS_TK:
        print("[!] tkinter is not available; running temporal animation "
              "without the levels GUI.")
        animate_ascii(img, width, charset, invert, char_aspect, compressed,
                      fps, frames, dither, label)
        return

    rng = random.Random()
    state = {"stop": False, "frame": 0, "ramp": "", "ramp_key": None,
             "ramp_start": 0, "ramp_end": 0, "best_mode": False,
             "geo_key": None, "lum_data": None, "new_w": 0, "new_h": 0}

    # --- GUI window -----------------------------------------------------
    try:
        root = tk.Tk()
    except Exception as e:
        print(f"[!] Could not open the levels GUI ({e}); running temporal "
              "animation without sliders.")
        animate_ascii(img, width, charset, invert, char_aspect, compressed,
                      fps, frames, dither, label)
        return

    root.title("ASCII Levels")
    root.geometry("360x410")
    root.columnconfigure(1, weight=1)

    # Monospace font used to MEASURE each glyph's ink coverage (tone), so the
    # dynamic "Count"/"Start" sliders can order characters dark -> light by
    # real rendered weight rather than by guesswork.
    mono_font = _load_mono_font(FONT_SIZE)

    # Precompute the code points this font can actually render. The "Start"
    # slider then indexes into THIS list, so every slider position maps to a
    # real glyph block and dragging it produces continuous change (no dead
    # zones across unassigned / full-width / tofu ranges).
    VALID_CPS = _collect_valid_cps(mono_font, FONT_SIZE)
    n_valid = len(VALID_CPS)

    # Keep references to the live slider variables so the render timer can read
    # the CURRENT slider position every tick (no cross-thread access, no
    # reliance on command callbacks).
    vars_ = {}

    def make_slider(row, name, lo, hi, default, res=None):
        tk.Label(root, text=name).grid(row=row, column=0, sticky="w", padx=6)
        v = tk.DoubleVar(value=default)
        vars_[name] = v
        scale = tk.Scale(
            root, from_=lo, to=hi, orient="horizontal", variable=v,
            resolution=res if res is not None else max(1, (hi - lo) / 100.0),
            length=240,
        )
        scale.grid(row=row, column=1, sticky="ew", padx=6)

    make_slider(0, "Black", 0, 255, 0)
    make_slider(1, "White", 0, 255, 255)
    make_slider(2, "Gamma", 10, 300, 100)
    make_slider(3, "Count", 4, 500, 70, res=1)
    # "Start" is an index into VALID_CPS (0 .. n_valid-1), not a raw code point,
    # so the whole slider range contains only real, renderable glyphs.
    make_slider(4, "Start", 0, max(0, n_valid - 1), 0, res=1)

    # "Best" checkbox: when on, ignore the Start window and instead pick the
    # N (Count) glyphs that form the most even grayscale ramp (darkest ->
    # mid-tone -> lightest) from ALL valid glyphs.
    vars_["Best"] = tk.BooleanVar(value=False)
    tk.Checkbutton(root, text="Best N grayscale", variable=vars_["Best"]).grid(
        row=5, column=0, columnspan=2, sticky="w", padx=6)

    # "Fit terminal" (default ON) caps WIDTH/height to the terminal so the
    # animation stays on one screen. Uncheck it to honor WIDTH exactly (art
    # may then be wider/taller than the terminal and wrap or scroll).
    vars_["Fit"] = tk.BooleanVar(value=True)
    tk.Checkbutton(root, text="Fit terminal (else use WIDTH)", variable=vars_["Fit"]).grid(
        row=7, column=0, columnspan=2, sticky="w", padx=6)

    def on_close():
        state["stop"] = True
        try:
            root.destroy()
        except Exception:
            pass

    tk.Button(root, text="Stop", command=on_close).grid(
        row=8, column=0, columnspan=2, pady=8)
    root.protocol("WM_DELETE_WINDOW", on_close)

    def tick():
        if state["stop"]:
            return
        rng.seed()  # fresh random seed each frame -> shimmer
        black = int(vars_["Black"].get())
        white = int(vars_["White"].get())
        gamma = vars_["Gamma"].get() / 100.0  # slider is 10..300 -> 0.1..3.0
        count = int(vars_["Count"].get())
        start_idx = int(vars_["Start"].get())
        best = bool(vars_["Best"].get())
        fit = bool(vars_["Fit"].get())

        # Recompute the (resized) luminance grid only when the geometry changes
        # (WIDTH or the Fit toggle). This is what lets WIDTH actually take
        # effect when "Fit terminal" is unchecked.
        geo_key = (width, fit)
        if geo_key != state["geo_key"]:
            eff_w, max_h = fit_to_terminal(width, char_aspect, compressed, fit=fit)
            lum_data, new_w, new_h = image_to_luminance(
                img, eff_w, char_aspect, compressed, max_h=max_h)
            state["lum_data"] = lum_data
            state["new_w"] = new_w
            state["new_h"] = new_h
            state["geo_key"] = geo_key
        lum_data = state["lum_data"]
        new_w = state["new_w"]
        new_h = state["new_h"]

        # Rebuild the tone-ordered ramp only when the sliders move. The "Best"
        # checkbox changes the rebuild strategy, so it is part of the key.
        key = (start_idx, count, best)
        if key != state["ramp_key"]:
            if best and VALID_CPS:
                # Pick the N glyphs that form the most even grayscale ramp
                # (darkest -> mid-tone -> lightest) from ALL valid glyphs.
                state["ramp"] = _build_best_ramp(VALID_CPS, count, mono_font, FONT_SIZE)
                state["best_mode"] = True
            elif VALID_CPS:
                window = VALID_CPS[start_idx: start_idx + count]
                state["ramp"] = _build_ramp_from_window(window, mono_font, FONT_SIZE)
                state["ramp_start"] = VALID_CPS[start_idx] if window else 0
                state["ramp_end"] = VALID_CPS[min(start_idx + count, n_valid) - 1] if window else 0
                state["best_mode"] = False
            else:
                state["ramp"] = build_tone_ramp(32, count, mono_font, FONT_SIZE)
                state["ramp_start"] = 32
                state["ramp_end"] = 32
                state["best_mode"] = False
            state["ramp_key"] = key
        ramp = state["ramp"]

        if ramp:
            adj = apply_levels(lum_data, black, white, gamma)
            art = render_ascii(adj, new_w, new_h, ramp, invert,
                               dither=dither, rng=rng)
        else:
            art = "(no renderable glyphs in this window)"

        mode = "best" if state.get("best_mode") else "win"
        draw_frame(
            f" ASCII ART (temporal + levels) | source: {label} | "
            f"frame {state['frame']} | B{black} W{white} G{gamma:.2f} | "
            f"ramp {len(ramp)} chars [{mode}] "
            + (f"U+{state['ramp_start']:04X}..U+{state['ramp_end']:04X} "
               if not state.get("best_mode") else "")
            + f"'{ramp[0] if ramp else '?'}'..'{ramp[-1] if ramp else '?'}'"
            f"\n\n{art}\n"
        )
        state["frame"] += 1
        if frames != 0 and state["frame"] >= frames:
            on_close()
            return
        root.after(max(1, int(1000.0 / fps)), tick)

    root.after(0, tick)
    sys.stdout.write("\033[?25l")  # hide cursor during animation
    sys.stdout.flush()
    try:
        root.mainloop()
    finally:
        sys.stdout.write("\033[?25h")  # restore cursor
        sys.stdout.flush()



# ==================================================
#  Main
# ==================================================
def main():
    parser = argparse.ArgumentParser(
        description="Render images to ASCII art (console + file).",
    )
    parser.add_argument(
        "paths", nargs="*",
        help="Image file(s) or director(y|ies) to convert.",
    )
    parser.add_argument(
        "-w", "--width", type=int, default=WIDTH,
        help=f"Output width in chars (default {WIDTH}).",
    )
    parser.add_argument(
        "--charset", default=None,
        help="Brightness ramp, dark -> light (default: ' .:-=+*#%%@').",
    )
    parser.add_argument(
        "--halftone", action="store_true",
        help="Restrict the ramp to Unicode shade blocks only "
             "(dark -> light: █ ▓ ▒ ░).",
    )
    parser.add_argument("--invert", action="store_true",
                        help="Invert brightness mapping.")
    parser.add_argument("--no-aspect", dest="compressed", action="store_true",
                        help="Do not correct for character cell aspect ratio.")
    parser.add_argument(
        "--ramp", choices=sorted(RAMPS.keys()), default=None,
        help="Named grayscale ramp: " + ", ".join(sorted(RAMPS.keys())) +
             f" (default {DEFAULT_RAMP}).",
    )
    parser.add_argument(
        "--temporal", action="store_true",
        help="Temporal dithering: animate live in the console with a fresh "
             "random seed each frame (different characters every frame). "
             "Nothing is written to a file.",
    )
    parser.add_argument(
        "--fps", type=float, default=TEMPORAL_FPS,
        help=f"Animation speed in frames/sec (default {TEMPORAL_FPS}).",
    )
    parser.add_argument(
        "--frames", type=int, default=TEMPORAL_FRAMES,
        help="Stop after N frames (0 = until Ctrl+C; default "
             f"{TEMPORAL_FRAMES}).",
    )
    parser.add_argument(
        "--dither", type=float, default=None,
        help="Per-frame random jitter as a fraction of the FULL 0..255 "
             f"luminance range (default {DITHER_STRENGTH}; only used with "
             "--temporal). 0 = static, higher = stronger shimmer.",
    )
    args = parser.parse_args()

    # Charset resolution priority:
    #   explicit --charset  >  --ramp  >  --halftone  >  default.
    if args.charset is not None:
        charset = args.charset
    elif args.ramp is not None:
        charset = RAMPS[args.ramp]
    elif args.halftone:
        charset = HALFTONE_CHARSET
    else:
        charset = CHARSET

    invert = INVERT or args.invert
    compressed = COMPRESSED or args.compressed
    cwd = os.getcwd()

    # Temporal dithering: random seed each frame -> shimmering animation.
    temporal = TEMPORAL_DITHERING or args.temporal
    if temporal:
        # Windows consoles ignore ANSI escapes unless VT mode is enabled,
        # which would make the per-frame clears fail and the output scroll.
        enable_vt_mode()
    dither = args.dither if args.dither is not None else DITHER_STRENGTH
    if temporal and args.fps <= 0:
        print("[-] --fps must be positive for temporal dithering.")
        return

    sources = collect_sources(args)
    if not sources:
        print("[-] No image input found (no path arg, no clipboard image, "
              "no images in cwd).")
        return

    if not HAS_PIL:
        print("[!] PIL (Pillow) is required to process images. "
              "Install with: pip install pillow")
        return

    for kind, value in sources:
        try:
            if kind == "file":
                img = Image.open(value)
                label = str(value)
            else:
                img = value
                label = "clipboard bitmap"

            if temporal:
                # Live animation with optional levels GUI; never writes a file.
                run_temporal_gui(
                    img, args.width, charset, invert, CHAR_ASPECT, compressed,
                    fps=args.fps, frames=args.frames, dither=dither, label=label,
                )
                continue
        except Exception as e:
            print(f"[-] Failed to process {label}: {e}")
            continue

        try:
            art = image_to_ascii(
                img, args.width, charset, invert, CHAR_ASPECT, compressed
            )
        except Exception as e:
            print(f"[-] Failed to process {label}: {e}")
            continue

        print("\n" + "=" * 60)
        print(f" ASCII ART  |  source: {label}")
        print("=" * 60)
        print(art)

        out_path = resolve_output_path(kind, value, cwd)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(art + "\n")
            print(f"[++] Saved: {out_path}")
        except Exception as e:
            print(f"[-] Save error: {e}")


if __name__ == "__main__":
    main()
