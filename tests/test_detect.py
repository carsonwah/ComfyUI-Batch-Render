"""Unit tests for the pure node-map auto-detector."""

from __future__ import annotations

import json
from pathlib import Path

from comfyui_batch_render.detect import detect_node_map

_EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "portrait.example.api.json"
)


def test_detect_example_graph():
    template = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    result = detect_node_map(template)
    nm = result["node_map"]
    assert nm["prompt"] == "6"
    assert nm["negative"] == "7"
    assert nm["seed"] == "3"
    assert nm["model_src"] == ["4", 0]
    assert nm["clip_src"] == ["4", 1]
    assert nm["ckpt"] == "4"
    assert result["default_checkpoint"] == "PUT_YOUR_CHECKPOINT.safetensors"


def test_detect_degenerate_graph():
    # No checkpoint, no sampler -> everything None, with notes explaining.
    template = {
        "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 512}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    result = detect_node_map(template)
    nm = result["node_map"]
    assert all(nm[k] is None for k in nm)
    assert result["default_checkpoint"] is None
    assert len(result["notes"]) >= 2


def test_detect_empty():
    result = detect_node_map({})
    assert all(v is None for v in result["node_map"].values())
    assert result["notes"]


def test_detect_prefers_sampler_feeding_save():
    # Two samplers; only #20 traces forward to SaveImage.
    template = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "x.safetensors"},
        },
        "10": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 0,
                "positive": ["11", 0],
                "negative": ["12", 0],
                "model": ["4", 0],
            },
        },
        "11": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1]}},
        "12": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1]}},
        "20": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 0,
                "positive": ["21", 0],
                "negative": ["22", 0],
                "model": ["4", 0],
            },
        },
        "21": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1]}},
        "22": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1]}},
        "30": {"class_type": "VAEDecode", "inputs": {"samples": ["20", 0]}},
        "31": {"class_type": "SaveImage", "inputs": {"images": ["30", 0]}},
    }
    result = detect_node_map(template)
    nm = result["node_map"]
    assert nm["seed"] == "20"
    assert nm["prompt"] == "21"
    assert nm["negative"] == "22"
