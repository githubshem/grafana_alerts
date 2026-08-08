# Security Policy

`grafana_alerts` is a provisioning toolkit: it compiles CloudWatch-backed alert
rules and applies them to a Grafana instance. It holds no user data and exposes
no network service, but it does handle **API tokens, an incoming webhook URL and
read-only AWS credentials**, and it can **write to and delete from a live Grafana
instance**. Those are the areas this policy cares about.

## Supported versions

This project is distributed from source, not as tagged releases. Only the latest
commit on `main` is supported.

| Version           | Supported          |
| ----------------- | ------------------ |
| `main` (latest)   | :white_check_mark: |
| Older commits     | :x:                |
| `legacy/`         | :x:                |

`legacy/` contains retired scripts kept for reference only. They receive no
fixes of any kind — do not run them.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub:

1. Go to the [Security tab](https://github.com/githubshem/grafana_alerts/security)
   of this repository.
2. Choose **Report a vulnerability** to open a private security advisory.

If private reporting is unavailable to you, email the maintainer
(`shemsumbelingforwork@gmail.com`) with `SECURITY` in the subject line.

Please include:

- what the issue is and where in the tree it lives (file and, if you have it, line);
- the steps or a minimal command sequence that reproduces it;
- what an attacker gets out of it — leaked credential, unintended Grafana write,
  arbitrary code execution, and so on;
- the Python version and OS you saw it on.

**Never include real tokens, webhook URLs or AWS credentials in a report.**
Redact them. If a report shows a secret was exposed, rotate it first, then
report.

### What to expect

| Stage                | Target                                             |
| -------------------- | -------------------------------------------------- |
| Acknowledgement      | Within 5 business days                             |
| Initial assessment   | Within 10 business days                            |
| Fix or mitigation    | Depends on severity; you will be kept updated      |
| Public disclosure    | After a fix lands, coordinated with the reporter   |

If a report is accepted, you will be credited in the advisory unless you ask not
to be. If it is declined, you will get a written explanation of why — usually
"out of scope" or "working as intended", with a pointer to the relevant test
that pins the behaviour.

Please give the maintainer a reasonable window to ship a fix before disclosing
publicly. 90 days is the default; shorter if the issue is already being
exploited.

## Scope

**In scope**

- Leakage of secrets into logs, reports, `output/`, `reports/`, error messages
  or committed files.
- Flaws in `scripts/secrets_store.py` — the environment-variable-then-AWS
  Secrets Manager resolution path.
- Flaws in `scripts/grafana_client.py`, including TLS verification, token
  handling and request construction.
- Injection through inventory or rule-definition YAML (`inventory/services.yaml`,
  `templates/rule_definitions.yaml`) that leads to code execution or to Grafana
  API calls the operator did not intend.
- Anything that causes a write-capable script (`apply_rule_groups.py`,
  `provision_notification_stack.py`, `create_contact_point.py`, `canary_test.py`,
  `rollback.py`) to affect entities outside the `alerts-` prefix and the
  configured folder — or to write while `--dry-run` was passed.
- UID-seed manipulation that silently reassigns rule UIDs, causing an apply to
  destroy existing alerts, alert state and silences rather than update them.
- Dependency vulnerabilities in `requirements.txt` that are reachable from this
  code.

**Out of scope**

- Vulnerabilities in Grafana, AWS CloudWatch, Microsoft Teams or any other
  upstream service — report those to their vendors.
- Anything requiring an attacker who already has your Grafana write token, your
  AWS credentials or write access to this repository. Those are trust
  boundaries this toolkit sits inside, not ones it defends.
- The demo inventory shipped in `inventory/services.yaml`. Names such as
  `app-alpha` and `app-beta` are placeholders and correspond to no real
  infrastructure.
- Example values in `README.md`, `.env.example` and
  `config/teams_webhook.example.yaml` (`grafana.example.com`, `PASTE_*_HERE`).
  These are placeholders by design; the scripts ignore them.
- `resource_type: ecs` compiling to zero rules. This is a documented,
  deliberately preserved limitation pinned by `tests/test_exclusions.py`, not a
  vulnerability.
- Findings that require running scripts under `legacy/`.
- Reports produced solely by an automated scanner with no demonstrated impact.

## Operating this toolkit safely

These are the assumptions the code is built on. Breaking them is the most likely
way to get hurt, and it will not be caught by a fix on our side.

**Secrets**

- Three secrets exist: `GRAFANA_READ_TOKEN`, `GRAFANA_WRITE_TOKEN` (or
  `GRAFANA_API_KEY`), and `GRAFANA_TEAMS_WEBHOOK_URL`. Resolution order is
  environment variable first, AWS Secrets Manager
  (`grafana-alerts/provisioning`) second.
- Never commit `.env`, `config/teams_webhook.local.yaml`, or anything under
  `backups/`. All three are gitignored — keep them that way.
- Treat the Teams webhook URL as a credential. Anyone holding it can post to
  that channel.
- Use a read-scoped token for read-only commands. Reserve the write token for
  the five scripts that need it.
- `backup.py` exports live contact points, which contain the webhook. Its output
  is gitignored; store it accordingly.
- Rotate any token that has appeared in a shell history, a CI log, a screenshot
  or a bug report.

**AWS**

- `generate_inventory.py` is the only script needing AWS credentials, and it
  needs read-only permissions. Do not grant it more.

**Grafana writes**

- Compilation, tests and validation are fully offline and need no credentials.
  Only the scripts listed under "Writes to Grafana" in the README touch a live
  instance.
- Always run `compare_baseline.py` and then the `--dry-run` form before a real
  apply. The diff is the review step; read it.
- `rollback.py` without `--dry-run` deletes every `alerts-` entity. Treat it as
  destructive.
- Rule UIDs are load-bearing. Changing the seed format, the rule prefix or a
  resource name reassigns UIDs and turns an update into a delete-and-recreate.
  `tests/test_uid_stability.py` and `scripts/compare_baseline.py` guard this —
  do not bypass either.

**Before contributing**

Run the suite. `tests/test_compiled_output.py` is a golden-master check; if it
fails, alert behaviour changed.

```bash
py -3.13 -m pytest
```
