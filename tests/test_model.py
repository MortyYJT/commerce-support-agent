from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json

import httpx
from langchain_core.messages import HumanMessage

from commerce_support.config import Settings


def test_gateway_streams_over_chat_completions_wire_protocol():
    module_spec = importlib.util.find_spec("commerce_support.model")
    assert module_spec is not None, "the chat model gateway module must exist"
    model_module = importlib.import_module("commerce_support.model")
    gateway_type = getattr(model_module, "ChatOpenAIModelGateway", None)
    assert gateway_type is not None, "the ChatOpenAI gateway must be available"

    captured: dict[str, object] = {}

    async def respond(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content)
        body = (
            'data: {"id":"chatcmpl-test","object":"chat.completion.chunk",'
            '"created":1,"model":"wire-model","choices":[{"index":0,'
            '"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
            'data: {"id":"chatcmpl-test","object":"chat.completion.chunk",'
            '"created":1,"model":"wire-model","choices":[{"index":0,'
            '"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
            'data: {"id":"chatcmpl-test","object":"chat.completion.chunk",'
            '"created":1,"model":"wire-model","choices":[{"index":0,'
            '"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/event-stream"},
            content=body.encode("utf-8"),
        )

    async def verify_wire_request() -> list[object]:
        gateway = gateway_type(
            Settings(
                _env_file=None,
                llm_base_url="https://model.invalid/v1",
                llm_model="wire-model",
                llm_api_key="wire-test-key",
                max_output_tokens=37,
                llm_timeout_seconds=13,
                llm_extra_body_json={"vendor_option": "configured"},
            ),
            transport=httpx.MockTransport(respond),
        )
        try:
            return [chunk async for chunk in gateway.stream([HumanMessage(content="hello")])]
        finally:
            await gateway.aclose()

    chunks = asyncio.run(verify_wire_request())

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/chat/completions"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "wire-model"
    assert payload["stream"] is True
    assert payload["max_tokens"] == 37
    assert payload["vendor_option"] == "configured"
    assert "response_format" not in payload
    assert [(chunk.content, chunk.finish_reason) for chunk in chunks] == [
        ("hello", None),
        ("", "stop"),
    ]
