"""UIDs are the identity Grafana uses to update rather than duplicate a rule.

Any change to the seed format reassigns all UIDs, which on apply would
replace every existing alert and lose alert state/silences.
"""

from __future__ import annotations

import pytest


def test_seed_format_is_pinned_for_non_ecs_service():
    from rulebuilder.compiler import stable_uid

    spec = {"id": "alb-5xx-warning", "metric": "HTTPCode_Target_5XX_Count"}
    seed = f"alerts-{spec['id']}:my-lb:{spec['metric']}"
    assert stable_uid(seed) == stable_uid("alerts-alb-5xx-warning:my-lb:HTTPCode_Target_5XX_Count")
    assert len(stable_uid(seed)) == 12


def test_stable_uid_is_deterministic():
    from rulebuilder.compiler import stable_uid

    assert stable_uid("a:b:c") == stable_uid("a:b:c")
    assert stable_uid("a:b:c") != stable_uid("a:b:d")


def test_ecs_service_seed_includes_cluster(build_ctx, ecs_service_resource):
    """Two clusters with an identically named service must not collide."""
    from rulebuilder import build_rule

    spec = {
        "id": "ecs-cpu-warning",
        "resource_type": "ecs_service",
        "namespace": "AWS/ECS",
        "metric": "CPUUtilization",
        "threshold": 70,
        "severity": "warning",
    }
    other = {**ecs_service_resource, "cluster": "app-beta"}

    a = build_rule(spec, ecs_service_resource, ctx=build_ctx)
    b = build_rule(spec, other, ctx=build_ctx)
    assert a["uid"] != b["uid"]
    assert a["title"] != b["title"]


def test_all_baseline_uid_title_pairs_are_reproduced(compiled_rules, baseline_rules):
    assert sorted((r["uid"], r["title"]) for r in compiled_rules) == sorted(
        (r["uid"], r["title"]) for r in baseline_rules
    )


def test_baseline_per_rule_hashes_still_match(compiled_rules, baseline_manifest):
    """Guards against a field changing inside a rule while count and UIDs hold."""
    import hashlib
    import json

    per_rule = baseline_manifest.get("rules")
    if not per_rule:
        pytest.skip("baseline manifest has no per-rule hashes")

    expected = {entry["uid"]: entry["sha256"] for entry in per_rule}
    for rule in compiled_rules:
        digest = hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest()
        assert digest == expected[rule["uid"]], f"rule {rule['uid']} changed"
