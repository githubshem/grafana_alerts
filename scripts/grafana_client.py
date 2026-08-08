"""Shared Grafana HTTP client for the provisioning scripts."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from config import DEFAULT_GRAFANA_URL
from secrets_store import KEY_READ_TOKEN, KEY_WRITE_TOKEN, SecretResolutionError, get_secret_value

logger = logging.getLogger(__name__)


class GrafanaError(Exception):
    """Raised when a Grafana API call fails."""

    def __init__(self, message: str, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class GrafanaClient:
    """Minimal Grafana REST client using service-account token auth."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int = 120,
        *,
        read_only: bool = False,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("GRAFANA_URL")
            or DEFAULT_GRAFANA_URL
        ).rstrip("/")
        self.read_only = read_only or os.environ.get("GRAFANA_READ_ONLY", "").lower() in (
            "1",
            "true",
            "yes",
        )
        self.api_key = api_key or self._resolve_token()
        self.timeout = timeout

        if not self.api_key:
            raise GrafanaError(
                "Grafana token required (GRAFANA_WRITE_TOKEN / GRAFANA_READ_TOKEN)",
            )

    def _resolve_token(self) -> str:
        """Environment variables first, then the AWS Secrets Manager JSON secret."""
        if self.read_only:
            env_vars = ["GRAFANA_READ_TOKEN", "GRAFANA_API_KEY"]
            secret_key = KEY_READ_TOKEN
        else:
            env_vars = [
                "GRAFANA_API_KEY",
                "GRAFANA_WRITE_TOKEN",
                "GRAFANA_READ_TOKEN",
            ]
            secret_key = KEY_WRITE_TOKEN

        for env_var in env_vars:
            value = os.environ.get(env_var, "").strip()
            if value:
                return value

        try:
            return get_secret_value(secret_key, env_vars=env_vars, required=False)
        except SecretResolutionError as exc:
            logger.debug("Secrets Manager lookup failed: %s", exc)
            return ""

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        method_u = method.upper()
        if self.read_only and method_u not in ("GET", "HEAD", "OPTIONS"):
            raise GrafanaError(
                f"Read-only client blocked {method_u} {path}",
                status=403,
            )

        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = Request(url, data=data, headers=self._headers(), method=method_u)
        logger.debug("%s %s", method_u, url)

        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                return json.loads(raw)
        except HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw_body) if raw_body else None
            except json.JSONDecodeError:
                parsed = raw_body
            raise GrafanaError(
                f"{method_u} {path} failed: HTTP {exc.code}",
                status=exc.code,
                body=parsed,
            ) from exc
        except URLError as exc:
            raise GrafanaError(f"{method_u} {path} failed: {exc.reason}") from exc

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, body: Any, **kwargs: Any) -> Any:
        return self.request("POST", path, body=body, **kwargs)

    def put(self, path: str, body: Any, **kwargs: Any) -> Any:
        return self.request("PUT", path, body=body, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    def list_dashboards(self) -> list[dict[str, Any]]:
        return self.get("/api/search", params={"type": "dash-db", "limit": "5000"})

    def get_dashboard(self, uid: str) -> dict[str, Any]:
        return self.get(f"/api/dashboards/uid/{uid}")

    def list_alert_rules(self) -> list[dict[str, Any]]:
        return self.get("/api/v1/provisioning/alert-rules")

    def get_alert_rule(self, uid: str) -> dict[str, Any]:
        return self.get(f"/api/v1/provisioning/alert-rules/{uid}")

    def create_alert_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        return self.post("/api/v1/provisioning/alert-rules", rule)

    def update_alert_rule(self, uid: str, rule: dict[str, Any]) -> dict[str, Any]:
        return self.put(f"/api/v1/provisioning/alert-rules/{uid}", rule)

    def delete_alert_rule(self, uid: str) -> None:
        self.delete(f"/api/v1/provisioning/alert-rules/{uid}")

    def create_notification_template(self, name: str, template_body: str) -> dict[str, Any]:
        return self.put(f"/api/v1/provisioning/templates/{name}", {"template": template_body})

    def upsert_contact_point(self, contact_point: dict[str, Any]) -> dict[str, Any]:
        return self.post("/api/v1/provisioning/contact-points", contact_point)

    def update_contact_point(self, uid: str, contact_point: dict[str, Any]) -> dict[str, Any]:
        return self.put(f"/api/v1/provisioning/contact-points/{uid}", contact_point)

    def create_dashboard(
        self,
        dashboard: dict[str, Any],
        folder_uid: str,
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "dashboard": dashboard,
            "folderUid": folder_uid,
            "overwrite": overwrite,
            "message": "Provisioned by grafana-alerts scripts",
        }
        return self.post("/api/dashboards/db", payload)

    def test_contact_point(self, name: str) -> Any:
        return self.post(f"/api/alertmanager/grafana/config/api/v1/receivers/{name}/test", {})

    def list_contact_points(self) -> list[dict[str, Any]]:
        return self.get("/api/v1/provisioning/contact-points")

    def get_contact_point(self, uid: str) -> dict[str, Any]:
        return self.get(f"/api/v1/provisioning/contact-points/{uid}")

    def list_notification_templates(self) -> list[dict[str, Any]]:
        return self.get("/api/v1/provisioning/templates")

    def get_notification_policies(self) -> dict[str, Any]:
        return self.get("/api/v1/provisioning/policies")

    def put_notification_policies(self, policies: dict[str, Any]) -> dict[str, Any]:
        return self.put("/api/v1/provisioning/policies", policies)

    def put_rule_group(
        self,
        folder_uid: str,
        group_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.put(
            f"/api/v1/provisioning/folder/{folder_uid}/rule-groups/{group_name}",
            payload,
        )

    def get_rule_group(self, folder_uid: str, group_name: str) -> dict[str, Any]:
        return self.get(f"/api/v1/provisioning/folder/{folder_uid}/rule-groups/{group_name}")

    def health(self) -> dict[str, Any]:
        return self.get("/api/health")


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Configure root logger for CLI scripts."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    if json_output:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonLogFormatter())
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"),
        )
    logging.basicConfig(level=log_level, handlers=[handler], force=True)


class JsonLogFormatter(logging.Formatter):
    """Emit log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            payload.update(record.extra_fields)
        return json.dumps(payload)


def emit_result(payload: dict[str, Any], json_output: bool) -> None:
    """Print structured CLI result."""
    if json_output:
        print(json.dumps(payload, indent=2, default=str))
    else:
        status = payload.get("status", "ok")
        print(f"status={status}")
        for key, value in payload.items():
            if key != "status":
                print(f"  {key}: {value}")
