from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from commerce_support.app import create_app
from commerce_support.config import Settings


def test_disconnect_closes_upstream_over_a_real_http_connection(local_http_server):
    async def verify_disconnect() -> None:
        upstream_closed = asyncio.Event()

        class BlockingGateway:
            async def stream(self, _messages):
                try:
                    yield SimpleNamespace(content="first answer", finish_reason=None)
                    await asyncio.Event().wait()
                finally:
                    upstream_closed.set()

            async def aclose(self):
                return None

        app = create_app(
            settings=Settings(_env_file=None, llm_api_key=None),
            gateway=BlockingGateway(),
        )

        async with (
            local_http_server(app) as base_url,
            httpx.AsyncClient(timeout=5, trust_env=False) as client,
            client.stream(
                "POST",
                f"{base_url}/chat/stream",
                json={"message": "Help with my order"},
            ) as response,
        ):
            assert response.status_code == 200
            lines = response.aiter_lines()
            event_line = await asyncio.wait_for(lines.__anext__(), timeout=2)
            data_line = await asyncio.wait_for(lines.__anext__(), timeout=2)
            assert event_line == "event: delta"
            assert data_line == 'data: {"content":"first answer"}'
            await response.aclose()

            await asyncio.wait_for(upstream_closed.wait(), timeout=3)

    asyncio.run(verify_disconnect())


def test_disconnect_before_first_delta_closes_blocked_upstream(local_http_server):
    async def verify_early_disconnect() -> None:
        upstream_started = asyncio.Event()
        upstream_closed = asyncio.Event()

        class BlockingGateway:
            async def stream(self, _messages):
                try:
                    upstream_started.set()
                    await asyncio.Event().wait()
                    yield SimpleNamespace(content="unreachable", finish_reason="stop")
                finally:
                    upstream_closed.set()

            async def aclose(self):
                return None

        app = create_app(
            settings=Settings(_env_file=None, llm_api_key=None),
            gateway=BlockingGateway(),
        )

        async with (
            local_http_server(app) as base_url,
            httpx.AsyncClient(timeout=None, trust_env=False) as client,
        ):
            request = client.build_request(
                "POST",
                f"{base_url}/chat/stream",
                json={"message": "Help with my order"},
            )
            pending_response = asyncio.create_task(client.send(request, stream=True))
            await asyncio.wait_for(upstream_started.wait(), timeout=2)
            pending_response.cancel()
            try:
                await pending_response
            except asyncio.CancelledError:
                pass

            await asyncio.wait_for(upstream_closed.wait(), timeout=1)

    asyncio.run(verify_early_disconnect())
