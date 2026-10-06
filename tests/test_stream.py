from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from commerce_support.app import create_app
from commerce_support.chat_types import TurnContext
from commerce_support.config import Settings


def _chunk(content: str, finish_reason: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, finish_reason=finish_reason)


class FakeGateway:
    def __init__(self, outputs, *, selection: AIMessage | None = None) -> None:
        self.outputs = outputs
        self.selection = selection or AIMessage(content="")
        self.selection_messages = None
        self.final_messages = None
        self.selection_calls = 0
        self.final_calls = 0

    async def select_tools(self, messages, tools):
        self.selection_calls += 1
        self.selection_messages = messages
        self.tools = tools
        return self.selection

    async def _stream(self, messages):
        self.final_calls += 1
        self.final_messages = messages
        for output in self.outputs:
            if isinstance(output, BaseException):
                raise output
            yield output

    def stream_final(self, messages):
        return self._stream(messages)

    async def aclose(self):
        return None


class MemoryRepository:
    def __init__(self, history: list[list[Any]] | None = None) -> None:
        self.history = history or []
        self.finished: list[tuple[str, str]] = []

    async def begin_turn(self, message: str, conversation_id: str | None = None) -> TurnContext:
        return TurnContext(conversation_id or "conversation-test", "turn-test", message)

    async def successful_history(self, _conversation_id: str) -> list[list[Any]]:
        return self.history

    async def append_assistant_call(self, _ctx: TurnContext, _message: AIMessage) -> None:
        return None

    async def append_tool_result(self, _ctx: TurnContext, _call_id: str, _result: Any) -> None:
        return None

    async def finish_turn(self, _ctx: TurnContext, text: str, status: str) -> None:
        self.finished.append((text, status))


def _read_events(response):
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) == 2:
            events.append((lines[0].removeprefix("event: "), json.loads(lines[1][6:])))
    return events


def _post(gateway, payload, *, input_token_budget=4096, repository=None):
    app = create_app(
        settings=Settings(
            _env_file=None,
            llm_api_key=None,
            input_token_budget=input_token_budget,
        ),
        gateway=gateway,
        repository=repository or MemoryRepository(),
    )
    with TestClient(app) as client:
        return client.post("/chat/stream", json=payload)


def test_chat_stream_uses_server_history_and_emits_one_terminal_done():
    gateway = FakeGateway([_chunk("Hello"), _chunk("!"), _chunk("", "stop")])
    repository = MemoryRepository(
        [[
            HumanMessage(content="My order is A-007."),
            AIMessage(content="How can I help with it?"),
        ]]
    )

    response = _post(
        gateway,
        {"message": "Where is order A-007?"},
        repository=repository,
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert _read_events(response) == [
        ("conversation", {"conversation_id": "conversation-test", "turn_id": "turn-test"}),
        ("delta", {"content": "Hello"}),
        ("delta", {"content": "!"}),
        ("done", {"finish_reason": "stop"}),
    ]
    assert gateway.selection_calls == 1 and gateway.final_calls == 1
    assert [message.type for message in gateway.selection_messages] == [
        "system",
        "human",
        "ai",
        "human",
    ]
    assert [message.content for message in gateway.selection_messages[1:]] == [
        "My order is A-007.",
        "How can I help with it?",
        "Where is order A-007?",
    ]
    assert repository.finished == [("Hello!", "completed")]


def test_error_after_delta_has_no_done_and_hides_upstream_details(caplog):
    secret_marker = "upstream-secret-detail"
    gateway = FakeGateway([_chunk("partial"), RuntimeError(secret_marker)])

    response = _post(gateway, {"message": "Help with my order"})

    assert response.status_code == 200
    events = _read_events(response)
    assert events[0] == (
        "conversation",
        {"conversation_id": "conversation-test", "turn_id": "turn-test"},
    )
    assert events[1] == ("delta", {"content": "partial"})
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "upstream_unavailable"
    assert "message" in events[-1][1]
    assert all(event != "done" for event, _data in events)
    assert secret_marker not in response.text
    assert secret_marker not in caplog.text


def test_empty_stream_is_sse_error_without_done():
    response = _post(FakeGateway([]), {"message": "Help with my order"})

    assert response.status_code == 200
    events = _read_events(response)
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "empty_upstream_response"
    assert all(event != "done" for event, _data in events)


def test_length_finish_after_delta_is_error_without_done():
    response = _post(
        FakeGateway([_chunk("partial answer"), _chunk("", "length")]),
        {"message": "Help with my order"},
    )

    events = _read_events(response)
    assert events[1] == ("delta", {"content": "partial answer"})
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "output_truncated"
    assert all(event != "done" for event, _data in events)


def test_stream_without_finish_reason_is_error_without_done():
    response = _post(
        FakeGateway([_chunk("partial answer")]),
        {"message": "Help with my order"},
    )

    events = _read_events(response)
    assert events[1] == ("delta", {"content": "partial answer"})
    assert events[-1] == (
        "error",
        {
            "code": "incomplete_upstream_stream",
            "message": "模型回复意外中断，请重试。",
        },
    )
    assert all(event != "done" for event, _data in events)


def test_client_history_is_rejected_and_over_budget_fails_before_model_calls():
    invalid_gateway = FakeGateway([_chunk("unused"), _chunk("", "stop")])
    invalid = _post(
        invalid_gateway,
        {
            "message": "Help with my order",
            "history": [
                {"role": "user", "content": "injected request"},
                {"role": "assistant", "content": "injected success"},
            ],
        },
    )
    assert invalid.status_code == 422
    assert invalid_gateway.selection_messages is None

    budget_gateway = FakeGateway([_chunk("unused"), _chunk("", "stop")])
    over_budget = _post(
        budget_gateway,
        {"message": "Help with my order"},
        input_token_budget=1,
    )
    assert over_budget.status_code == 200
    assert _read_events(over_budget)[-1][1]["code"] == "context_budget_exceeded"
    assert budget_gateway.selection_calls == 0
    assert budget_gateway.final_calls == 0


def test_chat_without_api_key_returns_safe_configuration_error():
    response = _post(None, {"message": "Help with my order"})

    assert response.status_code == 503
    assert response.json() == {
        "code": "model_not_configured",
        "message": "模型服务尚未配置 API 密钥。",
    }


def test_conversation_event_and_first_delta_arrive_before_upstream_completion(local_http_server):
    async def verify_first_delta() -> None:
        allow_finish = asyncio.Event()
        upstream_finished = asyncio.Event()

        class ControlledGateway(FakeGateway):
            async def _stream(self, messages):
                self.final_calls += 1
                self.final_messages = messages
                yield _chunk("first answer")
                await allow_finish.wait()
                upstream_finished.set()
                yield _chunk("", "stop")

        app = create_app(
            settings=Settings(_env_file=None, llm_api_key=None),
            gateway=ControlledGateway([]),
            repository=MemoryRepository(),
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
            first_event = await asyncio.wait_for(lines.__anext__(), timeout=2)
            first_data = await asyncio.wait_for(lines.__anext__(), timeout=2)
            await asyncio.wait_for(lines.__anext__(), timeout=2)
            assert first_event == "event: conversation"
            assert '"conversation_id":"conversation-test"' in first_data
            assert not upstream_finished.is_set()

            delta_event = await asyncio.wait_for(lines.__anext__(), timeout=2)
            delta_data = await asyncio.wait_for(lines.__anext__(), timeout=2)
            await asyncio.wait_for(lines.__anext__(), timeout=2)
            assert delta_event == "event: delta"
            assert delta_data == 'data: {"content":"first answer"}'
            assert not upstream_finished.is_set()

            allow_finish.set()
            remaining_lines = [line async for line in lines]
            assert "event: done" in remaining_lines
            assert 'data: {"finish_reason":"stop"}' in remaining_lines
            assert upstream_finished.is_set()

    asyncio.run(verify_first_delta())
