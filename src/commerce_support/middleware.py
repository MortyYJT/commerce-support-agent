import json
from collections import deque

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        buffered_messages: list[Message] = []
        body_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                buffered_messages.append(message)
                break

            if message["type"] != "http.request":
                buffered_messages.append(message)
                continue

            body_bytes += len(message.get("body", b""))
            if body_bytes > self.max_bytes:
                await self._send_too_large(scope, send)
                return

            buffered_messages.append(message)
            if not message.get("more_body", False):
                break

        replay_queue = deque(buffered_messages)

        async def replay_receive() -> Message:
            if replay_queue:
                return replay_queue.popleft()
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _send_too_large(self, scope: Scope, send: Send) -> None:
        body = json.dumps(
            {
                "code": "request_body_too_large",
                "message": f"Request body exceeds the {self.max_bytes} byte limit",
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
