from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from commerce_support.config import Settings

_EVALUATOR_PATH = Path(__file__).resolve().parents[1] / "scripts/evaluate_live_prompts.py"
_EVALUATOR_SPEC = importlib.util.spec_from_file_location("evaluate_live_prompts", _EVALUATOR_PATH)
assert _EVALUATOR_SPEC is not None and _EVALUATOR_SPEC.loader is not None
evaluate_live_prompts = importlib.util.module_from_spec(_EVALUATOR_SPEC)
_EVALUATOR_SPEC.loader.exec_module(evaluate_live_prompts)

_POSTAGE_CASE = {
    "case_id": "postage_no_match",
    "category": "expected_no_match",
    "user_message": "邮费是多少？",
    "expected_tool": "query_faq",
    "expected_arguments": {"keyword": "邮费"},
}
_ANSWER = "没有找到包含“邮费”的 FAQ。"
_STORED = {
    "tool_calls": [
        {
            "name": "query_faq",
            "args": {"keyword": "邮费"},
            "id": "call-replay-test",
            "type": "tool_call",
        }
    ],
    "tool_results": [{"status": "not_found", "data": []}],
    "ticket_results": [],
    "final_answer": _ANSWER,
}


def _replay(
    *,
    keyword: str = "邮费",
    row_count: int = 0,
    query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if query is None:
        query = {
            "statement": "SELECT id FROM faq WHERE question LIKE concat('%%', %s, '%%')",
            "bound_parameters": ["邮费", 5],
        }
    return {
        "source": "live replay of FAQRepository.search_literal",
        "keyword": keyword,
        "row_count": row_count,
        "query": query,
    }


def _run_postage_evaluation(monkeypatch, capsys, replay: dict[str, Any]):
    class FakeDatabase:
        def __init__(self, _settings: Settings) -> None:
            self.sessions = object()
            self.turn_lease_seconds = 180

        async def check_ready(self) -> bool:
            return True

        async def aclose(self) -> None:
            return None

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "text/event-stream"}
            self.text = (
                'event: conversation\ndata: {"conversation_id":"conversation-replay"}\n\n'
                f"event: delta\ndata: {json.dumps({'content': _ANSWER}, ensure_ascii=False)}\n\n"
                'event: done\ndata: {"finish_reason":"stop"}\n\n'
            )

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def get(self, _url: str) -> FakeResponse:
            return FakeResponse()

        async def post(self, _url: str, *, json: dict[str, Any]) -> FakeResponse:
            assert json == {"message": _POSTAGE_CASE["user_message"]}
            return FakeResponse()

    async def stored_observation(_repository, _conversation_id: str):
        return _STORED

    async def sql_replay(_database, keyword: str):
        assert keyword == _STORED["tool_calls"][0]["args"]["keyword"]
        return replay

    synthetic_url = "mysql+asyncmy://test:test@127.0.0.1/test"
    monkeypatch.setattr(
        evaluate_live_prompts,
        "_settings_and_host_database_url",
        lambda: (Settings(_env_file=None, llm_api_key="synthetic-test-key"), synthetic_url),
    )
    monkeypatch.setattr(evaluate_live_prompts, "_load_cases", lambda: [_POSTAGE_CASE])
    monkeypatch.setattr(evaluate_live_prompts, "Database", FakeDatabase)
    monkeypatch.setattr(evaluate_live_prompts, "_read_completed_conversation", stored_observation)
    monkeypatch.setattr(evaluate_live_prompts, "_faq_sql_replay", sql_replay)
    monkeypatch.setattr(evaluate_live_prompts.httpx, "AsyncClient", FakeClient)

    exit_code = asyncio.run(
        evaluate_live_prompts._evaluate(
            SimpleNamespace(base_url="http://model.invalid", timeout_seconds=5, case_id=None)
        )
    )
    output_lines = capsys.readouterr().out.splitlines()
    return exit_code, json.loads(output_lines[0])


def test_consistent_postage_sql_replay_is_recorded_as_passing(monkeypatch, capsys) -> None:
    replay = _replay()

    exit_code, evaluated = _run_postage_evaluation(monkeypatch, capsys, replay)

    assert exit_code == 0
    assert evaluated["deterministic_checks_pass"] is True
    assert evaluated["checks"].get("faq_sql_replay_query_captured") is True
    assert evaluated["checks"].get("faq_sql_replay_keyword_matches_expected") is True
    assert evaluated["checks"].get("faq_sql_replay_returns_zero_rows") is True
    assert evaluated["actual_tool_result"] == {"status": "not_found", "data": []}
    assert evaluated["faq_sql_replay"] == replay


@pytest.mark.parametrize(
    ("replay", "failed_check"),
    [
        pytest.param(
            _replay(row_count=1),
            "faq_sql_replay_returns_zero_rows",
            id="replay-returned-rows",
        ),
        pytest.param(
            {
                "source": "live replay of FAQRepository.search_literal",
                "keyword": "邮费",
                "row_count": 0,
                "query": None,
            },
            "faq_sql_replay_query_captured",
            id="replay-did-not-capture-query",
        ),
        pytest.param(
            _replay(
                query={
                    "statement": "SELECT id FROM faq WHERE question LIKE %s",
                    "bound_parameters": ["运费", 5],
                },
            ),
            "faq_sql_replay_keyword_matches_expected",
            id="replay-used-a-different-keyword",
        ),
    ],
)
def test_inconsistent_postage_sql_replay_fails_the_evaluation(
    monkeypatch,
    capsys,
    replay: dict[str, Any],
    failed_check: str,
) -> None:
    exit_code, evaluated = _run_postage_evaluation(monkeypatch, capsys, replay)

    assert exit_code == 1
    assert evaluated["deterministic_checks_pass"] is False
    assert evaluated["checks"].get(failed_check) is False
