"""Exclusion and filter helpers for rule expansion."""

from __future__ import annotations

from typing import Any


def should_skip_resource(spec: dict[str, Any], resource: dict[str, Any]) -> bool:
    if spec.get("skip_unless_baseline") == "memory":
        if resource.get("computed_warning_freeable_memory_bytes") is None:
            return True
    if spec.get("skip_unless_baseline") == "storage":
        key = spec.get("threshold_from_resource", "computed_critical_free_storage_bytes")
        if resource.get(key) is None:
            return True
    return False


def is_aurora_pause_storage(spec: dict[str, Any], resource: dict[str, Any]) -> bool:
    """Aurora clusters with auto-scaling storage get a paused rule instead of being dropped."""
    return (
        spec.get("skip_if") == "aurora_auto_scaling_storage"
        and bool(resource.get("aurora_auto_scaling_storage"))
    )


def passes_batch_canary_filter(
    spec: dict[str, Any],
    resource: dict[str, Any],
    resource_type: str,
    canary_names: set[str],
    *,
    ecs_canary_only: bool = False,
) -> bool:
    """Return True if the resource should be included for this spec."""
    is_canary = resource.get("name") in canary_names
    spec_batch = spec.get("apply_batch")

    if resource_type == "ecs_service" and spec_batch in (2, 3):
        if spec_batch == 2 and not is_canary:
            return False
        if spec_batch == 3 and is_canary:
            return False

    if ecs_canary_only and not is_canary:
        return False

    return True
