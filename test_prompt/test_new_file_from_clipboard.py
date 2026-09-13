BENCHMARK = True
BENCHMARK_ITERATIONS = 1000

import sys

import re
import time
import unittest


class ExecutionProfiler:
    def __init__(self):
        self._start = None
        self.elapsed = None

    def start(self):
        self._start = time.perf_counter()

    def stop(self):
        if self._start is not None:
            self.elapsed = time.perf_counter() - self._start
        return self.elapsed

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


profiler = ExecutionProfiler()

TEST_CASES = [
    # (description, text, expected_valid)

    # ---- nonsense as bat ----
    ("empty line", "", False),
    ("whitespace only", "   \t", False),
    # ---- Valid BAT lines ----
    ("plain comment REM", "REM this is a comment", True),
    ("comment with colon", "REM check: something", True),
    ("double colon comment", ":: another comment line", True),
    ("echo command", "echo Hello World", True),
    ("echo with colon in middle", "echo Hello: World", True),
    ("echo old-style dot", "echo.", True),
    ("echo old-style slash", "echo/", True),
    ("echo old-style semicolon", "echo;", True),
    ("echo old-style paren", "echo(", True),
    ("echo old-style bracket", "echo[", True),
    ("echo with trailing space", "echo ", True),
    ("set command", "set VAR=value", True),
    ("set with prompt", "set /p var=Prompt", True),
    ("set empty value", "set var=", True),
    ("set with colon in value", "set x=y:z", True),
    ("if command", "if exist file.txt echo yes", True),
    ("for command", "for %%f in (*) do echo %%f", True),
    ("goto command", "goto :EOF", True),
    ("goto lowercase eof", "goto :eof", True),
    ("goto label", "goto myLabel", True),
    ("label definition", ":myLabel", True),
    ("label with hyphen", ":my-label", True),
    ("label with underscore", ":my_label", True),
    ("label with numbers", ":Label42", True),
    ("call command", "call other.bat", True),
    ("call label subroutine", "call :mySub", True),
    ("pause command", "pause", True),
    ("pause with redirect", "pause >nul", True),
    ("cls command", "cls", True),
    ("dir command", "dir", True),
    ("cd command", "cd C:\\Temp", True),
    ("rd command", "rd MyFolder", True),
    ("md command", "md NewFolder", True),
    ("del command", "del oldfile.txt", True),
    ("copy command", "copy a.txt b.txt", True),
    ("move command", "move a.txt dest\\", True),
    ("ren command", "ren old.txt new.txt", True),
    ("type command", "type readme.txt", True),
    ("find command", "find \"text\" file.txt", True),
    ("findstr command", "findstr \"pattern\" file.txt", True),
    ("prefix @ for echo", "@echo off", True),
    ("prefix @ alone", "@echo Hello", True),
    ("exit command", "exit", True),
    ("exit /b", "exit /b 0", True),
    ("setlocal", "setlocal", True),
    ("endlocal", "endlocal", True),
    ("setlocal delayed", "setlocal enabledelayedexpansion", True),
    ("title command", "title My Window", True),
    ("color command", "color 0a", True),
    ("prompt command", "prompt $g", True),
    ("choice command", "choice /c YN /m \"Continue?\"", True),
    ("start command", "start \"\" cmd", True),

    # ---- Invalid BAT lines ----
    ("bare colon", "some text:", False),
    ("text then colon at eol", "Hello World:   ", False),
    ("colon surrounded by spaces", "text :  ", False),
    ("unknown command at start", "mycustomcmd arg", False),
    ("unknown command alone", "mycustomcmd", False),
    ("text before command", "hello echo World", False),
    ("plain text no command", "just some text", False),
    ("random stuff", "random stuff here", False),
    ("bare label end followed by something", ": trailing", False),
    ("label colon only with spaces", ":   ", False),
    ("label colon with tab", ":\t", False),
    ("command label at end", "echo something:  ", False),
    ("echo old-style dot at end", "echo.:  ", False),
    ("multiple colons trailing", "echo :: something:  ", False),
    ("unknown with colon end", "mycmd arg:", False),
    ("text then command then colon end", "hello echo world:  ", False),
    ("unknown with colon end", "mycmd arg:", False),
    ("file list", "C:\1\2\!3+=.txt", False),
]

# ---------------------------------------------------------------------------
# Version A: is_bat (from "new versions.txt" --- Section A)
# Uses broad command keyword whitelist; scans all lines; has quoted-path guard.
# ---------------------------------------------------------------------------
_VERSION_A_RE = re.compile(
    r'^'
    r'((?:^|\s)@?(?:echo|set|if|for|goto|call|exit|pause|cls|dir|cd|rd|md|del|copy|move|ren|type|find|findstr|pushd|popd|title|color|prompt|path|timeout|choice|start|cmd|reg|sort|more|setlocal|endlocal|tasklist|taskkill|schtasks|attrib|ipconfig|ping|systeminfo)\b'
    r'|(?:\s|^)::'
    r'|REM\b.*'
    r'|::.*'
    r'|:[\w-]+'
    r'|@\s)',
    re.IGNORECASE,
)

def version_a_is_bat(text: str) -> bool:
    """Version A: Multi-line BAT detector with quoted-path guard."""
    lines = [l.rstrip("\n\r") for l in text.splitlines()]
    if not lines:
        return False
    has_valid = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if re.search(r':\s*$', line):
            continue
        if re.search(r'^\s*\w+:\s*$', line):
            continue
        if re.search(r'"', stripped) and not _VERSION_A_RE.search(line):
            if not any(c in stripped.upper() for c in ['ECHO', 'SET ', 'FOR ', 'IF ', 'GOTO ', 'CALL ', 'EXIT', 'PAUSE', 'CLS', 'DIR', 'CD ', 'RD ', 'MD ', 'DEL ', 'COPY ', 'MOVE ', 'REN ', 'TYPE', 'FIND', 'START', 'REM ', '::']):
                continue
        if _VERSION_A_RE.search(line):
            has_valid = True
    return has_valid


# ---------------------------------------------------------------------------
# Version D3: is_bat_optimal_precheck (from "new versions.txt" --- Section D3)
# Strict anchors + structural syntax; 40-line limit; skips JSON/quoted patterns.
# ---------------------------------------------------------------------------
_VERSION_D3_ANCHORS = re.compile(
    r'^(?:'
    r'@echo\s+(?:on|off)\b'
    r'|@?\b(?:setlocal|endlocal)\b'
    r'|::'
    r'|:[\w-]+(?:\s|$)'
    r')', re.IGNORECASE
)
_VERSION_D3_SYNTAX = re.compile(
    r'(?:'
    r'@?set\s+[\w_]+='
    r'|@?goto\s+[\w-]+'
    r'|@?call\s+[:\w]'
    r'|@?if\s+(?:not\s+)?(?:exist\b|defined\b|errorlevel\b|cmdextversion\b|[\"%!(])'
    r'|@?for\s+(?:\/[drlf]\s+)?%{1,2}\w+\s+in\s*\('
    r'|%[\w_]+%'
    r'|!\w+!'
    r')', re.IGNORECASE
)

def version_d3_is_bat(text: str, line_limit: int = 40) -> bool:
    """Version D3: Optimal precheck with strict anchors and syntax."""
    if not text or not text.strip():
        return False
    for i, line in enumerate(text.splitlines()):
        if i >= line_limit:
            break
        stripped = line.strip()
        if not stripped or stripped.upper().startswith('REM '):
            continue
        if stripped.endswith(':') and not stripped.startswith(':'):
            continue
        if re.match(r'^\w+:\s*$', stripped):
            continue
        if stripped.startswith(('"', '{', '[')):
            continue
        if _VERSION_D3_ANCHORS.match(stripped) or _VERSION_D3_SYNTAX.search(stripped):
            return True
    return False


# ---------------------------------------------------------------------------
# Version D2: is_bat_improved_precheck (from "new versions.txt" --- Section D2)
# Same strict anchors + syntax as D3 but without JSON/quoted early-exit guard.
# ---------------------------------------------------------------------------
_VERSION_D2_ANCHORS = re.compile(
    r'^(?:'
    r'@echo\s+(?:on|off)\b'
    r'|@?\bsetlocal\b|@?\bendlocal\b'
    r'|::'
    r'|:[\w-]+(?:\s|$)'
    r')', re.IGNORECASE
)
_VERSION_D2_SYNTAX = re.compile(
    r'(?:'
    r'@?set\s+[\w_]+='
    r'|@?goto\s+[\w-]+'
    r'|@?call\s+[:\w]'
    r'|@?if\s+(?:not\s+)?(?:exist\b|defined\b|errorlevel\b|cmdextversion\b|[\"%!(])'
    r'|@?for\s+(?:\/[drlf]\s+)?%{1,2}\w+\s+in\s*\('
    r'|%[\w_]+%'
    r'|!\w+!'
    r')', re.IGNORECASE
)

def version_d2_is_bat(text: str, line_limit: int = 40) -> bool:
    """Version D2: Improved precheck (D3 minus JSON guard)."""
    if not text or not text.strip():
        return False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if i >= line_limit:
            break
        stripped = line.strip()
        if not stripped or stripped.upper().startswith('REM '):
            continue
        if stripped.endswith(':') and not stripped.startswith(':'):
            continue
        if re.match(r'^\w+:\s*$', stripped):
            continue
        if _VERSION_D2_ANCHORS.match(stripped) or _VERSION_D2_SYNTAX.search(stripped):
            return True
    return False


# ---------------------------------------------------------------------------
# Version D1: is_bat_fast_precheck (from "new versions.txt" --- Section D1)
# Three regex layers: strict / logic / structural; 30-line limit.
# ---------------------------------------------------------------------------
_VERSION_D1_STRICT = re.compile(
    r'^@\w+'
    r'|^@?(?:echo\b|setlocal\b|endlocal\b|rem\b|pause\b|cls\b|title\b|timeout\b|choice\b)',
    re.IGNORECASE
)
_VERSION_D1_LOGIC = re.compile(
    r'^@?set\s+[\w_]+='
    r'|^@?goto\s+'
    r'|^@?call\s+'
    r'|^@?if\s+(?:not\s+)?(?:exist\b|defined\b|errorlevel\b|cmdextversion\b|["%!(])'
    r'|^@?for\s+(?:\/[drlf]\s+)?%{1,2}\w+',
    re.IGNORECASE
)
_VERSION_D1_STRUCTURAL = re.compile(
    r'^::'
    r'|^:[\w-]+'
    r'|^%[\w_]+%',
    re.IGNORECASE
)

_D1_LINE_LIMIT = 30

def version_d1_is_bat(text: str, line_limit: int = _D1_LINE_LIMIT) -> bool:
    """Version D1: Fast precheck with three regex layers."""
    if not text:
        return False
    for i, line in enumerate(text.splitlines()):
        if i >= line_limit:
            break
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith(':') and not stripped.startswith(':'):
            continue
        if re.match(r'^\w+:\s*$', stripped):
            continue
        if (_VERSION_D1_STRICT.match(stripped)
                or _VERSION_D1_LOGIC.match(stripped)
                or _VERSION_D1_STRUCTURAL.match(stripped)):
            return True
    return False


# ---------------------------------------------------------------------------
# Multi-line test cases (shared by all multi-line aware implementations)
# ---------------------------------------------------------------------------
MULTILINE_TEST_CASES = [
    # (description, multiline_text, expected_valid)

    # ---- Valid multi-line BAT ----
    ("multi-line: REM + echo + set",
     "REM Header\necho Hello\nset VAR=1\n", True),
    ("multi-line: labels and goto",
     ":start\necho before\ngoto :EOF\n", True),
    ("multi-line: if + for",
     "if exist x echo y\nfor %%f in (*) do echo %%f\n", True),
    ("multi-line: setlocal + variable + endlocal",
     "setlocal\nset X=1\necho %X%\nendlocal\n", True),

    # ---- Invalid multi-line BAT ----
    ("multi-line: plain text with trailing colon",
     "Hello World:\nsecond line\n", False),
    ("multi-line: JSON-like lines only",
     '{"key": "value"}\n[1, 2, 3]\n', False),
    ("multi-line: quoted paths only",
     '"C:\\foo\\bar.py"\n"C:\\another\\file.py"\n', False),
    # ---- False positive guard: JavaScript code must not match as BAT ----
    ("multi-line: JavaScript import + if block",
     'import fs from "node:fs"\nimport path from "node:path"\n\nconst g = globalThis as any\n\nif (!g.__kilo_llm_debug_fetch_patch) {\n\tg.__kilo_llm_debug_fetch_patch = true\n\n\tconst original = g.fetch?.bind(globalThis)\n\tconst decoder = new TextDecoder()\n', False),
]


# ---------------------------------------------------------------------------
# Approach 1: Naive - only check for colon at end-of-line
# ---------------------------------------------------------------------------
def approach1_is_valid_bat(text):
    line = text.rstrip("\n\r")
    if line == "" or line.isspace():
        return False
    return not re.search(r':\s*$', line)

# ---------------------------------------------------------------------------
# Approach 2: Two negative rules - forbid :\s*$ AND ^\s*\w+:\s*$
# ---------------------------------------------------------------------------
def approach2_is_valid_bat(text):
    line = text.rstrip("\n\r")
    if line == "" or line.isspace():
        return False
    if re.search(r':\s*$', line):
        return False
    if re.search(r'^\s*\w+:\s*$', line):
        return False
    return True

# ---------------------------------------------------------------------------
# Approach 3: Positive whitelist regex - explicitly allow known BAT commands
# ---------------------------------------------------------------------------
_APPROACH3_RE = re.compile(
    r'^'
    r'(@?(?:echo|set|for|goto|call|exit|pause|cls|dir|cd|rd|md|del|copy|move|ren|type|find|findstr|pushd|popd|title|color|prompt|path|timeout|choice|start|cmd|reg|sort|more|setlocal|endlocal|tasklist|taskkill|schtasks|attrib|ipconfig|ping|systeminfo)'
    r'|if\s+(?:not\s+)?(?:exist|defined|errorlevel|cmdextversion|/\w|[^=]*==)'
    r'|::.*'
    r'|REM\b.*'
    r'|:[\w-]+'
    r'|@.*'
    r'|\s*$)',
    re.IGNORECASE,
)

def approach3_is_valid_bat(text):
    line = text.rstrip("\n\r")
    if line == "" or line.isspace():
        return True
    return bool(_APPROACH3_RE.match(line))

# ---------------------------------------------------------------------------
# Best Mix: Combine all three approaches.
# ---------------------------------------------------------------------------
def best_mix_is_valid_bat(text):
    line = text.rstrip("\n\r")
    if line == "" or line.isspace():
        return False
    if re.search(r':\s*$', line):
        return False
    if re.search(r'^\s*\w+:\s*$', line):
        return False
    return bool(_APPROACH3_RE.match(line))

class TestBATValidationComparison(unittest.TestCase):
    def _score(self, func):
        passed = 0
        failed = 0
        for _, text, expected in TEST_CASES:
            if func(text) == expected:
                passed += 1
            else:
                failed += 1
        return passed, failed

    def _score_multiline(self, func):
        passed = 0
        failed = 0
        for _, text, expected in MULTILINE_TEST_CASES:
            if func(text) == expected:
                passed += 1
            else:
                failed += 1
        return passed, failed

    def test_report_scores(self):
        benchmark = BENCHMARK
        iterations = BENCHMARK_ITERATIONS
        approaches = [
            ("Approach 1 (naive colon-at-EOL)", approach1_is_valid_bat),
            ("Approach 2 (two negative rules)", approach2_is_valid_bat),
            ("Approach 3 (positive whitelist)", approach3_is_valid_bat),
            ("Best Mix (1+2+3)", best_mix_is_valid_bat),
            ("Version A (_BAT_RE whitelist)", version_a_is_bat),
            ("Version D3 (optimal precheck)", version_d3_is_bat),
            ("Version D2 (improved precheck)", version_d2_is_bat),
            ("Version D1 (fast 3-layer)", version_d1_is_bat),
        ]

        print("\n=== BAT Validation Approach Comparison ===")
        print(f"Single-line test cases: {len(TEST_CASES)}")
        print(f"Multi-line test cases:  {len(MULTILINE_TEST_CASES)}\n")
        best_approach = None
        best_score = -1
        total_cases = len(TEST_CASES) + len(MULTILINE_TEST_CASES)
        for name, func in approaches:
            if benchmark:
                profiler2 = ExecutionProfiler()
                profiler2.start()
                for _ in range(max(1, iterations)):
                    p1, f1 = self._score(func)
                    p2, f2 = self._score_multiline(func)
                elapsed = profiler2.stop()
            else:
                p1, f1 = self._score(func)
                p2, f2 = self._score_multiline(func)
                elapsed = None
            total_passed = p1 + p2
            total_failed = f1 + f2
            status = "PASS" if total_failed == 0 else f"FAIL ({total_failed} failures)"
            if elapsed is not None:
                print(f"  {name}: {total_passed}/{total_cases} passed - {status} ({elapsed:.4f}s for {max(1, iterations)}x)")
            else:
                print(f"  {name}: {total_passed}/{total_cases} passed - {status}")
            if total_passed > best_score:
                best_score = total_passed
                best_approach = name

        print(f"\nBest approach: {best_approach} ({best_score}/{total_cases})")
        self.assertTrue(best_score > 0, "At least one approach must pass some cases")

    def test_version_a_is_bat(self):
        self.assertTrue(version_a_is_bat("echo Hello"))
        self.assertTrue(version_a_is_bat("set X=1\nset Y=2"))
        self.assertFalse(version_a_is_bat('"C:\\foo\\bar.py"\n"C:\\another\\file.py"'))

    def test_version_d1_is_bat(self):
        self.assertTrue(version_d1_is_bat("setlocal\nset X=1\nendlocal"))
        self.assertTrue(version_d1_is_bat("echo Hello\nset Y=2"))
        self.assertFalse(version_d1_is_bat('{"key": "value"}\n[1, 2, 3]\n'))

    def test_version_d2_is_bat(self):
        self.assertTrue(version_d2_is_bat("setlocal\nset X=1\nendlocal"))
        self.assertTrue(version_d2_is_bat("echo Hello\nset Y=2"))
        self.assertFalse(version_d2_is_bat('{"key": "value"}\n[1, 2, 3]\n'))

    def test_version_d3_is_bat(self):
        self.assertTrue(version_d3_is_bat("setlocal\nset X=1\nendlocal"))
        self.assertTrue(version_d3_is_bat("echo Hello\nset Y=2"))
        self.assertFalse(version_d3_is_bat('{"key": "value"}\n[1, 2, 3]\n'))
        self.assertFalse(version_d3_is_bat('"C:\\foo\\bar.py"\n"C:\\another\\file.py"'))


class TestRealBatFunction(unittest.TestCase):
    def test_quoted_windows_paths_not_false_positive(self):
        from dev_helper.new_file.new_file_from_clipboard import is_bat
        text = '"C:\\123.py"\n"C:\\345.py"'
        self.assertFalse(is_bat(text), "Quoted Windows paths should not match as BAT")
    def test_quoted_windows_paths_no_colon_trigger(self):
        from dev_helper.new_file.new_file_from_clipboard import is_bat
        text = '"C:\\foo\\bar.py"\n"C:\\another\\file.py"'
        self.assertFalse(is_bat(text), "Quoted Windows paths should not trigger colon-at-eol rule")


def test_javascript_with_lang_hint():
    from dev_helper.new_file.new_file_from_clipboard import get_resolved_ext_prefix
    text = 'import fs from "node:fs"\nimport path from "node:path"\n\nconst g = globalThis as any\n\nif (!g.__kilo_llm_debug_fetch_patch) {\n\tg.__kilo_llm_debug_fetch_patch = true\n\n\tconst original = g.fetch?.bind(globalThis)\n\tconst decoder = new TextDecoder()\n'
    ext, _ = get_resolved_ext_prefix(text, lang_hint="javascript")
    assert ext == "js"


def test_html_doctype_returns_html():
    from dev_helper.new_file.new_file_from_clipboard import is_html
    ext, prefix = is_html("<!DOCTYPE html>\n<html>\n")
    assert ext == "html"
    assert prefix == "index"


# ---------------------------------------------------------------------------
# Test extract_code blocks (local copy)
import os
import re

_HEADER_LINE = re.compile(
    r'^(?P<hashes>#{1,6})\s+(?P<name>[^\n]*?)(?:(?<=\S)\s*[-—|–]\s*(?P<note>[^\n]*))?$',
    re.MULTILINE,
)

_FENCED_BLOCK = re.compile(
    r'^```(?P<lang>[^\n]*)\n(?P<code>.*?\n)```',
    re.DOTALL | re.MULTILINE,
)


def extract_code_blocks_with_names(text: str):
    lines = text.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        m = _HEADER_LINE.match(lines[i])
        if not m:
            i += 1
            continue
        block_name = m.group("name").strip()
        block_lang = m.group("note").strip() if m.group("note") else ""
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        if not lines[i].startswith("```"):
            continue
        fence_open = lines[i]
        i += 1
        content_lines = []
        closed = False
        while i < len(lines):
            if lines[i].startswith("```"):
                closed = True
                i += 1
                break
            content_lines.append(lines[i])
            i += 1
        if not closed:
            break
        content = "\n".join(content_lines) + ("\n" if content_lines else "")
        matched = re.match(r'^```\s*([^\n`]*)\s*$', fence_open + "\n```", re.DOTALL | re.MULTILINE)
        lang = matched.group(1).strip() if matched else block_lang or ""
        filename = block_name or ({"javascript": "script.js"}.get(lang.lower(), lang + "_file") if lang else "unnamed")
        if not filename.startswith("`"):
            blocks.append({"filename": filename, "lang": lang, "content": content})
        continue
    return blocks


def test_two_named_blocks():
    text = (
        "\n"
        "## index.html\n"
        "```html\n"
        "<!DOCTYPE html>\n"
        "```\n"
        "\n"
        "## draw.js - main render\n"
        "```javascript\n"
        "let x = 1;\n"
        "```\n"
    )
    blocks = extract_code_blocks_with_names(text)
    assert len(blocks) == 2, blocks
    names = [b["filename"] for b in blocks]
    assert "index.html" in names
    assert "draw.js" in names
    langs = [b["lang"] for b in blocks]
    assert "html" in langs
    assert "javascript" in langs


def test_draw_js_not_timestamped():
    import importlib
    nf = importlib.import_module('new_file.new_file_from_clipboard')
    blocks = [
        {"filename": "draw.js", "lang": "javascript", "content": "let x=1;\n"},
    ]
    calls = []
    def fake_save(content, ext, prefix, output_dir, profiler, full_filename=None):
        calls.append((ext, prefix, full_filename))
    orig = nf.save_text_file
    nf.save_text_file = fake_save
    try:
        for block in blocks:
            raw_filename = block["filename"]
            base, dot_ext = os.path.splitext(raw_filename)
            if dot_ext:
                nf.save_text_file(
                    block["content"],
                    dot_ext.lstrip("."),
                    base,
                    None,
                    None,
                    full_filename=raw_filename,
                )
            else:
                nf.save_text_file(block["content"], "txt", raw_filename, None, None)
    finally:
        nf.save_text_file = orig
    assert len(calls) == 1, calls
    assert calls[0][2] == "draw.js"


def test_unnamed_block_uses_lang():
    text = (
        "## \n"
        "```python\n"
        "x = 1\n"
        "```\n"
    )
    blocks = extract_code_blocks_with_names(text)
    assert len(blocks) == 1
    assert blocks[0]["filename"] == "python_file"
    assert blocks[0]["lang"] == "python"


def test_no_header_returns_empty():
    text = ""\
        "```python\n"\
        "x = 1\n"\
        "```\n"
    assert extract_code_blocks_with_names(text) == []


def test_dot_names_accepted():
    text = ""\
        "## lib/helpers.js\n"\
        "```javascript\n"\
        "export const help = () => 1;\n"\
        "```\n"
    blocks = extract_code_blocks_with_names(text)
    assert len(blocks) == 1
    assert blocks[0]["filename"] == "lib/helpers.js"


def test_single_block_creates_md():
    import importlib
    nf = importlib.import_module('new_file.new_file_from_clipboard')
    text = (
        "## index.html\n"
        "```html\n"
        "<!DOCTYPE html>\n"
        "```\n"
    )
    calls = []
    def fake_save(content, ext, prefix, output_dir, profiler, full_filename=None):
        calls.append({"ext": ext, "prefix": prefix, "full_filename": full_filename, "content": content})
    orig = nf.save_text_file
    nf.save_text_file = fake_save
    try:
        blocks = nf.extract_code_blocks_with_names(text)
        for block in blocks:
            raw_filename = block["filename"]
            base, dot_ext = os.path.splitext(raw_filename)
            if dot_ext:
                nf.save_text_file(
                    block["content"],
                    dot_ext.lstrip("."),
                    base,
                    None,
                    None,
                    full_filename=raw_filename,
                )
            else:
                nf.save_text_file(block["content"], "txt", raw_filename, None, None)
        nf.save_text_file(text, *nf.unpack_config(nf.EXT_CONFIG["md"]), None, None)
    finally:
        nf.save_text_file = orig
    md_calls = [c for c in calls if c["ext"] == "md"]
    assert len(md_calls) >= 1, f"Expected at least 1 .md call, got: {calls}"


def test_user_index_html_not_skipped():
    text = (
        "\n"
        "## index.html\n"
        "```html\n"
        "<!DOCTYPE html>\n"
        "\n"
        "```\n"
        "\n"
        "## 1.js \n"
        "```javascript\n"
        "// 1.js\n"
        "\n"
        "```\n"
        "\n"
        "\n"
        "## game.js — Main game logic\n"
        "```javascript\n"
        "// game\n"
        "```\n"
    )
    blocks = extract_code_blocks_with_names(text)
    names = [b["filename"] for b in blocks]
    assert "index.html" in names, names
    assert "1.js" in names, names
    assert "game.js" in names, names
    assert len([b for b in blocks if b["filename"] == ".md"]) == 0


def test_clipboard_saves_all_files_and_md():
    import importlib
    nf = importlib.import_module('new_file.new_file_from_clipboard')

    text = (
        "# project\n"
        "\n"
        "## index.html\n"
        "```html\n"
        "<!DOCTYPE html>\n"
        " \n"
        "```\n"
        "\n"
        "## 1.js \n"
        "```javascript\n"
        "// 1.js\n"
        "\n"
        "```\n"
        "\n"
        "\n"
        "```\n"
        "\n"
        "\n"
        "## game.js — Main game logic\n"
        "```javascript\n"
        "// game\n"
        "```\n"
    )

    blocks = nf.extract_code_blocks_with_names(text)
    names = [b["filename"] for b in blocks]
    assert "index.html" in names, names
    assert "1.js" in names, names
    assert "game.js" in names, names
    assert len(blocks) == 3, f"Expected 3 code blocks, got: {blocks}"

    md_blocks = [b for b in blocks if b["filename"] == ".md"]
    assert len(md_blocks) == 0, f"Expected no .md from extraction, got: {blocks}"


def test_malformed_fenced_yields_empty():
    text = (
        "## index.html\n"
        "```html\n"
        "<!DOCTYPE html>\n"
    )
    assert extract_code_blocks_with_names(text) == []


# ---------------------------------------------------------------------------
# Format-agnostic path extraction (exercises the REAL module function).
# A path may appear before OR after any code block (``` fence or ======
# delimiter), relative or absolute, as a standalone line or inside a comment.
# ---------------------------------------------------------------------------
def test_extract_format_agnostic_paths():
    import importlib
    nf = importlib.import_module('new_file.new_file_from_clipboard')

    CASES = [
        ("path line above ``` fence", "2/b.txt\n```\n....\n```\n", [("2/b.txt", "txt")]),
        ("====== delimiter, path on line", "====== 2/b.txt\n....\n", [("2/b.txt", "txt")]),
        ("====== delimiter, path on line before", "2/b.txt\n======\n....\n", [("2/b.txt", "txt")]),
        ("comment inside block", "```\n//------ asd.html -------\n<p>hi</p>\n```\n", [("asd.html", "html")]),
        ("prose before block, absolute path", "place this text to A:/1.py and run it:\n```\nqwe\n```\n", [("A:/1.py", "py")]),
        ("comment at end of block", "```\nfn main() {}\n// file: src/main.rs\n```\n", [("src/main.rs", "rs")]),
        ("dedup multi-path above fence", "ui/a.cs\nui/b.cs\n```csharp\nclass C {}\n```\n", [("ui/a.cs", "cs"), ("ui/b.cs", "cs")]),
        ("dedup comma on ====== line", "====== 1/a.cs, c.txt\nclass C {}\n", [("1/a.cs", "cs"), ("c.txt", "txt")]),
        ("no-extension name", "Dockerfile\n```\nFROM scratch\n```\n", [("Dockerfile", None)]),
        ("markdown header before block", "## index.html\n```html\n<!DOCTYPE html>\n```\n", [("index.html", "html")]),
    ]
    for desc, text, expected in CASES:
        got = [(b["filename"], b["lang"]) for b in nf.extract_code_blocks_with_names(text)]
        assert got == expected, f"{desc}: got {got}, expected {expected}"


def test_extract_leak_does_not_create_comment_filename():
    import importlib
    nf = importlib.import_module('new_file.new_file_from_clipboard')
    text = (
        "ui/render.rs\n```rust\n/// block. we walk lines looking\n"
        "fn render() {}\n```\n"
        "ui/toolbar.rs\n```rust\nfn t() {}\n```\n"
    )
    got = [b["filename"] for b in nf.extract_code_blocks_with_names(text)]
    assert got == ["ui/render.rs", "ui/toolbar.rs"], got
    assert not any("walk" in (f or "") for f in got)


def test_extract_absolute_path_is_preserved():
    import importlib
    nf = importlib.import_module('new_file.new_file_from_clipboard')
    text = "save to C:/work/app.py:\n```\nprint(1)\n```\n"
    blocks = nf.extract_code_blocks_with_names(text)
    assert len(blocks) == 1
    assert blocks[0]["filename"] == "C:/work/app.py"



