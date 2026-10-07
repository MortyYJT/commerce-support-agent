import asyncio
import random
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from commerce_support.chat_types import TurnContext


@dataclass
class StubFAQRepository:
    rows: list[dict[str, Any]] = field(default_factory=list)
    searches: list[tuple[str, int]] = field(default_factory=list)

    async def search_literal(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        self.searches.append((keyword, limit))
        return [row for row in self.rows if keyword in row["question"]][:limit]


@dataclass
class StubTicketRepository:
    calls: list[tuple[TurnContext, str, str, str]] = field(default_factory=list)

    async def create_once(
        self,
        ctx: TurnContext,
        call_id: str,
        description: str,
        ticket_type: str,
    ) -> dict[str, str]:
        self.calls.append((ctx, call_id, description, ticket_type))
        return {
            "ticket_id": "TK-TEST-0001",
            "description": description,
            "ticket_type": ticket_type,
            "status": "open",
        }


@dataclass
class StubChatRepository:
    pass


def _context(message: str = "请查一下订单 1001 的物流") -> TurnContext:
    return TurnContext(
        conversation_id="conversation-1",
        turn_id="turn-1",
        user_message=message,
    )


def _build_registry(
    *,
    ctx: TurnContext | None = None,
    faq: StubFAQRepository | None = None,
    tickets: StubTicketRepository | None = None,
) -> tuple[dict[str, Any], StubFAQRepository, StubTicketRepository]:
    from commerce_support.tools.registry import build_registry

    faq_repository = faq or StubFAQRepository()
    ticket_repository = tickets or StubTicketRepository()
    registry = build_registry(
        repository=StubChatRepository(),
        faq=faq_repository,
        tickets=ticket_repository,
        ctx=ctx or _context(),
        rng=random.Random(7),
    )
    return registry, faq_repository, ticket_repository


def _tool_call(name: str, args: dict[str, Any], call_id: str = "call-1") -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_five_registered_decorated_tools() -> None:
    from langchain_core.tools import BaseTool

    registry, _, _ = _build_registry()

    assert set(registry) == {
        "query_order",
        "query_product",
        "query_logistics",
        "query_faq",
        "create_ticket",
    }
    assert all(isinstance(tool, BaseTool) for tool in registry.values())
    assert all(tool.name == name for name, tool in registry.items())

    public_ticket_schema = registry["create_ticket"].tool_call_schema.model_json_schema()
    assert set(public_ticket_schema["properties"]) == {"description", "ticket_type"}
    assert set(public_ticket_schema["required"]) == {"description", "ticket_type"}


def test_extra_args_rejected_before_ticket_repository_write() -> None:
    async def run() -> None:
        registry, _, tickets = _build_registry()
        ticket_tool = registry["create_ticket"]

        with pytest.raises(ValidationError):
            await ticket_tool.ainvoke(
                _tool_call(
                    "create_ticket",
                    {
                        "description": "Please arrange a return.",
                        "ticket_type": "return",
                        "conversation_id": "forged-conversation",
                        "tool_call_id": "forged-call",
                    },
                )
            )

        assert tickets.calls == []

    asyncio.run(run())


def test_faq_keyword_must_be_original_substring() -> None:
    async def run() -> None:
        registry, faq, _ = _build_registry(ctx=_context("邮费是多少？"))

        result = await registry["query_faq"].ainvoke(
            _tool_call("query_faq", {"keyword": "运费"})
        )

        assert result.status == "error"
        assert result.code == "KEYWORD_NOT_IN_MESSAGE"
        assert faq.searches == []

    asyncio.run(run())


def test_faq_keeps_like_wildcards_literal() -> None:
    async def run() -> None:
        rows = [
            {"question": "商品%_编码说明", "answer": "literal match", "category": "test"},
            {"question": "商品ABC编码说明", "answer": "wildcard match", "category": "test"},
        ]
        faq = StubFAQRepository(rows=rows)
        registry, _, _ = _build_registry(ctx=_context("查询商品 %_ 编码"), faq=faq)

        result = await registry["query_faq"].ainvoke(
            _tool_call("query_faq", {"keyword": "%_"})
        )

        assert result.status == "success"
        assert result.data == [rows[0]]
        assert faq.searches == [("%_", 5)]

    asyncio.run(run())


def test_ticket_uses_server_context_and_runtime_call_id() -> None:
    async def run() -> None:
        ctx = TurnContext(
            conversation_id="server-conversation",
            turn_id="server-turn",
            user_message="Please return my purchase.",
        )
        registry, _, tickets = _build_registry(ctx=ctx)

        result = await registry["create_ticket"].ainvoke(
            _tool_call(
                "create_ticket",
                {"description": "The item arrived damaged.", "ticket_type": "return"},
                call_id="server-call-id",
            )
        )

        assert result.status == "success"
        assert tickets.calls == [
            (ctx, "server-call-id", "The item arrived damaged.", "return")
        ]

    asyncio.run(run())


def test_demo_tools_echo_input_and_return_seeded_generated_fields() -> None:
    async def run() -> None:
        registry, _, _ = _build_registry()

        order = await registry["query_order"].ainvoke(
            _tool_call("query_order", {"order_id": "ORDER-1001"})
        )
        product = await registry["query_product"].ainvoke(
            _tool_call("query_product", {"product_id": "SKU-RED-1"})
        )
        logistics = await registry["query_logistics"].ainvoke(
            _tool_call("query_logistics", {"order_id": "ORDER-1001"})
        )

        assert order.data == {
            "demo": True,
            "order_id": "ORDER-1001",
            "status": "shipped",
            "total_aud": 332.53,
        }
        assert product.data == {
            "demo": True,
            "product_id": "SKU-RED-1",
            "name": "Travel Mug",
            "in_stock": True,
            "price_aud": 22.84,
        }
        assert logistics.data == {
            "demo": True,
            "order_id": "ORDER-1001",
            "status": "out_for_delivery",
            "estimated_days": 0,
            "carrier": "Demo Express",
        }

    asyncio.run(run())
