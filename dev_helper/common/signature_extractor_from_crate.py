from __future__ import annotations

import io
import json
import re
import tarfile
import urllib.request
import urllib.error

from .signature_extractor import RustExtractor


def fetch_crate_info(crate_name: str, version: str = "*") -> dict:
    """Fetch crate information from crates.io API.
    
    Args:
        crate_name: Name of the crate
        version: Version string ("*" for latest, or specific version like "1.2.3")
    
    Returns:
        Dict containing crate info including dl_path
    
    Raises:
        ValueError: If crate not found or API error
    """
    base_url = "https://crates.io/api/v1/crates"
    
    if version == "*":
        url = f"{base_url}/{crate_name}"
    else:
        url = f"{base_url}/{crate_name}/{version}"
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Kilo-Crate-Extractor/1.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            if version == "*":
                versions = data.get("versions", [])
                if versions:
                    return versions[0]
                return {}
            # Specific version: unwrap from "version" key
            return data.get("version", data)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(f"Crate '{crate_name}' not found")
        raise ValueError(f"API error: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise ValueError(f"Network error: {e.reason}")


def _parse_all_rs_files(tarball_bytes: bytes) -> list[str]:
    """Extract and parse all .rs files from tarball bytes.
    
    Args:
        tarball_bytes: Raw tarball content
    
    Returns:
        List of Rust file contents
    """
    contents = []
    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name.endswith(".rs"):
                f = tar.extractfile(member)
                if f:
                    try:
                        content = f.read().decode("utf-8", errors="ignore")
                        contents.append(content)
                    except Exception:
                        continue
    return contents


def get_crate_signatures(crate_name: str, version: str = "*") -> tuple[str, int]:
    """Fetch and extract all function signatures from a Rust crate.
    
    Args:
        crate_name: Name of the crate (e.g., "blake3")
        version: Version string ("*" for latest, or specific version like "1.2.3")
    
    Returns:
        Tuple of (signatures_block, count)
    """
    crate_info = fetch_crate_info(crate_name, version)
    download_url = f"https://crates.io{crate_info.get('dl_path', '')}"
    
    if not download_url or download_url == "https://crates.io":
        raise ValueError(f"Could not get download URL for crate '{crate_name}'")
    
    try:
        req = urllib.request.Request(download_url, headers={"User-Agent": "Kilo-Crate-Extractor/1.0"})
        with urllib.request.urlopen(req, timeout=60) as response:
            tarball_bytes = response.read()
    except urllib.error.URLError as e:
        raise ValueError(f"Failed to download crate: {e.reason}")
    
    all_signatures = []
    extractor = RustExtractor()
    
    for content in _parse_all_rs_files(tarball_bytes):
        sig_block, _ = extractor.extract(content)
        if sig_block:
            all_signatures.append(sig_block)
    
    combined = "\n".join(all_signatures)
    # Clean up multiple consecutive newlines
    combined = re.sub(r"\n{3,}", "\n\n", combined).strip()
    
    # Count individual signatures
    count = len([l for l in combined.split("\n") if l.strip()])
    
    return (combined, count)


def _extract_doc_before_match(content: str, match: re.Match, function_name: str) -> str:
    """Extract doc comments (`///`) preceding a function signature.
    
    Args:
        content: Full source file content
        match: Regex match for the function signature
        function_name: Function name being searched
    
    Returns:
        Doc comment string (cleaned, empty string if none)
    """
    lines = content.splitlines()
    match_line_idx = content[:match.start()].count('\n')
    
    doc_lines = []
    for i in range(match_line_idx - 1, -1, -1):
        line = lines[i].strip()
        if line.startswith('///'):
            doc_lines.insert(0, line[3:].strip())  # Remove '///' prefix
        elif line and not line.startswith('//'):
            break  # Hit non-comment, non-empty line - stop
    
    return ' '.join(doc_lines) if doc_lines else ""


def get_crate_function_doc(crate_name: str, version: str, function_name: str) -> str:
    """Fetch and extract documentation for a specific function from a Rust crate.
    
    Args:
        crate_name: Name of the crate (e.g., "blake3")
        version: Version string ("*" for latest, or specific version like "1.2.3")
        function_name: Name of the function to find
    
    Returns:
        Documentation string (or empty string if not found)
    """
    crate_info = fetch_crate_info(crate_name, version)
    download_url = f"https://crates.io{crate_info.get('dl_path', '')}"
    
    if not download_url or download_url == "https://crates.io":
        return ""
    
    try:
        req = urllib.request.Request(download_url, headers={"User-Agent": "Kilo-Crate-Extractor/1.0"})
        with urllib.request.urlopen(req, timeout=60) as response:
            tarball_bytes = response.read()
    except urllib.error.URLError:
        return ""
    
    sig_pattern = re.compile(
        r'\b(?:pub\s+)?fn\s+' + re.escape(function_name) + r'\s*\([^)]*\)(?:\s*->\s*[^{]+)?',
        re.MULTILINE
    )
    
    for content in _parse_all_rs_files(tarball_bytes):
        match = sig_pattern.search(content)
        if match:
            return _extract_doc_before_match(content, match, function_name)
    
    return ""


def get_crate_function_signature(crate_name: str, version: str, function_name: str) -> str:
    """Fetch and extract signature for a specific function from a Rust crate.
    
    Args:
        crate_name: Name of the crate (e.g., "blake3")
        version: Version string ("*" for latest, or specific version like "1.2.3")
        function_name: Name of the function to find
    
    Returns:
        Signature string (or empty string if not found)
    """
    crate_info = fetch_crate_info(crate_name, version)
    download_url = f"https://crates.io{crate_info.get('dl_path', '')}"
    
    if not download_url or download_url == "https://crates.io":
        return ""
    
    try:
        req = urllib.request.Request(download_url, headers={"User-Agent": "Kilo-Crate-Extractor/1.0"})
        with urllib.request.urlopen(req, timeout=60) as response:
            tarball_bytes = response.read()
    except urllib.error.URLError:
        return ""
    
    extractor = RustExtractor()
    
    for content in _parse_all_rs_files(tarball_bytes):
        # Look for the specific function
        sig_pattern = re.compile(
            r'\b(?:pub\s+)?fn\s+' + re.escape(function_name) + r'\s*\([^)]*\)(?:\s*->\s*[^{]+)?',
            re.MULTILINE
        )
        match = sig_pattern.search(content)
        if match:
            return extractor.clean_signature(match.group(0).strip())
    
    return ""