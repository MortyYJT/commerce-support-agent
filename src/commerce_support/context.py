"""Chat message construction and context budgeting."""

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from commerce_support.errors import BudgetExceeded
from commerce_support.schemas import HistoryMessage

_BUDGET_ERROR_CODE = "context_budget_exceeded"
_BUDGET_ERROR_MESSAGE = "当前问题和必要指令超过输入预算，请缩短当前问题后重试。"


def estimate_tokens(messages: list[BaseMessage]) -> int:
    """Estimate prompt size from UTF-8 bytes and a fixed per-message overhead."""
    return 3 + sum(len(message.content.encode("utf-8")) + 8 for message in messages)


def build_chat_messages(
    system: str,
    history: list[HistoryMessage],
    message: str,
    budget: int,
) -> list[BaseMessage]:
    """Build chat messages, dropping oldest complete history rounds to fit the budget."""
    system_message = SystemMessage(content=system)
    current_message = HumanMessage(content=message)
    retained_history = list(history)

    while True:
        messages: list[BaseMessage] = [system_message]
        for history_message in retained_history:
            if history_message.role == "user":
                messages.append(HumanMessage(content=history_message.content))
            else:
                messages.append(AIMessage(content=history_message.content))
        messages.append(current_message)

        if estimate_tokens(messages) <= budget:
            return messages
        if not retained_history:
            raise BudgetExceeded(
                code=_BUDGET_ERROR_CODE,
                public_message=_BUDGET_ERROR_MESSAGE,
            )

        del retained_history[:2]


def build_persisted_messages(
    system: str,
    history_groups: list[list[BaseMessage]],
    current_group: list[BaseMessage],
    tool_schemas: Sequence[Any],
    budget: int,
) -> list[BaseMessage]:
    """Build a prompt while trimming only complete persisted history turns."""
    system_message = SystemMessage(content=system)
    retained_history = list(history_groups)

    while True:
        messages = [system_message]
        for group in retained_history:
            messages.extend(group)
        messages.extend(current_group)

        if _estimate_persisted_size(messages, tool_schemas) <= budget:
            return messages
        if not retained_history:
            raise BudgetExceeded(
                code=_BUDGET_ERROR_CODE,
                public_message=_BUDGET_ERROR_MESSAGE,
            )

        del retained_history[0]


def _estimate_persisted_size(
    messages: list[BaseMessage],
    tool_schemas: Sequence[Any],
) -> int:
    size = 3
    for message in messages:
        size += len(_json_bytes(message.model_dump(mode="json"))) + 8
    for schema in tool_schemas:
        size += len(_json_bytes(_tool_schema_payload(schema))) + 8
    return size


def _tool_schema_payload(schema: Any) -> Any:
    if isinstance(schema, dict):
        return schema

    name = getattr(schema, "name", None)
    description = getattr(schema, "description", "")
    input_schema = getattr(schema, "tool_call_schema", None)
    if input_schema is None:
        input_schema = getattr(schema, "args_schema", None)
    if input_schema is not None and hasattr(input_schema, "model_json_schema"):
        input_schema = input_schema.model_json_schema()
    return {"name": name, "description": description, "parameters": input_schema}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
