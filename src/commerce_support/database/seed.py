from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from commerce_support.database.models import FAQ, Conversation, Message, Ticket
from commerce_support.resources.customer_support.loader import load_faq_seed_catalog

DEMO_CONVERSATION_ID = "00000000-0000-0000-0000-000000000001"
DEMO_TURN_ID = "00000000-0000-0000-0000-000000000002"
DEMO_TICKET_ID = "DEMO-000001"

def _faq_values(row: Mapping[str, object] | FAQ) -> dict[str, object]:
    if isinstance(row, Mapping):
        return {
            "id": row.get("id"),
            "question": row.get("question"),
            "answer": row.get("answer"),
            "category": row.get("category"),
        }
    return {
        "id": row.id,
        "question": row.question,
        "answer": row.answer,
        "category": row.category,
    }


def _same_faq_row(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return all(left.get(key) == right.get(key) for key in ("id", "question", "answer", "category"))


def plan_faq_sync(
    desired_rows: Sequence[Mapping[str, object]],
    existing_rows: Sequence[Mapping[str, object] | FAQ],
    known_prior_rows: Mapping[int, Sequence[Mapping[str, object]]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    existing_by_id: dict[int, dict[str, object]] = {}
    existing_by_question: dict[str, dict[str, object]] = {}
    for raw_row in existing_rows:
        row = _faq_values(raw_row)
        row_id = row.get("id")
        question = row.get("question")
        if not isinstance(row_id, int) or not isinstance(question, str):
            raise TypeError("FAQ table contains an invalid ID or question")
        if row_id in existing_by_id:
            raise ValueError(f"FAQ table contains duplicate ID {row_id}")
        if question in existing_by_question:
            raise ValueError(f"FAQ table contains duplicate question {question!r}")
        existing_by_id[row_id] = row
        existing_by_question[question] = row

    inserts: list[dict[str, object]] = []
    updates: list[dict[str, object]] = []
    desired_ids: set[int] = set()
    desired_questions: set[str] = set()
    for raw_desired in desired_rows:
        desired = dict(raw_desired)
        row_id = desired.get("id")
        question = desired.get("question")
        if not isinstance(row_id, int) or not isinstance(question, str):
            raise TypeError("resource FAQ rows need an integer ID and question")
        if row_id in desired_ids or question in desired_questions:
            raise ValueError("resource FAQ rows contain a duplicate ID or question")
        desired_ids.add(row_id)
        desired_questions.add(question)

        question_occupant = existing_by_question.get(question)
        if question_occupant is not None and question_occupant.get("id") != row_id:
            raise ValueError(
                f"resource FAQ question {question!r} is occupied by ID "
                f"{question_occupant['id']}"
            )

        current = existing_by_id.get(row_id)
        if current is not None:
            if _same_faq_row(current, desired):
                continue
            known_states = known_prior_rows.get(row_id, ())
            if any(_same_faq_row(current, known) for known in known_states):
                updates.append(desired)
                continue
            raise ValueError(f"managed FAQ ID {row_id} is occupied by an unknown row")

        inserts.append(desired)
    return inserts, updates


async def _synchronize_resource_faqs(session: AsyncSession) -> None:
    catalog = load_faq_seed_catalog()
    existing_rows = list(await session.scalars(select(FAQ)))
    inserts, updates = plan_faq_sync(
        catalog.rows,
        existing_rows,
        catalog.known_prior_rows,
    )
    by_id = {row.id: row for row in existing_rows}
    for row in updates:
        existing = by_id[row["id"]]
        existing.question = row["question"]
        existing.answer = row["answer"]
        existing.category = row["category"]
    for row in inserts:
        session.add(FAQ(**row))


async def seed_demo_data(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session, session.begin():
        await _synchronize_resource_faqs(session)

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
