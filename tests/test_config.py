from __future__ import annotations

import pytest
from pydantic_settings import SettingsError

from commerce_support.config import Settings
from commerce_support.errors import AppError

SETTING_ENV_NAMES = (
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "INPUT_TOKEN_BUDGET",
    "MAX_OUTPUT_TOKENS",
    "LLM_TIMEOUT_SECONDS",
    "LLM_EXTRA_BODY_JSON",
    "MAX_REQUEST_BODY_BYTES",
)


def clear_settings_environment(monkeypatch):
    for name in SETTING_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_env_overrides_dotenv(tmp_path, monkeypatch):
    clear_settings_environment(monkeypatch)

    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "LLM_BASE_URL=https://dotenv.example/v1\n"
        "LLM_MODEL=dotenv-model\n"
        "LLM_API_KEY=dotenv-secret\n"
        "INPUT_TOKEN_BUDGET=2048\n"
        "MAX_OUTPUT_TOKENS=512\n"
        "LLM_TIMEOUT_SECONDS=90\n"
        'LLM_EXTRA_BODY_JSON={"temperature":0.1}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_BASE_URL", "https://environment.example/v1")
    monkeypatch.setenv("LLM_MODEL", "environment-model")
    monkeypatch.setenv("LLM_API_KEY", "environment-secret-value")
    monkeypatch.setenv("LLM_EXTRA_BODY_JSON", '{"temperature":0.2,"stream":false}')

    settings = Settings(_env_file=dotenv_path)

    assert str(settings.llm_base_url) == "https://environment.example/v1"
    assert settings.llm_model == "environment-model"
    assert settings.llm_api_key.get_secret_value() == "environment-secret-value"
    assert settings.input_token_budget == 2048
    assert settings.max_output_tokens == 512
    assert settings.llm_timeout_seconds == 90
    assert settings.llm_extra_body_json == {"temperature": 0.2, "stream": False}
    assert "environment-secret-value" not in repr(settings)


def test_settings_defaults_use_deepseek_and_bounded_budgets(monkeypatch):
    clear_settings_environment(monkeypatch)

    settings = Settings(_env_file=None)

    assert str(settings.llm_base_url).rstrip("/") == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-flash"
    assert settings.llm_api_key is None
    assert settings.input_token_budget == 4096
    assert settings.max_output_tokens == 1024
    assert settings.llm_timeout_seconds == 60
    assert settings.llm_extra_body_json == {}


def test_settings_reject_invalid_json_extra_body(monkeypatch):
    clear_settings_environment(monkeypatch)
    monkeypatch.setenv("LLM_EXTRA_BODY_JSON", "not-json")

    with pytest.raises(SettingsError):
        Settings(_env_file=None)


def test_app_error_exposes_only_the_public_message():
    error = AppError(
        code="upstream_unavailable",
        public_message="The model service is unavailable.",
        status_code=502,
    )

    assert error.code == "upstream_unavailable"
    assert error.public_message == "The model service is unavailable."
    assert error.status_code == 502
