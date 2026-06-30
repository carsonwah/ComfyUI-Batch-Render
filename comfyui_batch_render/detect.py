"""Heuristic node-map auto-detection for BYO ComfyUI workflows.

Pure functions only -- no I/O, no network, no ComfyUI imports. Given an
API-format prompt graph (``{id: {"class_type", "inputs"}}``) this guesses which
nodes hold the positive/negative prompts, the sampler seed, and the
model/clip/checkpoint sources, so the web UI can pre-fill the slot mapping.
"""

from __future__ import annotations

from typing import Any


def _as_link(value: Any) -> list | None:
    """Return ``[node_id, idx]`` if ``value`` looks like a ComfyUI link, else None."""
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and value[0] is not None
    ):
        return [str(value[0]), value[1]]
    return None


def _inputs(node: Any) -> dict:
    """Return a node's ``inputs`` dict defensively (empty dict if absent/odd)."""
    if isinstance(node, dict):
        ins = node.get("inputs")
        if isinstance(ins, dict):
            return ins
    return {}


def _class_type(node: Any) -> str:
    if isinstance(node, dict):
        ct = node.get("class_type")
        if isinstance(ct, str):
            return ct
    return ""


def _find_first(template: dict, needle: str) -> str | None:
    """First node id (in dict order) whose class_type contains ``needle``."""
    for node_id, node in template.items():
        if needle.lower() in _class_type(node).lower():
            return str(node_id)
    return None


def _find_all(template: dict, needle: str) -> list[str]:
    return [
        str(nid)
        for nid, node in template.items()
        if needle.lower() in _class_type(node).lower()
    ]


def _feeds_save_image(template: dict, sampler_id: str) -> bool:
    """Whether ``sampler_id`` ultimately flows into a SaveImage node.

    Walks the graph forward from the sampler by following input links; bounded
    by the number of nodes so a malformed cyclic graph cannot loop forever.
    """
    save_ids = {
        nid for nid, node in template.items() if "saveimage" in _class_type(node).lower()
    }
    if not save_ids:
        return False
    # Build reverse adjacency: producer_id -> set(consumer_ids).
    consumers: dict[str, set[str]] = {}
    for nid, node in template.items():
        for value in _inputs(node).values():
            link = _as_link(value)
            if link is not None:
                consumers.setdefault(link[0], set()).add(str(nid))
    # BFS forward from the sampler.
    seen: set[str] = set()
    frontier = [str(sampler_id)]
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        if cur in save_ids:
            return True
        frontier.extend(consumers.get(cur, ()))
    return False


def _pick_sampler(template: dict, notes: list[str]) -> str | None:
    """Choose a KSampler: prefer one that feeds a SaveImage, else the first."""
    samplers = _find_all(template, "KSampler")
    if not samplers:
        notes.append("No KSampler node found; seed/prompt links unresolved.")
        return None
    if len(samplers) > 1:
        feeding = [s for s in samplers if _feeds_save_image(template, s)]
        if feeding:
            if len(feeding) > 1:
                notes.append(
                    f"Multiple samplers feed SaveImage ({', '.join(feeding)}); "
                    f"using {feeding[0]}."
                )
            else:
                notes.append(
                    f"Multiple samplers found ({', '.join(samplers)}); "
                    f"chose {feeding[0]} (feeds SaveImage)."
                )
            return feeding[0]
        notes.append(
            f"Multiple samplers found ({', '.join(samplers)}); none traced to "
            f"SaveImage, using {samplers[0]}."
        )
    return samplers[0]


def detect_node_map(template: dict) -> dict:
    """Best-effort guess of the slot mapping for an API-format workflow graph.

    Returns ``{"node_map": {...}, "default_checkpoint": str|None,
    "notes": [str, ...]}``. Unresolved slots are left as ``None`` with an
    explanatory note. Never raises on malformed input.
    """
    notes: list[str] = []
    node_map: dict[str, Any] = {
        "prompt": None,
        "negative": None,
        "seed": None,
        "model_src": None,
        "clip_src": None,
        "ckpt": None,
    }
    default_checkpoint: str | None = None

    if not isinstance(template, dict) or not template:
        notes.append("Template is empty or not a graph mapping.")
        return {
            "node_map": node_map,
            "default_checkpoint": None,
            "notes": notes,
        }

    # -- checkpoint ----------------------------------------------------------- #
    ckpt_id = _find_first(template, "CheckpointLoader")
    if ckpt_id is not None:
        node_map["ckpt"] = ckpt_id
        node_map["model_src"] = [ckpt_id, 0]
        node_map["clip_src"] = [ckpt_id, 1]
        ckpt_name = _inputs(template[ckpt_id]).get("ckpt_name")
        if isinstance(ckpt_name, str) and ckpt_name:
            default_checkpoint = ckpt_name
        else:
            notes.append(
                f"Checkpoint node {ckpt_id} has no usable 'ckpt_name'."
            )
    else:
        notes.append(
            "No CheckpointLoader node found; model_src/clip_src/ckpt unresolved."
        )

    # -- sampler + prompt links ---------------------------------------------- #
    sampler_id = _pick_sampler(template, notes)
    if sampler_id is not None:
        node_map["seed"] = sampler_id
        sampler_inputs = _inputs(template[sampler_id])

        pos = _as_link(sampler_inputs.get("positive"))
        if pos is not None and pos[0] in template:
            node_map["prompt"] = pos[0]
        else:
            notes.append(
                f"Sampler {sampler_id} 'positive' input is not a resolvable link."
            )

        neg = _as_link(sampler_inputs.get("negative"))
        if neg is not None and neg[0] in template:
            node_map["negative"] = neg[0]
        else:
            notes.append(
                f"Sampler {sampler_id} 'negative' input is not a resolvable link."
            )

    return {
        "node_map": node_map,
        "default_checkpoint": default_checkpoint,
        "notes": notes,
    }
