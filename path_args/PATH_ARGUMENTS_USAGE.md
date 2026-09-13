# Shared Python path argument system - path_args

Use `command_paths.py` in every command module. It gives one resolution chain:

## Path resolution order

1. **CLI args** (`source`, `destination`, `path`, etc. — configured per command).
2. **sys.stdin** — newline-separated paths piped/redirected into the command.
3. **Clipboard** file/directory references (`paths_from_clipboard()`).
4. **Hardcoded** per-script constant, but only if it exists.
5. **Current working directory**.

Directories are created by the resolver according to a generic `create` mode. The resolver does **not** know whether the command is a reader or a writer.

## Input text resolution order

For commands that need *text content* (not file paths):

1. **CLI args** (e.g. `--text`, `--content`)
2. **sys.stdin** (piped/redirected input)
3. **Clipboard** text
4. **Hardcoded** constant

## Output

- File output is always written to the resolved destination.
- **`--stdout`** flag: when enabled, the written/processed text is also printed to stdout (useful for piping to other commands).

Existing file overwrite is a per-script constant:

```py
DEFAULT_LOCATION = ""          # empty/non-existing => cwd
OVERWRITE_EXISTING = False     # default policy
```

## Common parser

```py
import argparse
from command_paths import add_common_path_arguments, resolve_paths

DEFAULT_LOCATION = ""
OVERWRITE_EXISTING = False

def parse_args():
    parser = argparse.ArgumentParser()
    add_common_path_arguments(parser)
    return parser.parse_args()

args = parse_args()
location = resolve_paths(args, constant=DEFAULT_LOCATION, create="auto")
print(location.origin, location.paths)
```

## Reader-like command: file/dir to clipboard or LLM

```py
import argparse
import pyperclip
from command_paths import add_common_path_arguments, iter_existing_files, resolve_paths

DEFAULT_SOURCE = ""

def parse_args():
    parser = argparse.ArgumentParser(description="Copy concatenated text from files/dirs")
    add_common_path_arguments(parser, destination=False)
    return parser.parse_args()

def main():
    args = parse_args()

    # Same resolver. We only choose which argument names matter here.
    # stdin paths are checked between args and clipboard automatically.
    src = resolve_paths(
        args,
        arg_names=("source", "sources", "src", "input", "inputs", "path", "paths"),
        constant=DEFAULT_SOURCE,
        create="auto",
    )

    chunks = []
    for file in iter_existing_files(src.paths, recursive=True):
        try:
            chunks.append(f"\n\n===== {file} =====\n")
            chunks.append(file.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            chunks.append(f"\n[Could not read {file}: {exc}]\n")

    pyperclip.copy("".join(chunks))
    print(f"Copied text from {len(list(iter_existing_files(src.paths)))} file(s); source={src.origin}")

if __name__ == "__main__":
    main()
```

### Piping paths via stdin

```bash
# Pipe a directory path into the command
echo "/home/user/project/src" | python copy_concat.py

# Or multiple paths
printf "/path/one\n/path/two\n" | python copy_concat.py
```

## Writer-like command: create file from clipboard text

```py
import argparse
import datetime
import re
import pyperclip
from command_paths import add_common_path_arguments, choose_output_file, resolve_paths, resolve_input_text

DEFAULT_DESTINATION = ""
OVERWRITE_EXISTING = False

def parse_args():
    parser = argparse.ArgumentParser(description="Create a file from clipboard text")
    add_common_path_arguments(parser, source=False)
    parser.add_argument("--stdout", action="store_true", help="Also print to stdout")
    return parser.parse_args()

def detect_file_type(text: str) -> tuple[str, str]:
    timestamp = datetime.datetime.now().strftime("%Y.%m.%d.%H.%M.%S")
    if re.search(r"<(html|body)[^>]*>", text, re.IGNORECASE):
        return "index", "html"
    if text.startswith("import"):
        return timestamp, "py"
    if text.startswith("using") or "namespace" in text:
        return timestamp, "cs"
    if text.startswith(("function", "const ", "let ", "var ")) or "console.log" in text:
        return "script", "js"
    if re.search(r"^\s*(body|html|div|\.|#|\@|\:)", text, re.MULTILINE):
        return "styles", "css"
    if text.startswith("#include"):
        return timestamp, "cpp"
    if re.search(r"^(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)", text, re.IGNORECASE):
        return timestamp, "sql"
    return timestamp, "txt"

def main():
    args = parse_args()

    # Resolve input text: args -> stdin -> clipboard
    input_text = resolve_input_text(args, arg_names=("text",))
    if not input_text.text.strip():
        print("No text found (args, stdin, or clipboard). No file created.")
        return

    dest = resolve_paths(
        args,
        arg_names=("destination", "destinations", "dest", "dst", "output", "out", "path", "paths"),
        constant=DEFAULT_DESTINATION,
        create="auto",
    )
    stem, suffix = detect_file_type(input_text.text)
    out_file = choose_output_file(dest, default_stem=stem, default_suffix=suffix, overwrite=OVERWRITE_EXISTING)
    out_file.write_text(input_text.text, encoding="utf-8", newline="\n")

    if getattr(args, "stdout", False):
        print(input_text.text)

    print(f"Created: {out_file} (destination from {dest.origin}, text from {input_text.origin})")

if __name__ == "__main__":
    main()
```

### Usage examples

```bash
# Write piped content to a file (stdin for both text and destination)
echo "hello world" | python write_text_file.py -d /tmp/out/

# Text from stdin, destination from args
cat log.txt | python write_text_file.py -d /tmp/ --name log --ext txt

# Both text and destination from args
python write_text_file.py -d /tmp/note.txt --text "hello"

# With --stdout to also print the result
echo "hello" | python write_text_file.py -d /tmp/ --stdout
```

## Writer-like command: image output path

```py
import argparse
from command_paths import add_common_path_arguments, choose_output_file, resolve_paths

DEFAULT_DESTINATION = ""
OVERWRITE_EXISTING = False

def parse_args():
    parser = argparse.ArgumentParser(description="Create numbered images")
    add_common_path_arguments(parser, source=False)
    parser.add_argument("-n", "--number", type=int, default=1)
    parser.add_argument("-f", "--format", default="webp")
    return parser.parse_args()

def main():
    args = parse_args()
    dest = resolve_paths(
        args,
        arg_names=("destination", "destinations", "dest", "dst", "output", "out", "path", "paths"),
        constant=DEFAULT_DESTINATION,
        create="auto",
    )

    # If dest is a directory, this becomes image.webp / image_001.webp etc.
    # If dest is file.webp, it uses that exact file or file_001.webp when overwrite=False.
    out_file = choose_output_file(
        dest,
        default_stem="image",
        default_suffix=args.format,
        overwrite=OVERWRITE_EXISTING,
    )
    print(out_file)

if __name__ == "__main__":
    main()
```

## Commands with both source and destination

Use the same function twice; only change `arg_names` and constants:

```py
src = resolve_paths(args, arg_names=("source", "src", "input"), constant=DEFAULT_SOURCE, create="auto")
dst = resolve_paths(args, arg_names=("destination", "dest", "output"), constant=DEFAULT_DESTINATION, create="auto")
```

This keeps path resolution deduplicated while avoiding any reader/writer branching inside the resolver.
