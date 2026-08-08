#!/usr/bin/env python3
"""Orchestrate Grafana provisioning: backup, template, contact point, dashboard, rules."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from apply_rules import compile_rules, load_yaml, apply_rules as apply_alert_rules
from backup import run_backup
from grafana_client import FOLDER_UID, GrafanaClient, GrafanaError, configure_logging, emit_result
from teams_webhook_config import TeamsWebhookConfigError, load_dotenv_if_present, resolve_webhook_url

logger = logging.getLogger(__name__)
ROOT = SCRIPT_DIR.parent
TEMPLATE_FILE = ROOT / "templates" / "stg-teams-default.tmpl"
DASHBOARD_FILE = ROOT / "templates" / "dashboard_api-metrics.json"
INVENTORY_FILE = ROOT / "inventory" / "services.yaml"
RULES_FILE = ROOT / "templates" / "rule_definitions_staging.yaml"
CONTACT_POINT_NAME = "stg-teams-alerts"
TEMPLATE_NAME = "stg-teams-default"


def _webhook_url() -> str:
    try:
        return resolve_webhook_url()
    except TeamsWebhookConfigError as exc:
        raise GrafanaError(str(exc)) from exc


def provision_template(client: GrafanaClient, *, dry_run: bool) -> dict:
    body = TEMPLATE_FILE.read_text(encoding="utf-8")
    if dry_run:
        return {"action": "dry-run", "template": TEMPLATE_NAME}
    client.create_notification_template(TEMPLATE_NAME, body)
    return {"action": "created", "template": TEMPLATE_NAME}


def provision_contact_point(client: GrafanaClient, *, dry_run: bool) -> dict:
    webhook = _webhook_url()
    payload = {
        "name": CONTACT_POINT_NAME,
        "type": "teams",
        "settings": {
            "url": webhook,
            "title": '{{ template "stg-teams-default.title" . }}',
            "message": '{{ template "stg-teams-default.message" . }}',
        },
        "disableResolveMessage": False,
    }
    if dry_run:
        return {"action": "dry-run", "contact_point": CONTACT_POINT_NAME}
    result = client.upsert_contact_point(payload)
    return {"action": "created", "contact_point": CONTACT_POINT_NAME, "uid": result.get("uid")}


def provision_api_dashboard(client: GrafanaClient, *, dry_run: bool) -> dict:
    doc = json.loads(DASHBOARD_FILE.read_text(encoding="utf-8"))
    dashboard = doc.get("dashboard", doc)
    if dry_run:
        return {"action": "dry-run", "dashboard_uid": dashboard.get("uid")}
    result = client.create_dashboard(dashboard, FOLDER_UID, overwrite=True)
    return {
        "action": "created",
        "dashboard_uid": dashboard.get("uid"),
        "url": result.get("url"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision staging monitoring in Grafana")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-backup", action="store_true")
    parser.add_argument("--skip-rules", action="store_true")
    parser.add_argument("--update-existing", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level, json_output=args.json)
    load_dotenv_if_present()
    summary: dict = {"status": "ok", "steps": {}, "dry_run": args.dry_run}

    try:
        client = GrafanaClient()

        if not args.skip_backup and not args.dry_run:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_dir = ROOT / "backups" / stamp
            summary["steps"]["backup"] = run_backup(client, backup_dir)
            summary["backup_dir"] = str(backup_dir)
        elif args.dry_run:
            summary["steps"]["backup"] = {"action": "dry-run"}

        summary["steps"]["template"] = provision_template(client, dry_run=args.dry_run)
        summary["steps"]["contact_point"] = provision_contact_point(client, dry_run=args.dry_run)
        summary["steps"]["api_dashboard"] = provision_api_dashboard(client, dry_run=args.dry_run)

        try:
            policies = client.get_notification_policies()
            summary["steps"]["policies"] = {"status": "ok", "data": policies}
        except GrafanaError as exc:
            summary["steps"]["policies"] = {
                "status": "blocked",
                "error": str(exc),
                "body": exc.body,
            }

        if not args.skip_rules:
            inventory_doc = load_yaml(INVENTORY_FILE)
            rules_doc = load_yaml(RULES_FILE)
            inventory = inventory_doc.get("inventory", inventory_doc)
            compiled = compile_rules(
                inventory,
                rules_doc.get("rules", []),
                rules_doc.get("defaults", {}),
            )
            # Skip info-severity rules
            compiled = [r for r in compiled if r.get("labels", {}).get("severity") != "info"]
            summary["compiled_rules"] = len(compiled)
            rule_result = apply_alert_rules(
                None if args.dry_run else client,
                compiled,
                dry_run=args.dry_run,
                update_existing=args.update_existing,
            )
            summary["steps"]["alert_rules"] = rule_result

        emit_result(summary, args.json)
        errors = summary.get("steps", {}).get("alert_rules", {}).get("errors", [])
        return 0 if not errors else 1
    except GrafanaError as exc:
        emit_result({"status": "error", "message": str(exc), "body": exc.body}, args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
