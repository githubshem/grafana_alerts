#!/usr/bin/env python3
"""Validate dashboard URLs embedded in alert rule annotations."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from grafana_client import configure_logging, emit_result
from rulebuilder import extract_dashboard_urls_from_rules

logger = logging.getLogger(__name__)

PROVISIONING_ROOT = SCRIPT_DIR.parent
DEFAULT_INVENTORY = PROVISIONING_ROOT / "inventory" / "services.yaml"
DEFAULT_RULES = PROVISIONING_ROOT / "templates" / "rule_definitions.yaml"

DASHBOARD_UID_PATTERN = re.compile(r"/d/([a-zA-Z0-9_-]+)/")


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def compile_rules_from_files(inventory_path: Path, rules_path: Path) -> list[dict]:
    from apply_rules import compile_rules

    inventory_doc = load_yaml(inventory_path)
    rules_doc = load_yaml(rules_path)
    inventory = inventory_doc.get("inventory", inventory_doc)
    return compile_rules(inventory, rules_doc.get("rules", []), rules_doc.get("defaults", {}))


def _check_url(url: str, *, timeout: int, api_key: str | None) -> dict[str, object]:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return {"url": url, "valid": False, "error": "malformed URL"}

    uid_match = DASHBOARD_UID_PATTERN.search(parsed.path)
    if not uid_match:
        return {"url": url, "valid": False, "error": "no dashboard UID in path"}

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    check_url = f"{parsed.scheme}://{parsed.netloc}/api/dashboards/uid/{uid_match.group(1)}"
    try:
        req = Request(check_url, headers=headers, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return {"url": url, "valid": False, "error": f"HTTP {resp.status}"}
        return {"url": url, "valid": True, "dashboard_uid": uid_match.group(1)}
    except HTTPError as exc:
        return {"url": url, "valid": False, "error": f"HTTP {exc.code}"}
    except URLError as exc:
        return {"url": url, "valid": False, "error": str(exc.reason)}


def validate_urls(
    urls: list[dict[str, str]],
    *,
    timeout: int,
    api_key: str | None,
) -> dict[str, object]:
    seen: set[str] = set()
    results: list[dict[str, object]] = []
    for item in urls:
        url = item["url"]
        if url in seen:
            continue
        seen.add(url)
        check = _check_url(url, timeout=timeout, api_key=api_key)
        check["rule"] = item.get("rule", "")
        results.append(check)
        status = "OK" if check["valid"] else "FAIL"
        logger.info("%s %s", status, url)

    invalid = [r for r in results if not r["valid"]]
    return {
        "total": len(results),
        "valid": len(results) - len(invalid),
        "invalid": len(invalid),
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Grafana dashboard URLs in rule annotations")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--rules-json", type=Path, default=None, help="Pre-compiled rules JSON file")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level, json_output=args.json)

    import os

    api_key = os.environ.get("GRAFANA_API_KEY")

    if args.rules_json:
        rules = json.loads(args.rules_json.read_text(encoding="utf-8"))
    else:
        rules = compile_rules_from_files(args.inventory, args.rules)

    urls = extract_dashboard_urls_from_rules(rules)
    summary = validate_urls(urls, timeout=args.timeout, api_key=api_key)
    result = {"status": "ok" if summary["invalid"] == 0 else "error", **summary}
    emit_result(result, args.json)
    return 0 if summary["invalid"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
