from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from commerce_support.config import Settings
from commerce_support.model import ChatOpenAIModelGateway
from commerce_support.prompts import render_system_prompt

RAW_ARGUMENTS = '{"keyword":'
TOOL_CALL_ID = "live-wire-probe-001"
TOOL_NAME = "query_faq"
TOOL_ERROR = {
    "status": "error",
    "data": None,
    "code": "INVALID_TOOL_CALL",
    "message": "The tool request arguments are not valid JSON.",
    "retryable": False,
}


class RecordingAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self.requests: list[dict[str, Any]] = []
        self.response_statuses: list[int] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(await request.aread())
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "payload": payload,
            }
        )
        response = await self._inner.handle_async_request(request)
        self.response_statuses.append(response.status_code)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def _wire_checks(request: dict[str, Any] | None, response_status: int | None) -> dict[str, Any]:
    if request is None:
        return {
            "one_real_final_request": False,
            "assistant_raw_arguments_preserved": False,
            "matching_tool_error_message_present": False,
            "no_executable_tools_sent": False,
            "provider_accepted_request": False,
        }

    payload = request["payload"]
    messages = payload.get("messages", [])
    assistant = next(
        (
            message
            for message in messages
            if message.get("role") == "assistant" and message.get("tool_calls")
        ),
        None,
    )
    tool_message = next(
        (
            message
            for message in messages
            if message.get("role") == "tool" and message.get("tool_call_id") == TOOL_CALL_ID
        ),
        None,
    )
    raw_argument_matches = False
    if assistant is not None:
        raw_argument_matches = any(
            call.get("id") == TOOL_CALL_ID
            and call.get("function", {}).get("name") == TOOL_NAME
            and call.get("function", {}).get("arguments") == RAW_ARGUMENTS
            for call in assistant["tool_calls"]
        )
    matching_error = False
    if tool_message is not None:
        try:
            matching_error = json.loads(tool_message.get("content", "")) == TOOL_ERROR
        except json.JSONDecodeError:
            matching_error = False

    return {
        "one_real_final_request": request["method"] == "POST"
        and request["path"].endswith("/chat/completions"),
        "assistant_raw_arguments_preserved": raw_argument_matches,
        "matching_tool_error_message_present": matching_error,
        "no_executable_tools_sent": not payload.get("tools"),
        "provider_accepted_request": response_status == 200,
        "streaming_requested": payload.get("stream") is True,
    }


async def _run() -> int:
    settings = Settings()
    if settings.llm_api_key is None:
        raise ValueError("LLM_API_KEY is not configured.")

    transport = RecordingAsyncTransport()
    gateway = ChatOpenAIModelGateway(settings, transport=transport)
    messages = [
        SystemMessage(content=render_system_prompt()),
        HumanMessage(content="退货政策是什么？"),
        AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "name": TOOL_NAME,
                    "args": RAW_ARGUMENTS,
                    "id": TOOL_CALL_ID,
                    "error": "Malformed arguments",
                    "type": "invalid_tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(TOOL_ERROR, ensure_ascii=False, separators=(",", ":")),
            tool_call_id=TOOL_CALL_ID,
            name=TOOL_NAME,
        ),
    ]
    chunks = []
    try:
        chunks = [chunk async for chunk in gateway.stream_final(messages)]
    finally:
        await gateway.aclose()

    request = transport.requests[0] if len(transport.requests) == 1 else None
    response_status = transport.response_statuses[0] if len(transport.response_statuses) == 1 else None
    checks = _wire_checks(request, response_status)
    checks["provider_completed_stream"] = any(
        chunk.finish_reason == "stop" for chunk in chunks
    )
    result = {
        "probe": "live malformed-tool feedback final request",
        "evidence_mode": "real_provider_http_request",
        "checks": checks,
        "passed": all(checks.values()),
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["passed"] else 1


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as error:  # noqa: BLE001 - Never print credentials or provider diagnostics.
        print(
            json.dumps(
                {
                    "probe": "live malformed-tool feedback final request",
                    "evidence_mode": "real_provider_http_request",
                    "error_type": type(error).__name__,
                    "passed": False,
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
