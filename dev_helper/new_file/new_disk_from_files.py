#!/usr/bin/env python3
# === SETTINGS START ===
PATH = None                 # Hardcoded fallback input file/folder (used only if it exists)
FORMAT = None               # iso / img / vhd / vhdx; None = ask in an interactive menu
ASK_BIG_MB = 1024           # Ask "create or not" when total input size exceeds this many MiB
MIN_IMAGE_MB = 64           # Minimum FAT32 image size (MiB) for img/vhd/vhdx
FS_HEADROOM_MB = 32         # Extra free space reserved inside FAT32 images (MiB)
VOLUME_LABEL = "NEW_DISK"   # Volume label (auto-sanitized per filesystem)
OVERWRITE = False           # Allow overwriting existing output file
RECURSIVE = True            # Include files from subfolders when a folder is the input
# === SETTINGS END ===
"""
Create a disk image (iso / img / vhd / vhdx) from files and folders.

Inputs use the standard path_args resolution order:
CLI args -> piped stdin paths -> clipboard file references -> PATH constant -> cwd.
So you can copy files in a file manager and just run the script, or pass
files/folders as arguments.

What is creatable depends on your system (all offline, detected at runtime):
  iso  - the pycdlib Python package, or xorriso/genisoimage/mkisofs in PATH
  img  - the pyfatfs Python package (pure-Python FAT32, no root/admin needed)
  vhd  - same as img + a fixed-VHD footer appended by this script (stdlib)
  vhdx - same as img + qemu-img in PATH or next to this script
         (https://qemu.weilnetz.de/w64/ for Windows, `apt install qemu-utils`)

Missing Python libraries can be installed directly from the menu:
the script offers to run `pip install pycdlib pyfatfs` for you.

Free space on the destination drive is always checked first. If the input
files are bigger than ASK_BIG_MB (1 GiB by default) the script asks for
confirmation before creating anything.

Output: new_disk_<timestamp>.<format> next to this script
(or to the exact path given with -o).
"""

import argparse
import importlib
import importlib.util
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))
from path_args import choose_output_file, resolve_paths

FORMATS = ("iso", "img", "vhd", "vhdx")

SECTOR_SIZE = 512
MiB = 1024 * 1024


def parse_args(argv=None):
    g = globals()
    parser = argparse.ArgumentParser(
        description="Create a disk image (iso / img / vhd / vhdx) from files and folders"
    )
    parser.add_argument("paths", nargs="*", default=None,
                        help="Files and/or folders to put into the image")
    parser.add_argument("-o", "--output", default=None,
                        help="Output file or folder (default: next to the first input)")
    parser.add_argument("-f", "--format", default=g.get("FORMAT"),
                        choices=FORMATS, help="Image format (default: ask in a menu)")
    parser.add_argument("-l", "--label", default=g.get("VOLUME_LABEL", "NEW_DISK"),
                        help="Volume label")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Do not ask for confirmation on big (> ASK_BIG_MB) inputs")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
#  Backend detection ("is it possible on this system?")
# ---------------------------------------------------------------------------

_import_cache = {}


def try_import(module_name):
    """Import a module, caching the result. Returns the module or None."""
    if module_name not in _import_cache:
        try:
            _import_cache[module_name] = importlib.import_module(module_name)
        except ImportError:
            _import_cache[module_name] = None
    return _import_cache[module_name]


def find_tool(*names):
    """Find an executable next to this script or in PATH (webpmux pattern)."""
    for name in names:
        executable = name + ".exe" if sys.platform == "win32" else name
        local_path = Path(__file__).resolve().parent / executable
        if local_path.is_file():
            return local_path
        system_path = shutil.which(executable)
        if system_path:
            return Path(system_path)
    return None


def iso_backend():
    """Backend for ISO creation: 'pycdlib' or a mkisofs-family tool name."""
    if try_import("pycdlib") is not None:
        return "pycdlib"
    tool = find_tool("xorriso", "genisoimage", "mkisofs")
    if tool:
        return tool.stem
    return None


def fat_backend():
    """Backend for FAT32 images: 'pyfatfs' or None."""
    if try_import("pyfatfs") is not None and try_import("fs") is not None:
        return "pyfatfs"
    return None


def backend_for(fmt):
    """Detect what can create the given format on this system."""
    if fmt == "iso":
        return iso_backend()
    if fmt == "img":
        return fat_backend()
    if fmt == "vhd":
        # Raw FAT32 image + fixed-VHD footer appended with the stdlib only.
        return "pyfatfs+vhd-footer" if fat_backend() else None
    if fmt == "vhdx":
        if fat_backend() and find_tool("qemu-img"):
            return "pyfatfs+qemu-img"
        return None
    return None


def hint_for(fmt):
    """Installation hint shown when a format is not creatable."""
    if fmt == "iso":
        return "pip install pycdlib   (or put xorriso in PATH)"
    if fmt == "img":
        return "pip install pyfatfs"
    if fmt == "vhd":
        return "pip install pyfatfs"
    if fmt == "vhdx":
        missing = []
        if not fat_backend():
            missing.append("pip install pyfatfs")
        if not find_tool("qemu-img"):
            missing.append("qemu-img in PATH or next to this script "
                           "(https://qemu.weilnetz.de/w64/ or `apt install qemu-utils`)")
        return "  and  ".join(missing)
    return ""


def pip_install(packages):
    """Offer the easy solution: `pip install` missing Python libraries."""
    if importlib.util.find_spec("pip") is None:
        print("❌ pip is not available on this system.")
        return False
    try:
        answer = input(f"Install now with pip ({' '.join(packages)})? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if answer.strip().lower() not in ("y", "yes", "1"):
        return False
    command = [sys.executable, "-m", "pip", "install", *packages]
    print("Running:", " ".join(command))
    result = subprocess.run(command)
    if result.returncode != 0:
        print("❌ pip install failed. Install the packages manually and retry.")
        return False
    _import_cache.clear()
    importlib.invalidate_caches()
    return True


def choose_format(preselected=None):
    """
    Ask what to create: iso / img / vhd / vhdx.
    Only formats possible on this system are selectable; for the others an
    install hint is shown. Non-interactive runs must pass --format.
    """
    if preselected:
        fmt = preselected.lower()
        if fmt not in FORMATS:
            print(f"❌ Unknown format: {fmt}. Allowed: {', '.join(FORMATS)}")
            return None
        while backend_for(fmt) is None:
            print(f"⚠ Format '{fmt}' is not creatable on this system.")
            print("  To make it work:", hint_for(fmt))
            pip_packages = []
            if fmt == "iso" and try_import("pycdlib") is None:
                pip_packages.append("pycdlib")
            if fmt in ("img", "vhd", "vhdx") and not fat_backend():
                pip_packages.append("pyfatfs")
            if not pip_packages or not pip_install(pip_packages):
                return None
        return fmt

    while True:
        statuses = {fmt: backend_for(fmt) for fmt in FORMATS}
        missing_pip = []
        if not try_import("pycdlib"):
            missing_pip.append("pycdlib")
        if try_import("pyfatfs") is None or try_import("fs") is None:
            missing_pip.append("pyfatfs")

        print("\nWhat to create?")
        ready_formats = []
        for index, fmt in enumerate(FORMATS, start=1):
            backend = statuses[fmt]
            if backend:
                ready_formats.append(fmt)
                print(f"  [{index}] {fmt:<5} (ready: {backend})")
            else:
                print(f"  [{index}] {fmt:<5} (unavailable - {hint_for(fmt)})")
        can_install = missing_pip and importlib.util.find_spec("pip") is not None
        if can_install:
            print(f"  [i] install missing Python libraries: pip install {' '.join(missing_pip)}")

        if not ready_formats and not can_install:
            print("\n❌ Nothing is creatable on this system and no easy pip fix exists.")
            print("   Suggested installs:")
            for fmt in FORMATS:
                print(f"     {fmt:<5} {hint_for(fmt)}")
            return None

        try:
            resp = input("Select: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            print("Non-interactive run: pass the format with -f/--format "
                  f"({', '.join(ready_formats)}).")
            return None

        if resp == "i":
            if can_install and pip_install(missing_pip):
                continue
            continue

        chosen = None
        for index, fmt in enumerate(FORMATS, start=1):
            if resp in (str(index), fmt):
                chosen = fmt
                break
        if chosen is None:
            print("Unknown selection, try again.")
            continue
        if statuses[chosen] is None:
            print(f"⚠ '{chosen}' needs: {hint_for(chosen)}")
            continue
        return chosen


# ---------------------------------------------------------------------------
#  File collection and in-image names
# ---------------------------------------------------------------------------

def is_hidden(path):
    return any(part.startswith(".") for part in path.parts
               if part not in (path.anchor, ".", ".."))


def sanitize_component(name, max_length=60):
    """Make a name valid for FAT-LFN / Joliet / Windows in one pass."""
    bad_chars = '<>:"/\\|?*'
    name = "".join("_" if c in bad_chars or ord(c) < 32 else c for c in name)
    name = name.rstrip(" .")
    if not name or set(name) == {"."}:
        name = "_"
    if len(name) > max_length:
        stem = Path(name).stem
        suffix = Path(name).suffix
        keep = max(1, max_length - len(suffix))
        name = stem[:keep].rstrip(" .") + suffix
    return name


def build_file_map(inputs, recursive):
    """
    Build {disk_path -> local Path} with sanitized, deduplicated names.

    A single input folder is expanded to the image root (like mkisofs does);
    several inputs each keep their own top-level name.
    """
    items = []
    single_dir = len(inputs) == 1 and inputs[0].is_dir()

    for path in inputs:
        if path.is_file():
            items.append((path.name, path))
        elif path.is_dir():
            base = "" if single_dir else path.name
            iterator = path.rglob("*") if recursive else path.iterdir()
            for file in sorted(iterator):
                if not file.is_file() or is_hidden(file.relative_to(path)):
                    continue
                rel = file.relative_to(path).as_posix()
                items.append((f"{base}/{rel}" if base else rel, file))

    used = set()
    file_map = {}
    for rel, local in items:
        parts = [sanitize_component(part) for part in rel.split("/") if part not in ("", ".")]
        if not parts:
            continue
        stem_parts = parts[:-1]
        name = parts[-1]
        candidate = name
        counter = 1
        while ("/".join(stem_parts + [candidate])).lower() in used:
            stem = Path(name).stem
            suffix = Path(name).suffix
            candidate = f"{stem} ({counter}){suffix}"
            counter += 1
        disk_path = "/".join(stem_parts + [candidate])
        used.add(disk_path.lower())
        file_map[disk_path] = local

    return file_map


def iso_name(component):
    """Mangle a name for ISO9660 (the readable name lives in Joliet anyway)."""
    cleaned = []
    for char in component.upper():
        if char.isalnum() or char in ("_", "-", "."):
            cleaned.append(char)
        else:
            cleaned.append("_")
    return "".join(cleaned)[:191] or "FILE"


def sanitize_label(label, limit, allowed_extra="_"):
    cleaned = "".join(
        char.upper() if (char.isalnum() or char in allowed_extra) else "_"
        for char in label
    ).strip()[:limit]
    return cleaned or "NEW_DISK"


def human_size(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} TiB"


# ---------------------------------------------------------------------------
#  Image builders
# ---------------------------------------------------------------------------

def create_iso_pycdlib(file_map, out_path, label):
    """Create an ISO9660+Joliet image in pure Python."""
    pycdlib = try_import("pycdlib")
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=4, joliet=3, vol_ident=sanitize_label(label, 32))

    def unique_child(parent_iso, name, used_map):
        """Deduplicate ISO9660 names within one directory."""
        used = used_map.setdefault(parent_iso, set())
        candidate = name
        counter = 1
        while candidate.lower() in used:
            stem = Path(name).stem
            suffix = Path(name).suffix
            candidate = f"{stem}_{counter}{suffix}"
            counter += 1
        used.add(candidate.lower())
        return candidate

    dir_iso_paths = {"/": ""}   # joliet dir path -> iso dir path (no leading slash)
    used_names = {}             # iso dir path -> used child names
    for disk_path in sorted(file_map):
        parts = disk_path.split("/")
        current_joliet = ""
        current_iso = ""
        for part in parts[:-1]:  # ensure intermediate directories exist
            current_joliet += "/" + part
            if current_joliet not in dir_iso_paths:
                base = iso_name(part).split(".")[0]
                name = unique_child(current_iso, base, used_names)
                iso_path = f"{current_iso}/{name}"
                iso.add_directory(iso_path=iso_path, joliet_path=current_joliet)
                dir_iso_paths[current_joliet] = iso_path
            current_iso = dir_iso_paths[current_joliet]
        file_name = unique_child(current_iso, iso_name(parts[-1]), used_names)
        iso.add_file(str(file_map[disk_path]),
                     iso_path=f"{current_iso}/{file_name}",
                     joliet_path="/" + disk_path)

    iso.write(str(out_path))
    iso.close()


def _escape_graft_point(value):
    """Escape a path used in mkisofs-family ``target=source`` syntax."""
    return str(value).replace("\\", "\\\\").replace("=", "\\=")


def _iso_tool_command(tool, out_path, label):
    if tool.stem == "xorriso":
        return [str(tool), "-as", "mkisofs", "-J", "-input-charset", "utf-8",
                "-V", sanitize_label(label, 32), "-o", str(out_path)]
    return [str(tool), "-J", "-V", sanitize_label(label, 32),
            "-o", str(out_path)]


def create_iso_tool(tool_name, file_map, out_path, label):
    """Create an ISO directly from source files via graft points.

    xorriso, genisoimage, and mkisofs all support path-list graft points. This
    avoids copying every input into a temporary staging tree first. A staged
    retry remains as a compatibility fallback for unusual tool builds.
    """
    tool = find_tool(tool_name)
    if tool is None:
        raise RuntimeError(f"{tool_name} executable not found.")
    base_command = _iso_tool_command(tool, out_path, label)

    # A line-based path list cannot represent newlines in names. Use the
    # compatible staged path immediately in that case.
    can_use_path_list = all(
        "\n" not in disk_path and "\r" not in disk_path
        and "\n" not in str(local) and "\r" not in str(local)
        for disk_path, local in file_map.items()
    )
    direct_error = None
    if can_use_path_list:
        with tempfile.TemporaryDirectory(prefix="iso_paths_") as temp_dir:
            path_list = Path(temp_dir) / "graft-points.txt"
            lines = [
                f"{_escape_graft_point(disk_path)}={_escape_graft_point(local.resolve())}\n"
                for disk_path, local in sorted(file_map.items())
            ]
            path_list.write_text("".join(lines), encoding="utf-8")
            command = [*base_command, "-graft-points", "-path-list", str(path_list)]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode == 0:
                return
            direct_error = result.stderr.strip() or result.stdout.strip()
            out_path.unlink(missing_ok=True)
            print(
                f"⚠ Direct graft-point ISO creation failed; retrying with staging: {direct_error}",
                file=sys.stderr,
            )

    with tempfile.TemporaryDirectory(prefix="iso_stage_") as stage:
        stage_root = Path(stage)
        for disk_path, local in file_map.items():
            target = stage_root / Path(disk_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, target)

        result = subprocess.run(
            [*base_command, str(stage_root)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            if direct_error:
                message = f"direct mode: {direct_error}\nstaged mode: {message}"
            raise RuntimeError(f"{tool_name} error:\n{message}")


def fat_image_size(total_size):
    """Volume size for img/vhd: input + headroom, whole MiBs, sector aligned."""
    size = total_size + total_size // 8 + FS_HEADROOM_MB * MiB
    size = max(size, MIN_IMAGE_MB * MiB)
    size = math.ceil(size / MiB) * MiB
    return size - (size % SECTOR_SIZE)


def create_fat_image(file_map, img_path, image_size, label):
    """Create a FAT32 image with PyFat. Pure Python, no root/admin needed."""
    from pyfatfs.PyFat import PyFat
    from pyfatfs.PyFatFS import PyFatFS

    img_path.touch()  # mkfs opens the file as rb+, it must exist
    formatter = PyFat()
    formatter.mkfs(str(img_path), fat_type=PyFat.FAT_TYPE_FAT32, size=image_size,
                   label=sanitize_label(label, 11, allowed_extra="_- "))
    formatter.close()

    # Keep the default OEM (ibm437) encoding: it is for 8.3 short names only,
    # while the real names are stored in encoding-independent UTF-16 LFN entries.
    with PyFatFS(str(img_path)) as fat:
        for disk_path in sorted(file_map):
            parent = str(Path(disk_path).parent).replace("\\", "/")
            if parent and parent != ".":
                fat.makedirs(parent, recreate=True)
            with open(file_map[disk_path], "rb") as handle:
                fat.upload(disk_path, handle)


# ---------------------------------------------------------------------------
#  VHD footer (Microsoft VHD spec, stdlib only)
# ---------------------------------------------------------------------------

def vhd_geometry(total_bytes):
    """CHS geometry algorithm from the Microsoft VHD spec appendix."""
    total_sectors = total_bytes // SECTOR_SIZE
    if total_sectors > 65535 * 16 * 255:
        return 65535, 16, 255
    sectors_per_track = 17
    cylinder_times_heads = total_sectors // sectors_per_track
    heads = (cylinder_times_heads + 1023) // 1024
    if heads < 4:
        heads = 4
    if cylinder_times_heads >= heads * 1024 or heads > 16:
        sectors_per_track = 31
        heads = 16
        cylinder_times_heads = total_sectors // sectors_per_track
    if cylinder_times_heads >= heads * 1024:
        sectors_per_track = 63
        heads = 16
        cylinder_times_heads = total_sectors // sectors_per_track
    cylinders = cylinder_times_heads // heads
    return cylinders, heads, sectors_per_track


def vhd_footer(image_size):
    """Build the 512-byte 'conectix' footer of a fixed VHD."""
    cylinders, heads, spt = vhd_geometry(image_size)
    timestamp = int(time.time()) - 946684800  # seconds since 2000-01-01 UTC
    footer = struct.pack(
        ">8sIIQI4sI4sQQHBBI",
        b"conectix",          # cookie
        0x00000002,           # features
        0x00010000,           # file format version
        0xFFFFFFFFFFFFFFFF,   # data offset: unused for fixed disks
        timestamp,
        b"vpc ",              # creator application
        0x00050003,           # creator version
        b"win ",              # creator host OS
        image_size,           # original size
        image_size,           # current size
        cylinders, heads, spt,
        2,                    # disk type: fixed
    )
    footer += b"\x00" * 4                     # checksum placeholder
    footer += uuid.uuid4().bytes              # unique id
    footer += b"\x00"                         # saved state
    footer += b"\x00" * (512 - len(footer))   # reserved
    checksum = (~sum(footer)) & 0xFFFFFFFF
    return footer[:64] + struct.pack(">I", checksum) + footer[68:]


def create_vhd(file_map, out_path, image_size, label):
    """Create a FAT32 image, then copy it and append the fixed-VHD footer."""
    with tempfile.NamedTemporaryFile(prefix="vhd_raw_", delete=False) as tmp:
        raw_path = Path(tmp.name)
    try:
        create_fat_image(file_map, raw_path, image_size, label)
        with open(raw_path, "rb") as src, open(out_path, "wb") as dst:
            shutil.copyfileobj(src, dst, length=8 * MiB)
            dst.write(vhd_footer(raw_path.stat().st_size))
    finally:
        raw_path.unlink(missing_ok=True)


def create_vhdx_tool(file_map, out_path, image_size, label):
    """Create a FAT32 image, then convert it to VHDX with qemu-img."""
    qemu = find_tool("qemu-img")
    if qemu is None:
        raise RuntimeError(
            "qemu-img is required for vhdx.\n\n"
            "Put qemu-img next to this script or install it:\n"
            "  Windows: https://qemu.weilnetz.de/w64/\n"
            "  Linux:   sudo apt install qemu-utils\n"
            "  macOS:   brew install qemu"
        )
    with tempfile.NamedTemporaryFile(prefix="vhdx_raw_", delete=False) as tmp:
        raw_path = Path(tmp.name)
    try:
        create_fat_image(file_map, raw_path, image_size, label)
        command = [str(qemu), "convert", "-f", "raw", "-O", "vhdx",
                   str(raw_path), str(out_path)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"qemu-img error:\n{message}")
    finally:
        raw_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def confirm(question):
    try:
        answer = input(question + " [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in ("y", "yes", "1")


def main(argv=None):
    global FORMAT, VOLUME_LABEL
    args = parse_args(argv)

    FORMAT = args.format
    VOLUME_LABEL = args.label

    resolved = resolve_paths(
        args,
        arg_names=("paths",),
        constant=PATH,
        create="none",
        fallback_to_cwd=True,
    )

    inputs = [p for p in resolved.paths if p.exists()]
    if not inputs:
        print("❌ No input files or folders found.")
        return

    file_map = build_file_map(inputs, RECURSIVE)
    if not file_map:
        print("❌ No files to put into the image.")
        return

    total_size = sum(p.stat().st_size for p in file_map.values())

    fmt = choose_format(FORMAT)
    if fmt is None:
        return
    backend = backend_for(fmt)

    if backend and backend.startswith("pyfatfs"):
        # FAT32 stores file sizes in 4 bytes: a single file >= 4 GiB cannot fit.
        oversized = sorted(
            disk_path for disk_path, local in file_map.items()
            if local.stat().st_size >= 4 * 1024 ** 3
        )
        if oversized:
            print("❌ FAT32 (img/vhd/vhdx) cannot store a file of 4 GiB or more:")
            for disk_path in oversized[:10]:
                print(f"   {disk_path} ({human_size(file_map[disk_path].stat().st_size)})")
            if len(oversized) > 10:
                print(f"   ... and {len(oversized) - 10} more")
            print("   Use the iso format instead, or split the file first.")
            return

    if total_size >= ASK_BIG_MB * MiB and not args.yes:
        print(f"\n⚠ Input files are big: {human_size(total_size)} (> {ASK_BIG_MB} MiB).")
        if not confirm(f"Create the {fmt} image anyway?"):
            print("Cancelled.")
            return

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if args.output:
        out_location = Path(args.output)
    else:
        out_location = _resolved.parent

    out_path = choose_output_file(
        location=out_location,
        default_stem=f"new_disk_{timestamp}",
        default_suffix=f".{fmt}",
        overwrite=OVERWRITE,
    )

    # Space estimate: worst-case bytes that will exist under out_path's drive.
    needs_fat_image = backend is not None and backend.startswith("pyfatfs")
    image_size = fat_image_size(total_size) if needs_fat_image else 0
    if fmt == "iso":
        # External tools read source files directly via graft points; no full
        # staged copy normally shares the output drive.
        estimate = total_size + 32 * MiB
    elif fmt == "vhdx":
        estimate = 2 * image_size
    else:
        estimate = image_size

    free = shutil.disk_usage(str(out_path.parent)).free
    if free < estimate + 32 * MiB:
        print(f"❌ Not enough free space on {out_path.parent.anchor or out_path.parent}: "
              f"need about {human_size(estimate)}, only {human_size(free)} free.")
        return

    print(f"\nCreating {fmt} from {len(file_map)} file(s), "
          f"{human_size(total_size)} total, via {backend} ...")
    try:
        if fmt == "iso" and backend == "pycdlib":
            create_iso_pycdlib(file_map, out_path, VOLUME_LABEL)
        elif fmt == "iso":
            create_iso_tool(backend, file_map, out_path, VOLUME_LABEL)
        elif fmt == "img":
            create_fat_image(file_map, out_path, image_size, VOLUME_LABEL)
        elif fmt == "vhd":
            create_vhd(file_map, out_path, image_size, VOLUME_LABEL)
        elif fmt == "vhdx":
            create_vhdx_tool(file_map, out_path, image_size, VOLUME_LABEL)
    except Exception as error:
        out_path.unlink(missing_ok=True)
        print(f"❌ Error:\n{error}")
        return

    print()
    print("✅ Disk image created successfully.")
    print(f"Input files: {len(file_map)} (source: {resolved.origin})")
    print(f"Input size: {human_size(total_size)}")
    print(f"Format: {fmt} (backend: {backend})")
    if needs_fat_image:
        print(f"Filesystem: FAT32, volume size {human_size(image_size)}, "
              f"label '{sanitize_label(VOLUME_LABEL, 11)}'")
    print(f"Output size: {human_size(out_path.stat().st_size)}")
    print("Output file:")
    print(out_path)


if __name__ == "__main__":
    main()
