from __future__ import annotations

import json
import math
import mimetypes
import os
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid
import random
import copy
import re
import datetime
import websocket #pip install websocket-client
from pathlib import Path
from typing import Any

try:
    from PIL import Image  # used for combining multiple input images into one grid
except ImportError:
    Image = None

from command_paths import _clipboard_text, paths_from_clipboard

# === SETTINGS START ===
WORKFLOW_FILE       = "ComfyUI_workflow_api_qwen2512.json"  # fallback if input has no .json workflows
# ComfyUI_workflow_api_qwen2512 ComfyUI_workflow_api_ideogram4

MULTIPLE_INPUT = "ask" # ask | 1. combine (concat text / pack images into grid) | 2. (any other key) one per prompt (ComfyUI has internal queue - do not need wait finish)
MULTIPLE_SAFETENSORS = "ask" # ask | 1. combine (sequence of loaders in single prompt) | 2. (any other key) (multiple prompts, 1 lora per prompt)

PROMPT          = "abstract colors maze"      # fallback for clipboard/arg
INPUT_IMAGE     = None        # fallback i2i source path; None => txt2img
OUTPUT_DIR      = None        # fallback output dir; None => CWD
LORA_STRENGTH_MODEL = 1.0   # Default LoRA model strength
LORA_STRENGTH_CLIP = 1.0    # Default LoRA clip strength

# prevent intermediate combined image from being too big if user by mistake runs it with 100 images
MAX_IMAGE_SIZE = 2048


SEARCH_VALUE_AND_REPLACE = {
    "##1": "{PROMPT}",  # Replace ##1 with PROMPT (from clipboard/file/args or PROMPT constant)
    # "forest": lambda: f"forest with {random.randint(1, 10)} trees",
}

# SEARCH format: "node_id/path/to/prop" | "partial/path/to/prop" | "prop"
# Use {VARIABLE} to interpolate Python global variables from this script
SEARCH_PROPERTY_NAME_AND_REPLACE_VALUE = {
    # Example: Add random overlay to prompt
    # "text": "{PROMPT}, with {random.randint(1, 5)} birds",
    # "6/inputs/text": "{PROMPT}, draw in anime style",
    # Example: Draw dial clock (uncomment date/time variables above)
    # "6/inputs/text": "dial clock showing {time_str}, {PROMPT}",
    # "6/inputs/text": "draw calendar with current{datetime.now().strftime("%Y-%m-%d")}",
    # "text": "grass maze among {random.randint(1, 5)} trees",
    # "6/inputs/text": "A public wall art clock showing {CLOCK_TIME} on {CURRENT_DATE}", # Example for clock
}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff"}
TEXT_EXTS  = {".txt", ".md", ".caption"}
WORKFLOW_EXTS = {".json"}  # .json inputs are treated as workflows and always ALL run

SERVER_ADDRESS      = "127.0.0.1:8188"

DISABLE_OUTPUT_SAVE = True    # replace SaveImage to PreviewImage (ComfyUI do no output )

DebugSaveJSON = False
# === SETTINGS END ===

"""
Run the Qwen-Image workflow against a local ComfyUI server.

Use cases
---------
1. User copies a .txt caption file (Explorer/Finder "Copy") and runs this script:
        -> caption is used as prompt, image saved into CWD (txt2img).

2. User edits SEARCH_*_REPLACE rules in this script and copies a folder path:
        -> rules are applied, image saved into that folder (txt2img).

3. User copies an image file (or raw image data) and runs this script:
        -> image is used as i2i input, result saved into CWD.

4. User copies .safetensors LoRA file(s):
        -> LoRA is injected into workflow, replacing/chaining before the original loader.

5. User copies workflow .json file(s):
        -> each workflow is run (fallback: hardcoded WORKFLOW_FILE).

Input can contain any number of txt, img, safetensors, workflows.

Multiple-item behavior (same selector architecture for txt, img, safetensors):
    - single item: used as-is
    - multiple items + setting "ask": interactive "1) combine | 2) one per prompt"
    - combine images: if workflow has multiple LoadImage nodes, images are assigned
      to those nodes; otherwise all images are packed into a single grid image
      (aspect ratio preserved, capped at MAX_IMAGE_SIZE).
    - .json workflows never use the selector: all of them always run.

Resolution order for every input (prompt / input image / output folder):
    CLI args  ->  stdin  ->  OS clipboard (files / image / text)  ->  script constants  ->  CWD / empty prompt.

Template variables: Use {VARNAME} in SEARCH_VALUE_AND_REPLACE or SEARCH_PROPERTY_NAME_AND_REPLACE_VALUE
    to interpolate Python global variables (e.g., {PROMPT}, {date}, and labmda {random.randint(1, 5)})
"""

LORA_EXTS = {".safetensors"}
MAX_LORA_SIZE_GB = 2
LORA_NAME = "no_lora"

# If LoRA is outside ComfyUI/models/loras, we'll try to symlink it here:
LORA_SYMLINK_SUBDIR = "_test"  # -> models/loras/_test/<symlink>

# LoRA loader class types that we can chain
LORA_LOADER_CLASSES = ("LoraLoader", "LoraLoaderModelOnly", "LoraLoaderGGUF", "PowerLoraLoader")

# --- Custom Python Variables (Accessible via {var_name} in rules) ---
#CURRENT_DATE = datetime.datetime.now().strftime("%Y-%m-%d")
# CLOCK_TIME = datetime.datetime.now().strftime("%H:%M")

CLIENT_ID = str(uuid.uuid4())

def resolve_value(val):
    # 1. Resolve callable (lambda)
    res = str(val()) if callable(val) else str(val)

    # 2. Dynamic interpolation from globals
    def replace_var(match):
        var_name = match.group(1)
        return str(globals().get(var_name, match.group(0)))

    return re.sub(r"\{(\w+)\}", replace_var, res)

def universal_replace(workflow: dict):
    counter = 0

    # separate to firstly apply global
    specific_paths = {}
    global_paths = {}

    for path, new_val in SEARCH_PROPERTY_NAME_AND_REPLACE_VALUE.items():
        parts = path.split('/')
        if parts[0].isdigit():
            specific_paths[path] = new_val
        else:
            global_paths[path] = new_val

    # Global replacements
    for path, new_val in global_paths.items():
        resolved_new_val = resolve_value(new_val)
        parts = path.split('/')

        for node_id, node_data in workflow.items():
            if not node_id.isdigit(): continue
            curr = node_data
            valid_path = True
            for step in parts:
                if isinstance(curr, dict) and step in curr: curr = curr[step]
                else:
                    valid_path = False
                    break

            if valid_path:
                target_parent = node_data
                for step in parts[:-1]: target_parent = target_parent[step]
                target_parent[parts[-1]] = resolved_new_val
                counter += 1

    # Specific replacements
    for path, new_val in specific_paths.items():
        resolved_new_val = resolve_value(new_val)
        parts = path.split('/')
        node_id, remaining_path = parts[0], parts[1:]
        if node_id in workflow:
            curr = workflow[node_id]
            valid_path = True
            for step in remaining_path:
                if isinstance(curr, dict) and step in curr: curr = curr[step]
                else:
                    valid_path = False
                    break
            if valid_path:
                target_parent = workflow[node_id]
                for step in remaining_path[:-1]: target_parent = target_parent[step]
                target_parent[remaining_path[-1]] = resolved_new_val
                counter += 1

    def walk_and_replace_text(obj):
        nonlocal counter
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str):
                    for search_str, replace_val in SEARCH_VALUE_AND_REPLACE.items():
                        if search_str in value:
                            obj[key] = value.replace(search_str, resolve_value(replace_val))
                            counter += 1
                else: walk_and_replace_text(value)
        elif isinstance(obj, list):
            for item in obj: walk_and_replace_text(item)

    walk_and_replace_text(workflow)

    # replace SaveImage to PreviewImage to disable save in ComfyUI
    if DISABLE_OUTPUT_SAVE:
        for node_id, node_data in workflow.items():
            if isinstance(node_data, dict) and node_data.get("class_type") == "SaveImage":
                node_data["class_type"] = "PreviewImage"

    return workflow, counter

def queue_prompt(prompt):
    p = {"prompt": prompt, "client_id": CLIENT_ID}
    data = json.dumps(p).encode('utf-8')
    req = urllib.request.Request(f"http://{SERVER_ADDRESS}/prompt", data=data)
    return json.loads(urllib.request.urlopen(req).read())

def get_image(filename, subfolder, folder_type):
    data = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    url_values = urllib.parse.urlencode(data)
    with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/view?{url_values}") as response:
        return response.read()

def wait_and_download_image(prompt_workflow, output_dir: str | None = None):
    if output_dir is None: output_dir = os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    prompt_id = queue_prompt(prompt_workflow)['prompt_id']
    _wait_for_prompts_and_download([prompt_id], output_dir)

def wait_and_download_batch(prompt_ids: list[str], output_dir: str | None = None):
    if output_dir is None: output_dir = os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    if prompt_ids:
        _wait_for_prompts_and_download(prompt_ids, output_dir)

def _wait_for_prompts_and_download(prompt_ids: list[str], output_dir: str):
    ws = websocket.WebSocket()
    ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={CLIENT_ID}")

    completed = set()
    remaining = set(prompt_ids)

    print(f"[wait  ] Monitoring queue for {len(prompt_ids)} images...")

    while remaining:
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            if message.get('type') == 'executing':
                data = message.get('data', {})
                if data.get('node') is None:
                    pid = data.get('prompt_id')
                    if pid in remaining:
                        print(f"[done  ] Prompt {pid} finished. Downloading...")
                        _download_images_for_prompt(pid, output_dir)
                        completed.add(pid)
                        remaining.remove(pid)
    ws.close()

def _download_images_for_prompt(prompt_id: str, output_dir: str) -> bool:
    saved_any = False
    try:
        with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/history/{prompt_id}") as response:
            history = json.loads(response.read()).get(prompt_id, {})
        for node_id in history.get('outputs', {}):
            node_output = history['outputs'][node_id]
            if 'images' in node_output:
                for image in node_output['images']:
                    image_data = get_image(image['filename'], image['subfolder'], image['type'])
                    save_path = os.path.join(output_dir, f"output_{uuid.uuid4().hex[:6]}.png")
                    with open(save_path, "wb") as f:
                        f.write(image_data)
                    print(f"image saved: {save_path}")
                    saved_any = True
    except Exception as exc:
        print(f"!! Failed to download images for prompt {prompt_id}: {exc}", file=sys.stderr)
    return saved_any

# ---------------------------------------------------------------------------
#  UNIFIED MULTI-INPUT SELECTOR (same architecture for txt, img, safetensors)
# ---------------------------------------------------------------------------

def resolve_multi_mode(kind: str, labels: list[str], setting) -> str:
    """
    Unified single|multiple selector used for txt, img and safetensors inputs.
    (Not used for .json workflows: those always ALL run.)

    Returns "combine" or "multi".
      - single item      -> "combine" (behaves identically either way)
      - setting "ask"    -> interactive: 1) (key 1) combine | 2) (any key) one per prompt
      - setting combine  -> "combine"
      - anything else    -> "multi"
    """
    if len(labels) <= 1:
        return "combine"

    mode = setting
    if mode == "ask":
        print(f"\nMultiple {kind} detected ({len(labels)}):")
        for name in labels:
            print(f"  - {name}")
        choice = input("  1) combine  2) one per prompt\nEnter choice (1-2): ").strip()
        mode = "combine" if choice == "1" else "multi"

    return "combine" if str(mode).lower() in ("1", "combine") else "multi"

# ---------------------------------------------------------------------------
#  IMAGE COMBINING
# ---------------------------------------------------------------------------

def pack_images(paths: list[Path]) -> Path:
    """
    Pack all images into a single grid image, keeping aspect ratio per cell.
    Final canvas never exceeds MAX_IMAGE_SIZE on either side - prevents huge
    intermediate images if user runs this with 100 images by mistake.
    """
    if Image is None:
        raise RuntimeError("Pillow is required to combine images (pip install pillow).")

    imgs = []
    for p in paths:
        try:
            imgs.append(Image.open(p).convert("RGB"))
        except Exception as exc:
            print(f"[image ] Skipped unreadable '{p.name}': {exc}")
    if not imgs:
        raise RuntimeError("No readable images to combine.")

    cols = math.ceil(math.sqrt(len(imgs)))
    rows = math.ceil(len(imgs) / cols)
    # cell size limited so the final canvas never exceeds MAX_IMAGE_SIZE
    cell = max(1, MAX_IMAGE_SIZE // max(cols, rows))

    canvas = Image.new("RGB", (cols * cell, rows * cell), (0, 0, 0))
    for i, img in enumerate(imgs):
        # keep aspect ratio inside the cell
        scale = min(cell / img.width, cell / img.height)
        w, h = max(1, int(img.width * scale)), max(1, int(img.height * scale))
        resized = img.resize((w, h), Image.LANCZOS)
        x = (i % cols) * cell + (cell - w) // 2
        y = (i // cols) * cell + (cell - h) // 2
        canvas.paste(resized, (x, y))

    tmp = tempfile.NamedTemporaryFile(prefix="combined_", suffix=".png", delete=False)
    tmp.close()
    canvas.save(tmp.name, "PNG")
    print(f"[image ] Combined {len(imgs)} images into {cols}x{rows} grid -> {tmp.name}")
    return Path(tmp.name)

# ---------------------------------------------------------------------------
#  LORA INJECTION
# ---------------------------------------------------------------------------

def find_lora_loader(workflow: dict) -> tuple[str | None, dict | None]:
    """Search for first LoRA loader node. Returns (node_id, node) or (None, None).
    IDs come from the live workflow, so they remain robust even if ComfyUI node ids change."""
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") in LORA_LOADER_CLASSES:
            return node_id, node
    return None, None

def copy_lora_loader(node: dict) -> dict:
    """Deep-copy a loader node, stripping multi-lora extras (lora_name_2, strength_2, ...)."""
    new_node = copy.deepcopy(node)
    for k in list(new_node.get("inputs", {})):
        if re.match(r"(lora_name|strength_model|strength_clip)_\d+$", k):
            del new_node["inputs"][k]
    return new_node

def next_free_node_id(workflow: dict) -> str:
    """Find next available unique node ID for new nodes."""
    max_id = max((int(k) for k in workflow if str(k).isdigit()), default=0)
    return str(max_id + 1)

def insert_node_before(workflow: dict, target_id: str, new_node: dict) -> str:
    """
    Insert new_node upstream of target_id in the model/clip chain:
      (upstream) -> new_node -> target -> (downstream unchanged)

    This keeps the original graph intact and actually inserts rather than replacing.
    """
    new_id = next_free_node_id(workflow)
    target = workflow[target_id]

    # new node steals target's upstream links
    new_node["inputs"]["model"] = target["inputs"].get("model")
    if "clip" in target["inputs"]:
        new_node["inputs"]["clip"] = target["inputs"].get("clip")

    workflow[new_id] = new_node

    # target now consumes the new node outputs (MODEL=0, CLIP=1)
    target["inputs"]["model"] = [new_id, 0]
    if "clip" in target["inputs"]:
        target["inputs"]["clip"] = [new_id, 1]

    return new_id

def inject_loras(workflow: dict, lora_names: list[str]) -> int:
    """
    Inject one loader per name, chaining them before the existing LoRA loader.
    Important: We iterate in reverse so the user-provided order is preserved in the final chain.
    """
    anchor_id, anchor = find_lora_loader(workflow)
    if not anchor_id:
        print("[lora  ] No LoRA loader found in workflow - cannot inject.")
        return 0

    injected = 0

    # Reverse so that [a1, a2] becomes a1 -> a2 -> anchor (not anchor -> a2 -> a1)
    for name in reversed(lora_names):
        new_node = copy_lora_loader(anchor)
        new_node["inputs"]["lora_name"] = name
        new_node["inputs"]["strength_model"] = LORA_STRENGTH_MODEL
        if "strength_clip" in new_node["inputs"]:
            new_node["inputs"]["strength_clip"] = LORA_STRENGTH_CLIP

        new_id = insert_node_before(workflow, anchor_id, new_node)
        print(f"[lora  ] Injected '{name}' as node {new_id} (chained before node {anchor_id})")
        injected += 1

    return injected

def _validate_lora_file(lora_path: Path) -> bool:
    """Check if file is a valid .safetensors under size limit."""
    if lora_path.suffix.lower() not in LORA_EXTS:
        return False
    try:
        size_gb = lora_path.stat().st_size / (1024 ** 3)
        if size_gb >= MAX_LORA_SIZE_GB:
            print(f"[lora  ] Skipped '{lora_path.name}': {size_gb:.2f}GB >= {MAX_LORA_SIZE_GB}GB limit.")
            return False
        return True
    except Exception as exc:
        print(f"[lora  ] Could not stat '{lora_path.name}': {exc}")
        return False

def _server_lora_list() -> set[str] | None:
    """Optional check: ask ComfyUI which lora names it knows."""
    try:
        # Depending on ComfyUI version, object_info routes may differ.
        # If it fails, we just skip the check.
        with urllib.request.urlopen(f"http://{SERVER_ADDRESS}/object_info/LoraLoader", timeout=5) as r:
            info = json.loads(r.read())
        return set(info["LoraLoader"]["input"]["required"]["lora_name"][0])
    except Exception:
        return None

def resolve_lora_name(lora_path: Path) -> str | None:
    """
    ComfyUI expects LoRA in models/loras.

    If lora is already under models/loras: return the relative path from models/loras.
    If lora is outside: ask to auto-create symlink into models/loras/_test/.
    - If user agrees: attempt symlink creation.
    - If user declines OR symlink creation fails: print clear recommendation and skip (return None).
    """
    p = lora_path.resolve()

    # Determine whether the file is already inside ComfyUI/models/loras
    # (case-insensitive match for path segments)
    parts_lower = [x.lower() for x in p.parts]
    for i in range(len(parts_lower) - 1):
        if parts_lower[i] == "models" and parts_lower[i + 1] == "loras":
            rel = str(Path(*p.parts[i + 2:]))
            print(f"[lora  ] '{p.name}' inside models/loras -> '{rel}'")
            return rel

    # Outside ComfyUI/models/loras
    print(f"\n[lora  ] '{p.name}' is OUTSIDE ComfyUI models/loras.")
    comfy_root = Path.cwd()
    manual_models_loras = (comfy_root / "models" / "loras").resolve()
    print(f"[lora  ] Expected location: {manual_models_loras}")

    ans = input(f"[lora  ] Create symlink automatically into models/loras/{LORA_SYMLINK_SUBDIR}/ ? (y/N): ").strip().lower()
    if ans not in ("y", "yes"):
        print(f"[lora  ] Please manually move/copy the LoRA into '{manual_models_loras}' "
              f"(or create your own symlink). Skipping '{p.name}'.")
        return None

    # Attempt symlink creation
    target_dir = (manual_models_loras / LORA_SYMLINK_SUBDIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    symlink_path = target_dir / p.name

    try:
        # If target already exists, accept it
        if symlink_path.exists():
            # (Optional) basic sanity: if it's not a symlink or points elsewhere, we still proceed.
            print(f"[lora  ] Symlink/target already exists: {symlink_path}")
        else:
            # Create symlink (file)
            os.symlink(str(p), str(symlink_path), target_is_directory=p.is_dir())
            print(f"[lora  ] Created symlink: {symlink_path} -> {p}")

        rel_name = f"{LORA_SYMLINK_SUBDIR}/{p.name}"

        known = _server_lora_list()
        if known is not None and rel_name not in known:
            print(f"[lora  ] WARNING: ComfyUI server does not list '{rel_name}' yet "
                  f"(restart/refresh might be needed). Using it anyway.")

        return rel_name

    except OSError as exc:
        # Requirement: if symlink creation fails, print a clear recommendation
        print(f"[lora  ] Could not create symlink due to error: {exc}")
        print(f"[lora  ] Recommendation: manually move/copy '{p.name}' into '{manual_models_loras}' "
              f"(or create a symlink yourself). Skipping this LoRA.")
        return None
    except Exception as exc:
        print(f"[lora  ] Unexpected symlink creation failure: {exc}")
        print(f"[lora  ] Recommendation: manually move/copy '{p.name}' into '{manual_models_loras}' "
              f"(or create a symlink yourself). Skipping this LoRA.")
        return None

def group_loras(lora_names: list[str]) -> list[list[str]]:
    """
    MULTIPLE_SAFETENSORS behavior (via unified selector):
      - "combine": sequence of loaders in a single prompt (one prompt, chained loaders)
      - "ask": interactive choice between combined chain vs one-per-prompt
      - any other key: one lora per prompt
    """
    if not lora_names:
        return []
    mode = resolve_multi_mode("LoRA files", lora_names, MULTIPLE_SAFETENSORS)
    if mode == "combine":
        return [lora_names]
    # one lora per prompt
    return [[n] for n in lora_names]

# ---------------------------------------------------------------------------
#  WORKFLOW CONFIGURATION
# ---------------------------------------------------------------------------

def upload_image_to_comfy(image_path: Path) -> str:
    boundary = uuid.uuid4().hex
    filename = image_path.name
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with open(image_path, "rb") as f: file_bytes = f.read()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        file_bytes,
        f"\r\n--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n',
        f"--{boundary}--\r\n".encode()
    ])
    req = urllib.request.Request(
        f"http://{SERVER_ADDRESS}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        info = json.loads(resp.read())
    name, subfolder = info.get("name") or filename, info.get("subfolder") or ""
    return f"{subfolder}/{name}" if subfolder else name

def _find_first_by_class(workflow: dict, class_types: tuple[str, ...]) -> str | None:
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") in class_types:
            return node_id
    return None

def _find_load_image_nodes(workflow: dict) -> list[str]:
    """Return all LoadImage node ids (order of dict iteration)."""
    return [nid for nid, node in workflow.items()
            if isinstance(node, dict) and node.get("class_type") == "LoadImage"]

def configure_workflow(workflow: dict, input_image_uploaded_name: str | None = None) -> dict:
    """
    Only configure image/latent wiring here.
    LoRA injection is done later per job (prompt x lora-group) to support MULTIPLE_SAFETENSORS modes.
    """
    global LORA_NAME

    # Keep existing LORA_NAME variable value (in case prompt templates use it)
    orig_lora_id, orig_lora = find_lora_loader(workflow)
    if orig_lora:
        LORA_NAME = Path(orig_lora["inputs"].get("lora_name", "no_lora")).stem
    else:
        LORA_NAME = "no_lora"

    ksampler_id = _find_first_by_class(workflow, ("KSampler", "KSamplerAdvanced"))
    if not ksampler_id:
        raise RuntimeError("Workflow has no KSampler node.")

    if input_image_uploaded_name:
        load_id, vae_enc_id = _find_first_by_class(workflow, ("LoadImage",)), _find_first_by_class(workflow, ("VAEEncode",))
        if not load_id or not vae_enc_id:
            raise RuntimeError("Workflow lacks LoadImage/VAEEncode.")
        workflow[load_id]["inputs"]["image"] = input_image_uploaded_name
        workflow[ksampler_id]["inputs"]["latent_image"] = [vae_enc_id, 0]
    else:
        empty_id = _find_first_by_class(workflow, ("EmptySD3LatentImage", "EmptyLatentImage"))
        if empty_id:
            workflow[ksampler_id]["inputs"]["latent_image"] = [empty_id, 0]

    return workflow

# ---------------------------------------------------------------------------
#  INPUT RESOLUTION (Using command_paths.py logic)
# ---------------------------------------------------------------------------

def _try_image_from_clipboard() -> Path | None:
    try:
        from PIL import ImageGrab
        img = ImageGrab.grabclipboard()
        if img is None or not hasattr(img, "save"):
            return None
        tmp = tempfile.NamedTemporaryFile(prefix="clip_", suffix=".png", delete=False)
        tmp.close()
        img.save(tmp.name, "PNG")
        return Path(tmp.name)
    except Exception:
        return None

def _looks_like_path(text: str) -> Path | None:
    stripped = text.strip().strip('"\'')
    if not stripped or len(stripped) > 4096 or "\n" in stripped:
        return None
    p = Path(stripped).expanduser()
    return p if p.exists() else None

def _collect_raw_candidates() -> list[tuple[str, object]]:
    """
    Walk args -> stdin -> clipboard and return a list of ('path'|'text', value).
    The first source that yields anything wins (matches the resolver chain).
    """
    candidates = []
    for arg in sys.argv[1:]:
        p = Path(arg).expanduser()
        candidates.append(("path", p) if p.exists() else ("text", arg))
    if candidates:
        return candidates

    # 2. stdin
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data and data.strip():
            p = _looks_like_path(data)
            candidates.append(("path", p) if p is not None else ("text", data))
            if candidates:
                return candidates

    # 3a. Clipboard: copied file/dir references
    for cp in paths_from_clipboard():
        candidates.append(("path", Path(cp)))
    if candidates:
        return candidates

    # 3b. Clipboard: raw image bitmap
    img_path = _try_image_from_clipboard()
    if img_path:
        return [("path", img_path)]

    # 3c. Clipboard: plain text
    text = _clipboard_text()
    if text and text.strip():
        p = _looks_like_path(text)
        candidates.append(("path", p) if p is not None else ("text", text))
        return candidates

    return candidates

def _read_text_file(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""

def resolve_run_inputs() -> tuple[list[str], list[Path], str, Path, list[Path], list[Path]]:
    """
    Sort all inputs into buckets (any number of txt, img, safetensors, workflows)
    and resolve prompts + image mode via the unified selector.

    Returns: (prompts, image_paths, image_mode, output_dir, lora_paths, workflow_paths)
    """
    candidates = _collect_raw_candidates()
    txt_paths: list[Path] = []
    image_paths: list[Path] = []
    lora_paths: list[Path] = []
    workflow_paths: list[Path] = []
    output_dir = None
    text_prompts: list[str] = []

    for kind, value in candidates:
        if kind == "path":
            p = value
            if p.is_dir():
                if output_dir is None:
                    output_dir = p
            else:
                ext = p.suffix.lower()
                if ext in IMAGE_EXTS:
                    image_paths.append(p)
                elif ext in LORA_EXTS:
                    lora_paths.append(p)
                elif ext in WORKFLOW_EXTS:
                    # .json => workflow file; always run all (no selector)
                    workflow_paths.append(p)
                elif ext in TEXT_EXTS or ext == "":
                    if p.exists():
                        txt_paths.append(p)

        elif kind == "text":
            text_prompts.append(str(value).strip())

    # --- prompts (txt files take priority over raw text) ---
    if txt_paths:
        mode = resolve_multi_mode("text files", [p.name for p in txt_paths], MULTIPLE_INPUT)
        texts = [_read_text_file(p) for p in txt_paths]
        if mode == "combine":
            joined = "\n\n".join(t for t in texts if t)
            prompts = [joined] if joined else ([PROMPT] if PROMPT else [""])
        else:  # one prompt per file
            prompts = [t if t else (PROMPT or "") for t in texts]
    elif text_prompts:
        labels = [t[:60] + "..." if len(t) > 60 else t for t in text_prompts]
        mode = resolve_multi_mode("text prompts", labels, MULTIPLE_INPUT)
        prompts = ["\n\n".join(text_prompts)] if mode == "combine" else text_prompts
    elif PROMPT:
        prompts = [PROMPT]
    else:
        prompts = [""]

    if not prompts:
        prompts = [PROMPT or ""]

    # Handle INPUT_IMAGE constant fallback (only if no images from input)
    if not image_paths and INPUT_IMAGE:
        ip = Path(INPUT_IMAGE).expanduser()
        if ip.exists() and ip.is_file():
            image_paths = [ip]

    # Unified selector for images: combine | one per prompt
    image_mode = resolve_multi_mode("image files", [p.name for p in image_paths], MULTIPLE_INPUT)

    if output_dir is None:
        if OUTPUT_DIR:
            od = Path(OUTPUT_DIR).expanduser()
            if od.exists() and od.is_dir():
                output_dir = od
        if output_dir is None:
            output_dir = Path.cwd()

    return prompts, image_paths, image_mode, output_dir.resolve(), lora_paths, workflow_paths

# ---------------------------------------------------------------------------
#  JOB PLANNING
# ---------------------------------------------------------------------------

def plan_image_jobs(image_paths: list[Path], image_mode: str,
                    load_node_ids: list[str], upload_fn) -> list[list[str] | None]:
    """
    Build per-workflow image assignments.
    Each item is a list of uploaded names (len > 1 => one name per LoadImage node)
    or None for txt2img.

    combine mode:
      - workflow has multiple LoadImage nodes -> set available images to available nodes
      - otherwise -> pack all images into a single grid image (aspect kept, MAX_IMAGE_SIZE cap)
    multi mode:
      - 1 image per prompt/job
    """
    if not image_paths:
        return [None]

    if len(image_paths) == 1:
        name = upload_fn(image_paths[0])
        return [[name]] if name else [None]

    if image_mode == "combine":
        if len(load_node_ids) > 1:
            # Workflow contains multiple LoadImage nodes: assign images to nodes
            usable = image_paths[:len(load_node_ids)]
            if len(image_paths) > len(load_node_ids):
                print(f"[image ] Workflow has {len(load_node_ids)} LoadImage nodes; "
                      f"using first {len(load_node_ids)} of {len(image_paths)} images.")
            names = [n for n in (upload_fn(p) for p in usable) if n]
            return [names] if names else [None]
        # Single LoadImage: pack all images into one grid image
        try:
            packed = pack_images(image_paths)
        except Exception as exc:
            print(f"!! Could not combine images: {exc}", file=sys.stderr)
            return [None]
        name = upload_fn(packed)
        return [[name]] if name else [None]

    # multi: 1 image per prompt
    jobs = []
    for p in image_paths:
        name = upload_fn(p)
        if name:
            jobs.append([name])
    return jobs or [None]

def apply_images_to_workflow(job_wf: dict, image_names: list[str]) -> None:
    """Wire uploaded images into a workflow copy."""
    if len(image_names) > 1:
        # Multiple LoadImage nodes: fill each with its own image, keep workflow wiring as designed
        load_ids = _find_load_image_nodes(job_wf)
        for nid, name in zip(load_ids, image_names):
            job_wf[nid]["inputs"]["image"] = name
    elif image_names:
        # Single image: standard i2i wiring (LoadImage -> VAEEncode -> KSampler)
        configure_workflow(job_wf, input_image_uploaded_name=image_names[0])

# ---------------------------------------------------------------------------
#  MAIN
# ---------------------------------------------------------------------------

def main() -> int:
    global PROMPT, LORA_NAME

    prompts, image_paths, image_mode, output_dir, lora_paths, workflow_paths = resolve_run_inputs()

    # Workflow input: if input contains .json then run each of them,
    # else fallback to hardcoded WORKFLOW_FILE.
    if not workflow_paths:
        wp = Path(WORKFLOW_FILE)
        if not wp.is_absolute():
            wp = Path(__file__).with_name(WORKFLOW_FILE)
        workflow_paths = [wp]

    # Resolve and validate LoRAs -> server-relative names (shared across all workflows)
    lora_names: list[str] = []
    for p in lora_paths:
        if not _validate_lora_file(p):
            continue
        try:
            print(f"[lora  ] Detected LoRA input: {p.name} ({p.stat().st_size / 2**20:.0f} MB)")
        except Exception:
            print(f"[lora  ] Detected LoRA input: {p.name}")

        name = resolve_lora_name(p)
        if name:
            lora_names.append(name)

    lora_groups = group_loras(lora_names)

    # Upload cache: each source image is uploaded only once across workflows
    upload_cache: dict[Path, str | None] = {}

    def upload_cached(path: Path) -> str | None:
        if path in upload_cache:
            return upload_cache[path]
        try:
            uploaded = upload_image_to_comfy(path)
            print(f"-> uploaded '{path.name}' as '{uploaded}'")
            upload_cache[path] = uploaded
            return uploaded
        except Exception as exc:
            print(f"!! Upload failed for '{path.name}' ({exc}); skipping image.", file=sys.stderr)
            upload_cache[path] = None
            return None

    def _extract_text_after_replacements(workflow_dict: dict) -> str | None:
        for node_id, node in workflow_dict.items():
            if isinstance(node, dict) and "inputs" in node:
                if "text" in node["inputs"] and isinstance(node["inputs"]["text"], str):
                    return node["inputs"]["text"]
        return None

    prompt_ids: list[str] = []
    queued_total = 0
    missing_workflows = 0

    for wf_idx, wf_path in enumerate(workflow_paths, 1):
        if not wf_path.exists():
            print(f"!! Workflow file not found: {wf_path}")
            missing_workflows += 1
            continue

        with open(wf_path, "r", encoding="utf-8") as f:
            workflow = json.load(f)

        print(f"[flow  ] Workflow {wf_idx}/{len(workflow_paths)}: {wf_path.name}")

        # Image assignments depend on the workflow (LoadImage node count matters for combine mode)
        load_node_ids = _find_load_image_nodes(workflow)
        image_jobs = plan_image_jobs(image_paths, image_mode, load_node_ids, upload_cached)

        # Job plan: combine prompts, image assignments and lora groups
        # Each job: (prompt, lora_group, image_assignment)
        jobs: list[tuple[str, list[str] | None, list[str] | None]] = []
        if len(image_jobs) <= 1:
            # Single (or combined/packed) image assignment: cartesian with prompts
            img_assign = image_jobs[0] if image_jobs else None
            for prompt in prompts:
                for group in (lora_groups or [None]):
                    jobs.append((prompt, group, img_assign))
        else:
            # multi mode: 1 image per prompt (cycle the shorter list)
            n_pairs = max(len(prompts), len(image_jobs))
            for i in range(n_pairs):
                prompt = prompts[i % len(prompts)]
                img_assign = image_jobs[i % len(image_jobs)]
                for group in (lora_groups or [None]):
                    jobs.append((prompt, group, img_assign))

        for i, (prompt, group, img_assign) in enumerate(jobs, 1):
            PROMPT = prompt
            job_wf = copy.deepcopy(workflow)

            if group:
                inject_loras(job_wf, group)
                LORA_NAME = Path(group[0]).stem

            if img_assign:
                apply_images_to_workflow(job_wf, img_assign)

            processed, n = universal_replace(job_wf)
            lora_note = f", loras: {group}" if group else ""
            img_note = f", image: {img_assign}" if img_assign else ""

            final_prompt = _extract_text_after_replacements(processed)

            print(f"Queueing prompt {i}/{len(jobs)} ({wf_path.name}) with {n} replacements{lora_note}{img_note}:")
            print(final_prompt or prompt)
            print()

            if DebugSaveJSON:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                with open(f"debug_workflow_{timestamp}_{wf_idx}_{i}.json", "w", encoding="utf-8") as f:
                    json.dump(processed, f, indent=2, ensure_ascii=False)

            try:
                response = queue_prompt(processed)
                pid = response['prompt_id']
                prompt_ids.append(pid)
                queued_total += 1
            except Exception as e:
                print(f"!! Failed to queue prompt {i} ({wf_path.name}): {e}", file=sys.stderr)

    if missing_workflows == len(workflow_paths):
        # Nothing to do at all
        return 1

    # Now wait for all queued prompts to finish and download them
    if prompt_ids:
        wait_and_download_batch(prompt_ids, output_dir=str(output_dir))

    print("[finish] All images processed.")
    return 0 if queued_total else 1


if __name__ == "__main__":
    raise SystemExit(main())