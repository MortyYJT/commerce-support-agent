from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Self

import pytest

from commerce_support.database.models import FAQ, Conversation, Message, Ticket
from commerce_support.database.seed import seed_demo_data

RESOURCE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src/commerce_support/resources/customer_support/v1"
)


class _Transaction:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _Session:
    def __init__(self, tables: dict[type[Any], dict[object, Any]]) -> None:
        self.tables = tables

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> _Transaction:
        return _Transaction()

    async def get(self, model: type[Any], key: object) -> Any | None:
        return self.tables.setdefault(model, {}).get(key)

    def add(self, row: Any) -> None:
        if isinstance(row, (FAQ, Conversation, Message)):
            primary_key = row.id
        else:
            primary_key = row.ticket_id
        self.tables.setdefault(type(row), {})[primary_key] = row

    def add_all(self, rows: list[Any]) -> None:
        for row in rows:
            self.add(row)

    async def flush(self) -> None:
        return None

    async def scalars(self, *_: object) -> list[Any]:
        return list(self.tables.setdefault(FAQ, {}).values())

    async def scalar(self, *_: object) -> int | None:
        from commerce_support.database.seed import DEMO_CONVERSATION_ID, DEMO_TURN_ID

        for message in self.tables.setdefault(Message, {}).values():
            if (
                message.conversation_id == DEMO_CONVERSATION_ID
                and message.turn_id == DEMO_TURN_ID
            ):
                return 1
        return None


def test_seed_upgrades_known_faqs_and_preserves_custom_data() -> None:
    layout = json.loads((RESOURCE_ROOT / "faq_seed_layout.json").read_text(encoding="utf-8"))
    tables: dict[type[Any], dict[object, Any]] = {
        FAQ: {
            int(row["id"]): FAQ(**row) for row in layout["known_legacy_rows"]
        },
        Conversation: {
            "custom-conversation": Conversation(
                id="custom-conversation",
                user_id="custom-user",
                status="idle",
                active_turn_id=None,
                active_until=None,
            )
        },
        Message: {
            7001: Message(
                id=7001,
                conversation_id="custom-conversation",
                turn_id="custom-turn",
                role="user",
                content="Keep this historical message.",
                turn_status="completed",
            )
        },
        Ticket: {
            "CUSTOM-7001": Ticket(
                ticket_id="CUSTOM-7001",
                conversation_id="custom-conversation",
                turn_id="custom-turn",
                tool_call_id="custom-call",
                description="Keep this ticket.",
                ticket_type="other",
                status="open",
            )
        },
    }
    original_message = tables[Message][7001]
    original_ticket = tables[Ticket]["CUSTOM-7001"]

    asyncio.run(seed_demo_data(lambda: _Session(tables)))

    assert "7 天" in tables[FAQ][1].answer
    assert "30天" not in tables[FAQ][1].answer
    assert "10 元" in tables[FAQ][2].answer
    assert "12 元" in tables[FAQ][2].answer
    assert tables[Message][7001] is original_message
    assert tables[Ticket]["CUSTOM-7001"] is original_ticket


def test_seed_questions_and_answers_are_generated_from_canonical_sections() -> None:
    from commerce_support.resources.customer_support.loader import load_faq_seed_rows

    rows = load_faq_seed_rows()
    return_policy = next(row for row in rows if row["question"] == "退货政策是什么？")
    shipping = next((row for row in rows if row["question"] == "运费怎么算？"), None)
    postage = next((row for row in rows if row["question"] == "邮费是多少？"), None)

    assert shipping is not None
    assert postage is not None
    assert "7 天从签收之日起算" in return_policy["answer"]
    for row in (shipping, postage):
        assert "99 元包邮" in row["answer"]
        assert "10 元基础运费" in row["answer"]
        assert "12 元附加运费" in row["answer"]
    assert "邮费即运费" in postage["answer"]
    assert len({row["id"] for row in rows}) == len(rows) == 51
    assert all(len(row["question"]) <= 256 and len(row["answer"]) <= 1000 for row in rows)


def test_common_faq_keywords_fit_public_tool_result_envelope() -> None:
    import random

    from commerce_support.chat_types import TurnContext
    from commerce_support.resources.customer_support.loader import load_faq_seed_rows
    from commerce_support.tools.registry import build_registry

    rows = load_faq_seed_rows()

    class FAQStore:
        async def search_literal(self, keyword: str, limit: int = 5) -> list[dict[str, object]]:
            assert limit == 5
            return [row for row in rows if keyword in row["question"]][:limit]

    async def run() -> None:
        for index, keyword in enumerate(("退货", "运费", "智能", "猫")):
            registry = build_registry(
                repository=object(),
                faq=FAQStore(),
                tickets=object(),
                ctx=TurnContext(
                    conversation_id=f"conversation-{index}",
                    turn_id=f"turn-{index}",
                    user_message=keyword,
                ),
                rng=random.Random(index),
            )
            result = await registry["query_faq"].ainvoke(
                {
                    "name": "query_faq",
                    "args": {"keyword": keyword},
                    "id": f"call-{index}",
                    "type": "tool_call",
                }
            )
            encoded = json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            assert result.status == "success"
            assert len(result.data) <= 5
            assert len(encoded) <= 4096

    asyncio.run(run())


def test_seed_refuses_to_overwrite_an_unknown_row_at_a_managed_legacy_id() -> None:
    layout = json.loads((RESOURCE_ROOT / "faq_seed_layout.json").read_text(encoding="utf-8"))
    custom = FAQ(
        id=1,
        question="A custom row using a managed id",
        answer="Keep custom content.",
        category="custom",
    )
    tables: dict[type[Any], dict[object, Any]] = {
        FAQ: {1: custom, 2: FAQ(**layout["known_legacy_rows"][1])},
        Conversation: {},
        Message: {},
        Ticket: {},
    }

    with pytest.raises(ValueError, match="managed FAQ ID 1 is occupied by an unknown row"):
        asyncio.run(seed_demo_data(lambda: _Session(tables)))

    assert tables[FAQ][1] is custom


def test_seed_is_idempotent_after_resource_faqs_are_loaded() -> None:
    tables: dict[type[Any], dict[object, Any]] = {
        FAQ: {},
        Conversation: {},
        Message: {},
        Ticket: {},
    }

    asyncio.run(seed_demo_data(lambda: _Session(tables)))
    first_faqs = dict(tables[FAQ])
    first_messages = dict(tables[Message])
    first_tickets = dict(tables[Ticket])

    asyncio.run(seed_demo_data(lambda: _Session(tables)))

    assert tables[FAQ] == first_faqs
    assert all(tables[FAQ][row_id] is row for row_id, row in first_faqs.items())
    assert tables[Message] == first_messages
    assert tables[Ticket] == first_tickets


def test_seed_refuses_collision_in_high_resource_id_range() -> None:
    custom = FAQ(
        id=900001,
        question="A custom FAQ using a reserved resource ID",
        answer="Keep custom content.",
        category="custom",
    )
    tables: dict[type[Any], dict[object, Any]] = {
        FAQ: {900001: custom},
        Conversation: {},
        Message: {},
        Ticket: {},
    }

    with pytest.raises(ValueError, match="managed FAQ ID 900001 is occupied by an unknown row"):
        asyncio.run(seed_demo_data(lambda: _Session(tables)))

    assert tables[FAQ][900001] is custom


def test_seed_refuses_resource_question_already_owned_by_another_id() -> None:
    layout = json.loads((RESOURCE_ROOT / "faq_seed_layout.json").read_text(encoding="utf-8"))
    legacy_alias = FAQ(**layout["known_legacy_rows"][1])
    custom_question = FAQ(
        id=3,
        question="邮费是多少？",
        answer="Keep this separately managed row.",
        category="custom",
    )
    tables: dict[type[Any], dict[object, Any]] = {
        FAQ: {2: legacy_alias, 3: custom_question},
        Conversation: {},
        Message: {},
        Ticket: {},
    }

    with pytest.raises(ValueError, match="resource FAQ question '邮费是多少？' is occupied by ID 3"):
        asyncio.run(seed_demo_data(lambda: _Session(tables)))

    assert tables[FAQ][2] is legacy_alias
    assert tables[FAQ][3] is custom_question
