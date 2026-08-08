#!/usr/bin/env python3
"""Classify live staging rules vs repo compiled set (deletion-safe report)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROVISIONING_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from grafana_client import emit_result

DEFAULT_LIVE = (
    PROVISIONING_ROOT / "test-results" / "live-staging-latest" / "live-alert-rules-pre-metadata.json"
)
DEFAULT_COMPILED = PROVISIONING_ROOT / "test-results" / "compiled_rules.json"
DEFAULT_YAML = PROVISIONING_ROOT / "templates" / "rule_definitions_staging.yaml"


def _yaml_rule_ids(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"(?m)^\s+- id:\s+(\S+)", text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diff live staging rules vs repo compile")
    parser.add_argument("--live-json", type=Path, default=DEFAULT_LIVE)
    parser.add_argument("--compiled-json", type=Path, default=DEFAULT_COMPILED)
    parser.add_argument("--rules-yaml", type=Path, default=DEFAULT_YAML)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROVISIONING_ROOT / "test-results" / "live-staging-latest" / "live_vs_repo_report.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.live_json.is_file():
        raise SystemExit(f"Missing live export: {args.live_json}. Run export_live_staging_rules.py first.")

    live = json.loads(args.live_json.read_text(encoding="utf-8"))
    live_by_uid = {r["uid"]: r for r in live if r.get("uid")}
    live_uids = set(live_by_uid)

    compiled_uids: set[str] = set()
    compiled_titles: dict[str, str] = {}
    if args.compiled_json.is_file():
        compiled = json.loads(args.compiled_json.read_text(encoding="utf-8"))
        for r in compiled:
            uid = r.get("uid")
            if uid:
                compiled_uids.add(uid)
                compiled_titles[uid] = r.get("title", "")

    yaml_ids = _yaml_rule_ids(args.rules_yaml) if args.rules_yaml.is_file() else []

    matched = sorted(live_uids & compiled_uids)
    repo_only = sorted(compiled_uids - live_uids)
    live_only = sorted(live_uids - compiled_uids)

    live_titles = {r.get("title"): r.get("uid") for r in live if r.get("title")}
    title_matched = sorted(set(live_titles) & set(compiled_titles.values()))
    repo_only_titles = sorted(set(compiled_titles.values()) - set(live_titles))
    live_only_titles = sorted(set(live_titles) - set(compiled_titles.values()))

    missing_metric = [
        {"uid": u, "title": live_by_uid[u].get("title")}
        for u in sorted(live_uids)
        if not (live_by_uid[u].get("annotations") or {}).get("metric")
    ]
    missing_threshold = [
        {"uid": u, "title": live_by_uid[u].get("title")}
        for u in sorted(live_uids)
        if not (live_by_uid[u].get("annotations") or {}).get("threshold")
    ]

    e2e = [
        {"uid": r.get("uid"), "title": r.get("title")}
        for r in live
        if "powerautomate" in str(r.get("title", "")).lower() or "e2e" in str(r.get("title", "")).lower()
    ]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "live_count": len(live_uids),
        "compiled_count": len(compiled_uids),
        "yaml_rule_ids": yaml_ids,
        "yaml_rule_id_count": len(yaml_ids),
        "matched_count": len(matched),
        "repo_only_count": len(repo_only),
        "live_only_count": len(live_only),
        "title_matched_count": len(title_matched),
        "repo_only_title_count": len(repo_only_titles),
        "live_only_title_count": len(live_only_titles),
        "repo_only_titles_sample": repo_only_titles[:30],
        "live_only_titles_sample": live_only_titles[:30],
        "repo_only_uids": [
            {"uid": u, "title": compiled_titles.get(u, "")} for u in repo_only
        ],
        "live_only_uids": [
            {"uid": u, "title": live_by_uid[u].get("title")} for u in live_only
        ],
        "matched_sample": matched[:20],
        "missing_metric_annotation_count": len(missing_metric),
        "missing_threshold_annotation_count": len(missing_threshold),
        "e2e_candidates": e2e,
        "live_by_group": dict(
            sorted(Counter(r.get("ruleGroup", "?") for r in live).items())
        ),
        "policy": {
            "repo_only": "excluded_not_applied - treat as intentionally deleted or never approved",
            "live_only": "annotation-patch candidates from live export (preserve UID)",
            "matched": "annotation-patch candidates",
            "note": "Live UIDs may differ from frozen compiled_rules.json; prefer title match + live allowlist",
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    emit_result({**report, "output": str(args.output)}, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
