import hashlib
import json
from datetime import timedelta
from typing import Any, Literal
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from commerce_support.chat_types import TurnContext
from commerce_support.database.engine import Database
from commerce_support.database.exceptions import (
    ConversationNotFoundError,
    CorruptHistoryError,
    ToolCallNotFoundError,
    ToolResultConflictError,
    TurnConflictError,
    TurnOwnershipError,
)
from commerce_support.database.models import FAQ, Conversation, Message, Ticket, utc_now

TurnStatus = Literal["completed", "failed", "cancelled"]
TICKET_TYPES = {"refund", "return", "exchange", "logistics", "other"}
MAX_TOOL_RESULT_BYTES = 4096


class ChatRepository:
    def __init__(self, database: Database) -> None:
        self._sessions = database.sessions
        self._turn_lease_seconds = database.turn_lease_seconds

    async def begin_turn(
        self,
        message: str,
        conversation_id: str | None = None,
    ) -> TurnContext:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must not be empty")

        is_new_conversation = conversation_id is None
        if conversation_id is None:
            conversation_id = str(uuid4())
        turn_id = str(uuid4())

        async with self._sessions() as session, session.begin():
            conversation = await session.scalar(
                select(Conversation).where(Conversation.id == conversation_id).with_for_update()
            )
            if conversation is None:
                if not is_new_conversation:
                    raise ConversationNotFoundError("conversation not found")
                conversation = Conversation(
                    id=conversation_id,
                    user_id="demo-user",
                    status="idle",
                    active_turn_id=None,
                    active_until=None,
                )
                session.add(conversation)
                await session.flush()

            now = utc_now()
            if conversation.active_turn_id is not None:
                if conversation.active_until is None or conversation.active_until > now:
                    raise TurnConflictError("conversation already has an active turn")

                expired_turn_id = conversation.active_turn_id
                await session.execute(
                    update(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.turn_id == expired_turn_id,
                    )
                    .values(turn_status="failed")
                )
                conversation.active_turn_id = None
                conversation.active_until = None
                conversation.status = "idle"
                await session.flush()

            active_until = now + timedelta(seconds=self._turn_lease_seconds)
            claimed = await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.active_turn_id.is_(None),
                )
                .values(
                    active_turn_id=turn_id,
                    active_until=active_until,
                    status="processing",
                )
            )
            if claimed.rowcount != 1:
                raise TurnConflictError("conversation already has an active turn")

            session.add(
                Message(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    role="user",
                    content=message,
                )
            )

        return TurnContext(
            conversation_id=conversation_id,
            turn_id=turn_id,
            user_message=message,
        )

    async def successful_history(self, conversation_id: str) -> list[list[BaseMessage]]:
        async with self._sessions() as session:
            messages = list(
                await session.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.turn_status == "completed",
                    )
                    .order_by(Message.id)
                )
            )

        grouped: dict[str, list[Message]] = {}
        for message in messages:
            grouped.setdefault(message.turn_id, []).append(message)

        history: list[list[BaseMessage]] = []
        for turn_messages in grouped.values():
            converted: list[BaseMessage] = []
            call_names: dict[str, str] = {}
            pending_calls: set[str] = set()
            seen_calls: set[str] = set()

            for message in turn_messages:
                if message.role == "user":
                    converted.append(HumanMessage(content=message.content))
                elif message.role == "assistant":
                    calls = message.tool_calls or []
                    for call in calls:
                        call_id = call.get("id")
                        name = call.get("name")
                        if not isinstance(call_id, str) or not isinstance(name, str):
                            raise CorruptHistoryError("stored assistant tool call is malformed")
                        if call_id in seen_calls:
                            raise CorruptHistoryError("stored assistant tool call id is duplicated")
                        seen_calls.add(call_id)
                        pending_calls.add(call_id)
                        call_names[call_id] = name
                    converted.append(AIMessage(content=message.content, tool_calls=calls))
                elif message.role == "tool":
                    call_id = message.tool_call_id
                    if call_id is None or call_id not in pending_calls:
                        raise CorruptHistoryError(
                            "stored tool result has no matching assistant call"
                        )
                    pending_calls.remove(call_id)
                    converted.append(
                        ToolMessage(
                            content=message.content,
                            tool_call_id=call_id,
                            name=call_names[call_id],
                        )
                    )
                else:
                    raise CorruptHistoryError("stored message has an unsupported role")

            if pending_calls:
                raise CorruptHistoryError(
                    "stored completed turn has unmatched assistant tool calls"
                )
            history.append(converted)

        return history

    async def append_assistant_call(self, ctx: TurnContext, message: AIMessage) -> None:
        calls = _validated_tool_calls(message)
        content = _content_to_text(message.content)

        async with self._sessions() as session, session.begin():
            await _lock_owned_conversation(session, ctx)
            previous_calls = await session.scalars(
                select(Message.tool_calls).where(
                    Message.conversation_id == ctx.conversation_id,
                    Message.turn_id == ctx.turn_id,
                    Message.role == "assistant",
                )
            )
            seen_ids = {
                call["id"]
                for prior_calls in previous_calls
                for call in prior_calls or []
                if isinstance(call.get("id"), str)
            }
            current_ids = {call["id"] for call in calls}
            if len(current_ids) != len(calls) or seen_ids.intersection(current_ids):
                raise ValueError("tool call ids must be unique within the turn")
            session.add(
                Message(
                    conversation_id=ctx.conversation_id,
                    turn_id=ctx.turn_id,
                    role="assistant",
                    content=content,
                    tool_calls=calls,
                )
            )

    async def append_tool_result(
        self,
        ctx: TurnContext,
        call_id: str,
        result: dict[str, Any] | list[Any],
    ) -> None:
        _validate_call_id(call_id)
        content = _serialize_tool_result(result)

        async with self._sessions() as session, session.begin():
            await _lock_owned_conversation(session, ctx)
            assistant_calls = await session.scalars(
                select(Message.tool_calls)
                .where(
                    Message.conversation_id == ctx.conversation_id,
                    Message.turn_id == ctx.turn_id,
                    Message.role == "assistant",
                )
                .order_by(Message.id)
            )
            matching_call = next(
                (
                    call
                    for calls in assistant_calls
                    for call in calls or []
                    if call.get("id") == call_id
                ),
                None,
            )
            if matching_call is None:
                raise ToolCallNotFoundError("tool result has no matching assistant call")

            existing = await session.scalar(
                select(Message).where(
                    Message.conversation_id == ctx.conversation_id,
                    Message.turn_id == ctx.turn_id,
                    Message.role == "tool",
                    Message.tool_call_id == call_id,
                )
            )
            if existing is not None:
                if existing.content != content:
                    raise ToolResultConflictError("tool call already has a different result")
                return

            session.add(
                Message(
                    conversation_id=ctx.conversation_id,
                    turn_id=ctx.turn_id,
                    role="tool",
                    content=content,
                    tool_call_id=call_id,
                )
            )

    async def finish_turn(self, ctx: TurnContext, text: str, status: TurnStatus) -> None:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("status must be completed, failed, or cancelled")
        if not isinstance(text, str):
            raise TypeError("turn text must be a string")

        now = utc_now()
        async with self._sessions() as session, session.begin():
            await _lock_owned_conversation(session, ctx, now=now)
            if status == "completed":
                await _require_paired_tool_calls(session, ctx)

            released = await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == ctx.conversation_id,
                    Conversation.active_turn_id == ctx.turn_id,
                    Conversation.active_until > now,
                )
                .values(active_turn_id=None, active_until=None, status="idle")
            )
            if released.rowcount != 1:
                raise TurnOwnershipError("turn no longer owns the conversation lease")

            await session.execute(
                update(Message)
                .where(
                    Message.conversation_id == ctx.conversation_id,
                    Message.turn_id == ctx.turn_id,
                )
                .values(turn_status=status)
            )
            session.add(
                Message(
                    conversation_id=ctx.conversation_id,
                    turn_id=ctx.turn_id,
                    role="assistant",
                    content=text,
                    turn_status=status,
                )
            )


class FAQRepository:
    def __init__(self, database: Database) -> None:
        self._sessions = database.sessions

    async def search_literal(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        if not isinstance(keyword, str) or not 1 <= len(keyword) <= 64:
            raise ValueError("keyword must contain between 1 and 64 characters")
        if not 1 <= limit <= 5:
            raise ValueError("limit must be between 1 and 5")

        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(FAQ)
                    .where(FAQ.question.contains(keyword, autoescape=True))
                    .order_by(FAQ.id)
                    .limit(limit)
                )
            )
        return [
            {
                "id": row.id,
                "question": row.question,
                "answer": row.answer,
                "category": row.category,
            }
            for row in rows
        ]


class TicketRepository:
    def __init__(self, database: Database) -> None:
        self._sessions = database.sessions

    async def create_once(
        self,
        ctx: TurnContext,
        call_id: str,
        description: str,
        ticket_type: str,
    ) -> dict[str, str]:
        _validate_call_id(call_id)
        if not isinstance(description, str) or not 1 <= len(description) <= 2000:
            raise ValueError("description must contain between 1 and 2000 characters")
        if ticket_type not in TICKET_TYPES:
            raise ValueError("ticket_type is not supported")

        digest = hashlib.sha256(
            f"{ctx.conversation_id}\x1f{ctx.turn_id}\x1f{call_id}".encode()
        ).hexdigest()[:20]
        ticket_id = f"TKT-{digest.upper()}"
        async with self._sessions() as session, session.begin():
            await _lock_owned_conversation(session, ctx)
            await _require_assistant_tool_call(session, ctx, call_id)
            ticket = await session.get(Ticket, ticket_id)
            if ticket is None:
                ticket = Ticket(
                    ticket_id=ticket_id,
                    conversation_id=ctx.conversation_id,
                    turn_id=ctx.turn_id,
                    tool_call_id=call_id,
                    description=description,
                    ticket_type=ticket_type,
                    status="open",
                )
                session.add(ticket)
                await session.flush()
            return _ticket_result(ticket)


async def _lock_owned_conversation(
    session: AsyncSession,
    ctx: TurnContext,
    *,
    now=None,
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == ctx.conversation_id).with_for_update()
    )
    if conversation is None:
        raise ConversationNotFoundError("conversation not found")

    check_time = now or utc_now()
    if (
        conversation.active_turn_id != ctx.turn_id
        or conversation.active_until is None
        or conversation.active_until <= check_time
    ):
        raise TurnOwnershipError("turn no longer owns the conversation lease")
    return conversation


async def _require_assistant_tool_call(
    session: AsyncSession,
    ctx: TurnContext,
    call_id: str,
) -> dict[str, Any]:
    tool_calls = await session.scalars(
        select(Message.tool_calls).where(
            Message.conversation_id == ctx.conversation_id,
            Message.turn_id == ctx.turn_id,
            Message.role == "assistant",
        )
    )
    match = next(
        (call for calls in tool_calls for call in calls or [] if call.get("id") == call_id),
        None,
    )
    if match is None:
        raise ToolCallNotFoundError("tool operation has no matching assistant call")
    return match


async def _require_paired_tool_calls(session: AsyncSession, ctx: TurnContext) -> None:
    await_calls = await session.scalars(
        select(Message.tool_calls).where(
            Message.conversation_id == ctx.conversation_id,
            Message.turn_id == ctx.turn_id,
            Message.role == "assistant",
        )
    )
    expected_ids = {
        call.get("id")
        for calls in await_calls
        for call in calls or []
        if isinstance(call.get("id"), str)
    }
    tool_ids = list(
        await session.scalars(
            select(Message.tool_call_id).where(
                Message.conversation_id == ctx.conversation_id,
                Message.turn_id == ctx.turn_id,
                Message.role == "tool",
            )
        )
    )
    if len(tool_ids) != len(set(tool_ids)) or set(tool_ids) != expected_ids:
        raise CorruptHistoryError("completed turn must contain paired tool calls and results")


def _validated_tool_calls(message: AIMessage) -> list[dict[str, Any]]:
    calls = message.tool_calls
    if not calls:
        raise ValueError("assistant message must contain at least one tool call")
    try:
        serializable_calls = json.loads(
            json.dumps(calls, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        )
    except (TypeError, ValueError) as error:
        raise ValueError("assistant tool calls must be JSON serializable") from error

    for call in serializable_calls:
        if not isinstance(call, dict):
            raise TypeError("assistant tool calls must be objects")
        call_id = call.get("id")
        name = call.get("name")
        args = call.get("args")
        _validate_call_id(call_id)
        if not isinstance(name, str) or not name:
            raise ValueError("assistant tool call name is required")
        if not isinstance(args, dict):
            raise TypeError("assistant tool call arguments must be an object")
    return serializable_calls


def _validate_call_id(call_id: object) -> None:
    if not isinstance(call_id, str) or not 1 <= len(call_id) <= 64:
        raise ValueError("tool call id must contain between 1 and 64 characters")


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("message content must be JSON serializable") from error


def _serialize_tool_result(result: dict[str, Any] | list[Any]) -> str:
    if not isinstance(result, (dict, list)):
        raise TypeError("tool result must be a JSON object or array")
    try:
        serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("tool result must be JSON serializable") from error
    if len(serialized.encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
        raise ValueError("tool result exceeds the 4096-byte storage limit")
    return serialized


def _ticket_result(ticket: Ticket) -> dict[str, str]:
    return {
        "ticket_id": ticket.ticket_id,
        "description": ticket.description,
        "ticket_type": ticket.ticket_type,
        "status": ticket.status,
    }
