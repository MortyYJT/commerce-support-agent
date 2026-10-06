import asyncio
import os
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
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
    from commerce_support.database.engine import Database

    database = Database(Settings(_env_file=None, database_url=handle.database_url))
    handle.databases.append(database)
    return database


async def _initialize(database: Any) -> None:
    from commerce_support.database.cli import initialize_database

    await initialize_database(database)


def test_ticket_commit_then_timeout_reuses_number(mysql_database: MySQLHandle) -> None:
    loop = mysql_database.loop

    async def run() -> None:
        from langchain_core.messages import AIMessage

        from commerce_support.chat_types import TurnContext
        from commerce_support.config import Settings
        from commerce_support.database.models import Ticket
        from commerce_support.database.repository import ChatRepository, TicketRepository
        from commerce_support.tools.executor import ToolExecutor
        from commerce_support.tools.registry import build_registry

        database = await _new_database(mysql_database)
        await _initialize(database)
        chat_repository = ChatRepository(database)
        async with database.sessions() as session:
            initial_ticket_count = await session.scalar(select(func.count()).select_from(Ticket))
        context = await chat_repository.begin_turn("请帮我退货，商品收到时已经损坏。")
        call_id = f"ticket-{uuid4()}"
        description = "The item arrived damaged."
        call_args = {"description": description, "ticket_type": "return"}
        await chat_repository.append_assistant_call(
            context,
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_ticket",
                        "args": call_args,
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
            ),
        )

        class CommitThenTimeoutTicketRepository:
            def __init__(self) -> None:
                self.delegate = TicketRepository(database)
                self.calls = 0
                self.committed_ticket_id: str | None = None

            async def create_once(
                self,
                ctx: TurnContext,
                call_id: str,
                description: str,
                ticket_type: str,
            ) -> dict[str, str]:
                self.calls += 1
                result = await self.delegate.create_once(
                    ctx, call_id, description, ticket_type
                )
                if self.calls == 1:
                    self.committed_ticket_id = result["ticket_id"]
                    raise TimeoutError("simulated lost response after commit")
                return result

        ticket_repository = CommitThenTimeoutTicketRepository()
        registry = build_registry(
            repository=chat_repository,
            faq=object(),
            tickets=ticket_repository,
            ctx=context,
            rng=random.Random(11),
        )
        settings = Settings(
            _env_file=None,
            llm_api_key=None,
            tool_timeout_seconds=0.5,
            tool_max_retries=1,
        )
        executor = ToolExecutor(settings)
        events = [
            event
            async for event in executor.execute(
                {
                    "name": "create_ticket",
                    "args": call_args,
                    "id": call_id,
                    "type": "tool_call",
                },
                registry,
                context,
            )
        ]

        result = events[-1].data["result"]
        async with database.sessions() as session:
            ticket_count = await session.scalar(select(func.count()).select_from(Ticket))

        assert ticket_repository.calls == 2
        assert ticket_count - initial_ticket_count == 1
        assert ticket_repository.committed_ticket_id is not None
        assert result.data["ticket_id"] == ticket_repository.committed_ticket_id

    loop.run_until_complete(run())


def test_faq_like_wildcards_are_literal_and_postage_stays_not_found(
    mysql_database: MySQLHandle,
) -> None:
    loop = mysql_database.loop

    async def run() -> None:

        from commerce_support.chat_types import TurnContext
        from commerce_support.database.models import FAQ
        from commerce_support.database.repository import FAQRepository
        from commerce_support.tools.registry import build_registry

        database = await _new_database(mysql_database)
        await _initialize(database)
        marker = uuid4().hex
        literal_answer = f"literal row {marker}"
        async with database.sessions.begin() as session:
            session.add_all(
                [
                    FAQ(
                        question=f"{marker}%_ lookup",
                        answer=literal_answer,
                        category="integration",
                    ),
                    FAQ(
                        question=f"{marker}XY lookup",
                        answer="wildcard decoy",
                        category="integration",
                    ),
                ]
            )

        faq_repository = FAQRepository(database)
        literal_matches = await faq_repository.search_literal(f"{marker}%_")
        assert [row["answer"] for row in literal_matches] == [literal_answer]

        ctx = TurnContext(
            conversation_id=str(uuid4()),
            turn_id=str(uuid4()),
            user_message="邮费是多少？",
        )
        registry = build_registry(
            repository=object(),
            faq=faq_repository,
            tickets=object(),
            ctx=ctx,
            rng=random.Random(11),
        )
        postage_result = await registry["query_faq"].ainvoke(
            {
                "name": "query_faq",
                "args": {"keyword": "邮费"},
                "id": "faq-postage-call",
                "type": "tool_call",
            }
        )
        assert postage_result.status == "not_found"
        assert postage_result.data == []

    loop.run_until_complete(run())
