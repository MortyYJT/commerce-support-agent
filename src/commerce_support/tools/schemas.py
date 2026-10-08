import json
from typing import Annotated, Any, Literal

from langchain_core.messages.tool import ToolOutputMixin
from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OrderQueryArgs(StrictToolArgs):
    order_id: str = Field(min_length=1, max_length=64)


class ProductQueryArgs(StrictToolArgs):
    product_id: str = Field(min_length=1, max_length=64)


class LogisticsQueryArgs(StrictToolArgs):
    order_id: str = Field(min_length=1, max_length=64)


class FAQQueryArgs(StrictToolArgs):
    keyword: str = Field(min_length=1, max_length=64)


class CreateTicketArgs(StrictToolArgs):
    description: str = Field(min_length=1, max_length=2000)
    ticket_type: Literal["refund", "return", "exchange", "logistics", "other"]
    tool_call_id: Annotated[str, InjectedToolCallId]


class ToolResult(BaseModel, ToolOutputMixin):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["success", "not_found", "error"]
    data: dict[str, Any] | list[Any] | None = None
    code: str | None = None
    message: str | None = None
    retryable: bool = False

    @model_validator(mode="after")
    def validate_result(self) -> "ToolResult":
        if self.status != "error" and self.retryable:
            raise ValueError("only error results can be retryable")
        try:
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise ValueError("tool result data must be JSON serializable") from error
        return self

    @classmethod
    def success(cls, data: dict[str, Any] | list[Any]) -> "ToolResult":
        return cls(status="success", data=data)

    @classmethod
    def not_found(
        cls,
        data: dict[str, Any] | list[Any] | None = None,
        message: str | None = None,
    ) -> "ToolResult":
        return cls(status="not_found", data=data, message=message)

    @classmethod
    def error(
        cls,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> "ToolResult":
        return cls(status="error", code=code, message=message, retryable=retryable)
