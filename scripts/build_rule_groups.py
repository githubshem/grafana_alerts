#!/usr/bin/env python3
"""Build rule-group PUT payloads from compiled_rules.json for Grafana apply."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROVISIONING_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config import CONTACT_POINT, DEFAULT_GROUP_INTERVAL, FOLDER_UID, group_interval


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build rule group JSON files for Grafana PUT")
    p.add_argument("--rules-json", type=Path, default=PROVISIONING_ROOT / "output" / "compiled_rules.json")
    p.add_argument("--out-dir", type=Path, default=PROVISIONING_ROOT / "output" / "rule_groups")
    p.add_argument("--receiver", default=CONTACT_POINT)
    p.add_argument("--folder-uid", default=FOLDER_UID)
    p.add_argument("--default-interval", type=int, default=DEFAULT_GROUP_INTERVAL)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    rules = json.loads(args.rules_json.read_text(encoding="utf-8"))
    groups: dict[str, list] = defaultdict(list)
    for rule in rules:
        r = {k: v for k, v in rule.items() if k != "_meta"}
        r["notification_settings"] = {"receiver": args.receiver}
        groups[r["ruleGroup"]].append(r)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for gname, grules in sorted(groups.items()):
        interval = group_interval(gname, args.default_interval)
        payload = {
            "title": gname,
            "folderUid": args.folder_uid,
            "interval": interval,
            "rules": grules,
        }
        safe = gname.replace("/", "_")
        out = args.out_dir / f"{safe}.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"{gname}: {len(grules)} rules interval={interval}s -> {out.name}")
        manifest.append({"group": gname, "rules": len(grules), "interval": interval, "file": out.name})
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {len(manifest)} rule groups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
