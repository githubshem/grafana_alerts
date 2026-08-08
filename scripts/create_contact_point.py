#!/usr/bin/env python3
"""Create/update engineering-alerts contact point as a Webhook to Power Automate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from config import CONTACT_POINT
from grafana_client import GrafanaClient, GrafanaError, configure_logging, emit_result
from teams_webhook_config import TeamsWebhookConfigError, load_dotenv_if_present, resolve_webhook_url

CONTACT_POINT_NAME = CONTACT_POINT


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--name", default=CONTACT_POINT_NAME)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging("INFO", json_output=args.json)
    load_dotenv_if_present()
    client = GrafanaClient()
    try:
        webhook = resolve_webhook_url()
    except TeamsWebhookConfigError as exc:
        raise GrafanaError(str(exc)) from exc

    payload = {
        "name": args.name,
        "type": "webhook",
        "settings": {
            "url": webhook,
            "httpMethod": "POST",
        },
        "disableResolveMessage": False,
    }
    if args.dry_run:
        emit_result({"status": "ok", "dry_run": True, "contact_point": args.name}, args.json)
        return 0

    existing = client.list_contact_points()
    match = next((p for p in existing if p.get("name") == args.name), None)
    if match and match.get("uid"):
        result = client.update_contact_point(match["uid"], payload)
        action = "updated"
    else:
        result = client.upsert_contact_point(payload)
        action = "created"
    emit_result(
        {
            "status": "ok",
            "action": action,
            "contact_point": args.name,
            "uid": (result or {}).get("uid") or (match or {}).get("uid"),
        },
        args.json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
