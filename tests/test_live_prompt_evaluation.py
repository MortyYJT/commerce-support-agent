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
    "case_id": "postage_success",
    "category": "required",
    "user_message": "邮费是多少？",
    "expected_tool": "query_faq",
    "expected_arguments": {"keyword": "邮费"},
}
_ANSWER = "订单金额满99元包邮；未满99元收取10元基础运费，偏远地区另加12元。"
_STORED = {
    "tool_calls": [
        {
            "name": "query_faq",
            "args": {"keyword": "邮费"},
            "id": "call-replay-test",
            "type": "tool_call",
        }
    ],
    "tool_results": [
        {
            "status": "success",
            "data": [
                {
                    "id": 2,
                    "question": "邮费是多少？",
                    "answer": _ANSWER,
                    "category": "shipping",
                }
            ],
        }
    ],
    "ticket_results": [],
    "final_answer": _ANSWER,
}


def _replay(
    *,
    keyword: str = "邮费",
    row_count: int = 1,
    rows: list[dict[str, Any]] | None = None,
    query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if query is None:
        query = {
            "statement": "SELECT id FROM faq WHERE question LIKE concat('%%', %s, '%%')",
            "bound_parameters": [keyword, 5],
        }
    if rows is None:
        rows = [
            {
                "id": 2,
                "question": "邮费是多少？",
                "answer": _ANSWER,
                "category": "shipping",
            }
        ][:row_count]
    return {
        "source": "live replay of FAQRepository.search_literal",
        "keyword": keyword,
        "row_count": row_count,
        "rows": rows,
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


def test_postage_policy_and_sql_replay_hit_are_recorded_as_passing(monkeypatch, capsys) -> None:
    replay = _replay()

    exit_code, evaluated = _run_postage_evaluation(monkeypatch, capsys, replay)

    assert exit_code == 0
    assert evaluated["deterministic_checks_pass"] is True
    assert evaluated["checks"].get("faq_sql_replay_query_captured") is True
    assert evaluated["checks"].get("faq_sql_replay_keyword_matches_expected") is True
    assert evaluated["checks"].get("faq_sql_replay_returns_expected_rows") is True
    assert evaluated["actual_tool_result"]["status"] == "success"
    assert "99元" in evaluated["final_answer"]
    assert "10元" in evaluated["final_answer"]
    assert "12元" in evaluated["final_answer"]
    assert "澳元" not in evaluated["final_answer"]
    assert evaluated["faq_sql_replay"] == replay


@pytest.mark.parametrize(
    ("replay", "failed_check"),
    [
        pytest.param(
            _replay(row_count=0, rows=[]),
            "faq_sql_replay_returns_expected_rows",
            id="replay-returned-no-rows",
        ),
        pytest.param(
            _replay(
                rows=[
                    {
                        "id": 4,
                        "question": "运费怎么算？",
                        "answer": _ANSWER,
                        "category": "shipping",
                    }
                ],
            ),
            "faq_sql_replay_returns_expected_rows",
            id="replay-row-does-not-match-original-keyword",
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
        pytest.param(
            _replay(
                query={
                    "statement": "SELECT id FROM faq WHERE question LIKE %s",
                    "bound_parameters": ["邮费"],
                },
            ),
            "faq_sql_replay_limit_is_five",
            id="replay-did-not-bind-five-row-limit",
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


@pytest.mark.parametrize(
    ("actual_keyword", "replay_keyword", "keyword_is_valid", "should_pass"),
    [
        pytest.param(
            "运费险理赔多久到账",
            "运费险理赔多久到账",
            True,
            True,
            id="long-original-substring-keeps-labeled-topic",
        ),
        pytest.param(
            "运费", "运费", False, False, id="does-not-preserve-labeled-topic"
        ),
        pytest.param(
            "运费险理赔时限",
            "运费险理赔时限",
            False,
            False,
            id="keyword-is-not-in-user-message",
        ),
        pytest.param(
            "运费险理赔多久到账",
            "运费险",
            True,
            False,
            id="sql-replay-does-not-match-actual-call",
        ),
    ],
)
def test_unknown_faq_case_requires_topic_preserving_substring_and_matching_replay(
    actual_keyword: str,
    replay_keyword: str,
    keyword_is_valid: bool,
    should_pass: bool,
) -> None:
    case = {
        "case_id": "unknown_faq_no_match",
        "category": "expected_no_match",
        "user_message": "运费险理赔多久到账？",
        "expected_tool": "query_faq",
        "expected_arguments": {"keyword": "运费险"},
    }
    stored = {
        "tool_calls": [
            {"name": "query_faq", "args": {"keyword": actual_keyword}, "id": "call-unknown"}
        ],
        "tool_results": [{"status": "not_found", "data": []}],
        "ticket_results": [],
        "final_answer": "没有找到包含“运费险”的 FAQ。",
    }
    replay = _replay(keyword=replay_keyword, row_count=0, rows=[])
    evaluated = evaluate_live_prompts._evaluate_case(
        case,
        [("done", {"finish_reason": "stop"})],
        stored,
        evidence_mode="live_api_real_tools",
    )
    assert evaluated["checks"]["original_faq_keyword_preserved"] is keyword_is_valid

    evaluate_live_prompts._apply_postage_sql_replay_checks(
        evaluated,
        replay,
        "运费险",
        expect_hit=False,
        actual_keyword=actual_keyword,
        user_message=case["user_message"],
    )

    assert evaluated["checks"]["actual_faq_query_returned_zero_rows"] is True
    assert evaluated["checks"]["faq_sql_replay_returns_zero_rows"] is True
    assert evaluated["checks"]["faq_sql_replay_keyword_matches_actual_call"] is should_pass
    assert evaluated["checks"]["faq_sql_replay_limit_is_five"] is True
    assert evaluated["deterministic_checks_pass"] is should_pass


def test_active_cases_encode_migrated_faq_policy_and_a_real_unknown_query() -> None:
    from commerce_support.resources.customer_support.loader import load_faq_seed_rows

    cases = {case["case_id"]: case for case in evaluate_live_prompts._load_cases()}
    return_answer = cases["return_policy"]["tool_result"]["data"][0]["answer"].replace(" ", "")
    postage_case = cases["postage_success"]
    postage_answer = postage_case["tool_result"]["data"][0]["answer"].replace(" ", "")
    unknown_keyword = cases["unknown_faq_no_match"]["expected_arguments"]["keyword"]

    assert "7天从签收之日起算" in return_answer and "30天" not in return_answer
    assert all(f"{amount}元" in postage_answer for amount in ("99", "10", "12"))
    assert postage_case["expected_arguments"]["keyword"] == "邮费"
    assert "邮费" in postage_case["tool_result"]["data"][0]["question"]
    assert unknown_keyword == "运费险"
    assert all(unknown_keyword not in row["question"] for row in load_faq_seed_rows())


@pytest.mark.parametrize(
    ("result_answer", "final_answer", "should_pass"),
    [
        pytest.param("7 天内支持无理由退货，7 天从签收之日起算。", "7 天从签收之日起算。", True, id="seven-days"),
        pytest.param(
            "7 天内支持无理由退货，7 天从签收之日起算。",
            "支持 **7 天无理由退货**；7 天从**签收之日**起算。",
            True,
            id="markdown-and-separated-receipt-clock",
        ),
        pytest.param("符合条件的商品可在签收后 30 天内申请退货。", "30 天内可以申请。", False, id="stale-thirty-days"),
        pytest.param(
            "7 天内支持无理由退货，7 天从签收之日起算。",
            "支持 7 天无理由退货，期限从下单之日起算。",
            False,
            id="wrong-order-date-clock",
        ),
        pytest.param(
            "7 天内支持无理由退货，7 天从签收之日起算。",
            "签收后 17 天内可以申请无理由退货。",
            False,
            id="seventeen-days-is-not-seven-days",
        ),
    ],
)
def test_return_policy_eval_rejects_stale_window(
    result_answer: str,
    final_answer: str,
    should_pass: bool,
) -> None:
    case = {
        "case_id": "return_policy",
        "user_message": "退货政策是什么？",
        "expected_tool": "query_faq",
        "expected_arguments": {"keyword": "退货政策"},
    }
    stored = {
        "tool_calls": [
            {"name": "query_faq", "args": {"keyword": "退货政策"}, "id": "call-return"}
        ],
        "tool_results": [
            {
                "status": "success",
                "data": [{"question": "退货政策是什么？", "answer": result_answer}],
            }
        ],
        "ticket_results": [],
        "final_answer": final_answer,
    }

    evaluated = evaluate_live_prompts._evaluate_case(
        case,
        [("done", {"finish_reason": "stop"})],
        stored,
        evidence_mode="live_api_real_tools",
    )

    assert evaluated["deterministic_checks_pass"] is should_pass
