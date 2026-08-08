#!/usr/bin/env python3
"""Provision Grafana template components for an environment (template, contact point, API dashboard)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from env_profiles import ENV_PROFILES
from grafana_client import GrafanaClient, GrafanaError, configure_logging, emit_result
from teams_webhook_config import TeamsWebhookConfigError, load_dotenv_if_present, resolve_webhook_url

logger = logging.getLogger(__name__)
ROOT = SCRIPT_DIR.parent

ENV_CONFIG = {
    "staging": {
        "template_file": ROOT / "templates" / "stg-teams-default.tmpl",
        "template_name": "stg-teams-default",
        "contact_point": "stg-teams-alerts",
        "title_template": "stg-teams-default.title",
        "message_template": "stg-teams-default.message",
        "dashboard_file": ROOT / "templates" / "dashboard_api-metrics.json",
    },
    "uat": {
        "template_file": ROOT / "templates" / "uat-teams-default.tmpl",
        "template_name": "uat-teams-default",
        "contact_point": "uat-alerts",
        "title_template": "uat-teams-default.title",
        "message_template": "uat-teams-default.message",
        "dashboard_file": ROOT / "templates" / "dashboard_api-metrics.json",
    },
}


def load_inventory(env: str) -> dict:
    profile = ENV_PROFILES[env]
    path = ROOT / profile["inventory"]
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc.get("inventory", doc)


def _webhook_url() -> str:
    try:
        return resolve_webhook_url()
    except TeamsWebhookConfigError as exc:
        raise GrafanaError(str(exc)) from exc


def provision_template(client: GrafanaClient, cfg: dict, *, dry_run: bool) -> dict:
    body = cfg["template_file"].read_text(encoding="utf-8")
    name = cfg["template_name"]
    if dry_run:
        return {"action": "dry-run", "template": name, "bytes": len(body)}
    client.create_notification_template(name, body)
    return {"action": "upserted", "template": name}


def provision_contact_point(
    client: GrafanaClient, cfg: dict, inventory: dict, *, dry_run: bool
) -> dict:
    name = inventory.get("contact_point", cfg["contact_point"])
    webhook = _webhook_url()
    payload = {
        "name": name,
        "type": "teams",
        "settings": {
            "url": webhook,
            "title": f'{{{{ template "{cfg["title_template"]}" . }}}}',
            "message": f'{{{{ template "{cfg["message_template"]}" . }}}}',
        },
        "disableResolveMessage": False,
    }
    if dry_run:
        return {"action": "dry-run", "contact_point": name}
    existing = None
    for cp in client.list_contact_points():
        if cp.get("name") == name:
            existing = cp
            break
    if existing and existing.get("uid"):
        payload["uid"] = existing["uid"]
        result = client.put(f"/api/v1/provisioning/contact-points/{existing['uid']}", payload)
        return {"action": "updated", "contact_point": name, "uid": existing["uid"], "result": result}
    result = client.upsert_contact_point(payload)
    return {"action": "created", "contact_point": name, "uid": result.get("uid")}


def provision_api_dashboard(
    client: GrafanaClient, cfg: dict, inventory: dict, *, dry_run: bool
) -> dict:
    doc = json.loads(cfg["dashboard_file"].read_text(encoding="utf-8"))
    dashboard = doc.get("dashboard", doc)
    folder_uid = inventory.get("folder_uid", doc.get("folderUid"))
    if dry_run:
        return {
            "action": "dry-run",
            "dashboard_uid": dashboard.get("uid"),
            "folder_uid": folder_uid,
        }
    result = client.create_dashboard(dashboard, folder_uid, overwrite=True)
    return {
        "action": "created",
        "dashboard_uid": dashboard.get("uid"),
        "url": result.get("url"),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Provision Grafana template for an environment")
    p.add_argument("--env", choices=sorted(ENV_PROFILES), required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-template", action="store_true")
    p.add_argument("--skip-contact-point", action="store_true")
    p.add_argument("--skip-dashboard", action="store_true")
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging("INFO", json_output=args.json)
    load_dotenv_if_present()
    cfg = ENV_CONFIG[args.env]
    inventory = load_inventory(args.env)
    summary: dict = {"status": "ok", "env": args.env, "dry_run": args.dry_run, "steps": {}}

    try:
        client = GrafanaClient(base_url=inventory.get("grafana_url"))
        if not args.skip_template:
            summary["steps"]["template"] = provision_template(client, cfg, dry_run=args.dry_run)
        if not args.skip_contact_point:
            summary["steps"]["contact_point"] = provision_contact_point(
                client, cfg, inventory, dry_run=args.dry_run
            )
        if not args.skip_dashboard:
            summary["steps"]["api_dashboard"] = provision_api_dashboard(
                client, cfg, inventory, dry_run=args.dry_run
            )
        emit_result(summary, args.json)
        return 0
    except GrafanaError as exc:
        emit_result({"status": "error", "message": str(exc), "body": exc.body}, args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
