# dev_helper

Developer helper - tools for clipboard and file operations.

Problem:
* slow terminal uix for non constant tasks
* gui consume resources (especially web-UI)

Our solution is portable scripts that can be symlinked or copy in any folder to do tasks using cwd, clipboard or selected folders as input and/or output.

Typical and most useful use case:
1. copy markdown from from any LLM chat
2. run new_file_from_clipboard.py 
3. you got all listed files with (usually) proper extensions

Quick start: copy something not big to clipboard and run any script from `_standalone_dst_do_not_edit` folder. Or copy any script from standalone folder to your project and run it.

Typical path resolution priority: **CLI Args** $\rightarrow$ **stdin** $\rightarrow$ **Clipboard** $\rightarrow$ **Constant** $\rightarrow$ **CWD**
So by default it will create files in folder where you put the script.

100% offline. Does not contain any web-api or online services. But can connect to some local servers, like ComfyUI, LM-studio.

! Note: these scripts may change or remove files based on links in your clipboard. Sometimes without additional confirmations.

## Main scripts

| Script | Description |
|---------|-------------|
| `to_clipboard` | You copy N files/folders OR path list. Script copy all content and/or ASCII file tree to clipboard with file names. Next e.g. you can paste it to Qwen chat |
| `new_file_from_clipboard` | You copy whole GLM answer and script will save it as md and also spawn N files with (i hope) proper extensions for ech code block. OR you copy single code block and script save it with proper extension |
| `new_HTLM_from_clipboard` | You copy text from web page. Script save formatted HTML. Similar to justpaste.it CSS usually lost. |
| `combine_img` | Combine multiple images into a grid layout e.g. 3 columns, or 1 row or fill best. choose size, auto-size, sort by name or time. |
| `new_img` | Create numbered placeholder color images with grid overlay. Useful for UI mockups and testing. |
| `new_audio` | Generate sine wave audio files, choose req, notes or pentatonic scale quantization. wav, opus. |
| `new_audio_sweep` | Generate frequency sweep audio files. Can quantize to pentatonic scale. |
| `new_voice` | Create numbered speech files using text-to-speech (pytttsx3). wav, opus. |
| `random_password` | Generate a random password and copy it to clipboard. |
| `apply_LF_ending` | all ending linux type to save size. |
| `signature_extractor.py` | produce most compact signature with types, if types present and necessary. ( build-in to_clipboard.py apply_to_clipboard.py ) |
| `signature_from_crate.py` | list with newline separators like <br>blake3/hash<br>crossbeam<br> will copy to clipboard list of signatures blake3/hash, then all from crossbeam. use signature_extractor_from_crate.py for input name-> output list|
| `check_hash.py` | Check file integrity via MD5 hashes; create/update .md5 sidecar files for verification. |
| `to_clipboard_ascii_path_tree.py` | copy only paths file tree without content. This also demonstrate how build create copies with diferent parameters without code duplication in source. |

All scripts should use exactly same `command_paths.py` to get and resolve file lists as all others in this project.


## Utility scripts

| Script | Description |
|---------|-------------|
| `build.py` | Creates self-contained script bundles that you can run .py from any folder. No code duplication in source. And creates `_terminal_scripts_dst_do_not_edit` - examples of commands for case if library installed via pip |
| `build_more.py` | Settings to build variants of same scripts. |
| `clean.py` | Remove `__pycache__`, `.pytest_cache`, and standalone folder contents. Run to clean build artifacts. |



## Requirements

- Python >= 3.9
- Optional: `pywin32` (Windows) - for clipboard operations
- Optional: `pyperclip` - cross-platform clipboard support
- Optional: `pathspec` - for .gitignore/.hgignore filtering in to_clipboard
- Optional: `Pillow` (PIL) - for image operations (combine_img, new_img)
- Optional: `pytttsx3` - for voice generation (new_voice)
- Optional: `numpy`, `pydub` - for audio sweep generation (new_audio_sweep)

## License

MIT
