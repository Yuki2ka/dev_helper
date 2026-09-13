"""Round-trip validation for 2way_explorer (pre/post refactor must match).

Builds a sample tree, runs files_to_text -> text_to_files -> files_to_text and
verifies the second text equals the first, and recreated files match originals.
Usage: python roundtrip_check.py
"""
import filecmp
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import importlib

ex = importlib.import_module("dev_helper.new_file.2way_explorer")  # digit-leading name


def build_sample(base: str) -> list[str]:
    files = {}
    files["README.md"] = "# Sample\n\nhello world\n"
    files["src/main.py"] = "def main():\n    print('hi')\n\nif __name__ == '__main__':\n    main()\n"
    files["src/util/helpers.py"] = "def add(a, b):\n    return a + b\n"
    files["src/util/notes.txt"] = "alpha\r\nbeta\r\ngamma\r\n"   # CRLF on purpose
    files["docs/guide.html"] = "<html><body><p>hi</p></body></html>\n"
    files["docs/style.css"] = "body { color: red; }\n"
    files["data.json"] = '{"a": 1, "b": [2, 3]}\n'
    files["empty_dir_probe/dup1.txt"] = "same content\n"
    files["dup2.txt"] = "same content\n"  # duplicate of dup1.txt (dedup path)
    created = []
    for rel, content in files.items():
        p = os.path.join(base, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        created.append(os.path.abspath(p))
    os.makedirs(os.path.join(base, "empty_dir_probe", "sub"), exist_ok=True)
    return sorted(created)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="rt_")
    try:
        src = os.path.join(tmp, "proj")
        os.makedirs(src)
        files = build_sample(src)

        text1 = ex.files_to_text(src, files, mode_of=lambda p: ex.Action.OPEN)
        assert text1.strip(), "files_to_text produced empty output"

        out1 = os.path.join(tmp, "out1")
        written1 = ex.text_to_files(text1, out1, overwrite=True)
        assert written1, "text_to_files wrote nothing"

        # Round 2: regenerate text from recreated files, must equal text1.
        files2 = sorted(ex._expand_to_files([out1]))
        text2 = ex.files_to_text(out1, files2, mode_of=lambda p: ex.Action.OPEN)
        if text1 != text2:
            print("MISMATCH between text1 and text2", file=sys.stderr)
            print("--- text1 ---\n" + text1, file=sys.stderr)
            print("--- text2 ---\n" + text2, file=sys.stderr)
            return 1

        # Content comparison for non-duplicate files.
        mismatches = []
        for rel in written1:
            src_p = os.path.join(src, rel.replace("/", os.sep))
            out_p = os.path.join(out1, rel.replace("/", os.sep))
            if not os.path.exists(out_p):
                mismatches.append(f"missing: {rel}")
                continue
            with open(src_p, encoding="utf-8") as a, open(out_p, encoding="utf-8") as b:
                ca, cb = ex.normalize_content(a.read(), os.path.splitext(rel)[1].lstrip(".")), b.read()
                if ca != cb:
                    mismatches.append(f"content differs: {rel}")
        if mismatches:
            print("\n".join(mismatches), file=sys.stderr)
            return 1

        # Signature mode should not crash.
        sig_text = ex.files_to_text(src, files, mode_of=lambda p: ex.Action.SIGNATURE)
        assert "main.py" in sig_text

        # _expand/_compact inverse sanity via ActionManager statics.
        data = {p: "open" for p in files}
        compacted = ex.ActionManager._compact(data)
        expanded = ex.ActionManager._expand(compacted)
        missing = [p for p in files if os.path.normpath(p) not in
                   {os.path.normpath(k) for k in expanded}]
        assert not missing, f"expand(compact(data)) lost files: {missing[:3]}"

        print("ROUNDTRIP OK: %d files, text %d chars, %d written" % (len(files), len(text1), len(written1)))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
