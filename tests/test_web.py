"""Hermetic tests for the web layer (no ComfyUI).

Drives a real aiohttp app over an ephemeral port with a FakeDeps that supplies
canned models, a real Store on a tmp dir, and a scripted ``start_run``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiohttp
from aiohttp import web

from comfyui_batch_render.server.app import create_app
from comfyui_batch_render.store import Store

_CANNED = {
    "loras": [{"name": "a.safetensors", "subfolder": "char", "triggers": "trig"}],
    "checkpoints": [{"name": "ckpt.safetensors", "subfolder": "", "triggers": ""}],
}

_FAKE_MANIFEST = {"pipeline": "p", "job_count": 2, "jobs": []}


class FakeDeps:
    def __init__(self, store: Store) -> None:
        self.store = store

    def list_models(self, kind: str) -> list[dict]:
        return _CANNED[kind]

    def comfy_target(self):
        return ("127.0.0.1", 8188)

    async def start_run(self, pipeline, template, on_progress):
        for i in (1, 2):
            await on_progress({"done": i, "total": 2, "job": {"index": i - 1}})
            await asyncio.sleep(0)
        return _FAKE_MANIFEST


class _Server:
    def __init__(self, app: web.Application) -> None:
        self.app = app
        self.runner: web.AppRunner | None = None
        self.port: int | None = None

    async def __aenter__(self) -> "_Server":
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        assert self.runner is not None
        await self.runner.cleanup()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"


def _build(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<h1>ComfyUI Batch Render</h1>", encoding="utf-8")
    (static / "style.css").write_text("body{}", encoding="utf-8")
    store = Store(tmp_path / "cfg")
    app = create_app(FakeDeps(store), static_dir=static)
    return app


def test_health_and_models(tmp_path):
    async def go():
        async with _Server(_build(tmp_path)) as srv, aiohttp.ClientSession() as sess:
            async with sess.get(srv.url("/api/brp/health")) as r:
                assert r.status == 200
                body = await r.json()
                assert body["ok"] is True
                assert "version" in body

            async with sess.get(srv.url("/api/brp/models?kind=loras")) as r:
                assert (await r.json())["models"] == _CANNED["loras"]
            async with sess.get(srv.url("/api/brp/models?kind=checkpoints")) as r:
                assert (await r.json())["models"] == _CANNED["checkpoints"]
            async with sess.get(srv.url("/api/brp/models?kind=bogus")) as r:
                assert r.status == 400

    asyncio.run(go())


def test_settings_get_post(tmp_path):
    async def go():
        async with _Server(_build(tmp_path)) as srv, aiohttp.ClientSession() as sess:
            async with sess.get(srv.url("/api/brp/settings")) as r:
                s = (await r.json())["settings"]
                assert s["comfyui"]["host"] == "127.0.0.1"

            async with sess.post(
                srv.url("/api/brp/settings"), json={"output_dir": "/tmp/x"}
            ) as r:
                s = (await r.json())["settings"]
                assert s["output_dir"] == "/tmp/x"

    asyncio.run(go())


def test_pipelines_crud(tmp_path):
    async def go():
        async with _Server(_build(tmp_path)) as srv, aiohttp.ClientSession() as sess:
            async with sess.post(
                srv.url("/api/brp/pipelines"),
                json={"name": "Demo", "bases": [1], "scenarios": [2, 3]},
            ) as r:
                assert r.status == 201

            async with sess.get(srv.url("/api/brp/pipelines")) as r:
                pipes = (await r.json())["pipelines"]
                assert len(pipes) == 1 and pipes[0]["name"] == "Demo"

            async with sess.get(srv.url("/api/brp/pipelines/Demo")) as r:
                assert (await r.json())["pipeline"]["name"] == "Demo"

            async with sess.get(srv.url("/api/brp/pipelines/missing")) as r:
                assert r.status == 404

            async with sess.delete(srv.url("/api/brp/pipelines/Demo")) as r:
                assert r.status == 200
            async with sess.get(srv.url("/api/brp/pipelines")) as r:
                assert (await r.json())["pipelines"] == []

    asyncio.run(go())


def test_index_and_static(tmp_path):
    async def go():
        async with _Server(_build(tmp_path)) as srv, aiohttp.ClientSession() as sess:
            async with sess.get(srv.url("/batch-render")) as r:
                assert r.status == 200
                assert "Batch Render" in await r.text()
            async with sess.get(srv.url("/brp_static/style.css")) as r:
                assert r.status == 200

    asyncio.run(go())


def test_run_and_websocket_progress(tmp_path):
    async def go():
        async with _Server(_build(tmp_path)) as srv, aiohttp.ClientSession() as sess:
            async with sess.ws_connect(srv.url("/ws/brp-progress")) as ws:
                snap = await ws.receive_json()
                assert snap["type"] == "snapshot"

                async with sess.post(
                    srv.url("/api/brp/run"), json={"pipeline": {"name": "p"}}
                ) as r:
                    assert r.status == 202
                    run_id = (await r.json())["run_id"]

                seen = []
                while True:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                    seen.append(msg["type"])
                    if msg["type"] == "done":
                        assert msg["manifest"] == _FAKE_MANIFEST
                        break
                assert seen.count("progress") == 2

            # Status endpoint shows the completed run.
            async with sess.get(srv.url(f"/api/brp/runs/{run_id}")) as r:
                status = await r.json()
                assert status["status"] == "done"
                assert status["manifest"] == _FAKE_MANIFEST

            async with sess.get(srv.url("/api/brp/runs/nope")) as r:
                assert r.status == 404

    asyncio.run(go())
