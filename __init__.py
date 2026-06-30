"""ComfyUI custom-node entry point.

Safe to import anywhere: the ComfyUI binding is guarded so that outside a real
ComfyUI process (e.g. tests) it is skipped silently rather than crashing.
"""

from __future__ import annotations

import logging

NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}
WEB_DIRECTORY = "./web/comfyui"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

_log = logging.getLogger("comfyui_batch_render")

try:  # Bind to ComfyUI's aiohttp server when running inside ComfyUI.
    from server import PromptServer  # type: ignore

    from comfyui_batch_render.server.app import register_routes
    from comfyui_batch_render.server.bindings import ComfyDeps

    register_routes(PromptServer.instance.app, ComfyDeps())
    _log.info("ComfyUI Batch Render: web routes registered at /batch-render")
except ImportError:
    # Not running inside ComfyUI (no `server` module) -- nothing to bind.
    pass
except Exception as exc:  # never crash ComfyUI on a binding error
    _log.warning("ComfyUI Batch Render: failed to register web routes: %s", exc)
