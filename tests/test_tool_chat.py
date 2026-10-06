from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from commerce_support import context as chat_context
from commerce_support.chat_types import StreamEvent, TurnContext
from commerce_support.config import Settings
from commerce_support.routes import _encode_public_event
from commerce_support.schemas import ChatRequest
from commerce_support.services import ChatService
from commerce_support.tools.schemas import FAQQueryArgs, ToolResult


class FakeTool:
    name = "query_faq"
    description = "Search a FAQ by exact keyword."
    args_schema = FAQQueryArgs


class FakeRepository:
    def __init__(self, *, fail_completed: bool = False) -> None:
        self.trace: list[str] = []
        self.history: list[list[Any]] = []
        self.tool_results: list[tuple[str, dict[str, Any] | list[Any]]] = []
        self.finished: list[tuple[str, str]] = []
        self.fail_completed = fail_completed

    async def begin_turn(self, message: str, conversation_id: str | None = None) -> TurnContext:
        return TurnContext(conversation_id or "conversation-1", "turn-1", message)

    async def successful_history(self, conversation_id: str) -> list[list[Any]]:
        assert conversation_id == "conversation-1"
        return self.history

    async def append_assistant_call(self, _ctx: TurnContext, message: AIMessage) -> None:
        self.trace.append("append_call")
        self.assistant_call = message

    async def append_tool_result(
        self,
        _ctx: TurnContext,
        call_id: str,
        result: dict[str, Any] | list[Any],
    ) -> None:
        self.trace.append("append_tool_result")
        self.tool_results.append((call_id, result))

    async def finish_turn(self, _ctx: TurnContext, text: str, status: str) -> None:
        self.trace.append(f"commit_{status}")
        if status == "completed" and self.fail_completed:
            raise RuntimeError("database secret")
        self.finished.append((text, status))


class FakeGateway:
    def __init__(self, selection: AIMessage, chunks: list[object]) -> None:
        self.selection = selection
        self.chunks = chunks
        self.selection_calls = 0
        self.final_calls = 0
        self.final_messages: list[Any] | None = None

    async def select_tools(self, messages, tools):
        self.selection_calls += 1
        self.selection_messages = messages
        self.tools = tools
        return self.selection

    async def _stream(self, messages) -> AsyncIterator[object]:
        self.final_calls += 1
        self.final_messages = messages
        for chunk in self.chunks:
            yield chunk

    def stream_final(self, messages):
        return self._stream(messages)


class FakeExecutor:
    def __init__(self, result: ToolResult | None = None) -> None:
        self.result = result or ToolResult.success({"answer": "found"})
        self.calls: list[dict[str, Any]] = []

    async def execute(self, call, _registry, _ctx):
        self.calls.append(call)
        yield StreamEvent(
            "tool_status",
            {
                "name": call.get("name"),
                "tool_call_id": call.get("id"),
                "status": "running",
                "attempt": 1,
            },
        )
        yield StreamEvent(
            "tool_status",
            {
                "name": call.get("name"),
                "tool_call_id": call.get("id"),
                "status": "succeeded",
                "attempt": 1,
            },
        )
        yield StreamEvent(
            "tool_result",
            {
                "name": call.get("name"),
                "tool_call_id": call.get("id"),
                "attempt": 1,
                "result": self.result,
            },
        )


def _call(call_id: str, keyword: str = "returns") -> dict[str, Any]:
    return {
        "name": "query_faq",
        "args": {"keyword": keyword},
        "id": call_id,
        "type": "tool_call",
    }


def _chunk(content: str, finish_reason: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, finish_reason=finish_reason)


def _service(
    selection: AIMessage,
    chunks: list[object],
    *,
    repository: FakeRepository | None = None,
    executor: FakeExecutor | None = None,
    budget: int = 4096,
):
    gateway = FakeGateway(selection, chunks)
    repo = repository or FakeRepository()
    tool_executor = executor or FakeExecutor()
    service = ChatService(
        gateway,
        Settings(_env_file=None, llm_api_key=None, input_token_budget=budget),
        repo,
        tool_executor,
        registry_factory=lambda _ctx: {"query_faq": FakeTool()},
    )
    return service, gateway, repo, tool_executor


def _run(coroutine):
    return asyncio.run(coroutine)


def test_one_tool_then_final_streams_only_public_events():
    async def run() -> None:
        service, gateway, repository, executor = _service(
            AIMessage(content="", tool_calls=[_call("faq-1")]),
            [_chunk("Returns are accepted."), _chunk("", "stop")],
        )
        context = await service.prepare(ChatRequest(message="What is the returns policy?"))

        events = [event async for event in service.stream(context)]

        assert gateway.selection_calls == 1
        assert gateway.final_calls == 1
        assert [call["id"] for call in executor.calls] == ["faq-1"]
        assert [event.event for event in events] == [
            "conversation",
            "tool_status",
            "tool_status",
            "delta",
            "done",
        ]
        assert events.index(next(e for e in events if e.event == "tool_status")) < events.index(
            next(e for e in events if e.event == "delta")
        )
        assert all(event.event != "tool_result" for event in events)
        assert any(isinstance(message, ToolMessage) for message in gateway.final_messages or [])
        assert repository.finished == [("Returns are accepted.", "completed")]

    _run(run())


def test_default_budget_allows_one_tool_round_with_the_real_registry():
    async def run() -> None:
        import random

        from commerce_support.prompts import render_system_prompt
        from commerce_support.tools.registry import build_registry

        gateway = FakeGateway(
            AIMessage(content="", tool_calls=[_call("faq-default")]),
            [_chunk("Returns are accepted."), _chunk("", "stop")],
        )
        repository = FakeRepository()
        executor = FakeExecutor()
        settings = Settings(_env_file=None, llm_api_key=None)
        service = ChatService(
            gateway,
            settings,
            repository,
            executor,
            registry_factory=lambda ctx: build_registry(
                None,
                None,
                None,
                ctx,
                random.Random(1),
            ),
        )
        context = await service.prepare(ChatRequest(message="What is the returns policy?"))

        events = [event async for event in service.stream(context)]

        assert settings.input_token_budget == 4096
        assert gateway.selection_messages[0].content == render_system_prompt()
        assert {tool.name for tool in gateway.tools} == {
            "query_order",
            "query_product",
            "query_logistics",
            "query_faq",
            "create_ticket",
        }
        assert gateway.selection_calls == 1 and gateway.final_calls == 1
        assert [call["id"] for call in executor.calls] == ["faq-default"]
        assert any(
            isinstance(message, AIMessage) and message.tool_calls
            for message in gateway.final_messages or []
        )
        assert any(isinstance(message, ToolMessage) for message in gateway.final_messages or [])
        assert [event.event for event in events][-1] == "done"
        assert repository.finished == [("Returns are accepted.", "completed")]

    _run(run())


def test_no_tool_still_streams_final():
    async def run() -> None:
        service, gateway, repository, executor = _service(
            AIMessage(content="selection draft must not be shown"),
            [_chunk("Hello."), _chunk("", "stop")],
        )
        context = await service.prepare(ChatRequest(message="Hello"))

        events = [event async for event in service.stream(context)]

        assert gateway.selection_calls == 1 and gateway.final_calls == 1
        assert executor.calls == []
        assert [event.data.get("content") for event in events if event.event == "delta"] == ["Hello."]
        assert all("selection draft" not in str(event.data) for event in events)
        assert repository.finished == [("Hello.", "completed")]

    _run(run())


def test_multiple_calls_execute_zero_business_tools_and_receive_paired_errors():
    async def run() -> None:
        service, gateway, repository, executor = _service(
            AIMessage(content="", tool_calls=[_call("faq-1"), _call("faq-2", "refund")]),
            [_chunk("I could not run both requests."), _chunk("", "stop")],
        )
        context = await service.prepare(ChatRequest(message="Tell me returns and refund rules"))

        events = [event async for event in service.stream(context)]

        assert executor.calls == []
        assert len(repository.tool_results) == 2
        assert {call_id for call_id, _result in repository.tool_results} == {"faq-1", "faq-2"}
        assert sum(isinstance(message, ToolMessage) for message in gateway.final_messages or []) == 2
        assert gateway.selection_calls == 1 and gateway.final_calls == 1
        assert [event.event for event in events].count("tool_status") == 2

    _run(run())


def test_missing_call_id_fails_safely_without_final_request():
    async def run() -> None:
        selection = AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "name": "query_faq",
                    "args": '{"keyword":"returns"',
                    "error": "Malformed arguments",
                    "type": "invalid_tool_call",
                }
            ],
        )
        service, gateway, repository, executor = _service(
            selection,
            [_chunk("unused"), _chunk("", "stop")],
        )
        context = await service.prepare(ChatRequest(message="What is the returns policy?"))

        events = [event async for event in service.stream(context)]

        assert gateway.final_calls == 0
        assert executor.calls == []
        assert events[-1].event == "error"
        assert events[-1].data["code"] == "invalid_tool_call"
        assert repository.finished[-1][1] == "failed"

    _run(run())


def test_done_is_emitted_only_after_final_commit():
    async def run() -> None:
        service, gateway, repository, _executor = _service(
            AIMessage(content=""),
            [_chunk("Stored."), _chunk("", "stop")],
        )
        context = await service.prepare(ChatRequest(message="Hello"))

        events = [event async for event in service.stream(context)]

        assert repository.trace.index("commit_completed") < events.index(
            next(event for event in events if event.event == "done")
        )
        assert gateway.final_calls == 1

    _run(run())


def test_final_commit_failure_never_emits_done_or_raw_database_error():
    async def run() -> None:
        repository = FakeRepository(fail_completed=True)
        service, gateway, _repository, _executor = _service(
            AIMessage(content=""),
            [_chunk("Partial."), _chunk("", "stop")],
            repository=repository,
        )
        context = await service.prepare(ChatRequest(message="Hello"))

        events = [event async for event in service.stream(context)]

        assert gateway.final_calls == 1
        assert all(event.event != "done" for event in events)
        assert events[-1].event == "error"
        assert "database secret" not in str(events)

    _run(run())


def test_overbudget_tool_result_does_not_start_final_generation():
    async def run() -> None:
        service, gateway, repository, _executor = _service(
            AIMessage(content="", tool_calls=[_call("faq-1")]),
            [_chunk("unused"), _chunk("", "stop")],
            executor=FakeExecutor(ToolResult.success({"answer": "答" * 1800})),
            budget=4096,
        )
        context = await service.prepare(ChatRequest(message="What is the returns policy?"))

        events = [event async for event in service.stream(context)]

        assert len(repository.tool_results) == 1
        assert gateway.final_calls == 0
        assert events[-1].event == "error"
        assert events[-1].data["code"] == "context_budget_exceeded"

    _run(run())


def test_history_groups_trim_together():
    old_round = [
        HumanMessage(content="old question " * 120),
        AIMessage(
            content="",
            tool_calls=[_call("old-tool", "old-keyword")],
        ),
        ToolMessage(
            content='{"status":"success","data":["old result"]}',
            tool_call_id="old-tool",
            name="query_faq",
        ),
        AIMessage(content="old final answer"),
    ]
    recent_round = [
        HumanMessage(content="recent question"),
        AIMessage(content="recent answer"),
    ]
    current_round = [HumanMessage(content="current question")]
    schemas = [
        {
            "name": "query_faq",
            "description": "Search a FAQ.",
            "parameters": {"type": "object", "properties": {"keyword": {"type": "string"}}},
        }
    ]

    messages = chat_context.build_persisted_messages(
        system="policy",
        history_groups=[old_round, recent_round],
        current_group=current_round,
        tool_schemas=schemas,
        budget=1200,
    )

    assert messages[0].content == "policy"
    assert [message.content for message in messages if isinstance(message, HumanMessage)] == [
        "recent question",
        "current question",
    ]
    assert any(message.content == "recent answer" for message in messages)
    assert not any("old" in str(message.content) for message in messages)
    assert not any(isinstance(message, ToolMessage) for message in messages)


def test_sse_encoder_exposes_only_whitelisted_fields_and_events():
    encoded = _encode_public_event(
        StreamEvent(
            "tool_status",
            {
                "name": "query_faq",
                "tool_call_id": "faq-1",
                "status": "succeeded",
                "attempt": 1,
                "result": {"data": ["private raw result"]},
            },
        )
    )

    assert encoded == (
        'event: tool_status\ndata: '
        '{"name":"query_faq","tool_call_id":"faq-1","status":"succeeded","attempt":1}\n\n'
    )
    assert _encode_public_event(StreamEvent("tool_result", {"result": "private"})) is None
