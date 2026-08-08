#!/usr/bin/env python3
"""Apply Grafana alert rules from inventory and rule definition templates."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from backup import run_backup
from config import ENV_PROFILES, RULE_PREFIX, BuildContext
from grafana_client import GrafanaClient, GrafanaError, configure_logging, emit_result
from rulebuilder import expand_rules
from validate_compiled_rules import validate_compiled

logger = logging.getLogger(__name__)

PROVISIONING_ROOT = SCRIPT_DIR.parent
DEFAULT_INVENTORY = PROVISIONING_ROOT / "inventory" / "services.yaml"
DEFAULT_RULES = PROVISIONING_ROOT / "templates" / "rule_definitions.yaml"


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def compile_rules(
    inventory: dict,
    rule_specs: list[dict],
    defaults: dict,
    *,
    apply_batch: int | None = None,
    ecs_cluster: str | None = None,
) -> list[dict]:
    """Expand YAML rule specs across inventory resources."""
    ctx = BuildContext.from_inventory(inventory)
    grafana_url = inventory.get("grafana_url")
    compiled: list[dict] = []

    for spec in rule_specs:
        merged = {**defaults, **spec}
        if apply_batch is not None and merged.get("apply_batch") != apply_batch:
            continue

        resource_type = merged.get("resource_type", "")
        name_filter = inventory.get("ecs_cluster_filter") if resource_type == "ecs" else None
        ecs_canary_only = apply_batch == 2 and resource_type == "ecs_service"

        compiled.extend(
            expand_rules(
                [merged],
                inventory,
                ctx=ctx,
                grafana_url=grafana_url,
                name_filter=name_filter,
                ecs_cluster=ecs_cluster if resource_type == "ecs_service" else None,
                ecs_canary_only=ecs_canary_only,
            ),
        )

    by_uid: dict[str, dict] = {}
    for rule in compiled:
        by_uid[rule["uid"]] = rule
    return list(by_uid.values())


def _existing_prefixed_rules(client: GrafanaClient, rule_prefix: str) -> dict[str, dict]:
    rules = client.list_alert_rules()
    return {
        r["uid"]: r
        for r in rules
        if r.get("title", "").startswith(rule_prefix)
    }


def apply_rules(
    client: GrafanaClient | None,
    rules: list[dict],
    *,
    rule_prefix: str,
    dry_run: bool,
    update_existing: bool,
) -> dict[str, object]:
    existing: dict[str, dict] = {}
    if client and not dry_run:
        existing = _existing_prefixed_rules(client, rule_prefix)

    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []

    for rule in rules:
        payload = {k: v for k, v in rule.items() if k != "_meta"}
        uid = payload["uid"]
        title = payload["title"]
        if dry_run:
            action = "update" if uid in existing else "create"
            logger.info("[dry-run] would %s rule %s (%s)", action, title, uid)
            (updated if uid in existing else created).append(uid)
            continue

        assert client is not None
        try:
            if uid in existing:
                if update_existing:
                    client.update_alert_rule(uid, payload)
                    updated.append(uid)
                    logger.info("Updated rule %s", title)
                else:
                    skipped.append(uid)
                    logger.info("Skipped existing rule %s", title)
            else:
                client.create_alert_rule(payload)
                created.append(uid)
                logger.info("Created rule %s", title)
        except GrafanaError as exc:
            logger.error("Failed rule %s: %s", title, exc)
            errors.append({"title": title, "uid": uid, "error": str(exc)})

    return {
        "total": len(rules),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
    }


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, str]:
    if args.env:
        profile = ENV_PROFILES[args.env]
        inventory = PROVISIONING_ROOT / profile["inventory"]
        rules = PROVISIONING_ROOT / profile["rules"]
        if args.rules:
            rules = args.rules
        if args.inventory:
            inventory = args.inventory
        return inventory, rules, profile["rule_prefix"]
    inventory = args.inventory or DEFAULT_INVENTORY
    rules = args.rules or DEFAULT_RULES
    return inventory, rules, RULE_PREFIX


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create Grafana alert rules from YAML definitions")
    parser.add_argument("--env", choices=sorted(ENV_PROFILES), help="Environment profile")
    parser.add_argument("--inventory", type=Path, default=None)
    parser.add_argument("--rules", type=Path, default=None)
    parser.add_argument("--apply-batch", type=int, choices=[1, 2, 3, 4], help="Staged apply batch filter")
    parser.add_argument("--ecs-cluster", help="Limit ECS service rules to one cluster (batch 3)")
    parser.add_argument("--dry-run", action="store_true", help="Plan changes without calling Grafana API")
    parser.add_argument("--update-existing", action="store_true", help="Update rules that already exist")
    parser.add_argument("--skip-validation", action="store_true", help="Skip compiled rule validation (not recommended)")
    parser.add_argument("--skip-backup", action="store_true", help="Skip pre-apply Grafana backup")
    parser.add_argument("--output", type=Path, default=None, help="Write compiled rules JSON to file")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--json", action="store_true", help="Emit structured JSON result")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level, json_output=args.json)

    inventory_path, rules_path, rule_prefix = resolve_paths(args)
    inventory_doc = load_yaml(inventory_path)
    rules_doc = load_yaml(rules_path)
    inventory = inventory_doc.get("inventory", inventory_doc)
    rule_specs = rules_doc.get("rules", [])
    defaults = rules_doc.get("defaults", {})

    compiled = compile_rules(
        inventory,
        rule_specs,
        defaults,
        apply_batch=args.apply_batch,
        ecs_cluster=args.ecs_cluster,
    )
    logger.info("Compiled %d alert rules (prefix=%s)", len(compiled), rule_prefix)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(compiled, indent=2), encoding="utf-8")
        logger.info("Wrote compiled rules to %s", args.output)

    if not args.skip_validation:
        validation_issues = validate_compiled(compiled)
        if validation_issues:
            emit_result(
                {
                    "status": "validation_failed",
                    "compiled_count": len(compiled),
                    "issues": validation_issues,
                },
                args.json,
            )
            return 1

    backup_dir: str | None = None
    try:
        client = None if args.dry_run else GrafanaClient(base_url=inventory.get("grafana_url"))
        if client and not args.skip_backup and args.update_existing:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            env_suffix = (args.env or "").replace("_", "-")
            backup_name = f"{stamp}-{env_suffix}" if env_suffix else stamp
            backup_path = PROVISIONING_ROOT / "backups" / backup_name
            backup_manifest = run_backup(client, backup_path)
            backup_dir = str(backup_path)
            logger.info("Backup completed: %s", backup_dir)

        summary = apply_rules(
            client,
            compiled,
            rule_prefix=inventory.get("rule_prefix", rule_prefix),
            dry_run=args.dry_run,
            update_existing=args.update_existing,
        )
        result = {
            "status": "ok",
            "compiled_count": len(compiled),
            "backup_dir": backup_dir,
            **summary,
        }
        emit_result(result, args.json)
        return 0 if not summary["errors"] else 1
    except GrafanaError as exc:
        logger.error("Apply failed: %s", exc)
        emit_result(
            {"status": "error", "message": str(exc), "compiled_count": len(compiled)},
            args.json,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
