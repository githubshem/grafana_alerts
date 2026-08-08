#!/usr/bin/env python3
"""Apply compiled rule groups to Grafana idempotently in manageable batches."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from config import FOLDER_UID
from grafana_client import GrafanaClient, GrafanaError, configure_logging, emit_result

logger = logging.getLogger(__name__)


def load_manifest(groups_dir: Path) -> list[dict]:
    manifest_path = groups_dir / "manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = []
    for path in sorted(groups_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "group": payload.get("title") or path.stem,
                "rules": len(payload.get("rules") or []),
                "interval": payload.get("interval", 300),
                "file": path.name,
            }
        )
    return rows


def sort_batches(manifest: list[dict]) -> list[dict]:
    def key(row: dict) -> tuple:
        name = row["group"]
        if name == "alerts-availability-critical":
            return (0, name)
        if name == "alerts-alb":
            return (1, name)
        if name == "alerts-nlb":
            return (2, name)
        if name == "alerts-rds":
            return (3, name)
        if name == "alerts-redis":
            return (4, name)
        if name == "alerts-mq":
            return (5, name)
        if name.startswith("alerts-ecs-"):
            return (6, name)
        return (9, name)

    return sorted(manifest, key=key)


def apply_group(client: GrafanaClient, folder_uid: str, payload: dict, *, dry_run: bool) -> dict:
    title = payload["title"]
    if dry_run:
        return {"group": title, "action": "would_put", "rules": len(payload.get("rules") or [])}
    # Grafana rule-group PUT replaces the whole group.
    client.put_rule_group(folder_uid, title, payload)
    # Read-back verification.
    live = client.get_rule_group(folder_uid, title)
    live_rules = live.get("rules") if isinstance(live, dict) else None
    if live_rules is None and isinstance(live, dict):
        live_rules = live.get("data", {}).get("rules") if isinstance(live.get("data"), dict) else None
    count = len(live_rules or [])
    expected = len(payload.get("rules") or [])
    return {
        "group": title,
        "action": "put",
        "expected": expected,
        "live_count": count,
        "verified": count == expected,
        "interval": payload.get("interval"),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Apply rule groups")
    p.add_argument("--groups-dir", type=Path, default=ROOT / "output" / "rule_groups")
    p.add_argument("--folder-uid", default=FOLDER_UID)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--start-from", default="", help="Resume from this group name")
    p.add_argument("--only", default="", help="Comma-separated group names to apply")
    p.add_argument("--sleep", type=float, default=0.5)
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging("INFO", json_output=args.json)
    manifest = sort_batches(load_manifest(args.groups_dir))
    only = {x.strip() for x in args.only.split(",") if x.strip()} if args.only else set()
    started = not bool(args.start_from)
    results = []
    errors = []

    client = None if args.dry_run else GrafanaClient()
    if client:
        client.health()

    for row in manifest:
        name = row["group"]
        if only and name not in only:
            continue
        if not started:
            if name == args.start_from:
                started = True
            else:
                continue
        path = args.groups_dir / row["file"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        try:
            logger.info("Applying group %s (%s rules)", name, len(payload.get("rules") or []))
            result = apply_group(client, args.folder_uid, payload, dry_run=args.dry_run) if client or args.dry_run else {}
            if args.dry_run:
                result = {"group": name, "action": "would_put", "rules": len(payload.get("rules") or []), "interval": payload.get("interval")}
            results.append(result)
            if not args.dry_run and not result.get("verified", True):
                errors.append(result)
                break
            time.sleep(args.sleep)
        except GrafanaError as exc:
            err = {"group": name, "error": str(exc), "body": getattr(exc, "body", None)}
            errors.append(err)
            results.append(err)
            logger.error("Failed group %s: %s", name, exc)
            break

    summary = {
        "status": "ok" if not errors else "error",
        "dry_run": args.dry_run,
        "applied_groups": len([r for r in results if r.get("action") in ("put", "would_put")]),
        "errors": errors,
        "results": results,
    }
    out = ROOT / "output" / ("apply_dry_run.json" if args.dry_run else "apply_result.json")
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    emit_result(summary, args.json)
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
