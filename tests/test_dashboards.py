"""Dashboard URLs are the link an engineer clicks from the alert."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def _params(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_ecs_dashboard_url_maps_cluster_and_service(build_ctx):
    from rulebuilder import build_dashboard_url

    url = build_dashboard_url("ecs", ctx=build_ctx, cluster="my-cluster", service="my-svc")
    assert "/d/example-ecs/aws-ecs" in url
    params = _params(url)
    assert params["var-cluster"] == ["my-cluster"]
    assert params["var-service"] == ["my-svc"]
    assert params["var-region"] == ["us-west-1"]
    assert params["orgId"] == ["1"]


def test_alb_dashboard_uses_capitalised_var_names(build_ctx):
    from rulebuilder import build_dashboard_url

    params = _params(build_dashboard_url("alb", ctx=build_ctx, lb="app/my-lb/abc"))
    assert params["var-LB"] == ["app/my-lb/abc"]
    assert params["var-Region"] == ["us-west-1"]


def test_redis_dashboard_var_name(build_ctx):
    from rulebuilder import build_dashboard_url

    params = _params(build_dashboard_url("redis", ctx=build_ctx, redis_cluster="cache-1"))
    assert params["var-RedisCluster"] == ["cache-1"]


def test_api_dashboard_falls_back_to_alb(build_ctx):
    """The api dashboard has no UID yet, so links must not 404."""
    from rulebuilder import build_dashboard_url

    url = build_dashboard_url("api", ctx=build_ctx, lb="app/my-lb/abc")
    assert "/d/example-alb/aws-alb-elb-monitoring-dashboard" in url


def test_unknown_dashboard_falls_back_to_alb(build_ctx):
    from rulebuilder import build_dashboard_url

    assert "/d/example-alb/" in build_dashboard_url("nonexistent", ctx=build_ctx)


def test_panel_id_adds_view_panel(build_ctx):
    from rulebuilder import build_dashboard_url

    params = _params(build_dashboard_url("ecs", ctx=build_ctx, panel_id=7, cluster="cl"))
    assert params["viewPanel"] == ["7"]
    assert params["orgId"] == ["1"]


def test_empty_var_values_are_omitted(build_ctx):
    from rulebuilder import build_dashboard_url

    assert "var-service" not in _params(build_dashboard_url("ecs", ctx=build_ctx, cluster="cl", service=""))


def test_dashboard_vars_per_resource_type(alb_resource, ecs_service_resource):
    from rulebuilder.dashboards import build_dashboard_vars
    from rulebuilder.resources import ecs_identity

    identity = ecs_identity(ecs_service_resource, "ecs_service", ecs_service_resource["name"])
    assert build_dashboard_vars(ecs_service_resource, "ecs_service", "svc", identity) == {
        "cluster": "app-alpha",
        "service": "alpha-api",
    }

    lb_identity = ecs_identity(alb_resource, "alb", alb_resource["name"])
    assert build_dashboard_vars(alb_resource, "alb", alb_resource["name"], lb_identity) == {
        "lb": "app/alb-public/abc123",
        "targetgroup": "targetgroup/tg/def456",
    }

    assert build_dashboard_vars({"name": "c"}, "redis", "c", {"cluster_name": ""}) == {
        "redis_cluster": "c"
    }
    assert build_dashboard_vars({"name": "b"}, "mq", "b", {"cluster_name": ""}) == {"broker": "b"}
    assert build_dashboard_vars(
        {"name": "db", "cluster": "dbc", "instance": "dbi"}, "rds", "db", {"cluster_name": ""}
    ) == {"db_cluster": "dbc", "db_instance": "dbi"}


def test_every_compiled_rule_has_an_absolute_dashboard_url(compiled_rules, build_ctx):
    for rule in compiled_rules:
        url = rule["annotations"]["dashboard_url"]
        assert url.startswith(build_ctx.grafana_url), rule["uid"]
        assert "/d/" in url, rule["uid"]


def test_extract_dashboard_urls_skips_rules_without_one():
    from rulebuilder import extract_dashboard_urls_from_rules

    rules = [
        {"title": "a", "annotations": {"dashboard_url": "https://x/d/1"}},
        {"title": "b", "annotations": {}},
        {"title": "c"},
    ]
    assert extract_dashboard_urls_from_rules(rules) == [{"rule": "a", "url": "https://x/d/1"}]
