"""Tests for pipeline dataclasses + job expansion (pure, no I/O)."""

from __future__ import annotations

import random

import pytest

from comfyui_batch_render.models import Layer, NodeMap
from comfyui_batch_render.pipeline import (
    Pipeline,
    RenderJob,
    SeedSpec,
    expand_jobs,
)


def make_pipeline(**overrides) -> Pipeline:
    d = {
        "name": "p",
        "workflow_template": "tpl.json",
        "node_map": {
            "prompt": "6",
            "negative": "7",
            "seed": "3",
            "model_src": ["4", 0],
            "clip_src": ["4", 1],
            "ckpt": "4",
        },
        "bases": [
            {"name": "Base One", "prompt": "b1"},
            {"name": "Base Two", "prompt": "b2"},
        ],
        "scenarios": [
            {"name": "Scn A", "prompt": "sa"},
            {"name": "Scn B", "prompt": "sb"},
            {"name": "Scn C", "prompt": "sc"},
        ],
        "seed": {"mode": "fixed", "value": 7},
    }
    d.update(overrides)
    return Pipeline.from_dict(d)


def test_from_dict_parses_nested_types():
    p = make_pipeline()
    assert isinstance(p.node_map, NodeMap)
    assert all(isinstance(b, Layer) for b in p.bases)
    assert all(isinstance(s, Layer) for s in p.scenarios)
    assert isinstance(p.seed, SeedSpec)
    assert p.seed.mode == "fixed" and p.seed.value == 7


def test_expand_cartesian_count_and_order():
    p = make_pipeline()
    jobs = expand_jobs(p)
    # 2 bases x 3 scenarios x 1 seed
    assert len(jobs) == 6
    # global incrementing index
    assert [j.index for j in jobs] == [0, 1, 2, 3, 4, 5]
    # order: for base, for scenario
    combos = [(j.base.name, j.scenario.name) for j in jobs]
    assert combos == [
        ("Base One", "Scn A"),
        ("Base One", "Scn B"),
        ("Base One", "Scn C"),
        ("Base Two", "Scn A"),
        ("Base Two", "Scn B"),
        ("Base Two", "Scn C"),
    ]


def test_fixed_seed_reused_everywhere():
    p = make_pipeline(seed={"mode": "fixed", "value": 1234})
    jobs = expand_jobs(p)
    assert all(j.seed == 1234 for j in jobs)


def test_fixed_seed_missing_value_raises():
    p = make_pipeline(seed={"mode": "fixed"})
    with pytest.raises(ValueError):
        expand_jobs(p)


def test_randomize_count_and_determinism():
    p = make_pipeline(seed={"mode": "randomize", "count": 3})
    rng = random.Random(99)
    jobs = expand_jobs(p, rng=rng)
    # 2 bases x 3 scenarios x 3 seeds
    assert len(jobs) == 18
    seeds = [j.seed for j in jobs]
    assert all(0 <= s <= 2**31 - 1 for s in seeds)

    # same seeded RNG -> identical sequence
    jobs2 = expand_jobs(p, rng=random.Random(99))
    assert [j.seed for j in jobs2] == seeds


def test_unknown_seed_mode_raises():
    p = make_pipeline(seed={"mode": "bogus", "value": 1})
    with pytest.raises(ValueError):
        expand_jobs(p)


def test_render_job_is_dataclass():
    job = RenderJob(base=Layer(name="b"), scenario=Layer(name="s"), seed=5, index=0)
    assert job.seed == 5 and job.index == 0
