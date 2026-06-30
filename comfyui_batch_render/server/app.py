"""The testable web layer (no ComfyUI imports).

Defines the :class:`Deps` contract the app depends on, a :class:`RunManager`
that tracks background runs + websockets, and route registration that can be
attached to a fresh app (:func:`create_app`) or an existing one
(:func:`register_routes`) -- the latter is what the ComfyUI binding uses to
piggyback on ``PromptServer.instance.app``.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from aiohttp import WSMsgType, web

from .. import __version__
from ..store import Store

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# aiohttp app keys (collision-safe storage on the shared app).
DEPS_KEY = web.AppKey("brp_deps", object)
RUNS_KEY = web.AppKey("brp_runs", object)
STATIC_KEY = web.AppKey("brp_static_dir", Path)


# --------------------------------------------------------------------------- #
# Dependency contract
# --------------------------------------------------------------------------- #


class Deps(Protocol):
    """Everything the web layer needs from the host environment."""

    store: Store

    def list_models(self, kind: str) -> list[dict]:
        """Return ``[{"name","subfolder","triggers"}, ...]`` for a model kind."""
        ...

    async def start_run(
        self,
        pipeline: dict,
        template: dict | None,
        on_progress: Callable[[dict], Any],
    ) -> dict:
        """Run a batch, invoking ``on_progress`` per job, return the manifest."""
        ...

    def comfy_target(self) -> tuple[str, int | None]:
        """Return the (host, port) of the target ComfyUI server."""
        ...


# --------------------------------------------------------------------------- #
# Run tracking
# --------------------------------------------------------------------------- #


class RunManager:
    """Tracks in-flight/finished runs and live websocket subscribers."""

    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}
        self._ws: set[web.WebSocketResponse] = set()

    # -- websocket registry ------------------------------------------------- #

    def register_ws(self, ws: web.WebSocketResponse) -> None:
        self._ws.add(ws)

    def unregister_ws(self, ws: web.WebSocketResponse) -> None:
        self._ws.discard(ws)

    async def broadcast(self, msg: dict) -> None:
        """Send ``msg`` as JSON to every live ws; drop dead connections."""
        dead: list[web.WebSocketResponse] = []
        for ws in list(self._ws):
            try:
                await ws.send_json(msg)
            except Exception:  # connection gone / closing
                dead.append(ws)
        for ws in dead:
            self._ws.discard(ws)

    # -- run lifecycle ------------------------------------------------------ #

    def get(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)

    def snapshot(self) -> dict:
        return {"type": "snapshot", "runs": self.runs}

    async def start(
        self, deps: Deps, pipeline: dict, template: dict | None
    ) -> str:
        """Spawn a background task running the batch; return its run_id."""
        run_id = uuid.uuid4().hex
        self.runs[run_id] = {
            "status": "running",
            "done": 0,
            "total": None,
            "manifest": None,
            "error": None,
        }
        asyncio.create_task(self._run(deps, run_id, pipeline, template))
        return run_id

    async def _run(
        self, deps: Deps, run_id: str, pipeline: dict, template: dict | None
    ) -> None:
        record = self.runs[run_id]

        async def on_progress(msg: dict) -> None:
            record["done"] = msg.get("done", record["done"])
            if msg.get("total") is not None:
                record["total"] = msg.get("total")
            await self.broadcast(
                {"type": "progress", "run_id": run_id, **msg}
            )

        try:
            manifest = await deps.start_run(pipeline, template, on_progress)
            record["status"] = "done"
            record["manifest"] = manifest
            await self.broadcast(
                {"type": "done", "run_id": run_id, "manifest": manifest}
            )
        except Exception as exc:  # surface failures to subscribers
            record["status"] = "error"
            record["error"] = str(exc)
            await self.broadcast(
                {"type": "error", "run_id": run_id, "error": str(exc)}
            )


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


def _deps(request: web.Request) -> Deps:
    return request.app[DEPS_KEY]


def _runs(request: web.Request) -> RunManager:
    return request.app[RUNS_KEY]


async def _index(request: web.Request) -> web.StreamResponse:
    index = request.app[STATIC_KEY] / "index.html"
    if not index.exists():
        return web.json_response({"error": "index.html missing"}, status=404)
    return web.FileResponse(index)


async def _health(request: web.Request) -> web.Response:
    host, port = _deps(request).comfy_target()
    return web.json_response(
        {"ok": True, "version": __version__, "comfyui": {"host": host, "port": port}}
    )


async def _models(request: web.Request) -> web.Response:
    kind = request.query.get("kind", "loras")
    if kind not in ("loras", "checkpoints"):
        return web.json_response({"error": f"unknown kind: {kind}"}, status=400)
    try:
        models = _deps(request).list_models(kind)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)
    return web.json_response({"models": models})


async def _get_settings(request: web.Request) -> web.Response:
    return web.json_response({"settings": _deps(request).store.get_settings()})


async def _post_settings(request: web.Request) -> web.Response:
    try:
        patch = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(patch, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    settings = _deps(request).store.update_settings(patch)
    return web.json_response({"settings": settings})


async def _list_pipelines(request: web.Request) -> web.Response:
    return web.json_response({"pipelines": _deps(request).store.list_pipelines()})


async def _create_pipeline(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict) or not body.get("name"):
        return web.json_response({"error": "body must include 'name'"}, status=400)
    name = body["name"]
    _deps(request).store.save_pipeline(name, body)
    return web.json_response({"ok": True, "name": name}, status=201)


async def _get_pipeline(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    try:
        data = _deps(request).store.get_pipeline(name)
    except KeyError:
        return web.json_response({"error": f"not found: {name}"}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"pipeline": data})


async def _put_pipeline(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    try:
        _deps(request).store.save_pipeline(name, body)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, "name": name})


async def _delete_pipeline(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    try:
        removed = _deps(request).store.delete_pipeline(name)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not removed:
        return web.json_response({"error": f"not found: {name}"}, status=404)
    return web.json_response({"ok": True})


async def _run(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)

    pipeline = body.get("pipeline")
    template = body.get("template")

    # Allow referencing a saved pipeline by name.
    if isinstance(pipeline, str):
        try:
            pipeline = _deps(request).store.get_pipeline(pipeline)
        except KeyError:
            return web.json_response(
                {"error": f"pipeline not found: {body.get('pipeline')}"}, status=404
            )
    if not isinstance(pipeline, dict):
        return web.json_response(
            {"error": "'pipeline' must be a dict or a saved pipeline name"},
            status=400,
        )

    run_id = await _runs(request).start(_deps(request), pipeline, template)
    return web.json_response({"run_id": run_id}, status=202)


async def _run_status(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    record = _runs(request).get(run_id)
    if record is None:
        return web.json_response({"error": f"unknown run: {run_id}"}, status=404)
    return web.json_response({"run_id": run_id, **record})


async def _ws_progress(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    runs = _runs(request)
    runs.register_ws(ws)
    try:
        await ws.send_json(runs.snapshot())
        async for msg in ws:
            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR):
                break
            # Inbound messages are ignored; this channel is server -> client.
    finally:
        runs.unregister_ws(ws)
    return ws


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


def register_routes(
    app: web.Application, deps: Deps, *, static_dir: Path | None = None
) -> web.Application:
    """Attach all Batch-Render routes + state onto an existing app.

    Namespaced under ``/api/brp``, ``/brp_static``, ``/batch-render`` and
    ``/ws/brp-progress`` to avoid colliding with the ComfyUI host app.
    """
    static = Path(static_dir) if static_dir is not None else _STATIC_DIR

    app[DEPS_KEY] = deps
    app[RUNS_KEY] = RunManager()
    app[STATIC_KEY] = static

    app.router.add_get("/batch-render", _index)
    app.router.add_get("/api/brp/health", _health)
    app.router.add_get("/api/brp/models", _models)
    app.router.add_get("/api/brp/settings", _get_settings)
    app.router.add_post("/api/brp/settings", _post_settings)
    app.router.add_get("/api/brp/pipelines", _list_pipelines)
    app.router.add_post("/api/brp/pipelines", _create_pipeline)
    app.router.add_get("/api/brp/pipelines/{name}", _get_pipeline)
    app.router.add_put("/api/brp/pipelines/{name}", _put_pipeline)
    app.router.add_delete("/api/brp/pipelines/{name}", _delete_pipeline)
    app.router.add_post("/api/brp/run", _run)
    app.router.add_get("/api/brp/runs/{run_id}", _run_status)
    app.router.add_get("/ws/brp-progress", _ws_progress)

    # Static assets last so explicit routes win.
    if static.exists():
        app.router.add_static("/brp_static/", static, name="brp_static")

    return app


def create_app(deps: Deps, *, static_dir: Path | None = None) -> web.Application:
    """Build a fresh aiohttp app wired with the Batch-Render routes."""
    app = web.Application()

    async def _on_startup(_app: web.Application) -> None:
        # Hook reserved for future heavy init (model index warmup, etc.).
        return None

    app.on_startup.append(_on_startup)
    register_routes(app, deps, static_dir=static_dir)
    return app
