from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from commerce_support.errors import AppError
from commerce_support.model import ModelChunk
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

        service = ChatService(gateway, request.app.state.settings)
        messages = service.prepare(payload)
        upstream = service.stream(messages)
        first_chunk = await _first_text_before_disconnect(request, upstream)

        return StreamingResponse(
            _event_stream(upstream, first_chunk),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


async def _first_text_chunk(upstream: AsyncIterator[ModelChunk]) -> ModelChunk:
    while True:
        try:
            chunk = await anext(upstream)
        except StopAsyncIteration:
            raise AppError(
                "empty_upstream_response",
                "模型未返回有效回复，请稍后重试。",
                status_code=502,
            ) from None

        if chunk.content:
            return chunk
        if chunk.finish_reason is not None:
            raise _finish_error(chunk.finish_reason)


async def _first_text_before_disconnect(
    request: Request,
    upstream: AsyncIterator[ModelChunk],
) -> ModelChunk:
    first_chunk_task = asyncio.create_task(_first_text_chunk(upstream))
    try:
        while True:
            if await request.is_disconnected():
                await _cancel_and_wait(first_chunk_task)
                await _close_iterator(upstream)
                raise asyncio.CancelledError

            completed, _pending = await asyncio.wait(
                {first_chunk_task},
                timeout=0.05,
            )
            if completed:
                return await first_chunk_task
    except BaseException:
        if not first_chunk_task.done():
            await _cancel_and_wait(first_chunk_task)
        await _close_iterator(upstream)
        raise


async def _event_stream(
    upstream: AsyncIterator[ModelChunk],
    first_chunk: ModelChunk,
) -> AsyncIterator[str]:
    has_text = False
    chunk = first_chunk
    try:
        while True:
            if chunk.content:
                has_text = True
                yield encode_sse("delta", {"content": chunk.content})

            if chunk.finish_reason is not None:
                if chunk.finish_reason == "stop" and has_text:
                    yield encode_sse("done", {"finish_reason": "stop"})
                else:
                    error = _finish_error(chunk.finish_reason)
                    yield encode_sse(
                        "error",
                        {"code": error.code, "message": error.public_message},
                    )
                return

            try:
                chunk = await anext(upstream)
            except StopAsyncIteration:
                yield encode_sse(
                    "error",
                    {
                        "code": "incomplete_upstream_stream",
                        "message": "模型回复意外中断，请重试。",
                    },
                )
                return
            except AppError as error:
                yield encode_sse(
                    "error",
                    {"code": error.code, "message": error.public_message},
                )
                return
    finally:
        await _close_iterator(upstream)


def _finish_error(finish_reason: str) -> AppError:
    if finish_reason == "length":
        return AppError(
            "output_truncated",
            "模型回复达到长度限制，未完成回复，请缩短问题后重试。",
            status_code=502,
        )
    if finish_reason == "stop":
        return AppError(
            "empty_upstream_response",
            "模型未返回有效回复，请稍后重试。",
            status_code=502,
        )
    return AppError(
        "upstream_finish_error",
        "模型未能完成回答，请稍后重试。",
        status_code=502,
    )


async def _close_iterator(iterator: object) -> None:
    close = getattr(iterator, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - do not leak iterator-close failures.
        return


async def _cancel_and_wait(task: asyncio.Task[ModelChunk]) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except BaseException:  # noqa: BLE001 - cancellation cleanup consumes its task result.
        return
