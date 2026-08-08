"""CloudWatch and expression query builders for Grafana alert rules."""

from __future__ import annotations

from typing import Any

from config import DATASOURCE_UID, REGION, BuildContext
from rulebuilder.resources import build_dimensions


def cloudwatch_query(
    ref_id: str,
    namespace: str,
    metric_name: str,
    statistic: str,
    period: int,
    dimensions: dict[str, str],
    *,
    ctx: BuildContext | None = None,
    expression: str | None = None,
    match_exact: bool = True,
    query_id: str | None = None,
    metric_query_type: int = 0,
) -> dict[str, Any]:
    period_str = f"{period // 60}m" if period >= 60 else f"{period}s"
    ds_uid = ctx.datasource_uid if ctx else DATASOURCE_UID
    region = ctx.region if ctx else REGION
    model: dict[str, Any] = {
        "refId": ref_id,
        "datasource": {"type": "cloudwatch", "uid": ds_uid},
        "dimensions": dimensions,
        "expression": expression or "",
        "id": "",
        "intervalMs": 1000,
        "label": "",
        "logGroups": [],
        "matchExact": match_exact,
        "maxDataPoints": 43200,
        "metricEditorMode": 0,
        "metricName": metric_name,
        "metricQueryType": metric_query_type,
        "namespace": namespace,
        "period": period_str,
        "queryLanguage": "CWLI",
        "queryMode": "Metrics",
        "region": region,
        "sqlExpression": "",
        "statistic": statistic,
    }
    if query_id:
        model["id"] = query_id
    return {
        "refId": ref_id,
        "queryType": "",
        "relativeTimeRange": {"from": 600, "to": 0},
        "datasourceUid": ds_uid,
        "model": model,
    }


def math_expression(expression: str, ref_id: str = "E") -> dict[str, Any]:
    return {
        "refId": ref_id,
        "queryType": "expression",
        "relativeTimeRange": {"from": 0, "to": 0},
        "datasourceUid": "__expr__",
        "model": {
            "refId": ref_id,
            "type": "math",
            "datasource": {"type": "__expr__", "uid": "__expr__"},
            "expression": expression,
            "intervalMs": 1000,
            "maxDataPoints": 43200,
        },
    }


def threshold_condition(
    input_ref: str,
    threshold: float,
    *,
    ref_id: str = "C",
    reducer: str = "last",
    evaluator: str = "gt",
) -> dict[str, Any]:
    return {
        "refId": ref_id,
        "queryType": "expression",
        "relativeTimeRange": {"from": 0, "to": 0},
        "datasourceUid": "__expr__",
        "model": {
            "refId": ref_id,
            "type": "threshold",
            "datasource": {"type": "__expr__", "uid": "__expr__"},
            "conditions": [
                {
                    "type": "query",
                    "query": {"params": [ref_id]},
                    "reducer": {"type": "last", "params": []},
                    "evaluator": {"type": evaluator, "params": [threshold]},
                    "operator": {"type": "and"},
                },
            ],
            "expression": input_ref,
            "intervalMs": 1000,
            "maxDataPoints": 43200,
        },
    }


def reduce_condition(
    source_ref: str = "A",
    reducer: str = "mean",
    *,
    ref_id: str = "B",
) -> dict[str, Any]:
    return {
        "refId": ref_id,
        "queryType": "expression",
        "relativeTimeRange": {"from": 0, "to": 0},
        "datasourceUid": "__expr__",
        "model": {
            "refId": ref_id,
            "type": "reduce",
            "datasource": {"type": "__expr__", "uid": "__expr__"},
            "expression": source_ref,
            "reducer": reducer,
            "intervalMs": 1000,
            "maxDataPoints": 43200,
            "conditions": [
                {
                    "type": "query",
                    "query": {"params": []},
                    "reducer": {"type": "last", "params": []},
                    "evaluator": {"type": "gt", "params": []},
                    "operator": {"type": "and"},
                },
            ],
        },
    }


def build_query_data(
    spec: dict[str, Any],
    resource: dict[str, Any],
    resource_type: str,
    name: str,
    *,
    ctx: BuildContext | None,
    period: int,
    reducer: str,
    threshold_val: float,
    evaluator: str,
) -> list[dict[str, Any]]:
    dimensions = build_dimensions(spec, resource, resource_type, name)

    if spec.get("query_mode") == "metric_math_ratio":
        metric_used = spec.get("metric_ratio_numerator", spec["metric"])
        metric_limit = spec.get("metric_ratio_denominator", "RabbitMQMemLimit")
        math_expr = spec.get("metric_ratio_expression", "($C / $D) * 100")
        return [
            cloudwatch_query(
                "A",
                spec["namespace"],
                metric_used,
                spec.get("statistic", "Average"),
                period,
                dimensions,
                ctx=ctx,
                match_exact=spec.get("match_exact", True),
            ),
            cloudwatch_query(
                "B",
                spec["namespace"],
                metric_limit,
                spec.get("statistic", "Average"),
                period,
                dimensions,
                ctx=ctx,
                match_exact=spec.get("match_exact", True),
            ),
            reduce_condition("A", reducer=reducer, ref_id="C"),
            reduce_condition("B", reducer=reducer, ref_id="D"),
            math_expression(math_expr, ref_id="E"),
            threshold_condition("E", threshold_val, ref_id="F", evaluator=evaluator),
        ]

    return [
        cloudwatch_query(
            "A",
            spec["namespace"],
            spec["metric"],
            spec.get("statistic", "Average"),
            period,
            dimensions,
            ctx=ctx,
            match_exact=spec.get("match_exact", True),
        ),
        reduce_condition("A", reducer=reducer, ref_id="B"),
        threshold_condition("B", threshold_val, ref_id="C", evaluator=evaluator),
    ]
