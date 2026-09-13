from __future__ import annotations

import math
import re
from functools import reduce
from pathlib import Path

PRESERVE_SPACE_EXTS = {
    "py", "txt", "json", "md", "yaml", "yml", "toml",
    "xml", "sh", "bash", "makefile", "dockerfile", "csv", "tsv",
}


def normalize_indentation_to_tabs(
    text: str,
    ext: str,
    preserve_space_exts: set[str] | None = None,
) -> str:
    if preserve_space_exts is None:
        preserve_space_exts = PRESERVE_SPACE_EXTS

    if ext in preserve_space_exts:
        return text

    lines = text.split("\n")
    space_counts: list[int] = []
    tab_counts: list[int] = []

    for line in lines:
        if not line:
            continue
        if line.startswith("\t"):
            tab_counts.append(len(line) - len(line.lstrip("\t")))
        elif line.startswith(" "):
            match = re.match(r"^( +)\S", line)
            if match:
                space_counts.append(len(match.group(1)))

    if not space_counts:
        return text

    if tab_counts and space_counts:
        avg_s = sum(space_counts) / len(space_counts)
        avg_t = sum(tab_counts) / len(tab_counts)
        space_unit = avg_s / avg_t if avg_t != 0 else 4
    else:
        detected_gcd = reduce(math.gcd, space_counts)
        space_unit = (
            detected_gcd
            if detected_gcd in (2, 4, 8)
            else (space_counts[0] if len(space_counts) == 1 else 4)
        )

    processed_lines: list[str] = []
    for line in lines:
        if not line or line.startswith("\t"):
            processed_lines.append(line)
            continue
        match = re.match(r"^( +)(.*)$", line)
        if match:
            spaces, content = match.groups()
            tab_depth = max(1, round(len(spaces) / space_unit))
            processed_lines.append("\t" * tab_depth + content)
        else:
            processed_lines.append(line)

    return "\n".join(processed_lines)


def normalize_line_endings(text: str) -> str:
    """Convert CRLF and CR line endings to Unix LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def trim_trailing_whitespace(text: str) -> str:
    """Remove trailing spaces and tabs from each line."""
    return "\n".join(line.rstrip() for line in text.split("\n"))


def trim_trailing_whitespace_file(file_path: Path) -> bool:
    """Remove trailing spaces and tabs from each line in a file. Returns True if modified."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            original = f.read()

        text = trim_trailing_whitespace(original)

        if text != original:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text)
            return True
        return False
    except Exception as e:
        print(f"[-] Error trimming {file_path}: {e}")
        return False
