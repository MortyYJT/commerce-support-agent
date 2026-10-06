import random
from typing import Annotated, Any

from langchain_core.tools import InjectedToolCallId, tool

from commerce_support.chat_types import TurnContext
from commerce_support.database.repository import ChatRepository, FAQRepository, TicketRepository
from commerce_support.tools.schemas import (
    CreateTicketArgs,
    FAQQueryArgs,
    LogisticsQueryArgs,
    OrderQueryArgs,
    ProductQueryArgs,
    ToolResult,
)

TOOL_NAMES = frozenset(
    {"query_order", "query_product", "query_logistics", "query_faq", "create_ticket"}
)


def build_registry(
    repository: ChatRepository,
    faq: FAQRepository,
    tickets: TicketRepository,
    ctx: TurnContext,
    rng: random.Random,
) -> dict[str, Any]:
    @tool(
        "query_order",
        args_schema=OrderQueryArgs,
        description="Return a randomly generated order example. The result is demo data.",
    )
    async def query_order(order_id: str) -> ToolResult:
        return ToolResult.success(
            {
                "demo": True,
                "order_id": order_id,
                "status": rng.choice(["processing", "shipped", "delivered"]),
                "total_aud": round(rng.uniform(15, 350), 2),
            }
        )

    @tool(
        "query_product",
        args_schema=ProductQueryArgs,
        description="Return a randomly generated product example. The result is demo data.",
    )
    async def query_product(product_id: str) -> ToolResult:
        return ToolResult.success(
            {
                "demo": True,
                "product_id": product_id,
                "name": rng.choice(["Everyday Tote", "Travel Mug", "Desk Lamp"]),
                "in_stock": rng.choice([True, False]),
                "price_aud": round(rng.uniform(9, 200), 2),
            }
        )

    @tool(
        "query_logistics",
        args_schema=LogisticsQueryArgs,
        description="Return randomly generated shipment progress. The result is demo data.",
    )
    async def query_logistics(order_id: str) -> ToolResult:
        return ToolResult.success(
            {
                "demo": True,
                "order_id": order_id,
                "status": rng.choice(["label_created", "in_transit", "out_for_delivery"]),
                "estimated_days": rng.randint(0, 6),
                "carrier": rng.choice(["Demo Post", "Demo Express"]),
            }
        )

    @tool(
        "query_faq",
        args_schema=FAQQueryArgs,
        description=(
            "Search FAQ questions for an exact substring from the current user message. "
            "Do not paraphrase or substitute synonyms; percent and underscore are literal."
        ),
    )
    async def query_faq(keyword: str) -> ToolResult:
        if keyword not in ctx.user_message:
            return ToolResult.error(
                "KEYWORD_NOT_IN_MESSAGE",
                "The FAQ keyword must be an exact substring of the current message.",
            )

        matches = await faq.search_literal(keyword, limit=5)
        if not matches:
            return ToolResult.not_found(
                data=[],
                message="No FAQ question contains that exact keyword.",
            )
        return ToolResult.success(matches)

    @tool(
        "create_ticket",
        args_schema=CreateTicketArgs,
        description=(
            "Create a support ticket for the current server-side conversation. "
            "The conversation and tool call identity are supplied by the server."
        ),
    )
    async def create_ticket(
        description: str,
        ticket_type: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> ToolResult:
        ticket = await tickets.create_once(
            ctx,
            tool_call_id,
            description,
            ticket_type,
        )
        return ToolResult.success(ticket)

    return {
        query_order.name: query_order,
        query_product.name: query_product,
        query_logistics.name: query_logistics,
        query_faq.name: query_faq,
        create_ticket.name: create_ticket,
    }
