import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import Settings, StudioConfig, load_settings, load_studio


@pytest.fixture
def credentials(monkeypatch):
    values = {
        "YCLIENTS_API_HOST": "https://example.invalid",
        "YCLIENTS_PARTNER_TOKEN": "dummy-partner",
        "YCLIENTS_USER_TOKEN": "dummy-user",
        "YCLIENTS_COMPANY_ID": "1",
        "TELEGRAM_BOT_TOKEN": "dummy-bot",
        "OWNER_TELEGRAM_ID": "2",
    }
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return values


def test_settings_from_external_file(tmp_path, credentials, monkeypatch):
    path = tmp_path / "credentials.txt"
    path.write_text("\n".join(f"{k}={v}" for k, v in credentials.items()))
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "3")
    settings = load_settings(path)
    assert settings.owner_telegram_id == 3
    assert "dummy-partner" not in repr(settings)


@pytest.mark.parametrize("variable", list(Settings.model_fields)[:-1])
def test_missing_variable(tmp_path, credentials, monkeypatch, variable):
    credentials.pop(variable.upper())
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=f"нет переменной {variable.upper()}"):
        load_settings(tmp_path / "absent")


def test_invalid_secret_is_not_printed(tmp_path, credentials, monkeypatch):
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "private-invalid-value")
    with pytest.raises(ValueError) as error:
        load_settings(tmp_path / "absent")
    assert "private-invalid-value" not in str(error.value)


@pytest.mark.parametrize("field,value", [("version", 0), ("unknown", 1), ("halls", [])])
def test_studio_rejects_invalid_config(field, value):
    config = load_studio().model_dump(by_alias=True)
    config[field] = value
    with pytest.raises(ValidationError):
        StudioConfig.model_validate(config)


def test_studio_pending_mapping_is_valid():
    config = load_studio()
    assert all(hall.yclients_staff_id is None for hall in config.halls)
    assert config.studio.timezone == "Europe/Moscow"


def test_claude_protection_rules():
    denies = json.loads(Path(".claude/settings.json").read_text())["permissions"]["deny"]
    assert "Read(**/secrets.env)" in denies
    assert "Read(~/.config/studio-bot/**)" in denies
    assert "Bash(docker compose config*)" in denies


def test_optional_proxy_empty(tmp_path, credentials, monkeypatch):
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "")
    assert load_settings(tmp_path / "absent").telegram_proxy_url is None


def test_combined_t102_t105_config():
    config = load_studio()
    assert config.version >= 4
    assert config.yclients_http.timeout_seconds > 0
    assert config.yclients_http.retry_backoff_seconds
    assert config.delivery.request_timeout_seconds > 0
    assert StudioConfig.model_validate(config.model_dump(by_alias=True)) == config


@pytest.mark.parametrize(
    "section,field",
    [
        ("yclients_http", "timeout_seconds"),
        ("delivery", "request_timeout_seconds"),
    ],
)
def test_combined_config_validates_both_sections(section, field):
    values = load_studio().model_dump(by_alias=True)
    values[section][field] = 0
    with pytest.raises(ValidationError):
        StudioConfig.model_validate(values)
