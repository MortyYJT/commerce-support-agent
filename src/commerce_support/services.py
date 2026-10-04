from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
from langchain_core.messages import BaseMessage
from openai import APITimeoutError

from commerce_support.config import Settings
from commerce_support.context import build_chat_messages
from commerce_support.errors import AppError
from commerce_support.model import ModelChunk, ModelGateway
from commerce_support.prompts import render_system_prompt
from commerce_support.schemas import ChatRequest


class ChatService:
    def __init__(self, gateway: ModelGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def prepare(self, request: ChatRequest) -> list[BaseMessage]:
        return build_chat_messages(
            system=render_system_prompt(),
            history=request.history,
            message=request.message,
            budget=self._settings.input_token_budget,
        )

    async def stream(self, messages: list[BaseMessage]) -> AsyncIterator[ModelChunk]:
        upstream = self._gateway.stream(messages)
        try:
            async for chunk in upstream:
                yield chunk
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - sanitize untrusted provider exceptions.
            if isinstance(error, (APITimeoutError, httpx.TimeoutException, TimeoutError)):
                raise AppError(
                    "upstream_timeout",
                    "模型服务响应超时，请稍后重试。",
                    status_code=504,
                ) from None
            raise AppError(
                "upstream_unavailable",
                "模型服务暂时不可用，请稍后重试。",
                status_code=502,
            ) from None
        finally:
            await _close_iterator(upstream)


async def _close_iterator(iterator: object) -> None:
    close = getattr(iterator, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - do not leak client-close failures.
        return
