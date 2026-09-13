from __future__ import annotations

import sys
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

from dev_helper.common.clipboard import copy_to_clipboard, paste_clipboard
from dev_helper.common.signature_extractor_from_crate import (
    get_crate_signatures,
    get_crate_function_signature,
    fetch_crate_info,
)


def _parse_crate_input(line: str) -> tuple[str, str, str] | None:
    """Parse a single crate input line.
    
    Format:
      "crate_name" - all signatures from latest
      "crate_name/version" - all signatures from specific version
      "crate_name/function" - specific function from latest
      "crate_name/version/function" - specific function from specific version
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    
    # Try slash format first: crate/function or crate/version/function
    if "/" in line:
        parts = [p.strip() for p in line.split("/")]
        crate_name = parts[0]
        if len(parts) == 2:
            return (crate_name, "*", parts[1])  # function lookup
        elif len(parts) >= 3:
            return (crate_name, parts[1], parts[2])  # version + function
        return None
    
    # Try comma format: crate,version,function
    parts = [p.strip() for p in line.split(",")]
    crate_name = parts[0] if parts else ""
    version = parts[1] if len(parts) > 1 else "*"
    function = ""
    
    # Check if second part looks like a function (contains only alphanumeric) or version
    if len(parts) > 2:
        function = parts[2]
    elif len(parts) == 2 and "/" not in parts[1]:
        function = parts[1]  # Could be function name
        version = "*"
    
    if crate_name:
        return (crate_name, version, function)
    return None


def _parse_crates_from_clipboard() -> list[tuple[str, str, str]]:
    """Parse crate names from clipboard.
    
    Supports formats:
      "crate_name" - all signatures from latest
      "crate_name/version" - all signatures from specific version
      "crate_name/function" - specific function from latest  
      "crate_name/version/function" - specific function from specific version
    """
    text = paste_clipboard()
    
    if not text:
        return []
    
    crates = []
    for line in text.splitlines():
        result = _parse_crate_input(line)
        if result:
            crates.append(result)
    
    return crates


def extract_crate_signatures_to_clipboard(crates: list[tuple[str, str, str]] | None = None) -> None:
    """Fetch signatures from crates and put them in clipboard.
    
    Args:
        crates: List of (crate_name, version, function) tuples. If None, reads from clipboard/args.
    """
    if crates is None:
        crates = []
    
    # CLI args have priority over clipboard
    if not crates:
        for arg in sys.argv[1:]:
            result = _parse_crate_input(arg)
            if result:
                crates.append(result)
    
    if not crates:
        crates = _parse_crates_from_clipboard()
    
    if not crates:
        print("No crates specified. Provide as CLI args or put in clipboard.")
        print("Usage: signature_from_crate.py crate_name [version|function] [function]")
        print("  blake3/hash - specific function from latest version")
        print("  blake3/1.5.0/hash - specific function from version")
        print("  blake3 - all signatures from latest version")
        print("  blake3/1.5.0 - all signatures from specific version")
        return
    
    output_lines = []
    for crate_name, version, function in crates:
        try:
            resolved_version = version
            if version == "*":
                crate_info = fetch_crate_info(crate_name)
                resolved_version = crate_info.get("num", "unknown")
            
            if function:
                sig = get_crate_function_signature(crate_name, version, function)
                if sig:
                    header = f"=== {crate_name} {resolved_version}"
                    output_lines.append(header)
                    output_lines.append(sig)
                else:
                    output_lines.append(f"=== {crate_name} {resolved_version}")
                    output_lines.append("Error: Function not found")
            else:
                sigs, count = get_crate_signatures(crate_name, version)
                header = f"=== {crate_name} {resolved_version}"
                output_lines.append(header)
                output_lines.append(sigs)
        except Exception as e:
            output_lines.append(f"=== {crate_name} {version}")
            output_lines.append(f"Error: {e}")
    
    final_output = "\n\n".join(output_lines)
    copy_to_clipboard(final_output)
    
    print(f"Success! Processed {len(crates)} crate(s).")
    print("The signatures are now in your clipboard.")


if __name__ == "__main__":
    extract_crate_signatures_to_clipboard()