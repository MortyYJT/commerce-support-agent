from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from openai import APITimeoutError

from commerce_support.async_cleanup import close_async_iterator, run_shielded_cleanup
from commerce_support.chat_types import StreamEvent, TurnContext
from commerce_support.config import Settings
from commerce_support.context import build_persisted_messages
from commerce_support.database.exceptions import ConversationNotFoundError, TurnConflictError
from commerce_support.errors import AppError
from commerce_support.model import ModelGateway
from commerce_support.prompts import render_system_prompt
from commerce_support.schemas import ChatRequest
from commerce_support.tools.executor import TOOL_RESULT_EVENT, ToolExecutor
from commerce_support.tools.schemas import ToolResult

RegistryFactory = Callable[[TurnContext], dict[str, Any]]


class ChatService:
    def __init__(
        self,
        gateway: ModelGateway,
        settings: Settings,
        repository: Any,
        tool_executor: ToolExecutor,
        *,
        registry_factory: RegistryFactory | None = None,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._repository = repository
        self._tool_executor = tool_executor
        self._registry_factory = registry_factory or (lambda _ctx: {})

    async def prepare(self, request: ChatRequest) -> TurnContext:
        try:
            return await self._repository.begin_turn(request.message, request.conversation_id)
        except ConversationNotFoundError:
            raise AppError(
                "conversation_not_found",
                "找不到此对话，请开始新的聊天。",
                status_code=404,
            ) from None
        except TurnConflictError:
            raise AppError(
                "conversation_busy",
                "此对话已有一条消息正在处理中，请稍后重试。",
                status_code=409,
            ) from None
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - map database details to a safe HTTP error.
            raise AppError(
                "database_unavailable",
                "暂时无法保存这条消息，请稍后重试。",
                status_code=503,
            ) from None

    async def stream(self, ctx: TurnContext) -> AsyncIterator[StreamEvent]:
        answer_parts: list[str] = []
        completed = False
        try:
            async with asyncio.timeout(self._settings.chat_deadline_seconds):
                yield StreamEvent(
                    "conversation",
                    {"conversation_id": ctx.conversation_id, "turn_id": ctx.turn_id},
                )

                history = await self._repository.successful_history(ctx.conversation_id)
                registry = self._registry_factory(ctx)
                tools = list(registry.values())
                current_group: list[BaseMessage] = [HumanMessage(content=ctx.user_message)]
                system = render_system_prompt()
                selection_messages = build_persisted_messages(
                    system=system,
                    history_groups=history,
                    current_group=current_group,
                    tool_schemas=tools,
                    budget=self._settings.input_token_budget,
                )

                try:
                    selection = await self._gateway.select_tools(selection_messages, tools)
                except asyncio.CancelledError:
                    raise
                except AppError:
                    raise
                except Exception as error:  # noqa: BLE001 - sanitize provider errors.
                    raise _upstream_error(error) from None

                calls = _selected_calls(selection)
                if calls:
                    await self._repository.append_assistant_call(ctx, selection)
                    current_group.append(selection)
                    for call in calls:
                        if not _valid_call_id(call.get("id")):
                            raise AppError(
                                "invalid_tool_call",
                                "模型返回了无法安全处理的工具请求，请重试。",
                                status_code=502,
                            )
                        if not isinstance(call.get("name"), str) or not call["name"]:
                            raise AppError(
                                "invalid_tool_call",
                                "模型返回了无法安全处理的工具请求，请重试。",
                                status_code=502,
                            )

                    if len({call["id"] for call in calls}) != len(calls):
                        raise AppError(
                            "invalid_tool_call",
                            "模型返回了无法安全处理的工具请求，请重试。",
                            status_code=502,
                        )

                    if len(calls) > 1:
                        for call in calls:
                            result = ToolResult.error(
                                "TOO_MANY_TOOL_CALLS",
                                "Only one business tool can run for each message.",
                            )
                            async for event in self._record_result(ctx, current_group, call, result):
                                yield event
                    else:
                        call = calls[0]
                        if call.get("type") == "invalid_tool_call":
                            result = ToolResult.error(
                                "INVALID_TOOL_CALL",
                                "The tool request arguments are not valid JSON.",
                            )
                            async for event in self._record_result(ctx, current_group, call, result):
                                yield event
                        else:
                            async for event in self._execute_one(
                                ctx,
                                current_group,
                                call,
                                registry,
                            ):
                                yield event

                # The final gateway request is unbound and sends no tool schemas.
                final_messages = build_persisted_messages(
                    system=system,
                    history_groups=history,
                    current_group=current_group,
                    tool_schemas=(),
                    budget=self._settings.input_token_budget,
                )
                upstream = self._gateway.stream_final(final_messages)
                try:
                    async for chunk in upstream:
                        if chunk.content:
                            answer_parts.append(chunk.content)
                            yield StreamEvent("delta", {"content": chunk.content})

                        if chunk.finish_reason is None:
                            continue
                        if chunk.finish_reason != "stop":
                            raise _finish_error(chunk.finish_reason)
                        if not answer_parts:
                            raise _finish_error("stop")

                        await self._repository.finish_turn(
                            ctx,
                            "".join(answer_parts),
                            "completed",
                        )
                        completed = True
                        yield StreamEvent("done", {"finish_reason": "stop"})
                        return
                except asyncio.CancelledError:
                    raise
                except AppError:
                    raise
                except Exception as error:  # noqa: BLE001 - sanitize provider errors.
                    raise _upstream_error(error) from None
                finally:
                    await close_async_iterator(upstream)

                if answer_parts:
                    raise AppError(
                        "incomplete_upstream_stream",
                        "模型回复意外中断，请重试。",
                        status_code=502,
                    )
                raise AppError(
                    "empty_upstream_response",
                    "模型未返回有效回复，请稍后重试。",
                    status_code=502,
                )
        except GeneratorExit:
            if not completed:
                await run_shielded_cleanup(
                    self._finish_best_effort(ctx, "".join(answer_parts), "cancelled")
                )
            raise
        except asyncio.CancelledError:
            if not completed:
                await run_shielded_cleanup(
                    self._finish_best_effort(ctx, "".join(answer_parts), "cancelled")
                )
            raise
        except TimeoutError:
            await self._finish_best_effort(ctx, "".join(answer_parts), "failed")
            yield StreamEvent(
                "error",
                {
                    "code": "chat_deadline_exceeded",
                    "message": "聊天处理超时，请缩短问题后重试。",
                },
            )
        except AppError as error:
            await self._finish_best_effort(ctx, "".join(answer_parts), "failed")
            yield StreamEvent(
                "error",
                {"code": error.code, "message": error.public_message},
            )
        except Exception:  # noqa: BLE001 - never expose internal stream failures.
            await self._finish_best_effort(ctx, "".join(answer_parts), "failed")
            yield StreamEvent(
                "error",
                {
                    "code": "chat_unavailable",
                    "message": "聊天暂时无法完成，请稍后重试。",
                },
            )

    async def _execute_one(
        self,
        ctx: TurnContext,
        current_group: list[BaseMessage],
        call: dict[str, Any],
        registry: dict[str, Any],
    ) -> AsyncIterator[StreamEvent]:
        call_id = call["id"]
        name = call["name"]
        result_added = False
        executor_events = self._tool_executor.execute(call, registry, ctx)
        try:
            async for event in executor_events:
                if event.event == TOOL_RESULT_EVENT:
                    result = event.data.get("result")
                    if not isinstance(result, ToolResult):
                        raise AppError(
                            "tool_result_invalid",
                            "工具无法安全返回结果，请稍后重试。",
                            status_code=502,
                        )
                    async for public_event in self._record_result(
                        ctx,
                        current_group,
                        call,
                        result,
                    ):
                        yield public_event
                    result_added = True
                elif event.event == "tool_status":
                    yield _public_tool_status(event, name=name, call_id=call_id)
        finally:
            await close_async_iterator(executor_events)

        if not result_added:
            raise AppError(
                "tool_result_missing",
                "工具没有返回可用结果，请稍后重试。",
                status_code=502,
            )

    async def _record_result(
        self,
        ctx: TurnContext,
        current_group: list[BaseMessage],
        call: dict[str, Any],
        result: ToolResult,
    ) -> AsyncIterator[StreamEvent]:
        call_id = call["id"]
        name = call["name"]
        should_report_failure = (
            (result.status == "error" and call.get("type") == "invalid_tool_call")
            or result.code == "TOO_MANY_TOOL_CALLS"
        )
        if should_report_failure:
            yield StreamEvent(
                "tool_status",
                {
                    "name": name,
                    "tool_call_id": call_id,
                    "status": "failed",
                    "attempt": 0,
                },
            )

        result_data = result.model_dump(mode="json")
        await self._repository.append_tool_result(ctx, call_id, result_data)
        current_group.append(
            ToolMessage(
                content=json.dumps(result_data, ensure_ascii=False, separators=(",", ":")),
                tool_call_id=call_id,
                name=name,
            )
        )

    async def _finish_best_effort(self, ctx: TurnContext, text: str, status: str) -> None:
        try:
            await self._repository.finish_turn(ctx, text, status)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - failure cleanup must not leak or mask errors.
            return


def _selected_calls(message: AIMessage) -> list[dict[str, Any]]:
    calls = [dict(call) for call in message.tool_calls or []]
    calls.extend(dict(call) for call in message.invalid_tool_calls or [])
    return calls


def _valid_call_id(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 64


def _public_tool_status(event: StreamEvent, *, name: str, call_id: str) -> StreamEvent:
    data = event.data
    status = data.get("status")
    if status not in {"running", "succeeded", "not_found", "failed"}:
        status = "failed"
    attempt = data.get("attempt")
    if not isinstance(attempt, int):
        attempt = 0
    return StreamEvent(
        "tool_status",
        {
            "name": name,
            "tool_call_id": call_id,
            "status": status,
            "attempt": attempt,
        },
    )


def _upstream_error(error: Exception) -> AppError:
    if isinstance(error, (APITimeoutError, httpx.TimeoutException, TimeoutError)):
        return AppError(
            "upstream_timeout",
            "模型服务响应超时，请稍后重试。",
            status_code=504,
        )
    return AppError(
        "upstream_unavailable",
        "模型服务暂时不可用，请稍后重试。",
        status_code=502,
    )


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
