# Chapter 24m §10 — Production Workflow Templates

Five JSON workflows that implement the categories described in chapter 24m, section 10. They use the live infrastructure validated in this repo:

- pfSense REST API V2 (`X-API-Key` header, alias `fail2ban_banned` id=2) — see `reference_pfsense_api.md` memory
- CNPG via SSH tunnel (`127.0.0.1:25432` from prod) — see `reference_cnpg_tunnel.md` memory
- Wazuh API (`https://10.0.10.26:55000`, creds in OpenBao at `secret/ops-host/wazuh-api`)
- OpenBao for all credential injection (no hardcoded secrets in JSON)

## Files

| File | Trigger | What it does |
|------|---------|--------------|
| `01-fraud-alert-enrichment.json` | Webhook `/fraud-alert` | KYC + 30d ledger + device + sanctions → Slack #fraud-ops + open case (cap 33b) |
| `02-kyc-reverification.json` | Cron daily 02:30 UTC | Find KYC docs near regulatory expiry → email Postmark with signed re-verify link → audit log |
| `03-withdrawal-hold.json` | Webhook `/withdrawal-threshold` | Pause → check PoF → request artifact → wait 24h → release or escalate |
| `04-incident-response-wazuh-pfsense.json` | Webhook `/incident-trigger` | AbuseIPDB enrich → pfSense V2 alias PATCH (read-modify-write) → apply → page on-call if severity≥high |
| `05-daily-compliance-report.json` | Cron daily 03:30 UTC | Ledger + KYC + cases → CSV → GPG sign → SFTP upload to regulator → audit |

## Import procedure

In the n8n UI:

1. **Settings → Workflows → Import from File** (top-right kebab menu)
2. Select one JSON at a time
3. n8n will create the workflow as `inactive` (per cap 24m §9 quiesce playbook)
4. Open the workflow, fix any node showing `PLACEHOLDER` credentials:
   - Open the node → **Credentials → Create New** → choose type (Postgres / HTTP Header Auth / Slack OAuth2 / SFTP / etc.)
   - Fill from OpenBao reference path (e.g. for pfSense: `secret/ops-host/pfsense.api_key`)
5. Run a single execution manually with **Execute Workflow** to validate
6. Toggle **Active** when ready

## Why every workflow ships INACTIVE

Per cap 24m §9 (rotation playbook) and the live operational reality on this stack: any workflow with active webhooks or schedules will fire as soon as it is activated. Activating five workflows that talk to Slack, PagerDuty, OpenAI, regulators (SFTP), and the prod ledger — without first wiring the right credentials — would page on-call, post wrong messages, and submit malformed regulator filings.

The safe path is the same as the safe restart playbook: activate one at a time after credentials are verified.

## OpenBao credential paths to provision

Each workflow references a credential by *name*, not by content. Provision these in OpenBao first, then create the matching n8n credential entries pointing at the same value (or use the OpenBao external-secret connector if installed):

| n8n credential name | OpenBao path | Field |
|---|---|---|
| `casino-cnpg-tunnel` | `secret/ops-host/cnpg-tunnel` (create) | DSN: `host=127.0.0.1 port=25432 sslmode=verify-ca dbname=casino user=...` |
| `fraud-ops-slack` | `secret/ops-host/slack/fraud-ops` (create) | OAuth2 token or Webhook URL |
| `pfsense-v2` | `secret/ops-host/pfsense` | `api_key` (already exists) |
| `abuseipdb` | `secret/ops-host/abuseipdb` (create) | `api_key` |
| `pagerduty-secops` | `secret/ops-host/pagerduty/secops` (create) | `routing_key` |
| `postmark-compliance` | `secret/ops-host/postmark/compliance` (create) | `server_token` |
| `regulator-sftp-ukgc` | `secret/ops-host/regulator/ukgc-sftp` (create) | `host, user, ssh_key` |
| `gpg-compliance-key` | `secret/ops-host/gpg/compliance` (create) | `passphrase` (key material in transit engine) |

## Testing

Each workflow has at least one **Webhook** or **Schedule** trigger that can be exercised manually:

- For webhooks, use the **Listen for test event** mode in n8n's editor and POST a sample payload from `curl`.
- For schedules, use **Execute Workflow** which runs the trigger node once.

Companion error workflows are *not* shipped here (cap 24m §10 mentions them but they're operator-specific). Recommended pattern: a single `[error] cap24m` workflow that accepts the n8n error payload and pages on-call when severity matches.

## What this gives you

The 207 historically-active workflows in your DB are SecOps **community templates** — useful as reference but built for someone else's stack (CrowdStrike, Snyk, Jamf, etc.). The 5 workflows here are **purpose-built for this iGaming stack**, using the same paths and credentials that everything else in this repo already uses.

After import + credential wiring, these are the workflows the book chapter is actually describing.
