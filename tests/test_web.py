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
    def __init__(self, store: Store, *, recapture_ok: bool = True) -> None:
        self.store = store
        self.recapture_ok = recapture_ok
        self.recapture_calls = 0

    def list_models(self, kind: str) -> list[dict]:
        return _CANNED[kind]

    def comfy_target(self):
        return ("127.0.0.1", 8188)

    def request_recapture(self) -> bool:
        self.recapture_calls += 1
        return self.recapture_ok

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


def test_detect_template_dict(tmp_path):
    template = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "x.safetensors"},
        },
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1]}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 0,
                "positive": ["6", 0],
                "negative": ["7", 0],
                "model": ["4", 0],
            },
        },
    }

    async def go():
        async with _Server(_build(tmp_path)) as srv, aiohttp.ClientSession() as sess:
            async with sess.post(
                srv.url("/api/brp/detect"), json={"template": template}
            ) as r:
                assert r.status == 200
                body = await r.json()
                assert body["node_map"]["prompt"] == "6"
                assert body["node_map"]["seed"] == "3"
                assert body["default_checkpoint"] == "x.safetensors"

            # Bad path -> 400.
            async with sess.post(
                srv.url("/api/brp/detect"),
                json={"path": "does/not/exist.json"},
            ) as r:
                assert r.status == 400
                assert "error" in await r.json()

    asyncio.run(go())


def test_capture_roundtrip(tmp_path):
    template = {"3": {"class_type": "KSampler", "inputs": {"seed": 0}}}

    async def go():
        async with _Server(_build(tmp_path)) as srv, aiohttp.ClientSession() as sess:
            # Empty slot to start.
            async with sess.get(srv.url("/api/brp/capture")) as r:
                assert r.status == 200
                assert (await r.json())["captured"] is None

            # POST stores the latest capture; GET returns it (survives reload).
            async with sess.post(
                srv.url("/api/brp/capture"),
                json={"template": template, "source": "comfyui-canvas", "ts": 42},
            ) as r:
                assert r.status == 200
                assert (await r.json())["nodes"] == 1
            for _ in range(2):  # idempotent across repeated reads
                async with sess.get(srv.url("/api/brp/capture")) as r:
                    cap = (await r.json())["captured"]
                    assert cap["template"] == template
                    assert cap["source"] == "comfyui-canvas"
                    assert cap["ts"] == 42

            # An empty/missing template is rejected.
            async with sess.post(
                srv.url("/api/brp/capture"), json={"template": {}}
            ) as r:
                assert r.status == 400

            # DELETE clears the slot.
            async with sess.delete(srv.url("/api/brp/capture")) as r:
                assert r.status == 200
            async with sess.get(srv.url("/api/brp/capture")) as r:
                assert (await r.json())["captured"] is None

    asyncio.run(go())


def test_request_recapture_and_capture_broadcast(tmp_path):
    template = {"3": {"class_type": "KSampler", "inputs": {"seed": 0}}}
    store = Store(tmp_path / "cfg")
    deps = FakeDeps(store)
    app = create_app(deps)

    async def go():
        async with _Server(app) as srv, aiohttp.ClientSession() as sess:
            # An open tab subscribes to the progress channel.
            async with sess.ws_connect(srv.url("/ws/brp-progress")) as ws:
                assert (await ws.receive_json())["type"] == "snapshot"

                # The UI asks ComfyUI (via the server) to re-capture.
                async with sess.post(srv.url("/api/brp/request-recapture")) as r:
                    assert r.status == 200
                    assert (await r.json())["ok"] is True
                assert deps.recapture_calls == 1

                # When the frontend POSTs the fresh snapshot, open tabs are
                # notified with a lightweight "capture" signal.
                async with sess.post(
                    srv.url("/api/brp/capture"), json={"template": template}
                ) as r:
                    assert r.status == 200
                msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                assert msg["type"] == "capture"

    asyncio.run(go())


def test_request_recapture_unreachable(tmp_path):
    store = Store(tmp_path / "cfg")
    deps = FakeDeps(store, recapture_ok=False)
    app = create_app(deps)

    async def go():
        async with _Server(app) as srv, aiohttp.ClientSession() as sess:
            async with sess.post(srv.url("/api/brp/request-recapture")) as r:
                assert r.status == 200
                assert (await r.json())["ok"] is False

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
