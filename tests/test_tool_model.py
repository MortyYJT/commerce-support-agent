from __future__ import annotations

import asyncio
import json
import random

import httpx
from langchain_core.messages import HumanMessage

from commerce_support.chat_types import TurnContext
from commerce_support.config import Settings
from commerce_support.model import ModelChunk
from commerce_support.tools.registry import build_registry


def _registry():
    return build_registry(
        repository=object(),
        faq=object(),
        tickets=object(),
        ctx=TurnContext(
            conversation_id="conversation-test",
            turn_id="turn-test",
            user_message="退货政策是什么？",
        ),
        rng=random.Random(7),
    )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_model="wire-model",
        llm_api_key="wire-test-key",
        max_output_tokens=37,
        llm_timeout_seconds=13,
        llm_extra_body_json={
            "thinking": {"type": "disabled"},
            "vendor_option": "configured",
        },
    )


def _tool_call_response(arguments: str) -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "wire-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-test",
                                "type": "function",
                                "function": {
                                    "name": "query_faq",
                                    "arguments": arguments,
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "total_tokens": 13,
            },
        }
    ).encode("utf-8")


def test_selection_sends_openai_tools_and_preserves_gateway_configuration() -> None:
    from commerce_support.model import ChatOpenAIModelGateway

    requests: list[tuple[str, dict[str, object]]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            content=_tool_call_response('{"keyword": "退货政策"}'),
            headers={"content-type": "application/json"},
        )

    async def verify() -> None:
        gateway = ChatOpenAIModelGateway(
            _settings(),
            transport=httpx.MockTransport(respond),
        )
        try:
            selection = await gateway.select_tools(
                [HumanMessage(content="退货政策是什么？")],
                list(_registry().values()),
            )
        finally:
            await gateway.aclose()

        assert selection.tool_calls == [
            {
                "name": "query_faq",
                "args": {"keyword": "退货政策"},
                "id": "call-test",
                "type": "tool_call",
            }
        ]

    asyncio.run(verify())

    assert len(requests) == 1
    path, payload = requests[0]
    assert path == "/v1/chat/completions"
    assert len(payload["tools"]) == 5
    assert payload["tool_choice"] == "auto"
    assert payload["max_tokens"] == 37
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["vendor_option"] == "configured"
    assert "max_completion_tokens" not in payload


def test_final_request_disables_tools_and_streams_upstream_chunks() -> None:
    from commerce_support.model import ChatOpenAIModelGateway

    requests: list[tuple[str, dict[str, object]]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        body = (
            'data: {"id":"chatcmpl-test","object":"chat.completion.chunk",'
            '"created":1,"model":"wire-model","choices":[{"index":0,'
            '"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
            'data: {"id":"chatcmpl-test","object":"chat.completion.chunk",'
            '"created":1,"model":"wire-model","choices":[{"index":0,'
            '"delta":{"content":"已查到"},"finish_reason":null}]}\n\n'
            'data: {"id":"chatcmpl-test","object":"chat.completion.chunk",'
            '"created":1,"model":"wire-model","choices":[{"index":0,'
            '"delta":{"content":"退货政策"},"finish_reason":null}]}\n\n'
            'data: {"id":"chatcmpl-test","object":"chat.completion.chunk",'
            '"created":1,"model":"wire-model","choices":[{"index":0,'
            '"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode("utf-8"),
        )

    async def verify() -> list[ModelChunk]:
        gateway = ChatOpenAIModelGateway(
            _settings(),
            transport=httpx.MockTransport(respond),
        )
        try:
            return [
                chunk
                async for chunk in gateway.stream_final(
                    [HumanMessage(content="工具结果：命中退货政策")]
                )
            ]
        finally:
            await gateway.aclose()

    chunks = asyncio.run(verify())

    assert len(requests) == 1
    path, payload = requests[0]
    assert path == "/v1/chat/completions"
    assert payload["stream"] is True
    assert not payload.get("tools")
    assert payload.get("tool_choice", "none") == "none"
    assert payload["max_tokens"] == 37
    assert payload["thinking"] == {"type": "disabled"}
    assert [(chunk.content, chunk.finish_reason) for chunk in chunks] == [
        ("已查到", None),
        ("退货政策", None),
        ("", "stop"),
    ]


def test_invalid_tool_calls_are_returned_without_losing_raw_arguments() -> None:
    from commerce_support.model import ChatOpenAIModelGateway

    raw_arguments = '{"keyword":'

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_tool_call_response(raw_arguments),
            headers={"content-type": "application/json"},
        )

    async def verify() -> None:
        gateway = ChatOpenAIModelGateway(
            _settings(),
            transport=httpx.MockTransport(respond),
        )
        try:
            selection = await gateway.select_tools(
                [HumanMessage(content="退货政策是什么？")],
                list(_registry().values()),
            )
        finally:
            await gateway.aclose()

        assert selection.tool_calls == []
        assert len(selection.invalid_tool_calls) == 1
        invalid_call = selection.invalid_tool_calls[0]
        assert {
            key: invalid_call[key] for key in ("name", "args", "id", "type")
        } == {
            "name": "query_faq",
            "args": raw_arguments,
            "id": "call-test",
            "type": "invalid_tool_call",
        }
        assert invalid_call["error"]

    asyncio.run(verify())
