"""Annotation and threshold display helpers for Grafana alert rules."""

from __future__ import annotations

import math
import re
from typing import Any

_GRAFANA_LABEL_TEMPLATE = re.compile(r"\{\{\s*\$labels\.")

# CloudWatch metric name -> Adaptive Card unit. Unrecognized metrics stay "unknown".
_METRIC_UNITS: dict[str, str] = {
    "CPUUtilization": "percent",
    "MemoryUtilization": "percent",
    "EngineCPUUtilization": "percent",
    "DatabaseMemoryUsagePercentage": "percent",
    "SystemCpuUtilization": "percent",
    "TargetResponseTime": "seconds",
    "FreeableMemory": "bytes",
    "FreeStorageSpace": "bytes",
    "HTTPCode_Target_5XX_Count": "count",
    "UnHealthyHostCount": "count",
    "Evictions": "count",
    "DatabaseConnections": "count",
    "ConnectionCount": "count",
}

# Optional Adaptive Card display suffix (e.g. "connections"). Empty for most metrics.
_METRIC_UNIT_LABELS: dict[str, str] = {
    "DatabaseConnections": "connections",
    "ConnectionCount": "connections",
    "HTTPCode_Target_5XX_Count": "responses",
    "UnHealthyHostCount": "unhealthy targets",
    "Evictions": "evictions",
}

UNIT_FORMATTING_ANNOTATION_KEYS: frozenset[str] = frozenset(
    {
        "metric",
        "threshold",
        "unit",
        "unit_label",
        "value_ref",
    }
)


def summary_context(
    resource: dict[str, Any],
    resource_type: str,
    name: str,
    ecs_identity: dict[str, str | None],
) -> dict[str, str]:
    cluster = ecs_identity.get("cluster_name") or ""
    service = ecs_identity.get("service_name") or ""
    return {
        "resource": name,
        "cluster": cluster,
        "service": service,
        "cluster_name": cluster,
        "service_name": service,
    }


def format_summary_pattern(pattern: str, context: dict[str, str]) -> str:
    try:
        return pattern.format(**context)
    except KeyError:
        return pattern


def default_summary(spec: dict[str, Any], resource_type: str, context: dict[str, str]) -> str:
    spec_id = spec.get("id", "")
    resource = context["resource"]
    cluster = context["cluster"]
    service = context["service"]

    if resource_type == "ecs":
        if "cpu" in spec_id and "warning" in spec_id:
            return f"ECS CPU warning for cluster {cluster}"
        if "cpu" in spec_id and "critical" in spec_id:
            return f"ECS CPU critical for cluster {cluster}"
        if "memory" in spec_id and "warning" in spec_id:
            return f"ECS memory warning for cluster {cluster}"
        if "memory" in spec_id and "critical" in spec_id:
            return f"ECS memory critical for cluster {cluster}"
        return f"ECS threshold exceeded for cluster {cluster}"

    if resource_type == "ecs_service":
        if "cpu" in spec_id and "warning" in spec_id:
            return f"ECS CPU warning for {service} on {cluster}"
        if "cpu" in spec_id and "critical" in spec_id:
            return f"ECS CPU critical for {service} on {cluster}"
        if "memory" in spec_id and "warning" in spec_id:
            return f"ECS memory warning for {service} on {cluster}"
        if "memory" in spec_id and "critical" in spec_id:
            return f"ECS memory critical for {service} on {cluster}"
        return f"ECS threshold exceeded for {service} on {cluster}"

    return f"{spec['metric']} threshold exceeded for {resource}"


def resolve_summary(
    spec: dict[str, Any],
    resource_type: str,
    context: dict[str, str],
) -> str:
    raw = spec.get("summary")
    if raw and not _GRAFANA_LABEL_TEMPLATE.search(raw):
        if "{" in raw and "}" in raw:
            return format_summary_pattern(raw, context)
        return raw
    if raw:
        return default_summary(spec, resource_type, context)
    return default_summary(spec, resource_type, context)


def build_runbook(
    spec: dict[str, Any],
    resource_type: str,
    name: str,
    region: str,
    ecs_identity: dict[str, str | None],
) -> str:
    if spec.get("runbook"):
        return spec["runbook"]

    cluster = ecs_identity.get("cluster_name") or ""
    service = ecs_identity.get("service_name")
    if resource_type == "ecs_service" and service:
        return (
            f"Check ECS service {service} on cluster {cluster} in {region}. "
            "Review dashboard, recent deploys, and scaling."
        )
    if resource_type == "ecs" and cluster:
        return (
            f"Check ECS cluster {cluster} in {region}. "
            "Review dashboard, recent deploys, and scaling."
        )
    return (
        f"Check {resource_type} {name} in {region}. "
        "Review dashboard, recent deploys, and scaling."
    )


def format_threshold_annotation(evaluator: str, threshold_val: float | int | str) -> str:
    """Short human-readable threshold for Power Automate Adaptive Cards (unit-agnostic)."""
    ev = (evaluator or "gt").lower()
    try:
        num = float(threshold_val)
        if num.is_integer():
            display: str | float | int = int(num)
        else:
            display = round(num, 4)
    except (TypeError, ValueError):
        display = threshold_val

    if ev in ("gt", "gte"):
        return f"Above {display}"
    if ev in ("lt", "lte"):
        return f"Below {display}"
    if ev == "within_range":
        return f"Within range {display}"
    if ev == "outside_range":
        return f"Outside range {display}"
    return f"{ev} {display}"


def format_bytes_threshold_value(num: float) -> str:
    """Readable byte threshold (GiB/MiB/KiB/B), matching Adaptive Card Current value ladder."""
    x = float(num)
    if x >= 1073741824:
        return f"{x / 1073741824:.2f} GB"
    if x >= 1048576:
        return f"{x / 1048576:.2f} MB"
    if x >= 1024:
        return f"{x / 1024:.2f} KB"
    if float(x).is_integer():
        return f"{int(x)} B"
    return f"{x:.2f} B"


def append_unit_label(threshold: str, unit_label: str) -> str:
    """Append `` {unit_label}`` idempotently (safe to re-run without doubling)."""
    label = (unit_label or "").strip()
    text = (threshold or "").rstrip()
    if not label:
        return text
    suffix = f" {label}"
    while text.endswith(suffix):
        text = text[: -len(suffix)].rstrip()
    return f"{text}{suffix}"


def format_threshold_display(
    evaluator: str,
    threshold_val: float | int | str,
    *,
    unit: str = "unknown",
    unit_label: str = "",
    metric: str | None = None,
) -> str:
    """Unit-aware threshold display for Adaptive Cards (annotations.threshold)."""
    ev = (evaluator or "gt").lower()
    metric_name = (metric or "").strip()
    label = (unit_label or "").strip()

    try:
        num = float(threshold_val)
    except (TypeError, ValueError):
        return format_threshold_annotation(evaluator, threshold_val)

    if metric_name == "UnHealthyHostCount" and ev == "gt":
        n = int(math.floor(num)) + 1
        word = "unhealthy target" if n == 1 else "unhealthy targets"
        return f"At least {n} {word}"

    if unit == "percent":
        display = f"{int(num)}%" if num.is_integer() else f"{round(num, 2)}%"
        if ev in ("gt", "gte"):
            return f"Above {display}"
        if ev in ("lt", "lte"):
            return f"Below {display}"
        return f"{ev} {display}"

    if unit == "seconds":
        display = f"{int(num)} s" if num.is_integer() else f"{round(num, 2)} s"
        if ev in ("gt", "gte"):
            return f"Above {display}"
        if ev in ("lt", "lte"):
            return f"Below {display}"
        return f"{ev} {display}"

    if unit == "bytes":
        display = format_bytes_threshold_value(num)
        if ev in ("gt", "gte"):
            return f"Above {display}"
        if ev in ("lt", "lte"):
            return f"Below {display}"
        return f"{ev} {display}"

    base = format_threshold_annotation(evaluator, threshold_val)
    return append_unit_label(base, label)


def unit_for_metric(metric_name: str | None) -> str:
    """Map a CloudWatch metric name to a display unit. Default is unknown."""
    if not metric_name:
        return "unknown"
    return _METRIC_UNITS.get(str(metric_name).strip(), "unknown")


def unit_label_for_metric(metric_name: str | None) -> str:
    """Human-readable unit word for Adaptive Cards. Empty when not applicable."""
    if not metric_name:
        return ""
    return _METRIC_UNIT_LABELS.get(str(metric_name).strip(), "")


def unit_annotation_from_spec(spec: dict[str, Any]) -> str:
    """Derive unit for a compiled rule spec."""
    if spec.get("query_mode") == "metric_math_ratio" and spec.get("metric") == "RabbitMQMemUsed":
        return "percent"
    return unit_for_metric(spec.get("metric"))


def unit_label_annotation_from_spec(spec: dict[str, Any]) -> str:
    """Derive unit_label for a compiled rule spec."""
    return unit_label_for_metric(spec.get("metric"))


def metric_annotation_from_spec(spec: dict[str, Any]) -> str:
    """Human-readable metric name from a rule definition spec."""
    if spec.get("metric_display"):
        return str(spec["metric_display"])
    metric = spec.get("metric")
    if metric:
        return str(metric)
    return "Grafana alert"


def threshold_annotation_from_spec(spec: dict[str, Any], resource: dict[str, Any], resolve_threshold) -> str:
    """Human-readable threshold from a rule definition spec.

    ``resolve_threshold`` is injected to avoid a circular import with compiler.
    """
    if spec.get("threshold_display"):
        return str(spec["threshold_display"])
    explanation = spec.get("threshold_explanation")
    if explanation and not explanation.startswith(("gt ", "lt ", "gte ", "lte ")):
        return str(explanation)
    threshold_val, evaluator = resolve_threshold(spec, resource)
    return format_threshold_display(
        evaluator,
        threshold_val,
        unit=unit_annotation_from_spec(spec),
        unit_label=unit_label_annotation_from_spec(spec),
        metric=str(spec.get("metric") or ""),
    )
