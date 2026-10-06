import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select, update
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


async def _table_counts(database: Any) -> dict[str, int]:
    from commerce_support.database.models import FAQ, Conversation, Message, Ticket

    async with database.sessions() as session:
        models = {
            "faq": FAQ,
            "conversations": Conversation,
            "messages": Message,
            "tickets": Ticket,
        }
        counts = {}
        for table_name, model in models.items():
            counts[table_name] = await session.scalar(select(func.count()).select_from(model))
        return counts


def test_exactly_four_tables_and_foreign_keys(
    mysql_database: MySQLHandle,
) -> None:
    loop = mysql_database.loop

    async def run() -> None:
        database = await _new_database(mysql_database)
        await _initialize(database)

        async with database.engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            foreign_keys = await connection.run_sync(
                lambda sync_connection: {
                    table_name: inspect(sync_connection).get_foreign_keys(table_name)
                    for table_name in ("conversations", "messages", "tickets")
                }
            )

        assert table_names == {"faq", "conversations", "messages", "tickets"}
        assert foreign_keys["messages"][0]["referred_table"] == "conversations"
        assert foreign_keys["tickets"][0]["referred_table"] == "conversations"

    loop.run_until_complete(run())


def test_seed_is_idempotent(mysql_database: MySQLHandle) -> None:
    loop = mysql_database.loop

    async def run() -> None:
        database = await _new_database(mysql_database)
        await _initialize(database)
        counts_after_first_seed = await _table_counts(database)

        await _initialize(database)
        counts_after_second_seed = await _table_counts(database)

        assert counts_after_second_seed == counts_after_first_seed

    loop.run_until_complete(run())


def test_begin_turn_conflicts_for_an_active_conversation(
    mysql_database: MySQLHandle,
) -> None:
    loop = mysql_database.loop

    async def run() -> None:
        from commerce_support.chat_types import TurnContext
        from commerce_support.database.models import Conversation
        from commerce_support.database.repository import ChatRepository, TurnConflictError

        database = await _new_database(mysql_database)
        await _initialize(database)
        repository = ChatRepository(database)

        conversation_id = str(uuid4())
        async with database.sessions.begin() as session:
            session.add(
                Conversation(
                    id=conversation_id,
                    user_id="integration-test",
                    status="idle",
                    active_turn_id=None,
                    active_until=None,
                )
            )

        results = await asyncio.gather(
            repository.begin_turn("first request", conversation_id),
            repository.begin_turn("competing request", conversation_id),
            return_exceptions=True,
        )
        turns = [result for result in results if isinstance(result, TurnContext)]
        conflicts = [result for result in results if isinstance(result, TurnConflictError)]

        assert len(turns) == 1
        assert len(conflicts) == 1

        async with database.sessions() as session:
            conversation = await session.get(Conversation, conversation_id)
            assert conversation is not None
            assert conversation.active_turn_id == turns[0].turn_id

    loop.run_until_complete(run())


def test_unknown_conversation_id_is_rejected(
    mysql_database: MySQLHandle,
) -> None:
    loop = mysql_database.loop

    async def run() -> None:
        from commerce_support.database.exceptions import ConversationNotFoundError
        from commerce_support.database.models import Conversation
        from commerce_support.database.repository import ChatRepository

        database = await _new_database(mysql_database)
        await _initialize(database)
        repository = ChatRepository(database)
        unknown_id = "d16db790-8163-4cf8-aabb-2fcb5eae6a54"

        with pytest.raises(ConversationNotFoundError):
            await repository.begin_turn("request for unknown id", unknown_id)

        async with database.sessions() as session:
            assert await session.get(Conversation, unknown_id) is None

    loop.run_until_complete(run())


def test_expired_turn_cannot_finish_a_new_turn(mysql_database: MySQLHandle) -> None:
    loop = mysql_database.loop

    async def run() -> None:
        from commerce_support.database.exceptions import TurnOwnershipError
        from commerce_support.database.models import Conversation, Message
        from commerce_support.database.repository import ChatRepository

        database = await _new_database(mysql_database)
        await _initialize(database)
        repository = ChatRepository(database)
        stale_turn = await repository.begin_turn("first request")

        async with database.sessions.begin() as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == stale_turn.conversation_id)
                .values(active_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))
            )

        current_turn = await repository.begin_turn("second request", stale_turn.conversation_id)
        with pytest.raises(TurnOwnershipError):
            await repository.finish_turn(stale_turn, "stale response", "completed")

        async with database.sessions() as session:
            conversation = await session.get(Conversation, stale_turn.conversation_id)
            stale_messages = list(
                await session.scalars(
                    select(Message).where(
                        Message.conversation_id == stale_turn.conversation_id,
                        Message.turn_id == stale_turn.turn_id,
                    )
                )
            )

        assert conversation is not None
        assert conversation.active_turn_id == current_turn.turn_id
        assert stale_messages
        assert all(message.turn_status == "failed" for message in stale_messages)
        assert not any(
            message.role == "assistant" and message.content == "stale response"
            for message in stale_messages
        )

    loop.run_until_complete(run())


def test_only_completed_turns_enter_stable_paired_history(
    mysql_database: MySQLHandle,
) -> None:
    loop = mysql_database.loop

    async def run() -> None:
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        from commerce_support.database.repository import ChatRepository

        database = await _new_database(mysql_database)
        await _initialize(database)
        repository = ChatRepository(database)

        completed = await repository.begin_turn("find the return policy")
        await repository.append_assistant_call(
            completed,
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_faq",
                        "args": {"keyword": "return policy"},
                        "id": "call-faq-1",
                        "type": "tool_call",
                    }
                ],
            ),
        )
        await repository.append_tool_result(
            completed,
            "call-faq-1",
            {"status": "success", "data": [{"question": "Return policy", "answer": "30 days"}]},
        )
        await repository.finish_turn(completed, "Returns are accepted within 30 days.", "completed")

        cancelled = await repository.begin_turn("second request", completed.conversation_id)
        await repository.finish_turn(cancelled, "partial response", "cancelled")

        history = await repository.successful_history(completed.conversation_id)

        assert len(history) == 1
        assert [type(message) for message in history[0]] == [
            HumanMessage,
            AIMessage,
            ToolMessage,
            AIMessage,
        ]
        assert history[0][0].content == "find the return policy"
        assert history[0][1].tool_calls[0]["id"] == "call-faq-1"
        assert history[0][2].tool_call_id == "call-faq-1"
        assert history[0][2].content == (
            '{"status":"success","data":[{"question":"Return policy","answer":"30 days"}]}'
        )
        assert history[0][3].content == "Returns are accepted within 30 days."

    loop.run_until_complete(run())


def test_ticket_creation_is_idempotent_for_a_tool_call(
    mysql_database: MySQLHandle,
) -> None:
    loop = mysql_database.loop

    async def run() -> None:
        from langchain_core.messages import AIMessage

        from commerce_support.database.models import Ticket
        from commerce_support.database.repository import ChatRepository, TicketRepository

        database = await _new_database(mysql_database)
        await _initialize(database)
        chat_repository = ChatRepository(database)
        ticket_repository = TicketRepository(database)
        context = await chat_repository.begin_turn("I need to return an item")
        await chat_repository.append_assistant_call(
            context,
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_ticket",
                        "args": {
                            "description": "The item arrived damaged.",
                            "ticket_type": "return",
                        },
                        "id": "call-ticket-1",
                        "type": "tool_call",
                    }
                ],
            ),
        )

        first = await ticket_repository.create_once(
            context, "call-ticket-1", "The item arrived damaged.", "return"
        )
        second = await ticket_repository.create_once(
            context, "call-ticket-1", "The item arrived damaged.", "return"
        )

        async with database.sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Ticket)
                .where(Ticket.ticket_id == first["ticket_id"])
            )

        assert first == second
        assert count == 1

    loop.run_until_complete(run())


def test_faq_search_treats_like_wildcards_as_literal_characters(
    mysql_database: MySQLHandle,
) -> None:
    loop = mysql_database.loop

    async def run() -> None:
        from commerce_support.database.models import FAQ
        from commerce_support.database.repository import FAQRepository

        database = await _new_database(mysql_database)
        await _initialize(database)
        async with database.sessions.begin() as session:
            if await session.get(FAQ, 9001) is None:
                session.add_all(
                    [
                        FAQ(
                            id=9001,
                            question="Promo 100% guarantee",
                            answer="Literal percent match",
                            category="shipping",
                        ),
                        FAQ(
                            id=9002,
                            question="Promo 100X guarantee",
                            answer="Wildcard false match",
                            category="shipping",
                        ),
                    ]
                )

        results = await FAQRepository(database).search_literal("100%", limit=5)

        assert [result["question"] for result in results] == ["Promo 100% guarantee"]

    loop.run_until_complete(run())


def test_database_init_cli_is_idempotent_and_does_not_echo_the_url(
    mysql_database: MySQLHandle,
) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = mysql_database.database_url

    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-m", "commerce_support.database.cli", "init"],
            cwd=os.getcwd(),
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "mysql+asyncmy://" not in output
        assert "commerce_support_test_task1" not in output
