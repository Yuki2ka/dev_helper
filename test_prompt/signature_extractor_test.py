import pytest
from unittest.mock import patch
from dev_helper.common.signature_extractor import (
    PythonExtractor,
    JSLikeExtractor,
    ShaderExtractor,
    CLikeExtractor,
    RustExtractor,
    extract_signatures,
    parse_html_structure,
    SUPPORTED_EXTENSIONS,
)
from dev_helper.to_clipboard.signature_from_crate import extract_crate_signatures_to_clipboard


class TestPythonExtractor:
    def test_extract_simple_function(self):
        content = "def hello(name):\n    print(name)"
        sig_text, sig_count = PythonExtractor().extract(content)
        assert sig_count == 1
        assert "def hello(name)" in sig_text

    def test_extract_function_with_multiline_signature(self):
        content = "def my_func(\n    a,\n    b,\n    c\n):"
        sig_text, sig_count = PythonExtractor().extract(content)
        assert sig_count == 1
        assert "def my_func" in sig_text

    def test_extract_class(self):
        content = "class MyClass:\n    pass"
        sig_text, sig_count = PythonExtractor().extract(content)
        assert sig_count == 1
        assert "class MyClass" in sig_text

    def test_extract_class_with_inheritance(self):
        content = "class Child(Parent):\n    pass"
        sig_text, sig_count = PythonExtractor().extract(content)
        assert sig_count == 1
        assert "class Child(Parent)" in sig_text

    def test_strip_comments_removes_single_line(self):
        content = "def foo():\n    # this is a comment\n    pass"
        result = PythonExtractor().strip_comments(content)
        assert "#" not in result

    def test_strip_comments_removes_triple_quotes(self):
        content = '"""docstring"""\ndef foo(): pass'
        result = PythonExtractor().strip_comments(content)
        assert '"""' not in result

    def test_clean_signature_removes_decorators(self):
        sig = "@decorator\ndef foo():"
        result = PythonExtractor().clean_signature(sig)
        assert "@decorator" not in result
        assert "def foo()" in result

    def test_clean_signature_removes_colon(self):
        sig = "def foo():"
        result = PythonExtractor().clean_signature(sig)
        assert result == "def foo()"

    def test_clean_signature_normalizes_whitespace(self):
        sig = "def   foo   (  a  ,  b  ):"
        result = PythonExtractor().clean_signature(sig)
        assert result == "def foo ( a , b )"


class TestJSLikeExtractor:
    def test_extract_function_declaration(self):
        content = "function myFunc(a, b) { return a + b; }"
        sig_text, sig_count = JSLikeExtractor().extract(content)
        assert sig_count == 1
        assert "function myFunc(a, b)" in sig_text

    def test_extract_arrow_function(self):
        content = "const add = (x, y) => x + y;"
        sig_text, sig_count = JSLikeExtractor(mode='DEFAULT').extract(content)
        assert sig_count == 1
        assert "const add = (x, y)" in sig_text

    def test_extract_ts_function_mode_a(self):
        content = "function greet(name: string): string { return 'Hello'; }"
        sig_text, sig_count = JSLikeExtractor(mode='A').extract(content)
        assert sig_count == 1
        assert "greet(name: string): string" in sig_text

    def test_extract_ts_class_method_mode_a(self):
        content = '''
class Calculator {
    public add(x: number): void {
        return x + 1;
    }
}
'''
        sig_text, sig_count = JSLikeExtractor(mode='A').extract(content)
        assert sig_count == 1
        assert "class Calculator" in sig_text

    def test_extract_ts_with_export_mode_a(self):
        content = "export function multiply(x: number, y: number): number { return x * y; }"
        sig_text, sig_count = JSLikeExtractor(mode='A').extract(content)
        assert sig_count == 1
        assert "multiply" in sig_text and "export" not in sig_text

    def test_extract_ts_with_export_mode_b(self):
        content = "export function multiply(x: number, y: number): number;"
        sig_text, sig_count = JSLikeExtractor(mode='B').extract(content)
        assert sig_count == 1
        assert "multiply" in sig_text

    def test_extract_ts_interface_mode_a(self):
        content = "export interface User;"
        sig_text, sig_count = JSLikeExtractor(mode='A').extract(content)
        assert sig_count == 1
        assert "interface User" in sig_text

    def test_extract_ts_const_arrow_mode_b(self):
        content = "const add = (a: number, b: number): number => a + b;"
        sig_text, sig_count = JSLikeExtractor(mode='B').extract(content)
        assert sig_count == 1
        assert "const add = (a: number, b: number)" in sig_text

    def test_clean_signature_removes_export(self):
        sig = "export function foo() {"
        result = JSLikeExtractor(mode='DEFAULT').clean_signature(sig)
        assert "export" not in result

    def test_clean_signature_removes_default(self):
        sig = "export default function bar() {"
        result = JSLikeExtractor(mode='DEFAULT').clean_signature(sig)
        assert "default" not in result
        assert "fun bar()" in result

    def test_clean_signature_removes_async(self):
        sig = "async function baz() {"
        result = JSLikeExtractor(mode='DEFAULT').clean_signature(sig)
        assert "async" not in result
        assert "fun baz()" in result

    def test_clean_signature_shortens_function(self):
        sig = "function foo()"
        result = JSLikeExtractor(mode='DEFAULT').clean_signature(sig)
        assert result == "fun foo()"


class TestShaderExtractor:
    def test_extract_struct(self):
        content = "struct UniformData { mat4 model; }"
        sig_text, sig_count = ShaderExtractor().extract(content)
        assert sig_count == 1
        assert "struct UniformData" in sig_text

    def test_extract_fn(self):
        content = "fn calculate(x: f32) -> f32 { return x; }"
        sig_text, sig_count = ShaderExtractor().extract(content)
        assert sig_count == 1
        assert "fn calculate(x: f32)" in sig_text

    def test_extract_fn_with_return_type(self):
        content = "fn process(data: vec3<f32>) -> vec4<f32> { }"
        sig_text, sig_count = ShaderExtractor().extract(content)
        assert sig_count == 1
        assert "fn process(data: vec3<f32>)" in sig_text

    def test_clean_signature_removes_attribute(self):
        sig = "@attribute vec4 position;"
        result = ShaderExtractor().clean_signature(sig)
        assert "attribute" not in result


class TestCLikeExtractor:
    def test_extract_class(self):
        content = "class MyClass { }"
        sig_text, sig_count = CLikeExtractor().extract(content)
        assert sig_count == 1
        assert "class MyClass" in sig_text

    def test_extract_method_with_modifiers(self):
        content = "public static void myMethod(int a) { }"
        sig_text, sig_count = CLikeExtractor().extract(content)
        assert sig_count >= 1
        assert "void myMethod(int a)" in sig_text

    def test_clean_signature_removes_modifiers(self):
        sig = "public static void foo() {"
        result = CLikeExtractor().clean_signature(sig)
        assert "public" not in result
        assert "static" not in result

    def test_clean_signature_removes_braces(self):
        sig = "void bar() {"
        result = CLikeExtractor().clean_signature(sig)
        assert result == "void bar()"

    def test_clean_signature_removes_semicolon(self):
        sig = "int getValue();"
        result = CLikeExtractor().clean_signature(sig)
        assert ";" not in result


class TestRustExtractor:
    def test_extract_simple_function(self):
        content = "fn add(a: i32, b: i32) -> i32 { a + b }"
        sig_text, sig_count = RustExtractor().extract(content)
        assert sig_count == 1
        assert "fn add(a: i32, b: i32)" in sig_text

    def test_extract_public_function(self):
        content = "pub fn calculate(x: f64) -> f64 { x * 2.0 }"
        sig_text, sig_count = RustExtractor().extract(content)
        assert sig_count == 1
        assert "pub fn calculate(x: f64)" in sig_text

    def test_extract_struct(self):
        content = "struct Point { x: f32, y: f32 }"
        sig_text, sig_count = RustExtractor().extract(content)
        assert sig_count == 1
        assert "struct Point" in sig_text

    def test_extract_public_struct(self):
        content = "pub struct User { name: String, age: u32 }"
        sig_text, sig_count = RustExtractor().extract(content)
        assert sig_count == 1
        assert "pub struct User" in sig_text

    def test_extract_enum(self):
        content = "enum Color { Red, Green, Blue }"
        sig_text, sig_count = RustExtractor().extract(content)
        assert sig_count == 1
        assert "enum Color" in sig_text

    def test_extract_trait(self):
        content = "trait Display { fn fmt(&self) -> String; }"
        sig_text, sig_count = RustExtractor().extract(content)
        assert sig_count >= 1
        assert "trait Display" in sig_text

    def test_extract_impl(self):
        content = "impl Point { fn new(x: f32, y: f32) -> Self { Self { x, y } } }"
        sig_text, sig_count = RustExtractor().extract(content)
        assert sig_count >= 1
        assert "impl Point" in sig_text

    def test_extract_impl_for(self):
        content = "impl Display for Point { fn fmt(&self) -> String { } }"
        sig_text, sig_count = RustExtractor().extract(content)
        assert sig_count >= 1
        assert "impl Display for Point" in sig_text

    def test_clean_signature_removes_braces(self):
        sig = "struct Point { x: f32 }"
        result = RustExtractor().clean_signature(sig)
        assert "{" not in result
        assert "}" not in result

    def test_clean_signature_normalizes_whitespace(self):
        sig = "fn   my_func  (  a  ,  b  )"
        result = RustExtractor().clean_signature(sig)
        assert result == "fn my_func(a, b)"


class TestExtractSignatures:
    def test_unsupported_extension_returns_empty(self):
        content = "some content"
        sig_text, sig_count = extract_signatures(content, "xyz")
        assert sig_text == ""
        assert sig_count == 0

    def test_empty_extension_returns_empty(self):
        content = "def foo(): pass"
        sig_text, sig_count = extract_signatures(content, "")
        assert sig_text == ""
        assert sig_count == 0

    def test_python_integration(self):
        content = "def hello(name):\n    print(name)"
        sig_text, sig_count = extract_signatures(content, "py")
        assert sig_count == 1
        assert "def hello(name)" in sig_text

    def test_js_integration(self):
        content = "function test() { }"
        sig_text, sig_count = extract_signatures(content, "js", mode='DEFAULT')
        assert sig_count == 1
        assert "fun test()" in sig_text

    def test_ts_integration(self):
        content = "function test() { }"
        sig_text, sig_count = extract_signatures(content, "ts")
        assert sig_count == 1

    def test_ts_integration_mode_a(self):
        content = "function greet(name: string): string { return 'Hello'; }"
        sig_text, sig_count = extract_signatures(content, "ts", mode='A')
        assert sig_count == 1
        assert "function greet(name: string): string" in sig_text

    def test_ts_integration_mode_b(self):
        content = "function multiply(x: number, y: number): number;"
        sig_text, sig_count = extract_signatures(content, "ts", mode='B')
        assert sig_count == 1
        assert "function multiply" in sig_text

    def test_ts_integration_mode_default(self):
        content = "function add(a, b) { return a + b; }"
        sig_text, sig_count = extract_signatures(content, "ts", mode='DEFAULT')
        assert sig_count == 1
        assert "fun add(a, b)" in sig_text

    def test_ts_complex_file_mode_a(self):
        content = '''
function greet(name: string): string { return "Hello " + name; }
const add = (a: number, b: number): number => a + b;
class Calculator {
    public add(x: number): void { this.value += x; }
}
export function multiply(x: number): number { return x * 2; }
'''
        sig_text, sig_count = extract_signatures(content, "ts", mode='A')
        assert sig_count >= 3
        assert "function greet" in sig_text

    def test_ts_complex_file_mode_b(self):
        content = '''
function greet(name: string): string;
const add = (a: number, b: number): number => a + b;
export function multiply(x: number): number;
'''
        sig_text, sig_count = extract_signatures(content, "ts", mode='B')
        assert sig_count >= 2

    def test_ts_complex_file_mode_default(self):
        content = '''
function greet(name: string): string { return "Hello"; }
const add = (a: number, b: number): number => a + b;
'''
        sig_text, sig_count = extract_signatures(content, "ts", mode='DEFAULT')
        assert sig_count >= 1

    def test_java_integration(self):
        content = "public class Foo { }"
        sig_text, sig_count = extract_signatures(content, "java")
        assert sig_count == 1
        assert "class Foo" in sig_text

    def test_cpp_integration(self):
        content = "void myFunction(int x) { }"
        sig_text, sig_count = extract_signatures(content, "cpp")
        assert sig_count >= 1

    def test_rs_integration(self):
        content = "pub fn calculate(x: f64) -> f64 { x * 2.0 }"
        sig_text, sig_count = extract_signatures(content, "rs")
        assert sig_count == 1
        assert "pub fn calculate(x: f64)" in sig_text

    def test_multiple_signatures(self):
        content = """
def foo():
    pass

def bar():
    pass

class MyClass:
    pass
"""
        sig_text, sig_count = extract_signatures(content, "py")
        assert sig_count == 3


class TestParseHtmlStructure:
    def test_extracts_single_id(self):
        html = '<div id="main">content</div>'
        result = parse_html_structure(html)
        assert ["div#main"] == result

    def test_extracts_multiple_ids(self):
        html = '<div id="header"><span id="title"></span></div>'
        result = parse_html_structure(html)
        assert ["div#header", "span#title"] == result

    def test_no_ids(self):
        html = "<div><span></span></div>"
        result = parse_html_structure(html)
        assert result == []


class TestSupportedExtensions:
    def test_includes_py(self):
        assert "*.py" in SUPPORTED_EXTENSIONS

    def test_includes_js(self):
        assert "*.js" in SUPPORTED_EXTENSIONS

    def test_includes_class_based_languages(self):
        for ext in ["java", "cpp", "c", "cs", "php"]:
            assert f"*.{ext}" in SUPPORTED_EXTENSIONS

    def test_includes_shader_languages(self):
        for ext in ["wgsl", "glsl"]:
            assert f"*.{ext}" in SUPPORTED_EXTENSIONS

    def test_includes_rust(self):
        assert "*.rs" in SUPPORTED_EXTENSIONS


class TestSignatureFromCrateFormat:
    def test_specific_function_header_format(self):
        captured = {}
        def mock_copy(text):
            captured["text"] = text

        with patch("dev_helper.to_clipboard.signature_from_crate.copy_to_clipboard", mock_copy):
            with patch("dev_helper.to_clipboard.signature_from_crate.get_crate_function_signature", return_value="pub fn hash(input: &[u8])-> Hash"):
                with patch("dev_helper.to_clipboard.signature_from_crate.fetch_crate_info", return_value={"num": "1.5.0"}):
                    extract_crate_signatures_to_clipboard([("blake3", "1.5.0", "hash")])

        assert captured["text"] == "=== blake3 1.5.0\n\npub fn hash(input: &[u8])-> Hash"

    def test_latest_function_header_format(self):
        captured = {}
        def mock_copy(text):
            captured["text"] = text

        with patch("dev_helper.to_clipboard.signature_from_crate.copy_to_clipboard", mock_copy):
            with patch("dev_helper.to_clipboard.signature_from_crate.get_crate_function_signature", return_value="pub fn hash(input: &[u8])-> Hash"):
                with patch("dev_helper.to_clipboard.signature_from_crate.fetch_crate_info", return_value={"num": "1.8.5"}):
                    extract_crate_signatures_to_clipboard([("blake3", "*", "hash")])

        assert captured["text"] == "=== blake3 1.8.5\n\npub fn hash(input: &[u8])-> Hash"

    def test_all_signatures_header_format(self):
        captured = {}
        def mock_copy(text):
            captured["text"] = text

        with patch("dev_helper.to_clipboard.signature_from_crate.copy_to_clipboard", mock_copy):
            with patch("dev_helper.to_clipboard.signature_from_crate.get_crate_signatures", return_value=("pub fn foo()-> i32", 1)):
                with patch("dev_helper.to_clipboard.signature_from_crate.fetch_crate_info", return_value={"num": "1.8.5"}):
                    extract_crate_signatures_to_clipboard([("blake3", "*", "")])

        assert captured["text"] == "=== blake3 1.8.5\n\npub fn foo()-> i32"