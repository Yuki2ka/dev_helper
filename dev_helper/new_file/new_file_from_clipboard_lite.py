import sys
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

import datetime
import re
import os
import argparse

from dev_helper.common.clipboard import paste_clipboard

try:
    from path_args import resolve_paths
except ImportError:
    resolve_paths = None  # type: ignore[assignment]

def create_file(clipboard_text, file_extension, prefix, output_dir=None):
    """Creates a file with the given content, extension, and prefix."""

    timestamp = datetime.datetime.now().strftime("%Y.%m.%d.%H.%M.%S")
    filename = f"{prefix}_{timestamp}.{file_extension}"

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, filename)
    else:
        filepath = filename

    with open(filepath, 'w', encoding='utf-8') as file:
        file.write(clipboard_text)

    print(f"{file_extension.upper()} file '{filename}' created successfully.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="Output file or directory")
    args = parser.parse_args()

    if resolve_paths is not None:
        resolved = resolve_paths(args, arg_names=("path",), fallback_to_cwd=True)
        output_dir = str(resolved.first) if resolved.origin != "cwd" else None
    else:
        output_dir = args.path if (args.path and os.path.isdir(args.path)) else None

    clipboard_text = paste_clipboard()
    clipboard_text = clipboard_text.replace("\r\n", "\n").replace("\r", "\n")

    if not clipboard_text.strip():
        print("Clipboard is empty or contains only whitespace. No file created.")
        return

    if re.search(r'<(html|body)[^>]*>', clipboard_text, re.IGNORECASE):
        create_file(clipboard_text, 'html', 'index', output_dir)
    elif clipboard_text.startswith("import"):
        create_file(clipboard_text, 'py', datetime.datetime.now().strftime("%Y.%m.%d.%H.%M.%S"), output_dir)
    elif clipboard_text.startswith("using") or "namespace" in clipboard_text:
        create_file(clipboard_text, 'cs', datetime.datetime.now().strftime("%Y.%m.%d.%H.%M.%S"), output_dir)
    elif (clipboard_text.startswith(("function", "const ", "let ", "var ")) or re.search(r'\.[a-zA-Z0-9]+\(', clipboard_text) or "console.log" in clipboard_text):
        create_file(clipboard_text, 'js', 'script', output_dir)
    elif re.search(r'^\s*(body|html|div|\.|#|\@|\:)', clipboard_text, re.MULTILINE) or re.search(r'\{[\s\S]*\}', clipboard_text):
        create_file(clipboard_text, 'css', 'styles', output_dir)

    elif clipboard_text.startswith("#include") and re.search(r'<(iostream|stdio.h)>', clipboard_text):
        create_file(clipboard_text, 'cpp', datetime.datetime.now().strftime("%Y.%m.%d.%H.%M.%S"), output_dir)
    elif re.search(r'^(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)', clipboard_text, re.IGNORECASE):
        create_file(clipboard_text, 'sql', datetime.datetime.now().strftime("%Y.%m.%d.%H.%M.%S"), output_dir)
    else:
        create_file(clipboard_text, 'txt', datetime.datetime.now().strftime("%Y.%m.%d.%H.%M.%S"), output_dir)

if __name__ == "__main__":
    main()