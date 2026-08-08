"""Annotation formatting drives what humans see in the Teams Adaptive Card."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "evaluator,value,expected",
    [
        ("gt", 80, "Above 80%"),
        ("gte", 80, "Above 80%"),
        ("lt", 20, "Below 20%"),
        ("lte", 20, "Below 20%"),
        ("gt", 82.5, "Above 82.5%"),
    ],
)
def test_percent_thresholds(evaluator, value, expected):
    from rulebuilder.annotations import format_threshold_display

    assert format_threshold_display(evaluator, value, unit="percent") == expected


def test_seconds_threshold():
    from rulebuilder.annotations import format_threshold_display

    assert format_threshold_display("gt", 3, unit="seconds") == "Above 3 s"
    assert format_threshold_display("gt", 1.5, unit="seconds") == "Above 1.5 s"


@pytest.mark.parametrize(
    "value,expected",
    [
        (1073741824, "1.00 GB"),
        (1610612736, "1.50 GB"),
        (1048576, "1.00 MB"),
        (1024, "1.00 KB"),
        (512, "512 B"),
    ],
)
def test_byte_ladder(value, expected):
    from rulebuilder.annotations import format_bytes_threshold_value

    assert format_bytes_threshold_value(value) == expected


def test_bytes_threshold_display():
    from rulebuilder.annotations import format_threshold_display

    assert format_threshold_display("lt", 1073741824, unit="bytes") == "Below 1.00 GB"


def test_unhealthy_host_count_is_rendered_as_whole_targets():
    """Grafana compares >0.5, but operators need to read '1 unhealthy target'."""
    from rulebuilder.annotations import format_threshold_display

    fmt = format_threshold_display
    assert fmt("gt", 0.5, unit="count", metric="UnHealthyHostCount") == "At least 1 unhealthy target"
    assert fmt("gt", 1, unit="count", metric="UnHealthyHostCount") == "At least 2 unhealthy targets"


def test_append_unit_label_is_idempotent():
    from rulebuilder.annotations import append_unit_label

    once = append_unit_label("Above 100", "connections")
    assert once == "Above 100 connections"
    assert append_unit_label(once, "connections") == once
    assert append_unit_label("Above 100", "") == "Above 100"


def test_unknown_unit_falls_back_to_label_suffix():
    from rulebuilder.annotations import format_threshold_display

    result = format_threshold_display("gt", 100, unit="unknown", unit_label="connections")
    assert result == "Above 100 connections"


def test_non_numeric_threshold_does_not_crash():
    from rulebuilder.annotations import format_threshold_display

    assert format_threshold_display("gt", "n/a") == "Above n/a"


@pytest.mark.parametrize(
    "metric,unit",
    [
        ("CPUUtilization", "percent"),
        ("TargetResponseTime", "seconds"),
        ("FreeableMemory", "bytes"),
        ("DatabaseConnections", "count"),
        ("SomethingUnmapped", "unknown"),
        (None, "unknown"),
    ],
)
def test_metric_unit_mapping(metric, unit):
    from rulebuilder.annotations import unit_for_metric

    assert unit_for_metric(metric) == unit


def test_rabbitmq_memory_ratio_is_reported_as_percent():
    from rulebuilder.annotations import unit_annotation_from_spec

    spec = {"query_mode": "metric_math_ratio", "metric": "RabbitMQMemUsed"}
    assert unit_annotation_from_spec(spec) == "percent"


def test_summary_falls_back_when_it_uses_grafana_label_templating():
    """`{{ $labels.x }}` renders literally in the card, so a plain summary is used."""
    from rulebuilder.annotations import resolve_summary

    context = {"resource": "svc", "cluster": "cl", "service": "svc",
               "cluster_name": "cl", "service_name": "svc"}
    spec = {"id": "ecs-cpu-warning", "summary": "CPU high on {{ $labels.ServiceName }}",
            "metric": "CPUUtilization"}
    assert resolve_summary(spec, "ecs_service", context) == "ECS CPU warning for svc on cl"


def test_summary_placeholders_are_formatted():
    from rulebuilder.annotations import resolve_summary

    context = {"resource": "svc", "cluster": "cl", "service": "svc",
               "cluster_name": "cl", "service_name": "svc"}
    spec = {"id": "x", "summary": "CPU high on {service} in {cluster}", "metric": "CPUUtilization"}
    assert resolve_summary(spec, "ecs_service", context) == "CPU high on svc in cl"


def test_unknown_placeholder_leaves_pattern_untouched():
    from rulebuilder.annotations import format_summary_pattern

    assert format_summary_pattern("hello {nope}", {"resource": "r"}) == "hello {nope}"


def test_runbook_prefers_explicit_value():
    from rulebuilder.annotations import build_runbook

    spec = {"runbook": "See wiki page 42"}
    identity = {"cluster_name": "cl", "service_name": "svc"}
    assert build_runbook(spec, "ecs_service", "svc", "us-west-1", identity) == "See wiki page 42"


def test_runbook_default_mentions_service_cluster_and_region():
    from rulebuilder.annotations import build_runbook

    identity = {"cluster_name": "cl", "service_name": "svc"}
    text = build_runbook({}, "ecs_service", "svc", "us-west-1", identity)
    assert "svc" in text and "cl" in text and "us-west-1" in text


def test_every_compiled_rule_has_the_adaptive_card_annotations(compiled_rules):
    required = {"summary", "description", "dashboard_url", "runbook",
                "metric", "threshold", "unit", "unit_label", "value_ref"}
    for rule in compiled_rules:
        assert required <= set(rule["annotations"]), rule["uid"]


def test_no_compiled_summary_leaks_grafana_templating(compiled_rules):
    for rule in compiled_rules:
        assert "{{" not in rule["annotations"]["summary"], rule["uid"]
