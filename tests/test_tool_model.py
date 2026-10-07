from __future__ import annotations

import asyncio
import json
import random

import httpx
import pytest
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


def _empty_selection_response() -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-selection",
            "object": "chat.completion",
            "created": 1,
            "model": "wire-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
        }
    ).encode("utf-8")


def _stream_chunk(delta: dict[str, object], finish_reason: str | None = None) -> bytes:
    payload = {
        "id": "chatcmpl-final",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "wire-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return b"data: " + json.dumps(payload, separators=(",", ":")).encode() + b"\n\n"


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
    wire_tools = payload["tools"]
    assert [item["function"]["name"] for item in wire_tools] == [
        "query_order",
        "query_product",
        "query_logistics",
        "query_faq",
        "create_ticket",
    ]
    expected_public_parameters = {
        "query_order": {"order_id"},
        "query_product": {"product_id"},
        "query_logistics": {"order_id"},
        "query_faq": {"keyword"},
        "create_ticket": {"description", "ticket_type"},
    }
    wire_schemas = {item["function"]["name"]: item["function"]["parameters"] for item in wire_tools}
    assert {
        name: set(schema["properties"]) for name, schema in wire_schemas.items()
    } == expected_public_parameters
    assert {
        name: set(schema["required"]) for name, schema in wire_schemas.items()
    } == expected_public_parameters
    assert all(
        set(schema) == {"properties", "required", "type"} and schema["type"] == "object"
        for schema in wire_schemas.values()
    )
    assert wire_schemas["query_order"]["properties"]["order_id"] == {
        "minLength": 1,
        "maxLength": 64,
        "type": "string",
    }
    assert wire_schemas["query_product"]["properties"]["product_id"] == {
        "minLength": 1,
        "maxLength": 64,
        "type": "string",
    }
    assert wire_schemas["query_logistics"]["properties"]["order_id"] == {
        "minLength": 1,
        "maxLength": 64,
        "type": "string",
    }
    assert wire_schemas["query_faq"]["properties"]["keyword"] == {
        "minLength": 1,
        "maxLength": 64,
        "type": "string",
    }
    assert wire_schemas["create_ticket"]["properties"]["description"] == {
        "minLength": 1,
        "maxLength": 2000,
        "type": "string",
    }
    assert wire_schemas["create_ticket"]["properties"]["ticket_type"] == {
        "enum": ["refund", "return", "exchange", "logistics", "other"],
        "type": "string",
    }
    wire_tool_json = json.dumps(wire_tools)
    assert "conversation_id" not in wire_tool_json
    assert "turn_id" not in wire_tool_json
    assert "tool_call_id" not in wire_tool_json
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
    assert payload["vendor_option"] == "configured"
    assert [(chunk.content, chunk.finish_reason) for chunk in chunks] == [
        ("已查到", None),
        ("退货政策", None),
        ("", "stop"),
    ]


@pytest.mark.parametrize(
    ("deltas", "persisted_partial_text", "expected_chunks_read"),
    [
        pytest.param(
            [
                (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "forbidden-final",
                                "type": "function",
                                "function": {
                                    "name": "create_ticket",
                                    "arguments": '{"description":"x","ticket_type":"other"}',
                                },
                            }
                        ]
                    },
                    None,
                ),
                ({"content": "I have handled it."}, None),
                ({}, "stop"),
            ],
            "",
            1,
            id="parsed-tool-call-before-text",
        ),
        pytest.param(
            [
                ({"content": "I have handled it."}, None),
                (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": '{"description":'},
                            }
                        ]
                    },
                    None,
                ),
                ({}, "stop"),
            ],
            "I have handled it.",
            2,
            id="partial-malformed-fragment-after-text",
        ),
    ],
)
def test_final_tool_call_fragment_fails_turn_and_closes_provider_stream(
    deltas: list[tuple[dict[str, object], str | None]],
    persisted_partial_text: str,
    expected_chunks_read: int,
) -> None:
    from commerce_support.chat_types import TurnContext
    from commerce_support.model import ChatOpenAIModelGateway
    from commerce_support.schemas import ChatRequest
    from commerce_support.services import ChatService
    from commerce_support.tools.executor import ToolExecutor

    requests: list[dict[str, object]] = []
    response_streams: list[httpx.AsyncByteStream] = []

    class TrackingByteStream(httpx.AsyncByteStream):
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks
            self.chunks_read = 0
            self.exhausted = False
            self.closed = asyncio.Event()

        async def __aiter__(self):
            for chunk in self.chunks:
                self.chunks_read += 1
                yield chunk
            self.exhausted = True

        async def aclose(self) -> None:
            await asyncio.sleep(0)
            self.closed.set()

    class RecordingRepository:
        def __init__(self) -> None:
            self.finished: list[tuple[str, str]] = []

        async def begin_turn(self, message: str, conversation_id: str | None = None):
            return TurnContext(conversation_id or "conversation-wire", "turn-wire", message)

        async def successful_history(self, _conversation_id: str):
            return []

        async def append_assistant_call(self, _ctx, _message):
            raise AssertionError("No tool selection was returned")

        async def append_tool_result(self, _ctx, _call_id, _result):
            raise AssertionError("No tool result should be written")

        async def finish_turn(self, _ctx, text: str, status: str):
            self.finished.append((text, status))

    class RecordingToolExecutor(ToolExecutor):
        def __init__(self, settings: Settings) -> None:
            super().__init__(settings)
            self.calls = 0

        def execute(self, call, registry, ctx):
            self.calls += 1
            return super().execute(call, registry, ctx)

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                content=_empty_selection_response(),
                headers={"content-type": "application/json"},
            )
        if len(requests) > 2:
            raise AssertionError("The final protocol error must not trigger another request")

        body = TrackingByteStream(
            [_stream_chunk(delta, finish) for delta, finish in deltas] + [b"data: [DONE]\n\n"]
        )
        response_streams.append(body)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=body,
        )

    async def verify() -> None:
        settings = _settings()
        gateway = ChatOpenAIModelGateway(
            settings,
            transport=httpx.MockTransport(respond),
        )
        repository = RecordingRepository()
        executor = RecordingToolExecutor(settings)
        service = ChatService(
            gateway,
            settings,
            repository,
            executor,
            registry_factory=lambda _ctx: _registry(),
        )
        try:
            ctx = await service.prepare(ChatRequest(message="Please check my purchase."))
            events = [event async for event in service.stream(ctx)]
            stream_closed_before_client_shutdown = (
                bool(response_streams) and response_streams[0].closed.is_set()
            )
        finally:
            await gateway.aclose()

        names = [event.event for event in events]
        assert names[0] == "conversation"
        assert names[-1] == "error"
        assert "done" not in names
        assert events[-1].data["code"] == "invalid_tool_call"
        assert repository.finished == [(persisted_partial_text, "failed")]
        assert executor.calls == 0
        assert len(requests) == 2
        assert requests[1].get("tools") is None
        assert len(response_streams) == 1
        assert stream_closed_before_client_shutdown is True
        assert response_streams[0].chunks_read == expected_chunks_read
        assert response_streams[0].exhausted is False

    asyncio.run(verify())


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


def test_malformed_tool_feedback_is_wired_to_final_provider_request() -> None:
    from commerce_support.schemas import ChatRequest
    from commerce_support.services import ChatService
    from commerce_support.tools.executor import ToolExecutor

    raw_arguments = '{"keyword":'
    requests: list[tuple[str, dict[str, object]]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        if len(requests) == 1:
            return httpx.Response(
                200,
                content=_tool_call_response(raw_arguments),
                headers={"content-type": "application/json"},
            )

        body = (
            'data: {"id":"chatcmpl-final","object":"chat.completion.chunk",'
            '"created":1,"model":"wire-model","choices":[{"index":0,'
            '"delta":{"content":"I could not use that search request."},'
            '"finish_reason":null}]}\n\n'
            'data: {"id":"chatcmpl-final","object":"chat.completion.chunk",'
            '"created":1,"model":"wire-model","choices":[{"index":0,'
            '"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode("utf-8"),
        )

    class RecordingRepository:
        async def begin_turn(self, message: str, conversation_id: str | None = None):
            from commerce_support.chat_types import TurnContext

            return TurnContext(conversation_id or "conversation-wire", "turn-wire", message)

        async def successful_history(self, _conversation_id: str):
            return []

        async def append_assistant_call(self, _ctx, message):
            self.assistant_call = message

        async def append_tool_result(self, _ctx, call_id: str, result):
            self.tool_results.append((call_id, result))

        async def finish_turn(self, _ctx, _text: str, _status: str):
            return None

        def __init__(self) -> None:
            self.tool_results: list[tuple[str, object]] = []

    async def verify() -> None:
        from commerce_support.model import ChatOpenAIModelGateway

        settings = _settings().model_copy(update={"input_token_budget": 6144})
        gateway = ChatOpenAIModelGateway(settings, transport=httpx.MockTransport(respond))
        repository = RecordingRepository()
        service = ChatService(
            gateway,
            settings,
            repository,
            ToolExecutor(settings),
            registry_factory=lambda _ctx: _registry(),
        )
        try:
            context = await service.prepare(ChatRequest(message="What is the returns policy?"))
            events = [event async for event in service.stream(context)]
        finally:
            await gateway.aclose()

        assert [event.event for event in events][-1] == "done"
        assert len(repository.tool_results) == 1
        assert repository.tool_results[0][0] == "call-test"
        assert len(requests) == 2
        final_path, final_payload = requests[1]
        assert final_path == "/v1/chat/completions"
        assert not final_payload.get("tools")
        assert final_payload.get("tool_choice", "none") == "none"
        assistant_message = next(
            message for message in final_payload["messages"] if message["role"] == "assistant"
        )
        assert assistant_message["tool_calls"][0]["function"]["arguments"] == raw_arguments
        tool_message = next(
            message for message in final_payload["messages"] if message["role"] == "tool"
        )
        assert tool_message["tool_call_id"] == "call-test"
        assert json.loads(tool_message["content"])["code"] == "INVALID_TOOL_CALL"

    asyncio.run(verify())
