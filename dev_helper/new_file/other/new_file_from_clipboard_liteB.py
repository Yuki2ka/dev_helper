import sys
from pathlib import Path

_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

import datetime
import re
import ast
import subprocess
import shutil
import json
import os
import argparse

from dev_helper.common.clipboard import paste_clipboard

try:
    from path_args import resolve_paths
except ImportError:
    resolve_paths = None  # type: ignore[assignment]

def create_file(clipboard_text, file_extension, output_dir=None):
    """Creates a file with a consistent prefix, timestamp, and extension."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"clip_{timestamp}.{file_extension}"

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, filename)
    else:
        filepath = filename

    with open(filepath, 'w', encoding='utf-8') as file:
        file.write(clipboard_text)
    print(f"Created: {filepath} ({file_extension.upper()})")

def is_valid_html(text):
    """Checks if the payload contains valid, non-prose HTML tags."""
    html_signature = r'<!DOCTYPE\s+html|</\s*\w+\s*>|<\s*\w+\s+[^>]*class=|=[’”"].*?[’”"]\s*/?>'
    if re.search(html_signature, text, re.IGNORECASE):
        words = len(re.findall(r'\w+', text))
        tags = len(re.findall(r'<[^>]+>', text))
        if tags > 0 and (words / tags) < 30:
            return True
    return False

def is_valid_python(text):
    """Uses Python's core compiler to check for actual executable syntax."""
    try:
        ast.parse(text)
        # Filters out single raw words or plain text lines that happen to pass AST
        if any(word in text for word in ['def ', 'import ', 'print', '=', '[', '{', 'if ', ':', 'class ']):
            return True
    except SyntaxError:
        pass
    return False

def is_valid_javascript(text):
    """Uses Node's V8 VM module to test syntax compiling without executing."""
    if not shutil.which("node"):
        return False
        
    # Using 'vm.Script' compiles the code cleanly without executing it.
    # It catches python syntax (like indentation blocks, 'def', 'print') as syntax errors.
    js_checker = """
    const vm = require('vm');
    try {
        new vm.Script(`%s`);
        console.log('VALID');
    } catch(e) {
        console.log('INVALID');
    }
    """ % text.replace('`', '\\`').replace('$', '\\$')
    
    try:
        result = subprocess.run(
            ["node", "-e", js_checker],
            capture_output=True,
            text=True,
            timeout=1
        )
        if "VALID" in result.stdout.strip():
            # Ensure it contains actual JS syntax symbols so plain sentences fail
            symbols = len(re.findall(r'[;{}()\[\]=+\-*/]', text))
            return symbols >= 2
    except Exception:
        pass
    return False


def is_valid_json(text):
    """Strictly checks if string is valid JSON formatting."""
    try:
        json.loads(text)
        return True
    except ValueError:
        return False

def detect_language(text):
    clean_text = text.strip()
    if not clean_text: 
        return None
    
    # 1. STRICHEST STRUCTURES FIRST (JSON / HTML)
    if is_valid_json(clean_text):
        return "json"
        
    if is_valid_html(clean_text):
        return "html"
        
    # 2. LANGUAGE PARSERS NEXT
    if is_valid_python(clean_text):
        return "py"
        
    if is_valid_javascript(clean_text):
        return "js"
    
    # 3. LOOSE STRUCTURAL FALLBACKS
    if re.search(r'^\s*(body|html|div|\.[\w-]+|#[\w-]+)\s*\{', clean_text, re.MULTILINE): 
        return "css"
        
    return "txt"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="Output directory")
    args = parser.parse_args()

    if resolve_paths is not None:
        resolved = resolve_paths(args, arg_names=("path",), fallback_to_cwd=True)
        output_dir = str(resolved.first) if resolved.origin != "cwd" else None
    else:
        output_dir = args.path if (args.path and os.path.isdir(args.path)) else None

    clipboard_text = paste_clipboard()
    normalized_text = clipboard_text.replace("\r\n", "\n").replace("\r", "\n")

    if not normalized_text.strip():
        print("Clipboard is empty.")
    else:
        file_ext = detect_language(normalized_text)
        create_file(normalized_text, file_ext, output_dir)

if __name__ == "__main__":
    main()
