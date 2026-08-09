"""Unit tests for the ComfyUI-bound deps helpers (import-safe parts only)."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from comfyui_batch_render.server.bindings import ComfyDeps, _strip_lora_tags


def test_strip_lora_tags_removes_invocation_and_stray_comma():
    assert _strip_lora_tags("<lora:my-lora:1>, girl, smile") == "girl, smile"
    assert _strip_lora_tags("girl, <lora:x:0.8>") == "girl"
    assert _strip_lora_tags("<lora:solo:1>") == ""
    assert _strip_lora_tags("<LORA:Caps:1>, thing") == "thing"


def test_strip_lora_tags_keeps_plain_words():
    assert _strip_lora_tags("1girl, smile") == "1girl, smile"


def test_extract_triggers_drops_lora_tag_from_trained_words():
    civitai = {"trainedWords": ["<lora:on-v3:1>, xxxx", "yyyy"]}
    assert ComfyDeps._extract_triggers({}, civitai) == "xxxx,\nyyyy"


def test_extract_triggers_drops_pure_lora_tag_entry():
    civitai = {"trainedWords": ["<lora:on-v3:1>", "keeper"]}
    assert ComfyDeps._extract_triggers({}, civitai) == "keeper"


def test_extract_triggers_fallback_strips_lora_tags():
    data = {"activation_text": "<lora:x:1>, plain"}
    assert ComfyDeps._extract_triggers(data, {}) == "plain"


def test_extract_prompt_options_keeps_metadata_recipes_separate():
    civitai = {"trainedWords": ["<lora:x:1>, pose one", "pose two", "pose one"]}
    assert ComfyDeps._extract_prompt_options({}, civitai) == ["pose one", "pose two"]


def test_extract_example_prompts_from_civitai_and_custom_images():
    civitai = {
        "images": [
            {"meta": {"prompt": "first example"}},
            {"meta": '{"prompt": "<lora:x:1>, second example"}'},
        ],
        "customImages": [
            {"meta": {"positivePrompt": "third example"}},
            {"meta": {"prompt": "first example"}},
        ],
    }
    assert ComfyDeps._extract_example_prompts({}, civitai) == [
        "first example",
        "second example",
        "third example",
    ]


def test_list_models_includes_example_prompts(tmp_path, monkeypatch):
    model = tmp_path / "character.safetensors"
    model.write_bytes(b"")
    model.with_suffix(".metadata.json").write_text(
        json.dumps(
            {
                "civitai": {
                    "images": [{"meta": {"prompt": "example from metadata"}}]
                }
            }
        ),
        encoding="utf-8",
    )
    folder_paths = SimpleNamespace(
        get_filename_list=lambda kind: [model.name],
        get_full_path=lambda kind, file: str(model),
    )
    monkeypatch.setitem(sys.modules, "folder_paths", folder_paths)

    listed = ComfyDeps().list_models("loras")

    assert listed[0]["example_prompts"] == ["example from metadata"]
