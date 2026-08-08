#!/usr/bin/env python3
"""Generate services.yaml from live AWS inventory (read-only)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config import INVENTORY_HEADER, REGION, DASHBOARDS

DEFAULT_OUTPUT = ROOT / "inventory" / "services.yaml"
DEFAULT_EXCLUDED = ROOT / "output" / "excluded_resources.json"

AWS_PROFILE = "example"
AWS_REGION = REGION

TARGET_LBS = [
    "nlb-infra",
    "alb-internal",
    "alb-public",
]

TARGET_RDS_CLUSTERS = [
    "db-primary",
]
TARGET_RDS_STANDALONE = ["db-auth"]

TARGET_REDIS = [
    "cache-alpha",
    "cache-beta",
]

TARGET_MQ = ["mq-main"]

NAMED_ECS_CLUSTERS = [
    "app-alpha",
    "app-beta",
    "infra",
]

# Memory GiB by instance class (approximate; for FreeableMemory baselines).
INSTANCE_MEMORY_GIB = {
    "db.t3.medium": 4.0,
    "db.t3.large": 8.0,
    "db.r5.large": 16.0,
    "db.r5.xlarge": 32.0,
    "db.r5.2xlarge": 64.0,
    "db.r6g.large": 16.0,
    "db.r6g.xlarge": 32.0,
    "db.r6g.2xlarge": 64.0,
    "db.r6g.4xlarge": 128.0,
    "db.r7g.large": 16.0,
    "db.r7g.xlarge": 32.0,
    "db.r7g.2xlarge": 64.0,
}


def aws_json(args: list[str]) -> Any:
    cmd = ["aws", *args, "--profile", AWS_PROFILE, "--region", AWS_REGION, "--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"AWS CLI failed: {' '.join(cmd)}\n{result.stderr}")
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def _cw_dimension(lb_arn: str) -> str:
    if "loadbalancer/" in lb_arn:
        return lb_arn.split(":loadbalancer/")[-1]
    return lb_arn


def _is_in_scope_cluster(name: str) -> bool:
    if name in NAMED_ECS_CLUSTERS:
        return True
    if name.startswith("app-") or name.startswith("infra"):
        return True
    return False


def collect_ecs() -> tuple[list[dict], list[dict]]:
    arns = aws_json(["ecs", "list-clusters", "--query", "clusterArns[]"]) or []
    clusters = sorted(a.split("/")[-1] for a in arns)
    in_scope = [c for c in clusters if _is_in_scope_cluster(c)]
    services: list[dict] = []
    excluded: list[dict] = []

    for cluster in in_scope:
        svc_arns = aws_json(
            ["ecs", "list-services", "--cluster", cluster, "--query", "serviceArns[]"]
        ) or []
        for i in range(0, len(svc_arns), 10):
            chunk = svc_arns[i : i + 10]
            described = aws_json(
                [
                    "ecs",
                    "describe-services",
                    "--cluster",
                    cluster,
                    "--services",
                    *chunk,
                    "--query",
                    "services[].{n:serviceName,d:desiredCount,r:runningCount,s:status}",
                ]
            ) or []
            for s in described:
                row = {
                    "name": s["n"],
                    "cluster": cluster,
                    "service": s["n"],
                    "desired_count": int(s.get("d") or 0),
                    "running_count": int(s.get("r") or 0),
                    "status": s.get("s") or "",
                }
                if row["desired_count"] == 0 and row["running_count"] == 0:
                    excluded.append({**row, "reason": "idle desired=0 running=0"})
                    continue
                if row["status"] and row["status"] != "ACTIVE":
                    excluded.append({**row, "reason": f"status={row['status']}"})
                    continue
                services.append(
                    {
                        "name": row["name"],
                        "cluster": row["cluster"],
                        "service": row["service"],
                        "desired_count": row["desired_count"],
                        "running_count": row["running_count"],
                    }
                )

    services.sort(key=lambda x: (x["cluster"], x["name"]))
    return services, excluded


def collect_lbs() -> list[dict]:
    lbs = aws_json(
        [
            "elbv2",
            "describe-load-balancers",
            "--names",
            *TARGET_LBS,
            "--query",
            "LoadBalancers[].{Name:LoadBalancerName,Type:Type,Arn:LoadBalancerArn,Scheme:Scheme}",
        ]
    ) or []
    by_name = {lb["Name"]: lb for lb in lbs}
    missing = [n for n in TARGET_LBS if n not in by_name]
    if missing:
        raise RuntimeError(f"Missing load balancers: {missing}")

    out = []
    for name in TARGET_LBS:
        lb = by_name[name]
        lb_type = "network" if lb["Type"] == "network" else "application"
        out.append(
            {
                "name": name,
                "type": lb_type,
                "cw_dimension": _cw_dimension(lb["Arn"]),
                "scheme": lb.get("Scheme"),
                "target_count": 2,
                "api_facing": False,
            }
        )
    return out


def collect_rds() -> list[dict]:
    clusters = aws_json(
        [
            "rds",
            "describe-db-clusters",
            "--query",
            "DBClusters[].{id:DBClusterIdentifier,engine:Engine,members:DBClusterMembers[].DBInstanceIdentifier}",
        ]
    ) or []
    instances = aws_json(
        [
            "rds",
            "describe-db-instances",
            "--query",
            "DBInstances[].{id:DBInstanceIdentifier,class:DBInstanceClass,engine:Engine,cluster:DBClusterIdentifier,storage:AllocatedStorage}",
        ]
    ) or []
    inst_by_id = {i["id"]: i for i in instances}
    out: list[dict] = []

    for cluster in clusters:
        cid = cluster["id"]
        if cid not in TARGET_RDS_CLUSTERS:
            continue
        for member in cluster.get("members") or []:
            inst = inst_by_id.get(member, {})
            cls = inst.get("class") or "db.r7g.large"
            mem = INSTANCE_MEMORY_GIB.get(cls, 16.0)
            warn_bytes = mem * 0.20 * (1024**3)
            crit_bytes = mem * 0.10 * (1024**3)
            out.append(
                {
                    "name": member,
                    "cluster": cid,
                    "instance": member,
                    "instance_class": cls,
                    "engine": inst.get("engine") or cluster.get("engine"),
                    "max_allocated_memory_gib": mem,
                    "computed_warning_freeable_memory_bytes": warn_bytes,
                    "computed_critical_freeable_memory_bytes": crit_bytes,
                    "aurora_auto_scaling_storage": True,
                    "allocated_storage_gib": 1,
                    "memory_baseline": "needs_baseline",
                    "storage_baseline": "needs_baseline",
                }
            )

    for sid in TARGET_RDS_STANDALONE:
        inst = inst_by_id.get(sid)
        if not inst:
            raise RuntimeError(f"Missing RDS instance: {sid}")
        cls = inst.get("class") or "db.t3.medium"
        mem = INSTANCE_MEMORY_GIB.get(cls, 4.0)
        warn_bytes = mem * 0.20 * (1024**3)
        crit_bytes = mem * 0.10 * (1024**3)
        out.append(
            {
                "name": sid,
                "cluster": sid,
                "instance": sid,
                "instance_class": cls,
                "engine": inst.get("engine"),
                "max_allocated_memory_gib": mem,
                "computed_warning_freeable_memory_bytes": warn_bytes,
                "computed_critical_freeable_memory_bytes": crit_bytes,
                "aurora_auto_scaling_storage": False,
                "allocated_storage_gib": inst.get("storage") or 200,
                "memory_baseline": "needs_baseline",
                "storage_baseline": "needs_baseline",
            }
        )

    out.sort(key=lambda x: x["name"])
    return out


def collect_redis() -> list[dict]:
    groups = aws_json(
        [
            "elasticache",
            "describe-replication-groups",
            "--query",
            "ReplicationGroups[].{Id:ReplicationGroupId,Nodes:MemberClusters,Status:Status}",
        ]
    ) or []
    by_id = {g["Id"]: g for g in groups}
    out: list[dict] = []
    for rid in TARGET_REDIS:
        g = by_id.get(rid)
        if not g:
            raise RuntimeError(f"Missing Redis replication group: {rid}")
        for node in g.get("Nodes") or []:
            out.append({"name": node, "replication_group": rid, "cache_cluster_id": node})
    out.sort(key=lambda x: x["name"])
    return out


def collect_mq() -> list[dict]:
    brokers = aws_json(
        [
            "mq",
            "list-brokers",
            "--query",
            "BrokerSummaries[].{Name:BrokerName,Id:BrokerId,State:BrokerState}",
        ]
    ) or []
    by_name = {b["Name"]: b for b in brokers}
    out = []
    for name in TARGET_MQ:
        b = by_name.get(name)
        if not b:
            raise RuntimeError(f"Missing MQ broker: {name}")
        out.append({"name": name, "broker_id": b["Id"]})
    return out


def build_inventory() -> tuple[dict, list[dict]]:
    ecs_services, excluded = collect_ecs()
    load_balancers = collect_lbs()
    rds_clusters = collect_rds()
    redis_groups = collect_redis()
    mq_brokers = collect_mq()

    # Prefer a stable canary from infra if present.
    canary = []
    for svc in ecs_services:
        if svc["cluster"] == "infra":
            canary.append(svc["name"])
            break
    if not canary and ecs_services:
        canary = [ecs_services[0]["name"]]

    doc = {
        "inventory": {
            **INVENTORY_HEADER,
            "ecs_services": ecs_services,
            "ecs_canary_services": canary,
            "load_balancers": load_balancers,
            "rds_clusters": rds_clusters,
            "redis_groups": redis_groups,
            "mq_brokers": mq_brokers,
            "dashboards": DASHBOARDS,
        }
    }
    return doc, excluded


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the monitoring inventory YAML")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--excluded-output", type=Path, default=DEFAULT_EXCLUDED)
    args = parser.parse_args()

    doc, excluded = build_inventory()
    inv = doc["inventory"]
    header = (
        "# Example monitoring inventory\n"
        "# Generated by generate_inventory.py. Do not hand-edit resource lists.\n"
        "# Regenerate: python scripts/generate_inventory.py\n"
        f"# ECS services: {len(inv['ecs_services'])}, "
        f"LBs: {len(inv['load_balancers'])}, "
        f"RDS: {len(inv['rds_clusters'])}, "
        f"Redis: {len(inv['redis_groups'])}, "
        f"MQ: {len(inv['mq_brokers'])}, "
        f"excluded idle ECS: {len(excluded)}\n\n"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        header + yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    args.excluded_output.parent.mkdir(parents=True, exist_ok=True)
    args.excluded_output.write_text(json.dumps(excluded, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")
    print(
        f"ECS={len(inv['ecs_services'])} LB={len(inv['load_balancers'])} "
        f"RDS={len(inv['rds_clusters'])} Redis={len(inv['redis_groups'])} "
        f"MQ={len(inv['mq_brokers'])} excluded={len(excluded)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
