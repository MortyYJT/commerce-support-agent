from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration


@dataclass
class MySQLHandle:
    database_url: str = field(repr=False)
    loop: asyncio.AbstractEventLoop
    databases: list[Any] = field(default_factory=list, repr=False)


@pytest.fixture
def mysql_database() -> Iterator[MySQLHandle]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.fail("TEST_DATABASE_URL must name a disposable MySQL integration database")

    parsed_url = make_url(database_url)
    if parsed_url.drivername != "mysql+asyncmy":
        pytest.fail("TEST_DATABASE_URL must use mysql+asyncmy")
    if not parsed_url.database or not parsed_url.database.startswith("commerce_support_test_"):
        pytest.fail("TEST_DATABASE_URL must use a commerce_support_test_ database name")

    loop = asyncio.new_event_loop()
    handle = MySQLHandle(database_url=database_url, loop=loop)
    try:
        yield handle
    finally:
        for database in handle.databases:
            loop.run_until_complete(database.aclose())
        loop.close()


async def _new_database(handle: MySQLHandle) -> Any:
    from commerce_support.config import Settings
    from commerce_support.database import Database

    database = Database(Settings(_env_file=None, database_url=handle.database_url))
    handle.databases.append(database)
    return database


async def _initialize(database: Any) -> None:
    from commerce_support.database.cli import initialize_database

    await initialize_database(database)


def _settings(database_url: str):
    from commerce_support.config import Settings

    return Settings(_env_file=None, llm_api_key=None, database_url=database_url)


def _read_events(response) -> list[tuple[str, dict[str, Any]]]:
    result = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) == 2:
            result.append((lines[0].removeprefix("event: "), json.loads(lines[1][6:])))
    return result


class FakeGateway:
    def __init__(self, selection: AIMessage, answer: str = "退货政策允许30天内申请。") -> None:
        from commerce_support.model import ModelChunk

        self.selection = selection
        self.chunks = [ModelChunk(answer), ModelChunk("", "stop")]
        self.selection_calls = 0
        self.final_calls = 0
        self.final_messages = None

    async def select_tools(self, messages, tools):
        self.selection_calls += 1
        self.selection_messages = messages
        self.tools = tools
        return self.selection

    async def _stream(self, messages):
        self.final_calls += 1
        self.final_messages = messages
        for chunk in self.chunks:
            yield chunk

    def stream_final(self, messages):
        return self._stream(messages)

    async def aclose(self) -> None:
        return None


def test_real_mysql_chat_persists_a_paired_tool_round(mysql_database: MySQLHandle) -> None:
    loop = mysql_database.loop

    async def initialize() -> Any:
        database = await _new_database(mysql_database)
        await _initialize(database)
        return database

    database = loop.run_until_complete(initialize())
    gateway = FakeGateway(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_faq",
                    "args": {"keyword": "退货政策"},
                    "id": "call-faq-persist",
                    "type": "tool_call",
                }
            ],
        )
    )
    from commerce_support.app import create_app
    from commerce_support.database.repository import ChatRepository

    app = create_app(settings=_settings(mysql_database.database_url), gateway=gateway)
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
        response = client.post("/chat/stream", json={"message": "退货政策是什么？"})

    assert response.status_code == 200
    events = _read_events(response)
    names = [name for name, _data in events]
    assert names[0] == "conversation"
    assert "tool_status" in names and "delta" in names, events
    assert names.index("tool_status") < names.index("delta")
    assert names[-1] == "done"
    assert gateway.selection_calls == 1 and gateway.final_calls == 1
    assert any(isinstance(message, ToolMessage) for message in gateway.final_messages)

    conversation_id = events[0][1]["conversation_id"]
    history = loop.run_until_complete(ChatRepository(database).successful_history(conversation_id))
    assert len(history) == 1
    assert [message.type for message in history[0]] == ["human", "ai", "tool", "ai"]
    assert history[0][1].tool_calls[0]["id"] == "call-faq-persist"
    assert history[0][2].tool_call_id == "call-faq-persist"
    assert '"status":"success"' in history[0][2].content


def test_real_mysql_preserves_malformed_raw_arguments_with_matched_feedback(
    mysql_database: MySQLHandle,
) -> None:
    loop = mysql_database.loop
    raw_arguments = '{"description":"return this item",'

    async def initialize() -> Any:
        database = await _new_database(mysql_database)
        await _initialize(database)
        return database

    database = loop.run_until_complete(initialize())
    gateway = FakeGateway(
        AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "name": "create_ticket",
                    "args": raw_arguments,
                    "id": "call-invalid-ticket-json",
                    "error": "Malformed arguments",
                    "type": "invalid_tool_call",
                }
            ],
        ),
        answer="I could not create the request from those invalid arguments.",
    )
    from commerce_support.app import create_app
    from commerce_support.database.models import Message, Ticket
    from commerce_support.database.repository import ChatRepository

    async def count_tickets() -> int:
        async with database.sessions() as session:
            return len(list(await session.scalars(select(Ticket.ticket_id))))

    initial_ticket_count = loop.run_until_complete(count_tickets())
    app = create_app(settings=_settings(mysql_database.database_url), gateway=gateway)
    with TestClient(app) as client:
        response = client.post("/chat/stream", json={"message": "Please create a return ticket."})

    assert response.status_code == 200
    events = _read_events(response)
    assert events[-1][0] == "done", events
    assert gateway.selection_calls == 1 and gateway.final_calls == 1
    assert any(isinstance(message, ToolMessage) for message in gateway.final_messages)
    assert loop.run_until_complete(count_tickets()) == initial_ticket_count

    conversation_id = events[0][1]["conversation_id"]
    history = loop.run_until_complete(ChatRepository(database).successful_history(conversation_id))
    invalid_call = history[0][1].invalid_tool_calls[0]
    assert invalid_call["args"] == raw_arguments
    assert history[0][2].tool_call_id == "call-invalid-ticket-json"

    async def stored_raw_call() -> Any:
        async with database.sessions() as session:
            return await session.scalar(
                select(Message.tool_calls).where(
                    Message.conversation_id == conversation_id,
                    Message.role == "assistant",
                )
            )

    calls = loop.run_until_complete(stored_raw_call())
    assert calls[0]["args"] == raw_arguments
    assert calls[0]["type"] == "invalid_tool_call"


def test_real_mysql_route_returns_not_found_and_active_conflict(
    mysql_database: MySQLHandle,
) -> None:
    loop = mysql_database.loop

    async def setup() -> tuple[Any, Any]:
        database = await _new_database(mysql_database)
        await _initialize(database)
        from commerce_support.database.repository import ChatRepository

        active_turn = await ChatRepository(database).begin_turn("hold this conversation")
        return database, active_turn

    database, active_turn = loop.run_until_complete(setup())
    del database
    gateway = FakeGateway(AIMessage(content=""))
    from commerce_support.app import create_app

    app = create_app(settings=_settings(mysql_database.database_url), gateway=gateway)
    with TestClient(app) as client:
        missing = client.post(
            "/chat/stream",
            json={"message": "follow up", "conversation_id": str(uuid4())},
        )
        conflict = client.post(
            "/chat/stream",
            json={"message": "competing turn", "conversation_id": active_turn.conversation_id},
        )

    assert missing.status_code == 404
    assert conflict.status_code == 409


def test_real_mysql_final_commit_failure_stores_only_failed_partial_turn(
    mysql_database: MySQLHandle,
) -> None:
    loop = mysql_database.loop

    async def run() -> None:
        from commerce_support.chat_types import TurnContext
        from commerce_support.model import ModelChunk
        from commerce_support.schemas import ChatRequest

        database = await _new_database(mysql_database)
        await _initialize(database)
        from commerce_support.config import Settings
        from commerce_support.database.repository import ChatRepository
        from commerce_support.services import ChatService
        from commerce_support.tools.executor import ToolExecutor

        delegate = ChatRepository(database)

        class FailCompletedCommitRepository:
            async def begin_turn(self, message: str, conversation_id: str | None = None):
                return await delegate.begin_turn(message, conversation_id)

            async def successful_history(self, conversation_id: str):
                return await delegate.successful_history(conversation_id)

            async def append_assistant_call(self, ctx: TurnContext, message: AIMessage) -> None:
                await delegate.append_assistant_call(ctx, message)

            async def append_tool_result(self, ctx: TurnContext, call_id: str, result: Any) -> None:
                await delegate.append_tool_result(ctx, call_id, result)

            async def finish_turn(self, ctx: TurnContext, text: str, status: str) -> None:
                if status == "completed":
                    raise RuntimeError("simulated final commit failure")
                await delegate.finish_turn(ctx, text, status)

        gateway = FakeGateway(AIMessage(content=""), answer="partial final answer")
        settings = Settings(_env_file=None, input_token_budget=4096)
        service = ChatService(
            gateway,
            settings,
            FailCompletedCommitRepository(),
            ToolExecutor(settings),
            registry_factory=lambda _ctx: {},
        )
        context = await service.prepare(
            ChatRequest(message="Save this answer only if the database commit succeeds")
        )
        gateway.chunks = [ModelChunk("partial final answer"), ModelChunk("", "stop")]
        events = [event async for event in service.stream(context)]

        assert [event.event for event in events][-1] == "error"
        assert all(event.event != "done" for event in events)
        assert await delegate.successful_history(context.conversation_id) == []

        from commerce_support.database.models import Message

        async with database.sessions() as session:
            messages = list(
                await session.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == context.conversation_id,
                        Message.turn_id == context.turn_id,
                    )
                    .order_by(Message.id)
                )
            )
        assert [message.turn_status for message in messages] == ["failed", "failed"]
        assert messages[-1].content == "partial final answer"

    loop.run_until_complete(run())
