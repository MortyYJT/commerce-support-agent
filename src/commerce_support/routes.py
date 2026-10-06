from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from commerce_support.chat_types import StreamEvent
from commerce_support.errors import AppError
from commerce_support.schemas import ChatRequest
from commerce_support.services import ChatService
from commerce_support.sse import encode_sse


def register_chat_routes(application: FastAPI) -> None:
    @application.post("/chat/stream")
    async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
        gateway = request.app.state.gateway
        if gateway is None:
            raise AppError(
                "model_not_configured",
                "模型服务尚未配置 API 密钥。",
                status_code=503,
            )

        repository = request.app.state.repository
        if repository is None:
            raise AppError(
                "database_not_configured",
                "聊天数据库尚未配置。",
                status_code=503,
            )

        service = ChatService(
            gateway,
            request.app.state.settings,
            repository,
            request.app.state.tool_executor,
            registry_factory=request.app.state.registry_factory,
        )
        context = await service.prepare(payload)

        return StreamingResponse(
            _event_stream(service.stream(context)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


async def _event_stream(events: AsyncIterator[StreamEvent]) -> AsyncIterator[str]:
    try:
        async for event in events:
            encoded = _encode_public_event(event)
            if encoded is not None:
                yield encoded
    finally:
        await _close_iterator(events)


def _encode_public_event(event: StreamEvent) -> str | None:
    data = event.data
    if event.event == "conversation":
        conversation_id = data.get("conversation_id")
        turn_id = data.get("turn_id")
        if not isinstance(conversation_id, str) or not isinstance(turn_id, str):
            return None
        return encode_sse(
            "conversation",
            {"conversation_id": conversation_id, "turn_id": turn_id},
        )
    if event.event == "tool_status":
        name = data.get("name")
        call_id = data.get("tool_call_id")
        status = data.get("status")
        attempt = data.get("attempt")
        if (
            not isinstance(name, str)
            or not isinstance(call_id, str)
            or status not in {"running", "succeeded", "not_found", "failed"}
            or not isinstance(attempt, int)
        ):
            return None
        return encode_sse(
            "tool_status",
            {
                "name": name,
                "tool_call_id": call_id,
                "status": status,
                "attempt": attempt,
            },
        )
    if event.event == "delta":
        content = data.get("content")
        if not isinstance(content, str) or not content:
            return None
        return encode_sse("delta", {"content": content})
    if event.event == "done":
        if data.get("finish_reason") != "stop":
            return None
        return encode_sse("done", {"finish_reason": "stop"})
    if event.event == "error":
        code = data.get("code")
        message = data.get("message")
        if not isinstance(code, str) or not isinstance(message, str):
            return None
        return encode_sse("error", {"code": code, "message": message})
    return None


async def _close_iterator(iterator: object) -> None:
    close = getattr(iterator, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - do not leak stream-close failures.
        return
