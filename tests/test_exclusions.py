"""Exclusions decide which resources get no rule at all."""

from __future__ import annotations

import pytest


def test_memory_baseline_missing_is_skipped():
    from rulebuilder.exclusions import should_skip_resource

    spec = {"skip_unless_baseline": "memory"}
    assert should_skip_resource(spec, {"name": "r"}) is True
    assert should_skip_resource(spec, {"name": "r", "computed_warning_freeable_memory_bytes": 1}) is False


def test_storage_baseline_missing_is_skipped():
    from rulebuilder.exclusions import should_skip_resource

    spec = {"skip_unless_baseline": "storage"}
    assert should_skip_resource(spec, {"name": "r"}) is True
    assert should_skip_resource(spec, {"name": "r", "computed_critical_free_storage_bytes": 1}) is False


def test_storage_skip_honours_custom_threshold_key():
    from rulebuilder.exclusions import should_skip_resource

    spec = {"skip_unless_baseline": "storage", "threshold_from_resource": "custom_storage"}
    assert should_skip_resource(spec, {"name": "r", "computed_critical_free_storage_bytes": 1}) is True
    assert should_skip_resource(spec, {"name": "r", "custom_storage": 1}) is False


def test_no_baseline_requirement_never_skips():
    from rulebuilder.exclusions import should_skip_resource

    assert should_skip_resource({}, {"name": "r"}) is False


def test_aurora_autoscaling_storage_is_paused_not_dropped():
    from rulebuilder.exclusions import is_aurora_pause_storage

    spec = {"skip_if": "aurora_auto_scaling_storage"}
    assert is_aurora_pause_storage(spec, {"aurora_auto_scaling_storage": True}) is True
    assert is_aurora_pause_storage(spec, {"aurora_auto_scaling_storage": False}) is False
    assert is_aurora_pause_storage({}, {"aurora_auto_scaling_storage": True}) is False


@pytest.mark.parametrize(
    "batch,is_canary,expected",
    [
        (2, True, True),    # batch 2 is the canary wave
        (2, False, False),
        (3, False, True),   # batch 3 is everything but the canaries
        (3, True, False),
        (1, True, True),    # other batches are unfiltered
        (1, False, True),
        (None, False, True),
    ],
)
def test_apply_batch_partitions_ecs_services(batch, is_canary, expected):
    from rulebuilder.exclusions import passes_batch_canary_filter

    spec = {"apply_batch": batch} if batch is not None else {}
    resource = {"name": "svc"}
    canaries = {"svc"} if is_canary else set()
    assert passes_batch_canary_filter(spec, resource, "ecs_service", canaries) is expected


def test_apply_batch_does_not_partition_non_ecs():
    from rulebuilder.exclusions import passes_batch_canary_filter

    spec = {"apply_batch": 2}
    assert passes_batch_canary_filter(spec, {"name": "lb"}, "alb", set()) is True


def test_canary_only_mode_drops_non_canaries():
    from rulebuilder.exclusions import passes_batch_canary_filter

    assert passes_batch_canary_filter(
        {}, {"name": "svc"}, "ecs_service", {"svc"}, ecs_canary_only=True
    ) is True
    assert passes_batch_canary_filter(
        {}, {"name": "other"}, "ecs_service", {"svc"}, ecs_canary_only=True
    ) is False


def test_disabled_specs_expand_to_nothing(inventory, build_ctx):
    from rulebuilder import expand_rules

    spec = {
        "id": "disabled-rule",
        "enabled": False,
        "resource_type": "ecs_service",
        "namespace": "AWS/ECS",
        "metric": "CPUUtilization",
        "threshold": 70,
    }
    assert expand_rules([spec], inventory, ctx=build_ctx) == []


def test_name_filter_restricts_expansion(inventory, build_ctx):
    from rulebuilder import expand_rules

    spec = {
        "id": "ecs-cpu-warning",
        "resource_type": "ecs_service",
        "namespace": "AWS/ECS",
        "metric": "CPUUtilization",
        "threshold": 70,
    }
    everything = expand_rules([spec], inventory, ctx=build_ctx)
    filtered = expand_rules([spec], inventory, ctx=build_ctx, name_filter="^alpha-")

    assert 0 < len(filtered) < len(everything)
    assert all(r["labels"]["resource"].startswith("alpha-") for r in filtered)


def test_ecs_cluster_filter_restricts_expansion(inventory, build_ctx):
    from rulebuilder import expand_rules

    cluster = inventory["ecs_services"][0]["cluster"]
    spec = {
        "id": "ecs-cpu-warning",
        "resource_type": "ecs_service",
        "namespace": "AWS/ECS",
        "metric": "CPUUtilization",
        "threshold": 70,
    }
    rules = expand_rules([spec], inventory, ctx=build_ctx, ecs_cluster=cluster)
    assert rules
    assert all(r["_meta"]["ecs_cluster"] == cluster for r in rules)


def test_inventory_has_no_ecs_clusters_so_cluster_rules_expand_to_zero(inventory, build_ctx):
    """Documented pre-existing gap: preserved deliberately, not fixed."""
    from rulebuilder import expand_rules

    assert not inventory.get("ecs_clusters")
    spec = {
        "id": "ecs-cluster-cpu",
        "resource_type": "ecs",
        "namespace": "AWS/ECS",
        "metric": "CPUUtilization",
        "threshold": 70,
    }
    assert expand_rules([spec], inventory, ctx=build_ctx) == []
