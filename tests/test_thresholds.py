"""Threshold resolution: the value that decides whether an alert fires."""

from __future__ import annotations

import pytest


def test_plain_threshold_and_default_evaluator():
    from rulebuilder.compiler import resolve_threshold

    assert resolve_threshold({"id": "x", "threshold": 80}, {"name": "r"}) == (80.0, "gt")


def test_explicit_evaluator_is_honoured():
    from rulebuilder.compiler import resolve_threshold

    spec = {"id": "x", "threshold": 5, "evaluator": "lt"}
    assert resolve_threshold(spec, {"name": "r"}) == (5.0, "lt")


def test_threshold_bytes_defaults_to_lt():
    from rulebuilder.compiler import resolve_threshold

    spec = {"id": "redis-mem", "threshold_bytes": 1073741824}
    assert resolve_threshold(spec, {"name": "r"}) == (1073741824.0, "lt")


def test_threshold_from_resource_reads_the_baseline():
    from rulebuilder.compiler import resolve_threshold

    spec = {"id": "rds-mem", "threshold_from_resource": "memory_baseline"}
    resource = {"name": "db", "memory_baseline": 2147483648}
    assert resolve_threshold(spec, resource) == (2147483648.0, "lt")


def test_missing_resource_baseline_raises():
    from rulebuilder.compiler import resolve_threshold

    spec = {"id": "rds-mem", "threshold_from_resource": "memory_baseline"}
    with pytest.raises(ValueError, match="memory_baseline"):
        resolve_threshold(spec, {"name": "db"})


def test_missing_threshold_raises():
    from rulebuilder.compiler import resolve_threshold

    with pytest.raises(ValueError, match="missing threshold"):
        resolve_threshold({"id": "no-threshold"}, {"name": "r"})


def test_aurora_storage_pause_overrides_threshold():
    from rulebuilder.compiler import resolve_threshold

    spec = {"id": "rds-storage", "pause_if_aurora_storage": True, "threshold_bytes": 1, "evaluator": "lt"}
    resource = {"name": "aurora-db", "aurora_auto_scaling_storage": True}
    assert resolve_threshold(spec, resource) == (1.0, "lt")


@pytest.mark.parametrize("rule_id", ["alb-unhealthy-critical", "nlb-unhealthy-critical"])
def test_single_target_unhealthy_host_uses_half(rule_id):
    """One target behind the LB: >0.5 fires at 1 unhealthy host, not 2."""
    from rulebuilder.compiler import resolve_threshold

    spec = {"id": rule_id, "threshold": 1, "severity": "critical"}
    assert resolve_threshold(spec, {"name": "lb", "target_count": 1}) == (0.5, "gt")


def test_multi_target_unhealthy_host_keeps_declared_threshold():
    from rulebuilder.compiler import resolve_threshold

    spec = {"id": "alb-unhealthy-critical", "threshold": 1, "severity": "critical"}
    assert resolve_threshold(spec, {"name": "lb", "target_count": 3}) == (1.0, "gt")


def test_unhealthy_host_warning_is_not_special_cased():
    from rulebuilder.compiler import resolve_threshold

    spec = {"id": "alb-unhealthy-warning", "threshold": 1, "severity": "warning"}
    assert resolve_threshold(spec, {"name": "lb", "target_count": 1}) == (1.0, "gt")


def test_missing_target_count_is_treated_as_multi_target():
    from rulebuilder.compiler import resolve_threshold

    spec = {"id": "alb-unhealthy-critical", "threshold": 1, "severity": "critical"}
    assert resolve_threshold(spec, {"name": "lb"}) == (1.0, "gt")
