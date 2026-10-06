from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import httpx
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from commerce_support.config import Settings
from commerce_support.errors import AppError


@dataclass(frozen=True, slots=True)
class ModelChunk:
    content: str
    finish_reason: str | None = None


class ModelGateway(Protocol):
    async def select_tools(
        self,
        messages: list[BaseMessage],
        tools: list[BaseTool],
    ) -> AIMessage: ...

    def stream_final(self, messages: list[BaseMessage]) -> AsyncIterator[ModelChunk]: ...

    def stream(self, messages: list[BaseMessage]) -> AsyncIterator[ModelChunk]: ...

    async def aclose(self) -> None: ...


class ChatOpenAIModelGateway:
    """Async Chat Completions adapter with clients owned by the application lifespan."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if settings.llm_api_key is None:
            raise AppError(
                "model_not_configured",
                "模型服务尚未配置 API 密钥。",
                status_code=503,
            )

        self._http_client = httpx.Client(timeout=settings.llm_timeout_seconds)
        self._http_async_client = httpx.AsyncClient(
            timeout=settings.llm_timeout_seconds,
            transport=transport,
        )
        self._closed = False

        # langchain-openai 1.6.7 maps its max_tokens field to
        # max_completion_tokens. DeepSeek's Chat Completions endpoint expects
        # max_tokens, so send the configured standard parameter through extra_body.
        extra_body = dict(settings.llm_extra_body_json)
        extra_body["max_tokens"] = settings.max_output_tokens

        self._model = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=str(settings.llm_base_url).rstrip("/"),
            max_tokens=None,
            max_retries=0,
            timeout=settings.llm_timeout_seconds,
            stream_usage=False,
            use_responses_api=False,
            extra_body=extra_body,
            http_client=self._http_client,
            http_async_client=self._http_async_client,
        )

    async def select_tools(
        self,
        messages: list[BaseMessage],
        tools: list[BaseTool],
    ) -> AIMessage:
        selection_model = self._model.bind_tools(tools, tool_choice="auto")
        return await selection_model.ainvoke(messages)

    def stream_final(self, messages: list[BaseMessage]) -> AsyncIterator[ModelChunk]:
        # Omitting tool definitions leaves the final request with no executable tools.
        return self._stream(messages)

    def stream(self, messages: list[BaseMessage]) -> AsyncIterator[ModelChunk]:
        """Retain the first-stage stream interface for existing callers."""
        return self.stream_final(messages)

    async def _stream(self, messages: list[BaseMessage]) -> AsyncIterator[ModelChunk]:
        upstream = self._model.astream(messages)
        try:
            async for chunk in upstream:
                content = _text_content(chunk.content)
                finish_reason = chunk.response_metadata.get("finish_reason")
                if not isinstance(finish_reason, str):
                    finish_reason = None
                if not content and finish_reason is None:
                    continue
                yield ModelChunk(content=content, finish_reason=finish_reason)
        finally:
            await _close_iterator(upstream)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._http_async_client.aclose()
        finally:
            self._http_client.close()


def _text_content(content: object) -> str:
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
