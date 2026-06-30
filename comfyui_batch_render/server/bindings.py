"""ComfyUI-bound :class:`Deps` implementation.

All ComfyUI imports (``folder_paths``, ``server``) happen lazily *inside*
methods, so this module is import-safe in any environment (tests, plain CLI).
"""

from __future__ import annotations

import asyncio
import json
import os
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
        # Sidecar parse cache: ``sidecar path -> (mtime, slim metadata dict)``.
        # Re-parse a ``.metadata.json`` only when its mtime changes, so listing
        # hundreds of LoRAs stays cheap on repeat calls (mirrors LoRA Manager's
        # mtime-keyed model cache, minus the SQLite persistence).
        self._meta_cache: dict[str, tuple[float, dict]] = {}

    # Sidecar preview extensions, in priority order. LoRA Manager writes a
    # ``<base>.<ext>`` (and sometimes ``<base>.preview.<ext>``) next to the model.
    _PREVIEW_EXTS = (
        ".preview.png",
        ".preview.jpeg",
        ".preview.jpg",
        ".preview.webp",
        ".png",
        ".jpeg",
        ".jpg",
        ".webp",
        ".gif",
    )

    # -- models ------------------------------------------------------------- #

    def list_models(self, kind: str) -> list[dict]:
        """List loras/checkpoints from ``folder_paths``, enriched with metadata.

        Each entry carries ``file`` (the full relative path ComfyUI's loaders
        expect as ``lora_name``/``ckpt_name``), ``name``/``subfolder`` for
        display, plus ``model_name``, ``base_model``, ``tags``, ``triggers``,
        ``preview`` and ``nsfw_level`` read from the sidecar ``.metadata.json``.
        """
        import folder_paths  # type: ignore  # ComfyUI runtime only

        if kind not in ("loras", "checkpoints"):
            raise ValueError(f"unknown kind: {kind}")

        out: list[dict] = []
        for rel in folder_paths.get_filename_list(kind):
            rel_native = str(rel)
            rel_fwd = rel_native.replace("\\", "/")
            subfolder, _, name = rel_fwd.rpartition("/")
            full = folder_paths.get_full_path(kind, rel)
            meta = self._read_meta(full)
            out.append(
                {
                    # ``file`` is what LoraLoader/CheckpointLoader expect: the
                    # path relative to the models dir, native separators intact
                    # so it matches the /object_info combo exactly.
                    "file": rel_native,
                    "name": name,
                    "subfolder": subfolder,
                    "model_name": meta.get("model_name", ""),
                    "base_model": meta.get("base_model", ""),
                    "tags": meta.get("tags", []),
                    "triggers": meta.get("triggers", ""),
                    "preview": bool(full and self._find_preview(str(full))),
                    "nsfw_level": meta.get("nsfw_level", 0),
                }
            )
        return out

    def preview_path(self, kind: str, file: str) -> str | None:
        """Resolve the on-disk preview image for a model, or ``None``.

        ``file`` is the relative path from :meth:`list_models`. Resolution goes
        through ``folder_paths.get_full_path`` (which only returns paths for
        known model files), so it can't be steered outside the model roots.
        """
        import folder_paths  # type: ignore

        if kind not in ("loras", "checkpoints"):
            raise ValueError(f"unknown kind: {kind}")
        full = folder_paths.get_full_path(kind, file)
        if not full:
            return None
        return self._find_preview(str(full))

    @classmethod
    def _find_preview(cls, model_full: str) -> str | None:
        """Find a sidecar preview image next to ``model_full`` (``...x.safetensors``)."""
        base, _ext = os.path.splitext(model_full)
        for suffix in cls._PREVIEW_EXTS:
            cand = base + suffix
            if os.path.isfile(cand):
                return cand
        return None

    def _read_meta(self, full: str | None) -> dict:
        """Return slim sidecar metadata for a model, cached by sidecar mtime."""
        if not full:
            return {}
        # LoRA Manager writes ``<base>.metadata.json`` *beside* the model, i.e.
        # the model extension is replaced, not appended.
        sidecar = os.path.splitext(str(full))[0] + ".metadata.json"
        try:
            mtime = os.path.getmtime(sidecar)
        except OSError:
            return {}
        cached = self._meta_cache.get(sidecar)
        if cached and cached[0] == mtime:
            return cached[1]
        slim = self._parse_meta(sidecar)
        self._meta_cache[sidecar] = (mtime, slim)
        return slim

    @classmethod
    def _parse_meta(cls, sidecar: str) -> dict:
        """Project a ``.metadata.json`` into the fields the picker needs."""
        try:
            data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        civitai = data.get("civitai")
        civitai = civitai if isinstance(civitai, dict) else {}
        tags = data.get("tags")
        tags = [str(t) for t in tags if t] if isinstance(tags, list) else []
        return {
            "model_name": str(data.get("model_name") or ""),
            "base_model": str(data.get("base_model") or ""),
            "tags": tags,
            "triggers": cls._extract_triggers(data, civitai),
            "nsfw_level": int(data.get("preview_nsfw_level") or 0),
        }

    @staticmethod
    def _extract_triggers(data: dict, civitai: dict) -> str:
        """Pull trigger words, preferring LoRA Manager's ``civitai.trainedWords``."""
        words = civitai.get("trainedWords")
        if isinstance(words, list) and words:
            # Keep each trainedWords entry on its own line so the user can see
            # the distinct trigger-word sets and delete the ones they don't
            # want. The ",\n" separator stays valid in the prompt (the newline
            # is just whitespace to CLIP) if they leave several in place.
            parts = [str(w).strip().rstrip(",").strip() for w in words]
            parts = [p for p in parts if p]
            if parts:
                return ",\n".join(parts)
        # Fall back to flat schemas other metadata tools use.
        for key in ("trigger_words", "triggerWords", "triggers", "activation_text"):
            val = data.get(key)
            if isinstance(val, list):
                joined = ", ".join(str(v) for v in val if v)
                if joined:
                    return joined
            if isinstance(val, str) and val.strip():
                return val.strip()
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
        run_cfg = settings.get("run") if isinstance(settings, dict) else None
        run_path = run_cfg.get("workflow_path") if isinstance(run_cfg, dict) else None
        tpl_ref = (
            pipeline.workflow_template
            or run_path
            or settings.get("default_template")
        )
        if not tpl_ref:
            raise ValueError(
                "no template: pass a 'template' or set a run-time workflow"
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
