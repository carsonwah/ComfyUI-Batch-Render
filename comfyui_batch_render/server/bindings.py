"""ComfyUI-bound :class:`Deps` implementation.

All ComfyUI imports (``folder_paths``, ``server``) happen lazily *inside*
methods, so this module is import-safe in any environment (tests, plain CLI).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from .. import config as cfg
from ..comfy_client import ComfyClient
from ..pipeline import Pipeline
from ..runner import run_pipeline
from ..store import Store

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8188


class ComfyDeps:
    """Wires the web layer to a live ComfyUI install + the on-disk store."""

    def __init__(self, store: Store | None = None) -> None:
        self.store = store if store is not None else Store()

    # -- models ------------------------------------------------------------- #

    def list_models(self, kind: str) -> list[dict]:
        """List loras/checkpoints from ``folder_paths``, enriched with triggers."""
        import folder_paths  # type: ignore  # ComfyUI runtime only

        if kind not in ("loras", "checkpoints"):
            raise ValueError(f"unknown kind: {kind}")

        out: list[dict] = []
        for rel in folder_paths.get_filename_list(kind):
            rel_str = str(rel).replace("\\", "/")
            subfolder, _, name = rel_str.rpartition("/")
            triggers = self._read_triggers(kind, rel)
            out.append(
                {"name": name, "subfolder": subfolder, "triggers": triggers}
            )
        return out

    @staticmethod
    def _read_triggers(kind: str, rel: str) -> str:
        """Best-effort read of a sidecar ``<file>.metadata.json`` for triggers."""
        try:
            import folder_paths  # type: ignore

            full = folder_paths.get_full_path(kind, rel)
            if not full:
                return ""
            sidecar = Path(str(full) + ".metadata.json")
            if not sidecar.exists():
                return ""
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            return ""

        # Be defensive about schema variations across metadata tools.
        try:
            if isinstance(data, dict):
                for key in ("trigger_words", "triggerWords", "triggers", "activation_text"):
                    val = data.get(key)
                    if isinstance(val, list):
                        return ", ".join(str(v) for v in val if v)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
        except Exception:
            return ""
        return ""

    # -- target ------------------------------------------------------------- #

    def comfy_target(self) -> tuple[str, int | None]:
        """Resolve the running ComfyUI server's host/port (best effort)."""
        host, port = _DEFAULT_HOST, None
        try:
            import server  # type: ignore  # ComfyUI runtime only

            instance = server.PromptServer.instance
            port = getattr(instance, "port", None)
            host = getattr(instance, "address", None) or _DEFAULT_HOST
            if host in ("0.0.0.0", ""):
                host = _DEFAULT_HOST
        except Exception:
            pass
        return host, port

    # -- recapture ---------------------------------------------------------- #

    def request_recapture(self) -> bool:
        """Ask the ComfyUI frontend to re-capture the open workflow.

        Pushes a ``brp_recapture`` websocket event that ``top_menu.js`` listens
        for; the frontend responds by POSTing the live graph to
        ``/api/brp/capture`` (which then notifies open Batch Render tabs). This
        is the only way the standalone UI can pull a fresh canvas snapshot,
        since ``graphToPrompt()`` exists only in the ComfyUI frontend. Returns
        ``True`` if the signal was sent, ``False`` if ComfyUI is unreachable.
        """
        try:
            import server  # type: ignore  # ComfyUI runtime only

            server.PromptServer.instance.send_sync("brp_recapture", {})
            return True
        except Exception:
            return False

    # -- run ---------------------------------------------------------------- #

    def _resolve_template(self, pipeline: Pipeline, template: dict | None) -> dict:
        """Pick the workflow template: explicit dict > pipeline field > default."""
        if isinstance(template, dict) and template:
            return template
        settings = self.store.get_settings()
        tpl_ref = pipeline.workflow_template or settings.get("default_template")
        if not tpl_ref:
            raise ValueError(
                "no template: pass a 'template' or set workflow_template/default_template"
            )
        return cfg.load_template(tpl_ref)

    async def start_run(
        self,
        pipeline: dict,
        template: dict | None,
        on_progress: Callable[[dict], Any],
    ) -> dict:
        """Render a batch against live ComfyUI, mapping progress to ``on_progress``."""
        pipe = Pipeline.from_dict(pipeline)
        tpl = self._resolve_template(pipe, template)

        settings = self.store.get_settings()
        comfy = settings.get("comfyui", {}) if isinstance(settings, dict) else {}
        host = comfy.get("host") or _DEFAULT_HOST
        _, live_port = self.comfy_target()
        port = comfy.get("port") or live_port or _DEFAULT_PORT
        output_dir = settings.get("output_dir", "./output")

        jobs_total: dict[str, int] = {}
        loop = asyncio.get_running_loop()

        def _progress(done: int, total: int, job: Any) -> None:
            jobs_total["total"] = total
            loop.create_task(
                on_progress(
                    {
                        "done": done,
                        "total": total,
                        "job": {
                            "index": job.index,
                            "base": job.base.name,
                            "scenario": job.scenario.name,
                            "seed": job.seed,
                        },
                    }
                )
            )

        async with ComfyClient(host, int(port)) as client:
            return await run_pipeline(
                pipe, tpl, client, output_dir, progress=_progress
            )
