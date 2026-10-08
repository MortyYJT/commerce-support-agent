import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from commerce_support.chat_types import StreamEvent, TurnContext
from commerce_support.config import Settings
from commerce_support.tools.registry import TOOL_NAMES
from commerce_support.tools.schemas import ToolResult

TOOL_RESULT_EVENT = "tool_result"
_TEMPORARY_MYSQL_ERRORS = {1040, 1205, 1213, 2006, 2013, 2055}


class _ToolResultTooLargeError(ValueError):
    pass


class ToolExecutor:
    def __init__(self, settings: Settings) -> None:
        self._timeout_seconds = settings.tool_timeout_seconds
        self._max_retries = settings.tool_max_retries

    async def execute(
        self,
        call: dict[str, Any],
        registry: dict[str, Any],
        ctx: TurnContext,
    ) -> AsyncIterator[StreamEvent]:
        del ctx
        name, call_id, args, call_error = _parse_call(call)
        if call_error is not None:
            for event in _failure_events(name, call_id, call_error, attempt=0):
                yield event
            return

        if name not in TOOL_NAMES or name not in registry:
            for event in _failure_events(
                name,
                call_id,
                ToolResult.error("UNKNOWN_TOOL", "The requested tool is unavailable."),
                attempt=0,
            ):
                yield event
            return

        tool = registry[name]
        if getattr(tool, "name", None) != name:
            for event in _failure_events(
                name,
                call_id,
                ToolResult.error("UNKNOWN_TOOL", "The requested tool is unavailable."),
                attempt=0,
            ):
                yield event
            return

        try:
            input_schema = tool.args_schema
            if not isinstance(input_schema, type) or not issubclass(input_schema, BaseModel):
                raise TypeError("tool schema is not a Pydantic model")
            validation_args = dict(args)
            if name == "create_ticket":
                if "tool_call_id" in validation_args:
                    raise ValueError("tool_call_id is server injected")
                validation_args["tool_call_id"] = call_id
            input_schema.model_validate(validation_args)
        except (ValidationError, TypeError, ValueError):
            for event in _failure_events(
                name,
                call_id,
                ToolResult.error(
                    "INVALID_ARGUMENTS",
                    "The tool arguments do not match the required input schema.",
                ),
                attempt=0,
            ):
                yield event
            return

        for attempt in range(1, self._max_retries + 2):
            yield StreamEvent(
                "tool_status",
                {
                    "name": name,
                    "tool_call_id": call_id,
                    "status": "running",
                    "attempt": attempt,
                },
            )
            try:
                raw_result = await asyncio.wait_for(
                    tool.ainvoke(
                        {
                            "name": name,
                            "args": args,
                            "id": call_id,
                            "type": "tool_call",
                        }
                    ),
                    timeout=self._timeout_seconds,
                )
                result = _validated_result(raw_result)
            except asyncio.CancelledError:
                raise
            except (TimeoutError, SQLAlchemyTimeoutError):
                result = ToolResult.error(
                    "TOOL_TIMEOUT",
                    "The tool did not finish before its time limit.",
                    retryable=True,
                )
            except ValidationError:
                result = ToolResult.error(
                    "INVALID_TOOL_RESULT",
                    "The tool returned data that exceeded the supported result format.",
                )
            except _ToolResultTooLargeError:
                result = ToolResult.error(
                    "TOOL_RESULT_TOO_LARGE",
                    "The tool result exceeded the supported size limit.",
                )
            except Exception as error:  # noqa: BLE001 - Convert unexpected tool failures safely.
                retryable = _is_temporary_database_error(error)
                result = ToolResult.error(
                    "TEMPORARY_TOOL_FAILURE" if retryable else "TOOL_EXECUTION_FAILED",
                    "The tool could not complete this request.",
                    retryable=retryable,
                )

            if result.status == "error" and result.retryable and attempt <= self._max_retries:
                await asyncio.sleep(0.2)
                continue

            yield StreamEvent(
                "tool_status",
                {
                    "name": name,
                    "tool_call_id": call_id,
                    "status": _public_status(result),
                    "attempt": attempt,
                },
            )
            yield StreamEvent(
                TOOL_RESULT_EVENT,
                {
                    "name": name,
                    "tool_call_id": call_id,
                    "attempt": attempt,
                    "result": result,
                },
            )
            return


def _parse_call(
    call: object,
) -> tuple[str, str | None, dict[str, Any], ToolResult | None]:
    if not isinstance(call, dict):
        return "", None, {}, ToolResult.error("INVALID_TOOL_CALL", "The tool request is invalid.")

    raw_name = call.get("name")
    name = raw_name if isinstance(raw_name, str) and len(raw_name) <= 64 else ""
    raw_id = call.get("id")
    call_id = raw_id if isinstance(raw_id, str) and 1 <= len(raw_id) <= 64 else None
    args = call.get("args")

    if set(call) - {"name", "id", "args", "type"}:
        return name, call_id, {}, ToolResult.error("INVALID_TOOL_CALL", "The tool request is invalid.")
    if call.get("type", "tool_call") != "tool_call":
        return name, call_id, {}, ToolResult.error("INVALID_TOOL_CALL", "The tool request is invalid.")
    if not name or call_id is None or not isinstance(args, dict):
        return name, call_id, {}, ToolResult.error("INVALID_TOOL_CALL", "The tool request is invalid.")
    return name, call_id, args, None


def _failure_events(
    name: str,
    call_id: str | None,
    result: ToolResult,
    *,
    attempt: int,
) -> tuple[StreamEvent, StreamEvent]:
    status = StreamEvent(
        "tool_status",
        {
            "name": name,
            "tool_call_id": call_id,
            "status": "failed",
            "attempt": attempt,
        },
    )
    internal = StreamEvent(
        TOOL_RESULT_EVENT,
        {
            "name": name,
            "tool_call_id": call_id,
            "attempt": attempt,
            "result": result,
        },
    )
    return status, internal


def _public_status(result: ToolResult) -> str:
    if result.status == "success":
        return "succeeded"
    if result.status == "not_found":
        return "not_found"
    return "failed"


def _validated_result(raw_result: Any) -> ToolResult:
    if not isinstance(raw_result, ToolResult):
        raise TypeError("registered tool must return ToolResult")
    serialized = json.dumps(
        raw_result.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(serialized.encode("utf-8")) > 4096:
        raise _ToolResultTooLargeError("tool result exceeds the 4096-byte limit")
    return raw_result


def _is_temporary_database_error(error: Exception) -> bool:
    if not isinstance(error, DBAPIError):
        return False
    if error.connection_invalidated:
        return True
    original = getattr(error, "orig", None)
    code = None
    if original is not None:
        args = getattr(original, "args", ())
        if args and isinstance(args[0], int):
            code = args[0]
    return code in _TEMPORARY_MYSQL_ERRORS
