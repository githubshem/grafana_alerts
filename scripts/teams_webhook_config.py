"""Resolve the Teams/Power Automate webhook URL.

Sources, in the order given by ``config/teams_webhook.yaml``:

1. ``env`` - the environment variable named by ``webhook.env_var``
2. ``aws_secrets_manager`` - the shared JSON secret read via ``secrets_store``

The URL itself is never written to the repository, logs, or evidence files.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
PROVISIONING_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from secrets_store import (
    KEY_WEBHOOK_URL,
    SecretResolutionError,
    get_secret_value,
    looks_like_placeholder,
)

DEFAULT_CONFIG = PROVISIONING_ROOT / "config" / "teams_webhook.yaml"
LOCAL_CONFIG = PROVISIONING_ROOT / "config" / "teams_webhook.local.yaml"

# Patterns that may appear in webhook URLs or JSON error payloads.
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SETTINGS_URL_RE = re.compile(r'("url"\s*:\s*")[^"]*(")', re.IGNORECASE)

# Keys where a real value in .env should win over a stale shell export.
PROVISIONING_DOTENV_KEYS = frozenset(
    {
        "GRAFANA_TEAMS_WEBHOOK_URL",
        "GRAFANA_WRITE_TOKEN",
        "GRAFANA_READ_TOKEN",
    }
)


class TeamsWebhookConfigError(RuntimeError):
    """Raised when the webhook URL cannot be resolved from configured sources."""


@dataclass
class DotenvLoadResult:
    env_path: Path
    missing_file: bool = False
    loaded: list[str] = field(default_factory=list)
    skipped_placeholder: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    overridden_placeholder: list[str] = field(default_factory=list)


def value_looks_like_placeholder(value: str) -> bool:
    """Kept as the public name used by provisioning scripts."""
    return looks_like_placeholder(value)


def load_config() -> dict[str, Any]:
    """Load teams webhook config; local override merges on top of defaults."""
    if not DEFAULT_CONFIG.is_file():
        raise TeamsWebhookConfigError(f"Missing config file: {DEFAULT_CONFIG}")
    doc = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")) or {}
    if LOCAL_CONFIG.is_file():
        local = yaml.safe_load(LOCAL_CONFIG.read_text(encoding="utf-8")) or {}
        webhook = {**(doc.get("webhook") or {}), **(local.get("webhook") or {})}
        doc["webhook"] = webhook
    return doc


def _webhook_section(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if config is not None else load_config()
    section = cfg.get("webhook") or {}
    if not section:
        raise TeamsWebhookConfigError("Config missing 'webhook' section")
    return section


def _from_env(env_var: str) -> str | None:
    value = os.environ.get(env_var, "").strip()
    return value or None


def resolve_webhook_url(*, config: dict[str, Any] | None = None) -> str:
    """Resolve the webhook URL. Raises TeamsWebhookConfigError if not found."""
    section = _webhook_section(config)
    env_var = (section.get("env_var") or "GRAFANA_TEAMS_WEBHOOK_URL").strip()
    sm_cfg = section.get("aws_secrets_manager") or {}
    source_order = section.get("source_order") or ["env", "aws_secrets_manager"]

    errors: list[str] = []
    for source in source_order:
        if source == "env":
            value = _from_env(env_var)
            if value:
                return value
            errors.append(f"env var {env_var} is unset or empty")
        elif source == "aws_secrets_manager":
            if not sm_cfg.get("enabled"):
                errors.append("aws_secrets_manager is disabled in config")
                continue
            secret_name = (sm_cfg.get("secret_name") or "").strip()
            if not secret_name:
                raise TeamsWebhookConfigError(
                    "aws_secrets_manager.enabled but secret_name is empty"
                )
            secret_key = (sm_cfg.get("secret_key") or KEY_WEBHOOK_URL).strip()
            region = (sm_cfg.get("region") or os.environ.get("AWS_REGION") or "").strip()
            try:
                kwargs: dict[str, Any] = {
                    "env_vars": [env_var],
                    "required": False,
                    "secret_name": secret_name,
                }
                if region:
                    kwargs["region"] = region
                value = get_secret_value(secret_key, **kwargs)
            except SecretResolutionError as exc:
                raise TeamsWebhookConfigError(str(exc)) from exc
            if value:
                return value
            errors.append(f"AWS secret {secret_name!r} key {secret_key!r} returned empty value")
        else:
            errors.append(f"unknown source_order entry: {source!r}")

    raise TeamsWebhookConfigError(
        "Teams webhook URL not found. "
        + "; ".join(errors)
        + f". Set {env_var} or enable aws_secrets_manager in {DEFAULT_CONFIG.name}."
    )


def describe_webhook_source(*, config: dict[str, Any] | None = None) -> str:
    """Return a safe label for evidence (never the URL)."""
    section = _webhook_section(config)
    env_var = (section.get("env_var") or "GRAFANA_TEAMS_WEBHOOK_URL").strip()
    sm_cfg = section.get("aws_secrets_manager") or {}
    source_order = section.get("source_order") or ["env", "aws_secrets_manager"]

    for source in source_order:
        if source == "env" and _from_env(env_var):
            return f"env:{env_var}"
        if source == "aws_secrets_manager" and sm_cfg.get("enabled"):
            return f"aws_secrets_manager:{sm_cfg.get('secret_name', 'unknown')}"
    return "unresolved"


def redact_secrets(text: str, *, webhook_url: str | None = None) -> str:
    """Redact webhook URLs from log/evidence strings."""
    if not text:
        return text
    out = text
    if webhook_url:
        out = out.replace(webhook_url, "[REDACTED_WEBHOOK_URL]")
    out = _SETTINGS_URL_RE.sub(r'\1[REDACTED_WEBHOOK_URL]\2', out)
    out = _URL_RE.sub("[REDACTED_URL]", out)
    return out


def load_dotenv_if_present() -> DotenvLoadResult:
    """Load the repository ``.env`` into os.environ (cwd-independent).

    For PROVISIONING_DOTENV_KEYS, a non-placeholder value in .env always wins
    over the shell (including a previously exported real value) so local .env
    edits such as a webhook rotation are not shadowed by a stale export. Other
    keys are not overridden when already set in the environment.
    """
    env_path = PROVISIONING_ROOT / ".env"
    result = DotenvLoadResult(env_path=env_path, missing_file=not env_path.is_file())
    if result.missing_file:
        return result

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue

        if value_looks_like_placeholder(value):
            result.skipped_placeholder.append(key)
            continue

        shell_value = os.environ.get(key, "")
        shell_is_placeholder = not shell_value or value_looks_like_placeholder(shell_value)

        if key in PROVISIONING_DOTENV_KEYS:
            if key in os.environ and not shell_is_placeholder and os.environ.get(key) == value:
                result.skipped_existing.append(key)
            else:
                if key in os.environ:
                    result.overridden_placeholder.append(key)
                else:
                    result.loaded.append(key)
                os.environ[key] = value
            continue

        if key in os.environ:
            result.skipped_existing.append(key)
            continue
        os.environ[key] = value
        result.loaded.append(key)

    return result
