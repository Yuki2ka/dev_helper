import json
import os
import sys
import tempfile
import copy
from pathlib import Path
from unittest.mock import patch, MagicMock

TEST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TEST_DIR.parent.parent

sys.path.insert(0, str(PROJECT_ROOT / "path_args"))
sys.path.insert(0, str(TEST_DIR))

import ComfyUI_workflow_api as api


TEST_WORKFLOW = {
    "3": {
        "inputs": {
            "model": ["95", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["71", 0],
            "steps": 4,
            "cfg": 1.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "seed": 0,
        },
        "class_type": "KSampler",
    },
    "6": {
        "inputs": {
            "text": "positive prompt",
            "clip": ["95", 1],
        },
        "class_type": "CLIPTextEncode",
    },
    "7": {
        "inputs": {
            "text": "negative prompt",
            "clip": ["95", 1],
        },
        "class_type": "CLIPTextEncode",
    },
    "37": {
        "inputs": {"unet_name": "model.safetensors"},
        "class_type": "UNETLoader",
    },
    "38": {
        "inputs": {"clip_name": "clip.safetensors"},
        "class_type": "CLIPLoader",
    },
    "95": {
        "inputs": {
            "lora_name": "original_lora.safetensors",
            "strength_model": 1.0,
            "strength_clip": 1.0,
            "model": ["37", 0],
            "clip": ["38", 0],
        },
        "class_type": "LoraLoader",
    },
    "71": {
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        "class_type": "EmptySD3LatentImage",
    },
}


def _clear_lora_state():
    api.LORA_NAME = "no_lora"


def _make_lora_file(name: str, size_kb: int = 1) -> Path:
    tmp_dir = Path(tempfile.mkdtemp())
    p = tmp_dir / name
    p.write_bytes(b"0" * (size_kb * 1024))
    return p


class TestFindLoraLoader:
    def test_finds_lora_loader(self):
        node_id, node = api._find_lora_loader(TEST_WORKFLOW)
        assert node_id == "95"
        assert node["class_type"] == "LoraLoader"

    def test_returns_none_when_missing(self):
        node_id, node = api._find_lora_loader({})
        assert node_id is None
        assert node is None


class TestGenerateUniqueNodeId:
    def test_increments_until_free(self):
        wf = {"100": {}, "101": {}, "102": {}}
        assert api._generate_unique_node_id(wf, 100) == "103"

    def test_returns_free_id_immediately(self):
        wf = {"101": {}}
        assert api._generate_unique_node_id(wf, 100) == "100"


class TestValidateLoraFile:
    def test_valid_safetensors(self):
        p = _make_lora_file("valid.safetensors")
        assert api._validate_lora_file(p) is True

    def test_rejects_non_safetensors(self):
        p = _make_lora_file("invalid.png")
        assert api._validate_lora_file(p) is False

    def test_rejects_too_large(self):
        p = _make_lora_file("huge.safetensors")
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value = MagicMock(st_size=int(1024**3) * 3)
            assert api._validate_lora_file(p) is False

    def test_rejects_at_limit(self):
        p = _make_lora_file("limit.safetensors")
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value = MagicMock(st_size=int(1024**3) * 2)
            assert api._validate_lora_file(p) is False

    def test_accepts_under_limit(self):
        p = _make_lora_file("ok.safetensors")
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value = MagicMock(st_size=int(1024**3) * 1)
            assert api._validate_lora_file(p) is True


class TestInjectLoraLoader:
    def test_injects_single_lora(self):
        _clear_lora_state()
        wf = copy.deepcopy(TEST_WORKFLOW)
        lora_path = str(_make_lora_file("lora.safetensors"))

        result = api._inject_lora_loader(wf, lora_path, 0.8, 0.9)

        assert result is True
        new_ids = [nid for nid in wf if nid not in TEST_WORKFLOW]
        assert len(new_ids) == 1
        new_id = new_ids[0]
        new_node = wf[new_id]
        assert new_node["class_type"] == "LoraLoader"
        assert new_node["inputs"]["lora_name"] == lora_path
        assert new_node["inputs"]["strength_model"] == 0.8
        assert new_node["inputs"]["strength_clip"] == 0.9
        assert new_node["inputs"]["model"] == ["37", 0]
        assert new_node["inputs"]["clip"] == ["38", 0]
        assert wf["3"]["inputs"]["model"] == [new_id, 0]
        assert wf["6"]["inputs"]["clip"] == [new_id, 0]
        assert wf["7"]["inputs"]["clip"] == [new_id, 0]
        assert wf["95"]["inputs"]["model"] == ["37", 0]
        assert wf["95"]["inputs"]["clip"] == ["38", 0]

    def test_injects_when_no_lora_loader_exists(self):
        _clear_lora_state()
        wf = copy.deepcopy(TEST_WORKFLOW)
        del wf["95"]
        lora_path = str(_make_lora_file("lora.safetensors"))
        result = api._inject_lora_loader(wf, lora_path)
        assert result is False


class TestConfigureWorkflowCombine:
    def test_combine_two_loras_single_prompt(self):
        _clear_lora_state()
        api.MULTIPLE_SAFETENSORS = "combine"

        wf = copy.deepcopy(TEST_WORKFLOW)
        lora1 = _make_lora_file("lora1.safetensors")
        lora2 = _make_lora_file("lora2.safetensors")

        api.configure_workflow(wf, None, [lora1, lora2])

        assert api.LORA_NAME == "lora1"

        lora_nodes = {
            nid: node
            for nid, node in wf.items()
            if isinstance(node, dict) and node.get("class_type") == "LoraLoader"
        }
        assert len(lora_nodes) == 3

        orig = "95"
        new_ids = sorted([nid for nid in lora_nodes if nid != orig], key=int)
        assert len(new_ids) == 2

        chain_head = wf["3"]["inputs"]["model"][0]
        chain_after_head = wf[chain_head]["inputs"]["model"][0]
        chain_after_second = wf[chain_after_head]["inputs"]["model"][0]

        assert chain_head == new_ids[0]
        assert chain_after_head == new_ids[1]
        assert chain_after_second == orig


class TestConfigureWorkflowMultiplePrompts:
    def test_single_lora_per_prompt_mode(self):
        _clear_lora_state()
        api.MULTIPLE_SAFETENSORS = "multiple"

        lora1 = _make_lora_file("lora1.safetensors")
        lora2 = _make_lora_file("lora2.safetensors")
        loras = [lora1, lora2]

        for lora in loras:
            wf = copy.deepcopy(TEST_WORKFLOW)
            api.configure_workflow(wf, None, None)
            api._inject_lora_loader(wf, str(lora), api.LORA_STRENGTH_MODEL, api.LORA_STRENGTH_CLIP)

            lora_nodes = {
                nid: node
                for nid, node in wf.items()
                if isinstance(node, dict) and node.get("class_type") == "LoraLoader"
            }
            assert len(lora_nodes) == 2
            new_id = [nid for nid in lora_nodes if nid != "95"][0]
            assert lora_nodes[new_id]["inputs"]["strength_model"] == api.LORA_STRENGTH_MODEL


class TestConfigureWorkflowNoLora:
    def test_existing_lora_preserved_when_no_injection(self):
        _clear_lora_state()
        result = api.configure_workflow(copy.deepcopy(TEST_WORKFLOW), None, None)
        assert result["95"]["inputs"]["lora_name"] == "original_lora.safetensors"
        lora_nodes = [n for n in result.values() if isinstance(n, dict) and n.get("class_type") == "LoraLoader"]
        assert len(lora_nodes) == 1
        assert api.LORA_NAME == "original_lora"


class TestResolveRunInputsLora:
    def test_detects_safetensors_from_args(self):
        lora1 = _make_lora_file("a.safetensors")
        lora2 = _make_lora_file("b.safetensors")

        with patch.object(sys, "argv", ["script.py", str(lora1), str(lora2)]):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch.object(api, "_clipboard_text", return_value=""):
                    with patch.object(api, "paths_from_clipboard", return_value=[]):
                        prompts, img, out_dir, loras = api.resolve_run_inputs()

        assert len(loras) == 2
        assert loras[0].name == "a.safetensors"
        assert loras[1].name == "b.safetensors"

    def test_detects_safetensors_from_clipboard(self):
        lora1 = _make_lora_file("clip_lora.safetensors")

        with patch.object(api, "_collect_raw_candidates", return_value=[("path", lora1)]):
            prompts, img, out_dir, loras = api.resolve_run_inputs()

        assert len(loras) == 1
        assert loras[0].name == "clip_lora.safetensors"


class TestInteractiveLoraSelect:
    def test_selects_single_lora(self):
        loras = [_make_lora_file(f"l{i}.safetensors") for i in range(3)]
        with patch("builtins.input", return_value="1"):
            selected = api._interactive_lora_select(loras)
        assert selected == [loras[0]]

    def test_selects_combine_all(self):
        loras = [_make_lora_file(f"l{i}.safetensors") for i in range(3)]
        with patch("builtins.input", return_value=str(len(loras) + 1)):
            selected = api._interactive_lora_select(loras)
        assert selected == loras


def teardown_module():
    api.MULTIPLE_SAFETENSORS = "ask"
