"""Async HTTP + WebSocket client for a running ComfyUI server.

This is the only module in Tier 1 that talks to a live server. It is written
against the documented ComfyUI API contract and is exercised in tests against
an in-process aiohttp mock that mimics the same contract.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import aiohttp


class ComfyClientError(Exception):
    """Raised when the ComfyUI server reports an error or is unreachable."""


class ComfyClient:
    """Async client for the ComfyUI HTTP/WebSocket API.

    Use as an async context manager so the underlying ``aiohttp.ClientSession``
    is created and cleaned up deterministically::

        async with ComfyClient("127.0.0.1", 8188) as client:
            descriptors = await client.run_graph(graph)
    """

    def __init__(self, host: str, port: int, client_id: str | None = None) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id or str(uuid.uuid4())
        self.base_url = f"http://{host}:{port}"
        self._session: aiohttp.ClientSession | None = None

    # -- context management ------------------------------------------------- #

    async def __aenter__(self) -> "ComfyClient":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise ComfyClientError(
                "ComfyClient session is not open; use 'async with ComfyClient(...)'."
            )
        return self._session

    # -- websocket helpers -------------------------------------------------- #

    def _ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/ws?clientId={self.client_id}"

    # -- HTTP endpoints ----------------------------------------------------- #

    async def queue_prompt(self, graph: dict) -> str:
        """POST a graph to ``/prompt`` and return the assigned ``prompt_id``."""
        payload = {"prompt": graph, "client_id": self.client_id}
        async with self.session.post(
            f"{self.base_url}/prompt", json=payload
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise ComfyClientError(
                    f"queue_prompt failed (HTTP {resp.status}): {text}"
                )
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise ComfyClientError(
                    f"queue_prompt returned non-JSON body: {text!r}"
                ) from exc
        node_errors = data.get("node_errors")
        if node_errors:
            raise ComfyClientError(f"queue_prompt node_errors: {node_errors}")
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyClientError(f"queue_prompt missing prompt_id: {data!r}")
        return prompt_id

    async def get_history(self, prompt_id: str) -> dict:
        """GET ``/history/{prompt_id}`` and return the parsed JSON dict."""
        async with self.session.get(
            f"{self.base_url}/history/{prompt_id}"
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise ComfyClientError(
                    f"get_history failed (HTTP {resp.status}): {text}"
                )
            return await resp.json()

    async def get_image(self, filename: str, subfolder: str, type_: str) -> bytes:
        """GET raw image bytes from ``/view``."""
        params = {"filename": filename, "subfolder": subfolder, "type": type_}
        async with self.session.get(
            f"{self.base_url}/view", params=params
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise ComfyClientError(
                    f"get_image failed (HTTP {resp.status}): {text}"
                )
            return await resp.read()

    async def check_connection(self) -> dict:
        """GET ``/system_stats`` for a connectivity smoke test."""
        try:
            async with self.session.get(
                f"{self.base_url}/system_stats"
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise ComfyClientError(
                        f"check_connection failed (HTTP {resp.status}): {text}"
                    )
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise ComfyClientError(
                f"could not reach ComfyUI at {self.base_url}: {exc}"
            ) from exc

    # -- completion waiting ------------------------------------------------- #

    async def wait_for_completion(
        self, prompt_id: str, *, timeout: float | None = None
    ) -> None:
        """Open a WS and block until ``prompt_id`` finishes executing.

        Raises ``ComfyClientError`` on an ``execution_error`` message or if the
        optional ``timeout`` (seconds) elapses first.
        """
        async with self.session.ws_connect(self._ws_url()) as ws:
            await self._consume_until_done(ws, prompt_id, timeout=timeout)

    async def _consume_until_done(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        prompt_id: str,
        *,
        timeout: float | None = None,
    ) -> None:
        """Read WS messages until the terminal ``executing`` for ``prompt_id``."""
        loop = asyncio.get_event_loop()
        deadline = None if timeout is None else loop.time() + timeout

        while True:
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise ComfyClientError(
                        f"timed out waiting for prompt {prompt_id} to complete"
                    )
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                except asyncio.TimeoutError as exc:
                    raise ComfyClientError(
                        f"timed out waiting for prompt {prompt_id} to complete"
                    ) from exc
            else:
                msg = await ws.receive()

            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except (json.JSONDecodeError, TypeError):
                    continue
                if self._is_terminal(data, prompt_id):
                    return
            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
            ):
                raise ComfyClientError(
                    f"websocket closed before prompt {prompt_id} completed"
                )
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise ComfyClientError(
                    f"websocket error while waiting for prompt {prompt_id}"
                )
            # BINARY (preview frames) and everything else: ignore safely.

    @staticmethod
    def _is_terminal(data: dict, prompt_id: str) -> bool:
        """Return True for the terminal ``executing`` message; raise on error."""
        if not isinstance(data, dict):
            return False
        msg_type = data.get("type")
        payload = data.get("data") or {}
        if msg_type == "execution_error" and payload.get("prompt_id") == prompt_id:
            raise ComfyClientError(f"execution_error for prompt {prompt_id}: {payload}")
        if msg_type == "executing":
            if payload.get("node") is None and payload.get("prompt_id") == prompt_id:
                return True
        return False

    # -- orchestration ------------------------------------------------------ #

    async def run_graph(
        self, graph: dict, *, timeout: float | None = None
    ) -> list[dict]:
        """Queue ``graph``, wait for it, and return output image descriptors.

        The WebSocket is connected *before* the prompt is queued so the terminal
        completion message cannot be missed. Bytes are NOT downloaded here; the
        caller decides what to fetch via :meth:`get_image`.

        Returns a flat list of ``{"filename","subfolder","type","node_id"}``.
        """
        async with self.session.ws_connect(self._ws_url()) as ws:
            prompt_id = await self.queue_prompt(graph)
            await self._consume_until_done(ws, prompt_id, timeout=timeout)

        history = await self.get_history(prompt_id)
        return self._extract_descriptors(history, prompt_id)

    @staticmethod
    def _extract_descriptors(history: dict, prompt_id: str) -> list[dict]:
        """Flatten history outputs into image descriptors."""
        descriptors: list[dict] = []
        entry = history.get(prompt_id, {})
        outputs = entry.get("outputs", {}) if isinstance(entry, dict) else {}
        for node_id, node_out in outputs.items():
            if not isinstance(node_out, dict):
                continue
            for img in node_out.get("images", []) or []:
                descriptors.append(
                    {
                        "filename": img.get("filename"),
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                        "node_id": node_id,
                    }
                )
        return descriptors
