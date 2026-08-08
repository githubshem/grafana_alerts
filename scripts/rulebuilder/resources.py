"""Resource mapping helpers for Grafana CloudWatch alert rules."""

from __future__ import annotations

from typing import Any


def ecs_identity(resource: dict[str, Any], resource_type: str, name: str) -> dict[str, str | None]:
    if resource_type == "ecs":
        return {"cluster_name": name, "service_name": None}
    if resource_type == "ecs_service":
        return {
            "cluster_name": resource.get("cluster") or name,
            "service_name": resource.get("service") or name,
        }
    return {"cluster_name": "", "service_name": None}


def apply_ecs_labels(
    labels: dict[str, str],
    resource_type: str,
    identity: dict[str, str | None],
) -> None:
    if resource_type not in ("ecs", "ecs_service"):
        return
    cluster = identity.get("cluster_name")
    if cluster:
        labels["ClusterName"] = cluster
    service = identity.get("service_name")
    if service:
        labels["ServiceName"] = service


def build_dimensions(
    spec: dict[str, Any],
    resource: dict[str, Any],
    resource_type: str,
    name: str,
) -> dict[str, str]:
    dimensions = dict(resource.get("dimensions") or {})
    if dimensions:
        return dimensions

    if "dimension_keys" not in spec:
        return dimensions

    for dim_key, resource_key in spec["dimension_keys"].items():
        if resource_key == "cw_dimension" and resource.get("cw_dimension"):
            dimensions[dim_key] = resource["cw_dimension"]
        elif resource_key in resource:
            dimensions[dim_key] = str(resource[resource_key])
        elif resource_key == "name":
            dimensions[dim_key] = name
        elif resource_key == "cluster":
            dimensions[dim_key] = resource.get("cluster", name)
        elif resource_key == "service":
            if resource_type == "ecs":
                dimensions[dim_key] = "*"
            else:
                dimensions[dim_key] = resource.get("service", name)
        elif resource_key == "broker_id" and resource.get("broker_id"):
            dimensions[dim_key] = resource["broker_id"]
    return dimensions


def resources_for_type(
    inventory: dict[str, Any],
    resource_type: str,
    spec: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    mapping = {
        "ecs": "ecs_clusters",
        "ecs_service": "ecs_services",
        "alb": "load_balancers",
        "nlb": "load_balancers",
        "api_alb": "load_balancers",
        "rds": "rds_clusters",
        "redis": "redis_groups",
        "mq": "mq_brokers",
    }
    key = mapping.get(resource_type, resource_type)
    items = list(inventory.get(key) or [])

    if resource_type in ("alb", "nlb"):
        lb_type = "application" if resource_type == "alb" else "network"
        return [r for r in items if r.get("type", "application") == lb_type]

    if resource_type == "api_alb":
        return [r for r in items if r.get("type") == "application" and r.get("api_facing")]

    return items
