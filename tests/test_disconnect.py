from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
from langchain_core.messages import AIMessage

from commerce_support.app import create_app
from commerce_support.chat_types import TurnContext
from commerce_support.config import Settings
from commerce_support.model import ChatOpenAIModelGateway
from commerce_support.tools.executor import ToolExecutor
from commerce_support.tools.registry import build_registry
from commerce_support.tools.schemas import FAQQueryArgs


def test_disconnect_closes_upstream_over_a_real_http_connection(local_http_server):
    async def verify_disconnect() -> None:
        upstream_closed = asyncio.Event()

        class BlockingGateway:
            async def select_tools(self, _messages, _tools):
                return AIMessage(content="")

            async def _stream(self, _messages):
                try:
                    yield SimpleNamespace(content="first answer", finish_reason=None)
                    await asyncio.Event().wait()
                finally:
                    upstream_closed.set()

            def stream_final(self, messages):
                return self._stream(messages)

            async def aclose(self):
                return None

        app = create_app(
            settings=Settings(_env_file=None, llm_api_key=None),
            gateway=BlockingGateway(),
            repository=SocketRepository(),
        )

        async with (
            local_http_server(app) as base_url,
            httpx.AsyncClient(timeout=5, trust_env=False) as client,
            client.stream(
                "POST",
                f"{base_url}/chat/stream",
                json={"message": "Help with my order"},
            ) as response,
        ):
            assert response.status_code == 200
            lines = response.aiter_lines()
            event_line, _conversation = await _read_event(lines)
            delta_event = await asyncio.wait_for(lines.__anext__(), timeout=2)
            data_line = await asyncio.wait_for(lines.__anext__(), timeout=2)
            await asyncio.wait_for(lines.__anext__(), timeout=2)
            assert event_line == "event: conversation"
            assert delta_event == "event: delta"
            assert data_line == 'data: {"content":"first answer"}'
            await response.aclose()

            await asyncio.wait_for(upstream_closed.wait(), timeout=3)

    asyncio.run(verify_disconnect())


def test_disconnect_before_first_delta_closes_blocked_upstream(local_http_server):
    async def verify_early_disconnect() -> None:
        upstream_started = asyncio.Event()
        upstream_closed = asyncio.Event()

        class BlockingGateway:
            async def select_tools(self, _messages, _tools):
                return AIMessage(content="")

            async def _stream(self, _messages):
                try:
                    upstream_started.set()
                    await asyncio.Event().wait()
                    yield SimpleNamespace(content="unreachable", finish_reason="stop")
                finally:
                    upstream_closed.set()

            def stream_final(self, messages):
                return self._stream(messages)

            async def aclose(self):
                return None

        app = create_app(
            settings=Settings(_env_file=None, llm_api_key=None),
            gateway=BlockingGateway(),
            repository=SocketRepository(),
        )

        async with (
            local_http_server(app) as base_url,
            httpx.AsyncClient(timeout=None, trust_env=False) as client,
        ):
            request = client.build_request(
                "POST",
                f"{base_url}/chat/stream",
                json={"message": "Help with my order"},
            )
            pending_response = await client.send(request, stream=True)
            assert pending_response.status_code == 200
            lines = pending_response.aiter_lines()
            event_line = await asyncio.wait_for(lines.__anext__(), timeout=2)
            assert event_line == "event: conversation"
            await asyncio.wait_for(upstream_started.wait(), timeout=2)
            await pending_response.aclose()

            await asyncio.wait_for(upstream_closed.wait(), timeout=1)

    asyncio.run(verify_early_disconnect())


class SocketRepository:
    def __init__(self) -> None:
        self.finished: list[tuple[str, str]] = []
        self.tool_calls: list[AIMessage] = []
        self.tool_results: list[tuple[str, Any]] = []

    async def begin_turn(self, message: str, conversation_id: str | None = None) -> TurnContext:
        return TurnContext(conversation_id or "socket-conversation", "socket-turn", message)

    async def successful_history(self, _conversation_id: str) -> list[list[Any]]:
        return []

    async def append_assistant_call(self, _ctx: TurnContext, message: AIMessage) -> None:
        self.tool_calls.append(message)

    async def append_tool_result(self, _ctx: TurnContext, call_id: str, result: Any) -> None:
        self.tool_results.append((call_id, result))

    async def finish_turn(self, _ctx: TurnContext, text: str, status: str) -> None:
        await asyncio.sleep(0)
        self.finished.append((text, status))


class SocketTool:
    name = "query_faq"
    description = "Search FAQs."
    args_schema = FAQQueryArgs


async def _read_event(lines) -> tuple[str, str]:
    event_line = await asyncio.wait_for(lines.__anext__(), timeout=2)
    data_line = await asyncio.wait_for(lines.__anext__(), timeout=2)
    await asyncio.wait_for(lines.__anext__(), timeout=2)
    return event_line, data_line


def _gateway_for_upstream_stream(stream):
    class OpenAIStyleStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            chunk = await stream.__anext__()
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=getattr(chunk, "content", None),
                            tool_calls=None,
                            function_call=None,
                        ),
                        finish_reason=getattr(chunk, "finish_reason", None),
                    )
                ]
            )

        async def close(self):
            await stream.aclose()

    class StubbedModelGateway(ChatOpenAIModelGateway):
        async def select_tools(self, _messages, _tools):
            return AIMessage(content="")

        async def aclose(self):
            return None

    gateway = object.__new__(StubbedModelGateway)

    async def create_stream(**_payload):
        return OpenAIStyleStream()

    gateway._model = SimpleNamespace(
        _get_request_payload=lambda _messages: {"messages": []},
        async_client=SimpleNamespace(create=create_stream),
    )
    return gateway


def test_real_disconnect_during_tool_selection_cancels_provider_and_turn(local_http_server):
    async def verify_disconnect() -> None:
        selection_started = asyncio.Event()
        selection_cancelled = asyncio.Event()
        repository = SocketRepository()

        class BlockingSelectionGateway:
            async def select_tools(self, _messages, _tools):
                selection_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    selection_cancelled.set()
                    raise

            async def stream_final(self, _messages):
                yield SimpleNamespace(content="unused", finish_reason="stop")

        settings = Settings(_env_file=None, llm_api_key=None)
        app = create_app(
            settings=settings,
            gateway=BlockingSelectionGateway(),
            repository=repository,
        )

        async with (
            local_http_server(app) as base_url,
            httpx.AsyncClient(timeout=5, trust_env=False) as client,
            client.stream(
                "POST",
                f"{base_url}/chat/stream",
                json={"message": "Please check my return"},
            ) as response,
        ):
            assert response.status_code == 200
            lines = response.aiter_lines()
            event_line, data_line = await _read_event(lines)
            assert event_line == "event: conversation"
            assert '"conversation_id":"socket-conversation"' in data_line
            await asyncio.wait_for(selection_started.wait(), timeout=2)
            await response.aclose()

        await asyncio.wait_for(selection_cancelled.wait(), timeout=2)
        assert repository.finished == [("", "cancelled")]

    asyncio.run(verify_disconnect())


def test_real_disconnect_during_tool_retry_cancels_executor(local_http_server):
    async def verify_disconnect() -> None:
        second_attempt_started = asyncio.Event()
        second_attempt_cancelled = asyncio.Event()
        repository = SocketRepository()

        class RetryFAQRepository:
            def __init__(self) -> None:
                self.calls = 0

            async def search_literal(self, _keyword: str, limit: int = 5) -> list[dict[str, str]]:
                del limit
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("retry this lookup")
                second_attempt_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    second_attempt_cancelled.set()
                    raise
                return []

        faq_repository = RetryFAQRepository()
        registry_factory = lambda ctx: build_registry(
            repository=repository,
            faq=faq_repository,
            tickets=object(),
            ctx=ctx,
            rng=__import__("random").Random(11),
        )

        class ToolSelectionGateway:
            async def select_tools(self, _messages, _tools):
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "query_faq",
                            "args": {"keyword": "return"},
                            "id": "retry-call",
                            "type": "tool_call",
                        }
                    ],
                )

            async def stream_final(self, _messages):
                yield SimpleNamespace(content="unexpected", finish_reason="stop")

        settings = Settings(_env_file=None, llm_api_key=None, tool_timeout_seconds=5)
        app = create_app(
            settings=settings,
            gateway=ToolSelectionGateway(),
            repository=repository,
            registry_factory=registry_factory,
            tool_executor=ToolExecutor(settings),
        )

        async with (
            local_http_server(app) as base_url,
            httpx.AsyncClient(timeout=5, trust_env=False) as client,
            client.stream(
                "POST",
                f"{base_url}/chat/stream",
                json={"message": "Please check return policy"},
            ) as response,
        ):
            assert response.status_code == 200
            lines = response.aiter_lines()
            assert (await _read_event(lines))[0] == "event: conversation"
            while True:
                event_line, data_line = await _read_event(lines)
                if event_line == "event: tool_status" and '"attempt":2' in data_line:
                    break
            await asyncio.wait_for(second_attempt_started.wait(), timeout=2)
            await response.aclose()

        await asyncio.wait_for(second_attempt_cancelled.wait(), timeout=2)
        assert faq_repository.calls == 2
        assert repository.finished == [("", "cancelled")]

    asyncio.run(verify_disconnect())


def test_real_disconnect_before_final_first_token_cancels_provider(local_http_server):
    async def verify_disconnect() -> None:
        final_started = asyncio.Event()
        final_cancelled = asyncio.Event()
        repository = SocketRepository()

        class BlockingFinalStream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                final_started.set()
                await asyncio.Event().wait()
                raise StopAsyncIteration

            async def aclose(self):
                await asyncio.sleep(0)
                final_cancelled.set()

        app = create_app(
            settings=Settings(_env_file=None, llm_api_key=None),
            gateway=_gateway_for_upstream_stream(BlockingFinalStream()),
            repository=repository,
        )
        async with (
            local_http_server(app) as base_url,
            httpx.AsyncClient(timeout=5, trust_env=False) as client,
            client.stream(
                "POST",
                f"{base_url}/chat/stream",
                json={"message": "hello"},
            ) as response,
        ):
            assert response.status_code == 200
            lines = response.aiter_lines()
            assert (await _read_event(lines))[0] == "event: conversation"
            await asyncio.wait_for(final_started.wait(), timeout=2)
            await response.aclose()

        await asyncio.wait_for(final_cancelled.wait(), timeout=2)
        assert repository.finished == [("", "cancelled")]

    asyncio.run(verify_disconnect())


def test_real_disconnect_after_delta_persists_partial_as_cancelled(local_http_server):
    async def verify_disconnect() -> None:
        final_cancelled = asyncio.Event()
        repository = SocketRepository()

        class PartialFinalStream:
            def __init__(self) -> None:
                self.sent_partial = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.sent_partial:
                    self.sent_partial = True
                    return SimpleNamespace(
                        content="partial answer", response_metadata={}
                    )
                await asyncio.Event().wait()
                raise StopAsyncIteration

            async def aclose(self):
                await asyncio.sleep(0)
                final_cancelled.set()

        app = create_app(
            settings=Settings(_env_file=None, llm_api_key=None),
            gateway=_gateway_for_upstream_stream(PartialFinalStream()),
            repository=repository,
        )
        async with (
            local_http_server(app) as base_url,
            httpx.AsyncClient(timeout=5, trust_env=False) as client,
            client.stream(
                "POST",
                f"{base_url}/chat/stream",
                json={"message": "hello"},
            ) as response,
        ):
            assert response.status_code == 200
            lines = response.aiter_lines()
            assert (await _read_event(lines))[0] == "event: conversation"
            event_line, data_line = await _read_event(lines)
            assert event_line == "event: delta"
            assert data_line == 'data: {"content":"partial answer"}'
            await response.aclose()

        await asyncio.wait_for(final_cancelled.wait(), timeout=2)
        assert repository.finished == [("partial answer", "cancelled")]

    asyncio.run(verify_disconnect())


def test_real_disconnect_after_model_delta_closes_openai_response(local_http_server):
    async def verify_disconnect() -> None:
        repository = SocketRepository()
        requests: list[dict[str, Any]] = []

        class BlockingByteStream(httpx.AsyncByteStream):
            def __init__(self) -> None:
                self.closed = asyncio.Event()
                self.exhausted = False

            async def __aiter__(self):
                yield (
                    b'data: {"id":"chatcmpl-final","object":"chat.completion.chunk",'
                    b'"created":1,"model":"wire-model","choices":[{"index":0,'
                    b'"delta":{"content":"partial answer"},"finish_reason":null}]}\n\n'
                )
                await asyncio.Event().wait()
                self.exhausted = True

            async def aclose(self) -> None:
                await asyncio.sleep(0)
                self.closed.set()

        body = BlockingByteStream()

        async def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            if len(requests) == 1:
                return httpx.Response(
                    200,
                    json={
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
                    },
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=body,
            )

        settings = Settings(
            _env_file=None,
            llm_base_url="https://model.invalid/v1",
            llm_model="wire-model",
            llm_api_key="wire-test-key",
        )
        gateway = ChatOpenAIModelGateway(
            settings,
            transport=httpx.MockTransport(respond),
        )
        app = create_app(
            settings=settings,
            gateway=gateway,
            repository=repository,
        )

        async with (
            local_http_server(app) as base_url,
            httpx.AsyncClient(timeout=5, trust_env=False) as client,
            client.stream(
                "POST",
                f"{base_url}/chat/stream",
                json={"message": "hello"},
            ) as response,
        ):
            assert response.status_code == 200
            lines = response.aiter_lines()
            assert (await _read_event(lines))[0] == "event: conversation"
            event_line, data_line = await _read_event(lines)
            assert event_line == "event: delta"
            assert data_line == 'data: {"content":"partial answer"}'
            await response.aclose()

        try:
            await asyncio.wait_for(body.closed.wait(), timeout=2)
        finally:
            await gateway.aclose()

        assert body.exhausted is False
        assert repository.finished == [("partial answer", "cancelled")]
        assert len(requests) == 2

    asyncio.run(verify_disconnect())
