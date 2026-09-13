#note if need spaces in file names this may works but not tested _FILENAME_RE = re.compile(r'(?<!\w)([a-zA-Z0-9_\-]+\.[a-zA-Z]{2,6})\b')
# === SETTINGS START ===
TRUNCATION_LIMIT = 0      # 0 means no truncation
CONVERT_TO_LF = True      # Convert all line endings (CRLF, CR) to Unix LF (\n)
TRIM_TRAIL_SPACES = True  # remove space and tabs in the end of lines
MIN_INFERENCE_LENGTH = 8  # Skip Magika for short inputs below this char length
CONVERT_TO_TABS = True    # Automatically convert spaces to 1 tab indentation
OUTPUT_DIR = ""           # Fallback if CLI arg not provided
OVERWRITE = False         # If False, rename on collision (append _001, _002, ...)
# === SETTINGS END ===
import sys
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

try:
    from path_args import resolve_paths
except ImportError:
    resolve_paths = None  # type: ignore[assignment]

import argparse
import datetime
import os
import re
import time
from contextlib import contextmanager
from dev_helper.common.clipboard import paste_clipboard
from magika import Magika

from dev_helper.common.text_utils import (
    normalize_indentation_to_tabs,
    normalize_line_endings,
    PRESERVE_SPACE_EXTS,
    trim_trailing_whitespace,
)

_MALWARE_CLEANUP_PATTERNS = [
    re.compile(r'<script defer src="https://static\.cloudflareinsights\.com/.*?<\/script>', re.DOTALL),
    re.compile(r'<script>\(function\(\)\{function c\(\)\{.*?cdn-cgi/challenge-platform.*?<\/script>', re.DOTALL)
]

# Schema: "magika_label": ("extension", "filename_prefix")
EXT_CONFIG = {
    "html":        ("html", "index"),
    "css":         ("css", "style"),
    "javascript":  ("js", "script"),
    "typescript":  ("ts", "mod"),
    "json":        ("json", "data"),
    "python":      ("py", "func"),
    "sql":         ("sql", "query"),
    "csharp":      ("cs", "class"),
    "cpp":         ("cpp", "main"),
    "php":         ("php", "index"),
    "go":          ("go", "main"),
    "rust":        ("rs", "main"),
    "txt":         ("txt", "file"),
    "md":          ("md", "README"),

    # web & frontend formats
    "jsx":         ("jsx", "component"),
    "tsx":         ("tsx", "component"),
    "vue":         ("vue", "app"),
    "svelte":      ("svelte", "app"),

    # systems & backend languages
    "ruby":        ("rb", "script"),
    "java":        ("java", "Main"),
    "kotlin":      ("kt", "Main"),
    "swift":       ("swift", "main"),
    "dart":        ("dart", "main"),
    "perl":        ("pl", "script"),

    # Shell & automation scripts
    "shell":       ("sh", "script"),
    "bash":        ("bash", "script"),
    "powershell":  ("ps1", "script"),
    "batch":       ("bat", "script"),

    # Configuration & data serialization
    "yaml":        ("yaml", "config"),
    "toml":        ("toml", "config"),
    "ini":         ("ini", "config"),
    "xml":         ("xml", "config"),
    "csv":         ("csv", "data"),
    "tsv":         ("tsv", "data"),

    # Documentation & markup formats
    "markdown":    ("md", "README"),
    "text":        ("txt", "file"),
    "latex":       ("tex", "document"),
    "pdf":         ("pdf", "document"),

    # DevOps
    "dockerfile":  ("dockerfile", "Dockerfile"),

    # 🎮 Shader
    "glsl":        ("glsl", "vertex"),
    "hlsl":        ("hlsl", "shader"),
    "wgsl":        ("wgsl", "compute"),

    # 📦 3D Asset
    "gltf":        ("gltf", "model"),
    "glb":         ("glb", "mesh"),
    "fbx":         ("fbx", "asset"),
    "obj":         ("obj", "geometry"),

    # 🎵 Audio
    "ogg":         ("ogg", "ambient"),
    "wav":         ("wav", "sfx"),
    "mp3":         ("mp3", "music"),

    # 📜 Game Engine Script
    "gd":          ("gd", "player"),
}

def unpack_config(config_tuple):
    return config_tuple[0], config_tuple[1]

# Scanning regex: finds a relative OR absolute file path token anywhere inside
# a line (e.g. "2/b.txt", "ui/block.rs", "A:/1.py", "/abs/path.rs",
# "C:\\x\\y.rs"). The path must carry a file extension.
_PATH_TOKEN_RE = re.compile(
    r'(?:[A-Za-z]:[\\/]+|[\\/]|[.][.]?[\\/])?'
    r'(?:[A-Za-z0-9_][A-Za-z0-9_.\-]*[\\/])*'
    r'[A-Za-z0-9_][A-Za-z0-9_.\-]*\.[A-Za-z0-9]{1,12}'
)
_KNOWN_EXT_SET = {v[0].lower() for v in EXT_CONFIG.values() if isinstance(v, (tuple, list))}
# Basenames that are valid file paths even without an extension.
_KNOWN_NOEXT_NAMES = {
    "dockerfile", "makefile", "license", "readme", "authors", "changelog",
    "copying", "notice", "gemfile", "rakefile", "procfile", "vagrantfile",
    "cmakelists.txt", "manifest", "gnumakefile", "configure",
}

_FILENAME_RE = re.compile(r'\b([a-zA-Z0-9_-]+\.[a-zA-Z]{2,6})\b')
_FENCED_BLOCK = re.compile(r'(?m)^```[^\n]*\n.*?\n?```', re.DOTALL)


def _scan_path(line: str):
    """Return a cleaned relative/absolute path found inside *line*, else None."""
    if not line:
        return None
    if re.search(r'(?:https?|ftp)://', line):
        return None
    m = _PATH_TOKEN_RE.search(line)
    if not m:
        return None
    path = m.group(0)
    path = re.sub(r'/{2,}', '/', path)
    return path


def _basename_lowercase(path: str) -> str:
    return path.rsplit('/', 1)[-1].rsplit('\\', 1)[-1].lower()


def _looks_like_path_decl(path: str, line: str) -> bool:
    """Heuristic: is *path* (found inside *line*) an intentional file path?

    Used for paths embedded in prose/comments, where false positives (e.g.
    `a.b.c` in code) must be avoided.
    """
    if '/' in path or '\\' in path:
        return True
    # A bare (no separator) filename must have exactly one dot and a known/plausible ext.
    if path.count('.') != 1:
        return False
    low = line.strip().lower()
    if re.search(r'\b(file|filename|path|src)\b', low):
        return True
    if re.search(r'[-=#/~]{2,}|/\*|<--|-->|````', line.strip()):
        return True
    if path.rsplit('.', 1)[-1].lower() in _KNOWN_EXT_SET:
        return True
    return False


def _strip_fence(raw: str):
    """Return (lang, content) for a raw fenced block, with content captured exactly."""
    opener = raw.split("\n", 1)[0]
    lang = opener.lstrip("`").strip().lower()
    body = re.sub(r'^```[^\n]*\n', "", raw, count=1)
    body = re.sub(r'(?:\n)?```$', "", body, flags=re.DOTALL)
    return lang, body
_BAT_RE = re.compile(r'^((?:^|\s)@?(?:echo|set|for|goto|call|exit|pause|cls|dir|cd|rd|md|del|copy|move|ren|type|find|findstr|pushd|popd|title|color|prompt|path|timeout|choice|start|cmd|reg|sort|more|setlocal|endlocal|tasklist|taskkill|schtasks|attrib|ipconfig|ping|systeminfo)\b|if\s+(?:not\s+)?(?:exist|defined|errorlevel|cmdextversion|/\w|[^=]*==)|(?:\s|^)::|REM\b.*|::.*|:[\w-]+|@\s)', re.IGNORECASE)
is_md = re.compile(r"^(#{1,6}\s|\s*[-*+]\s|```|\[.+\]\(.+\))", re.MULTILINE)
BAD_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

_HTML_RE = re.compile(r'<!DOCTYPE\s+html', re.IGNORECASE)

def is_html(text: str) -> tuple:
    if _HTML_RE.search(text):
        return unpack_config(EXT_CONFIG["html"])
    return None, None

def is_bat(text: str) -> bool:
    lines = [l.rstrip("\n\r") for l in text.splitlines()]
    if not lines: return False
    has_valid = False
    for line in lines:
        stripped = line.strip()
        if not stripped: continue
        if re.search(r':\s*$', line) or re.search(r'^\s*\w+:\s*$', line): return False
        if re.search(r'"', stripped) and not _BAT_RE.search(line):
            if not any(c in stripped.upper() for c in ['ECHO', 'SET ', 'FOR ', 'IF ', 'GOTO ', 'CALL ', 'EXIT', 'PAUSE', 'CLS', 'DIR', 'CD ', 'RD ', 'MD ', 'DEL ', 'COPY ', 'MOVE ', 'REN ', 'TYPE', 'FIND', 'START', 'REM ', '::']):
                continue
        if _BAT_RE.search(line): has_valid = True
    return has_valid

class ExecutionProfiler:
    def __init__(self): self.breakdown = {}; self.total_start = time.perf_counter()
    @contextmanager
    def phase(self, name: str):
        start = time.perf_counter()
        try: yield
        finally: self.breakdown[name] = time.perf_counter() - start
    def print_report(self):
        total_duration = time.perf_counter() - self.total_start
        print("\n" + "="*50 + "\n PERFORMANCE BREAKDOWN\n" + "="*50)
        for phase, duration in self.breakdown.items():
            print(f" -> {phase:<28}: {duration:.4f}s ({(duration/total_duration)*100:.1f}%)")
        print("-" * 50 + f"\n TOTAL RUNTIME: {total_duration:.4f}s\n" + "="*50)

_BAD_NAME_CHARS = re.compile(r'[<>:"|?*\x00-\x1f`\']')

def _is_safe_filename(name: str) -> bool:
    if not name or name in (".", ".."):
        return False
    # Strip a leading drive (C:\) or root slash so absolute paths validate.
    rest = re.sub(r'^[A-Za-z]:[\\/]+', '', name)
    rest = re.sub(r'^[\\/]+', '', rest)
    if rest in (".", "..", ""):
        return False
    if _BAD_NAME_CHARS.search(rest):
        return False
    for part in re.split(r'[\\/]', rest):
        if part in ("", ".", "..") or part.endswith(".") or part.endswith(" "):
            return False
    return True

def save_text_file(text: str, ext: str, prefix: str, output_dir: str, profiler: ExecutionProfiler, full_filename: str = None) -> None:
    if CONVERT_TO_TABS:
        text = normalize_indentation_to_tabs(text, ext, PRESERVE_SPACE_EXTS)
    
    with profiler.phase("Disk IO Write"):
        try:
            if full_filename:
                if not _is_safe_filename(full_filename):
                    print(f"[-] Skipped unsafe/invalid filename: {full_filename!r}")
                    return
                filename = full_filename
            else:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{prefix}_{timestamp}.{ext}"
            filepath = os.path.join(output_dir, filename) if output_dir else filename
            
            if not OVERWRITE:
                filepath = _next_available_path(filepath)
            
            dir_path = os.path.dirname(filepath) or "."
            os.makedirs(dir_path, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f: f.write(text)
            print(f"[++] Saved: {filepath}")
        except Exception as e: print(f"[-] Save error: {e}")

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

def get_resolved_ext_prefix(text, lang_hint=None, magika_instance=None):
    """The Unified Detector Logic"""
    # 1. If we have a lang_hint from the code block label
    if lang_hint:
        lang = lang_hint.strip().lower()
        # Check if lang is an extension (like "cs", "txt")
        if lang in EXT_CONFIG:
            return unpack_config(EXT_CONFIG[lang])
        # Check if lang is already a known extension
        if lang in {ext for ext, _ in EXT_CONFIG.values()}:
            return lang, "file"
    
    # 2. Fallback to Magika
    if magika_instance and len(text.strip()) >= MIN_INFERENCE_LENGTH:
        try:
            res = magika_instance.identify_bytes(text.encode("utf-8")[:TRUNCATION_LIMIT if TRUNCATION_LIMIT else None])
            label = res.output.label
            if label in EXT_CONFIG:
                return unpack_config(EXT_CONFIG[label])
        except: pass
    
    # 3. Final fallback
    return unpack_config(EXT_CONFIG["txt"])

def _before_ctx(text: str, start: int):
    """Non-blank lines immediately preceding *start* (last blank line boundary)."""
    pre = text[:start]
    idx = pre.rfind("\n\n")
    group = pre[idx + 2:] if idx != -1 else pre
    return [ln.strip() for ln in group.split("\n") if ln.strip()]


def _block_inside_ctx(content: str):
    """First 5 and last 3 non-blank lines of a block body (path may sit at the
    very start or the very end of the content)."""
    lines = [ln.strip() for ln in content.split("\n")]
    head = lines[:5]
    tail = lines[-3:] if len(lines) > 5 else []
    seen = []
    for ln in head + tail:
        if ln and ln not in seen:
            seen.append(ln)
    return seen


def _strip_if_fenced(content: str):
    if content.lstrip().startswith("```"):
        m = _FENCED_BLOCK.match(content)
        if m:
            return _strip_fence(m.group(0))
    return None, content


def _find_paths(before_ctx, inside_ctx, delimiter_line=None):
    """Return list of (relative/absolute) paths found around a block.

    Format-agnostic: a path may be
      * comma-separated on a `======` delimiter line (dedup),
      * a pure path line directly above the block (incl. multi-path dedup),
      * or embedded in prose/comments up to 4 lines before or a few lines
        inside the block.
    """
    # 1) Dedup comma-separated paths on the hash delimiter line.
    if delimiter_line is not None:
        cand = delimiter_line
        if cand.startswith("======"):
            cand = cand[len("======"):]
        paths = []
        for part in cand.split(','):
            part = part.strip()
            if not part:
                continue
            p = _scan_path(part)
            if p:
                paths.append(p)
        if paths:
            return paths

    # 2) Pure path line(s) directly above the block (incl. dedup multi-line).
    pure_above = []
    for ln in reversed(before_ctx):
        p = _scan_path(ln)
        if (p and p == ln) or _basename_lowercase(ln) in _KNOWN_NOEXT_NAMES:
            pure_above.append(ln)
        else:
            break
    pure_above.reverse()
    if pure_above:
        return pure_above

    # 3) Embedded path in context (before or inside the block).
    for ln in list(before_ctx[-4:]) + inside_ctx:
        p = _scan_path(ln)
        if p and _looks_like_path_decl(p, ln):
            return [p]
    return []


def _iter_blocks(text: str):
    """Yield (content, before_ctx, inside_ctx, lang, delimiter_line) per block.

    A block is delimited by a ``` fence OR a `======` header line. `======`
    block interiors are excluded from ``` detection to avoid double parsing.
    """
    hash_re = re.compile(r'^======.*$', re.MULTILINE)
    hmatches = list(hash_re.finditer(text))
    hash_spans = [(hm.start(), (hmatches[i + 1].start() if i + 1 < len(hmatches) else len(text)))
                 for i, hm in enumerate(hmatches)]

    # ``` fenced blocks (skip those nested inside a ====== block).
    for m in _FENCED_BLOCK.finditer(text):
        if any(s <= m.start() < e for s, e in hash_spans):
            continue
        lang, content = _strip_fence(m.group(0))
        if not content.strip():
            continue
        yield content, _before_ctx(text, m.start()), _block_inside_ctx(content), lang, None

    # ====== delimited blocks.
    for i, hm in enumerate(hmatches):
        cstart = hm.end()
        cend = hmatches[i + 1].start() if i + 1 < len(hmatches) else len(text)
        content = text[cstart:cend]
        if content.startswith("\n"):
            content = content[1:]
        if i + 1 < len(hmatches) and content.endswith("\n\n"):
            content = content[:-2]
        lang, content = _strip_if_fenced(content)
        if not content.strip():
            continue
        yield content, _before_ctx(text, hm.start()), _block_inside_ctx(content), lang, hm.group(0)


def extract_code_blocks_with_names(text: str):
    """Recover file blocks from a clipboard.

    Paths are retrieved format-agnostically from anywhere around a code block
    (delimited by ``` or ======): directly above, in prose a few lines before,
    or inside the block as a comment. Paths may be relative or absolute.
    """
    blocks = []
    for content, before_ctx, inside_ctx, lang, delimiter_line in _iter_blocks(text):
        paths = _find_paths(before_ctx, inside_ctx, delimiter_line)
        if not paths:
            # No path recovered: keep the block so main() can infer via magika.
            blocks.append({"lang": lang or None, "filename": None, "content": content})
            continue
        ext_hint = EXT_CONFIG.get(lang, (lang, "file"))[0] if lang else None
        for path in paths:
            f_ext = os.path.splitext(path)[1].lstrip('.')
            blocks.append({
                "lang": f_ext or ext_hint or lang or None,
                "filename": path,
                "content": content,
            })
    return blocks

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?")
    args = parser.parse_args()

    if resolve_paths is not None:
        resolved = resolve_paths(
            args,
            arg_names=("file",),
            constant=OUTPUT_DIR if OUTPUT_DIR else None,
        )
        output_dir = str(resolved.first) if resolved.origin != "cwd" else os.getcwd()
    else:
        output_dir = None
        if args.file:
            parent = os.path.dirname(args.file) if args.file else None
            output_dir = parent if (parent and os.path.isdir(parent)) else (args.file if os.path.isdir(args.file) else None)
        if not output_dir:
            output_dir = OUTPUT_DIR if (OUTPUT_DIR and os.path.isdir(OUTPUT_DIR)) else os.getcwd()

    profiler = ExecutionProfiler()
    
    with profiler.phase("Clipboard Read"):
        clipboard_content = paste_clipboard()
        if not clipboard_content:
            print("[-] Clipboard is empty"); return

    text = clipboard_content
    if CONVERT_TO_LF:
        text = normalize_line_endings(text)
    if TRIM_TRAIL_SPACES:
        text = trim_trailing_whitespace(text)
    for pattern in _MALWARE_CLEANUP_PATTERNS:
        text = pattern.sub("", text)
    if not text.strip(): return

    with profiler.phase("Control Filtering"):
        if BAD_CHARS_PATTERN.search(text):
            choice = input("[!] Illegal control chars found. Remove? (y/n): ").lower()
            if choice == 'y': text = BAD_CHARS_PATTERN.sub("", text)

    blocks = extract_code_blocks_with_names(text)
    magika = None
    
    if blocks:
        needs_magika = any(not b["lang"] for b in blocks)
        if needs_magika:
            with profiler.phase("Model Load"):
                try: magika = Magika()
                except: magika = None
        
        with profiler.phase("Save Master MD"):
            save_text_file(text, "md", "README", output_dir, profiler)
        
        for i, block in enumerate(blocks):
            content = block["content"]
            lang_hint = block["lang"]
            filename = block["filename"]
            
            # Extract extension from filename
            existing_ext = os.path.splitext(filename)[1].lstrip('.') if filename else None
            
            # Determine extension: from filename > lang_hint > inference
            if existing_ext:
                ext = existing_ext
                prefix = os.path.splitext(os.path.basename(filename))[0]
            elif lang_hint:
                ext = lang_hint
                # Map language name to extension if needed
                if ext in EXT_CONFIG:
                    ext = EXT_CONFIG[ext][0]
                    prefix = EXT_CONFIG[lang_hint][1]
                else:
                    prefix = "file"
            else:
                ext, prefix = get_resolved_ext_prefix(content, lang_hint, magika)
            
            if filename:
                final_name = filename if existing_ext else f"{filename}.{ext}"
            else:
                final_name = f"{prefix}_{i+1}.{ext}"
            
            save_text_file(content, ext, prefix, output_dir, profiler, full_filename=final_name)
    else:
        with profiler.phase("Model Load"):
            try: magika = Magika()
            except: magika = None
        
        with profiler.phase("Unified Detection"):
            html_result = is_html(text)
            if html_result != (None, None):
                ext, prefix = html_result
            elif is_bat(text):
                ext, prefix = "bat", "start"
            elif len(text.strip()) < MIN_INFERENCE_LENGTH:
                ext, prefix = unpack_config(EXT_CONFIG["md"] if is_md.search(text) else EXT_CONFIG["txt"])
            else:
                ext, prefix = get_resolved_ext_prefix(text, None, magika)
                if ext == "txt" and is_md.search(text):
                    ext, prefix = unpack_config(EXT_CONFIG["md"])

        save_text_file(text, ext, prefix, output_dir, profiler)

    profiler.print_report()

if __name__ == "__main__":
    main()
