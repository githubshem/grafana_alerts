"""Compile Grafana CloudWatch alert rule payloads from specs and inventory."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from config import DEFAULT_GRAFANA_URL, BuildContext
from rulebuilder.annotations import (
    build_runbook,
    format_threshold_display,
    metric_annotation_from_spec,
    resolve_summary,
    summary_context,
    unit_annotation_from_spec,
    unit_label_annotation_from_spec,
)
from rulebuilder.dashboards import build_dashboard_url, build_dashboard_vars
from rulebuilder.exclusions import (
    is_aurora_pause_storage,
    passes_batch_canary_filter,
    should_skip_resource,
)
from rulebuilder.queries import build_query_data
from rulebuilder.resources import apply_ecs_labels, ecs_identity, resources_for_type

GIB = 1024**3


def stable_uid(seed: str) -> str:
    digest = hashlib.sha256(seed.encode()).hexdigest()
    return digest[:12]


def resolve_threshold(spec: dict[str, Any], resource: dict[str, Any]) -> tuple[float, str]:
    """Return (threshold_value, evaluator)."""
    evaluator = spec.get("evaluator", "gt")

    if spec.get("pause_if_aurora_storage") and resource.get("aurora_auto_scaling_storage"):
        return float(spec.get("threshold_bytes", 1)), spec.get("evaluator", "lt")

    if spec.get("threshold_from_resource"):
        key = spec["threshold_from_resource"]
        val = resource.get(key)
        if val is None:
            raise ValueError(
                f"Rule {spec.get('id')} missing resource baseline {key} for {resource.get('name')}"
            )
        return float(val), spec.get("evaluator", "lt")

    if spec.get("threshold_bytes") is not None:
        return float(spec["threshold_bytes"]), spec.get("evaluator", "lt")

    threshold = spec.get("threshold")
    if threshold is not None:
        # Unhealthy hosts: single-target LB uses critical at >=1.
        if spec.get("id", "").startswith("alb-unhealthy") or spec.get("id", "").startswith("nlb-unhealthy"):
            if spec.get("severity") == "critical" and resource.get("target_count", 2) <= 1:
                return 0.5, "gt"
        return float(threshold), evaluator

    raise ValueError(f"Rule spec {spec.get('id')} missing threshold")


def _resolve_context(ctx: BuildContext | None, grafana_url: str | None) -> BuildContext:
    if ctx is None:
        return BuildContext(grafana_url=grafana_url or DEFAULT_GRAFANA_URL)
    if grafana_url:
        return BuildContext(
            rule_prefix=ctx.rule_prefix,
            folder_uid=ctx.folder_uid,
            datasource_uid=ctx.datasource_uid,
            region=ctx.region,
            grafana_url=grafana_url.rstrip("/"),
            contact_point=ctx.contact_point,
            dashboards=ctx.dashboards,
            default_labels=ctx.default_labels,
        )
    return ctx


def _rule_identity(spec: dict[str, Any], resource: dict[str, Any], resource_type: str, name: str, identity: dict[str, str | None], prefix: str) -> tuple[str, str]:
    """Return (title, uid_seed). ECS services are keyed by cluster as well as name."""
    if resource_type == "ecs_service":
        cluster = identity.get("cluster") or resource.get("cluster") or ""
        title = f"{prefix}{spec['id']}-{cluster}-{name}"
        seed = f"{prefix}{spec['id']}:{cluster}:{name}:{spec['metric']}"
    else:
        title = f"{prefix}{spec['id']}-{name}"
        seed = f"{prefix}{spec['id']}:{name}:{spec['metric']}"
    return title, seed


def _resolve_dashboard_key(spec: dict[str, Any], resource_type: str, ctx: BuildContext) -> str:
    dashboard_key = spec.get("dashboard", resource_type)
    if dashboard_key == "api":
        api_meta = ctx.dashboards.get("api", {})
        if not api_meta.get("uid"):
            dashboard_key = api_meta.get("fallback_dashboard", "alb")
    return dashboard_key


def _build_labels(
    spec: dict[str, Any],
    resource: dict[str, Any],
    resource_type: str,
    name: str,
    identity: dict[str, str | None],
    dashboard_key: str,
    dashboard_meta: dict[str, Any],
    ctx: BuildContext,
) -> dict[str, str]:
    labels = {
        **ctx.default_labels,
        "region": resource.get("region", ctx.region),
        "service_type": resource_type.replace("_service", ""),
        "severity": spec.get("severity", "warning"),
        "resource": name,
    }
    labels.update(spec.get("labels") or {})
    labels.update(resource.get("labels") or {})

    if resource_type in ("ecs", "ecs_service"):
        apply_ecs_labels(labels, resource_type, identity)

    if spec.get("apply_batch"):
        labels["apply_batch"] = str(spec["apply_batch"])

    if resource.get("memory_baseline") == "needs_baseline":
        labels["memory_baseline"] = "needs_baseline"
    if resource.get("storage_baseline") == "needs_baseline":
        labels["storage_baseline"] = "needs_baseline"
    if resource.get("needs_dashboard_follow_up"):
        labels["needs_dashboard_follow_up"] = "true"
    elif dashboard_meta.get("needs_dashboard_follow_up") or ctx.dashboards.get(dashboard_key, {}).get(
        "needs_dashboard_follow_up"
    ):
        labels["needs_dashboard_follow_up"] = "true"

    return labels


def _threshold_annotation(spec: dict[str, Any], threshold_explanation: str, evaluator: str, threshold_val: float) -> str:
    if spec.get("threshold_display"):
        return str(spec["threshold_display"])
    if spec.get("threshold_explanation") and not str(spec["threshold_explanation"]).startswith(
        ("gt ", "lt ", "gte ", "lte ")
    ):
        return str(threshold_explanation)
    return format_threshold_display(
        evaluator,
        threshold_val,
        unit=unit_annotation_from_spec(spec),
        unit_label=unit_label_annotation_from_spec(spec),
        metric=str(spec.get("metric") or ""),
    )


def _resolve_rule_group(spec: dict[str, Any], resource: dict[str, Any], resource_type: str, name: str, ctx: BuildContext) -> str:
    rule_group = spec.get("rule_group")
    if not rule_group and spec.get("rule_group_template"):
        try:
            rule_group = str(spec["rule_group_template"]).format(
                prefix=ctx.rule_prefix,
                resource_type=resource_type,
                cluster=resource.get("cluster") or "",
                name=name,
                severity=spec.get("severity", "warning"),
            )
        except (KeyError, ValueError):
            rule_group = f"{ctx.rule_prefix}{resource_type}"
    if not rule_group:
        rule_group = f"{ctx.rule_prefix}{resource_type}"
    return rule_group


def build_rule(
    spec: dict[str, Any],
    resource: dict[str, Any],
    *,
    ctx: BuildContext | None = None,
    grafana_url: str | None = None,
) -> dict[str, Any]:
    ctx = _resolve_context(ctx, grafana_url)

    resource_type = spec["resource_type"]
    name = resource["name"]
    identity = ecs_identity(resource, resource_type, name)
    title, seed = _rule_identity(spec, resource, resource_type, name, identity, ctx.rule_prefix)
    uid = stable_uid(seed)

    context = summary_context(resource, resource_type, name, identity)

    period = int(spec.get("period", 300))
    reducer = spec.get("reducer", "mean")
    threshold_val, evaluator = resolve_threshold(spec, resource)

    data = build_query_data(
        spec,
        resource,
        resource_type,
        name,
        ctx=ctx,
        period=period,
        reducer=reducer,
        threshold_val=threshold_val,
        evaluator=evaluator,
    )
    condition_ref = "F" if spec.get("query_mode") == "metric_math_ratio" else "C"

    dashboard_key = _resolve_dashboard_key(spec, resource_type, ctx)
    panel = spec.get("panel")
    dashboard_meta = ctx.dashboards.get(dashboard_key, ctx.dashboards.get("alb", {}))
    var_values = build_dashboard_vars(resource, resource_type, name, identity)

    dashboard_url = build_dashboard_url(
        dashboard_key,
        ctx=ctx,
        grafana_url=ctx.grafana_url,
        region=resource.get("region", ctx.region),
        panel_id=panel,
        **var_values,
    )

    labels = _build_labels(
        spec, resource, resource_type, name, identity, dashboard_key, dashboard_meta, ctx
    )

    threshold_explanation = spec.get(
        "threshold_explanation",
        f"{evaluator} {threshold_val} over {spec.get('for_duration', '5m')} "
        f"({spec['namespace']}/{spec['metric']}, {spec.get('statistic', 'Average')})",
    )

    annotations = {
        "summary": resolve_summary(spec, resource_type, context),
        "description": spec.get(
            "description",
            f"CloudWatch {spec['namespace']}/{spec['metric']} is {evaluator} {threshold_val} for {name}.",
        ),
        "__dashboardUid__": dashboard_meta.get("uid") or "",
        "__panelId__": str(panel) if panel is not None else "",
        "dashboard_url": dashboard_url,
        "runbook": build_runbook(spec, resource_type, name, resource.get("region", ctx.region), identity),
        "threshold_explanation": threshold_explanation,
        # Power Automate Adaptive Cards read these (annotation-only; no label churn).
        "metric": metric_annotation_from_spec(spec),
        "threshold": _threshold_annotation(spec, threshold_explanation, evaluator, threshold_val),
        "unit": unit_annotation_from_spec(spec),
        "unit_label": unit_label_annotation_from_spec(spec),
        "value_ref": "E" if spec.get("query_mode") == "metric_math_ratio" else "B",
    }

    contact_point = spec.get("contact_point", ctx.contact_point)
    for_duration = spec.get("for_duration", "3m" if spec.get("severity") == "warning" else "2m")
    is_paused = bool(spec.get("paused")) or bool(
        spec.get("pause_if_aurora_storage") and resource.get("aurora_auto_scaling_storage")
    )
    rule_group = _resolve_rule_group(spec, resource, resource_type, name, ctx)

    return {
        "uid": uid,
        "title": title,
        "ruleGroup": rule_group,
        "folderUID": ctx.folder_uid,
        "condition": condition_ref,
        "data": data,
        "noDataState": spec.get("no_data_state", "NoData"),
        "execErrState": spec.get("exec_err_state", "Error"),
        "for": for_duration,
        "annotations": annotations,
        "labels": labels,
        "isPaused": is_paused,
        "notification_settings": {"receiver": contact_point},
        "_meta": {
            "apply_batch": spec.get("apply_batch"),
            "ecs_cluster": resource.get("cluster"),
            "resource_type": resource_type,
            "primary_target_group_cw": resource.get("primary_target_group_cw"),
            "aurora_auto_scaling_storage": resource.get("aurora_auto_scaling_storage"),
            "limitation_fix": spec.get("limitation_fix"),
        },
    }


def expand_rules(
    rule_specs: list[dict[str, Any]],
    inventory: dict[str, Any],
    *,
    ctx: BuildContext | None = None,
    grafana_url: str | None = None,
    name_filter: str | None = None,
    ecs_cluster: str | None = None,
    ecs_canary_only: bool = False,
) -> list[dict[str, Any]]:
    if ctx is None:
        ctx = BuildContext.from_inventory(inventory)
    if grafana_url:
        ctx.grafana_url = grafana_url.rstrip("/")

    compiled: list[dict[str, Any]] = []
    pattern = re.compile(name_filter) if name_filter else None
    canary_names = set(inventory.get("ecs_canary_services") or [])

    for spec in rule_specs:
        if spec.get("enabled") is False:
            continue

        resource_type = spec["resource_type"]
        resources = resources_for_type(inventory, resource_type, spec)

        for resource in resources:
            if pattern and not pattern.search(resource["name"]):
                continue
            if ecs_cluster and resource.get("cluster") != ecs_cluster:
                continue

            if is_aurora_pause_storage(spec, resource):
                pause_spec = {
                    **spec,
                    "pause_if_aurora_storage": True,
                    "limitation_fix": "aurora_storage_pause",
                }
                compiled.append(build_rule(pause_spec, resource, ctx=ctx))
                continue

            if should_skip_resource(spec, resource):
                continue

            if not passes_batch_canary_filter(
                spec,
                resource,
                resource_type,
                canary_names,
                ecs_canary_only=ecs_canary_only,
            ):
                continue

            compiled.append(build_rule(spec, resource, ctx=ctx))

    return compiled
