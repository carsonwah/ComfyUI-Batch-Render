"""Unit tests for the Tier 0 graph-patcher layer."""

from __future__ import annotations

import copy

import pytest

from comfyui_batch_render.models import Layer, LoraRef, NodeMap
from comfyui_batch_render.patcher import (
    _join_nonempty,
    _set_field,
    build_render_graph,
    combine_layers,
    splice_lora_chain,
)


def make_template() -> dict:
    """A realistic SDXL API-format graph with string node ids."""
    return {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "base.safetensors"},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["4", 1]},
        },
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
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["8", 0]},
        },
    }


def make_node_map(**overrides) -> NodeMap:
    base = dict(
        prompt="6",
        seed="3",
        model_src=["4", 0],
        clip_src=["4", 1],
        negative="7",
        ckpt="4",
    )
    base.update(overrides)
    return NodeMap.from_dict(base)


# --------------------------------------------------------------------------- #
# combine_layers
# --------------------------------------------------------------------------- #


def test_join_nonempty_drops_blank():
    assert _join_nonempty(["a", "", "  ", "b"]) == "a, b"
    assert _join_nonempty([]) == ""


def test_combine_prompt_order_and_skips_empty():
    base = Layer(
        name="base",
        prompt="masterpiece",
        loras=[LoraRef(file="b.safetensors", triggers="btrig")],
    )
    scenario = Layer(
        name="scn",
        prompt="forest",
        loras=[
            LoraRef(file="s.safetensors", triggers="strig"),
            LoraRef(file="empty.safetensors", triggers=""),
        ],
    )
    combo = combine_layers(base, scenario)
    # order: base.prompt, base lora triggers, scenario.prompt, scenario lora triggers
    assert combo["positive"] == "masterpiece, btrig, forest, strig"


def test_combine_checkpoint_precedence_scenario():
    base = Layer(name="b", checkpoint="base.ckpt")
    scenario = Layer(name="s", checkpoint="scn.ckpt")
    combo = combine_layers(base, scenario, default_checkpoint="def.ckpt")
    assert combo["checkpoint"] == "scn.ckpt"


def test_combine_checkpoint_precedence_base():
    base = Layer(name="b", checkpoint="base.ckpt")
    scenario = Layer(name="s")
    combo = combine_layers(base, scenario, default_checkpoint="def.ckpt")
    assert combo["checkpoint"] == "base.ckpt"


def test_combine_checkpoint_precedence_default():
    base = Layer(name="b")
    scenario = Layer(name="s")
    combo = combine_layers(base, scenario, default_checkpoint="def.ckpt")
    assert combo["checkpoint"] == "def.ckpt"

    combo2 = combine_layers(base, scenario)
    assert combo2["checkpoint"] is None


def test_combine_negative_join():
    base = Layer(name="b", negative="lowres")
    scenario = Layer(name="s", negative="blurry")
    combo = combine_layers(base, scenario)
    assert combo["negative"] == "lowres, blurry"

    combo2 = combine_layers(Layer(name="b"), Layer(name="s", negative="blurry"))
    assert combo2["negative"] == "blurry"


def test_combine_loras_concatenation_order():
    base = Layer(name="b", loras=[LoraRef(file="b0"), LoraRef(file="b1")])
    scenario = Layer(name="s", loras=[LoraRef(file="s0")])
    combo = combine_layers(base, scenario)
    assert [lr.file for lr in combo["loras"]] == ["b0", "b1", "s0"]


# --------------------------------------------------------------------------- #
# splice_lora_chain
# --------------------------------------------------------------------------- #


def test_splice_empty_loras_unchanged():
    graph = make_template()
    original = copy.deepcopy(graph)
    new_graph, model_out, clip_out = splice_lora_chain(
        graph, ["4", 0], ["4", 1], []
    )
    assert new_graph == original
    assert graph == original  # input not mutated
    assert model_out == ["4", 0]
    assert clip_out == ["4", 1]


def test_splice_one_lora_wiring():
    graph = make_template()
    loras = [LoraRef(file="l0.safetensors", weight=0.8, clip_weight=0.5)]
    new_graph, model_out, clip_out = splice_lora_chain(
        graph, ["4", 0], ["4", 1], loras
    )
    assert "brp_lora_0" in new_graph
    node = new_graph["brp_lora_0"]
    assert node["class_type"] == "LoraLoader"
    assert node["inputs"]["model"] == ["4", 0]
    assert node["inputs"]["clip"] == ["4", 1]
    assert node["inputs"]["lora_name"] == "l0.safetensors"
    assert node["inputs"]["strength_model"] == 0.8
    assert node["inputs"]["strength_clip"] == 0.5
    assert model_out == ["brp_lora_0", 0]
    assert clip_out == ["brp_lora_0", 1]


def test_splice_three_loras_chain_and_strength_fallback():
    graph = make_template()
    loras = [
        LoraRef(file="l0", weight=1.0),  # clip_weight falls back to weight
        LoraRef(file="l1", weight=0.6, clip_weight=0.2),
        LoraRef(file="l2", weight=0.4),
    ]
    new_graph, model_out, clip_out = splice_lora_chain(
        graph, ["4", 0], ["4", 1], loras
    )
    ids = ["brp_lora_0", "brp_lora_1", "brp_lora_2"]
    for nid in ids:
        assert new_graph[nid]["class_type"] == "LoraLoader"

    # chain wiring
    assert new_graph["brp_lora_0"]["inputs"]["model"] == ["4", 0]
    assert new_graph["brp_lora_0"]["inputs"]["clip"] == ["4", 1]
    assert new_graph["brp_lora_1"]["inputs"]["model"] == ["brp_lora_0", 0]
    assert new_graph["brp_lora_1"]["inputs"]["clip"] == ["brp_lora_0", 1]
    assert new_graph["brp_lora_2"]["inputs"]["model"] == ["brp_lora_1", 0]
    assert new_graph["brp_lora_2"]["inputs"]["clip"] == ["brp_lora_1", 1]

    # clip_weight fallback to weight
    assert new_graph["brp_lora_0"]["inputs"]["strength_clip"] == 1.0
    assert new_graph["brp_lora_1"]["inputs"]["strength_clip"] == 0.2

    assert model_out == ["brp_lora_2", 0]
    assert clip_out == ["brp_lora_2", 1]


def test_splice_rewires_existing_consumers():
    graph = make_template()
    loras = [LoraRef(file="l0"), LoraRef(file="l1")]
    new_graph, model_out, clip_out = splice_lora_chain(
        graph, ["4", 0], ["4", 1], loras
    )
    # KSampler model now points at final lora model output
    assert new_graph["3"]["inputs"]["model"] == model_out
    # both CLIPTextEncode clip inputs repointed to final lora clip output
    assert new_graph["6"]["inputs"]["clip"] == clip_out
    assert new_graph["7"]["inputs"]["clip"] == clip_out
    # VAEDecode vae (["4", 2]) is a different output index: UNCHANGED
    assert new_graph["8"]["inputs"]["vae"] == ["4", 2]
    # the first lora still legitimately points at the original src
    assert new_graph["brp_lora_0"]["inputs"]["model"] == ["4", 0]
    assert new_graph["brp_lora_0"]["inputs"]["clip"] == ["4", 1]


def test_splice_id_collision_avoided():
    graph = make_template()
    graph["brp_lora_0"] = {
        "class_type": "LoraLoader",
        "inputs": {"lora_name": "pre.safetensors"},
    }
    loras = [LoraRef(file="new.safetensors")]
    new_graph, model_out, _ = splice_lora_chain(graph, ["4", 0], ["4", 1], loras)
    # existing node preserved
    assert new_graph["brp_lora_0"]["inputs"]["lora_name"] == "pre.safetensors"
    # new node got a non-colliding id
    assert model_out[0] != "brp_lora_0"
    assert new_graph[model_out[0]]["inputs"]["lora_name"] == "new.safetensors"


# --------------------------------------------------------------------------- #
# _set_field
# --------------------------------------------------------------------------- #


def test_set_field_uses_existing():
    node = {"inputs": {"seed": 0}}
    used = _set_field(node, ["seed", "noise_seed"], 42)
    assert used == "seed"
    assert node["inputs"]["seed"] == 42

    node2 = {"inputs": {"noise_seed": 0}}
    used2 = _set_field(node2, ["seed", "noise_seed"], 42)
    assert used2 == "noise_seed"
    assert node2["inputs"]["noise_seed"] == 42


def test_set_field_creates_first_when_missing():
    node = {"inputs": {}}
    used = _set_field(node, ["seed", "noise_seed"], 7)
    assert used == "seed"
    assert node["inputs"]["seed"] == 7


# --------------------------------------------------------------------------- #
# build_render_graph
# --------------------------------------------------------------------------- #


def test_build_render_graph_end_to_end():
    template = make_template()
    original = copy.deepcopy(template)
    node_map = make_node_map()
    base = Layer(
        name="base",
        prompt="masterpiece",
        negative="lowres",
        checkpoint="chosen.safetensors",
        loras=[LoraRef(file="b.safetensors", triggers="btrig", weight=0.7)],
    )
    scenario = Layer(name="scn", prompt="forest", negative="blurry")

    graph = build_render_graph(template, node_map, base, scenario, seed=123)

    assert graph["6"]["inputs"]["text"] == "masterpiece, btrig, forest"
    assert graph["7"]["inputs"]["text"] == "lowres, blurry"
    assert graph["3"]["inputs"]["seed"] == 123
    assert graph["4"]["inputs"]["ckpt_name"] == "chosen.safetensors"
    # lora spliced in
    assert "brp_lora_0" in graph
    assert graph["3"]["inputs"]["model"] == ["brp_lora_0", 0]
    assert graph["6"]["inputs"]["clip"] == ["brp_lora_0", 1]
    # template not mutated
    assert template == original


def test_build_render_graph_noise_seed_autodetect():
    template = make_template()
    # variant sampler using noise_seed
    template["3"]["inputs"].pop("seed")
    template["3"]["inputs"]["noise_seed"] = 0
    template["3"]["class_type"] = "KSamplerAdvanced"

    node_map = make_node_map()
    graph = build_render_graph(
        template, node_map, Layer(name="b"), Layer(name="s"), seed=999
    )
    assert graph["3"]["inputs"]["noise_seed"] == 999
    assert "seed" not in graph["3"]["inputs"]


def test_build_render_graph_no_checkpoint_leaves_ckpt():
    template = make_template()
    node_map = make_node_map()
    graph = build_render_graph(
        template,
        node_map,
        Layer(name="b"),
        Layer(name="s"),
        seed=1,
        default_checkpoint=None,
    )
    # no checkpoint resolved => ckpt_name untouched
    assert graph["4"]["inputs"]["ckpt_name"] == "base.safetensors"


def test_build_render_graph_no_negative_node():
    template = make_template()
    node_map = make_node_map(negative=None)
    graph = build_render_graph(
        template,
        node_map,
        Layer(name="b", negative="lowres"),
        Layer(name="s"),
        seed=1,
    )
    # negative node "7" untouched (still empty)
    assert graph["7"]["inputs"]["text"] == ""


# --------------------------------------------------------------------------- #
# models.from_dict tolerance
# --------------------------------------------------------------------------- #


def test_from_dict_accepts_instances_and_dicts():
    lr = LoraRef.from_dict({"file": "x", "weight": 0.5})
    assert lr.file == "x" and lr.weight == 0.5
    assert LoraRef.from_dict(lr) is lr

    layer = Layer.from_dict(
        {"name": "n", "prompt": "p", "loras": [{"file": "l"}]}
    )
    assert layer.name == "n"
    assert isinstance(layer.loras[0], LoraRef)
    assert Layer.from_dict(layer) is layer

    nm = NodeMap.from_dict(
        {"prompt": "6", "seed": "3", "model_src": ["4", 0], "clip_src": ["4", 1]}
    )
    assert nm.prompt == "6" and nm.negative is None
    assert NodeMap.from_dict(nm) is nm
