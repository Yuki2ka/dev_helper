# Bundle Source Files for LLM Delivery

You are packaging a project's source files for delivery to an LLM service.
The output must be a zip archive that contains **only the files necessary to
understand, run, and extend the target feature or entrypoint**, with full
folder structure preserved.

---

## Methodology

### 1. Identify the entrypoint
Find the file the user wants to send. This is usually a script, module, or
document explicitly named by the user (for example `2way_explorer.py`,
`main.ts`, `app.go`, `index.js`).

### 2. Trace runtime dependencies only
Follow the **actual runtime call graph** from the entrypoint:

- Static `import` / `require` / `using` / `include` statements
- Dynamic imports that execute at module load time
- Side effects in `__init__.py`, module top-level code, or package
  initializers that run because the entrypoint or its transitive imports
  cause the package to load

Do **not** trace:
- Test files / test directories
- Type stub files (`*.d.ts`) unless the language runtime requires them
- Documentation, README, CI configs, lockfiles
- Unused submodules listed in package initializers that never execute
  because the package itself is never imported

### 3. Distinguish "import name" from "execution"
Many languages load package / namespace initializers lazily:

- **Python**: `import a.b.c` executes `a/__init__.py` and `a/b/__init__.py`
  automatically. The only way an `__init__.py` does NOT run is if the
  runtime path never crosses it — which is rare. Verify execution, not
  just names.
- **Node / TypeScript**: `index.js` and `package.json` `main` fields only
  load when the package is resolved as a whole.
- **Go**: `init()` functions only run when the package is imported as a
  dependency in a build graph.
- **Rust**: `mod` declarations only pull in `mod.rs` / `lib.rs` if
  the parent module is compiled.

### 4. Inspect for side effects at import time
Before including a file, check whether top-level code (outside functions)
runs because of an import. If a file only defines functions / classes /
types and contains no top-level side effects, it is safe to thin-include
only what is actually called.

### 5. Build the closure
Collect every file that:
- Defines a symbol the entrypoint or its transitive imports reference at
  runtime, OR
- Executes side effects that occur because of those imports

Remove every file that does not satisfy at least one of those conditions.

### 6. Create the zip
- Preserve relative folder paths inside the archive, including the package
  root folder
- Use ZIP_DEFLATED compression
- Exclude bytecode / build artifacts / caches (`__pycache__`, `node_modules`,
  `.next`, `dist`, `target`, `vendor`, etc.)
- Validate the archive contents against the dependency list after creation

---

## Output Requirements

- Zip path: `{project_root}/bundle/{entrypoint_stem}_bundle.zip`
  (create `bundle/` if it does not exist)
- The receiving environment will extract the zip and run the entrypoint
  from the extraction root
- Do not include the zip archive itself inside the archive
- Do not include any secrets, credentials, `.env` files, or keys

---

## Python Pitfalls

### 1. `__init__.py` always executes on submodule import
```python
# dev_helper/new_file/__init__.py
from .new_img import main as new_img        # executes on import
```
When any code executes `import dev_helper.new_file.new_file_from_clipboard_paste`,
Python runs both `dev_helper/__init__.py` and `dev_helper/new_file/__init__.py`.
The sibling imports inside those initializers run too, making them real
runtime dependencies even though the package itself is never imported
directly. A static scanner that only checks `import` statements in the
entrypoint file will miss these.

### 2. Package-root structure must be preserved
Files with runtime path injection assume a fixed layout:
```python
_resolved = Path(__file__).resolve()
sys.path.insert(0, str(_resolved.parents[2]))
```
This hardcodes "go up two directories from this file to find the package
root." Flattening the zip — placing `2way_explorer.py` at the archive root
instead of under `dev_helper/new_file/` — shifts `parents[2]` to a
directory with no `dev_helper` subpackage, causing `ModuleNotFoundError`.
Always preserve the package folder hierarchy in the archive.

### 3. `sys.modules` bypasses `__init__.py` re-export shadowing
```python
import dev_helper.new_file.new_file_from_clipboard_paste
nf = sys.modules["dev_helper.new_file.new_file_from_clipboard_paste"]
```
`__init__.py` may rebind the submodule name to a function (e.g. `main`).
`as nf` would normally capture that function, but the code reaches through
`sys.modules` to grab the real module object. Import-rewrite bundlers that
normalize `import x.y.z as foo` may silently change semantics. Verify the
result still resolves the same object.

### 4. Module-level side effects can cross module boundaries
```python
def apply_settings():
    tc.CONVERT_TO_LF = CONVERT_TO_LF
    nf.CONVERT_TO_TABS = CONVERT_TO_TABS
apply_settings()
```
This mutates globals in `to_clipboard` and `new_file_from_clipboard_paste`
at import time. If the receiving environment imports modules out of order
or caches partially initialized modules, those side effects are lost.
Include any module whose globals are mutated this way; do not rely on
import ordering.

### 5. `None` as an intentional enum value
```python
cycle = (None, Action.OPEN, Action.SIGNATURE)
```
`None` is stored as "no mode / skip" in config files and dictionaries.
It is not equivalent to "unset" or falsy — it is a sentinel distinct from
`Action.OPEN` and `Action.SIGNATURE`. Do not strip or normalize it during
text/JSON transformations.

### 6. Lazy imports inside functions are safe to omit if unreachable
```python
def _make_root():
    try:
        from tkinterdnd2 import TkinterDnD
        ...
    except Exception:
        import tkinter as tk
```
Third-party imports inside `try/except` or function bodies only run if
that branch executes. If the entrypoint code path never reaches them,
they are not runtime dependencies. However, top-level optional imports
in other modules (e.g. `win32clipboard` at module top) are different —
they execute on import and may fail on other platforms. Verify whether
they are top-level or inside a function.

---

## Cross-Language Pitfalls

### Package initializers are conditional
Files like `__init__.py`, `index.js`, `package.json`, `mod.rs`, or
`__init__.pyi` are easy false positives. A static scanner sees sibling
imports and includes them; the runtime may never load the initializer
because the entrypoint imports submodules by fully-qualified name or
relative path instead.

### Multi-language projects
In polyglot repos (Python backend + TypeScript frontend + SQL migrations),
only include files from the language and layer the entrypoint belongs to.
Do not pull in the whole repo just because paths share a prefix.

---

## Verification Checklist

Before finalizing the zip:

- [ ] Every imported module in the entrypoint maps to exactly one file
      inside the archive
- [ ] No file inside the archive fails to resolve at least one symbol used
      at runtime by the closure
- [ ] Folder hierarchy inside the zip preserves the package root
      (e.g. `dev_helper/new_file/2way_explorer.py`, not flat files)
- [ ] No `__pycache__`, `.pyc`, `node_modules`, `dist`, `target`, or
      similar build artifacts are present
- [ ] No secrets, keys, credentials, or environment files are present
- [ ] The number of files in the archive matches the documented dependency
      closure size
- [ ] Extracted archive can be imported / run from its root without
      path hacks or additional environment setup
