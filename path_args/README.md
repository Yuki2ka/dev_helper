Good to have all modules (commands) use same system.

# Shared Python path argument system - path_args

## Resolution priority chain

```
1. CLI arguments (-s, -d, positional)
2. sys.stdin (piped/redirected paths or text)
3. OS clipboard (file references or text)
4. Hardcoded script constant (only if exists)
5. Current working directory (fallback)
```

OVERWRITE_EXISTING is a per-script constant (default `False`).
Missing directories are always created.

## Key design decisions

- The resolver does **not** know whether the command is a reader or writer.
- `resolve_paths()` returns file/directory locations (args -> stdin -> clipboard -> constant -> cwd).
- `resolve_input_text()` returns text content (args -> stdin -> clipboard -> constant).
- **`--stdout`** flag: opt-in stdout output for piping results to other commands.
- stdin is checked **second**, after args but before clipboard.

## Example use cases

- **Reader**: copy file, LLM translation — `args -> stdin -> clipboard -> constant -> cwd`
- **Writer**: create file from clipboard text — `args -> stdin -> clipboard -> constant`
- **Piping**: `echo /path/to/dir | python copy_concat.py` reads from stdin paths
- **Composition**: `echo "hello" | python write_text_file.py -d /tmp/ --stdout | wc -c`
