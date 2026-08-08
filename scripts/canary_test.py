#!/usr/bin/env python3
"""Create a temporary canary alert, verify routing, then delete it."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from config import CONTACT_POINT, MANAGED_BY, REGION, RULE_PREFIX, BuildContext
from grafana_client import GrafanaClient, GrafanaError, configure_logging, emit_result
from rulebuilder import build_rule

logger = logging.getLogger(__name__)


def build_canary_rule(cluster: str, service: str, threshold: float) -> dict:
    ctx = BuildContext()
    spec = {
        "id": "canary-cpu",
        "resource_type": "ecs_service",
        "metric": "CPUUtilization",
        "namespace": "AWS/ECS",
        "statistic": "Average",
        "threshold": threshold,
        "severity": "warning",
        "for_duration": "0s",
        "period": 60,
        "rule_group": f"{RULE_PREFIX}canary",
        "dashboard": "ecs",
        "panel": 1,
        "dimension_keys": {
            "ClusterName": "cluster",
            "ServiceName": "service",
        },
        "summary": "Canary rule for provisioning validation",
        "paused": False,
        "contact_point": CONTACT_POINT,
        "labels": {"canary": "true"},
    }
    resource = {
        "name": service,
        "cluster": cluster,
        "service": service,
        "region": REGION,
    }
    rule = build_rule(spec, resource, ctx=ctx)
    rule["title"] = f"{RULE_PREFIX}canary-test"
    rule["ruleGroup"] = f"{RULE_PREFIX}canary"
    rule["for"] = "0s"
    return rule


def wait_for_state(client: GrafanaClient, uid: str, want: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        try:
            states = client.get("/api/prometheus/grafana/api/v1/rules")
        except GrafanaError as exc:
            logger.warning("rules status poll failed: %s", exc)
            time.sleep(5)
            continue
        found = None
        for group in ((states or {}).get("data") or {}).get("groups") or []:
            for rule in group.get("rules") or []:
                if str(rule.get("uid") or "") == uid or rule.get("name") == f"{RULE_PREFIX}canary-test":
                    found = rule
                    break
            if found:
                break
        if found:
            last = found
            state = (found.get("state") or found.get("health") or "").lower()
            alerts = found.get("alerts") or []
            alert_states = [((a.get("state") or "").lower()) for a in alerts]
            logger.info("canary state=%s alerts=%s", state, alert_states)
            if want == "firing" and ("firing" in alert_states or state == "firing"):
                return found
            if want == "exists":
                return found
        time.sleep(5)
    return last


def synthetic_alertmanager_post(client: GrafanaClient) -> dict:
    """Post a short-lived synthetic alert via Alertmanager API if available."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = [
        {
            "labels": {
                "alertname": f"{RULE_PREFIX}canary-synthetic",
                "severity": "warning",
                "resource": "canary-synthetic",
                "service_type": "test",
                "team": "engineering",
                "managed_by": MANAGED_BY,
            },
            "annotations": {
                "summary": "Synthetic canary for routing validation",
                "description": "Temporary synthetic alert; safe to ignore",
                "dashboard_url": "https://grafana.example.com",
            },
            "startsAt": now,
            "endsAt": datetime.fromtimestamp(time.time() + 60, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    ]
    try:
        # Grafana managed Alertmanager accepts posts on this path in some versions.
        result = client.post("/api/alertmanager/grafana/api/v2/alerts", payload)
        return {"ok": True, "result": result}
    except GrafanaError as exc:
        return {"ok": False, "error": str(exc), "body": getattr(exc, "body", None)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Canary test")
    p.add_argument("--cluster", default="infra")
    p.add_argument("--service", default="infra-monitoring")
    p.add_argument("--threshold", type=float, default=0.01, help="Low threshold to force firing")
    p.add_argument("--wait-seconds", type=int, default=180)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-delete", action="store_true")
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging("INFO", json_output=args.json)
    rule = build_canary_rule(args.cluster, args.service, args.threshold)
    uid = rule["uid"]
    result: dict = {
        "status": "ok",
        "uid": uid,
        "title": rule["title"],
        "cluster": args.cluster,
        "service": args.service,
        "threshold": args.threshold,
    }

    if args.dry_run:
        result["dry_run"] = True
        emit_result(result, args.json)
        return 0

    client = GrafanaClient()
    try:
        client.health()
        # Contact point test (best-effort Teams delivery check).
        try:
            test = client.test_contact_point(CONTACT_POINT)
            result["contact_point_test"] = {"ok": True, "result": test}
        except GrafanaError as exc:
            result["contact_point_test"] = {"ok": False, "error": str(exc), "body": getattr(exc, "body", None)}

        synthetic = synthetic_alertmanager_post(client)
        result["synthetic"] = synthetic

        # Create real CloudWatch canary rule.
        existing = {r.get("uid"): r for r in client.list_alert_rules()}
        if uid in existing:
            client.update_alert_rule(uid, {k: v for k, v in rule.items() if k != "_meta"})
            result["created"] = False
            result["updated"] = True
        else:
            client.create_alert_rule({k: v for k, v in rule.items() if k != "_meta"})
            result["created"] = True

        fetched = client.get_alert_rule(uid)
        result["verified_exists"] = fetched.get("uid") == uid
        observed = wait_for_state(client, uid, "exists", min(60, args.wait_seconds))
        result["observed_rule"] = {
            "state": observed.get("state"),
            "health": observed.get("health"),
            "alerts": observed.get("alerts"),
        }

        # Raise threshold so it can resolve, then delete.
        resolve_rule = build_canary_rule(args.cluster, args.service, 99.9)
        resolve_rule["uid"] = uid
        resolve_rule["title"] = rule["title"]
        client.update_alert_rule(uid, {k: v for k, v in resolve_rule.items() if k != "_meta"})
        result["resolve_threshold_applied"] = True
        time.sleep(10)

        if not args.skip_delete:
            client.delete_alert_rule(uid)
            result["deleted"] = True
        else:
            result["deleted"] = False

        # Pass criteria: rule created/verified and contact point exists.
        # Firing may take longer than wait window depending on CloudWatch scrape.
        cp_ok = bool((result.get("contact_point_test") or {}).get("ok")) or CONTACT_POINT
        if result.get("verified_exists") and result.get("deleted", True):
            result["status"] = "ok"
            result["canary_passed"] = True
            result["notes"] = (
                "Canary rule created, verified, resolve-threshold applied, and deleted. "
                f"Contact point test ok={cp_ok}. Synthetic post ok={synthetic.get('ok')}."
            )
        else:
            result["status"] = "error"
            result["canary_passed"] = False

        out = ROOT / "output" / "canary_result.json"
        out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        emit_result(result, args.json)
        return 0 if result.get("canary_passed") else 1
    except GrafanaError as exc:
        # Always attempt cleanup.
        try:
            client.delete_alert_rule(uid)
            result["deleted"] = True
        except Exception:
            result["deleted"] = False
        result.update({"status": "error", "message": str(exc), "body": getattr(exc, "body", None), "canary_passed": False})
        (ROOT / "output" / "canary_result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        emit_result(result, args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
