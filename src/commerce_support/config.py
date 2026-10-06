from typing import Any
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_base_url: AnyHttpUrl = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_api_key: SecretStr | None = Field(default=None, repr=False)
    input_token_budget: int = Field(default=4096, gt=0)
    max_output_tokens: int = Field(default=1024, gt=0)
    llm_timeout_seconds: int = Field(default=60, gt=0)
    llm_extra_body_json: dict[str, Any] = Field(default_factory=dict)
    max_request_body_bytes: int = Field(default=65_536, gt=0)
    database_url: SecretStr | None = Field(default=None, repr=False)
    tool_timeout_seconds: float = Field(default=5, gt=0, le=30)
    tool_max_retries: int = Field(default=1, ge=0, le=1)
    chat_deadline_seconds: int = Field(default=150, gt=0)
    turn_lease_seconds: int = Field(default=180, gt=0)

    @field_validator("database_url")
    @classmethod
    def require_asyncmy_driver(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and urlsplit(value.get_secret_value()).scheme != "mysql+asyncmy":
            raise ValueError("DATABASE_URL must use the mysql+asyncmy driver")
        return value

    @model_validator(mode="after")
    def validate_turn_lease(self) -> "Settings":
        if self.turn_lease_seconds < self.chat_deadline_seconds + 30:
            raise ValueError(
                "TURN_LEASE_SECONDS must be at least CHAT_DEADLINE_SECONDS plus 30 seconds"
            )
        return self
