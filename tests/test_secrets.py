"""Secret resolution: env wins over AWS, and secret values never reach an error.

boto3 is stubbed throughout, so no AWS call and no credentials are needed.
"""

from __future__ import annotations

import sys
import types

import pytest

SECRET_VALUE = "glsa_super_secret_value_do_not_log"


@pytest.fixture(autouse=True)
def clear_secret_cache():
    import secrets_store

    secrets_store.reset_cache()
    yield
    secrets_store.reset_cache()


def _stub_boto3(monkeypatch, payload: str, *, raises: Exception | None = None):
    """Install a fake boto3 module that returns payload from get_secret_value."""
    calls: list[dict] = []

    class FakeClient:
        def get_secret_value(self, **kwargs):
            calls.append(kwargs)
            if raises:
                raise raises
            return {"SecretString": payload}

    fake = types.ModuleType("boto3")
    fake.client = lambda service, region_name=None: FakeClient()  # noqa: ARG005
    monkeypatch.setitem(sys.modules, "boto3", fake)
    return calls


def test_env_var_wins_over_secrets_manager(monkeypatch):
    from secrets_store import get_secret_value

    calls = _stub_boto3(monkeypatch, '{"read_token": "from-aws"}')
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "from-env")

    assert get_secret_value("read_token", env_vars=["GRAFANA_READ_TOKEN"]) == "from-env"
    assert calls == [], "Secrets Manager must not be called when the env var is set"


def test_falls_back_to_secrets_manager(monkeypatch):
    from secrets_store import get_secret_value

    _stub_boto3(monkeypatch, '{"read_token": "from-aws"}')
    monkeypatch.delenv("GRAFANA_READ_TOKEN", raising=False)

    assert get_secret_value("read_token", env_vars=["GRAFANA_READ_TOKEN"]) == "from-aws"


def test_placeholder_env_var_is_ignored(monkeypatch):
    from secrets_store import get_secret_value

    _stub_boto3(monkeypatch, '{"write_token": "real-token"}')
    monkeypatch.setenv("GRAFANA_WRITE_TOKEN", "PASTE_YOUR_TOKEN_HERE")

    assert get_secret_value("write_token", env_vars=["GRAFANA_WRITE_TOKEN"]) == "real-token"


def test_secret_is_fetched_once_and_cached(monkeypatch):
    from secrets_store import get_secret_value

    calls = _stub_boto3(monkeypatch, '{"read_token": "a", "write_token": "b"}')
    monkeypatch.delenv("GRAFANA_READ_TOKEN", raising=False)

    assert get_secret_value("read_token") == "a"
    assert get_secret_value("write_token") == "b"
    assert len(calls) == 1


def test_missing_key_raises_without_leaking_any_value(monkeypatch):
    from secrets_store import SecretResolutionError, get_secret_value

    _stub_boto3(monkeypatch, f'{{"read_token": "{SECRET_VALUE}"}}')
    monkeypatch.delenv("GRAFANA_WRITE_TOKEN", raising=False)

    with pytest.raises(SecretResolutionError) as exc:
        get_secret_value("write_token", env_vars=["GRAFANA_WRITE_TOKEN"])

    message = str(exc.value)
    assert SECRET_VALUE not in message
    assert "write_token" in message
    assert "GRAFANA_WRITE_TOKEN" in message
    assert "grafana-alerts/provisioning" in message


def test_aws_failure_message_names_the_secret_not_the_value(monkeypatch):
    from secrets_store import SecretResolutionError, get_secret_value

    _stub_boto3(monkeypatch, "", raises=RuntimeError(f"denied for {SECRET_VALUE}"))
    monkeypatch.delenv("GRAFANA_READ_TOKEN", raising=False)

    with pytest.raises(SecretResolutionError) as exc:
        get_secret_value("read_token", env_vars=["GRAFANA_READ_TOKEN"])

    assert SECRET_VALUE not in str(exc.value)
    assert "grafana-alerts/provisioning" in str(exc.value)


def test_invalid_json_raises_a_clear_error(monkeypatch):
    from secrets_store import SecretResolutionError, get_secret_value

    _stub_boto3(monkeypatch, "not json at all")
    monkeypatch.delenv("GRAFANA_READ_TOKEN", raising=False)

    with pytest.raises(SecretResolutionError, match="not valid JSON"):
        get_secret_value("read_token")


def test_not_required_returns_empty_instead_of_raising(monkeypatch):
    from secrets_store import get_secret_value

    _stub_boto3(monkeypatch, "{}")
    monkeypatch.delenv("GRAFANA_READ_TOKEN", raising=False)

    assert get_secret_value("read_token", required=False) == ""


def test_grafana_client_prefers_env_token(monkeypatch):
    from grafana_client import GrafanaClient

    _stub_boto3(monkeypatch, '{"write_token": "from-aws"}')
    monkeypatch.setenv("GRAFANA_API_KEY", "from-env")
    assert GrafanaClient().api_key == "from-env"


def test_grafana_client_read_only_uses_the_read_token(monkeypatch):
    from grafana_client import GrafanaClient

    _stub_boto3(monkeypatch, '{"read_token": "aws-read", "write_token": "aws-write"}')
    for var in ("GRAFANA_API_KEY", "GRAFANA_READ_TOKEN", "GRAFANA_WRITE_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    assert GrafanaClient(read_only=True).api_key == "aws-read"


def test_grafana_client_write_uses_the_write_token(monkeypatch):
    from grafana_client import GrafanaClient

    _stub_boto3(monkeypatch, '{"read_token": "aws-read", "write_token": "aws-write"}')
    for var in ("GRAFANA_API_KEY", "GRAFANA_READ_TOKEN", "GRAFANA_WRITE_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    assert GrafanaClient().api_key == "aws-write"


def test_grafana_client_raises_when_nothing_resolves(monkeypatch):
    from grafana_client import GrafanaClient, GrafanaError

    _stub_boto3(monkeypatch, "{}")
    for var in ("GRAFANA_API_KEY", "GRAFANA_READ_TOKEN", "GRAFANA_WRITE_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(GrafanaError, match="token required"):
        GrafanaClient()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("", True),
        ("PASTE_TOKEN_HERE", True),
        ("changeme", True),
        ("your-token-here", True),
        ("glsa_realtoken123", False),
        ("https://example.com/webhook", False),
    ],
)
def test_placeholder_detection(value, expected):
    from secrets_store import looks_like_placeholder

    assert looks_like_placeholder(value) is expected
