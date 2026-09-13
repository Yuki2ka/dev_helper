from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Type, Dict, Optional, Literal

# === SETTINGS START ===
SUPPORTED_EXTENSIONS = [
    "*.py", "*.js", "*.ts", "*.jsx", "*.tsx",
    "*.html", "*.wgsl", "*.glsl", "*.vert", "*.frag",
    "*.java", "*.cpp", "*.c", "*.h", "*.go", "*.rs", "*.cs", "*.php"
]
# === SETTINGS END ===

class SignatureExtractor(ABC):
    """Base class for language-specific signature extraction."""

    @abstractmethod
    def strip_comments(self, content: str) -> str: 
        """Remove comments specific to the language."""
        ...

    @abstractmethod
    def clean_signature(self, sig: str) -> str: 
        """Normalize the extracted signature string."""
        ...

    def get_patterns(self) -> list[str]: 
        """Return regex patterns for signatures. Override in subclasses."""
        return []

    def extract(self, content: str) -> tuple[str, int]:
        """Core extraction pipeline for Regex-based extractors."""
        cleaned_content = self.strip_comments(content)
        patterns = self.get_patterns()
        
        if not patterns:
            return ("", 0)
            
        combined_pattern = '|'.join(patterns)
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
    def strip_comments(self, content: str) -> str:
        content = re.sub(r'"""[\s\S]*?"""', '', content) # Triple quotes
        content = re.sub(r"'''[\s\S]*?'''", '', content) # Triple quotes
        return re.sub(r'#.*$', '', content, flags=re.MULTILINE)

    def get_patterns(self) -> list[str]:
        return [
            r'\bdef\s+\w+\s*\(.*?\)',          # def func(...)
            r'\bclass\s+\w+(?:\s*\([^)]*\))?',  # class MyClass(...)
        ]

    def clean_signature(self, sig: str) -> str:
        sig = re.sub(r'@\w+(?:\(.*?\)|)', '', sig) # Remove decorators
        sig = re.sub(r'\s+', ' ', sig).strip()
        return sig.rstrip(':')


class JSLikeExtractor(SignatureExtractor):
    """
    Handles JS, TS, JSX, TSX. 
    Modes: 'A' (State Machine/Strict - Default), 'B' (Semicolon-based), 'DEFAULT' (Legacy Regex)
    """
    def __init__(self, mode: Literal['A', 'B', 'DEFAULT'] = 'A'):
        self.mode = mode

    def strip_comments(self, content: str) -> str:
        # A & B require deeper JSDoc stripping to avoid regex noise
        if self.mode in ('A', 'B'):
            content = re.sub(r'/\*\*[\s\S]*?\*/', '', content) 
        content = re.sub(r'/\*[\s\S]*?\*/', '', content)
        return re.sub(r'//.*$', '', content, flags=re.MULTILINE)

    def clean_signature(self, sig: str) -> str:
        if self.mode == 'A':
            sig = re.sub(r'\bexport\b', '', sig)
            sig = sig.replace("declare ", "")
            sig = re.sub(r'<[^>]+>', '', sig)
            sig = re.sub(r'\s+', ' ', sig)
            sig = re.sub(r'\{[^}]*\}', '', sig)
            sig = re.sub(r'\s+', ' ', sig)
            sig = sig.rstrip('&|=:;{ ').strip()
            if any(bad in sig for bad in ['`', '</span>', 'LlamaText', '/**']):
                if not sig.startswith(('export', 'class', 'function', 'public', 'type', 'interface', 'const')):
                    return ""
            return sig
        
        elif self.mode == 'B':
            sig = re.sub(r'\bexport\b', '', sig)
            sig = sig.replace("declare ", "")
            sig = re.sub(r'\s+', ' ', sig)
            sig = re.sub(r'\{[^}]*\}', '', sig)
            sig = re.sub(r'\{[^}]*\}', '', sig)
            sig = sig.rstrip('= ').strip()
            return sig
        
        else:
            sig = re.sub(r'\b(export|default|async)\b', '', sig)
            sig = re.sub(r'@\w+(?:\(.*?\)|)', '', sig)
            sig = re.sub(r'\s+', ' ', sig)
            sig = re.sub(r'\{[^}]*\}', '', sig)
            sig = sig.replace(';', '')
            sig = re.sub(r'\bfunction\b', 'fun', sig)
            return sig.strip()

    def get_patterns(self) -> list[str]:
        if self.mode == 'B':
            # Pattern from sigB.py: matches until semicolon or keyword boundary
            return [r'((?:export\s+)?(?:class|function|interface|type|enum|const)\s+[^;]+|public\s+[^;]+)']
        return [
            r'\bfunction\s+\w+\s*\(.*?\)',
            r'\bconst\s+\w+\s*=\s*\(.*?\)\s*=>',
            r'\bclass\s+\w+',
            r'^\s*(?:async\s+)?\b(?!if|for|while|switch|catch\b)\w+\s*\(.*?\)\s*\{',
        ]

    def extract(self, content: str) -> tuple[str, int]:
        if self.mode == 'A':
            # State Machine logic
            content = self.strip_comments(content)
            start_pattern = re.compile(
                r'^\s*(export\s+(?:class|function|interface|type|enum|const)\b|class\s+|public\s+|const\s+\w+\s*:|function\s+)'
            )
            
            lines = content.splitlines()
            clean_lines = []
            current_sig = ""
            brace_count = 0
            paren_count = 0
            
            for line in lines:
                stripped = line.strip()
                if not stripped: continue
                
                if not current_sig:
                    if start_pattern.match(line):
                        current_sig = stripped
                    else:
                        continue
                else:
                    current_sig += " " + stripped
                
                brace_count += stripped.count('{') - stripped.count('}')
                paren_count += stripped.count('(') - stripped.count(')')
                
                is_complete = False
                if ';' in stripped and brace_count <= 0 and paren_count <= 0:
                    is_complete = True
                elif brace_count == 0 and paren_count == 0:
                    is_complete = True
                
                if is_complete or brace_count < 0 or paren_count < 0:
                    cleaned = self.clean_signature(current_sig)
                    # Exact filters
                    if cleaned and not cleaned.startswith("export *") and cleaned not in ["export type", "export interface", "export class"]:
                        clean_lines.append(cleaned)
                    current_sig, brace_count, paren_count = "", 0, 0
            
            return ("\n".join(clean_lines), len(clean_lines))
        
        # Unified pipeline for B & DEFAULT with exact original filters preserved
        cleaned_content = self.strip_comments(content)
        patterns = self.get_patterns()
        combined_pattern = '|'.join(patterns)
        pattern = re.compile(combined_pattern, re.MULTILINE | re.DOTALL)
        
        clean_lines = []
        for match in pattern.finditer(cleaned_content):
            sig = match.group(0).strip()
            
            # Mode B pre-filter (from original sigB.py)
            if self.mode == 'B':
                if sig in ["export", "export *", "export default"] or sig.startswith("export * from"):
                    continue
            
            cleaned_sig = self.clean_signature(sig).rstrip('{').strip()
            
            # Mode B post-filter (from original sigB.py)
            if self.mode == 'B' and (not cleaned_sig or cleaned_sig in ["export type", "export interface"]):
                continue
                
            if cleaned_sig:
                clean_lines.append(cleaned_sig)
        
        return ("\n".join(line for line in clean_lines if line.strip()), len(clean_lines))


class ShaderExtractor(SignatureExtractor):
    def strip_comments(self, content: str) -> str:
        content = re.sub(r'/\*[\s\S]*?\*/', '', content)
        return re.sub(r'//.*$', '', content, flags=re.MULTILINE)

    def get_patterns(self) -> list[str]:
        return [
            r'\bstruct\s+\w+',
            r'\bfn\s+\w+\s*\(.*?\)(?:\s*->\s*[\w<>]+)?',
            r'\b(?:void|vec[234]|mat[234]|float|int)\s+\w+\s*\(.*?\)',
            r'\b(?:uniform|var)\s+[^;\{]+',
        ]

    def clean_signature(self, sig: str) -> str:
        sig = re.sub(r'\b(attribute|uniform|varying)\b', '', sig)
        sig = re.sub(r'@\w+(?:\(.*?\)|)', '', sig)
        sig = re.sub(r'\s+', ' ', sig).strip()
        return sig


class CLikeExtractor(SignatureExtractor):
    """Handles Java, C++, C#, PHP, etc."""
    def strip_comments(self, content: str) -> str:
        content = re.sub(r'/\*[\s\S]*?\*/', '', content)
        return re.sub(r'//.*$', '', content, flags=re.MULTILINE)

    def get_patterns(self) -> list[str]:
        return [
            r'\bclass\s+\w+',
            r'\b(?:public|private|protected|static|final)\s+.*?\s+\w+\s*\(.*?\)\s*\{?',
            r'\b(?:void|int|float|double|bool|char|long|byte)\s+\w+\s*\(.*?\)\s*\{?',
        ]

    def clean_signature(self, sig: str) -> str:
        sig = re.sub(r'\b(public|private|protected|static|final)\b', '', sig)
        sig = re.sub(r'\s+', ' ', sig)
        sig = sig.replace(';', '').rstrip('{').strip()
        return sig


class RustExtractor(SignatureExtractor):
    def strip_comments(self, content: str) -> str:
        content = re.sub(r'/\*[\s\S]*?\*/', '', content)
        return re.sub(r'//.*$', '', content, flags=re.MULTILINE)

    def get_patterns(self) -> list[str]:
        return [
            r'\b(?:pub\s+)?fn\s+\w+\s*\(.*?\)(?:\s*->\s*[^{]+)?',
            r'\b(?:pub\s+)?struct\s+\w+(?:\s*{[^}]*})?',
            r'\b(?:pub\s+)?enum\s+\w+(?:\s*{[^}]*})?',
            r'\b(?:pub\s+)?trait\s+\w+',
            r'\b(?:pub\s+)?impl\s+(?:\w+(?:<[^>]+>)?\s+for\s+)?\w+(?:<[^>]+>)?',
        ]

    def clean_signature(self, sig: str) -> str:
        sig = re.sub(r'\s+', ' ', sig).strip()
        sig = re.sub(r'\s*\(\s*', '(', sig)
        sig = re.sub(r'\s*\)\s*', ')', sig)
        sig = re.sub(r'\s*,\s*', ', ', sig)
        sig = re.sub(r'\s*\{[^}]*\}\s*', ' ', sig)
        return sig.strip()


# === ROUTING & DISPATCHER ===

# Lazy Loading Map: We store the CLASS, not the INSTANCE.
# This saves memory as extractors are created only when needed.
_EXTRACTOR_MAP: Dict[str, Type[SignatureExtractor]] = {
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
    script_blocks = re.findall(r'<script[\s\S]*?>([\s\S]*?)</script>', content)
    js_content = "\n".join(script_blocks) if script_blocks else ""
    return JSLikeExtractor(mode='A').extract(js_content)

def extract_signatures(content: str, extension: str, mode: Optional[Literal['A', 'B', 'DEFAULT']] = None) -> tuple[str, int]:
    """Main entry point. Routes to the correct extractor by file extension."""
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
        # Allow overriding mode for testing, default is 'A' as recommended
        init_kwargs = {"mode": mode} if mode else {}
        extractor_instance = extractor_class(**init_kwargs)
        return extractor_instance.extract(content)

    return ("", 0)

# === UTILITY ===

def parse_html_structure(html_content: str) -> list[str]:
    """Extract element signatures from HTML (tag#id format)."""
    pattern = r'<([a-zA-Z0-9]+)\s+[^>]*id=["\']([^"\']+)["\'][^>]*>'
    return [f"{m.group(1)}#{m.group(2)}" for m in re.finditer(pattern, html_content)]
