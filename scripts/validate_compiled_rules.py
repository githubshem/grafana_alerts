#!/usr/bin/env python3
"""Validate compiled Grafana alert rules against standardization policy."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REQUIRED_LABELS = {
    "team",
    "region",
    "managed_by",
    "service_type",
    "severity",
    "resource",
}

ECS_EXTRA_LABELS = {"ClusterName", "ServiceName"}


def load_rules(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resource_type(rule: dict) -> str:
    meta = rule.get("_meta") or {}
    return meta.get("resource_type") or rule.get("labels", {}).get("service_type", "")


def validate_labels(rules: list[dict]) -> list[str]:
    issues: list[str] = []
    for rule in rules:
        labels = rule.get("labels") or {}
        missing = REQUIRED_LABELS - set(labels)
        if missing:
            issues.append(f"{rule['title']}: missing labels {sorted(missing)}")

        resource_type = _resource_type(rule)
        if resource_type in ("ecs", "ecs_service"):
            if not labels.get("ClusterName"):
                issues.append(f"{rule['title']}: missing ECS label ClusterName")
            if resource_type == "ecs_service" and not labels.get("ServiceName"):
                issues.append(f"{rule['title']}: missing ECS label ServiceName")
    return issues


def validate_annotations(rules: list[dict]) -> list[str]:
    issues: list[str] = []
    for rule in rules:
        anns = rule.get("annotations") or {}
        summary = anns.get("summary", "")
        if "{{ $labels" in summary:
            issues.append(f"{rule['title']}: summary still uses Grafana label templates")
        if "[no value]" in summary:
            issues.append(f"{rule['title']}: summary contains [no value]")
        if not (anns.get("metric") or "").strip():
            issues.append(f"{rule['title']}: missing annotations.metric")
        if not (anns.get("threshold") or "").strip():
            issues.append(f"{rule['title']}: missing annotations.threshold")
        url = anns.get("dashboard_url") or ""
        if "localhost" in url or "127.0.0.1" in url:
            issues.append(f"{rule['title']}: dashboard_url contains localhost/127.0.0.1")
        # Never require N/A placeholder labels (fingerprint churn).
        labels = rule.get("labels") or {}
        for key in ("ClusterName", "ServiceName"):
            if labels.get(key) == "N/A":
                issues.append(f"{rule['title']}: label {key}=N/A is prohibited")
    return issues


def validate_dashboard_urls(rules: list[dict], *, limitation_fix: bool = False) -> list[str]:
    issues: list[str] = []
    for rule in rules:
        url = (rule.get("annotations") or {}).get("dashboard_url", "")
        if not url:
            continue

        resource_type = _resource_type(rule)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        cluster_vals = query.get("var-cluster", [])
        service_vals = query.get("var-service", [])
        cluster = cluster_vals[0] if cluster_vals else ""
        service = service_vals[0] if service_vals else ""

        if resource_type == "ecs":
            if not cluster:
                issues.append(f"{rule['title']}: ECS cluster rule missing var-cluster in dashboard_url")
            if service in ("*", "%2A"):
                issues.append(f"{rule['title']}: ECS cluster rule must not use var-service=*")
            if service and service == cluster:
                issues.append(f"{rule['title']}: ECS cluster rule must not set var-service to cluster name")
        elif resource_type == "ecs_service":
            if not cluster or not service:
                issues.append(f"{rule['title']}: ECS service rule missing var-cluster or var-service in dashboard_url")
            if service in ("*", "%2A"):
                issues.append(f"{rule['title']}: ECS service rule must not use var-service=*")

        if limitation_fix and resource_type in ("alb", "api_alb") and rule.get("ruleGroup", "").endswith("-api"):
            path_parts = parsed.path.split("/")
            if path_parts and path_parts[-1] == "api":
                issues.append(f"{rule['title']}: API dashboard URL uses bare slug 'api'")
            meta = rule.get("_meta") or {}
            if meta.get("primary_target_group_cw") and not query.get("var-targetgroup"):
                issues.append(f"{rule['title']}: API rule missing var-targetgroup despite TG mapping")
    return issues


def validate_limitation_fixes(rules: list[dict]) -> list[str]:
    issues: list[str] = []
    memory_thresholds: dict[str, list[float]] = {}
    for rule in rules:
        title = rule["title"]
        meta = rule.get("_meta") or {}
        if "freeable-memory" in title:
            threshold = None
            for node in rule.get("data", []):
                model = node.get("model") or {}
                if model.get("type") == "threshold":
                    params = model.get("conditions", [{}])[0].get("evaluator", {}).get("params", [])
                    if params:
                        threshold = float(params[0])
            if threshold is not None:
                memory_thresholds.setdefault("values", []).append(threshold)
        if "mq-memory" in title:
            metrics = []
            ref_ids: list[str] = []
            has_cw_math = False
            has_grafana_math = False
            broker_dim = None
            threshold_val = None
            for node in rule.get("data", []):
                ref_ids.append(node.get("refId", ""))
                model = node.get("model") or {}
                metric = model.get("metricName")
                if metric:
                    metrics.append(metric)
                if model.get("metricQueryType") == 1:
                    has_cw_math = True
                if model.get("type") == "math":
                    has_grafana_math = True
                dims = model.get("dimensions") or {}
                if "Broker" in dims:
                    broker_dim = dims["Broker"]
                if model.get("type") == "threshold" and node.get("refId") == "F":
                    params = model.get("conditions", [{}])[0].get("evaluator", {}).get("params", [])
                    if params:
                        threshold_val = float(params[0])
            if "RabbitMQMemUsed" not in metrics or "RabbitMQMemLimit" not in metrics:
                issues.append(f"{title}: MQ memory must use RabbitMQMemUsed + RabbitMQMemLimit")
            if has_cw_math:
                issues.append(f"{title}: MQ memory must not use CloudWatch metric math (metricQueryType 1)")
            if not has_grafana_math:
                issues.append(f"{title}: MQ memory must use Grafana __expr__ math")
            if ref_ids != ["A", "B", "C", "D", "E", "F"]:
                issues.append(f"{title}: MQ memory refId chain must be A-F, got {ref_ids}")
            elif len(set(ref_ids)) != len(ref_ids):
                issues.append(f"{title}: MQ memory has duplicate refIds")
            if rule.get("condition") != "F":
                issues.append(f"{title}: MQ memory condition must be F, got {rule.get('condition')}")
            if not broker_dim:
                issues.append(f"{title}: MQ memory must use Broker dimension")
            if "warning" in title and threshold_val != 70.0:
                issues.append(f"{title}: MQ memory warning threshold must be 70, got {threshold_val}")
            if "critical" in title and threshold_val != 85.0:
                issues.append(f"{title}: MQ memory critical threshold must be 85, got {threshold_val}")
        if "free-storage" in title and not rule.get("isPaused"):
            if meta.get("aurora_auto_scaling_storage"):
                issues.append(f"{title}: active Aurora storage rule should be paused or skipped")
    values = memory_thresholds.get("values", [])
    if len(values) >= 2 and len(set(values)) < 2:
        issues.append("RDS freeable-memory thresholds are flat across clusters (expected per-class baselines)")
    return issues


def validate_uids(rules: list[dict]) -> list[str]:
    issues: list[str] = []
    seen: dict[str, str] = {}
    for rule in rules:
        uid = rule["uid"]
        if uid in seen:
            issues.append(f"UID collision {uid}: {seen[uid]} vs {rule['title']}")
        seen[uid] = rule["title"]
    return issues


def summarize_batches(rules: list[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for rule in rules:
        batch = (rule.get("labels") or {}).get("apply_batch", "unlabeled")
        counts[str(batch)] += 1
    return dict(counts)


def validate_compiled(rules: list[dict], *, limitation_fix: bool = False) -> list[str]:
    issues: list[str] = []
    issues.extend(validate_labels(rules))
    issues.extend(validate_annotations(rules))
    issues.extend(validate_dashboard_urls(rules, limitation_fix=limitation_fix))
    issues.extend(validate_uids(rules))
    if limitation_fix:
        issues.extend(validate_limitation_fixes(rules))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate compiled alert rules JSON")
    parser.add_argument("--compiled", type=Path, required=True)
    parser.add_argument("--env", default="default")
    parser.add_argument("--limitation-fix", action="store_true", help="Apply limitation-fix validation checks")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rules = load_rules(args.compiled)
    label_issues = validate_labels(rules)
    annotation_issues = validate_annotations(rules)
    url_issues = validate_dashboard_urls(rules, limitation_fix=args.limitation_fix)
    uid_issues = validate_uids(rules)
    limitation_issues = validate_limitation_fixes(rules) if args.limitation_fix else []
    all_issues = label_issues + annotation_issues + url_issues + uid_issues + limitation_issues
    batches = summarize_batches(rules)

    result = {
        "status": "ok" if not all_issues else "fail",
        "rule_count": len(rules),
        "batch_counts": batches,
        "label_issues": label_issues,
        "annotation_issues": annotation_issues,
        "url_issues": url_issues,
        "limitation_issues": limitation_issues,
        "uid_issues": uid_issues,
        "issues": all_issues,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Rules: {len(rules)}")
        print(f"Batches: {batches}")
        if all_issues:
            print("Issues:")
            for issue in all_issues[:30]:
                print(f"  - {issue}")

    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
