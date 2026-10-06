from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class FAQ(Base):
    __tablename__ = "faq"
    __table_args__ = (UniqueConstraint("question", name="uq_faq_question"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(String(256), nullable=False)
    answer: Mapped[str] = mapped_column(String(1000), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('idle', 'processing')",
            name="ck_conversations_status",
        ),
        CheckConstraint(
            "(active_turn_id IS NULL AND active_until IS NULL AND status = 'idle') OR "
            "(active_turn_id IS NOT NULL AND active_until IS NOT NULL AND status = 'processing')",
            name="ck_conversations_active_turn",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    active_turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utc_now)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'tool')", name="ck_messages_role"),
        CheckConstraint(
            "turn_status IS NULL OR turn_status IN ('completed', 'failed', 'cancelled')",
            name="ck_messages_turn_status",
        ),
        UniqueConstraint(
            "conversation_id",
            "turn_id",
            "role",
            "tool_call_id",
            name="uq_messages_tool_result",
        ),
        Index("ix_messages_conversation_turn_id", "conversation_id", "turn_id", "id"),
        Index("ix_messages_conversation_status_id", "conversation_id", "turn_status", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", name="fk_messages_conversation"), nullable=False
    )
    turn_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    turn_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utc_now)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(
            "ticket_type IN ('refund', 'return', 'exchange', 'logistics', 'other')",
            name="ck_tickets_type",
        ),
        UniqueConstraint("conversation_id", "turn_id", "tool_call_id", name="uq_tickets_tool_call"),
        Index("ix_tickets_conversation_created", "conversation_id", "created_at"),
    )

    ticket_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", name="fk_tickets_conversation"), nullable=False
    )
    turn_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False)
    ticket_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utc_now)
