"""Resolve Grafana credentials from environment or AWS Secrets Manager.

Named ``secrets_store`` rather than ``secrets`` because ``scripts/`` is prepended
to ``sys.path``, and a module named ``secrets`` would shadow the standard library
module that boto3 depends on.

One JSON secret (created manually in the AWS Console) holds every credential:

    Name:   grafana-alerts/provisioning
    Region: us-west-1
    Keys:   webhook_url, read_token, write_token

Environment variables always take precedence, so local development and one-off
overrides keep working without touching AWS. Secrets Manager is only consulted
when the environment variable is unset or holds a placeholder.
"""

from __future__ import annotations

import json
import os
from typing import Any

from config import SECRETS_MANAGER_REGION, SECRETS_MANAGER_SECRET_NAME

# Secret keys.
KEY_WEBHOOK_URL = "webhook_url"
KEY_READ_TOKEN = "read_token"
KEY_WRITE_TOKEN = "write_token"

_PLACEHOLDER_MARKERS = ("PASTE_", "CHANGEME", "YOUR-TOKEN-HERE")

_CACHE: dict[str, Any] | None = None


class SecretResolutionError(RuntimeError):
    """Raised when a credential cannot be resolved. Never contains secret values."""


def looks_like_placeholder(value: str) -> bool:
    """True for empty or obviously unfilled template values."""
    if not value:
        return True
    upper = value.upper()
    return (
        any(marker in upper for marker in _PLACEHOLDER_MARKERS)
        or upper.endswith("_HERE")
        or value in {"changeme", "your-token-here"}
    )


def reset_cache() -> None:
    """Clear the cached secret payload (used by tests)."""
    global _CACHE
    _CACHE = None


def _load_secret(
    *,
    secret_name: str = SECRETS_MANAGER_SECRET_NAME,
    region: str = SECRETS_MANAGER_REGION,
) -> dict[str, Any]:
    """Fetch and cache the JSON secret. Returns {} when unavailable."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    try:
        import boto3
    except ImportError as exc:
        raise SecretResolutionError(
            "boto3 is required to read AWS Secrets Manager; install requirements.txt"
        ) from exc

    try:
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=secret_name)
    except Exception as exc:
        raise SecretResolutionError(
            f"Failed to read AWS secret {secret_name!r} in {region}: {type(exc).__name__}"
        ) from exc

    raw = response.get("SecretString") or ""
    if not raw:
        raise SecretResolutionError(f"AWS secret {secret_name!r} has no SecretString")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretResolutionError(
            f"AWS secret {secret_name!r} is not valid JSON; expected an object with "
            f"keys {KEY_WEBHOOK_URL}, {KEY_READ_TOKEN}, {KEY_WRITE_TOKEN}"
        ) from exc

    if not isinstance(parsed, dict):
        raise SecretResolutionError(f"AWS secret {secret_name!r} JSON must be an object")

    _CACHE = parsed
    return _CACHE


def get_secret_value(
    key: str,
    *,
    env_vars: list[str] | None = None,
    required: bool = True,
    secret_name: str = SECRETS_MANAGER_SECRET_NAME,
    region: str = SECRETS_MANAGER_REGION,
) -> str:
    """Resolve one credential: environment variables first, then Secrets Manager.

    Raises SecretResolutionError naming the env vars and the secret key, never
    the value itself.
    """
    for env_var in env_vars or []:
        value = os.environ.get(env_var, "").strip()
        if value and not looks_like_placeholder(value):
            return value

    try:
        payload = _load_secret(secret_name=secret_name, region=region)
    except SecretResolutionError:
        if not required:
            return ""
        raise

    value = str(payload.get(key, "")).strip()
    if value and not looks_like_placeholder(value):
        return value

    if not required:
        return ""

    tried = ", ".join(env_vars or []) or "(none)"
    raise SecretResolutionError(
        f"Credential {key!r} not found. Set one of [{tried}] in the environment, "
        f"or add key {key!r} to AWS secret {secret_name!r} in {region}."
    )
