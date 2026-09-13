import os
import zipfile

# Workspace root is this script's project root (a:\dev_helper)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SRC = os.path.join(ROOT, "dev_helper")
DST = os.path.join(ROOT, "2way_explorer_bundle.zip")

FILES = [
    (os.path.join(SRC, "common/clipboard.py"),               "dev_helper/common/clipboard.py"),
    (os.path.join(SRC, "common/signature_extractor.py"),     "dev_helper/common/signature_extractor.py"),
    (os.path.join(SRC, "common/text_utils.py"),              "dev_helper/common/text_utils.py"),
    (os.path.join(SRC, "new_file/__init__.py"),              "dev_helper/new_file/__init__.py"),
    (os.path.join(SRC, "new_file/2way_explorer.py"),         "dev_helper/new_file/2way_explorer.py"),
    (os.path.join(SRC, "new_file/new_file_from_clipboard_paste.py"), "dev_helper/new_file/new_file_from_clipboard_paste.py"),
    (os.path.join(SRC, "new_file/new_HTLM_from_clipboard_paste.py"),  "dev_helper/new_file/new_HTLM_from_clipboard_paste.py"),
    (os.path.join(SRC, "new_file/new_img.py"),               "dev_helper/new_file/new_img.py"),
    (os.path.join(SRC, "to_clipboard/__init__.py"),          "dev_helper/to_clipboard/__init__.py"),
    (os.path.join(SRC, "to_clipboard/to_clipboard.py"),      "dev_helper/to_clipboard/to_clipboard.py"),
    (os.path.join(ROOT, "path_args/__init__.py"),            "path_args/__init__.py"),
    (os.path.join(ROOT, "path_args/command_paths.py"),       "path_args/command_paths.py"),
]

with zipfile.ZipFile(DST, "w", zipfile.ZIP_DEFLATED) as zf:
    for src_path, arcname in FILES:
        if os.path.exists(src_path):
            zf.write(src_path, arcname)
        else:
            print(f"WARNING: missing {src_path}")

print("Created", DST)
