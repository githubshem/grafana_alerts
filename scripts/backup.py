#!/usr/bin/env python3
"""Export Grafana dashboards, alert rules, contact points, templates, and policies."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from grafana_client import GrafanaClient, GrafanaError, configure_logging, emit_result

logger = logging.getLogger(__name__)

PROVISIONING_ROOT = SCRIPT_DIR.parent
DEFAULT_BACKUP_ROOT = PROVISIONING_ROOT / "backups"


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote %s", path)


def export_dashboards(client: GrafanaClient, dest: Path) -> dict[str, int]:
    dashboards_dir = dest / "dashboards"
    dashboards_dir.mkdir(parents=True, exist_ok=True)
    search = client.list_dashboards()
    count = 0
    for item in search:
        uid = item.get("uid")
        if not uid:
            continue
        try:
            full = client.get_dashboard(uid)
            _write_json(dashboards_dir / f"{uid}.json", full)
            count += 1
        except GrafanaError as exc:
            logger.warning("Skipping dashboard %s: %s", uid, exc)
    return {"dashboards": count}


def export_alert_rules(client: GrafanaClient, dest: Path) -> dict[str, int]:
    rules = client.list_alert_rules()
    _write_json(dest / "alert_rules.json", rules)
    return {"alert_rules": len(rules)}


def export_contact_points(client: GrafanaClient, dest: Path) -> dict[str, int]:
    points = client.list_contact_points()
    _write_json(dest / "contact_points.json", points)
    return {"contact_points": len(points)}


def export_templates(client: GrafanaClient, dest: Path) -> dict[str, int]:
    try:
        templates = client.list_notification_templates()
    except GrafanaError as exc:
        logger.warning("Could not export notification templates: %s", exc)
        templates = []
    if templates is None:
        templates = []
    _write_json(dest / "notification_templates.json", templates)
    return {"notification_templates": len(templates)}


def export_policies(client: GrafanaClient, dest: Path) -> dict[str, int]:
    try:
        policies = client.get_notification_policies()
    except GrafanaError as exc:
        logger.warning("Could not export notification policies: %s", exc)
        policies = {"error": str(exc)}
    _write_json(dest / "notification_policies.json", policies)
    return {"notification_policies": 1 if policies else 0}


def run_backup(client: GrafanaClient, backup_dir: Path) -> dict[str, object]:
    manifest: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "grafana_url": client.base_url,
        "exports": {},
    }
    manifest["exports"].update(export_dashboards(client, backup_dir))
    manifest["exports"].update(export_alert_rules(client, backup_dir))
    manifest["exports"].update(export_contact_points(client, backup_dir))
    manifest["exports"].update(export_templates(client, backup_dir))
    manifest["exports"].update(export_policies(client, backup_dir))
    _write_json(backup_dir / "manifest.json", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Grafana alerting configuration to backups/")
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=DEFAULT_BACKUP_ROOT,
        help="Root directory for timestamped backups",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Explicit backup directory (overrides timestamp subdir)",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--json", action="store_true", help="Emit structured JSON result")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level, json_output=args.json)

    if args.output_dir:
        backup_dir = args.output_dir
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = args.backup_root / stamp

    try:
        client = GrafanaClient()
        manifest = run_backup(client, backup_dir)
        result = {"status": "ok", "backup_dir": str(backup_dir), **manifest}
        emit_result(result, args.json)
        return 0
    except GrafanaError as exc:
        logger.error("Backup failed: %s", exc)
        emit_result({"status": "error", "message": str(exc)}, args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
