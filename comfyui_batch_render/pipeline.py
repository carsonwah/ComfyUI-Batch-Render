"""Pipeline dataclasses and job expansion (pure, no I/O).

A *pipeline* is the cartesian product of bases x scenarios x seeds. Expanding it
yields a flat ordered list of :class:`RenderJob` objects that the runner turns
into patched graphs.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .models import Layer, NodeMap

# Inclusive upper bound for randomized seeds (signed 32-bit range).
_SEED_MAX = 2**31 - 1


@dataclass
class SeedSpec:
    """How seeds are chosen for each base x scenario combination."""

    mode: str  # "fixed" | "randomize"
    value: int | None = None  # required when mode == "fixed"
    count: int = 1  # how many seeds when mode == "randomize"

    @classmethod
    def from_dict(cls, d: Any) -> "SeedSpec":
        if isinstance(d, SeedSpec):
            return d
        return cls(
            mode=d.get("mode", "fixed"),
            value=d.get("value"),
            count=int(d.get("count", 1)),
        )


@dataclass
class Pipeline:
    """A full render plan: template + node map + layers + seed policy."""

    name: str
    workflow_template: str
    node_map: NodeMap
    bases: list[Layer]
    scenarios: list[Layer]
    seed: SeedSpec
    default_checkpoint: str | None = None
    defaults: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Any) -> "Pipeline":
        if isinstance(d, Pipeline):
            return d
        return cls(
            name=d.get("name", "pipeline"),
            workflow_template=d.get("workflow_template", ""),
            node_map=NodeMap.from_dict(d["node_map"]),
            bases=[Layer.from_dict(x) for x in d.get("bases", [])],
            scenarios=[Layer.from_dict(x) for x in d.get("scenarios", [])],
            seed=SeedSpec.from_dict(d.get("seed", {})),
            default_checkpoint=d.get("default_checkpoint"),
            defaults=dict(d.get("defaults", {})),
        )


@dataclass
class RenderJob:
    """A single render: one base x one scenario x one seed."""

    base: Layer
    scenario: Layer
    seed: int
    index: int


def expand_jobs(
    pipeline: Pipeline, *, rng: random.Random | None = None
) -> list[RenderJob]:
    """Expand a pipeline into an ordered flat list of render jobs.

    Order is: for each base, for each scenario, for each seed. ``index`` is a
    global incrementing counter starting at 0. Randomized seeds are drawn from
    ``rng`` (or a fresh module ``random.Random``) in ``[0, 2**31 - 1]``.
    """
    if rng is None:
        rng = random.Random()

    spec = pipeline.seed
    if spec.mode not in ("fixed", "randomize"):
        raise ValueError(f"unknown seed mode: {spec.mode!r}")
    if spec.mode == "fixed" and spec.value is None:
        raise ValueError("seed mode 'fixed' requires a 'value'")

    jobs: list[RenderJob] = []
    index = 0
    for base in pipeline.bases:
        for scenario in pipeline.scenarios:
            if spec.mode == "fixed":
                seeds = [int(spec.value)]
            else:
                seeds = [rng.randint(0, _SEED_MAX) for _ in range(spec.count)]
            for seed in seeds:
                jobs.append(
                    RenderJob(
                        base=base, scenario=scenario, seed=seed, index=index
                    )
                )
                index += 1
    return jobs
