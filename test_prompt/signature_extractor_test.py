"""Strict tests for signature extraction.

Every extraction test asserts the EXACT full output (signature text and
count), so any body-code leftover, modifier leak, or dropped type is a
failure.  The cases include the original bug report (function headers
polluted with body code) plus fakes in comments / strings / initializers.
"""

from unittest.mock import patch

import pytest

from dev_helper.common.signature_extractor import (
    SUPPORTED_EXTENSIONS,
    CLikeExtractor,
    JSLikeExtractor,
    PythonExtractor,
    RustExtractor,
    ShaderExtractor,
    extract_signatures,
    parse_html_structure,
)
from dev_helper.to_clipboard.signature_from_crate import (
    extract_crate_signatures_to_clipboard,
)

# ---------------------------------------------------------------------------
# Exact-output case tables: (content, expected signature lines)
# ---------------------------------------------------------------------------

PYTHON_CASES = [
    (
        "def hello(name):\n    print(name)",
        ["def hello(name)"],
    ),
    (
        "def f(x: int, y: str = 'hi') -> bool:\n    return True",
        ["def f(x: int, y: str = 'hi') -> bool"],
    ),
    (
        "async def load(url: str) -> str:\n    return url",
        ["async def load(url: str) -> str"],
    ),
    (
        "class Child(Parent, Mixin):\n    pass",
        ["class Child(Parent, Mixin)"],
    ),
    (
        "class Point[T]:\n    pass",
        ["class Point[T]"],
    ),
    (
        "def f(\n    a,\n    b,\n    c,\n):\n    pass",
        ["def f(a, b, c)"],
    ),
    (
        'def f(x=")"):\n    pass',
        ['def f(x=")")'],
    ),
    (
        "def f(cb=lambda x: x):\n    pass",
        ["def f(cb=lambda x: x)"],
    ),
    (
        "def f() -> dict[str, int]:\n    return {}",
        ["def f() -> dict[str, int]"],
    ),
    (
        "@app.route('/x')\ndef handler():\n    pass",
        ["def handler()"],
    ),
    (
        "# def fake(a):\n    pass\ndef real(x):\n    return x",
        ["def real(x)"],
    ),
    (
        '"""docstring with def fake():\n    pass"""\ndef real():\n    pass',
        ["def real()"],
    ),
    (
        "def outer():\n    def inner(x):\n        return x\n    return inner",
        ["def outer()", "def inner(x)"],
    ),
    (
        "def foo():\n    pass\n\ndef bar():\n    pass\n\nclass MyClass:\n    pass",
        ["def foo()", "def bar()", "class MyClass"],
    ),
]

# The original bug report: headers polluted with body code.
USER_REPRO_JS = r"""
const AppState = { planetKey: 'earth' };

function drawA1_clearBaseline(w, h) {
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, w, h);
}

function drawA2_atmosphereGradients(w, h, p) {
    const topColor = p === 'day' ? `rgba(135, 206, 250, 0.9)` : `rgba(26, 26, 46, 0.9)`;
    if (topColor === 'init') {
        topColor = `rgba(135, 206, 250, 0.9)`;
    } else if (AppState.planetKey === 'earth_dusty') {
        topColor = `rgba(160, 120, 60, 0.9)`;
    } else {
        topColor = `rgba(120, 90, 50, 0.9)`;
    }
    ctx.fillRect(0, y, w, stepSize);
}

function drawA3_smogLayer(w, h) {
    const path = new Path2D();
    path.moveTo(0, 0);
    if (fog) { path.rect(0, 0, w, h); }
    ctx.closePath();
    ctx.fill();
}
"""

JS_CASES = [
    (
        "function greet(name) { return 'hi'; }",
        ["function greet(name)"],
    ),
    (
        "function greet(name: string): string { return name; }",
        ["function greet(name: string): string"],
    ),
    (
        "async function load(url: string): Promise<Data> { }",
        ["async function load(url: string): Promise<Data>"],
    ),
    (
        ("function f(a: number): number;\n"
         "function f(a: string): string;\n"
         "function f(a: any): any { return a; }"),
        [
            "function f(a: number): number",
            "function f(a: string): string",
            "function f(a: any): any",
        ],
    ),
    (
        "const f = (x) => x + 1;",
        ["const f = (x) =>"],
    ),
    (
        "const f = (x: number): number => x + 1;",
        ["const f = (x: number): number =>"],
    ),
    (
        "const f = (x) => { return x; };",
        ["const f = (x) =>"],
    ),
    (
        "class Foo { bar(x) { return x; } }",
        ["class Foo"],
    ),
    (
        "class C { handler = (e) => { log(e); } }",
        ["class C", "handler = (e) =>"],
    ),
    (
        "class C { handler = (e: Event) => { log(e); } }",
        ["class C", "handler = (e: Event) =>"],
    ),
    (
        "class C { run() { const f = (x) => x; return f(1); } }",
        ["class C"],
    ),
    (
        "type Handler = (e: Event) => void;",
        ["type Handler = (e: Event) => void"],
    ),
    (
        "interface Foo { bar(): void; }",
        ["interface Foo"],
    ),
    (
        "function getCfg(): { a: number; b: string } { return { a: 1, b: 'x' }; }",
        ["function getCfg(): { a: number; b: string }"],
    ),
    (
        "function f() { const s = '{ not a body }'; return s; }",
        ["function f()"],
    ),
    (
        "function f() { if (a) { g(); } }",
        ["function f()"],
    ),
    (
        "function f() { const s = `hello ${name}`; }",
        ["function f()"],
    ),
    (
        "export function multiply(x: number, y: number): number { return x * y; }",
        ["function multiply(x: number, y: number): number"],
    ),
    (
        "function f({ a = g() }) { }",
        ["function f({ a = g() })"],
    ),
    (
        "// function fake(a) {}\nfunction real(a) { }",
        ["function real(a)"],
    ),
    # The original bug report, verbatim.
    (
        USER_REPRO_JS,
        [
            "function drawA1_clearBaseline(w, h)",
            "function drawA2_atmosphereGradients(w, h, p)",
            "function drawA3_smogLayer(w, h)",
        ],
    ),
]

C_CASES = [
    (
        "public class Foo { }",
        ["class Foo"],
    ),
    (
        "public class Foo extends Bar implements Baz, Qux { }",
        ["class Foo extends Bar implements Baz, Qux"],
    ),
    (
        "public static void myMethod(int a) { }",
        ["void myMethod(int a)"],
    ),
    (
        "void myFunction(int x) { }",
        ["void myFunction(int x)"],
    ),
    (
        "int getValue();",
        ["int getValue()"],
    ),
    (
        "void f(int (*cb)(int)) { }",
        ["void f(int (*cb)(int))"],
    ),
    (
        'void g(const char* s = ")") { }',
        ['void g(const char* s = ")")'],
    ),
    (
        "int C::f(int x) { return x; }",
        ["int C::f(int x)"],
    ),
    (
        "Foo::Foo(int x) : m_x(x) { }",
        ["Foo::Foo(int x)"],
    ),
    (
        "public Foo(int x) { }",
        ["Foo(int x)"],
    ),
    (
        "std::pair<int, std::string> make() { }",
        ["std::pair<int, std::string> make()"],
    ),
    (
        "std::vector<int>& get() { }",
        ["std::vector<int>& get()"],
    ),
    (
        "void f(std::string& s) { }",
        ["void f(std::string& s)"],
    ),
    (
        "public Map<String, Integer> getMap() { }",
        ["Map<String, Integer> getMap()"],
    ),
    (
        "void f() { Point p(1, 2); }",
        ["void f()"],
    ),
    (
        "void f() { Point p(a, b); }",
        ["void f()"],
    ),
    (
        "void f() { obj.method(x); }",
        ["void f()"],
    ),
    (
        "void f() { g(a ? b : c); }",
        ["void f()"],
    ),
    (
        "void f() { process(int arg); }",
        ["void f()"],
    ),
    (
        "void f() { for (int i = 0; i < n; i++) { } }",
        ["void f()"],
    ),
    (
        "template <typename T>\nvoid f(T x) { }",
        ["void f(T x)"],
    ),
    (
        "struct Point { int x; int y; };",
        ["struct Point"],
    ),
    (
        "void f(struct Point p) { }",
        ["void f(struct Point p)"],
    ),
    (
        "enum Color { RED, GREEN };",
        ["enum Color"],
    ),
    (
        "#define F(x) ((x)+1)",
        [],
    ),
    (
        "interface Drawable { void draw(); }",
        ["interface Drawable", "void draw()"],
    ),
    (
        "// void fake(int x) {}\nvoid real(int x) { }",
        ["void real(int x)"],
    ),
    (
        "const char* f(int x) { }",
        ["const char* f(int x)"],
    ),
    (
        "void f(int a[10]) { }",
        ["void f(int a[10])"],
    ),
]

RUST_CASES = [
    (
        "fn add(a: i32, b: i32) -> i32 { a + b }",
        ["fn add(a: i32, b: i32) -> i32"],
    ),
    (
        "pub fn calculate(x: f64) -> f64 { x * 2.0 }",
        ["pub fn calculate(x: f64) -> f64"],
    ),
    (
        "pub(crate) fn f(x: u32) -> u32 { x }",
        ["pub(crate) fn f(x: u32) -> u32"],
    ),
    (
        "const unsafe fn raw(p: *const u8) { }",
        ["const unsafe fn raw(p: *const u8)"],
    ),
    (
        "fn first<T: Clone>(x: T) -> T where T: Debug { x }",
        ["fn first<T: Clone>(x: T) -> T where T: Debug"],
    ),
    (
        "async fn fetch(url: &str) -> Result<Data, Err> { todo!() }",
        ["async fn fetch(url: &str) -> Result<Data, Err>"],
    ),
    (
        "struct Point { x: f32, y: f32 }",
        ["struct Point"],
    ),
    (
        "pub struct User { name: String, age: u32 }",
        ["pub struct User"],
    ),
    (
        "struct Pair(i32, i32);",
        ["struct Pair"],
    ),
    (
        "struct Marker;",
        ["struct Marker"],
    ),
    (
        "enum Color { Red, Green, Blue }",
        ["enum Color"],
    ),
    (
        "pub enum Result<T, E> { Ok(T), Err(E) }",
        ["pub enum Result<T, E>"],
    ),
    (
        "trait Display { fn fmt(&self) -> String; }",
        ["trait Display", "fn fmt(&self) -> String"],
    ),
    (
        "trait Foo: Debug + Send { }",
        ["trait Foo: Debug + Send"],
    ),
    (
        "impl Point { fn new(x: f32, y: f32) -> Self { Self { x, y } } }",
        ["impl Point", "fn new(x: f32, y: f32) -> Self"],
    ),
    (
        "impl Display for Point { fn fmt(&self) -> String { } }",
        ["impl Display for Point", "fn fmt(&self) -> String"],
    ),
    (
        "impl<T> Foo<T> { }",
        ["impl<T> Foo<T>"],
    ),
    (
        "type Pair = (i32, i32);",
        ["type Pair = (i32, i32)"],
    ),
    (
        "type Map<K, V> = HashMap<K, V>;",
        ["type Map<K, V> = HashMap<K, V>"],
    ),
    (
        "mod utils;",
        ["mod utils"],
    ),
    (
        "mod utils { pub fn helper() { } }",
        ["mod utils", "pub fn helper()"],
    ),
    (
        "fn f<'a>(x: &str) -> &str { x }",
        ["fn f<'a>(x: &str) -> &str"],
    ),
    (
        "/// fn fake() {}\npub fn real() { }",
        ["pub fn real()"],
    ),
    (
        'fn f() { let s = "fn fake() {}"; }',
        ["fn f()"],
    ),
]

SHADER_CASES = [
    (
        "struct UniformData { mat4 model; }",
        ["struct UniformData"],
    ),
    (
        "fn calculate(x: f32) -> f32 { return x; }",
        ["fn calculate(x: f32) -> f32"],
    ),
    (
        "fn process(data: vec3<f32>) -> vec4<f32> { }",
        ["fn process(data: vec3<f32>) -> vec4<f32>"],
    ),
    (
        "fn main() { }",
        ["fn main()"],
    ),
    (
        "void main() { }",
        ["main()"],
    ),
    (
        "vec4 mixColors(vec4 a, vec4 b, float t) { return mix(a, b, t); }",
        ["mixColors(vec4 a, vec4 b, float t)"],
    ),
    (
        "uniform mat4 u_mvp; ",
        ["mat4 u_mvp"],
    ),
    (
        "uniform float u_time; ",
        ["float u_time"],
    ),
    (
        "@group(0) @binding(0) var<uniform> u: UniformData; ",
        ["var<uniform> u: UniformData"],
    ),
    (
        "varying vec4 v_color; ",
        ["vec4 v_color"],
    ),
    (
        "// fn fake() {}\nfn real(x: f32) -> f32 { return x; }",
        ["fn real(x: f32) -> f32"],
    ),
    (
        "void main() { gl_FragColor = mix(a, b, t); }",
        ["main()"],
    ),
    (
        "layout(location = 0) out vec4 fragColor; ",
        ["out vec4 fragColor"],
    ),
]


# ---------------------------------------------------------------------------
# Shared exact-output checker
# ---------------------------------------------------------------------------

def _parens_balanced(line):
    """Paren/bracket balance, ignoring contents of string literals."""
    depth_p = depth_b = 0
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c in "\"'`":
            i += 1
            while i < n and line[i] != c:
                i += 2 if line[i] == "\\" else 1
            i += 1
            continue
        if c == "(":
            depth_p += 1
        elif c == ")":
            depth_p -= 1
        elif c == "[":
            depth_b += 1
        elif c == "]":
            depth_b -= 1
        i += 1
    return depth_p == 0 and depth_b == 0


def _check(extractor, content, expected):
    """Assert extract() returns exactly `expected` (list of signature lines)."""
    sig_text, sig_count = extractor.extract(content)
    lines = sig_text.split("\n") if sig_text else []
    assert sig_count == len(expected), (
        f"count mismatch: got {sig_count}, want {len(expected)}\n"
        f"--- got:\n{sig_text}\n--- want:\n" + "\n".join(expected)
    )
    assert lines == expected, (
        f"--- got:\n{sig_text}\n--- want:\n" + "\n".join(expected)
    )
    # invariants that catch leftovers / sloppy output
    for line in lines:
        assert line == line.strip(), f"untrimmed signature: {line!r}"
        assert "  " not in line, f"double space in signature: {line!r}"
        assert _parens_balanced(line), f"unbalanced parens: {line!r}"
    return sig_text


def _check_no_leftover(extractor, content, expected, banned):
    """Exact output + none of the `banned` substrings may appear anywhere."""
    sig_text = _check(extractor, content, expected)
    for token in banned:
        assert token not in sig_text, f"leftover {token!r} in:\n{sig_text}"


# ---------------------------------------------------------------------------
# Per-language exact-output suites
# ---------------------------------------------------------------------------

class TestPythonExtractor:
    @pytest.mark.parametrize("content,expected", PYTHON_CASES)
    def test_extract_exact(self, content, expected):
        _check(PythonExtractor(), content, expected)

    def test_string_paren_no_leftover(self):
        content = 'def f(x=")"):\n    pass'
        _check_no_leftover(
            PythonExtractor(), content, ['def f(x=")")'],
            banned=["return", "pass"],
        )

    def test_docstring_fake_not_extracted(self):
        content = '"""def fake():\n    pass"""\ndef real():\n    pass'
        sig_text, count = PythonExtractor().extract(content)
        assert count == 1
        assert "fake" not in sig_text

    def test_strip_comments_preserves_offsets(self):
        extractor = PythonExtractor()
        content = 'def f():  # a comment\n    """doc"""\n    x = ")():"\n'
        result = extractor.strip_comments(content)
        assert len(result) == len(content)
        assert "comment" not in result
        assert "doc" not in result


class TestJSLikeExtractor:
    @pytest.mark.parametrize("content,expected", JS_CASES)
    def test_extract_exact(self, content, expected):
        _check(JSLikeExtractor(), content, expected)

    def test_user_repro_no_leftover(self):
        banned = ["ctx.", "fillRect", "else", "if (", "Path2D", "closePath",
                  "fill();", "rgba", "{", "}"]
        _check_no_leftover(JSLikeExtractor(), USER_REPRO_JS, [
            "function drawA1_clearBaseline(w, h)",
            "function drawA2_atmosphereGradients(w, h, p)",
            "function drawA3_smogLayer(w, h)",
        ], banned=banned)

    def test_assignment_is_not_a_function(self):
        content = "const x = 5; const y = 6;"
        sig_text, count = JSLikeExtractor().extract(content)
        assert count == 0
        assert sig_text == ""

    def test_strip_comments_preserves_offsets(self):
        extractor = JSLikeExtractor()
        content = "function f() { // hi\n const s = '{ }'; } /* block */"
        result = extractor.strip_comments(content)
        assert len(result) == len(content)
        assert "hi" not in result
        assert "block" not in result


class TestCLikeExtractor:
    @pytest.mark.parametrize("content,expected", C_CASES)
    def test_extract_exact(self, content, expected):
        _check(CLikeExtractor(), content, expected)

    def test_var_init_no_leftover(self):
        _check_no_leftover(
            CLikeExtractor(), "void f() { Point p(1, 2); }",
            ["void f()"], banned=["Point", "1, 2"],
        )

    def test_body_call_not_extracted(self):
        sig_text, count = CLikeExtractor().extract("void f() { process(int arg); }")
        assert count == 1
        assert "process" not in sig_text

    def test_strip_comments_preserves_offsets(self):
        extractor = CLikeExtractor()
        content = "void f() { /* x */ int a; // y\n }"
        result = extractor.strip_comments(content)
        assert len(result) == len(content)
        assert "/* x */" not in result
        assert "// y" not in result


class TestRustExtractor:
    @pytest.mark.parametrize("content,expected", RUST_CASES)
    def test_extract_exact(self, content, expected):
        _check(RustExtractor(), content, expected)

    def test_string_fake_not_extracted(self):
        sig_text, count = RustExtractor().extract('fn f() { let s = "fn fake() {}"; }')
        assert count == 1
        assert "fake" not in sig_text

    def test_strip_comments_preserves_offsets(self):
        extractor = RustExtractor()
        content = "fn f() { let a = 1; // c\n /* b */ }"
        result = extractor.strip_comments(content)
        assert len(result) == len(content)
        assert "// c" not in result
        assert "/* b */" not in result


class TestShaderExtractor:
    @pytest.mark.parametrize("content,expected", SHADER_CASES)
    def test_extract_exact(self, content, expected):
        _check(ShaderExtractor(), content, expected)

    def test_comment_fake_not_extracted(self):
        sig_text, count = ShaderExtractor().extract(
            "// fn fake() {}\nfn real(x: f32) -> f32 { return x; }"
        )
        assert count == 1
        assert "fake" not in sig_text

    def test_glsl_call_in_body_not_extracted(self):
        sig_text, count = ShaderExtractor().extract(
            "void main() { gl_FragColor = mix(a, b, t); }"
        )
        assert count == 1
        assert "mix" not in sig_text


# ---------------------------------------------------------------------------
# clean_signature behaviour (legacy helper, used by the base-class pipeline)
# ---------------------------------------------------------------------------

class TestCleanSignature:
    def test_python_removes_colon(self):
        assert PythonExtractor().clean_signature("def foo():") == "def foo()"

    def test_python_normalizes_whitespace(self):
        assert PythonExtractor().clean_signature("def   foo(  a  ,  b  ):") == (
            "def foo( a , b )"
        )

    def test_js_removes_export(self):
        assert JSLikeExtractor().clean_signature("export function foo() {") == (
            "function foo()"
        )

    def test_js_keeps_async_and_arrow(self):
        assert JSLikeExtractor().clean_signature("async function baz() {") == (
            "async function baz()"
        )

    def test_c_removes_modifiers(self):
        assert CLikeExtractor().clean_signature("public static void foo() {") == (
            "void foo()"
        )

    def test_c_removes_semicolon_and_brace(self):
        assert CLikeExtractor().clean_signature("int getValue();") == "int getValue()"

    def test_rust_normalizes_spacing(self):
        assert RustExtractor().clean_signature("fn   my_func  (  a  ,  b  )") == (
            "fn my_func(a, b)"
        )

    def test_shader_removes_storage_qualifier(self):
        assert ShaderExtractor().clean_signature("uniform mat4 u_mvp;") == "mat4 u_mvp"


# ---------------------------------------------------------------------------
# extract_signatures dispatch
# ---------------------------------------------------------------------------

class TestExtractSignatures:
    def test_unsupported_extension_returns_empty(self):
        assert extract_signatures("some content", "xyz") == ("", 0)

    def test_empty_extension_returns_empty(self):
        assert extract_signatures("def foo(): pass", "") == ("", 0)

    def test_python(self):
        sig_text, count = extract_signatures("def hello(name):\n    print(name)", "py")
        assert count == 1
        assert sig_text == "def hello(name)"

    def test_js(self):
        sig_text, count = extract_signatures("function test() { }", "js")
        assert count == 1
        assert sig_text == "function test()"

    def test_ts_keeps_types(self):
        content = "function greet(name: string): string { return 'Hello'; }"
        sig_text, count = extract_signatures(content, "ts")
        assert count == 1
        assert sig_text == "function greet(name: string): string"

    def test_mode_is_a_noop(self):
        content = "function greet(name: string): string { return 'Hello'; }"
        default = extract_signatures(content, "ts")
        assert extract_signatures(content, "ts", mode="A") == default
        assert extract_signatures(content, "ts", mode="B") == default

    def test_uppercase_extension(self):
        sig_text, count = extract_signatures("function test() { }", "JS")
        assert count == 1
        assert sig_text == "function test()"

    def test_java(self):
        sig_text, count = extract_signatures("public class Foo { }", "java")
        assert count == 1
        assert sig_text == "class Foo"

    def test_cpp(self):
        sig_text, count = extract_signatures("void myFunction(int x) { }", "cpp")
        assert count == 1
        assert sig_text == "void myFunction(int x)"

    def test_rust(self):
        sig_text, count = extract_signatures(
            "pub fn calculate(x: f64) -> f64 { x * 2.0 }", "rs"
        )
        assert count == 1
        assert sig_text == "pub fn calculate(x: f64) -> f64"

    def test_wgsl(self):
        sig_text, count = extract_signatures("fn main() { }", "wgsl")
        assert count == 1
        assert sig_text == "fn main()"

    def test_glsl(self):
        sig_text, count = extract_signatures("void main() { }", "glsl")
        assert count == 1
        assert sig_text == "main()"

    def test_html_delegates_to_js(self):
        content = "<html><body><script>function fromScript() { }</script></body></html>"
        sig_text, count = extract_signatures(content, "html")
        assert count == 1
        assert sig_text == "function fromScript()"

    def test_multiple_signatures(self):
        content = "def foo():\n    pass\n\ndef bar():\n    pass\n\nclass MyClass:\n    pass"
        sig_text, count = extract_signatures(content, "py")
        assert count == 3
        assert sig_text == "def foo()\ndef bar()\nclass MyClass"


# ---------------------------------------------------------------------------
# parse_html_structure
# ---------------------------------------------------------------------------

class TestParseHtmlStructure:
    def test_extracts_single_id(self):
        assert parse_html_structure('<div id="main">content</div>') == ["div#main"]

    def test_extracts_multiple_ids(self):
        html = '<div id="header"><span id="title"></span></div>'
        assert parse_html_structure(html) == ["div#header", "span#title"]

    def test_no_ids(self):
        assert parse_html_structure("<div><span></span></div>") == []

    def test_single_quoted_id(self):
        assert parse_html_structure("<section id='wrap'></section>") == ["section#wrap"]


# ---------------------------------------------------------------------------
# SUPPORTED_EXTENSIONS
# ---------------------------------------------------------------------------

class TestSupportedExtensions:
    def test_includes_py(self):
        assert "*.py" in SUPPORTED_EXTENSIONS

    def test_includes_js_family(self):
        for ext in ["js", "ts", "jsx", "tsx"]:
            assert f"*.{ext}" in SUPPORTED_EXTENSIONS

    def test_includes_class_based_languages(self):
        for ext in ["java", "cpp", "c", "cs", "php"]:
            assert f"*.{ext}" in SUPPORTED_EXTENSIONS

    def test_includes_shader_languages(self):
        for ext in ["wgsl", "glsl"]:
            assert f"*.{ext}" in SUPPORTED_EXTENSIONS

    def test_includes_rust(self):
        assert "*.rs" in SUPPORTED_EXTENSIONS

    def test_includes_html(self):
        assert "*.html" in SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------------------
# signature_from_crate formatting
# ---------------------------------------------------------------------------

class TestSignatureFromCrateFormat:
    def test_specific_function_header_format(self):
        captured = {}

        def mock_copy(text):
            captured["text"] = text

        with patch(
            "dev_helper.to_clipboard.signature_from_crate.copy_to_clipboard", mock_copy
        ), patch(
            "dev_helper.to_clipboard.signature_from_crate.get_crate_function_signature",
            return_value="pub fn hash(input: &[u8])-> Hash",
        ), patch(
            "dev_helper.to_clipboard.signature_from_crate.fetch_crate_info",
            return_value={"num": "1.5.0"},
        ):
            extract_crate_signatures_to_clipboard([("blake3", "1.5.0", "hash")])

        assert captured["text"] == (
            "=== blake3 1.5.0\n\npub fn hash(input: &[u8])-> Hash"
        )

    def test_latest_function_header_format(self):
        captured = {}

        def mock_copy(text):
            captured["text"] = text

        with patch(
            "dev_helper.to_clipboard.signature_from_crate.copy_to_clipboard", mock_copy
        ), patch(
            "dev_helper.to_clipboard.signature_from_crate.get_crate_function_signature",
            return_value="pub fn hash(input: &[u8])-> Hash",
        ), patch(
            "dev_helper.to_clipboard.signature_from_crate.fetch_crate_info",
            return_value={"num": "1.8.5"},
        ):
            extract_crate_signatures_to_clipboard([("blake3", "*", "hash")])

        assert captured["text"] == (
            "=== blake3 1.8.5\n\npub fn hash(input: &[u8])-> Hash"
        )

    def test_all_signatures_header_format(self):
        captured = {}

        def mock_copy(text):
            captured["text"] = text

        with patch(
            "dev_helper.to_clipboard.signature_from_crate.copy_to_clipboard", mock_copy
        ), patch(
            "dev_helper.to_clipboard.signature_from_crate.get_crate_signatures",
            return_value=("pub fn foo()-> i32", 1),
        ), patch(
            "dev_helper.to_clipboard.signature_from_crate.fetch_crate_info",
            return_value={"num": "1.8.5"},
        ):
            extract_crate_signatures_to_clipboard([("blake3", "*", "")])

        assert captured["text"] == "=== blake3 1.8.5\n\npub fn foo()-> i32"
