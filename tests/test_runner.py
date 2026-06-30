"""Tests for the run engine: dry_run, output_path, slugify, manifest shape."""

from __future__ import annotations

import json
from pathlib import Path

from comfyui_batch_render.models import Layer
from comfyui_batch_render.pipeline import Pipeline, RenderJob
from comfyui_batch_render.runner import (
    build_job_graph,
    dry_run,
    output_path,
    slugify,
)


def make_template() -> dict:
    return {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "base.safetensors"},
        },
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 0,
                "steps": 20,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
    }


def make_pipeline(**overrides) -> Pipeline:
    d = {
        "name": "My Pipeline",
        "workflow_template": "tpl.json",
        "node_map": {
            "prompt": "6",
            "negative": "7",
            "seed": "3",
            "model_src": ["4", 0],
            "clip_src": ["4", 1],
            "ckpt": "4",
        },
        "default_checkpoint": "def.safetensors",
        "bases": [
            {
                "name": "Hero A",
                "prompt": "masterpiece",
                "negative": "lowres",
                "loras": [
                    {"file": "characters/a.safetensors", "weight": 0.8, "triggers": "atrig"}
                ],
            },
        ],
        "scenarios": [
            {"name": "Forest Day", "prompt": "forest"},
            {"name": "City Night", "prompt": "city"},
        ],
        "seed": {"mode": "fixed", "value": 42},
    }
    d.update(overrides)
    return Pipeline.from_dict(d)


def test_slugify_shape():
    assert slugify("Hero A") == "hero-a"
    assert slugify("  City   Night!! ") == "city-night"
    assert slugify("Mix_Of-99 things") == "mix-of-99-things"
    assert slugify("") == "untitled"


def test_output_path_shape(tmp_path):
    job = RenderJob(base=Layer(name="Hero A"), scenario=Layer(name="City Night"), seed=42, index=0)
    p = output_path(tmp_path, "My Pipeline", job)
    rel = p.relative_to(tmp_path).as_posix()
    assert rel == "my-pipeline/hero-a/city-night/hero-a__city-night__seed42.png"


def test_build_job_graph_splices_lora_and_prompt():
    pipeline = make_pipeline()
    template = make_template()
    job = RenderJob(base=pipeline.bases[0], scenario=pipeline.scenarios[0], seed=42, index=0)
    graph = build_job_graph(pipeline, template, job)
    # assembled positive prompt: base, base lora trigger, scenario
    assert graph["6"]["inputs"]["text"] == "masterpiece, atrig, forest"
    assert graph["3"]["inputs"]["seed"] == 42
    # a LoraLoader was spliced in
    lora_nodes = [n for n in graph.values() if n.get("class_type") == "LoraLoader"]
    assert len(lora_nodes) == 1
    assert lora_nodes[0]["inputs"]["lora_name"] == "characters/a.safetensors"


def test_dry_run_writes_graphs_and_manifest(tmp_path):
    pipeline = make_pipeline()
    template = make_template()
    manifest = dry_run(pipeline, template, tmp_path)

    slug = "my-pipeline"
    graphs_dir = tmp_path / slug / "_graphs"
    graph_files = sorted(graphs_dir.glob("*.json"))
    # 1 base x 2 scenarios x 1 seed = 2 graph files
    assert len(graph_files) == 2

    # graph contents include spliced LoraLoader + assembled prompt
    first = json.loads(graph_files[0].read_text(encoding="utf-8"))
    assert any(n.get("class_type") == "LoraLoader" for n in first.values())
    assert first["6"]["inputs"]["text"] == "masterpiece, atrig, forest"

    # manifest file written + structure
    manifest_path = tmp_path / slug / "manifest.json"
    assert manifest_path.exists()
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk == manifest
    assert manifest["pipeline"] == "My Pipeline"
    assert manifest["job_count"] == 2
    assert "created_utc" in manifest
    assert len(manifest["jobs"]) == 2

    entry = manifest["jobs"][0]
    assert entry["index"] == 0
    assert entry["base"] == "Hero A"
    assert entry["scenario"] == "Forest Day"
    assert entry["seed"] == 42
    assert entry["checkpoint"] == "def.safetensors"
    assert entry["loras"] == [{"file": "characters/a.safetensors", "weight": 0.8}]
    assert entry["positive"] == "masterpiece, atrig, forest"
    assert entry["images"] == []


def test_dry_run_graph_filenames(tmp_path):
    pipeline = make_pipeline()
    dry_run(pipeline, make_template(), tmp_path)
    names = sorted(f.name for f in (tmp_path / "my-pipeline" / "_graphs").glob("*.json"))
    assert names == [
        "0_hero-a__forest-day__seed42.json",
        "1_hero-a__city-night__seed42.json",
    ]
