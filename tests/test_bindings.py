"""Unit tests for the ComfyUI-bound deps helpers (import-safe parts only)."""

from __future__ import annotations

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
