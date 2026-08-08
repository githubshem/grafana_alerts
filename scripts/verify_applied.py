#!/usr/bin/env python3
"""Verify live rules against compiled source and write post-deploy reports."""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from config import CONTACT_POINT, RULE_PREFIX
from grafana_client import GrafanaClient


def _threshold(rule: dict) -> str:
    for node in rule.get("data") or []:
        model = node.get("model") or {}
        if model.get("type") == "threshold":
            conds = model.get("conditions") or [{}]
            ev = conds[0].get("evaluator") or {}
            params = ev.get("params") or []
            typ = ev.get("type") or "gt"
            if params:
                val = params[0]
                try:
                    num = float(val)
                    if num.is_integer():
                        val = int(num)
                    else:
                        val = num
                except (TypeError, ValueError):
                    pass
                return f"{typ} {val}"
    return ""


def _dims(rule: dict) -> str:
    for node in rule.get("data") or []:
        model = node.get("model") or {}
        dims = model.get("dimensions")
        if dims:
            return ";".join(f"{k}={v}" for k, v in sorted(dims.items()))
    return ""


def _metric(rule: dict) -> str:
    anns = rule.get("annotations") or {}
    if anns.get("metric"):
        return anns["metric"]
    for node in rule.get("data") or []:
        model = node.get("model") or {}
        if model.get("metricName"):
            return model["metricName"]
    return ""


def main() -> int:
    compiled = json.loads((ROOT / "output" / "compiled_rules.json").read_text(encoding="utf-8"))
    live_path = ROOT / "output" / "live_rules_after.json"
    if live_path.is_file():
        live = json.loads(live_path.read_text(encoding="utf-8"))
    else:
        client = GrafanaClient(read_only=True)
        live = client.list_alert_rules()
        live_path.write_text(json.dumps(live, indent=2), encoding="utf-8")

    live_pref = [r for r in live if str(r.get("title", "")).startswith(RULE_PREFIX)]
    compiled_by_uid = {r["uid"]: r for r in compiled}
    live_by_uid = {r["uid"]: r for r in live_pref}

    issues = []
    if len(live_pref) != len(compiled):
        issues.append(f"count mismatch live={len(live_pref)} compiled={len(compiled)}")
    missing = set(compiled_by_uid) - set(live_by_uid)
    extra = set(live_by_uid) - set(compiled_by_uid)
    if missing:
        issues.append(f"missing live uids: {len(missing)}")
    if extra:
        issues.append(f"extra live uids: {len(extra)}")
    if len({r["uid"] for r in live_pref}) != len(live_pref):
        issues.append("duplicate live uids")
    if len({r["title"] for r in live_pref}) != len(live_pref):
        issues.append("duplicate live titles")

    mismatch_thresholds = 0
    mismatch_receivers = 0
    for uid, src in compiled_by_uid.items():
        dst = live_by_uid.get(uid)
        if not dst:
            continue
        if _threshold(src) != _threshold(dst):
            mismatch_thresholds += 1
        recv = (dst.get("notification_settings") or {}).get("receiver")
        if recv != CONTACT_POINT:
            mismatch_receivers += 1
    if mismatch_thresholds:
        issues.append(f"threshold mismatches: {mismatch_thresholds}")
    if mismatch_receivers:
        issues.append(f"receiver mismatches: {mismatch_receivers}")

    apply_result = {}
    apply_path = ROOT / "output" / "apply_result.json"
    if apply_path.is_file():
        apply_result = json.loads(apply_path.read_text(encoding="utf-8"))

    # applied_alerts.csv
    reports = ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    action_by_group = {r.get("group"): r for r in (apply_result.get("results") or [])}
    fields = [
        "UID",
        "Title",
        "Rule Group",
        "AWS Service",
        "Resource",
        "Metric",
        "Severity",
        "Threshold",
        "Evaluation Interval",
        "Pending Duration",
        "Dimensions",
        "Receiver",
        "Apply Action",
        "Verification Status",
    ]
    type_map = {
        "alb": "ALB",
        "nlb": "NLB",
        "ecs": "ECS",
        "ecs_service": "ECS",
        "rds": "RDS",
        "redis": "Redis",
        "mq": "RabbitMQ",
    }
    with (reports / "applied_alerts.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for rule in compiled:
            labels = rule.get("labels") or {}
            meta = rule.get("_meta") or {}
            rtype = meta.get("resource_type") or labels.get("service_type") or ""
            group = rule.get("ruleGroup", "")
            group_res = action_by_group.get(group) or {}
            interval = "1m" if group == "alerts-availability-critical" else "5m"
            writer.writerow(
                {
                    "UID": rule["uid"],
                    "Title": rule["title"],
                    "Rule Group": group,
                    "AWS Service": type_map.get(rtype, rtype),
                    "Resource": labels.get("resource", ""),
                    "Metric": _metric(rule),
                    "Severity": labels.get("severity", ""),
                    "Threshold": _threshold(rule),
                    "Evaluation Interval": interval,
                    "Pending Duration": rule.get("for", ""),
                    "Dimensions": _dims(rule),
                    "Receiver": CONTACT_POINT,
                    "Apply Action": group_res.get("action", "put"),
                    "Verification Status": "verified" if rule["uid"] in live_by_uid else "missing",
                }
            )

    by_svc = Counter(type_map.get((r.get("_meta") or {}).get("resource_type") or (r.get("labels") or {}).get("service_type"), "other") for r in compiled)
    by_sev = Counter((r.get("labels") or {}).get("severity") for r in compiled)
    canary = {}
    canary_path = ROOT / "output" / "canary_result.json"
    if canary_path.is_file():
        canary = json.loads(canary_path.read_text(encoding="utf-8"))
    notif = {}
    notif_path = ROOT / "output" / "notification_stack_result.json"
    if notif_path.is_file():
        notif = json.loads(notif_path.read_text(encoding="utf-8"))
    excluded = []
    excl_path = ROOT / "output" / "excluded_resources.json"
    if excl_path.is_file():
        excluded = json.loads(excl_path.read_text(encoding="utf-8"))

    md = [
        "# Post-Deployment Report",
        "",
        "## Outcome",
        "",
        f"- Status: **{'PASS' if not issues else 'FAIL'}**",
        f"- Live rules: **{len(live_pref)}**",
        f"- Compiled rules: **{len(compiled)}**",
        f"- Applied groups: **{apply_result.get('applied_groups', 0)}**",
        f"- Apply errors: **{len(apply_result.get('errors') or [])}**",
        "",
        "## Totals by AWS service",
        "",
    ]
    for k, v in sorted(by_svc.items()):
        md.append(f"- {k}: {v}")
    md += [
        "",
        f"- Warning: {by_sev.get('warning', 0)}",
        f"- Critical: {by_sev.get('critical', 0)}",
        "",
        "## Prerequisites provisioned",
        "",
        f"- Notification template: `{((notif.get('template') or {}).get('name'))}` action={(notif.get('template') or {}).get('action')}",
        f"- Contact point: `{((notif.get('contact_point') or {}).get('name'))}` uid={(notif.get('contact_point') or {}).get('uid')} action={(notif.get('contact_point') or {}).get('action')}",
        f"- Notification policy: action={(notif.get('policy') or {}).get('action')} routes={(notif.get('policy') or {}).get('routes')}",
        "",
        "## Canary",
        "",
        f"- Passed: {canary.get('canary_passed')}",
        f"- Title: {canary.get('title')}",
        f"- Created/verified/deleted: created={canary.get('created')} verified={canary.get('verified_exists')} deleted={canary.get('deleted')}",
        f"- Notes: {canary.get('notes')}",
        "",
        "## Verification checks",
        "",
        f"- Unique UIDs: {len({r['uid'] for r in live_pref})} / {len(live_pref)}",
        f"- Unique titles: {len({r['title'] for r in live_pref})} / {len(live_pref)}",
        f"- Missing vs compiled: {len(missing)}",
        f"- Extra vs compiled: {len(extra)}",
        f"- Threshold mismatches: {mismatch_thresholds}",
        f"- Receiver mismatches: {mismatch_receivers}",
        f"- All receivers {CONTACT_POINT}: {mismatch_receivers == 0}",
        "",
        "## Skipped resources",
        "",
    ]
    if not excluded:
        md.append("None.")
    else:
        md.append(f"{len(excluded)} idle ECS services excluded:")
        for row in excluded:
            md.append(f"- `{row.get('cluster')}/{row.get('service')}`: {row.get('reason')}")
    md += [
        "",
        "## Issues",
        "",
    ]
    if not issues:
        md.append("None.")
    else:
        for issue in issues:
            md.append(f"- {issue}")
    md += [
        "",
        "## Artifacts",
        "",
        "- `output/compiled_rules.json`",
        "- `output/rule_groups/`",
        "- `output/live_rules_after.json`",
        "- `output/apply_result.json`",
        "- `output/canary_result.json`",
        "- `output/notification_stack_result.json`",
        "- `reports/alert_plan.csv`",
        "- `reports/applied_alerts.csv`",
        "- `reports/validation_report.md`",
        "- `reports/dry_run_summary.md`",
        "- `reports/rollback_plan.md`",
        "- `backups/<stamp>/`",
        "",
    ]
    (reports / "post_deployment_report.md").write_text("\n".join(md), encoding="utf-8")
    summary = {
        "status": "ok" if not issues else "error",
        "live": len(live_pref),
        "compiled": len(compiled),
        "issues": issues,
    }
    print(json.dumps(summary, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
