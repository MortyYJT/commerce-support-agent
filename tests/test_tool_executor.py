import asyncio
from typing import Any

import pytest

from commerce_support.chat_types import TurnContext
from commerce_support.config import Settings


def _context() -> TurnContext:
    return TurnContext(
        conversation_id="conversation-1",
        turn_id="turn-1",
        user_message="Please check order 1001.",
    )


def _call(args: dict[str, Any], call_id: str = "call-1") -> dict[str, Any]:
    return {"name": "query_order", "args": args, "id": call_id, "type": "tool_call"}


def _make_order_tool(handler: Any) -> Any:
    from langchain_core.tools import tool

    from commerce_support.tools.schemas import OrderQueryArgs, ToolResult

    @tool(args_schema=OrderQueryArgs, description="Return a controlled order result.")
    async def query_order(order_id: str) -> ToolResult:
        return await handler(order_id)

    return query_order


def _executor(*, timeout: float = 0.2, retries: int = 1) -> Any:
    from commerce_support.tools.executor import ToolExecutor

    settings = Settings(
        _env_file=None,
        llm_api_key=None,
        tool_timeout_seconds=timeout,
        tool_max_retries=retries,
    )
    return ToolExecutor(settings)


async def _collect(executor: Any, call: dict[str, Any], tool: Any) -> list[Any]:
    return [
        event
        async for event in executor.execute(call, {"query_order": tool}, _context())
    ]


def test_timeout_retries_once_after_the_same_tool_was_selected() -> None:
    async def run() -> None:
        from commerce_support.tools.schemas import ToolResult

        attempts = 0

        async def handler(order_id: str) -> ToolResult:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                await asyncio.sleep(1)
            return ToolResult.success(data={"demo": True, "order_id": order_id})

        events = await _collect(
            _executor(timeout=0.02, retries=1),
            _call({"order_id": "ORDER-1001"}),
            _make_order_tool(handler),
        )

        running_attempts = [
            event.data["attempt"]
            for event in events
            if event.event == "tool_status" and event.data["status"] == "running"
        ]
        assert attempts == 2
        assert running_attempts == [1, 2]
        assert events[-1].event == "tool_result"
        assert events[-1].data["result"].status == "success"

    asyncio.run(run())


def test_validation_error_never_calls_tool_or_retries() -> None:
    async def run() -> None:
        from commerce_support.tools.schemas import ToolResult

        attempts = 0

        async def handler(order_id: str) -> ToolResult:
            nonlocal attempts
            attempts += 1
            return ToolResult.success(data={"order_id": order_id})

        events = await _collect(
            _executor(timeout=0.02, retries=1),
            _call({"order_id": "ORDER-1001", "unexpected": "value"}),
            _make_order_tool(handler),
        )

        assert attempts == 0
        assert events[-1].event == "tool_result"
        assert events[-1].data["result"].status == "error"
        assert events[-1].data["result"].code == "INVALID_ARGUMENTS"
        assert events[-1].data["attempt"] == 0

    asyncio.run(run())


def test_oversized_utf8_result_returns_explicit_error_without_retry() -> None:
    async def run() -> None:
        from commerce_support.tools.schemas import ToolResult

        attempts = 0

        async def handler(order_id: str) -> ToolResult:
            nonlocal attempts
            attempts += 1
            return ToolResult.success(data={"details": "鱼" * 1500})

        events = await _collect(
            _executor(timeout=0.2, retries=1),
            _call({"order_id": "ORDER-1001"}),
            _make_order_tool(handler),
        )

        assert attempts == 1
        assert events[-1].event == "tool_result"
        assert events[-1].data["result"].status == "error"
        assert events[-1].data["result"].code == "TOOL_RESULT_TOO_LARGE"

    asyncio.run(run())


def test_cancel_never_retries_and_propagates_to_running_tool() -> None:
    async def run() -> None:
        from commerce_support.tools.schemas import ToolResult

        attempts = 0
        started = asyncio.Event()
        was_cancelled = asyncio.Event()

        async def handler(order_id: str) -> ToolResult:
            nonlocal attempts
            attempts += 1
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                was_cancelled.set()
                raise

        executor = _executor(timeout=1, retries=1)
        consumer = asyncio.create_task(
            _collect(executor, _call({"order_id": "ORDER-1001"}), _make_order_tool(handler))
        )
        await asyncio.wait_for(started.wait(), timeout=0.5)
        consumer.cancel()

        with pytest.raises(asyncio.CancelledError):
            await consumer

        assert attempts == 1
        assert was_cancelled.is_set()

    asyncio.run(run())


def test_status_events_do_not_expose_raw_tool_result() -> None:
    async def run() -> None:
        from commerce_support.tools.schemas import ToolResult

        async def handler(order_id: str) -> ToolResult:
            return ToolResult.success(data={"private_detail": "only for model context"})

        events = await _collect(
            _executor(),
            _call({"order_id": "ORDER-1001"}),
            _make_order_tool(handler),
        )
        statuses = [event for event in events if event.event == "tool_status"]

        assert statuses
        assert all(
            set(event.data) == {"name", "tool_call_id", "status", "attempt"}
            for event in statuses
        )
        assert all("private_detail" not in repr(event.data) for event in statuses)
        assert events[-1].event == "tool_result"
        assert events[-1].data["result"].data == {
            "private_detail": "only for model context"
        }

    asyncio.run(run())


def test_unknown_tool_is_rejected_without_execution() -> None:
    async def run() -> None:

        executor = _executor()
        events = [
            event
            async for event in executor.execute(
                {"name": "untrusted_function", "args": {}, "id": "call-1"},
                {},
                _context(),
            )
        ]

        assert events[-1].event == "tool_result"
        assert events[-1].data["result"].code == "UNKNOWN_TOOL"

    asyncio.run(run())
