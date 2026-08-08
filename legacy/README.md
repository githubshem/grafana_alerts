# Legacy scripts (retired, not supported)

These scripts were moved out of `scripts/` during the refactor. They are kept
**unchanged** for reference and rollback only. None of them is part of the
supported workflow, and every one was already broken or targeted an
environment that this repository no longer provisions.

Do not run these. They are not imported by any active script, are excluded from
the test suite, and will not be maintained.

| Script | Why it was retired |
| --- | --- |
| `apply_all_rules.py` | References `extract_rule.py`, which does not exist in this repository. Hard-codes the staging receiver `stg-teams-alerts` and reads `test-results/compiled_rules.json` instead of `output/compiled_rules.json`. |
| `bulk_apply_compiled.py` | Detects already-existing rules by filtering on the `stg-` title prefix. Run against the current stack it would fail to match any `alerts-` rule and would re-create every rule as a duplicate. |
| `export_live_rules.py` | Imports `grafana_transport`, a module that does not exist in this repository, so it cannot be imported at all. Only supports `--env staging` and `--env uat`. |
| `diff_live_vs_repo.py` | Defaults to `templates/rule_definitions_staging.yaml` and references `export_live_staging_rules.py`; neither exists here. Entirely staging-oriented. |
| `provision_grafana.py` | Depends on `inventory/services.yaml` and staging templates that do not exist. Also calls `apply_alert_rules()` without the required `rule_prefix` argument, so it raises `TypeError`. |
| `provision_grafana_env.py` | Its CLI only accepts the profiles in `ENV_PROFILES`, but its internal `ENV_CONFIG` only defines `staging` and `uat`, so any invocation raises `KeyError`. |

## Known dangling reference

`export_live_rules.py` imports `is_mutation_denylisted_rule` from the old
`rule_builder` module. That function was part of the live-rule annotation
patching family removed during the refactor, and `rule_builder.py` itself no
longer exists. This does not make the script any more broken than it already
was: it could never be imported because of the missing `grafana_transport`.

## Replacements

| Retired script | Use instead |
| --- | --- |
| `apply_all_rules.py`, `bulk_apply_compiled.py` | `scripts/apply_rule_groups.py` (atomic rule-group PUT, with read-back verification) |
| `provision_grafana.py`, `provision_grafana_env.py` | `scripts/provision_notification_stack.py` |
| `diff_live_vs_repo.py` | `scripts/compare_baseline.py` (offline diff against the committed baseline) |
| `export_live_rules.py` | `scripts/verify_applied.py` |
