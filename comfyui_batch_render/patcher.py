"""Pure graph-patching functions for the Tier 0 layer.

Design rule: NEVER mutate the input template -- always deep-copy first.

ComfyUI API-format graphs are ``{node_id: {"class_type": str, "inputs": {...}}}``
dicts. Links are encoded as ``[node_id, output_index]`` lists inside an input
value.
"""

from __future__ import annotations

import copy

from .models import Layer, LoraRef, NodeMap


def _join_nonempty(parts: list[str]) -> str:
    """Join with ", " while dropping empty / whitespace-only strings."""
    return ", ".join(p for p in parts if p and p.strip())


def combine_layers(
    base: Layer,
    scenario: Layer,
    default_checkpoint: str | None = None,
) -> dict:
    """Merge a base layer and a scenario layer into a flat render spec.

    Returns ``{"positive", "negative", "checkpoint", "loras"}``.
    """
    positive_parts: list[str] = [base.prompt]
    positive_parts += [lora.triggers for lora in base.loras]
    positive_parts.append(scenario.prompt)
    positive_parts += [lora.triggers for lora in scenario.loras]

    negative = _join_nonempty([base.negative, scenario.negative])

    checkpoint = scenario.checkpoint or base.checkpoint or default_checkpoint

    loras = list(base.loras) + list(scenario.loras)

    return {
        "positive": _join_nonempty(positive_parts),
        "negative": negative,
        "checkpoint": checkpoint,
        "loras": loras,
    }


def _is_link_to(value: object, src: list) -> bool:
    """True when ``value`` is a 2-element link list equal to ``src``."""
    return isinstance(value, list) and len(value) == 2 and value == src


def _fresh_lora_ids(graph: dict, count: int) -> list[str]:
    """Generate ``count`` LoraLoader node ids that don't collide with keys."""
    ids: list[str] = []
    existing = set(graph.keys())
    for i in range(count):
        candidate = f"brp_lora_{i}"
        suffix = 0
        while candidate in existing:
            suffix += 1
            candidate = f"brp_lora_{i}_{suffix}"
        existing.add(candidate)
        ids.append(candidate)
    return ids


def splice_lora_chain(
    graph: dict,
    model_src: list,
    clip_src: list,
    loras: list[LoraRef],
) -> tuple[dict, list, list]:
    """Splice a serial chain of LoraLoader nodes between src and consumers.

    Returns ``(new_graph, model_out, clip_out)``. The input graph is never
    mutated. With an empty ``loras`` list the copy is returned unchanged and
    the endpoints equal the given ``model_src`` / ``clip_src``.
    """
    new_graph = copy.deepcopy(graph)

    if not loras:
        return new_graph, list(model_src), list(clip_src)

    lora_ids = _fresh_lora_ids(new_graph, len(loras))

    # Build the serial chain.
    prev_model = list(model_src)
    prev_clip = list(clip_src)
    for node_id, ref in zip(lora_ids, loras):
        new_graph[node_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": ref.file,
                "strength_model": ref.weight,
                "strength_clip": ref.clip_weight
                if ref.clip_weight is not None
                else ref.weight,
                "model": prev_model,
                "clip": prev_clip,
            },
        }
        prev_model = [node_id, 0]
        prev_clip = [node_id, 1]

    model_out = [lora_ids[-1], 0]
    clip_out = [lora_ids[-1], 1]

    # Rewire pre-existing nodes only (exclude the newly added LoraLoaders).
    new_ids = set(lora_ids)
    for node_id, node in new_graph.items():
        if node_id in new_ids:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, value in inputs.items():
            if _is_link_to(value, model_src):
                inputs[key] = list(model_out)
            elif _is_link_to(value, clip_src):
                inputs[key] = list(clip_out)

    return new_graph, model_out, clip_out


def _set_field(node: dict, candidate_names: list[str], value) -> str:
    """Set the first candidate field already present in ``node['inputs']``.

    If none of the candidates exist, the first candidate is created. Returns
    the field name that was written.
    """
    inputs = node.setdefault("inputs", {})
    for name in candidate_names:
        if name in inputs:
            inputs[name] = value
            return name
    name = candidate_names[0]
    inputs[name] = value
    return name


def build_render_graph(
    template: dict,
    node_map: NodeMap,
    base: Layer,
    scenario: Layer,
    seed: int,
    default_checkpoint: str | None = None,
) -> dict:
    """Patch a template graph for a single render. Never mutates ``template``."""
    combo = combine_layers(base, scenario, default_checkpoint)

    graph = copy.deepcopy(template)

    _set_field(graph[node_map.prompt], ["text"], combo["positive"])

    if node_map.negative is not None:
        _set_field(graph[node_map.negative], ["text"], combo["negative"])

    _set_field(graph[node_map.seed], ["seed", "noise_seed"], int(seed))

    if combo["checkpoint"] and node_map.ckpt is not None:
        _set_field(graph[node_map.ckpt], ["ckpt_name"], combo["checkpoint"])

    graph, _model_out, _clip_out = splice_lora_chain(
        graph,
        node_map.model_src,
        node_map.clip_src,
        combo["loras"],
    )

    return graph
