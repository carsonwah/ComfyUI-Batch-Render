"""The Tier 1 run engine: ties Tier 0 patching to the ComfyUI client.

``run_pipeline`` drives a live (or mock) server; ``dry_run`` is the offline
verification path that writes patched graphs + a manifest without any network.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .patcher import build_render_graph, combine_layers
from .pipeline import Pipeline, RenderJob, expand_jobs


# --------------------------------------------------------------------------- #
# Path helpers
# --------------------------------------------------------------------------- #


def slugify(s: str) -> str:
    """Make a filesystem-safe slug: lowercase, non-alnum -> '-', collapsed."""
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "untitled"


def output_path(output_dir: Any, pipeline_name: str, job: RenderJob) -> Path:
    """Compute the PNG path for a job's primary image."""
    out = Path(output_dir)
    pipeline_slug = slugify(pipeline_name)
    base_slug = slugify(job.base.name)
    scenario_slug = slugify(job.scenario.name)
    filename = f"{base_slug}__{scenario_slug}__seed{job.seed}.png"
    return out / pipeline_slug / base_slug / scenario_slug / filename


def _stem_for_extra(path: Path, n: int) -> Path:
    """Path for the n-th extra image (``_1``, ``_2``, ...)."""
    return path.with_name(f"{path.stem}_{n}{path.suffix}")


# --------------------------------------------------------------------------- #
# Graph building + manifest entry
# --------------------------------------------------------------------------- #


def build_job_graph(pipeline: Pipeline, template: dict, job: RenderJob) -> dict:
    """Patch ``template`` for a single job using the pipeline's node map."""
    return build_render_graph(
        template,
        pipeline.node_map,
        job.base,
        job.scenario,
        job.seed,
        pipeline.default_checkpoint,
    )


def _manifest_entry(pipeline: Pipeline, job: RenderJob, images: list[str]) -> dict:
    """Build a manifest record describing a single render job."""
    combo = combine_layers(job.base, job.scenario, pipeline.default_checkpoint)
    return {
        "index": job.index,
        "base": job.base.name,
        "scenario": job.scenario.name,
        "seed": job.seed,
        "checkpoint": combo["checkpoint"],
        "loras": [{"file": lr.file, "weight": lr.weight} for lr in combo["loras"]],
        "positive": combo["positive"],
        "negative": combo["negative"],
        "images": images,
    }


def _manifest(pipeline: Pipeline, jobs_entries: list[dict]) -> dict:
    return {
        "pipeline": pipeline.name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "job_count": len(jobs_entries),
        "jobs": jobs_entries,
    }


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Live run
# --------------------------------------------------------------------------- #


async def run_pipeline(
    pipeline: Pipeline,
    template: dict,
    client: Any,
    output_dir: Any,
    *,
    progress: Callable[[int, int, RenderJob], None] | None = None,
    timeout: float | None = None,
) -> dict:
    """Render every job against ``client`` and write images + a manifest."""
    out = Path(output_dir)
    pipeline_slug = slugify(pipeline.name)
    jobs = expand_jobs(pipeline)
    total = len(jobs)

    entries: list[dict] = []
    for done, job in enumerate(jobs, start=1):
        graph = build_job_graph(pipeline, template, job)
        descriptors = await client.run_graph(graph, timeout=timeout)

        primary = output_path(out, pipeline.name, job)
        rel_paths: list[str] = []
        for i, desc in enumerate(descriptors):
            data = await client.get_image(
                desc["filename"], desc.get("subfolder", ""), desc.get("type", "output")
            )
            target = primary if i == 0 else _stem_for_extra(primary, i)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            rel_paths.append(target.relative_to(out).as_posix())

        entries.append(_manifest_entry(pipeline, job, rel_paths))
        if progress is not None:
            progress(done, total, job)

    manifest = _manifest(pipeline, entries)
    _write_json(out / pipeline_slug / "manifest.json", manifest)
    return manifest


# --------------------------------------------------------------------------- #
# Offline dry run
# --------------------------------------------------------------------------- #


def dry_run(pipeline: Pipeline, template: dict, output_dir: Any) -> dict:
    """Write patched graphs + a manifest WITHOUT contacting a server."""
    out = Path(output_dir)
    pipeline_slug = slugify(pipeline.name)
    graphs_dir = out / pipeline_slug / "_graphs"
    jobs = expand_jobs(pipeline)

    entries: list[dict] = []
    for job in jobs:
        graph = build_job_graph(pipeline, template, job)
        base_slug = slugify(job.base.name)
        scenario_slug = slugify(job.scenario.name)
        graph_name = (
            f"{job.index}_{base_slug}__{scenario_slug}__seed{job.seed}.json"
        )
        _write_json(graphs_dir / graph_name, graph)
        entries.append(_manifest_entry(pipeline, job, []))

    manifest = _manifest(pipeline, entries)
    _write_json(out / pipeline_slug / "manifest.json", manifest)
    return manifest
