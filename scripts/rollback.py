"""Delete Grafana resources created by this provisioning toolkit (alerts- prefix only)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config import RULE_PREFIX
from grafana_client import GrafanaClient, GrafanaError, configure_logging, emit_result

logger = logging.getLogger(__name__)


def _is_prefixed_resource(item: dict, prefix: str, *, title_key: str = "title", name_key: str = "name") -> bool:
    for key in (title_key, name_key, "uid"):
        value = item.get(key, "")
        if isinstance(value, str) and value.startswith(prefix):
            return True
    return False


def rollback_alert_rules(client: GrafanaClient, *, dry_run: bool, rule_prefix: str = RULE_PREFIX) -> dict[str, object]:
    rules = client.list_alert_rules()
    targets = [r for r in rules if _is_prefixed_resource(r, rule_prefix)]
    deleted: list[str] = []
    errors: list[dict[str, str]] = []

    for rule in targets:
        uid = rule["uid"]
        title = rule.get("title", uid)
        if dry_run:
            logger.info("[dry-run] would delete alert rule %s (%s)", title, uid)
            deleted.append(uid)
            continue
        try:
            client.delete_alert_rule(uid)
            deleted.append(uid)
            logger.info("Deleted alert rule %s", title)
        except GrafanaError as exc:
            logger.error("Failed to delete %s: %s", title, exc)
            errors.append({"uid": uid, "title": title, "error": str(exc)})

    return {"alert_rules_deleted": deleted, "alert_rule_errors": errors}


def rollback_contact_points(client: GrafanaClient, *, dry_run: bool, prefix: str = RULE_PREFIX) -> dict[str, object]:
    points = client.list_contact_points()
    targets = [
        p
        for p in points
        if _is_prefixed_resource(p, prefix, name_key="name") or p.get("name") == "engineering-alerts"
    ]
    deleted: list[str] = []
    errors: list[dict[str, str]] = []

    for point in targets:
        uid = point.get("uid", "")
        name = point.get("name", uid)
        if dry_run:
            logger.info("[dry-run] would delete contact point %s (%s)", name, uid)
            deleted.append(uid)
            continue
        try:
            client.delete(f"/api/v1/provisioning/contact-points/{uid}")
            deleted.append(uid)
            logger.info("Deleted contact point %s", name)
        except GrafanaError as exc:
            logger.error("Failed to delete contact point %s: %s", name, exc)
            errors.append({"uid": uid, "name": name, "error": str(exc)})

    return {"contact_points_deleted": deleted, "contact_point_errors": errors}


def rollback_templates(client: GrafanaClient, *, dry_run: bool, prefix: str = RULE_PREFIX) -> dict[str, object]:
    try:
        templates = client.list_notification_templates() or []
    except GrafanaError:
        templates = []

    targets = [
        t
        for t in templates
        if (
            isinstance(t, dict)
            and (
                _is_prefixed_resource(t, prefix, name_key="name", title_key="name")
                or t.get("name") == "teams-default"
            )
        )
    ]
    deleted: list[str] = []
    errors: list[dict[str, str]] = []

    for tmpl in targets:
        name = tmpl.get("name", "")
        if dry_run:
            logger.info("[dry-run] would delete template %s", name)
            deleted.append(name)
            continue
        try:
            client.delete(f"/api/v1/provisioning/templates/{name}")
            deleted.append(name)
            logger.info("Deleted template %s", name)
        except GrafanaError as exc:
            logger.error("Failed to delete template %s: %s", name, exc)
            errors.append({"name": name, "error": str(exc)})

    return {"templates_deleted": deleted, "template_errors": errors}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Delete Grafana resources with '{RULE_PREFIX}' prefix",
    )
    parser.add_argument("--prefix", default=RULE_PREFIX, help="Rule title prefix to delete (default: alerts-)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    parser.add_argument("--skip-contact-points", action="store_true")
    parser.add_argument("--skip-templates", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level, json_output=args.json)

    try:
        client = GrafanaClient()
        summary: dict[str, object] = {"status": "ok", "dry_run": args.dry_run, "prefix": args.prefix}
        summary.update(rollback_alert_rules(client, dry_run=args.dry_run, rule_prefix=args.prefix))

        if not args.skip_contact_points:
            summary.update(rollback_contact_points(client, dry_run=args.dry_run, prefix=args.prefix))
        if not args.skip_templates:
            summary.update(rollback_templates(client, dry_run=args.dry_run, prefix=args.prefix))

        has_errors = any(
            summary.get(k)
            for k in ("alert_rule_errors", "contact_point_errors", "template_errors")
            if summary.get(k)
        )
        if has_errors:
            summary["status"] = "error"
        emit_result(summary, args.json)
        return 0 if summary["status"] == "ok" else 1
    except GrafanaError as exc:
        logger.error("Rollback failed: %s", exc)
        emit_result({"status": "error", "message": str(exc)}, args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
