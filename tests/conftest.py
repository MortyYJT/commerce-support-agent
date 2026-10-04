import asyncio
import socket
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from commerce_support.app import create_app
from commerce_support.config import Settings
from commerce_support.errors import AppError
from commerce_support.schemas import ChatRequest


@asynccontextmanager
async def _local_http_server(application: FastAPI) -> AsyncIterator[str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    host, port = listener.getsockname()

    server = uvicorn.Server(
        uvicorn.Config(
            application,
            log_level="critical",
            access_log=False,
            timeout_graceful_shutdown=0.5,
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(500):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        if not server.started:
            raise RuntimeError("local test server did not start")
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5)
        finally:
            listener.close()


@pytest.fixture
def local_http_server():
    return _local_http_server


@pytest.fixture
def test_api() -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(
        settings=Settings(
            _env_file=None,
            llm_api_key=None,
            max_request_body_bytes=65_536,
        )
    )

    @app.post("/__test__/chat")
    async def accept_chat(request: ChatRequest) -> dict[str, object]:
        return {
            "message": request.message,
            "history": [item.model_dump() for item in request.history],
        }

    @app.get("/__test__/error")
    async def raise_test_error() -> None:
        raise AppError(
            code="TEST_ERROR",
            public_message="Safe test message",
            status_code=409,
        )

    with TestClient(app) as client:
        yield app, client
