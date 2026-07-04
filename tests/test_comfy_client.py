"""End-to-end tests for ComfyClient against an in-process aiohttp mock server.

The mock implements the same HTTP/WS contract as a real ComfyUI server, bound
to an ephemeral port on 127.0.0.1. Tests are hermetic -- no real ComfyUI and no
pytest-asyncio (each test drives its own ``asyncio.run``).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from aiohttp import web

from comfyui_batch_render.comfy_client import ComfyClient, ComfyClientError

MOCK_IMAGE = {"filename": "out_0001.png", "subfolder": "sub", "type": "output"}

PENDING: web.AppKey = web.AppKey("pending", asyncio.Queue)
MODE: web.AppKey = web.AppKey("mode", str)
LAST_BODY: web.AppKey = web.AppKey("last_body", dict)


def _make_app(mode: str = "ok") -> web.Application:
    """Build a mock ComfyUI app. ``mode`` is 'ok' or 'error'."""
    app = web.Application()
    app[PENDING] = asyncio.Queue()
    app[MODE] = mode
    app[LAST_BODY] = {}

    async def prompt(request: web.Request) -> web.Response:
        body = await request.json()
        request.app[LAST_BODY].clear()
        request.app[LAST_BODY].update(body)
        graph = body.get("prompt")
        if "prompt" not in body or graph == "BAD":
            return web.json_response(
                {"error": "invalid prompt", "node_errors": {"3": "bad seed"}},
                status=400,
            )
        if graph == "NODEERR":
            return web.json_response(
                {"prompt_id": "", "number": 1, "node_errors": {"6": "bad clip"}}
            )
        prompt_id = str(uuid.uuid4())
        await app[PENDING].put(prompt_id)
        return web.json_response(
            {"prompt_id": prompt_id, "number": 1, "node_errors": {}}
        )

    async def ws(request: web.Request) -> web.WebSocketResponse:
        wsr = web.WebSocketResponse()
        await wsr.prepare(request)
        prompt_id = await app[PENDING].get()
        # noise messages the client must ignore
        await wsr.send_json({"type": "status", "data": {"status": {"exec_info": {}}}})
        await wsr.send_json(
            {"type": "execution_cached", "data": {"nodes": [], "prompt_id": prompt_id}}
        )
        await wsr.send_json(
            {"type": "executing", "data": {"node": "3", "prompt_id": prompt_id}}
        )
        await wsr.send_json(
            {"type": "progress", "data": {"value": 1, "max": 1, "prompt_id": prompt_id}}
        )
        if app[MODE] == "error":
            await wsr.send_json(
                {
                    "type": "execution_error",
                    "data": {"prompt_id": prompt_id, "exception_message": "boom"},
                }
            )
        else:
            # terminal: node is null for this prompt
            await wsr.send_json(
                {"type": "executing", "data": {"node": None, "prompt_id": prompt_id}}
            )
        await wsr.close()
        return wsr

    async def history(request: web.Request) -> web.Response:
        prompt_id = request.match_info["prompt_id"]
        return web.json_response(
            {prompt_id: {"outputs": {"9": {"images": [MOCK_IMAGE]}}}}
        )

    async def view(request: web.Request) -> web.Response:
        filename = request.query.get("filename", "")
        return web.Response(body=f"bytes-for-{filename}".encode())

    async def system_stats(request: web.Request) -> web.Response:
        return web.json_response(
            {"system": {"comfyui_version": "mock"}, "devices": []}
        )

    app.router.add_post("/prompt", prompt)
    app.router.add_get("/ws", ws)
    app.router.add_get("/history/{prompt_id}", history)
    app.router.add_get("/view", view)
    app.router.add_get("/system_stats", system_stats)
    return app


class _MockServer:
    """Async context manager starting the mock on an ephemeral port."""

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.runner: web.AppRunner | None = None
        self.port: int | None = None
        self.app: web.Application | None = None

    async def __aenter__(self) -> "_MockServer":
        self.app = _make_app(self.mode)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        assert self.runner is not None
        await self.runner.cleanup()


def test_run_graph_end_to_end():
    async def go():
        async with _MockServer() as server:
            async with ComfyClient("127.0.0.1", server.port) as client:
                descriptors = await client.run_graph({"prompt": "graph"})
                assert descriptors == [
                    {
                        "filename": "out_0001.png",
                        "subfolder": "sub",
                        "type": "output",
                        "node_id": "9",
                    }
                ]
                data = await client.get_image("out_0001.png", "sub", "output")
                assert data == b"bytes-for-out_0001.png"

    asyncio.run(go())


def test_check_connection():
    async def go():
        async with _MockServer() as server:
            async with ComfyClient("127.0.0.1", server.port) as client:
                stats = await client.check_connection()
                assert stats["system"]["comfyui_version"] == "mock"

    asyncio.run(go())


def test_queue_prompt_returns_id():
    async def go():
        async with _MockServer() as server:
            async with ComfyClient("127.0.0.1", server.port) as client:
                prompt_id = await client.queue_prompt({"any": "graph"})
                assert isinstance(prompt_id, str) and prompt_id

    asyncio.run(go())


def test_queue_prompt_attaches_workflow_metadata():
    """The submit payload includes extra_data.extra_pnginfo.workflow so that
    UI-oriented nodes (e.g. WidgetToString) can locate nodes under the API."""
    async def go():
        graph = {
            "30": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "model.safetensors"},
                "_meta": {"title": "Load Checkpoint"},
            },
            "49": {
                "class_type": "WidgetToString",
                "inputs": {"id": 30, "widget_name": "ckpt_name"},
            },
        }
        async with _MockServer() as server:
            async with ComfyClient("127.0.0.1", server.port) as client:
                await client.queue_prompt(graph)
            wf = server.app[LAST_BODY]["extra_data"]["extra_pnginfo"]["workflow"]
            ids = {n["id"] for n in wf["nodes"]}
            assert 30 in ids and 49 in ids

    asyncio.run(go())


def test_wait_for_completion_raises_on_execution_error():
    async def go():
        async with _MockServer(mode="error") as server:
            async with ComfyClient("127.0.0.1", server.port) as client:
                # push a prompt so the mock WS has a prompt_id to signal on
                prompt_id = await client.queue_prompt({"any": "graph"})
                with pytest.raises(ComfyClientError):
                    await client.wait_for_completion(prompt_id)

    asyncio.run(go())


def test_run_graph_raises_on_execution_error():
    async def go():
        async with _MockServer(mode="error") as server:
            async with ComfyClient("127.0.0.1", server.port) as client:
                with pytest.raises(ComfyClientError):
                    await client.run_graph({"prompt": "graph"})

    asyncio.run(go())


def test_queue_prompt_http_400_raises():
    async def go():
        async with _MockServer() as server:
            async with ComfyClient("127.0.0.1", server.port) as client:
                # mock returns HTTP 400 when the wrapped graph is "BAD"
                with pytest.raises(ComfyClientError):
                    await client.queue_prompt("BAD")

    asyncio.run(go())


def test_queue_prompt_node_errors_raises():
    async def go():
        async with _MockServer() as server:
            async with ComfyClient("127.0.0.1", server.port) as client:
                # mock returns 200 but with node_errors populated
                with pytest.raises(ComfyClientError):
                    await client.queue_prompt("NODEERR")

    asyncio.run(go())
