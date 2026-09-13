from __future__ import annotations

from collections.abc import Iterable

# === SETTINGS START ===
CONTENT_TO_SIGNATURE = False
SMALL_FILE_THRESHOLD_MB = 0.05  # Files < this size (MB): show full content
BIG_FILE_THRESHOLD_MB = 5       # Files > this size (MB): skip entirely
# When CONTENT_TO_SIGNATURE = False:
#   - small (< SMALL_FILE_THRESHOLD_MB): show full content
#   - medium (SMALL_FILE_THRESHOLD_MB - BIG_FILE_THRESHOLD_MB): extract signatures
#   - big (> BIG_FILE_THRESHOLD_MB): skip
# When CONTENT_TO_SIGNATURE = True: all files under BIG_FILE_THRESHOLD_MB get signatures
SKIP_BINARY_FILES_PATH_TREE = False
USE_GIT_AND_HG_IGNORE = True    # Enable both .gitignore and .hgignore filtering
CONVERT_TO_LF = True      # Convert all line endings (CRLF, CR) to Unix LF (\n)
CONVERT_TO_TABS = True    # Automatically convert spaces to 1 tab indentation
ADDITIONAL_IGNORE_PATTERNS = [
    "package-lock.json",
    "node_modules/",
    ".git/",
    ".hg/",
]
ASCII_TREE_SHOW = True
ASCII_TREE_SHOW_IGNORED = True # show big files, binary, and ignored folders but except subfolders (is possible? TODO)
ASCII_TREE_SHOW_SIZE_THRESHOLD = 0.1  # None - do not show |  10 mean show if file > 10MB

SHOW_CONTENT_WHITELIST = {
    "py", "js", "ts", "jsx", "tsx", "java", "c", "cpp", "h", "hpp", "cs", "go", "rs", "rb", "php", "swift",
    "kt", "kts", "scala", "dart", "lua", "r", "m", "sql", "sh", "bash", "zsh", "ps1", "bat", "cmd", "vbs",
    "html", "htm", "css", "scss", "sass", "less", "xml", "json", "yaml", "yml", "toml", "ini", "cfg",
    "conf", "env", "properties", "md", "txt", "rst", "tex", "org", "makefile", "cmake", "dockerfile",
    "gradle", "pom", "csv", "tsv", "log", "diff", "patch",
}
SHOW_CONTENT = "ALL_NON_BINARY"  # None | "ALL_NON_BINARY" | array SHOW_CONTENT_WHITELIST
CONTENT_MARK_FORMAT = "markdown"  # "default" | "markdown"
#TODO if SHOW_CONTENT is false (None 0 itc - then no need to check in loop)

BINARY_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".pyd",
    ".so", ".dll", ".dylib", ".o", ".a", ".lib", ".exe", ".bin",
    ".obj", ".pdb", ".exp", ".ilk",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".class", ".wasm", ".rlib", ".rmeta", ".d", ".s", ".S", ".i",
})
# === SETTINGS END ===

import sys
from pathlib import Path
"""
concatenate to single txt and put into clipboard

user copy files in OS file explorer to clipboard (references) or "copy as path" (strings list), then run this script, so extract path from that info. works good on windows explorer/clipboard format

this script get from clipboard (a list of file pointers) path(s) of folder(s) or file(s). 
read all files in these folders/files
produce output like

1
└── a.cs

2
└── b.txt

c.txt

====== 1/a.cs, c.txt
....same hash content....

====== 2/b.txt
....content2....

Or with CONTENT_MARK_FORMAT = "markdown":

1/a.cs, c.txt
```csharp
....same hash content....
```

2/b.txt
```
....content2....
```

if files are in same folder - don't write path (because same common parent)
else write relative path related to nearest common parent

In the start of script constants settings and this description.
keep comments.
"""

SMALL_FILE_THRESHOLD_BYTES = int(SMALL_FILE_THRESHOLD_MB * 1024 * 1024)
BIG_FILE_THRESHOLD_BYTES = int(BIG_FILE_THRESHOLD_MB * 1024 * 1024)

import os
import platform
import urllib.parse
import re
import fnmatch
import hashlib
import pathspec  # pip install pathspec
from dev_helper.common.clipboard import copy_to_clipboard

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

from dev_helper.common.text_utils import (
    normalize_indentation_to_tabs,
    normalize_line_endings,
    PRESERVE_SPACE_EXTS,
)
from dev_helper.common.signature_extractor import extract_signatures

EXT_CONFIG = {
    "html":        ("html", "index"),
    "css":         ("css", "style"),
    "javascript":  ("js", "script"),
    "typescript":  ("ts", "mod"),
    "json":        ("json", "data", True),
    "python":      ("py", "func", True),
    "sql":         ("sql", "query"),
    "csharp":      ("cs", "class"),
    "cpp":         ("cpp", "main"),
    "php":         ("php", "index"),
    "go":          ("go", "main"),
    "rust":        ("rs", "main"),
    "txt":         ("txt", "file", True),   
    "md":          ("md", "README", True),   

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
    "csv":         ("csv", "data", True),
    "tsv":         ("tsv", "data", True),

    # Documentation & markup formats
    "markdown":    ("md", "README", True),
    "text":        ("txt", "file", True),
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

_gitignore_cache = {}
_hgignore_cache = {}

def load_gitignore_spec(root_dir):
    """
    Looks for a .gitignore file in the root directory.
    Combines file content with ADDITIONAL_IGNORE_PATTERNS.
    Returns a PathSpec object if found, or None.
    """
    if root_dir in _gitignore_cache:
        return _gitignore_cache[root_dir]
    
    lines = []
    
    # Load .gitignore only if enabled
    if USE_GIT_AND_HG_IGNORE:
        gitignore_path = os.path.join(root_dir, ".gitignore")
        if os.path.exists(gitignore_path):
            try:
                with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except Exception as e:
                print(f"Warning: Could not read .gitignore in {root_dir}: {e}")
    
    # Always append additional ignore patterns
    lines.extend(
        pattern.rstrip("\n") + "\n" for pattern in ADDITIONAL_IGNORE_PATTERNS
    )

    
    if lines:
        try:
            spec = pathspec.PathSpec.from_lines(
                pathspec.patterns.GitWildMatchPattern, lines
            )
            _gitignore_cache[root_dir] = spec
            return spec
        except Exception as e:
            print(f"Warning: Could not compile ignore patterns: {e}")
    
    _gitignore_cache[root_dir] = None
    return None


def load_hgignore_spec(root_dir):
    """
    Looks for a .hgignore file in the root directory.
    Parses and returns a list of compiled regex matchers.
    """
    if root_dir in _hgignore_cache:
        return _hgignore_cache[root_dir]

    if not USE_GIT_AND_HG_IGNORE:
        _hgignore_cache[root_dir] = []
        return []

    hgignore_path = os.path.join(root_dir, ".hgignore")
    if not os.path.exists(hgignore_path):
        _hgignore_cache[root_dir] = []
        return []

    matchers = []
    current_syntax = "regexp"  # Mercurial default format

    try:
        with open(hgignore_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue

                # Check for syntax state changes
                if line.startswith("syntax:"):
                    syntax_type = line.split(":", 1)[1].strip()
                    if syntax_type in ["regexp", "glob"]:
                        current_syntax = syntax_type
                    continue

                try:
                    if current_syntax == "regexp":
                        # Raw Python regex matching anywhere in the string
                        matchers.append(re.compile(line))
                    elif current_syntax == "glob":
                        # Convert shell wildcards to an active regex pattern
                        regex_pattern = fnmatch.translate(line)
                        matchers.append(re.compile(regex_pattern))
                except Exception as pattern_err:
                    print(f"Warning: Skipping invalid pattern '{line}' in .hgignore: {pattern_err}")

        _hgignore_cache[root_dir] = matchers
        return matchers
    except Exception as e:
        print(f"Warning: Could not read .hgignore in {root_dir}: {e}")
    
    _hgignore_cache[root_dir] = []
    return []


def is_hg_ignored(rel_path, hg_matchers):
    """
    Checks if a relative path matches any compiled .hgignore regex rules.
    Mercurial matches against the file path or any prefix path components.
    """
    if not hg_matchers:
        return False
        
    # Normalize to forward slashes for unified cross-platform regex alignment
    normalized_path = rel_path.replace("\\", "/")
    
    # Check each path component against the compiled expressions
    parts = normalized_path.split("/")
    for i in range(len(parts)):
        prefix = "/".join(parts[:i+1])
        for matcher in hg_matchers:
            if matcher.search(prefix):
                return True
    return False


def is_binary(file_path):
    """Checks if a file is binary by looking for a null byte in the first 1KB."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return True
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(1024)
            return b'\x00' in chunk
    except Exception:
        return False


def _generate_size_label(name, path, size_threshold_bytes):
    try:
        size = os.path.getsize(path)
        if size > size_threshold_bytes:
            size_mb = size / (1024 * 1024)
            if size_mb >= 1:
                return name + f" {size_mb:.1f}MB"
            else:
                return name + f" {size_mb * 1024:.0f}KB"
    except Exception:
        pass
    return name


def generate_ascii_tree(base_path: str, paths=None, show_tree: bool = True, show_ignored: bool = False, is_ignored_fn=None, show_root: bool = True) -> str:
    if not show_tree:
        return ""
    if paths is None and not os.path.exists(base_path):
        return ""
    if os.path.isfile(base_path):
        name = os.path.basename(base_path)
        if ASCII_TREE_SHOW_SIZE_THRESHOLD is not None:
            size_threshold_bytes = int(ASCII_TREE_SHOW_SIZE_THRESHOLD * 1024 * 1024)
            name = _generate_size_label(name, base_path, size_threshold_bytes)
        return name + "\n\n"

    all_rel_paths = _collect_paths(base_path, paths)
    
    has_folders = any("/" in p for p in all_rel_paths)
    if not has_folders:
        return ""

    ignore_cache = {} if is_ignored_fn else None

    def is_ignored_cached(rel_path: str) -> bool:
        if not is_ignored_fn:
            return False
        cached = ignore_cache.get(rel_path)
        if cached is not None:
            return cached
        result = is_ignored_fn(rel_path)
        ignore_cache[rel_path] = result
        return result

    tree_dict = {}
    for rel_path in sorted(all_rel_paths):
        parts = rel_path.split('/')
        current = tree_dict
        skipped = False
        ignore_at = -1

        for i, part in enumerate(parts):
            node_path = '/'.join(parts[:i + 1])
            if is_ignored_cached(node_path):
                if not show_ignored:
                    skipped = True
                    break
                ignore_at = i
                break

        if skipped:
            continue

        end = ignore_at + 1 if ignore_at >= 0 else len(parts)
        for part in parts[:end]:
            current = current.setdefault(part, _TreeNode())

    folder_paths = set()
    for rel_path in sorted(all_rel_paths):
        parts = rel_path.split('/')
        for i in range(1, len(parts)):
            folder_paths.add('/'.join(parts[:i]))

    lines = _render_tree(tree_dict, is_ignored_cached, folder_paths, base_path, show_root)
    root_name = os.path.basename(os.path.normpath(base_path)) or "."
    if show_root:
        return "\n".join([root_name] + lines) + "\n\n"
    return "\n".join(lines) + "\n\n"

def _collect_paths(base_path, paths):
    all_rel_paths = []
    if paths is not None:
        for p in paths:
            all_rel_paths.append(p.replace("\\", "/"))
    else:
        for root, dirs, files in os.walk(base_path):
            for file in files:
                all_rel_paths.append(os.path.relpath(os.path.join(root, file), base_path).replace("\\", "/"))
    return all_rel_paths


class _TreeNode(dict):
    __slots__ = ()


def _render_tree(tree_dict, is_ignored_fn, folder_paths, base_path, show_root=True):
    lines = []
    size_threshold_bytes = None
    if ASCII_TREE_SHOW_SIZE_THRESHOLD is not None:
        size_threshold_bytes = int(ASCII_TREE_SHOW_SIZE_THRESHOLD * 1024 * 1024)

    def _render(tree_dict, parent_path, is_top):
        result = []
        items = sorted(tree_dict.keys(), key=lambda k: (0 if k in folder_paths or bool(tree_dict[k]) else 1, k.lower()))
        for index, name in enumerate(items):
            is_last = index == len(items) - 1
            if is_top and not show_root:
                connector = ""
            else:
                connector = "└── " if is_last else "├── "
            child_node = tree_dict[name]
            full_path = f"{parent_path}/{name}" if parent_path else name
            line = connector + name
            is_file = full_path not in folder_paths and not bool(child_node)
            if is_file and size_threshold_bytes is not None:
                file_full_path = os.path.join(base_path, full_path.replace("/", os.sep))
                line = _generate_size_label(line, file_full_path, size_threshold_bytes)
            result.append(line)
            if child_node:
                if is_top and not show_root:
                    next_prefix = ""
                else:
                    next_prefix = "    " if is_last else "│   "
                result.extend(next_prefix + child for child in _render(child_node, full_path, False))
        return result

    return _render(tree_dict, "", True)

def _build_check_path_ignored(base_path, git_spec, hg_matchers):
    def check_path_ignored(rel_path: str) -> bool:
        if not rel_path:
            return False
        full_check_path = os.path.join(base_path, rel_path.replace("/", os.sep))
        if git_spec:
            if git_spec.match_file(rel_path):
                return True
            if not rel_path.endswith("/") and git_spec.match_file(rel_path + "/"):
                return True
        if hg_matchers and is_hg_ignored(rel_path, hg_matchers):
            return True
        if os.path.isfile(full_check_path):
            if SKIP_BINARY_FILES_PATH_TREE and is_binary(full_check_path):
                return True
        return False

    return check_path_ignored


try:
    from path_args import paths_from_clipboard as _paths_from_clipboard
except ImportError:
    _paths_from_clipboard = None  # type: ignore[assignment]


def _lines_to_path_strings(text: str | None) -> list[str]:
    if not text:
        return []
    return [cleaned for line in text.splitlines() if (cleaned := _clean_path_string(line))]


def _file_uri_to_path(uri: str) -> str:
    parsed = urllib.parse.urlparse(uri)
    path = urllib.parse.unquote(parsed.path)

    if platform.system() == "Windows":
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        if re.match(r"^/[A-Za-z]:/", path):
            path = path[1:]

    return path


def _clean_path_string(value: str) -> str:
    return value.strip().strip('"\'')


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = os.path.normcase(os.path.normpath(value))
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _get_paths_from_clipboard() -> list[str]:
    if _paths_from_clipboard is not None:
        return _paths_from_clipboard()
    return []


def _collect_files_for_path(path):
    path = _clean_path_string(path)
    if not os.path.exists(path):
        print(f"Warning: Path does not exist -> {path}")
        return None

    git_spec = None
    hg_matchers = []
    base_path = os.path.dirname(path) if os.path.isfile(path) else path
    files_to_process = []
    all_rel_paths_for_tree = []

    if os.path.isfile(path):
        git_spec = load_gitignore_spec(os.path.dirname(path))
        hg_matchers = load_hgignore_spec(os.path.dirname(path))
        check_path_ignored = _build_check_path_ignored(os.path.dirname(path), git_spec, hg_matchers)
        rel_path = os.path.basename(path)
        all_rel_paths_for_tree.append(rel_path)
        if not check_path_ignored(rel_path):
            files_to_process.append(path)
    elif os.path.isdir(path):
        git_spec = load_gitignore_spec(path)
        hg_matchers = load_hgignore_spec(path)
        check_path_ignored = _build_check_path_ignored(base_path, git_spec, hg_matchers)
        base_rel = os.path.basename(os.path.normpath(path)) or "."
        if check_path_ignored(base_rel):
            if ASCII_TREE_SHOW_IGNORED:
                all_rel_paths_for_tree.append(base_rel)
        else:
            for root, dirs, files in os.walk(path):
                for file in files:
                    full_file_path = os.path.join(root, file)
                    rel_match_path = os.path.relpath(full_file_path, base_path).replace("\\", "/")
                    all_rel_paths_for_tree.append(rel_match_path)
                    if check_path_ignored(rel_match_path):
                        continue
                    files_to_process.append(full_file_path)

    return {
        "base_path": base_path,
        "files_to_process": files_to_process,
        "all_rel_paths_for_tree": all_rel_paths_for_tree,
        "check_path_ignored": check_path_ignored,
    }


def _get_language_for_ext(ext: str) -> str:
    if ext in ("txt", "text"):
        return ""
    for lang_name, lang_config in EXT_CONFIG.items():
        if ext in lang_config:
            return lang_name
    return ""

def _format_block(rel_paths: list[str], content: str) -> str:
    if CONTENT_MARK_FORMAT == "markdown":
        ext = os.path.splitext(rel_paths[0])[1].lstrip(".").lower() if rel_paths else ""
        lang = _get_language_for_ext(ext)
        paths_line = "\n".join(rel_paths)
        if lang:
            return f"{paths_line}\n```{lang}\n{content}\n```"
        else:
            return f"{paths_line}\n```\n{content}\n```"
    return f"====== {', '.join(rel_paths)}\n{content}"

def _read_file_content(file_path, rel_path):
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        if CONVERT_TO_LF:
            content = normalize_line_endings(content)

        if CONVERT_TO_TABS:
            ext = os.path.splitext(file_path)[1].lstrip(".").lower()
            content = normalize_indentation_to_tabs(content, ext, PRESERVE_SPACE_EXTS)

        chunk = _format_block([rel_path], content)
        return (hashlib.sha256(content.encode("utf-8")).hexdigest(), chunk)
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return None


def _deduplicate_chunks(chunks):
    if not chunks:
        return []

    hash_map = {}
    for h, chunk in chunks:
        hash_map.setdefault(h, []).append(chunk)

    formatted = []
    for h, chunk_list in hash_map.items():
        if len(chunk_list) == 1:
            formatted.append(chunk_list[0])
        else:
            rel_paths = []
            for chunk in chunk_list:
                if CONTENT_MARK_FORMAT == "markdown":
                    fence_idx = chunk.find("```")
                    raw = chunk[:fence_idx].strip() if fence_idx != -1 else chunk
                else:
                    line_end = chunk.find('\n')
                    raw = chunk[:line_end].strip() if line_end != -1 else chunk
                    if raw.startswith("====== "):
                        raw = raw[len("====== "):]
                rel_paths.extend(p.strip() for p in raw.split("\n") if p.strip())
            first_chunk = chunk_list[0]
            if CONTENT_MARK_FORMAT == "markdown":
                match = re.search(r'(?s)```[^\n]*\n(.*?)(?:\n)?```$', first_chunk)
                content = match.group(1) if match else ""
            else:
                content = first_chunk.split('\n', 1)[1] if '\n' in first_chunk else ""
            formatted.append(_format_block(sorted(set(rel_paths)), content))

    return formatted



def _find_common_prefix_dir(paths: list[str]) -> str:
    """Find the longest common path prefix (directory) shared by all paths."""
    if not paths:
        return ""
    common_parts = None
    for path in paths:
        parts = Path(path).resolve().parts
        if common_parts is None:
            common_parts = list(parts)
        else:
            common_parts = [p for p, q in zip(common_parts, parts) if p == q]
    if not common_parts:
        return ""
    return str(Path(*common_parts))

def concatenate_files():
    paths = _get_paths_from_clipboard()

    if not paths:
        print("Clipboard is empty or does not contain valid file paths.")
        return

    folders = []
    files = []
    for path in paths:
        path = _clean_path_string(path)
        if not os.path.exists(path):
            continue
        if os.path.isdir(path):
            folders.append(path)
        else:
            files.append(path)

    folders.sort(key=lambda p: os.path.basename(p).lower())
    files.sort(key=lambda p: os.path.basename(p).lower())
    ordered_paths = folders + files
    
    # Determine the common prefix directory of all selected paths (the tree base).
    common_prefix = _find_common_prefix_dir(paths)
    selected_dirs = {os.path.normcase(os.path.normpath(p)) for p in ordered_paths if os.path.isdir(p)}
    show_root = bool(common_prefix) and os.path.normcase(os.path.normpath(common_prefix)) in selected_dirs
    # Content paths are shown relative to:
    #  - the parent of the common prefix when that prefix is itself an explicitly selected folder
    #  - the common prefix (the container) otherwise
    if show_root and common_prefix:
        content_base = os.path.dirname(common_prefix)
    else:
        content_base = common_prefix

    collected_items = []
    all_content_chunks = []
    signature_blocks: list[tuple[str, str]] = []

    for path in ordered_paths:
        collected = _collect_files_for_path(path)
        if not collected:
            continue
        collected_items.append(collected)

        for file_path in collected["files_to_process"]:
            file_size = os.path.getsize(file_path)

            should_show = (
                SHOW_CONTENT == "ALL_NON_BINARY" or
                (SHOW_CONTENT and not isinstance(SHOW_CONTENT, str) and os.path.splitext(file_path)[1].lstrip(".").lower() in SHOW_CONTENT)
            )
            if SKIP_BINARY_FILES_PATH_TREE and is_binary(file_path):
                should_show = False

            if not should_show or file_size > BIG_FILE_THRESHOLD_BYTES:
                continue

            # Compute relative path from the content base for proper naming
            rel_path = os.path.relpath(file_path, content_base).replace("\\", "/") if content_base else os.path.basename(file_path)

            if CONTENT_TO_SIGNATURE or file_size >= SMALL_FILE_THRESHOLD_BYTES:
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        file_content = f.read()
                    ext = os.path.splitext(file_path)[1].lstrip(".").lower()
                    sig_text, sig_count = extract_signatures(file_content, ext)
                    if sig_text:
                        signature_blocks.append((hashlib.sha256(file_content.encode("utf-8")).hexdigest(), _format_block([rel_path], sig_text)))
                except Exception as e:
                    print(f"Error reading {file_path}: {e}")
            else:
                chunk = _read_file_content(file_path, rel_path)
                if chunk:
                    all_content_chunks.append(chunk)

    # Build a single combined tree relative to the common prefix (covers both
    # a copied folder and several files copied from inside a folder).
    tree_str = ""
    if ASCII_TREE_SHOW and common_prefix:
        tree_rel_paths = []
        ignore_specs = []
        for collected in collected_items:
            base_path = collected["base_path"]
            prefix = os.path.relpath(base_path, common_prefix).replace("\\", "/") if common_prefix else ""
            if prefix == ".":
                prefix = ""
            ignore_specs.append((prefix, collected["check_path_ignored"]))
            for rel in collected["all_rel_paths_for_tree"]:
                tree_rel_paths.append(f"{prefix}/{rel}" if prefix else rel)

        def _combined_ignore(rel_path: str) -> bool:
            for prefix, check_fn in ignore_specs:
                if prefix in ("", "."):
                    if check_fn(rel_path):
                        return True
                elif rel_path == prefix or rel_path.startswith(prefix + "/"):
                    sub = "" if rel_path == prefix else rel_path[len(prefix) + 1:]
                    if check_fn(sub):
                        return True
            return False

        tree_str = generate_ascii_tree(
            base_path=common_prefix,
            paths=tree_rel_paths,
            show_tree=ASCII_TREE_SHOW,
            show_ignored=ASCII_TREE_SHOW_IGNORED,
            is_ignored_fn=_combined_ignore if ignore_specs else None,
            show_root=show_root,
        ) or ""

    if CONTENT_TO_SIGNATURE:
        deduped_chunks = _deduplicate_chunks(signature_blocks)
    else:
        content_chunks = _deduplicate_chunks(all_content_chunks)
        deduped_chunks = content_chunks + [block for _, block in signature_blocks]

    if deduped_chunks or tree_str:
        elements = []
        if tree_str:
            elements.append(tree_str.strip())

        if deduped_chunks:
            elements.append("\n\n".join(deduped_chunks))

        final_string = "\n\n".join(elements)
        copy_to_clipboard(final_string)

        if CONTENT_TO_SIGNATURE:
            sig_count = sum(len(block.splitlines()) for block in deduped_chunks)
            print(f"\nSuccess! Processed {sig_count} signatures.")
        else:
            print(f"\nSuccess! Processed {len(deduped_chunks)} files.")
        print("The concatenated text is now in your clipboard.")
    else:
        print("\nNo readable text content found in the selected files.")

if __name__ == "__main__":
    concatenate_files()