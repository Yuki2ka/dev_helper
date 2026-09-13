# dev_helper

```bash
pip install dev_helper
pip install dev_helper[win]
```

## Commands

| Command | Description |
|---------|-------------|
| `to_clipboard` | Copy files/folders to clipboard |
| `new_file_from_clipboard` | Create files from clipboard (Magika) |
| `new_file_from_clipboard_lite` | Create files from clipboard (simple detection) |
| `new_file_from_clipboard_liteB` | Create files from clipboard (strict validation) |
| `new_html_from_clipboard` | Save HTML rich text from clipboard |

## Python API

```python
from to_clipboard import (
    concatenate_files,
    get_paths_from_clipboard,
    generate_ascii_tree,
    load_gitignore_spec,
    load_hgignore_spec,
    is_hg_ignored,
    is_binary,
    normalize_indentation_to_tabs,
)

from new_file import (
    new_file_from_clipboard,
    new_file_from_clipboard_lite,
    new_file_from_clipboard_liteB,
    new_html_from_clipboard,
    create_file_lite,
    create_file_liteb,
    detect_language,
    is_valid_html,
    is_valid_json,
    is_valid_python,
    is_valid_javascript,
)
```

MIT
