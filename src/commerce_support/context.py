"""Chat message construction and context budgeting."""

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
