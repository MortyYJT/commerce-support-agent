from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from commerce_support.app import create_app
from commerce_support.config import Settings


def _chunk(content: str, finish_reason: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, finish_reason=finish_reason)


class FakeGateway:
    def __init__(self, outputs):
        self.outputs = outputs
        self.messages = None

    async def stream(self, messages):
        self.messages = messages
        for output in self.outputs:
            if isinstance(output, BaseException):
                raise output
            yield output

    async def aclose(self):
        return None


def _read_events(response):
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) == 2:
            events.append((lines[0].removeprefix("event: "), json.loads(lines[1][6:])))
    return events


def _post(gateway, payload, *, input_token_budget=4096):
    app = create_app(
        settings=Settings(
            _env_file=None,
            llm_api_key=None,
            input_token_budget=input_token_budget,
        ),
        gateway=gateway,
    )
    with TestClient(app) as client:
        return client.post("/chat/stream", json=payload)


def test_chat_stream_uses_server_prompt_and_emits_one_terminal_done():
    gateway = FakeGateway([_chunk("Hello"), _chunk("!"), _chunk("", "stop")])
    payload = {
        "message": "Where is order A-007?",
        "history": [
            {"role": "user", "content": "My order is A-007."},
            {"role": "assistant", "content": "How can I help with it?"},
        ],
    }

    response = _post(gateway, payload)

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert _read_events(response) == [
        ("delta", {"content": "Hello"}),
        ("delta", {"content": "!"}),
        ("done", {"finish_reason": "stop"}),
    ]
    assert [message.type for message in gateway.messages] == ["system", "human", "ai", "human"]
    assert [message.content for message in gateway.messages[1:]] == [
        "My order is A-007.",
        "How can I help with it?",
        "Where is order A-007?",
    ]


def test_error_after_delta_has_no_done_and_hides_upstream_details(caplog):
    secret_marker = "upstream-secret-detail"
    gateway = FakeGateway([_chunk("partial"), RuntimeError(secret_marker)])

    response = _post(gateway, {"message": "Help with my order"})

    assert response.status_code == 200
    events = _read_events(response)
    assert events[0] == ("delta", {"content": "partial"})
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "upstream_unavailable"
    assert "message" in events[-1][1]
    assert all(event != "done" for event, _data in events)
    assert secret_marker not in response.text
    assert secret_marker not in caplog.text


def test_empty_stream_is_http_error_before_sse_starts():
    response = _post(FakeGateway([]), {"message": "Help with my order"})

    assert response.status_code == 502
    assert response.json()["code"] == "empty_upstream_response"


def test_length_finish_after_delta_is_error_without_done():
    response = _post(
        FakeGateway([_chunk("partial answer"), _chunk("", "length")]),
        {"message": "Help with my order"},
    )

    events = _read_events(response)
    assert events[0] == ("delta", {"content": "partial answer"})
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "output_truncated"
    assert all(event != "done" for event, _data in events)


def test_stream_without_finish_reason_is_error_without_done():
    response = _post(
        FakeGateway([_chunk("partial answer")]),
        {"message": "Help with my order"},
    )

    events = _read_events(response)
    assert events[0] == ("delta", {"content": "partial answer"})
    assert events[-1] == (
        "error",
        {
            "code": "incomplete_upstream_stream",
            "message": "模型回复意外中断，请重试。",
        },
    )
    assert all(event != "done" for event, _data in events)


def test_invalid_request_and_over_budget_fail_before_calling_gateway():
    invalid_gateway = FakeGateway([_chunk("unused"), _chunk("", "stop")])
    invalid = _post(
        invalid_gateway,
        {
            "message": "Help with my order",
            "history": [{"role": "user", "content": "incomplete"}],
        },
    )
    assert invalid.status_code == 422
    assert invalid_gateway.messages is None

    budget_gateway = FakeGateway([_chunk("unused"), _chunk("", "stop")])
    over_budget = _post(
        budget_gateway,
        {"message": "Help with my order"},
        input_token_budget=1,
    )
    assert over_budget.status_code == 413
    assert over_budget.json()["code"] == "context_budget_exceeded"
    assert budget_gateway.messages is None


def test_chat_without_api_key_returns_safe_configuration_error():
    response = _post(None, {"message": "Help with my order"})

    assert response.status_code == 503
    assert response.json() == {
        "code": "model_not_configured",
        "message": "模型服务尚未配置 API 密钥。",
    }


def test_first_delta_arrives_before_upstream_completion(local_http_server):
    async def verify_first_delta() -> None:
        allow_finish = asyncio.Event()
        upstream_finished = asyncio.Event()

        class ControlledGateway:
            async def stream(self, _messages):
                yield _chunk("first answer")
                await allow_finish.wait()
                upstream_finished.set()
                yield _chunk("", "stop")

            async def aclose(self):
                return None

        app = create_app(
            settings=Settings(_env_file=None, llm_api_key=None),
            gateway=ControlledGateway(),
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
            assert await asyncio.wait_for(lines.__anext__(), timeout=2) == "event: delta"
            assert await asyncio.wait_for(lines.__anext__(), timeout=2) == (
                'data: {"content":"first answer"}'
            )
            assert not upstream_finished.is_set()

            allow_finish.set()
            remaining_lines = [line async for line in lines]
            assert "event: done" in remaining_lines
            assert 'data: {"finish_reason":"stop"}' in remaining_lines
            assert upstream_finished.is_set()

    asyncio.run(verify_first_delta())
