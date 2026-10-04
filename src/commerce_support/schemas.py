from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

MAX_TEXT_LENGTH = 8192
MAX_HISTORY_MESSAGES = 40


def _reject_whitespace_only(value: str) -> str:
    if not value.strip():
        raise ValueError("must contain at least one non-whitespace character")
    return value


BoundedText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_TEXT_LENGTH),
    AfterValidator(_reject_whitespace_only),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HistoryMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: BoundedText


class ChatRequest(StrictModel):
    message: BoundedText
    history: list[HistoryMessage] = Field(
        default_factory=list,
        max_length=MAX_HISTORY_MESSAGES,
    )

    @model_validator(mode="after")
    def validate_complete_history_turns(self) -> Self:
        if len(self.history) % 2:
            raise ValueError("history must contain complete user/assistant pairs")

        for index in range(0, len(self.history), 2):
            user_message = self.history[index]
            assistant_message = self.history[index + 1]
            if user_message.role != "user" or assistant_message.role != "assistant":
                raise ValueError("history must alternate user then assistant")

        return self


class ExtractRequest(StrictModel):
    description: BoundedText


class AfterSales(StrictModel):
    order_id: str | None
    request_type: Literal["退款", "退货", "换货", "维修", "物流问题", "其他", "未知"]
    expected_solution: str | None


class HealthResponse(StrictModel):
    status: Literal["ok"]


class ErrorResponse(StrictModel):
    code: str
    message: str
