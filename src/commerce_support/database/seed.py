from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from commerce_support.database.models import FAQ, Conversation, Message, Ticket

DEMO_CONVERSATION_ID = "00000000-0000-0000-0000-000000000001"
DEMO_TURN_ID = "00000000-0000-0000-0000-000000000002"
DEMO_TICKET_ID = "DEMO-000001"

FAQ_ROWS = (
    {
        "id": 1,
        "question": "退货政策是什么？",
        "answer": "演示政策：符合条件的商品可在签收后30天内申请退货。",
        "category": "returns",
    },
    {
        "id": 2,
        "question": "运费是多少？",
        "answer": "演示费用：标准配送运费为8澳元。",
        "category": "shipping",
    },
)


async def seed_demo_data(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session, session.begin():
        for row in FAQ_ROWS:
            if await session.get(FAQ, row["id"]) is None:
                session.add(FAQ(**row))

        if await session.get(Conversation, DEMO_CONVERSATION_ID) is None:
            session.add(
                Conversation(
                    id=DEMO_CONVERSATION_ID,
                    user_id="demo-seed",
                    status="idle",
                    active_turn_id=None,
                    active_until=None,
                )
            )
            await session.flush()

        seeded_message = await session.scalar(
            select(Message.id)
            .where(
                Message.conversation_id == DEMO_CONVERSATION_ID,
                Message.turn_id == DEMO_TURN_ID,
            )
            .limit(1)
        )
        if seeded_message is None:
            session.add_all(
                [
                    Message(
                        conversation_id=DEMO_CONVERSATION_ID,
                        turn_id=DEMO_TURN_ID,
                        role="user",
                        content="演示对话已就绪。",
                        turn_status="completed",
                    ),
                    Message(
                        conversation_id=DEMO_CONVERSATION_ID,
                        turn_id=DEMO_TURN_ID,
                        role="assistant",
                        content="需要查询业务信息时，请直接告诉我。",
                        turn_status="completed",
                    ),
                ]
            )

        if await session.get(Ticket, DEMO_TICKET_ID) is None:
            session.add(
                Ticket(
                    ticket_id=DEMO_TICKET_ID,
                    conversation_id=DEMO_CONVERSATION_ID,
                    turn_id=DEMO_TURN_ID,
                    tool_call_id="demo-seed-ticket-call",
                    description="Sample return request for local demonstration.",
                    ticket_type="return",
                    status="open",
                )
            )
