#!/usr/bin/env python3
"""Apply compiled alert rules JSON to Grafana (batch-friendly, resumable)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from grafana_client import GrafanaClient, GrafanaError, configure_logging, emit_result

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Apply compiled_rules.json to Grafana")
    p.add_argument("--rules-json", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--limit", type=int, default=0, help="0 = all")
    p.add_argument("--sleep-ms", type=int, default=200)
    p.add_argument("--receiver", default=None, help="Override notification receiver")
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging("INFO", json_output=args.json)
    rules = json.loads(args.rules_json.read_text(encoding="utf-8"))
    end = len(rules) if args.limit <= 0 else min(len(rules), args.start + args.limit)
    subset = rules[args.start:end]

    created, updated, errors = [], [], []
    client = None if args.dry_run else GrafanaClient()
    existing = {}
    if client:
        existing = {r["uid"]: r for r in client.list_alert_rules() if r.get("title", "").startswith("stg-")}

    for rule in subset:
        if args.receiver:
            rule = dict(rule)
            rule["notification_settings"] = {"receiver": args.receiver}
        uid = rule["uid"]
        title = rule["title"]
        if args.dry_run:
            action = "update" if uid in existing else "create"
            logger.info("[dry-run] %s %s", action, title)
            (updated if uid in existing else created).append(uid)
            continue
        try:
            if uid in existing:
                client.update_alert_rule(uid, rule)
                updated.append(uid)
            else:
                client.create_alert_rule(rule)
                created.append(uid)
            time.sleep(args.sleep_ms / 1000.0)
        except GrafanaError as exc:
            errors.append({"uid": uid, "title": title, "error": str(exc), "body": exc.body})

    result = {
        "status": "ok" if not errors else "partial",
        "dry_run": args.dry_run,
        "processed": len(subset),
        "created": len(created),
        "updated": len(updated),
        "errors": errors,
    }
    emit_result(result, args.json)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
