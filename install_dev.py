"""
install_dev.py - One-shot developer environment setup for dev_helper.

Installs the package in editable mode and the dependencies needed to run the
scripts in this repo. Dependencies are split into groups; the "rare-need"
groups (voice, audio, drag-and-drop) are DISABLED by default so a normal dev
setup stays light. Enable them with a CLI flag or by editing ENABLED_GROUPS.

Note on `wave`: it is part of the Python standard library (no pip install
needed). It is used by new_audio / new_audio_sweep / new_voice and is mentioned
here only so the related optional deps are grouped correctly.

Usage:
    python install_dev.py                 # core + windows + images + build (rare off)
    python install_dev.py --with-voice    # also install pyttsx3
    python install_dev.py --with-all      # install everything
    python install_dev.py --core-only     # only core/windows/build (no images)
    python install_dev.py --venv .venv    # create a venv first, then install into it
"""

from __future__ import annotations

# === SETTINGS START ===
# Install the package itself in editable mode (`pip install -e .`).
EDITABLE_INSTALL = True
# If True, `pip install -e .` also pulls the dependencies declared in
# pyproject.toml (pyperclip, pathspec, magika, pyttsx3). Set False to let THIS
# script control every dependency explicitly, so the rare ones can stay off by
# default (this is what makes "comment out pyttsx3 by default" actually work).
EDITABLE_WITH_DEPS = False
# === SETTINGS END ===


# --- Dependency groups -------------------------------------------------------
# Each group is a list of pip specifiers.
#
# COMMON groups are enabled unless you change the flags below.
# RARE groups are disabled by default (commented-out behavior the user asked
# for). Re-enable by flipping the matching key in ENABLED_GROUPS to True, or by
# passing the CLI flag (e.g. --with-voice).

DEPS_CORE = [
    "pyperclip",   # cross-platform clipboard fallback (common/clipboard, path_args)
    "pathspec",    # .gitignore/.hgignore filtering in to_clipboard
    "magika",      # file-type inference in new_file_from_clipboard
]

# Windows-only native clipboard / filesystem backend.
DEPS_WINDOWS = [
    "pywin32",     # native Windows clipboard + reparse-point/symlink ops
]

# Image generation / manipulation.
DEPS_IMAGES = [
    "Pillow",      # combine_img, new_img, ascii_art_from_input
]

# Build tooling required to run build.py / build_more.py.
DEPS_BUILD = [
    "stickytape",  # single-file standalone bundler used by build.py
]

# ---- RARE-NEED script dependencies (DISABLED by default) --------------------
DEPS_VOICE = [
    "pyttsx3",     # new_voice / new_voice1 (text-to-speech). Uses stdlib `wave`.
]
DEPS_AUDIO = [
    "numpy",       # new_audio_sweep waveform math
    "pydub",       # new_audio_sweep audio export
]
DEPS_DND = [
    "tkinterdnd2", # drag-and-drop in GUI scripts (sync_copy, 2way_explorer)
]

# Which groups to install by default. Rare-need groups are False.
ENABLED_GROUPS = {
    "core":    True,
    "windows": True,    # auto-skipped on non-Windows inside main()
    "images":  True,
    "build":   True,
    "voice":   False,   # pyttsx3  -> new_voice                (rare)
    "audio":   False,   # numpy, pydub -> new_audio_sweep     (rare)
    "dnd":     False,   # tkinterdnd2 -> GUI drag & drop       (rare)
}


import argparse
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

ALL_GROUPS = {
    "core": DEPS_CORE,
    "windows": DEPS_WINDOWS,
    "images": DEPS_IMAGES,
    "build": DEPS_BUILD,
    "voice": DEPS_VOICE,
    "audio": DEPS_AUDIO,
    "dnd": DEPS_DND,
}


def _run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd))
    subprocess.check_call(cmd)


def _pip_install(python: str, specs: list[str]) -> None:
    if not specs:
        return
    _run([python, "-m", "pip", "install", *specs])


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Set up the dev_helper dev environment")
    parser.add_argument(
        "--with-voice", action="store_true",
        help="Also install pyttsx3 (new_voice) - rare by default",
    )
    parser.add_argument(
        "--with-audio", action="store_true",
        help="Also install numpy + pydub (new_audio_sweep) - rare by default",
    )
    parser.add_argument(
        "--with-dnd", action="store_true",
        help="Also install tkinterdnd2 (GUI drag & drop) - rare by default",
    )
    parser.add_argument(
        "--with-images", action="store_true",
        help="Force-install Pillow even in --core-only mode",
    )
    parser.add_argument(
        "--with-all", action="store_true",
        help="Install every dependency group (including rare ones)",
    )
    parser.add_argument(
        "--core-only", action="store_true",
        help="Only core + windows + build (skip images and all rare groups)",
    )
    parser.add_argument(
        "--venv", metavar="PATH", default=None,
        help="Create a virtualenv at PATH first and install into it",
    )
    parser.add_argument(
        "--no-editable", action="store_true",
        help="Skip `pip install -e .`; only install the listed dependency groups",
    )
    return parser.parse_args(argv)


def _resolve_python(venv: str | None) -> str:
    if not venv:
        return sys.executable
    venv_path = ROOT / venv
    if not venv_path.exists():
        _run([sys.executable, "-m", "venv", str(venv_path)])
    if platform.system() == "Windows":
        return str(venv_path / "Scripts" / "python.exe")
    return str(venv_path / "bin" / "python")


def main(argv=None) -> int:
    args = parse_args(argv)
    enabled = dict(ENABLED_GROUPS)

    if args.core_only:
        for key in enabled:
            enabled[key] = key in ("core", "windows", "build")
    if args.with_all:
        for key in enabled:
            enabled[key] = True
    if args.with_voice:
        enabled["voice"] = True
    if args.with_audio:
        enabled["audio"] = True
    if args.with_dnd:
        enabled["dnd"] = True
    if args.with_images:
        enabled["images"] = True

    python = _resolve_python(args.venv)

    if EDITABLE_INSTALL and not args.no_editable:
        cmd = [python, "-m", "pip", "install", "-e", "."]
        if not EDITABLE_WITH_DEPS:
            cmd.append("--no-deps")
        _run(cmd)
    else:
        print("-- Skipping editable package install.")

    if platform.system() != "Windows":
        enabled["windows"] = False  # pywin32 is Windows-only

    for name, specs in ALL_GROUPS.items():
        if enabled.get(name):
            print(f"\n== Installing group: {name} ==")
            _pip_install(python, specs)
        else:
            print(f"-- Skipped group: {name} (rare / optional / not applicable)")

    print("\n[+] dev_helper dev environment is ready.")
    print("    Rare groups left out by default; re-run with --with-voice / --with-audio / --with-dnd / --with-all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
