import pytest
from pydantic import ValidationError

from commerce_support.config import Settings


def test_database_url_defaults_to_missing_without_exposing_credentials() -> None:
    settings = Settings(_env_file=None, llm_api_key=None)

    assert settings.database_url is None
    assert "database_url" not in repr(settings)


def test_database_url_requires_mysql_asyncmy_and_is_secret() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key=None,
        database_url="mysql+asyncmy://support:private-value@localhost:3307/commerce_support",
    )

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value().startswith("mysql+asyncmy://")
    assert "private-value" not in repr(settings)
    assert "private-value" not in str(settings.database_url)

    with pytest.raises(ValidationError) as error:
        Settings(
            _env_file=None,
            llm_api_key=None,
            database_url="mysql://support:private-value@localhost:3307/commerce_support",
        )
    assert "private-value" not in str(error.value)


def test_turn_lease_exceeds_chat_deadline_by_at_least_thirty_seconds() -> None:
    settings = Settings(_env_file=None, llm_api_key=None)

    assert settings.chat_deadline_seconds == 150
    assert settings.turn_lease_seconds == 180
    assert settings.tool_timeout_seconds == 5
    assert settings.tool_max_retries == 1

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            llm_api_key=None,
            chat_deadline_seconds=150,
            turn_lease_seconds=179,
        )
