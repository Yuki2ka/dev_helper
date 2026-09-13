from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Literal

# === SETTINGS START ===
SUPPORTED_EXTENSIONS = [
    "*.py", "*.js", "*.ts", "*.jsx", "*.tsx",
    "*.html", "*.htm", "*.wgsl", "*.glsl", "*.vert", "*.frag",
    "*.java", "*.cpp", "*.c", "*.h", "*.cs", "*.php", "*.rs",
]
# === SETTINGS END ===


# ---------------------------------------------------------------------------
# Low-level scanning helpers shared by all extractors.
#
# All extractors follow the same strategy:
#   1. blank out comments and string literals (same length, offsets preserved)
#      so that brace/paren counting is never fooled by `}`, `)` inside strings
#   2. find declaration starts
#   3. parse ONLY the declaration header (parameter lists are matched with a
#      balanced-paren scan), and skip the body with a balanced-block scan
# Signature text is sliced from the ORIGINAL source, so literals in type
# positions (e.g. `type Dir = 'up' | 'down'`) survive.
# ---------------------------------------------------------------------------

def _is_ident_start(c: str) -> bool:
    return c.isalpha() or c in "_$"


def _is_ident_char(c: str) -> bool:
    return c.isalnum() or c in "_$"


def _skip_ws(text: str, i: int, limit: int | None = None) -> int:
    n = len(text) if limit is None else min(limit, len(text))
    while i < n and text[i].isspace():
        i += 1
    return i


def _match_paired(text: str, i: int, open_ch: str, close_ch: str,
                  limit: int | None = None) -> int | None:
    """`i` must point at `open_ch`.

    Returns the index just past the matching `close_ch`, or None when unbalanced.
    """
    n = len(text) if limit is None else min(limit, len(text))
    depth = 0
    j = i
    while j < n:
        c = text[j]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return None


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clean_params(raw: str) -> str:
    """Normalize a parameter list; drop dangling operators left by blanked
    default values (e.g. `a = "str"` becomes `a =` -> `a`)."""
    p = _norm_ws(raw)
    p = re.sub(r"=\s*(?=,|$)", "", p)
    p = re.sub(r"[=|?,]+$", "", p)
    return p.strip()


def _blank_region(out: list, a: int, b: int) -> None:
    for k in range(a, b):
        if out[k] != "\n":
            out[k] = " "


# ---------------------------------------------------------------------------
# Comment / literal blanking passes (one per language family)
# ---------------------------------------------------------------------------

def _js_blank(content: str) -> str:
    """Blank JS/TS comments and string/template/regex literals.

    Returns a string of the SAME LENGTH with those regions turned into spaces
    (newlines preserved), so structural characters can be counted safely.
    """
    out = list(content)
    n = len(content)
    i = 0
    last: tuple[str, ...] | None = None  # ('word', name) | ('punct', ch) | ('lit',)

    while i < n:
        c = content[i]
        if c == "/" and i + 1 < n and content[i + 1] == "/":
            j = content.find("\n", i)
            j = n if j == -1 else j
            _blank_region(out, i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and content[i + 1] == "*":
            j = content.find("*/", i + 2)
            j = n if j == -1 else j + 2
            _blank_region(out, i, j)
            i = j
            continue
        if c in "'\"":
            q = c
            j = i + 1
            while j < n:
                cj = content[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == "\n" or cj == q:
                    break
                j += 1
            j = min(j + 1, n)
            _blank_region(out, i, j)
            last = ("lit",)
            i = j
            continue
        if c == "`":
            j = _js_skip_template(content, i)
            _blank_region(out, i, j)
            last = ("lit",)
            i = j
            continue
        if c == "/":
            if _js_regex_allowed(last):
                end = _js_skip_regex(content, i)
                if end is not None:
                    _blank_region(out, i, end)
                    last = ("lit",)
                    i = end
                    continue
            last = ("punct", "/")
            i += 1
            continue
        if c.isdigit():
            last = ("lit",)
            i = _js_skip_number(content, i)
            continue
        if c.isalpha() or c in "_$":
            j = i
            while j < n and _is_ident_char(content[j]):
                j += 1
            last = ("word", content[i:j])
            i = j
            continue
        if not c.isspace():
            last = ("punct", c)
        i += 1
    return "".join(out)


def _js_skip_template(s: str, i: int) -> int:
    """`i` points at an opening backtick; return index past the closing one."""
    n = len(s)
    depth = 0  # open ${ interpolations
    j = i + 1
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "$" and j + 1 < n and s[j + 1] == "{":
            depth += 1
            j += 2
            continue
        if c == "}" and depth:
            depth -= 1
            j += 1
            continue
        if c == "`":
            if depth == 0:
                return j + 1
            j = _js_skip_template(s, j)  # nested template inside ${...}
            continue
        j += 1
    return n


_REGEX_BEFORE_PUNCT = set("(,=:[!&|?{};+-*/%<>~^")
_REGEX_BEFORE_WORDS = {
    "return", "case", "do", "else", "in", "of", "instanceof", "new",
    "typeof", "void", "delete", "throw", "yield", "await",
}


def _js_regex_allowed(last) -> bool:
    """Heuristic: may `/` at this position start a regex literal?"""
    if last is None:
        return True
    if last[0] == "punct":
        return last[1] in _REGEX_BEFORE_PUNCT
    if last[0] == "word":
        return last[1] in _REGEX_BEFORE_WORDS
    return False


def _js_skip_regex(s: str, i: int) -> int | None:
    """`i` points at a `/` that may open a regex literal.
    Returns index past the closing `/` + flags, or None (it was division)."""
    n = len(s)
    j = i + 1
    in_class = False
    while j < n:
        c = s[j]
        if c == "\n":
            return None  # regex literals cannot span lines
        if c == "\\":
            j += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
        elif c == "/":
            j += 1
            while j < n and (s[j].isalnum() or s[j] in "gimsuydv"):
                j += 1
            return j
        j += 1
    return None


_HEXDIGITS = frozenset("0123456789abcdefABCDEF")


def _js_skip_number(s: str, i: int) -> int:
    n = len(s)
    j = i
    if s.startswith(("0x", "0X"), i):
        j = i + 2
        while j < n and s[j] in _HEXDIGITS:
            j += 1
        return j
    while j < n and (s[j].isdigit() or s[j] == "_"):
        j += 1
    if j < n and s[j] == ".":
        j += 1
        while j < n and (s[j].isdigit() or s[j] == "_"):
            j += 1
    if j < n and s[j] in "eE":
        j += 1
        if j < n and s[j] in "+-":
            j += 1
        while j < n and s[j].isdigit():
            j += 1
    return j


def _py_blank(content: str) -> str:
    """Blank Python comments and string literals (single + triple quoted)."""
    out = list(content)
    n = len(content)
    i = 0
    while i < n:
        c = content[i]
        if c in "\"'":
            if content.startswith(c * 3, i):
                q = c * 3
                j = content.find(q, i + 3)
                j = n if j == -1 else j + 3
                _blank_region(out, i, j)
                i = j
                continue
            j = i + 1
            while j < n:
                cj = content[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == "\n" or cj == c:
                    break
                j += 1
            j = min(j + 1, n)
            _blank_region(out, i, j)
            i = j
            continue
        if c == "#":
            j = content.find("\n", i)
            j = n if j == -1 else j
            _blank_region(out, i, j)
            i = j
            continue
        i += 1
    return "".join(out)


def _c_blank(content: str) -> str:
    """Blank C-like (C/C++/Java/C#/PHP/GLSL) comments and string/char literals."""
    out = list(content)
    n = len(content)
    i = 0
    while i < n:
        c = content[i]
        if c == "/" and i + 1 < n and content[i + 1] == "/":
            j = content.find("\n", i)
            j = n if j == -1 else j
            _blank_region(out, i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and content[i + 1] == "*":
            j = content.find("*/", i + 2)
            j = n if j == -1 else j + 2
            _blank_region(out, i, j)
            i = j
            continue
        if c in "'\"":
            j = i + 1
            while j < n:
                cj = content[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == c or cj == "\n":
                    break
                j += 1
            j = min(j + 1, n)
            _blank_region(out, i, j)
            i = j
            continue
        i += 1
    return "".join(out)


def _rs_blank(content: str) -> str:
    """Blank Rust comments (nested block comments!) and literals.

    Handles: // and /// and //!, /* */ (nested), "str" with escapes,
    b"byte str", raw strings r".." / r#".."# / r###".."### (and br variants),
    char literals 'a' / '\\n' (but NOT lifetimes like 'static).
    """
    out = list(content)
    n = len(content)
    i = 0
    while i < n:
        c = content[i]
        if c == "/" and i + 1 < n and content[i + 1] == "/":
            j = content.find("\n", i)
            j = n if j == -1 else j
            _blank_region(out, i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and content[i + 1] == "*":
            depth = 1
            j = i + 2
            while j < n and depth:
                if content.startswith("/*", j):
                    depth += 1
                    j += 2
                elif content.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            _blank_region(out, i, j)
            i = j
            continue
        if c in "rb" and i + 1 < n and content[i + 1] == "r" and i + 2 < n and content[i + 2] in '"#':
            k = i + 2
            hashes = 0
            while k < n and content[k] == "#":
                hashes += 1
                k += 1
            if k < n and content[k] == '"':
                end_idx = content.find('"' + "#" * hashes, k + 1)
                j = n if end_idx == -1 else end_idx + 1 + hashes
                _blank_region(out, i, j)
                i = j
                continue
        if c in "\"b" and (c == '"' or (i + 1 < n and content[i + 1] == '"')):
            start = i + (1 if c == "b" else 0)
            j = start + 1
            while j < n:
                cj = content[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == '"':
                    j += 1
                    break
                j += 1
            _blank_region(out, i, j)
            i = j
            continue
        if c == "'":
            # char literal 'x' / '\x' vs lifetime 'static
            j = i + 1
            close: int | None = None
            if j < n and content[j] == "\\":
                if j + 2 < n and content[j + 2] == "'":
                    close = j + 3
            elif j + 1 < n and content[j + 1] == "'" and content[j] != "\\":
                close = j + 2
            if close is not None:
                _blank_region(out, i, close)
                i = close
                continue
            # lifetime 'static - skip the identifier
            j = i + 1
            while j < n and _is_ident_char(content[j]):
                j += 1
            i = j
            continue
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# TS/JS type-expression helpers (scan on the BLANKED text, slice from source)
# ---------------------------------------------------------------------------

_PAIR = {"(": ")", "[": "]", "{": "}", "<": ">"}
_TYPE_STOP = set(";,|&=")


def _parse_type_atom(src: str, text: str, i: int, n: int) -> tuple[str | None, int]:
    i = _skip_ws(text, i, n)
    if i >= n:
        return None, i
    c = text[i]
    if c in "([{<":
        close = _match_paired(text, i, c, _PAIR[c], n)
        if close is None:
            close = n
        j = _skip_ws(text, close, n)
        if c == "(" and text[j:j + 2] == "=>":
            # function type: (args) => ReturnType
            rest, k = _parse_type_union(src, text, j + 2, n)
            if rest:
                return _norm_ws(src[i:k]), k
        return _norm_ws(src[i:close]), close
    # bare atom: identifier / number / this, incl. `Map<string, number>`,
    # `string[]`, conditional types. Stops at depth-0 separators.
    depth = 0
    j = i
    while j < n:
        cj = text[j]
        if cj in "([{<":
            depth += 1
            j += 1
            continue
        if cj in ")]}>":
            if depth == 0:
                break
            depth -= 1
            j += 1
            continue
        if depth == 0 and cj in _TYPE_STOP:
            break
        j += 1
    if j == i:
        return None, i
    return _norm_ws(src[i:j]), j


def _parse_type_union(src: str, text: str, i: int, limit: int) -> tuple[str, int]:
    """Parse a (possibly union/intersection) TS type starting at i."""
    n = min(limit, len(text))
    pieces: list[tuple[str, str]] = []
    sep = ""
    while True:
        i = _skip_ws(text, i, n)
        if i < n and text[i] in "|&":
            sep = text[i]
            i += 1
            continue
        atom, i = _parse_type_atom(src, text, i, n)
        if atom is None:
            break
        pieces.append((sep, atom))
        sep = ""
    if not pieces:
        return "", i
    out = pieces[0][1]
    for s, atom in pieces[1:]:
        out += f" {s} {atom}"
    return out, i


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class SignatureExtractor(ABC):
    """Base class for language-specific signature extraction."""

    @abstractmethod
    def strip_comments(self, content: str) -> str:
        """Blank out comments (and string literals) - offsets are preserved."""
        ...

    @abstractmethod
    def clean_signature(self, sig: str) -> str:
        """Normalize the extracted signature string."""
        ...

    def get_patterns(self) -> list[str]:
        """Return regex patterns for signatures (legacy pipeline). Override in subclasses."""
        return []

    def extract(self, content: str) -> tuple[str, int]:
        """Legacy regex pipeline for extractors that do not override extract()."""
        cleaned_content = self.strip_comments(content)
        patterns = self.get_patterns()

        if not patterns:
            return ("", 0)

        combined_pattern = "|".join(patterns)
        pattern = re.compile(combined_pattern, re.MULTILINE | re.DOTALL)

        clean_lines = []
        for match in pattern.finditer(cleaned_content):
            sig = match.group(0).strip()
            cleaned_sig = self.clean_signature(sig).rstrip('{').strip()
            if cleaned_sig:
                clean_lines.append(cleaned_sig)

        return ("\n".join(line for line in clean_lines if line.strip()), len(clean_lines))


# === LANGUAGE IMPLEMENTATIONS ===

class PythonExtractor(SignatureExtractor):
    _DECL_RE = re.compile(
        r"(?<![\w.])(?P<async>async\s+)?"
        r"(?P<kind>def|class)\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    )

    def strip_comments(self, content: str) -> str:
        return _py_blank(content)

    def get_patterns(self) -> list[str]:
        return [
            r"\b(?:async\s+)?def\s+\w+\s*\([^)]*\)",
            r"\bclass\s+\w+(?:\s*\([^)]*\))?",
        ]

    def clean_signature(self, sig: str) -> str:
        sig = re.sub(r"\s+", " ", sig).strip()
        return sig.rstrip(":").strip()

    def extract(self, content: str) -> tuple[str, int]:
        text = _py_blank(content)
        src = content
        sigs: list[str] = []
        for m in self._DECL_RE.finditer(text):
            sig = self._parse(src, text, m)
            if sig:
                sigs.append(sig)
        return ("\n".join(sigs), len(sigs))

    def _parse(self, src: str, text: str, m) -> str:
        kind, name = m.group("kind"), m.group("name")
        prefix = "async " if m.group("async") else ""
        i = m.end()
        n = len(text)
        if kind == "def":
            i = _skip_ws(text, i)
            if i >= n or text[i] != "(":
                return ""
            close = _match_paired(text, i, "(", ")")
            if close is None:
                return ""
            params = _clean_params(src[i + 1:close - 1])
            # optional return annotation: scan to ':' at depth 0
            depth = 0
            j = close
            while j < n:
                c = text[j]
                if c in "([{":
                    depth += 1
                elif c in ")]}":
                    depth -= 1
                elif c == ":" and depth == 0:
                    break
                j += 1
            ann = _norm_ws(src[close:j])
            ann = re.sub(r"^->\s*", "", ann)
            sig = f"{prefix}def {name}({params})"
            if ann:
                sig += f" -> {ann}"
            return sig
        # class
        i = _skip_ws(text, i)
        generics = ""
        if i < n and text[i] == "[":  # PEP 695 generics
            close = _match_paired(text, i, "[", "]")
            if close is not None:
                generics = _norm_ws(src[i:close])
                i = close
                i = _skip_ws(text, i)
        bases = ""
        if i < n and text[i] == "(":
            close = _match_paired(text, i, "(", ")")
            if close is not None:
                bases = _clean_params(src[i + 1:close - 1])
        sig = f"class {name}{generics}"
        if bases:
            sig += f"({bases})"
        return sig


class JSLikeExtractor(SignatureExtractor):
    """Structural scanner for JS/TS/JSX/TSX.

    The previous implementation accumulated whole lines until its raw
    brace/paren counters "balanced" (strings and template literals broke the
    counts), then stripped brace groups with a regex - so body code leaked
    into signatures (leftovers like `) else if (...)` or `ctx.fillRect(...)}`).

    Pipeline:
      1. `_js_blank` blanks comments and string/template/regex literals
         (same length, offsets preserved), so braces/parens count safely.
      2. declaration starts are found: function / class / interface / type /
         enum / namespace / const|let|var arrow functions, with the modifiers
         export / default / declare / abstract / async / readonly / const.
      3. each declaration is parsed structurally: parameter lists are matched
         with a balanced-paren scan, TS type annotations are parsed, and only
         the header is emitted. Bodies are skipped with balanced-block scans.
         Class bodies are scanned for arrow-function fields
         (`handler = (e) => ...`, incl. TS-typed fields) and then skipped.

    Declarations at brace depth <= 1 are kept (top level + direct class
    members); anything deeper is local code and is dropped.
    """

    _DECL_RE = re.compile(
        r"(?<![\w$])"
        r"(?P<mods>(?:(?:export|default|declare|abstract|async|static|readonly|const)\s+)*)"
        r"\b(?:"
        r"(?P<kind>function)(?:\*\s*|\s+)(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
        r"|"
        r"(?P<kind2>class|interface|type|enum|namespace|const|let|var)\s+(?P<name2>[A-Za-z_$][A-Za-z0-9_$]*)"
        r")"
    )
    _FIELD_CAND_RE = re.compile(
        r"(?P<fword>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?\("
    )
    # words allowed between a class body's '{' (or previous member) and a field
    _FIELD_MODS = frozenset({
        "static", "get", "set", "public", "private", "protected", "readonly",
        "declare", "abstract", "final", "override",
    })

    def strip_comments(self, content: str) -> str:
        return _js_blank(content)

    def get_patterns(self) -> list[str]:
        return [
            r"\bfunction\s+\w+\s*\([^)]*\)",
            r"\bconst\s+\w+\s*=\s*(?:async\s+)?\([^)]*\)\s*=>",
            r"\bclass\s+\w+",
        ]

    def clean_signature(self, sig: str) -> str:
        sig = re.sub(r"\b(export|declare)\b", "", sig)
        sig = _norm_ws(sig)
        return sig.rstrip(";{").strip()

    def extract(self, content: str) -> tuple[str, int]:
        text = _js_blank(content)
        src = content
        n = len(text)
        decl_starts = [m.start() for m in self._DECL_RE.finditer(text)]
        sigs: list[str] = []
        pos = 0
        base_depth = 0  # brace depth at 'pos'
        while pos < n:
            m = self._DECL_RE.search(text, pos)
            if m is None:
                break
            depth = base_depth + text[pos:m.start()].count("{") - text[pos:m.start()].count("}")
            parsed = self._parse(src, text, decl_starts, m)
            if parsed is None:
                new_pos = m.end()
            else:
                decl_sigs, end = parsed
                if depth <= 1:
                    sigs.extend(s for s in decl_sigs if s)
                new_pos = max(end, m.end())
            base_depth += text[pos:new_pos].count("{") - text[pos:new_pos].count("}")
            pos = new_pos
        return ("\n".join(sigs), len(sigs))

    # -- declaration parsers (each returns ([sig, ...], end_pos) or None) ----

    def _parse(self, src, text, decl_starts, m):
        kind = m.group("kind") or m.group("kind2")
        name = m.group("name") or m.group("name2")
        i = m.end()
        if kind == "function":
            r = self._parse_function(src, text, i, name, m)
        elif kind in ("class", "interface", "enum", "namespace"):
            r = self._parse_block_header(src, text, i, m, kind)
        elif kind == "type":
            r = self._parse_type_alias(src, text, decl_starts, i, name)
        else:
            r = self._parse_arrow(src, text, decl_starts, i, name, kind)
        if r is None:
            return None
        sig, end = r
        if isinstance(sig, list):  # class header + its fields
            return sig, end
        return [sig], end

    @staticmethod
    def _mods_prefix(m, keep: set) -> str:
        tokens = (m.group("mods") or "").split()
        prefix = " ".join(w for w in tokens if w in keep)
        return prefix + " " if prefix else ""

    def _parse_function(self, src, text, i, name, m):
        n = len(text)
        prefix = self._mods_prefix(m, {"async"})
        i = _skip_ws(text, i)
        if i < n and text[i] == "<":  # TS type parameters
            close = _match_paired(text, i, "<", ">")
            if close is None:
                return None
            i = _skip_ws(text, close)
        if i >= n or text[i] != "(":
            return None
        close = _match_paired(text, i, "(", ")")
        if close is None:
            return None
        params = _clean_params(src[i + 1:close - 1])
        i = _skip_ws(text, close)
        ret = ""
        if i < n and text[i] == ":":
            # limit the type parse to the body start so the body is never
            # swallowed (object-type return annotations are handled because
            # _find_body_start skips balanced {...} groups)
            body = _find_body_start(text, i + 1)
            ret, _ = _parse_type_union(src, text, i + 1, body if body is not None else n)
            i = _skip_ws(text, body) if body is not None else n
        sig = f"{prefix}function {name}({params})"
        if ret:
            sig += f": {ret}"
        if i < n and text[i] == ";":  # ambient declaration
            return sig, i + 1
        if i < n and text[i] == "{":
            close = _match_paired(text, i, "{", "}")
            return sig, (close if close is not None else n)
        return sig, i

    def _parse_block_header(self, src, text, i, m, kind):
        """class / interface / enum / namespace: header up to the body '{' or ';'."""
        n = len(text)
        depth = 0
        j = i
        stop = None
        while j < n:
            c = text[j]
            if c in "([<":
                depth += 1
            elif c in ")]>":
                depth -= 1
            elif c in "{;" and depth == 0:
                stop = j
                break
            j += 1
        if stop is None:
            return None
        header = _norm_ws(src[m.start():stop])
        header = re.sub(r"^(?:export|declare)\s+", "", header)
        if not header:
            return None
        if kind == "class" and text[stop] == "{":
            close = _match_paired(text, stop, "{", "}")
            if close is not None:
                fields = self._scan_class_fields(src, text, stop, close)
                return [header] + fields, close
            return header, n
        return header, stop

    def _parse_type_alias(self, src, text, decl_starts, i, name):
        n = len(text)
        i = _skip_ws(text, i)
        if i >= n or text[i] != "=":
            return None
        # the type expression ends at a depth-0 ';' or the next declaration;
        # slice the ORIGINAL source so literal unions ('up' | 'down') survive
        # blanking (they are pure whitespace in the scanned text)
        limit = self._next_decl(decl_starts, i, n)
        stop = limit
        depth = 0
        j = i + 1
        while j < limit:
            c = text[j]
            if c in "([<>{}":
                depth += 1
            elif c in ")]>}":
                depth -= 1
            elif c == ";" and depth == 0:
                stop = j
                break
            j += 1
        raw = _norm_ws(src[i + 1:stop])
        raw = re.sub(r"^[|&]\s*", "", raw)
        raw = raw.rstrip(";").strip()
        if not raw:
            return None
        end = stop + 1 if stop < n and text[stop] == ";" else stop
        return f"type {name} = {raw}", end

    def _parse_arrow(self, src, text, decl_starts, i, name, kind):
        n = len(text)
        limit = self._next_decl(decl_starts, i, n)
        i = _skip_ws(text, i)
        if i < n and text[i] == ":":  # TS annotation: const f: (x: T) => U = ...
            _, i = _parse_type_union(src, text, i + 1, limit)
            i = _skip_ws(text, i)
        if i >= n or text[i] != "=":
            return None
        if i + 1 < n and text[i + 1] == "=":  # '==' / '==='
            return None
        i = _skip_ws(text, i + 1)
        async_ = False
        if text.startswith("async", i) and (i + 5 >= n or not _is_ident_char(text[i + 5])):
            i = _skip_ws(text, i + 5)
            async_ = True
        if i < n and text[i] == "(":
            close = _match_paired(text, i, "(", ")")
            if close is None:
                return None
            params = _clean_params(src[i + 1:close - 1])
            j = _skip_ws(text, close, limit)
            ret = ""
            if j < n and text[j] == ":":
                ret, j = _parse_type_union(src, text, j + 1, limit)
                j = _skip_ws(text, j)
            if text[j:j + 2] != "=>":
                return None
            sig = f"{kind} {name} = "
            if async_:
                sig += "async "
            sig += f"({params})"
            if ret:
                sig += f": {ret}"
            sig += " =>"
            return sig, _skip_block_or_expr(text, j + 2)
        if i < n and _is_ident_start(text[i]):
            j = i
            while j < n and _is_ident_char(text[j]):
                j += 1
            k = _skip_ws(text, j)
            if text[k:k + 2] != "=>":
                return None
            sig = f"{kind} {name} = "
            if async_:
                sig += "async "
            sig += f"{src[i:j]} =>"
            return sig, _skip_block_or_expr(text, k + 2)
        return None

    # -- class fields ---------------------------------------------------------

    def _scan_class_fields(self, src, text, body_open, body_close) -> list[str]:
        """Arrow-function fields at class-body level: `name[: T] = (params) => ...`"""
        sigs: list[str] = []
        hi = body_close
        pos = body_open + 1
        while pos < hi:
            m = self._FIELD_CAND_RE.search(text, pos, hi)
            if m is None:
                break
            depth_at = text[body_open + 1:m.start()].count("{") - text[body_open + 1:m.start()].count("}")
            if depth_at != 0:  # inside a method body / nested block - not a field
                pos = m.end()
                continue
            name = self._field_name_at(text, m.start(), m.group("fword"))
            if name is None:
                pos = m.end()
                continue
            open_paren = m.end() - 1
            close = _match_paired(text, open_paren, "(", ")", hi)
            if close is None:
                pos = m.end()
                continue
            j = _skip_ws(text, close, hi)
            ret = ""
            if j < hi and text[j] == ":":
                ret, j = _parse_type_union(src, text, j + 1, hi)
                j = _skip_ws(text, j)
            if text[j:j + 2] != "=>":
                pos = m.end()
                continue
            params = _clean_params(src[open_paren + 1:close - 1])
            sig = f"{name} = ({params})"
            if ret:
                sig += f": {ret}"
            sig += " =>"
            sigs.append(sig)
            pos = min(_skip_block_or_expr(text, j + 2), hi)
        return sigs

    def _field_name_at(self, text: str, cand_start: int, cand_word: str) -> str | None:
        """Resolve the field name for a candidate `word = (` at cand_start.

        The candidate may be the last word of a TS type annotation
        (`handler: (e: Event) => void = (e) => ...`); walking back to the
        nearest boundary resolves the real member name.
        """
        k = cand_start - 1
        rdepth = 0
        while k >= 0:
            c = text[k]
            if c == ")":
                rdepth += 1
            elif c == "(":
                rdepth -= 1
            elif rdepth == 0 and c in "{};:":
                break
            k -= 1
        if k < 0:
            return None
        if text[k] == ":":
            ann = text[k + 1:cand_start]
            # single-line annotation only; a newline means the ':' belongs to
            # the PREVIOUS member
            return self._member_name_before(text, k) if "\n" not in ann else None
        return cand_word if self._seg_is_mods_only(_norm_ws(text[k + 1:cand_start])) else None

    def _member_name_before(self, text: str, colon_idx: int) -> str | None:
        k = colon_idx - 1
        while k >= 0 and text[k].isspace():
            k -= 1
        e = k
        while k >= 0 and _is_ident_char(text[k]):
            k -= 1
        k += 1
        if k > e or not _is_ident_start(text[k]):
            return None
        name = text[k:e + 1]
        p = k - 1
        rdepth = 0
        while p >= 0:
            c = text[p]
            if c == ")":
                rdepth += 1
            elif c == "(":
                rdepth -= 1
            elif rdepth == 0 and c in "{};":
                break
            elif rdepth == 0 and c == ":":
                return None  # another annotation - not a member start
            p -= 1
        return name if self._seg_is_mods_only(_norm_ws(text[p + 1:k])) else None

    @classmethod
    def _seg_is_mods_only(cls, seg: str) -> bool:
        """Only modifier words / decorators may sit before a field name."""
        seg = re.sub(r"\([^()]*\)", "", seg)  # drop decorator call args
        words = seg.split()
        if not words:
            return True
        return all(w in cls._FIELD_MODS or w.startswith("@") for w in words)

    @staticmethod
    def _next_decl(decl_starts, i, default):
        for s in decl_starts:
            if s > i:
                return s
        return default


def _find_body_start(text: str, i: int) -> int | None:
    """Index of the body '{' / terminator ';' of a function declaration.

    Scans from `i` (just past the ':' of a return-type annotation). A balanced
    depth-0 `{...}` group is an object-type annotation, not the body - the
    body follows it (or a ';' terminates an ambient declaration).
    """
    n = len(text)
    depth = 0
    j = i
    while j < n:
        c = text[j]
        if c == "-" and j + 1 < n and text[j + 1] == ">":
            j += 2
            continue
        if c in "([<":
            depth += 1
        elif c in ")]>":
            depth -= 1
        elif c == "{" and depth == 0:
            close = _match_paired(text, j, "{", "}")
            if close is None:
                return j
            k = _skip_ws(text, close)
            if k < n and text[k] in "{;":
                j = k  # balanced group was the object-type annotation
                continue
            return j  # this '{' opens the body
        elif c == ";" and depth == 0:
            return j
        j += 1
    return None


def _parse_ret_type(src: str, text: str, i: int, limit: int | None = None) -> tuple[str, int]:
    """Parse an optional `: Type` (TS) / `-> Type` (WGSL) after a parameter list.

    `i` is the index just past the matching ')' of the parameter list.
    Returns (type_text, index_after_type).
    """
    n = len(text) if limit is None else min(limit, len(text))
    i = _skip_ws(text, i, n)
    if i < n and text[i] == ":":
        raw, j = _parse_type_union(src, text, i + 1, n)
        return raw, j
    if i + 1 < n and text[i] == "-" and text[i + 1] == ">":
        raw, j = _parse_type_union(src, text, i + 2, n)
        return raw, j
    return "", i


def _skip_block_or_expr(text: str, i: int) -> int:
    """Skip a `{...}` block, or an expression body up to depth-0 ';' / newline."""
    n = len(text)
    i = _skip_ws(text, i)
    if i >= n:
        return n
    if text[i] == "{":
        close = _match_paired(text, i, "{", "}")
        return close if close is not None else n
    depth = 0
    while i < n:
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return i  # enclosing block's '}' - not ours
            depth -= 1
        elif c == ";" and depth == 0:
            return i + 1
        elif c == "\n" and depth == 0:
            return i
        i += 1
    return n


class ShaderExtractor(SignatureExtractor):
    _STRUCT_RE = re.compile(r"(?<![\w])(?:struct)\s+([A-Za-z_]\w*)")
    _FN_RE = re.compile(r"(?<![\w])(?:fn)\s+([A-Za-z_]\w*)")
    _GLSL_FN_RE = re.compile(
        r"(?<![\w])"
        r"(?:void|vec[234]|mat[234]|bvec[234]|float|double|int|uint|bool|"
        r"sampler[123]D|isampler[123]D|usampler[123]D|highp|mediump|lowp)\s+"
        r"([A-Za-z_]\w*)\s*\("
    )
    _VAR_RE = re.compile(r"(?<![\w])(?:uniform|varying|attribute|var|out|inout)\b[^;{\n]*")

    def strip_comments(self, content: str) -> str:
        return _c_blank(content)

    def get_patterns(self) -> list[str]:
        return [
            r"\bstruct\s+\w+",
            r"\bfn\s+\w+\s*\([^)]*\)(?:\s*->\s*[\w<>]+)?",
            r"\b(?:void|vec[234]|mat[234]|float|int)\s+\w+\s*\([^)]*\)",
            r"\b(?:uniform|var)\s+[^;{\n]+",
        ]

    def clean_signature(self, sig: str) -> str:
        sig = re.sub(r"^(?:attribute|uniform|varying)\s+", "", sig)
        sig = _norm_ws(sig)
        return sig.rstrip(";").strip()

    def extract(self, content: str) -> tuple[str, int]:
        text = _c_blank(content)
        src = content
        candidates = []
        for m in self._STRUCT_RE.finditer(text):
            candidates.append((m.start(), "struct", m.group(1), m.end()))
        for m in self._FN_RE.finditer(text):
            candidates.append((m.start(), "fn", m.group(1), m.end()))
        for m in self._GLSL_FN_RE.finditer(text):
            candidates.append((m.start(), "glslfn", m.group(1), m.end() - 1))
        for m in self._VAR_RE.finditer(text):
            candidates.append((m.start(), "var", None, m.end()))
        candidates.sort(key=lambda t: t[0])
        sigs: list[str] = []
        for start, kind, name, after in candidates:
            sig = self._parse_candidate(src, text, start, after, kind, name)
            if sig:
                sigs.append(sig)
        return ("\n".join(sigs), len(sigs))

    def _parse_candidate(self, src, text, start, i, kind, name) -> str:
        n = len(text)
        if kind == "struct":
            depth = 0
            j = i
            stop = None
            while j < n:
                c = text[j]
                if depth == 0 and c in "{;\n":
                    stop = j
                    break
                if c in "([{":
                    depth += 1
                elif c in ")]}":
                    depth -= 1
                j += 1
            if stop is None:
                stop = n
            return _norm_ws(src[start:stop])
        if kind in ("fn", "glslfn"):
            i = _skip_ws(text, i)
            if i >= n or text[i] != "(":
                return ""
            close = _match_paired(text, i, "(", ")")
            if close is None:
                return ""
            params = _clean_params(src[i + 1:close - 1])
            body = _find_body_start(text, close)
            ret, _ = _parse_ret_type(src, text, close, body if body is not None else n)
            sig = f"fn {name}({params})" if kind == "fn" else f"{name}({params})"
            if ret:
                sig += f" -> {ret}"
            return sig
        # variable: up to ';' or end of line
        j = i
        while j < n and text[j] not in ";{\n":
            j += 1
        return self.clean_signature(src[start:j])


class CLikeExtractor(SignatureExtractor):
    """Handles Java, C++, C#, PHP, etc."""

    _CONTROL = frozenset({
        "if", "for", "while", "switch", "catch", "return", "sizeof",
        "new", "delete", "else", "do", "case", "break", "continue",
        "throw", "using", "typeof", "await", "yield",
    })
    _MODIFIERS = (
        r"public|private|protected|internal|static|final|virtual|override|"
        r"sealed|abstract|partial|inline"
    )

    # a type word: identifier + optional one-level-nested generics + ptr/ref
    _TW = r"[A-Za-z_]\w*(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>)?(?:[*&])?"
    _DECL_RE = re.compile(
        r"(?<![\w:])"
        r"(?:"
        r"(?P<kind>class|struct|enum|union|interface|typedef|namespace)\s+(?P<kind_name>[A-Za-z_]\w*)"
        r"|"
        + rf"(?P<type>{_TW}(?:[ \t:]+{_TW})*)(?:[ \t:]+)(?P<name>[A-Za-z_]\w*)\s*\("
        r"|"
        + rf"(?P<qtype>{_TW}(?:[ \t:]+{_TW})*)::\s*(?P<qname>[A-Za-z_]\w*)\s*\("
        r")"
    )

    def strip_comments(self, content: str) -> str:
        return _c_blank(content)

    def get_patterns(self) -> list[str]:
        return [
            r"\bclass\s+\w+",
            r"\b(?:public|private|protected|static|final)\s+.*?\s+\w+\s*\([^)]*\)\s*\{?",
            r"\b(?:void|int|float|double|bool|char|long|byte)\s+\w+\s*\([^)]*\)\s*\{?",
        ]

    def clean_signature(self, sig: str) -> str:
        sig = re.sub(rf"\b(?:{self._MODIFIERS})\b", "", sig)
        sig = _norm_ws(sig)
        return sig.replace(";", "").rstrip("{").strip()

    def extract(self, content: str) -> tuple[str, int]:
        text = _c_blank(content)
        src = content
        n = len(text)
        sigs: list[str] = []
        pos = 0
        while pos < n:
            m = self._DECL_RE.search(text, pos)
            if m is None:
                break
            parsed = self._parse(src, text, m)
            if parsed is None:
                pos = m.end()
                continue
            sig, end = parsed
            if sig:
                sigs.append(sig)
            pos = max(end, m.end())
        return ("\n".join(sigs), len(sigs))

    def _parse(self, src, text, m):
        n = len(text)
        # preprocessor line (#include / #define ...)
        line_start = text.rfind("\n", 0, m.start()) + 1
        if text[line_start:m.start()].strip().startswith("#"):
            return None

        if m.group("qtype") is not None:  # C++ qualified definition: `int C::f(...)`
            typ = m.group("qtype")
            name = m.group("qname")
            if name in self._CONTROL:
                return None
            last_type_word = re.findall(r"[A-Za-z_]\w*", typ)[-1] if typ else ""
            if last_type_word in self._CONTROL:
                return None
            open_paren = m.end() - 1
            close = _match_paired(text, open_paren, "(", ")")
            if close is None:
                return None
            params_raw = src[open_paren + 1:close - 1]
            params = _clean_params(params_raw)
            if params and not self._has_typed_arg(params_raw):
                return None
            end = self._skip_after_params(text, close)
            if end is None:
                return None
            typ_clean = re.sub(rf"^(?:{self._MODIFIERS})(?:\s+|$)", "", _norm_ws(typ))
            sig = _norm_ws(f"{typ_clean}::{name}({params})")
            return sig, end

        if m.group("kind") is not None:
            name = m.group("kind_name")
            j = _skip_ws(text, m.end())
            # `struct Point p;` is a variable declaration, not a type
            if (j < n and _is_ident_start(text[j])
                    and not text[j:j + 8].startswith(("extends", "implements"))):
                return None
            stop = self._find_header_stop(text, j)
            if stop is None:
                return None
            header = _norm_ws(src[m.start():stop])
            if not header:
                return None
            return header, stop

        typ = m.group("type")
        name = m.group("name")
        if name in self._CONTROL:
            return None
        last_type_word = re.findall(r"[A-Za-z_]\w*", typ)[-1] if typ else ""
        if last_type_word in self._CONTROL:
            return None
        open_paren = m.end() - 1
        close = _match_paired(text, open_paren, "(", ")")
        if close is None:
            return None
        params_raw = src[open_paren + 1:close - 1]
        params = _clean_params(params_raw)
        # a call / constructor initializer like `Point p(1, 2);` has no
        # `type name` parameter shape in its argument list
        if params and not self._has_typed_arg(params_raw):
            return None
        end = self._skip_after_params(text, close)
        if end is None:
            return None
        typ_clean = re.sub(r"^template\s*<.*?>\s*", "", _norm_ws(typ), flags=re.DOTALL)
        for _ in range(4):
            new = re.sub(rf"^(?:{self._MODIFIERS})(?:\s+|$)", "", typ_clean)
            if new == typ_clean:
                break
            typ_clean = new
        joiner = "::" if text[m.end("type"):m.start("name")].strip() == "::" else " "
        sig = _norm_ws(f"{typ_clean}{joiner}{name}({params})")
        return sig, end

    @classmethod
    def _has_typed_arg(cls, params_raw: str) -> bool:
        """True when the parameter list contains at least one `type name`-shaped
        parameter (i.e. this is a declaration, not a call / initializer)."""
        depth = 0
        cur: list[str] = []
        args: list[str] = []
        for c in params_raw:
            if c in "([{<":
                depth += 1
            elif c in ")]}>":
                depth -= 1
            if c == "," and depth == 0:
                args.append("".join(cur))
                cur = []
            else:
                cur.append(c)
        args.append("".join(cur))
        return any(cls._arg_is_typed(a) for a in args)

    @staticmethod
    def _arg_is_typed(arg: str) -> bool:
        a = arg.strip()
        if not a:
            return False
        a = re.sub(r"=\s*.*$", "", a).strip()  # drop default value
        if not a:
            return False
        # function pointer: `int (*cb)(int)`
        if re.match(r"^[A-Za-z_][\w:<>,\s]*\(\s*\*[A-Za-z_]\w*\s*\)", a):
            return True
        a = re.sub(r"\[[^\]]*\]", "", a).strip()  # drop array bounds
        if not a or any(ch in a for ch in "()+.?-"):
            return False
        if not re.fullmatch(r"[A-Za-z0-9_:<>,&* \t]+", a):
            return False
        a = re.sub(r"[*&]", " ", a)
        # count identifier words (namespaces like std::string contribute both)
        return len(re.findall(r"[A-Za-z_]\w*", a)) >= 2

    @staticmethod
    def _find_header_stop(text: str, i: int) -> int | None:
        """Index of the first depth-0 '{' or ';' at/after i (None if absent)."""
        n = len(text)
        depth = 0
        j = i
        while j < n:
            c = text[j]
            if c in "([<":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c in "{;" and depth == 0:
                return j
            j += 1
        return None

    @staticmethod
    def _skip_after_params(text: str, close: int) -> int | None:
        """Position just past a function's '{...}' body / ';' / '= default;'."""
        n = len(text)
        j = _skip_ws(text, close)
        if j < n and text[j] == "{":
            end = _match_paired(text, j, "{", "}")
            return end if end is not None else n
        if j < n and text[j] == ";":
            return j + 1
        depth = 0
        k = close
        while k < n:
            c = text[k]
            if c in "([<":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif depth == 0 and c in "{;":
                if c == "{":
                    end = _match_paired(text, k, "{", "}")
                    return end if end is not None else n
                return k + 1
            k += 1
        return None


class RustExtractor(SignatureExtractor):
    _DECL_RE = re.compile(
        r"(?<![\w!])"
        r"(?P<mods>(?:(?:pub(?:\s*\([^)]*\))?|const|unsafe|async|extern)\s+)*)"
        r"(?P<kind>fn|struct|enum|trait|impl|type|mod)\b"
    )
    _NAME_RE = re.compile(r"\s*(?P<name>[A-Za-z_]\w*)")

    def strip_comments(self, content: str) -> str:
        return _rs_blank(content)

    def get_patterns(self) -> list[str]:
        return [
            r"\b(?:pub\s+)?fn\s+\w+\s*\([^)]*\)(?:\s*->\s*[^{;]+)?",
            r"\b(?:pub\s+)?struct\s+\w+",
            r"\b(?:pub\s+)?enum\s+\w+",
            r"\b(?:pub\s+)?trait\s+\w+",
            r"\b(?:pub\s+)?impl\s+(?:\w+\s+for\s+)?\w+",
            r"\b(?:pub\s+)?type\s+\w+\s*=",
            r"\bmod\s+\w+",
        ]

    def clean_signature(self, sig: str) -> str:
        sig = _norm_ws(sig)
        sig = re.sub(r"\s*\(\s*", "(", sig)
        sig = re.sub(r"\s*\)\s*", ")", sig)
        sig = re.sub(r"\s*,\s*", ", ", sig)
        return re.sub(r"\s*\{[^}]*\}\s*", " ", sig).strip()

    def extract(self, content: str) -> tuple[str, int]:
        text = _rs_blank(content)
        src = content
        n = len(text)
        sigs: list[str] = []
        pos = 0
        while pos < n:
            m = self._DECL_RE.search(text, pos)
            if m is None:
                break
            parsed = self._parse(src, text, m)
            if parsed is None:
                pos = m.end()
                continue
            sig, end = parsed
            if sig:
                sigs.append(sig)
            pos = max(end, m.end())
        return ("\n".join(sigs), len(sigs))

    def _parse(self, src, text, m):
        n = len(text)
        kind = m.group("kind")
        prefix = (m.group("mods") or "").strip()
        prefix = prefix + " " if prefix else ""
        i = m.end()
        if kind == "fn":
            nm = self._NAME_RE.match(text, i)
            if not nm:
                return None
            i = nm.end()
            i = _skip_ws(text, i)
            if i < n and text[i] == "<":
                close = _match_paired(text, i, "<", ">")
                if close is None:
                    return None
                i = _skip_ws(text, close)
            if i >= n or text[i] != "(":
                return None
            close = _match_paired(text, i, "(", ")")
            if close is None:
                return None
            # return type + where clause: scan to depth-0 '{' or ';'
            depth = 0
            j = close
            stop = None
            while j < n:
                c = text[j]
                if c == "-" and j + 1 < n and text[j + 1] == ">":
                    j += 2
                    continue
                if c in "([<":
                    depth += 1
                elif c in ")]}>":
                    depth -= 1
                elif depth == 0 and c in "{;":
                    stop = j
                    break
                j += 1
            if stop is None:
                return None
            sig = _norm_ws(src[m.start():stop])
            end = stop + 1
            if text[stop] == "{":
                close2 = _match_paired(text, stop, "{", "}")
                if close2 is not None:
                    end = close2
            return sig, end
        if kind in ("struct", "enum"):
            nm = self._NAME_RE.match(text, i)
            if not nm:
                return None
            name = nm.group("name")
            i = nm.end()
            i = _skip_ws(text, i)
            generics = ""
            if i < n and text[i] == "<":
                close = _match_paired(text, i, "<", ">")
                if close is not None:
                    generics = _norm_ws(src[i:close])
                    i = _skip_ws(text, close)
            if i >= n:
                return None
            if text[i] == "{":
                # body is skipped: a struct body contains no items
                close2 = _match_paired(text, i, "{", "}")
                end = close2 if close2 is not None else n
            elif text[i] == "(":  # tuple struct
                close2 = _match_paired(text, i, "(", ")")
                if close2 is None:
                    return None
                j = _skip_ws(text, close2)
                end = j + 1 if j < n and text[j] == ";" else close2
            elif text[i] == ";":  # unit struct
                end = i + 1
            else:
                return None
            return f"{prefix}{kind} {name}{generics}", end
        if kind in ("trait", "impl"):
            # header only - the scanner keeps going into the body, so the
            # `fn` methods inside an impl / trait are picked up as signatures
            depth = 0
            j = i
            stop = None
            while j < n:
                c = text[j]
                if c in "([<":
                    depth += 1
                elif c in ")]}>":
                    depth -= 1
                elif depth == 0 and c == "{":
                    stop = j
                    break
                j += 1
            if stop is None:
                return None
            header = _norm_ws(src[m.start():stop])
            if not header:
                return None
            return header, stop
        if kind == "type":
            nm = re.match(r"\s*(?P<name>[A-Za-z_]\w*)(?P<gen>(?:\s*<[^;=]*>)?)\s*=", text[i:])
            if not nm:
                return None
            j = i + nm.end()
            depth = 0
            k = j
            stop = None
            while k < n:
                c = text[k]
                if c in "([{<":
                    depth += 1
                elif c in ")]}>":
                    depth -= 1
                elif depth == 0 and c == ";":
                    stop = k
                    break
                k += 1
            if stop is None:
                return None
            expr = _norm_ws(src[j:stop])
            if not expr:
                return None
            gen = (nm.group("gen") or "").strip()
            return f"{prefix}type {nm.group('name')}{gen} = {expr}", stop + 1
        if kind == "mod":
            nm = self._NAME_RE.match(text, i)
            if not nm:
                return None
            name = nm.group("name")
            i = nm.end()
            i = _skip_ws(text, i)
            if i < n and text[i] == "{":
                # header only - keep scanning so nested items surface
                end = i + 1
            elif i < n and text[i] == ";":
                end = i + 1
            else:
                return None
            return f"{prefix}mod {name}", end
        return None


# === ROUTING & DISPATCHER ===

# Lazy Loading Map: We store the CLASS, not the INSTANCE.
# This saves memory as extractors are created only when needed.
_EXTRACTOR_MAP: dict[str, type[SignatureExtractor]] = {
    "py": PythonExtractor,
    "js": JSLikeExtractor,
    "ts": JSLikeExtractor,
    "jsx": JSLikeExtractor,
    "tsx": JSLikeExtractor,
    "wgsl": ShaderExtractor,
    "glsl": ShaderExtractor,
    "vert": ShaderExtractor,
    "frag": ShaderExtractor,
    "java": CLikeExtractor,
    "cpp": CLikeExtractor,
    "c": CLikeExtractor,
    "h": CLikeExtractor,
    "cs": CLikeExtractor,
    "php": CLikeExtractor,
    "rs": RustExtractor,
}


def _extract_from_html(content: str) -> tuple[str, int]:
    """Special handler for HTML that delegates to JS logic."""
    script_blocks = re.findall(r"<script[\s\S]*?>([\s\S]*?)</script>", content)
    js_content = "\n".join(script_blocks) if script_blocks else ""
    return JSLikeExtractor().extract(js_content)


def extract_signatures(content: str, extension: str,
                       mode: Literal['A', 'B', 'DEFAULT'] | None = None) -> tuple[str, int]:
    """Main entry point. Routes to the correct extractor by file extension.

    `mode` is accepted for backward compatibility with older callers; every
    mode now uses the same structural scanner.
    """
    ext = extension.lower() if extension else ""

    # Check if the extension is in our supported settings list
    if f"*.{ext}" not in SUPPORTED_EXTENSIONS:
        return ("", 0)

    # HTML special case
    if ext in ("html", "htm"):
        return _extract_from_html(content)

    # Lazy initialization of the extractor
    extractor_class = _EXTRACTOR_MAP.get(ext)
    if extractor_class:
        return extractor_class().extract(content)

    return ("", 0)


# === UTILITY ===

def parse_html_structure(html_content: str) -> list[str]:
    """Extract element signatures from HTML (tag#id format)."""
    pattern = r'<([a-zA-Z0-9]+)\s+[^>]*id=["\']([^"\']+)["\'][^>]*>'
    return [f"{m.group(1)}#{m.group(2)}" for m in re.finditer(pattern, html_content)]
