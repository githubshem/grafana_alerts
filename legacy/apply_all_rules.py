#!/usr/bin/env python3
"""Apply all rules from compiled_rules.json using Grafana API (run on VPN host)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPILED = ROOT / "test-results" / "compiled_rules.json"
EXTRACT = Path(__file__).resolve().parent / "extract_rule.py"
BULK = Path(__file__).resolve().parent / "bulk_apply_compiled.py"


def main() -> int:
    if not COMPILED.exists():
        subprocess.check_call([
            sys.executable, str(ROOT / "scripts" / "apply_rules.py"),
            "--output", str(COMPILED), "--dry-run",
        ])
    return subprocess.call([
        sys.executable, str(BULK),
        "--rules-json", str(COMPILED),
        "--receiver", "stg-teams-alerts",
        "--json",
    ] + sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
