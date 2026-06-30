"""Pure dataclasses for the Tier 0 graph-patcher layer.

No I/O, no network, no ComfyUI imports -- only stdlib + dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoraRef:
    """A reference to a LoRA to splice into a model/clip chain."""

    file: str  # path as ComfyUI reports it, e.g. "char/ayaka.safetensors"
    weight: float = 1.0  # strength_model
    clip_weight: float | None = None  # strength_clip; defaults to weight when None
    triggers: str = ""  # activation words auto-added to the prompt

    @classmethod
    def from_dict(cls, d: Any) -> "LoraRef":
        """Build from a plain dict; pass through existing instances."""
        if isinstance(d, LoraRef):
            return d
        return cls(
            file=d["file"],
            weight=d.get("weight", 1.0),
            clip_weight=d.get("clip_weight"),
            triggers=d.get("triggers", ""),
        )


@dataclass
class Layer:
    """A prompt layer (base or scenario) that contributes to a render."""

    name: str
    prompt: str = ""
    negative: str = ""
    checkpoint: str | None = None  # None => inherit
    loras: list[LoraRef] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Any) -> "Layer":
        """Build from a plain dict; pass through existing instances."""
        if isinstance(d, Layer):
            return d
        return cls(
            name=d["name"],
            prompt=d.get("prompt", ""),
            negative=d.get("negative", ""),
            checkpoint=d.get("checkpoint"),
            loras=[LoraRef.from_dict(x) for x in d.get("loras", [])],
        )


@dataclass
class NodeMap:
    """Where in the user's template to write each value."""

    prompt: str  # node id of positive CLIPTextEncode
    seed: str  # node id of the sampler
    model_src: list  # [node_id, output_index] producing MODEL (usually checkpoint)
    clip_src: list  # [node_id, output_index] producing CLIP
    negative: str | None = None  # node id of negative CLIPTextEncode (optional)
    ckpt: str | None = None  # node id of CheckpointLoaderSimple (optional)

    @classmethod
    def from_dict(cls, d: Any) -> "NodeMap":
        """Build from a plain dict; pass through existing instances."""
        if isinstance(d, NodeMap):
            return d
        return cls(
            prompt=d["prompt"],
            seed=d["seed"],
            model_src=list(d["model_src"]),
            clip_src=list(d["clip_src"]),
            negative=d.get("negative"),
            ckpt=d.get("ckpt"),
        )
