"""Dashboard URL builders for Grafana alert annotations."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from config import DEFAULT_GRAFANA_URL, REGION, DASHBOARDS, BuildContext

DASHBOARDS: dict[str, dict[str, Any]] = DASHBOARDS


def build_dashboard_vars(
    resource: dict[str, Any],
    resource_type: str,
    name: str,
    ecs_identity: dict[str, str | None],
) -> dict[str, str]:
    var_values: dict[str, str] = {}
    if resource_type == "ecs":
        var_values["cluster"] = ecs_identity["cluster_name"] or name
    elif resource_type == "ecs_service":
        var_values["cluster"] = ecs_identity["cluster_name"] or ""
        if ecs_identity.get("service_name"):
            var_values["service"] = ecs_identity["service_name"]
    elif resource_type in ("alb", "nlb", "api_alb"):
        var_values["lb"] = resource.get("cw_dimension", name)
        if resource.get("primary_target_group_cw"):
            var_values["targetgroup"] = resource["primary_target_group_cw"]
    elif resource_type == "rds":
        var_values["db_cluster"] = resource.get("cluster", name)
        var_values["db_instance"] = resource.get("instance", name)
    elif resource_type == "redis":
        var_values["redis_cluster"] = name
    elif resource_type == "mq":
        var_values["broker"] = name
    return var_values


def build_dashboard_url(
    dashboard_key: str,
    *,
    ctx: BuildContext | None = None,
    grafana_url: str = DEFAULT_GRAFANA_URL,
    region: str = REGION,
    panel_id: int | None = None,
    **var_values: str,
) -> str:
    dashboards = (ctx.dashboards if ctx else DASHBOARDS)
    if dashboard_key not in dashboards or not dashboards[dashboard_key].get("uid"):
        fallback = dashboards.get(dashboard_key, {}).get("fallback_dashboard", "alb")
        if fallback in dashboards:
            dashboard_key = fallback
        else:
            return grafana_url

    meta = dashboards[dashboard_key]
    params: dict[str, str] = {}
    raw_vars = meta.get("vars") or {}
    if isinstance(raw_vars, list):
        var_map = {v: v for v in raw_vars}
        if "Region" in raw_vars:
            var_map = {"region": "Region", "lb": "LB"}
        elif "RedisCluster" in raw_vars:
            var_map = {"region": "region", "redis_cluster": "RedisCluster"}
        elif "broker" in raw_vars:
            var_map = {"region": "region", "broker": "broker"}
        elif "cluster" in raw_vars:
            var_map = {"region": "region", "cluster": "cluster", "service": "service"}
        elif "db_cluster" in raw_vars:
            var_map = {
                "region": "region",
                "db_cluster": "db_cluster",
                "db_instance": "db_instance",
            }
        else:
            var_map = {v: v for v in raw_vars}
    else:
        var_map = raw_vars

    if "region" in var_map:
        params[f"var-{var_map['region']}"] = region

    for key, grafana_var in var_map.items():
        if key == "region":
            continue
        if key in var_values and var_values[key]:
            params[f"var-{grafana_var}"] = var_values[key]

    slug = meta.get("slug") or dashboard_key
    base = f"{grafana_url.rstrip('/')}/d/{meta['uid']}/{slug}"
    query = urlencode(params)
    url = f"{base}?{query}" if query else base
    if panel_id is not None:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}orgId=1&viewPanel={panel_id}"
    elif "?" in url:
        url = f"{url}&orgId=1"
    else:
        url = f"{url}?orgId=1"
    return url


def extract_dashboard_urls_from_rules(rules: list[dict[str, Any]]) -> list[dict[str, str]]:
    urls: list[dict[str, str]] = []
    for rule in rules:
        url = (rule.get("annotations") or {}).get("dashboard_url", "")
        if url:
            urls.append({"rule": rule.get("title", ""), "url": url})
    return urls
