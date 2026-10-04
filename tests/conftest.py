from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from commerce_support.app import create_app
from commerce_support.config import Settings
from commerce_support.errors import AppError
from commerce_support.schemas import ChatRequest


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
