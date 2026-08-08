"""The static half of the inventory must be verifiable without AWS credentials.

generate_inventory.py needs AWS to collect resource lists, but its header
and dashboards block are static. They live in config.py so these assertions run
offline; no boto3 client is constructed here.
"""

from __future__ import annotations

import yaml


def test_header_matches_the_committed_inventory(inventory):
    from config import INVENTORY_HEADER

    for key, value in INVENTORY_HEADER.items():
        assert inventory[key] == value, key


def test_dashboards_block_matches_the_committed_inventory(inventory):
    from config import DASHBOARDS

    assert inventory["dashboards"] == DASHBOARDS


def test_generator_imports_the_shared_constants():
    """Guards against the generator re-growing its own copy of the dashboards."""
    import generate_inventory
    from config import INVENTORY_HEADER, DASHBOARDS

    assert generate_inventory.INVENTORY_HEADER is INVENTORY_HEADER
    assert generate_inventory.DASHBOARDS is DASHBOARDS


def test_generator_region_matches_config():
    import generate_inventory
    from config import REGION

    assert generate_inventory.AWS_REGION == REGION


def test_emitted_header_key_order_is_preserved(inventory):
    """Key order matters only for the diff of the generated YAML, but keep it stable."""
    from config import INVENTORY_HEADER

    emitted = list(inventory.keys())
    assert emitted[: len(INVENTORY_HEADER)] == list(INVENTORY_HEADER.keys())


def test_header_round_trips_through_yaml():
    from config import INVENTORY_HEADER, DASHBOARDS

    doc = {"inventory": {**INVENTORY_HEADER, "dashboards": DASHBOARDS}}
    reparsed = yaml.safe_load(yaml.dump(doc, default_flow_style=False, sort_keys=False))
    assert reparsed["inventory"]["dashboards"] == DASHBOARDS
    for key, value in INVENTORY_HEADER.items():
        assert reparsed["inventory"][key] == value


def test_ecs_cluster_filter_matches_every_inventory_cluster(inventory):
    import re

    pattern = re.compile(inventory["ecs_cluster_filter"])
    clusters = {s["cluster"] for s in inventory["ecs_services"]}
    assert clusters
    for cluster in clusters:
        assert pattern.search(cluster), cluster
