#!/usr/bin/env python3
"""Diff compiled alert rules against the committed baseline.

Emits a machine-readable report so a refactor can be proven behaviour-preserving:

    {
      "summary": {"added": 0, "removed": 0, "changed": 0, ...},
      "added":   [{"uid": ..., "title": ...}],
      "removed": [{"uid": ..., "title": ...}],
      "changed": [{"uid": ..., "title": ..., "field": ..., "before": ..., "after": ...}]
    }

Exits 0 only when the two sets are identical.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

DEFAULT_BASELINE = ROOT / "baseline" / "compiled_rules.baseline.json"
DEFAULT_CURRENT = ROOT / "output" / "compiled_rules.json"
DEFAULT_OUTPUT = ROOT / "baseline" / "rule_diff.json"

# Compared field by field so a diff points at the exact key that moved.
COMPARED_FIELDS = (
    "title",
    "ruleGroup",
    "folderUID",
    "condition",
    "data",
    "noDataState",
    "execErrState",
    "for",
    "annotations",
    "labels",
    "isPaused",
    "notification_settings",
    "_meta",
)


def load_rules(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SystemExit(f"Not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"Expected a JSON array of rules in {path}")
    return data


def index_by_uid(rules: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(rule.get("uid")): rule for rule in rules}


def _flatten(value: Any, prefix: str) -> dict[str, Any]:
    """Flatten dicts one level so a changed annotation names the annotation."""
    if isinstance(value, dict):
        return {f"{prefix}.{k}": v for k, v in value.items()}
    return {prefix: value}


def diff_rule(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for field in COMPARED_FIELDS:
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        if isinstance(old, dict) and isinstance(new, dict):
            flat_old = _flatten(old, field)
            flat_new = _flatten(new, field)
            for key in sorted(set(flat_old) | set(flat_new)):
                if flat_old.get(key) != flat_new.get(key):
                    changes.append(
                        {"field": key, "before": flat_old.get(key), "after": flat_new.get(key)}
                    )
        else:
            changes.append({"field": field, "before": old, "after": new})
    return changes


def compare(baseline: list[dict[str, Any]], current: list[dict[str, Any]]) -> dict[str, Any]:
    base_by_uid = index_by_uid(baseline)
    curr_by_uid = index_by_uid(current)

    added = [
        {"uid": uid, "title": curr_by_uid[uid].get("title", "")}
        for uid in sorted(set(curr_by_uid) - set(base_by_uid))
    ]
    removed = [
        {"uid": uid, "title": base_by_uid[uid].get("title", "")}
        for uid in sorted(set(base_by_uid) - set(curr_by_uid))
    ]

    changed: list[dict[str, Any]] = []
    for uid in sorted(set(base_by_uid) & set(curr_by_uid)):
        for change in diff_rule(base_by_uid[uid], curr_by_uid[uid]):
            changed.append(
                {"uid": uid, "title": base_by_uid[uid].get("title", ""), **change}
            )

    duplicate_uids = len(current) - len(curr_by_uid)

    return {
        "summary": {
            "baseline_count": len(baseline),
            "current_count": len(current),
            "baseline_unique_uids": len(base_by_uid),
            "current_unique_uids": len(curr_by_uid),
            "duplicate_uids": duplicate_uids,
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "changed_rules": len({c["uid"] for c in changed}),
            "identical": not (added or removed or changed) and duplicate_uids == 0,
        },
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Diff compiled rules against the baseline")
    p.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    p.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--max-print", type=int, default=20, help="Max diff entries to print")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    report = compare(load_rules(args.baseline), load_rules(args.current))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    summary = report["summary"]
    print(json.dumps(summary, indent=2))
    for kind in ("added", "removed", "changed"):
        for entry in report[kind][: args.max_print]:
            print(f"  {kind.upper():8} {entry}")
        overflow = len(report[kind]) - args.max_print
        if overflow > 0:
            print(f"  ... and {overflow} more {kind}")

    print(f"Wrote {args.output}")
    return 0 if summary["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
