"""Central configuration for Grafana alert provisioning.

Single source of truth for stack constants, dashboards, BuildContext,
inventory header, and rule-group intervals. Zero internal dependencies so
grafana_client, generate_inventory, and rulebuilder can all import it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- Core stack constants ---

DEFAULT_GRAFANA_URL = "https://grafana.example.com"
FOLDER_UID = "example-folder"
DATASOURCE_UID = "example-cloudwatch"
REGION = "us-west-1"
RULE_PREFIX = "alerts-"
CONTACT_POINT = "engineering-alerts"

# AWS Secrets Manager (created manually in the console).
SECRETS_MANAGER_SECRET_NAME = "grafana-alerts/provisioning"
SECRETS_MANAGER_REGION = REGION

# managed_by scopes the notification policy route and rollback to rules this
# toolkit owns. Changing it detaches existing alerts from their route.
MANAGED_BY = "provisioning"

DEFAULT_LABELS: dict[str, str] = {
    "team": "engineering",
    "managed_by": MANAGED_BY,
}

# --- Dashboards ---

DASHBOARDS: dict[str, dict[str, Any]] = {
    "ecs": {
        "uid": "example-ecs",
        "slug": "aws-ecs",
        "vars": {"region": "region", "cluster": "cluster", "service": "service"},
        "panels": [1, 2, 6, 7],
        "needs_panel_verification": True,
    },
    "alb": {
        "uid": "example-alb",
        "slug": "aws-alb-elb-monitoring-dashboard",
        "vars": {"region": "Region", "lb": "LB"},
        "panels": [1, 2, 3, 4, 7],
        "needs_panel_verification": True,
    },
    "rds": {
        "uid": "example-rds",
        "slug": "aws-rds",
        "vars": {
            "region": "region",
            "db_cluster": "db_cluster",
            "db_instance": "db_instance",
        },
        "panels": [4, 21, 10],
        "needs_panel_verification": True,
    },
    "redis": {
        "uid": "example-redis",
        "slug": "aws-redis",
        "vars": {"region": "region", "redis_cluster": "RedisCluster"},
        "panels": [60],
        "needs_panel_verification": True,
    },
    "mq": {
        "uid": "example-mq",
        "slug": "aws-rabbitmq",
        "vars": {"region": "region", "broker": "broker"},
        "panels": [1, 2],
        "needs_panel_verification": True,
    },
    "api": {
        "uid": None,
        "slug": None,
        "needs_dashboard_follow_up": True,
        "fallback_dashboard": "alb",
    },
}

# Static inventory header used by generate_inventory.py (no AWS needed).
INVENTORY_HEADER: dict[str, Any] = {
    "grafana_url": DEFAULT_GRAFANA_URL,
    "folder_uid": FOLDER_UID,
    "datasource_uid": DATASOURCE_UID,
    "region": REGION,
    "rule_prefix": RULE_PREFIX,
    "contact_point": CONTACT_POINT,
    "ecs_cluster_filter": "(?i)(app-|infra)",
}

# Rule-group evaluation intervals (seconds).
DEFAULT_GROUP_INTERVAL = 300
FAST_INTERVAL_GROUPS: dict[str, int] = {
    "alerts-availability-critical": 60,
    "alerts-canary": 60,
}


def group_interval(group_name: str, default_interval: int = DEFAULT_GROUP_INTERVAL) -> int:
    if group_name in FAST_INTERVAL_GROUPS:
        return FAST_INTERVAL_GROUPS[group_name]
    if group_name.endswith("-availability-critical"):
        return 60
    return default_interval


@dataclass
class BuildContext:
    rule_prefix: str = RULE_PREFIX
    folder_uid: str = FOLDER_UID
    datasource_uid: str = DATASOURCE_UID
    region: str = REGION
    grafana_url: str = DEFAULT_GRAFANA_URL
    contact_point: str = CONTACT_POINT
    dashboards: dict[str, dict[str, Any]] = field(default_factory=lambda: dict(DASHBOARDS))
    default_labels: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_LABELS))

    @classmethod
    def from_inventory(cls, inventory: dict[str, Any]) -> BuildContext:
        dashboards = inventory.get("dashboards") or DASHBOARDS
        merged_dashboards = {**DASHBOARDS, **dashboards}
        return cls(
            rule_prefix=inventory.get("rule_prefix", RULE_PREFIX),
            folder_uid=inventory.get("folder_uid", FOLDER_UID),
            datasource_uid=inventory.get("datasource_uid", DATASOURCE_UID),
            region=inventory.get("region", REGION),
            grafana_url=inventory.get("grafana_url", DEFAULT_GRAFANA_URL).rstrip("/"),
            contact_point=inventory.get("contact_point", CONTACT_POINT),
            dashboards=merged_dashboards,
            default_labels=dict(DEFAULT_LABELS),
        )


ENV_PROFILES: dict[str, dict[str, Any]] = {
    "default": {
        "inventory": "inventory/services.yaml",
        "rules": "templates/rule_definitions.yaml",
        "rule_prefix": RULE_PREFIX,
    },
}
