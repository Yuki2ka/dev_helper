from __future__ import annotations

# === SETTINGS START ===
CONVERT_TO_LF = True      # Convert all line endings (CRLF, CR) to Unix LF (\n)
TRIM_TRAIL_SPACES = True  # remove space and tabs in the end of lines
CONVERT_TO_TABS = True    # Automatically convert spaces to 1 tab indentation
CONTENT_TO_SIGNATURE = False
# === SETTINGS END ===

import sys
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

from dev_helper.common.clipboard import copy_to_clipboard, paste_clipboard
from dev_helper.common.text_utils import (
    normalize_indentation_to_tabs,
    normalize_line_endings,
    PRESERVE_SPACE_EXTS,
    trim_trailing_whitespace,
)
from dev_helper.common.signature_extractor import extract_signatures


def main():
    clipboard_content = paste_clipboard()
    if not clipboard_content:
        print("Clipboard is empty.")
        return

    text = clipboard_content

    if CONVERT_TO_LF:
        text = normalize_line_endings(text)

    if TRIM_TRAIL_SPACES:
        text = trim_trailing_whitespace(text)

    if CONVERT_TO_TABS:
        ext = ""
        text = normalize_indentation_to_tabs(text, ext, PRESERVE_SPACE_EXTS)

    if CONTENT_TO_SIGNATURE:
        sig_text, sig_count = extract_signatures(text, "")
        if sig_count:
            text = sig_text
            print(f"[+] Extracted {sig_count} signatures.")
        else:
            print("[!] No signatures found, using normalized content.")

    copy_to_clipboard(text)
    print("[+] Result copied to clipboard.")


if __name__ == "__main__":
    main()
