"""Shared fixtures. Compilation is fully offline: no Grafana, no AWS, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

INVENTORY_PATH = ROOT / "inventory" / "services.yaml"
RULES_PATH = ROOT / "templates" / "rule_definitions.yaml"
BASELINE_PATH = ROOT / "baseline" / "compiled_rules.baseline.json"
BASELINE_MANIFEST_PATH = ROOT / "baseline" / "baseline_manifest.json"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@pytest.fixture(scope="session")
def inventory() -> dict[str, Any]:
    doc = _load_yaml(INVENTORY_PATH)
    return doc.get("inventory", doc)


@pytest.fixture(scope="session")
def rules_doc() -> dict[str, Any]:
    return _load_yaml(RULES_PATH)


@pytest.fixture(scope="session")
def rule_specs(rules_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Rule specs with defaults merged in, exactly as apply_rules.compile_rules does."""
    defaults = rules_doc.get("defaults", {})
    return [{**defaults, **spec} for spec in rules_doc.get("rules", [])]


@pytest.fixture(scope="session")
def baseline_rules() -> list[dict[str, Any]]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def baseline_manifest() -> dict[str, Any]:
    return json.loads(BASELINE_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def compiled_rules(inventory: dict[str, Any], rules_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Recompile from source YAML through the public apply_rules entry point."""
    from apply_rules import compile_rules

    return compile_rules(
        inventory,
        rules_doc.get("rules", []),
        rules_doc.get("defaults", {}),
    )


@pytest.fixture(scope="session")
def build_ctx(inventory: dict[str, Any]):
    from config import BuildContext

    return BuildContext.from_inventory(inventory)


@pytest.fixture
def ecs_service_resource() -> dict[str, Any]:
    return {
        "name": "alpha-api",
        "cluster": "app-alpha",
        "service": "alpha-api",
        "region": "us-west-1",
    }


@pytest.fixture
def alb_resource() -> dict[str, Any]:
    return {
        "name": "alb-public",
        "cw_dimension": "app/alb-public/abc123",
        "primary_target_group_cw": "targetgroup/tg/def456",
        "target_count": 3,
        "region": "us-west-1",
    }
