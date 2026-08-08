"""CloudWatch dimensions decide which resource a rule actually watches."""

from __future__ import annotations


def test_explicit_resource_dimensions_win():
    from rulebuilder.resources import build_dimensions

    resource = {"name": "r", "dimensions": {"Custom": "value"}}
    spec = {"dimension_keys": {"ClusterName": "cluster"}}
    assert build_dimensions(spec, resource, "ecs_service", "r") == {"Custom": "value"}


def test_no_dimension_keys_yields_no_dimensions():
    from rulebuilder.resources import build_dimensions

    assert build_dimensions({}, {"name": "r"}, "ecs_service", "r") == {}


def test_ecs_service_dimensions():
    from rulebuilder.resources import build_dimensions

    resource = {"name": "svc", "cluster": "my-cluster", "service": "svc"}
    spec = {"dimension_keys": {"ClusterName": "cluster", "ServiceName": "service"}}
    assert build_dimensions(spec, resource, "ecs_service", "svc") == {
        "ClusterName": "my-cluster",
        "ServiceName": "svc",
    }


def test_cluster_level_ecs_uses_wildcard_service():
    """Cluster-level ECS rules aggregate across services via ServiceName=*."""
    from rulebuilder.resources import build_dimensions

    resource = {"name": "my-cluster"}
    spec = {"dimension_keys": {"ClusterName": "cluster", "ServiceName": "service"}}
    dims = build_dimensions(spec, resource, "ecs", "my-cluster")
    assert dims == {"ClusterName": "my-cluster", "ServiceName": "*"}


def test_load_balancer_uses_cw_dimension():
    from rulebuilder.resources import build_dimensions

    resource = {"name": "lb", "cw_dimension": "app/lb/abc123"}
    spec = {"dimension_keys": {"LoadBalancer": "cw_dimension"}}
    assert build_dimensions(spec, resource, "alb", "lb") == {"LoadBalancer": "app/lb/abc123"}


def test_mq_broker_id_dimension():
    from rulebuilder.resources import build_dimensions

    resource = {"name": "broker", "broker_id": "b-123"}
    spec = {"dimension_keys": {"Broker": "broker_id"}}
    assert build_dimensions(spec, resource, "mq", "broker") == {"Broker": "b-123"}


def test_name_fallback_when_key_absent():
    from rulebuilder.resources import build_dimensions

    spec = {"dimension_keys": {"CacheClusterId": "name"}}
    assert build_dimensions(spec, {"name": "redis-1"}, "redis", "redis-1") == {
        "CacheClusterId": "redis-1"
    }


def test_ecs_identity_for_cluster_and_service():
    from rulebuilder.resources import ecs_identity

    assert ecs_identity({}, "ecs", "cl") == {"cluster_name": "cl", "service_name": None}
    assert ecs_identity({"cluster": "cl", "service": "svc"}, "ecs_service", "svc") == {
        "cluster_name": "cl",
        "service_name": "svc",
    }
    assert ecs_identity({}, "alb", "lb") == {"cluster_name": "", "service_name": None}


def test_apply_ecs_labels_only_touches_ecs():
    from rulebuilder.resources import apply_ecs_labels

    labels: dict[str, str] = {}
    apply_ecs_labels(labels, "alb", {"cluster_name": "cl", "service_name": "svc"})
    assert labels == {}

    apply_ecs_labels(labels, "ecs_service", {"cluster_name": "cl", "service_name": "svc"})
    assert labels == {"ClusterName": "cl", "ServiceName": "svc"}


def test_apply_ecs_labels_omits_empty_service():
    from rulebuilder.resources import apply_ecs_labels

    labels: dict[str, str] = {}
    apply_ecs_labels(labels, "ecs", {"cluster_name": "cl", "service_name": None})
    assert labels == {"ClusterName": "cl"}
