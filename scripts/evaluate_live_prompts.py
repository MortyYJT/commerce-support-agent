from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import dotenv_values
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import SecretStr
from sqlalchemy import event, select

from commerce_support.chat_types import StreamEvent
from commerce_support.config import Settings
from commerce_support.database.engine import Database
from commerce_support.database.models import Ticket
from commerce_support.database.repository import (
    ChatRepository,
    FAQRepository,
    TicketRepository,
)
from commerce_support.model import ChatOpenAIModelGateway
from commerce_support.schemas import ChatRequest
from commerce_support.services import ChatService
from commerce_support.tools.executor import TOOL_RESULT_EVENT
from commerce_support.tools.registry import build_registry
from commerce_support.tools.schemas import ToolResult

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = REPO_ROOT / "note/stage2-add_tool/evaluation/prompt-cases.jsonl"


class FailureInjectionExecutor:
    def __init__(self, result: ToolResult) -> None:
        self._result = result

    async def execute(self, call: dict[str, Any], _registry: dict[str, Any], _ctx):
        name = call.get("name")
        call_id = call.get("id")
        yield StreamEvent(
            "tool_status",
            {"name": name, "tool_call_id": call_id, "status": "running", "attempt": 1},
        )
        yield StreamEvent(
            "tool_status",
            {"name": name, "tool_call_id": call_id, "status": "failed", "attempt": 1},
        )
        yield StreamEvent(
            TOOL_RESULT_EVENT,
            {"name": name, "tool_call_id": call_id, "attempt": 1, "result": self._result},
        )


def _load_cases() -> list[dict[str, Any]]:
    cases = []
    with CASES_PATH.open(encoding="utf-8") as case_file:
        for line_number, line in enumerate(case_file, start=1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid prompt case on line {line_number}.") from error
            if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
                raise TypeError(f"Prompt case on line {line_number} has no string case_id.")
            cases.append(case)
    return cases


def _settings_and_host_database_url() -> tuple[Settings, str]:
    dotenv = dotenv_values(REPO_ROOT / ".env")
    mysql_password = os.environ.get("MYSQL_PASSWORD") or dotenv.get("MYSQL_PASSWORD")
    if not isinstance(mysql_password, str) or not mysql_password:
        raise ValueError("MYSQL_PASSWORD must be set in the local environment or .env file.")

    settings = Settings(_env_file=REPO_ROOT / ".env")
    if settings.llm_api_key is None:
        raise ValueError("LLM_API_KEY must be configured for live model evaluation.")

    encoded_password = quote(mysql_password, safe="")
    database_url = (
        "mysql+asyncmy://commerce_support:"
        f"{encoded_password}@127.0.0.1:3307/commerce_support"
    )
    settings = settings.model_copy(update={"database_url": SecretStr(database_url)})
    return settings, database_url


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.replace("\r\n", "\n").strip().split("\n\n"):
        event_name = None
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.partition(":")[2].strip()
            elif line.startswith("data:"):
                data_lines.append(line.partition(":")[2].lstrip())
        if event_name and data_lines:
            try:
                data = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                events.append((event_name, data))
    return events


def _tool_calls(messages: list[Any]) -> list[dict[str, Any]]:
    calls = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        calls.extend(dict(call) for call in message.tool_calls or [])
        calls.extend(dict(call) for call in message.invalid_tool_calls or [])
    return calls


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    return ""


def _normalized_policy_text(text: str) -> str:
    return "".join(character for character in text if not character.isspace() and character not in "*_`~")


def _states_current_return_window(text: str) -> bool:
    normalized = _normalized_policy_text(text)
    if any(
        marker in normalized
        for marker in ("30天", "从下单", "自下单", "下单后", "下单之日", "订单之日")
    ):
        return False
    receipt_clock = any(
        marker in normalized
        for marker in ("从签收之日", "自签收之日", "签收之日起", "签收后")
    )
    has_seven_day_window = re.search(r"(?<!\d)7天", normalized) is not None
    return has_seven_day_window and receipt_clock


def _unknown_faq_keyword_is_valid(
    case: dict[str, Any], actual_arguments: object
) -> bool:
    expected_arguments = case.get("expected_arguments")
    expected_topic = (
        expected_arguments.get("keyword")
        if isinstance(expected_arguments, dict)
        else None
    )
    actual_keyword = (
        actual_arguments.get("keyword")
        if isinstance(actual_arguments, dict)
        else None
    )
    user_message = case.get("user_message")
    return (
        isinstance(expected_topic, str)
        and bool(expected_topic)
        and isinstance(actual_keyword, str)
        and bool(actual_keyword)
        and expected_topic in actual_keyword
        and isinstance(user_message, str)
        and actual_keyword in user_message
    )


def _stored_observation(messages: list[Any]) -> dict[str, Any]:
    calls = _tool_calls(messages)
    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
    results = []
    for message in tool_messages:
        try:
            results.append(json.loads(message.content))
        except (TypeError, json.JSONDecodeError):
            results.append({"parse_error": True})

    final_answer = ""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls and not message.invalid_tool_calls:
            final_answer = _message_text(message.content)
            break

    tickets = []
    for result in results:
        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, dict) and isinstance(data.get("ticket_id"), str):
            tickets.append(data)

    return {
        "tool_calls": calls,
        "tool_results": results,
        "ticket_results": tickets,
        "final_answer": final_answer,
    }


async def _read_completed_conversation(
    repository: ChatRepository,
    conversation_id: str,
) -> dict[str, Any] | None:
    history = await repository.successful_history(conversation_id)
    if not history:
        return None
    return _stored_observation(history[-1])


async def _read_persisted_tickets(database: Database, conversation_id: str) -> list[dict[str, str]]:
    async with database.sessions() as session:
        tickets = list(
            await session.scalars(
                select(Ticket)
                .where(Ticket.conversation_id == conversation_id)
                .order_by(Ticket.created_at)
            )
        )
    return [
        {
            "ticket_id": ticket.ticket_id,
            "ticket_type": ticket.ticket_type,
            "status": ticket.status,
            "description": ticket.description,
        }
        for ticket in tickets
    ]


async def _run_failure_injection(
    case: dict[str, Any],
    settings: Settings,
    database: Database,
) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    if case.get("case_id") != "tool_failure" or case.get("category") != "tool_failure":
        raise ValueError("Failure injection is allowed only for the labeled tool_failure case.")

    fixture = case.get("tool_result")
    if not isinstance(fixture, dict) or fixture.get("status") != "error":
        raise ValueError("The tool_failure case must contain its explicit error fixture.")

    result = ToolResult.error(
        str(fixture.get("code", "TOOL_UNAVAILABLE")),
        str(fixture.get("message", "The injected tool failure could not complete.")),
    )
    chat_repository = ChatRepository(database)
    faq_repository = FAQRepository(database)
    ticket_repository = TicketRepository(database)

    def registry_factory(ctx):
        return build_registry(
            repository=chat_repository,
            faq=faq_repository,
            tickets=ticket_repository,
            ctx=ctx,
            rng=random.Random(),
        )

    gateway = ChatOpenAIModelGateway(settings)
    service = ChatService(
        gateway,
        settings,
        chat_repository,
        FailureInjectionExecutor(result),
        registry_factory=registry_factory,
    )
    try:
        context = await service.prepare(ChatRequest(message=case["user_message"]))
        events = [event async for event in service.stream(context)]
    finally:
        await gateway.aclose()

    return [(event.event, event.data) for event in events], context.conversation_id


async def _faq_sql_replay(database: Database, keyword: str) -> dict[str, Any]:
    captured: list[dict[str, Any]] = []

    def capture(_connection, _cursor, statement, parameters, _context, _executemany) -> None:
        normalized_statement = " ".join(statement.lower().replace("`", "").split())
        if " from faq " in normalized_statement and " like " in normalized_statement:
            captured.append({"statement": statement, "bound_parameters": parameters})

    event.listen(database.engine.sync_engine, "before_cursor_execute", capture)
    try:
        rows = await FAQRepository(database).search_literal(keyword, limit=5)
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", capture)
    return {
        "source": "live replay of FAQRepository.search_literal",
        "keyword": keyword,
        "row_count": len(rows),
        "rows": rows,
        "query": captured[-1] if captured else None,
    }


def _evaluate_case(
    case: dict[str, Any],
    events: list[tuple[str, dict[str, Any]]],
    stored: dict[str, Any] | None,
    *,
    evidence_mode: str,
    http_status: int | None = None,
) -> dict[str, Any]:
    expected_tool = case.get("expected_tool")
    expected_arguments = case.get("expected_arguments")
    calls = stored["tool_calls"] if stored else []
    actual_call = calls[0] if len(calls) == 1 else None
    actual_name = actual_call.get("name") if isinstance(actual_call, dict) else None
    actual_arguments = actual_call.get("args") if isinstance(actual_call, dict) else None
    answer = stored["final_answer"] if stored else ""
    tool_results = stored["tool_results"] if stored else []
    statuses = [data for name, data in events if name == "tool_status"]
    errors = [data for name, data in events if name == "error"]
    done = any(name == "done" for name, _data in events)

    name_matches = actual_name == expected_tool and len(calls) == (0 if expected_tool is None else 1)
    arguments_match = expected_tool is None
    if isinstance(actual_arguments, dict) and isinstance(expected_arguments, dict):
        if expected_tool == "create_ticket":
            arguments_match = (
                actual_arguments.get("ticket_type") == expected_arguments.get("ticket_type")
                and isinstance(actual_arguments.get("description"), str)
                and bool(actual_arguments["description"].strip())
            )
        elif expected_tool == "query_faq" and case.get("case_id") == "unknown_faq_no_match":
            arguments_match = _unknown_faq_keyword_is_valid(case, actual_arguments)
        else:
            arguments_match = all(
                actual_arguments.get(key) == value for key, value in expected_arguments.items()
            )

    result = tool_results[0] if len(tool_results) == 1 else None
    result_status = result.get("status") if isinstance(result, dict) else None
    checks = {
        "tool_name_matches_label": name_matches,
        "tool_arguments_match_label": arguments_match,
        "one_completed_response": done and bool(answer) and not errors,
    }

    if case.get("case_id") == "postage_success":
        result_data = result.get("data") if isinstance(result, dict) else None
        checks["original_faq_keyword_preserved"] = (
            isinstance(actual_arguments, dict)
            and actual_arguments.get("keyword") == expected_arguments.get("keyword")
            and expected_arguments.get("keyword") in case["user_message"]
        )
        matching_rows = (
            [
                row
                for row in result_data
                if isinstance(row, dict)
                and isinstance(row.get("question"), str)
                and expected_arguments.get("keyword") in row["question"]
            ]
            if isinstance(result_data, list)
            else []
        )
        checks["actual_faq_query_returned_expected_rows"] = (
            result_status == "success" and bool(matching_rows) and len(result_data) <= 5
        )
        normalized_answer = "".join(answer.split())
        checks["final_answer_uses_current_fee_policy"] = all(
            value in normalized_answer for value in ("99元", "10元", "12元")
        ) and not any(value in normalized_answer for value in ("8澳元", "AUD"))

    if case.get("case_id") == "unknown_faq_no_match":
        result_data = result.get("data") if isinstance(result, dict) else None
        checks["original_faq_keyword_preserved"] = _unknown_faq_keyword_is_valid(
            case, actual_arguments
        )
        checks["actual_faq_query_returned_zero_rows"] = (
            result_status == "not_found" and result_data == []
        )
        checks["final_answer_keeps_unknown_faq_behavior"] = (
            expected_arguments.get("keyword") in answer
            and not any(value in answer for value in ("99元", "10元", "12元"))
        )

    if case.get("case_id") == "return_policy":
        result_data = result.get("data") if isinstance(result, dict) else None
        checks["faq_result_contains_policy"] = (
            result_status == "success"
            and isinstance(result_data, list)
            and any(
                "退货政策" in str(row.get("question", ""))
                and _states_current_return_window(str(row.get("answer", "")))
                for row in result_data
            )
        )
        checks["final_answer_uses_current_return_window"] = (
            _states_current_return_window(answer)
        )

    if case.get("case_id") == "logistics_1001":
        result_data = result.get("data") if isinstance(result, dict) else None
        checks["tool_result_is_demo_data"] = (
            isinstance(result_data, dict)
            and result_data.get("demo") is True
            and result_data.get("order_id") == "1001"
        )
        checks["final_answer_labels_demo_data"] = "演示" in answer or "demo" in answer.lower()

    if case.get("case_id") == "create_return_ticket":
        ticket_data = stored["ticket_results"][0] if stored and stored["ticket_results"] else None
        checks["ticket_type_matches_label"] = (
            isinstance(ticket_data, dict)
            and ticket_data.get("ticket_type") == expected_arguments.get("ticket_type")
        )
        checks["ticket_id_in_final_answer"] = (
            isinstance(ticket_data, dict)
            and isinstance(ticket_data.get("ticket_id"), str)
            and ticket_data["ticket_id"] in answer
        )

    if case.get("case_id") == "tool_failure":
        injected_failure_events = [
            data
            for name, data in events
            if name == "tool_status" and data.get("attempt") == 1
        ]
        checks["failure_fixture_is_explicitly_labeled"] = (
            case.get("category") == "tool_failure" and evidence_mode == "live_model_failure_injection"
        )
        checks["injected_error_is_persisted"] = (
            result_status == "error"
            and isinstance(result, dict)
            and result.get("code") == "TOOL_UNAVAILABLE"
            and any(data.get("status") == "running" for data in injected_failure_events)
            and any(data.get("status") == "failed" for data in injected_failure_events)
        )

    if expected_tool is None:
        checks["no_tool_status_was_emitted"] = not statuses

    core_pass = all(value is True for value in checks.values())
    return {
        "case_id": case["case_id"],
        "category": case.get("category"),
        "evidence_mode": evidence_mode,
        "http_status": http_status,
        "actual_tool": actual_name,
        "actual_arguments": actual_arguments,
        "actual_tool_result": result,
        "tool_status_events": statuses,
        "final_answer": answer,
        "expected_response_guidance": case.get("expected_response"),
        "checks": checks,
        "deterministic_checks_pass": core_pass,
        "manual_response_review_required": True,
    }


def _apply_postage_sql_replay_checks(
    evaluated: dict[str, Any],
    replay: dict[str, Any] | None,
    expected_keyword: object,
    *,
    expect_hit: bool = True,
    actual_keyword: object = None,
    user_message: object = None,
) -> None:
    replay = replay if isinstance(replay, dict) else None
    query = replay.get("query") if replay is not None else None
    statement = query.get("statement") if isinstance(query, dict) else None
    parameters = query.get("bound_parameters") if isinstance(query, dict) else None
    normalized_statement = (
        " ".join(statement.lower().replace("`", "").split())
        if isinstance(statement, str)
        else ""
    )
    query_captured = (
        normalized_statement.startswith("select ")
        and " from faq " in f" {normalized_statement} "
        and " like " in f" {normalized_statement} "
        and isinstance(parameters, (list, tuple))
    )
    if expect_hit:
        keyword_check_name = "faq_sql_replay_keyword_matches_expected"
        keyword_matches = (
            isinstance(expected_keyword, str)
            and replay is not None
            and replay.get("keyword") == expected_keyword
            and query_captured
            and any(value == expected_keyword for value in parameters)
        )
    else:
        keyword_check_name = "faq_sql_replay_keyword_matches_actual_call"
        keyword_matches = (
            isinstance(expected_keyword, str)
            and isinstance(actual_keyword, str)
            and expected_keyword in actual_keyword
            and isinstance(user_message, str)
            and actual_keyword in user_message
            and replay is not None
            and replay.get("keyword") == actual_keyword
            and query_captured
            and any(value == actual_keyword for value in parameters)
        )
    limit_is_five = (
        isinstance(parameters, (list, tuple))
        and any(value == 5 and not isinstance(value, bool) for value in parameters)
    )
    row_count = replay.get("row_count") if replay is not None else None
    zero_rows = (
        isinstance(row_count, int)
        and not isinstance(row_count, bool)
        and row_count == 0
    )
    rows = replay.get("rows") if replay is not None else None
    rows_are_valid = isinstance(rows, list) and len(rows) == row_count and len(rows) <= 5
    if expect_hit:
        hit_rows = (
            [
                row
                for row in rows
                if isinstance(row, dict)
                and isinstance(row.get("question"), str)
                and isinstance(expected_keyword, str)
                and expected_keyword in row["question"]
            ]
            if isinstance(rows, list)
            else []
        )
        replay_verdict = rows_are_valid and bool(hit_rows)
        verdict_name = "faq_sql_replay_returns_expected_rows"
    else:
        replay_verdict = zero_rows and rows_are_valid and rows == []
        verdict_name = "faq_sql_replay_returns_zero_rows"

    evaluated["faq_sql_replay"] = replay
    evaluated["checks"]["faq_sql_replay_query_captured"] = query_captured
    evaluated["checks"][keyword_check_name] = keyword_matches
    evaluated["checks"]["faq_sql_replay_limit_is_five"] = limit_is_five
    evaluated["checks"][verdict_name] = replay_verdict
    evaluated["deterministic_checks_pass"] = all(
        value is True for value in evaluated["checks"].values()
    )


async def _evaluate(args: argparse.Namespace) -> int:
    settings, host_database_url = _settings_and_host_database_url()
    settings = settings.model_copy(update={"database_url": SecretStr(host_database_url)})
    database = Database(settings)
    chat_repository = ChatRepository(database)
    all_cases = _load_cases()
    cases = (
        [case for case in all_cases if case.get("case_id") == args.case_id]
        if args.case_id
        else all_cases
    )
    if args.case_id and not cases:
        raise ValueError(f"No prompt case named {args.case_id}.")
    results = []
    try:
        if not await database.check_ready():
            raise RuntimeError("The host MySQL database is not ready with the four required tables.")

        async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
            ready_response = await client.get(f"{args.base_url.rstrip('/')}/ready")
            if ready_response.status_code != 200:
                raise RuntimeError("The running app did not pass GET /ready.")

            for case in cases:
                if case.get("case_id") == "tool_failure":
                    events, conversation_id = await _run_failure_injection(case, settings, database)
                    evidence_mode = "live_model_failure_injection"
                    http_status = None
                else:
                    response = await client.post(
                        f"{args.base_url.rstrip('/')}/chat/stream",
                        json={"message": case["user_message"]},
                    )
                    events = _parse_sse(response.text) if response.status_code == 200 else []
                    conversation_event = next(
                        (data for name, data in events if name == "conversation"),
                        {},
                    )
                    conversation_id = conversation_event.get("conversation_id")
                    evidence_mode = "live_api_real_tools"
                    http_status = response.status_code
                    if response.status_code != 200:
                        results.append(
                            {
                                "case_id": case["case_id"],
                                "category": case.get("category"),
                                "evidence_mode": evidence_mode,
                                "http_status": response.status_code,
                                "error_event": response.json()
                                if "application/json" in response.headers.get("content-type", "")
                                else None,
                                "deterministic_checks_pass": False,
                            }
                        )
                        continue

                stored = (
                    await _read_completed_conversation(chat_repository, conversation_id)
                    if isinstance(conversation_id, str)
                    else None
                )
                observation_events = events
                evaluated = _evaluate_case(
                    case,
                    observation_events,
                    stored,
                    evidence_mode=evidence_mode,
                    http_status=http_status,
                )
                if evidence_mode == "live_api_real_tools":
                    stream_answer = "".join(
                        data.get("content", "")
                        for name, data in events
                        if name == "delta" and isinstance(data.get("content"), str)
                    )
                    if stored is not None:
                        stream_matches = stream_answer == stored["final_answer"]
                        evaluated["checks"]["stream_matches_persisted_answer"] = stream_matches
                        evaluated["deterministic_checks_pass"] = (
                            evaluated["deterministic_checks_pass"] and stream_matches
                        )
                if stored is not None:
                    evaluated["conversation_id"] = conversation_id
                    if case.get("case_id") in {"postage_success", "unknown_faq_no_match"}:
                        arguments = evaluated.get("actual_arguments")
                        keyword = arguments.get("keyword") if isinstance(arguments, dict) else None
                        expected_arguments = case.get("expected_arguments")
                        expected_keyword = (
                            expected_arguments.get("keyword")
                            if isinstance(expected_arguments, dict)
                            else None
                        )
                        replay = (
                            await _faq_sql_replay(database, keyword)
                            if isinstance(keyword, str)
                            else None
                        )
                        _apply_postage_sql_replay_checks(
                            evaluated,
                            replay,
                            expected_keyword,
                            expect_hit=case.get("case_id") == "postage_success",
                            actual_keyword=keyword,
                            user_message=case.get("user_message"),
                        )
                    if case.get("case_id") == "create_return_ticket":
                        persisted_tickets = await _read_persisted_tickets(
                            database,
                            conversation_id,
                        )
                        evaluated["persisted_ticket_rows"] = persisted_tickets
                        ticket_id = next(
                            (
                                item.get("ticket_id")
                                for item in stored["ticket_results"]
                                if isinstance(item, dict)
                            ),
                            None,
                        )
                        if case.get("expected_tool") is None:
                            evaluated["checks"]["no_ticket_persisted_without_tool_call"] = (
                                not persisted_tickets
                            )
                        else:
                            evaluated["checks"]["created_ticket_matches_tool_result"] = (
                                isinstance(ticket_id, str)
                                and any(row["ticket_id"] == ticket_id for row in persisted_tickets)
                            )
                        evaluated["deterministic_checks_pass"] = all(
                            value is True for value in evaluated["checks"].values()
                        )
                results.append(evaluated)
    finally:
        await database.aclose()

    for result in results:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    passed = sum(result.get("deterministic_checks_pass") is True for result in results)
    print(json.dumps({"summary": {"cases": len(results), "deterministic_passed": passed}}))
    return 0 if passed == len(cases) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run labeled live-model prompts and inspect actual persisted tool results."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--timeout-seconds", type=float, default=210)
    parser.add_argument("--case-id", help="Run one labeled case, for example after a prompt revision.")
    args = parser.parse_args()
    try:
        return asyncio.run(_evaluate(args))
    except Exception as error:  # noqa: BLE001 - Do not print URLs, credentials, or provider logs.
        print(
            json.dumps(
                {"evaluation_error": type(error).__name__, "message": "Live evaluation could not complete."}
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
