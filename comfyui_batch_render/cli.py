"""Command-line interface: ``brp`` subcommands.

Subcommands:
    expand    print the expanded job list + count
    dry-run   patch graphs offline and write graphs + manifest
    run       render against a live ComfyUI server
    ping      connectivity smoke test against /system_stats
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from . import config as cfg
from .comfy_client import ComfyClient, ComfyClientError
from .pipeline import Pipeline, expand_jobs
from .runner import dry_run, run_pipeline

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8188


# --------------------------------------------------------------------------- #
# Resolution helpers
# --------------------------------------------------------------------------- #


def _resolve_template_path(pipeline: Pipeline, pipeline_path: Any, template: Any) -> Path:
    """Pick the template path from ``--template`` or the pipeline field.

    A relative ``workflow_template`` is resolved against the pipeline file's
    directory so pipelines are portable.
    """
    if template:
        return Path(template)
    tpl = pipeline.workflow_template
    if not tpl:
        raise ValueError(
            "no template given: pass --template or set workflow_template in the pipeline"
        )
    tpl_path = Path(tpl)
    if not tpl_path.is_absolute():
        tpl_path = Path(pipeline_path).resolve().parent / tpl_path
    return tpl_path


def _resolve_host_port(args: argparse.Namespace) -> tuple[str, int]:
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    config_path = getattr(args, "config", None)
    if config_path:
        conf = cfg.load_config(config_path)
        comfy = conf.get("comfyui", {}) if isinstance(conf, dict) else {}
        host = comfy.get("host", host)
        port = int(comfy.get("port", port))
    if getattr(args, "host", None):
        host = args.host
    if getattr(args, "port", None):
        port = int(args.port)
    return host, port


def _resolve_output_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "output", None):
        return Path(args.output)
    config_path = getattr(args, "config", None)
    if config_path:
        conf = cfg.load_config(config_path)
        if isinstance(conf, dict) and conf.get("output_dir"):
            return Path(conf["output_dir"])
    return Path("./output")


# --------------------------------------------------------------------------- #
# Subcommand handlers
# --------------------------------------------------------------------------- #


def _cmd_expand(args: argparse.Namespace) -> int:
    pipeline = cfg.load_pipeline(args.pipeline)
    jobs = expand_jobs(pipeline)
    for job in jobs:
        print(
            f"[{job.index}] base={job.base.name!r} "
            f"scenario={job.scenario.name!r} seed={job.seed}"
        )
    print(f"total: {len(jobs)} job(s)")
    return 0


def _cmd_dry_run(args: argparse.Namespace) -> int:
    pipeline = cfg.load_pipeline(args.pipeline)
    template_path = _resolve_template_path(pipeline, args.pipeline, args.template)
    template = cfg.load_template(template_path)
    output_dir = _resolve_output_dir(args)

    manifest = dry_run(pipeline, template, output_dir)

    from .runner import slugify

    base = Path(output_dir) / slugify(pipeline.name)
    print(f"dry-run: {manifest['job_count']} job(s)")
    print(f"graphs written to: {base / '_graphs'}")
    print(f"manifest written to: {base / 'manifest.json'}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    pipeline = cfg.load_pipeline(args.pipeline)
    template_path = _resolve_template_path(pipeline, args.pipeline, args.template)
    template = cfg.load_template(template_path)
    output_dir = _resolve_output_dir(args)
    host, port = _resolve_host_port(args)

    def _progress(done: int, total: int, job) -> None:
        print(
            f"[{done}/{total}] base={job.base.name!r} "
            f"scenario={job.scenario.name!r} seed={job.seed}"
        )

    async def _go() -> dict:
        async with ComfyClient(host, port) as client:
            return await run_pipeline(
                pipeline,
                template,
                client,
                output_dir,
                progress=_progress,
                timeout=args.timeout,
            )

    print(f"connecting to ComfyUI at http://{host}:{port} ...")
    try:
        manifest = asyncio.run(_go())
    except ComfyClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"done: rendered {manifest['job_count']} job(s) into {output_dir}")
    return 0


def _cmd_ping(args: argparse.Namespace) -> int:
    host, port = _resolve_host_port(args)

    async def _go() -> dict:
        async with ComfyClient(host, port) as client:
            return await client.check_connection()

    print(f"pinging ComfyUI at http://{host}:{port} ...")
    try:
        stats = asyncio.run(_go())
    except ComfyClientError as exc:
        print(f"unreachable: {exc}", file=sys.stderr)
        return 1
    system = stats.get("system", {}) if isinstance(stats, dict) else {}
    print("ok: ComfyUI reachable")
    if system:
        print(f"  system: {system}")
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brp", description="Batch-render images across base x scenario combos."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_expand = sub.add_parser("expand", help="print the expanded job list")
    p_expand.add_argument("--pipeline", required=True)
    p_expand.set_defaults(func=_cmd_expand)

    p_dry = sub.add_parser("dry-run", help="patch graphs offline")
    p_dry.add_argument("--pipeline", required=True)
    p_dry.add_argument("--template", default=None)
    p_dry.add_argument("--output", default=None)
    p_dry.set_defaults(func=_cmd_dry_run)

    p_run = sub.add_parser("run", help="render against a live ComfyUI server")
    p_run.add_argument("--pipeline", required=True)
    p_run.add_argument("--template", default=None)
    p_run.add_argument("--config", default=None)
    p_run.add_argument("--output", default=None)
    p_run.add_argument("--host", default=None)
    p_run.add_argument("--port", default=None)
    p_run.add_argument("--timeout", type=float, default=None)
    p_run.set_defaults(func=_cmd_run)

    p_ping = sub.add_parser("ping", help="connectivity smoke test")
    p_ping.add_argument("--config", default=None)
    p_ping.add_argument("--host", default=None)
    p_ping.add_argument("--port", default=None)
    p_ping.set_defaults(func=_cmd_ping)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
