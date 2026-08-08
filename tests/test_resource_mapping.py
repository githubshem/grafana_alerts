"""Resource-type mapping decides which inventory list a spec expands over."""

from __future__ import annotations

import pytest

SAMPLE_INVENTORY = {
    "ecs_services": [{"name": "svc", "cluster": "cl"}],
    "load_balancers": [
        {"name": "app-lb", "type": "application"},
        {"name": "api-lb", "type": "application", "api_facing": True},
        {"name": "net-lb", "type": "network"},
        {"name": "implicit-lb"},
    ],
    "rds_clusters": [{"name": "db"}],
    "redis_groups": [{"name": "cache"}],
    "mq_brokers": [{"name": "broker"}],
}


def _names(resource_type: str) -> list[str]:
    from rulebuilder.resources import resources_for_type

    return [r["name"] for r in resources_for_type(SAMPLE_INVENTORY, resource_type)]


def test_alb_selects_application_load_balancers_including_untyped():
    """A load balancer with no explicit type defaults to 'application'."""
    assert _names("alb") == ["app-lb", "api-lb", "implicit-lb"]


def test_nlb_selects_only_network_load_balancers():
    assert _names("nlb") == ["net-lb"]


def test_api_alb_requires_the_api_facing_flag():
    assert _names("api_alb") == ["api-lb"]


@pytest.mark.parametrize(
    "resource_type,expected",
    [
        ("ecs_service", ["svc"]),
        ("rds", ["db"]),
        ("redis", ["cache"]),
        ("mq", ["broker"]),
    ],
)
def test_simple_mappings(resource_type, expected):
    assert _names(resource_type) == expected


def test_missing_inventory_key_yields_empty_list():
    from rulebuilder.resources import resources_for_type

    assert resources_for_type({}, "ecs_service") == []
    assert resources_for_type(SAMPLE_INVENTORY, "ecs") == []


def test_unknown_resource_type_falls_back_to_its_own_key():
    from rulebuilder.resources import resources_for_type

    inv = {"custom_things": [{"name": "thing"}]}
    assert resources_for_type(inv, "custom_things") == [{"name": "thing"}]


def test_real_inventory_partitions_load_balancers(inventory):
    from rulebuilder.resources import resources_for_type

    albs = resources_for_type(inventory, "alb")
    nlbs = resources_for_type(inventory, "nlb")
    assert albs and nlbs
    assert not {r["name"] for r in albs} & {r["name"] for r in nlbs}
    assert len(albs) + len(nlbs) == len(inventory["load_balancers"])
