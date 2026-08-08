# Rollback Plan

## Scope

Rollback deletes only resources with the `alerts-` prefix (alert rules, and optionally the contact point / template created by this toolkit).
Existing dashboards and out-of-scope resources are never deleted.

## Commands

```powershell
cd grafana-alerts
$env:GRAFANA_URL = $env:GRAFANA_URL
$env:GRAFANA_API_KEY = $env:GRAFANA_WRITE_TOKEN
py -3.13 scripts/rollback.py --prefix alerts- --dry-run --json
py -3.13 scripts/rollback.py --prefix alerts- --json
```

## Restore notification policy

Restore `notification_policies.json` from the pre-change backup under `backups/<stamp>/` via:

```powershell
py -3.13 -c "from pathlib import Path; import json, os, sys; sys.path.insert(0,'scripts'); from grafana_client import GrafanaClient; p=Path('backups/<stamp>/notification_policies.json'); client=GrafanaClient(); client.put_notification_policies(json.loads(p.read_text()))"
```

## Evidence

Keep the pre-change backup directory and `output/compiled_rules.json` for audit.
