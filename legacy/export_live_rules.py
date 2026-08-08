#!/usr/bin/env python3
"""Export live Grafana alert rules, complete UID allowlist, and mutation denylist.

Supports --env staging | uat | all.
Allowlist includes FreeStorageSpace (mutation-denylisted; no PUT).
If a similar title/metric reappears, is_mutation_denylisted_rule still skips PUT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROVISIONING_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from grafana_client import DEFAULT_GRAFANA_URL, emit_result
from grafana_transport import list_alert_rules
from rule_builder import is_mutation_denylisted_rule
from teams_webhook_config import load_dotenv_if_present, value_looks_like_placeholder

ENV_CFG = {
    "staging": {
        "prefix": "stg-",
        "token_envs": ["GRAFANA_STAGING_WRITE_TOKEN", "GRAFANA_API_KEY"],
        "url_envs": ["GRAFANA_URL_STAGING", "GRAFANA_URL"],
        "default_url": DEFAULT_GRAFANA_URL,
        "stamp_suffix": "staging",
        "latest_dir": "live-staging-latest",
    },
    "uat": {
        "prefix": "uat-",
        "token_envs": [
            "GRAFANA_UAT_WRITE_TOKEN",
            "GRAFANA_UAT_WRITE_TOKEN",
            "GRAFANA_API_KEY",
        ],
        "url_envs": ["GRAFANA_URL_UAT", "GRAFANA_URL_UAT"],
        "default_url": "https://grafana-uat.example.com",
        "stamp_suffix": "uat",
        "latest_dir": "live-uat-latest",
    },
}


def _resolve_token(env_name: str, api_key: str | None) -> str:
    if api_key and not value_looks_like_placeholder(api_key):
        return api_key
    for env_var in ENV_CFG[env_name]["token_envs"]:
        token = os.environ.get(env_var, "").strip()
        if token and not value_looks_like_placeholder(token):
            return token
    raise RuntimeError(
        f"Missing Grafana token for {env_name}; set one of {ENV_CFG[env_name]['token_envs']}"
    )


def _resolve_base_url(env_name: str) -> str:
    cfg = ENV_CFG[env_name]
    for env_var in cfg["url_envs"]:
        url = (os.environ.get(env_var) or "").strip()
        if url:
            return url.rstrip("/")
    return str(cfg["default_url"]).rstrip("/")


def export_env(env_name: str, *, api_key: str | None, output_dir: Path | None) -> dict[str, Any]:
    cfg = ENV_CFG[env_name]
    token = _resolve_token(env_name, api_key)
    base_url = _resolve_base_url(env_name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = output_dir or (
        PROVISIONING_ROOT / "backups" / f"{stamp}-{cfg['stamp_suffix']}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    all_rules = list_alert_rules(base_url=base_url, token=token)
    prefix = cfg["prefix"]
    scoped = [r for r in all_rules if str(r.get("title", "")).startswith(prefix)]

    allowlist = []
    denylist_entries = []
    for r in scoped:
        uid = r.get("uid")
        denied, reason = is_mutation_denylisted_rule(r)
        row = {
            "uid": uid,
            "title": r.get("title"),
            "ruleGroup": r.get("ruleGroup"),
            "isPaused": r.get("isPaused"),
            "receiver": (r.get("notification_settings") or {}).get("receiver"),
            "has_metric": bool((r.get("annotations") or {}).get("metric")),
            "has_threshold": bool((r.get("annotations") or {}).get("threshold")),
            "has_unit": bool((r.get("annotations") or {}).get("unit")),
            "labels": r.get("labels") or {},
            "mutation_denylisted": denied,
            "denylist_reason": reason or None,
        }
        allowlist.append(row)
        if denied:
            denylist_entries.append(
                {
                    "uid": uid,
                    "title": r.get("title"),
                    "reason": reason,
                }
            )

    by_group = dict(sorted(Counter(r.get("ruleGroup", "?") for r in scoped).items()))
    live_path = out_dir / "live-alert-rules-pre-metadata.json"
    allowlist_path = out_dir / "uid-allowlist.json"
    denylist_path = out_dir / "mutation-denylist.json"
    summary_path = out_dir / "export_summary.json"

    live_path.write_text(json.dumps(scoped, indent=2), encoding="utf-8")
    allowlist_path.write_text(json.dumps(allowlist, indent=2), encoding="utf-8")
    denylist_payload = {
        "env": env_name,
        "uids": sorted(e["uid"] for e in denylist_entries if e.get("uid")),
        "entries": denylist_entries,
    }
    denylist_path.write_text(json.dumps(denylist_payload, indent=2), encoding="utf-8")

    summary = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "env": env_name,
        "grafana_url": base_url,
        "total_grafana_rules": len(all_rules),
        "scoped_count": len(scoped),
        "allowlist_count": len(allowlist),
        "denylist_count": len(denylist_entries),
        "by_rule_group": by_group,
        "allowlist_path": str(allowlist_path),
        "denylist_path": str(denylist_path),
        "live_rules_path": str(live_path),
        "uids": sorted(r["uid"] for r in scoped if r.get("uid")),
        "denylist_uids": sorted(e["uid"] for e in denylist_entries if e.get("uid")),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    latest_dir = PROVISIONING_ROOT / "test-results" / cfg["latest_dir"]
    latest_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("live-alert-rules-pre-metadata.json", scoped),
        ("uid-allowlist.json", allowlist),
        ("mutation-denylist.json", denylist_payload),
        ("export_summary.json", summary),
    ):
        (latest_dir / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {
        "status": "ok",
        "env": env_name,
        "scoped_count": len(scoped),
        "allowlist_count": len(allowlist),
        "denylist_count": len(denylist_entries),
        "by_rule_group": by_group,
        "output_dir": str(out_dir),
        "latest_dir": str(latest_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export live rules + complete UID allowlist + mutation denylist"
    )
    parser.add_argument("--env", choices=["staging", "uat", "all"], default="staging")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    load_dotenv_if_present()
    envs = ["staging", "uat"] if args.env == "all" else [args.env]
    results = []
    for env_name in envs:
        # Per-env output dirs when exporting all
        out = args.output_dir if args.env != "all" else None
        results.append(export_env(env_name, api_key=args.api_key, output_dir=out))

    if len(results) == 1:
        emit_result(results[0], args.json)
    else:
        emit_result({"status": "ok", "environments": results}, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
