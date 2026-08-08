"""Golden master: the refactor must reproduce the baseline exactly."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _sha256(rules: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()


def test_rule_count_matches_baseline(compiled_rules, baseline_rules):
    assert len(compiled_rules) == len(baseline_rules) == 62


def test_sha256_matches_baseline(compiled_rules, baseline_rules):
    assert _sha256(compiled_rules) == _sha256(baseline_rules)


def test_manifest_sha256_still_holds(compiled_rules, baseline_manifest):
    assert _sha256(compiled_rules) == baseline_manifest["sha256"]


def test_no_added_or_removed_uids(compiled_rules, baseline_rules):
    assert {r["uid"] for r in compiled_rules} == {r["uid"] for r in baseline_rules}


def test_uids_are_unique(compiled_rules):
    uids = [r["uid"] for r in compiled_rules]
    assert len(uids) == len(set(uids))


def test_every_rule_matches_field_by_field(compiled_rules, baseline_rules):
    base = {r["uid"]: r for r in baseline_rules}
    for rule in compiled_rules:
        expected = base[rule["uid"]]
        for field in ("title", "ruleGroup", "folderUID", "condition", "for", "isPaused"):
            assert rule[field] == expected[field], f"{rule['uid']} {field}"
        assert rule["annotations"] == expected["annotations"], rule["uid"]
        assert rule["labels"] == expected["labels"], rule["uid"]
        assert rule["data"] == expected["data"], rule["uid"]
        assert rule["notification_settings"] == expected["notification_settings"], rule["uid"]


def test_routing_is_uniform(compiled_rules):
    from config import CONTACT_POINT, FOLDER_UID

    assert {r["folderUID"] for r in compiled_rules} == {FOLDER_UID}
    assert {r["notification_settings"]["receiver"] for r in compiled_rules} == {CONTACT_POINT}


def test_no_rule_is_paused(compiled_rules):
    assert [r["uid"] for r in compiled_rules if r["isPaused"]] == []


def test_resource_type_distribution(compiled_rules):
    counts: dict[str, int] = {}
    for rule in compiled_rules:
        key = rule["_meta"]["resource_type"]
        counts[key] = counts.get(key, 0) + 1
    assert counts == {
        "ecs_service": 20,
        "redis": 10,
        "alb": 12,
        "rds": 12,
        "mq": 6,
        "nlb": 2,
    }


def test_severity_distribution(compiled_rules):
    counts: dict[str, int] = {}
    for rule in compiled_rules:
        key = rule["labels"]["severity"]
        counts[key] = counts.get(key, 0) + 1
    assert counts == {"critical": 32, "warning": 30}


def test_rule_group_count(compiled_rules):
    assert len({r["ruleGroup"] for r in compiled_rules}) == 9
