from typing import Any

from pydantic import AnyHttpUrl, Field, SecretStr
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
