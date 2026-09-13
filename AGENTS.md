# Agent instructions for dev_helper

##
build.py - build standalone. ensure script become true standalone (without deps of this project. but can deps on external pip lib) and can work from any folder.

do not edit content of /_standalone_dst_do_not_edit/

if script need path or clipboard as command argument - make it compatible with /path_args/command_paths.py

## Lint/Check Commands
- `python -m py_compile <file>` - syntax check
- `python -m ruff check <file>` - linting (if ruff installed)
- `python -m mypy <file> --ignore-missing-imports` - type checking (if mypy installed)