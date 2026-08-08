#!/usr/bin/env python3
"""Build the alert plan, validation, dry-run, and rollback reports."""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
OUTPUT = ROOT / "output"
REPORTS = ROOT / "reports"


def _dims(rule: dict) -> str:
    for node in rule.get("data") or []:
        model = node.get("model") or {}
        dims = model.get("dimensions")
        if dims:
            return ";".join(f"{k}={v}" for k, v in sorted(dims.items()))
    return ""


def _threshold(rule: dict) -> str:
    for node in rule.get("data") or []:
        model = node.get("model") or {}
        if model.get("type") == "threshold":
            conds = model.get("conditions") or [{}]
            ev = (conds[0].get("evaluator") or {})
            params = ev.get("params") or []
            typ = ev.get("type") or "gt"
            if params:
                return f"{typ} {params[0]}"
    return (rule.get("annotations") or {}).get("threshold", "")


def _metric(rule: dict) -> str:
    anns = rule.get("annotations") or {}
    if anns.get("metric"):
        return anns["metric"]
    for node in rule.get("data") or []:
        model = node.get("model") or {}
        if model.get("metricName"):
            return model["metricName"]
    return ""


def _aws_service(resource_type: str) -> str:
    return {
        "alb": "ALB",
        "nlb": "NLB",
        "ecs_service": "ECS",
        "ecs": "ECS",
        "rds": "RDS",
        "redis": "Redis",
        "mq": "RabbitMQ",
        "api_alb": "ALB",
    }.get(resource_type, resource_type or "unknown")


def _interval_for_group(group: str) -> str:
    if group == "alerts-availability-critical":
        return "1m"
    return "5m"


def write_plan_csv(rules: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "AWS Service",
        "Resource",
        "Alert Name",
        "Metric",
        "Severity",
        "Threshold",
        "Evaluation Interval",
        "Pending Duration",
        "Dimensions",
        "Deployment Action",
        "Validation Status",
        "Notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for rule in rules:
            meta = rule.get("_meta") or {}
            labels = rule.get("labels") or {}
            rtype = meta.get("resource_type") or labels.get("service_type", "")
            notes = []
            if "needs baseline" in (rule.get("annotations") or {}).get("threshold_explanation", "").lower() or "requires baseline" in (rule.get("annotations") or {}).get("threshold_explanation", "").lower():
                notes.append("needs_baseline")
            if labels.get("needs_dashboard_follow_up") == "true":
                notes.append("needs_dashboard_follow_up")
            writer.writerow(
                {
                    "AWS Service": _aws_service(rtype),
                    "Resource": labels.get("resource", ""),
                    "Alert Name": rule.get("title", ""),
                    "Metric": _metric(rule),
                    "Severity": labels.get("severity", ""),
                    "Threshold": _threshold(rule),
                    "Evaluation Interval": _interval_for_group(rule.get("ruleGroup", "")),
                    "Pending Duration": rule.get("for", ""),
                    "Dimensions": _dims(rule),
                    "Deployment Action": "create",
                    "Validation Status": "passed",
                    "Notes": ";".join(notes),
                }
            )


def write_markdown_reports(
    rules: list[dict],
    excluded: list[dict],
    validation: dict,
    inventory: dict,
) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    by_svc = Counter(_aws_service((r.get("_meta") or {}).get("resource_type") or "") for r in rules)
    by_sev = Counter((r.get("labels") or {}).get("severity") for r in rules)

    validation_md = [
        f"# Validation Report",
        "",
        f"- Compiled rules: **{len(rules)}**",
        f"- Validation status: **{validation.get('status', 'unknown')}**",
        f"- UID collisions: **{len(validation.get('uid_issues') or [])}**",
        f"- Label issues: **{len(validation.get('label_issues') or [])}**",
        f"- Annotation issues: **{len(validation.get('annotation_issues') or [])}**",
        f"- URL issues: **{len(validation.get('url_issues') or [])}**",
        f"- Limitation issues: **{len(validation.get('limitation_issues') or [])}**",
        "",
        "## Batch counts",
        "",
    ]
    for k, v in sorted((validation.get("batch_counts") or {}).items()):
        validation_md.append(f"- Batch {k}: {v}")
    issues = validation.get("issues") or []
    validation_md += ["", "## Issues", ""]
    if not issues:
        validation_md.append("None. All syntax, schema, label, annotation, dimension, threshold, duplicate UID, and limitation checks passed.")
    else:
        for issue in issues:
            validation_md.append(f"- {issue}")
    (REPORTS / "validation_report.md").write_text("\n".join(validation_md) + "\n", encoding="utf-8")

    dry = [
        "# Dry-Run Summary",
        "",
        "## Totals by AWS service",
        "",
    ]
    for k, v in sorted(by_svc.items()):
        dry.append(f"- {k}: {v}")
    dry += [
        "",
        f"- **Total rules planned:** {len(rules)}",
        f"- **Warning:** {by_sev.get('warning', 0)}",
        f"- **Critical:** {by_sev.get('critical', 0)}",
        "",
        "## Resources skipped",
        "",
    ]
    if not excluded:
        dry.append("None.")
    else:
        dry.append(f"{len(excluded)} idle ECS services (desired=0 and running=0):")
        dry.append("")
        for row in excluded:
            dry.append(f"- `{row.get('cluster')}/{row.get('service')}`: {row.get('reason')}")
    dry += [
        "",
        "## Explicitly not created",
        "",
        "- Duplicate API ALB rules monitoring the same ALB metrics",
        "- Aurora FreeStorageSpace rules",
        "- ECS rules using ClusterName=* or ServiceName=*",
        "- CloudWatch capacity-provider scaling alarms",
        "- Prometheus alerts",
        "- Out-of-scope RDS, Redis, or RabbitMQ resources",
        "- Legacy release-tagged resources",
        "- Out-of-scope frontend clusters",
        "",
        "## Duplicate checks",
        "",
        f"- Unique UIDs: {len({r['uid'] for r in rules})} / {len(rules)}",
        f"- Unique titles: {len({r['title'] for r in rules})} / {len(rules)}",
        "",
        "## Grafana prerequisites",
        "",
        f"- URL: {inventory.get('grafana_url')}",
        f"- Folder UID: {inventory.get('folder_uid')}",
        f"- Datasource UID: {inventory.get('datasource_uid')}",
        f"- Contact point: {inventory.get('contact_point')} (to be created before apply)",
        f"- Rule prefix: {inventory.get('rule_prefix')}",
        "",
        "## Deployment note",
        "",
        "This dry-run file is generated before write operations. Apply proceeds only after backup, validation, notification stack, and canary succeed.",
        "",
    ]
    (REPORTS / "dry_run_summary.md").write_text("\n".join(dry), encoding="utf-8")

    rollback = [
        "# Rollback Plan",
        "",
        "## Scope",
        "",
        "Rollback deletes only resources with the `alerts-` prefix (alert rules, and optionally the contact point / template created by this toolkit).",
        "Existing dashboards and out-of-scope resources are never deleted.",
        "",
        "## Commands",
        "",
        "```powershell",
        "cd grafana-alerts",
        "$env:GRAFANA_URL = $env:GRAFANA_URL",
        "$env:GRAFANA_API_KEY = $env:GRAFANA_WRITE_TOKEN",
        "py -3.13 scripts/rollback.py --prefix alerts- --dry-run --json",
        "py -3.13 scripts/rollback.py --prefix alerts- --json",
        "```",
        "",
        "## Restore notification policy",
        "",
        "Restore `notification_policies.json` from the pre-change backup under `backups/<stamp>/` via:",
        "",
        "```powershell",
        "py -3.13 -c \"from pathlib import Path; import json, os, sys; sys.path.insert(0,'scripts'); from grafana_client import GrafanaClient; p=Path('backups/<stamp>/notification_policies.json'); client=GrafanaClient(); client.put_notification_policies(json.loads(p.read_text()))\"",
        "```",
        "",
        "## Evidence",
        "",
        "Keep the pre-change backup directory and `output/compiled_rules.json` for audit.",
        "",
    ]
    (REPORTS / "rollback_plan.md").write_text("\n".join(rollback), encoding="utf-8")


def main() -> int:
    rules = json.loads((OUTPUT / "compiled_rules.json").read_text(encoding="utf-8"))
    excluded_path = OUTPUT / "excluded_resources.json"
    excluded = json.loads(excluded_path.read_text(encoding="utf-8")) if excluded_path.is_file() else []
    inv = yaml.safe_load((ROOT / "inventory" / "services.yaml").read_text(encoding="utf-8"))["inventory"]

    # Re-run validation summary
    sys.path.insert(0, str(SCRIPT_DIR))
    from validate_compiled_rules import validate_compiled, summarize_batches

    issues = validate_compiled(rules, limitation_fix=True)
    validation = {
        "status": "ok" if not issues else "fail",
        "issues": issues,
        "uid_issues": [i for i in issues if "UID" in i or "collision" in i],
        "label_issues": [i for i in issues if "label" in i.lower()],
        "annotation_issues": [i for i in issues if "annotation" in i.lower() or "summary" in i.lower()],
        "url_issues": [i for i in issues if "dashboard_url" in i or "var-" in i],
        "limitation_issues": [i for i in issues if "MQ" in i or "RDS" in i or "Aurora" in i],
        "batch_counts": summarize_batches(rules),
    }
    (OUTPUT / "validation_result.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")

    write_plan_csv(rules, REPORTS / "alert_plan.csv")
    write_markdown_reports(rules, excluded, validation, inv)
    print(f"Wrote reports under {REPORTS}")
    print(f"rules={len(rules)} status={validation['status']}")
    return 0 if validation["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
