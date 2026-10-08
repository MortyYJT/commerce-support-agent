from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TurnContext:
    conversation_id: str
    turn_id: str
    user_message: str


@dataclass(frozen=True, slots=True)
class StreamEvent:
    event: str
    data: dict[str, Any]
