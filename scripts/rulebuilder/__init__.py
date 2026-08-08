"""Grafana CloudWatch alert rule compilation.

Public API:
    build_rule  - compile a single rule from a spec and an inventory resource
    expand_rules - expand rule specs across all matching inventory resources
    extract_dashboard_urls_from_rules - pull dashboard URLs out of compiled rules
"""

from __future__ import annotations

from rulebuilder.compiler import build_rule, expand_rules, resolve_threshold, stable_uid
from rulebuilder.dashboards import build_dashboard_url, extract_dashboard_urls_from_rules

__all__ = [
    "build_dashboard_url",
    "build_rule",
    "expand_rules",
    "extract_dashboard_urls_from_rules",
    "resolve_threshold",
    "stable_uid",
]
