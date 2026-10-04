from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from commerce_support import context, errors
from commerce_support.schemas import HistoryMessage


def test_keeps_recent_complete_round():
    history = [
        HistoryMessage(role="user", content="old"),
        HistoryMessage(role="assistant", content="old answer"),
        HistoryMessage(role="user", content="new"),
        HistoryMessage(role="assistant", content="new answer"),
    ]

    messages = context.build_chat_messages(system="S", history=history, message="Q", budget=50)

    assert messages == [
        SystemMessage(content="S"),
        HumanMessage(content="new"),
        AIMessage(content="new answer"),
        HumanMessage(content="Q"),
    ]
    assert context.estimate_tokens(messages) == 50


def test_exact_budget_is_allowed():
    messages = context.build_chat_messages(system="S", history=[], message="C", budget=21)

    assert messages == [SystemMessage(content="S"), HumanMessage(content="C")]
    assert context.estimate_tokens(messages) == 21


def test_current_message_over_budget():
    with pytest.raises(errors.BudgetExceeded) as raised:
        context.build_chat_messages(system="S", history=[], message="C", budget=20)

    assert raised.value.code == "context_budget_exceeded"
    assert raised.value.status_code == 413


def test_unicode_budget_uses_utf8_bytes():
    messages = context.build_chat_messages(system="你🙂", history=[], message="好💡", budget=33)

    assert messages == [SystemMessage(content="你🙂"), HumanMessage(content="好💡")]
    assert context.estimate_tokens(messages) == 33


def test_no_history_keeps_only_system_and_current_message():
    messages = context.build_chat_messages(
        system="policy", history=[], message="question", budget=33
    )

    assert messages == [
        SystemMessage(content="policy"),
        HumanMessage(content="question"),
    ]
