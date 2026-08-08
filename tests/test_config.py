"""config.py is the single source of truth; it must agree with the inventory YAML."""

from __future__ import annotations


def test_constants_match_the_committed_inventory(inventory):
    from config import (
        CONTACT_POINT,
        DATASOURCE_UID,
        DEFAULT_GRAFANA_URL,
        FOLDER_UID,
        REGION,
        RULE_PREFIX,
    )

    assert inventory["grafana_url"] == DEFAULT_GRAFANA_URL
    assert inventory["folder_uid"] == FOLDER_UID
    assert inventory["datasource_uid"] == DATASOURCE_UID
    assert inventory["region"] == REGION
    assert inventory["rule_prefix"] == RULE_PREFIX
    assert inventory["contact_point"] == CONTACT_POINT


def test_build_context_from_inventory(inventory):
    from config import BuildContext

    ctx = BuildContext.from_inventory(inventory)
    assert ctx.rule_prefix == inventory["rule_prefix"]
    assert ctx.folder_uid == inventory["folder_uid"]
    assert ctx.datasource_uid == inventory["datasource_uid"]
    assert ctx.grafana_url == inventory["grafana_url"].rstrip("/")
    assert ctx.contact_point == inventory["contact_point"]


def test_build_context_defaults_without_inventory():
    from config import CONTACT_POINT, FOLDER_UID, RULE_PREFIX, BuildContext

    ctx = BuildContext()
    assert (ctx.rule_prefix, ctx.folder_uid, ctx.contact_point) == (
        RULE_PREFIX,
        FOLDER_UID,
        CONTACT_POINT,
    )


def test_build_context_dashboards_are_not_shared_between_instances():
    from config import DASHBOARDS, BuildContext

    a, b = BuildContext(), BuildContext()
    a.dashboards["ecs"] = {"uid": "mutated"}
    assert b.dashboards["ecs"]["uid"] == DASHBOARDS["ecs"]["uid"]


def test_trailing_slash_in_grafana_url_is_normalised():
    from config import BuildContext

    ctx = BuildContext.from_inventory({"grafana_url": "https://example.com/"})
    assert ctx.grafana_url == "https://example.com"


def test_default_labels_applied_to_every_rule(compiled_rules):
    from config import DEFAULT_LABELS

    for rule in compiled_rules:
        for key, value in DEFAULT_LABELS.items():
            assert rule["labels"][key] == value, rule["uid"]


def test_group_intervals():
    from config import DEFAULT_GROUP_INTERVAL, group_interval

    assert group_interval("alerts-availability-critical") == 60
    assert group_interval("alerts-canary") == 60
    assert group_interval("anything-availability-critical") == 60
    assert group_interval("alerts-alb") == DEFAULT_GROUP_INTERVAL
    assert group_interval("alerts-alb", 120) == 120


def test_env_profiles_only_defines_the_default_profile():
    from config import ENV_PROFILES

    assert set(ENV_PROFILES) == {"default"}
    assert ENV_PROFILES["default"]["inventory"] == "inventory/services.yaml"
    assert ENV_PROFILES["default"]["rules"] == "templates/rule_definitions.yaml"


def test_env_profile_paths_exist():
    from pathlib import Path

    from config import ENV_PROFILES

    root = Path(__file__).resolve().parent.parent
    for profile in ENV_PROFILES.values():
        assert (root / profile["inventory"]).is_file()
        assert (root / profile["rules"]).is_file()


def test_secrets_manager_config_is_pinned():
    from config import REGION, SECRETS_MANAGER_REGION, SECRETS_MANAGER_SECRET_NAME

    assert SECRETS_MANAGER_SECRET_NAME == "grafana-alerts/provisioning"
    assert SECRETS_MANAGER_REGION == REGION
