# SOAR System — AcmeToCasino iGaming Platform

Security Orchestration, Automation and Response (SOAR) system for the AcmeToCasino iGaming platform. This system detects threats in real time, classifies them by severity, and automatically triggers multi-layer defensive responses across both cloud and on-premise infrastructure — without requiring manual intervention for common attack patterns.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Component List](#component-list)
3. [Prerequisites and Dependencies](#prerequisites-and-dependencies)
4. [Installation Guide](#installation-guide)
5. [Configuration Guide](#configuration-guide)
6. [API Reference — Webhook Endpoints](#api-reference--webhook-endpoints)
7. [Troubleshooting Guide](#troubleshooting-guide)
8. [Security Considerations](#security-considerations)

---

## Architecture Overview

The SOAR system operates in three logical tiers:

```
Log Sources  →  n8n SOAR Engine  →  Defense Layer (Cloud + On-Prem)
                      ↓
               Notification Layer  +  SOC Dashboard
```

**Ingestion tier** collects structured events from Nginx access logs, application logs, AWS WAF logs, authentication service logs, and Kafka event streams. Log shippers (Filebeat / Fluent Bit) forward these to the n8n webhook receiver.

**Processing tier** (n8n) applies threat-analysis workflows, classifies severity (P1–P4), and dispatches automated responses. For high-confidence threats the response is fully automated; for ambiguous signals a human-review ticket is created.

**Defense tier** applies blocks simultaneously at the cloud edge (AWS WAF v2 / AWS Shield) and on-premise (ModSecurity v3, nftables/iptables, fail2ban). This dual-layer approach ensures that a misconfiguration in one layer does not leave the platform unprotected.

For a full diagrammatic view see [`docs/architecture.md`](docs/architecture.md).

---

## Component List

| Component | Role | Location |
|---|---|---|
| **n8n** | SOAR workflow engine — receives webhooks, evaluates rules, dispatches actions | Docker / on-prem VM |
| **AWS WAF v2** | Cloud-edge request filtering; SOAR-managed IP blocklist (`soar-blocklist-cloudfront`, `soar-blocklist-regional`) | AWS (CloudFront + ALB) |
| **AWS Shield** | Volumetric DDoS absorption; escalated automatically by SOAR on L3/L4 floods | AWS |
| **CloudWatch** | Receives custom `AcmeToCasino/WAF` metrics for every block/unblock action | AWS |
| **On-prem Firewall** (`firewall_manager.py`) | Manages nftables / iptables `SOAR_BLOCK` chain and `soar_blocklist` ipset | Application servers, edge proxies |
| **On-prem WAF** (ModSecurity v3) | HTTP-layer request filtering on Nginx; SOAR pushes dynamic IP deny-lists and rule updates | Nginx on-prem |
| **fail2ban** | Supplementary ban daemon; SOAR can invoke ban actions for auth-layer events | Auth service hosts |
| **SOC Dashboard** | Real-time incident view; connects to n8n API and CloudWatch metrics | Internal web app |
| **Grafana** | Metric visualisation for WAF counters, block rates, and alert volumes | Monitoring stack |
| **Slack / PagerDuty / Email / JIRA** | Notification and ticketing for human-reviewed alerts | SaaS |

---

## Prerequisites and Dependencies

### System Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| n8n host CPU | 2 vCPU | 4 vCPU |
| n8n host RAM | 4 GB | 8 GB |
| Disk (n8n + logs) | 20 GB | 100 GB |
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |

### Runtime Dependencies

**Python (WAF auto-block and firewall manager scripts)**

```
python >= 3.11
boto3 >= 1.34
botocore >= 1.34
```

**On-premise host**

- `nft` (nftables) — preferred; falls back to `iptables` + `ipset`
- `ipset` (optional but recommended for large blocklists)
- `fail2ban >= 0.11`
- ModSecurity v3 with Nginx connector (`libmodsecurity3`, `libnginx-mod-security2`)

**n8n**

- Docker >= 24 and Docker Compose v2, **or** Node.js >= 20 + npm
- PostgreSQL >= 15 (production) or SQLite (development only)

**AWS**

- AWS CLI v2 configured with a role that has the following IAM permissions:
  - `wafv2:GetIPSet`, `wafv2:UpdateIPSet`, `wafv2:ListIPSets`
  - `wafv2:GetWebACL`, `wafv2:UpdateWebACL`
  - `cloudwatch:PutMetricData`
- Existing WAF IP sets (`soar-blocklist-cloudfront`, `soar-blocklist-regional`) — created by the Terraform module in `aws-waf/terraform/`

**Network**

- n8n webhook port (default `5678`) reachable from log shippers
- Outbound HTTPS from n8n host to AWS API endpoints, Slack, PagerDuty

---

## Installation Guide

### Step 1 — Clone the repository

```bash
git clone https://github.com/acmetocasino/igaming-platform.git
cd igaming-platform/scripts/security-soar
```

### Step 2 — Provision AWS WAF resources with Terraform

```bash
cd aws-waf/terraform
terraform init
terraform plan -var-file=vars/production.tfvars
terraform apply -var-file=vars/production.tfvars
```

This creates the two SOAR IP sets (`soar-blocklist-cloudfront` and `soar-blocklist-regional`) and attaches rate-based rules to the Web ACLs.

### Step 3 — Install Python dependencies

```bash
cd ../../
python3 -m venv .venv
source .venv/bin/activate
pip install boto3 botocore
```

Verify the AWS WAF script works:

```bash
python aws-waf/waf_auto_block.py --dry-run block --ip 203.0.113.1/32 --scope REGIONAL
```

### Step 4 — Set up the on-premise firewall manager

Copy the script to a system path and make it executable:

```bash
sudo install -m 755 onprem-firewall/firewall_manager.py /usr/local/sbin/firewall_manager.py
```

Verify detection and a dry-run block:

```bash
sudo python3 /usr/local/sbin/firewall_manager.py --dry-run block-ip --ip 203.0.113.1
```

Create the audit log directory:

```bash
sudo mkdir -p /var/log/acmetocasino
sudo chown root:adm /var/log/acmetocasino
sudo chmod 750 /var/log/acmetocasino
```

### Step 5 — Install ModSecurity v3 on Nginx hosts

```bash
sudo apt-get install -y libmodsecurity3 libnginx-mod-security2
sudo cp onprem-waf/modsecurity/modsecurity.conf /etc/nginx/modsecurity/
sudo cp onprem-waf/modsecurity/acmetocasino-rules.conf /etc/nginx/modsecurity/
sudo nginx -t && sudo systemctl reload nginx
```

### Step 6 — Deploy n8n

**Option A — Docker Compose (recommended for production)**

```bash
cd n8n-workflows
cp .env.example .env
# Edit .env with your secrets (see Configuration Guide below)
docker compose up -d
```

**Option B — Existing Kubernetes cluster**

```bash
helm repo add n8n https://helm.n8n.io
helm install n8n n8n/n8n -f n8n-workflows/helm-values.yaml
```

### Step 7 — Import n8n workflows

1. Open the n8n UI at `http://<n8n-host>:5678`
2. Navigate to **Workflows → Import**
3. Import each JSON file from `n8n-workflows/`:
   - `brute-force-response.json`
   - `ddos-mitigation.json`
   - `sql-injection-response.json`
   - `account-takeover-response.json`
   - `fraud-detection-response.json`
4. Activate each workflow

### Step 8 — Configure log shippers

Add the n8n webhook URL to your Filebeat / Fluent Bit output configuration. Example Filebeat output:

```yaml
output.http:
  hosts: ["http://n8n-host:5678/webhook/security-events"]
  codec.json:
    pretty: false
```

### Step 9 — Verify end-to-end

Run the included integration test to simulate a brute-force alert:

```bash
curl -s -X POST http://n8n-host:5678/webhook/security-events \
  -H "Content-Type: application/json" \
  -H "X-SOAR-Token: <your-token>" \
  -d '{
    "event_type": "brute_force",
    "source_ip": "203.0.113.99",
    "severity": "HIGH",
    "failed_attempts": 25,
    "target": "login",
    "timestamp": "2026-03-16T00:00:00Z"
  }'
```

Confirm that:
- n8n workflow executes without errors
- `203.0.113.99` appears in the AWS WAF blocklist (REGIONAL scope)
- `203.0.113.99` appears in the nftables `blocklist` set
- A Slack notification arrives in `#security-alerts`

---

## Configuration Guide

### Environment Variables (n8n)

| Variable | Description | Example |
|---|---|---|
| `N8N_WEBHOOK_SECRET` | Shared secret validated on every inbound webhook | `changeme-replace-in-prod` |
| `AWS_REGION` | Default AWS region for REGIONAL scope actions | `eu-west-1` |
| `AWS_ACCESS_KEY_ID` | AWS credentials (prefer instance role / IRSA) | — |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials | — |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook for alert notifications | `https://hooks.slack.com/...` |
| `PAGERDUTY_ROUTING_KEY` | PagerDuty Events v2 integration key | — |
| `JIRA_BASE_URL` | JIRA/Linear API base URL | `https://acmetocasino.atlassian.net` |
| `JIRA_API_TOKEN` | JIRA API token | — |
| `N8N_ENCRYPTION_KEY` | n8n credential encryption key (min 32 chars) | — |
| `DB_TYPE` | Database backend | `postgresdb` |
| `DB_POSTGRESDB_HOST` | PostgreSQL host | `postgres` |
| `DB_POSTGRESDB_PASSWORD` | PostgreSQL password | — |
| `SOAR_DRY_RUN` | Set to `true` to disable actual blocking calls | `false` |

### Severity Classification Thresholds

Thresholds are configured inside each n8n workflow's **Severity Classifier** node. Default values:

| Severity | Condition | Automated Response |
|---|---|---|
| **P1 — Critical** | > 100 failed auth/min OR > 10k req/min from single IP | Immediate block on all layers + PagerDuty page |
| **P2 — High** | 50–100 failed auth/min OR 5–10k req/min | Block on WAF + on-prem + Slack alert |
| **P3 — Medium** | 20–50 failed auth/min OR known-bad ASN | Rate-limit on WAF + Slack alert |
| **P4 — Low** | Anomalous but not confirmed malicious | Log + JIRA ticket for analyst review |

### AWS WAF Rate-Limit Default

The default rate limit (`soar-rate-limit` rule) is **2,000 requests per 5-minute window** per source IP. Adjust during runtime:

```bash
python aws-waf/waf_auto_block.py update-rate-rule \
  --acl-id <id> --acl-name <name> \
  --scope REGIONAL \
  --limit 1000
```

### On-Prem Firewall Audit Log

Structured JSON audit events are written to `/var/log/acmetocasino/firewall_audit.jsonl`. Each record contains:

```json
{
  "ts": "2026-03-16T12:00:00+00:00",
  "action": "block_ip",
  "ip": "203.0.113.1",
  "comment": "brute-force INC-1042",
  "backend": "nftables",
  "pid": 12345
}
```

Rotate logs with logrotate:

```
/var/log/acmetocasino/*.jsonl {
    daily
    rotate 90
    compress
    delaycompress
    missingok
    notifempty
}
```

---

## API Reference — Webhook Endpoints

All webhook endpoints are served by n8n. The base URL is `http://<n8n-host>:5678`.

Authentication: include `X-SOAR-Token: <token>` header on every request. The token must match `N8N_WEBHOOK_SECRET`.

---

### `POST /webhook/security-events`

General-purpose security event ingestion endpoint. Dispatches to the appropriate workflow based on `event_type`.

**Request body**

```json
{
  "event_type": "brute_force",
  "source_ip": "203.0.113.1",
  "severity": "HIGH",
  "failed_attempts": 30,
  "target": "login",
  "timestamp": "2026-03-16T12:00:00Z",
  "metadata": {}
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `event_type` | string | Yes | One of: `brute_force`, `ddos`, `sql_injection`, `xss`, `account_takeover`, `fraud`, `geo_violation`, `credential_stuffing` |
| `source_ip` | string | Yes | IPv4 or IPv6 in CIDR notation (e.g. `1.2.3.4/32`) or plain IP |
| `severity` | string | Yes | `CRITICAL`, `HIGH`, `MEDIUM`, `LOW` |
| `timestamp` | string | Yes | ISO 8601 UTC |
| `failed_attempts` | integer | No | Relevant for brute-force events |
| `request_rate` | float | No | Requests per second — relevant for DDoS events |
| `target` | string | No | Endpoint or service targeted |
| `metadata` | object | No | Arbitrary key-value pairs forwarded to JIRA/Slack |

**Response — 200 OK**

```json
{
  "status": "accepted",
  "workflow_id": "brute-force-response",
  "execution_id": "abc123",
  "actions_triggered": ["waf_block", "firewall_block", "slack_notify"]
}
```

**Response — 401 Unauthorized**

```json
{ "error": "invalid_token" }
```

---

### `POST /webhook/waf-block`

Direct WAF block action. Bypasses the classifier and blocks immediately on the specified scope.

**Request body**

```json
{
  "ip_cidr": "198.51.100.0/24",
  "scope": "REGIONAL",
  "reason": "manual-block",
  "incident_id": "INC-1042"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `ip_cidr` | string | Yes | IPv4 or IPv6 CIDR |
| `scope` | string | Yes | `CLOUDFRONT` or `REGIONAL` |
| `reason` | string | No | Free-text reason stored in audit log |
| `incident_id` | string | No | Associated incident reference |

**Response — 200 OK**

```json
{ "status": "blocked", "ip_cidr": "198.51.100.0/24", "scope": "REGIONAL" }
```

---

### `POST /webhook/waf-unblock`

Remove an IP or CIDR from the WAF blocklist.

**Request body**

```json
{
  "ip_cidr": "198.51.100.0/24",
  "scope": "REGIONAL",
  "reason": "false-positive"
}
```

**Response — 200 OK**

```json
{ "status": "unblocked", "ip_cidr": "198.51.100.0/24", "scope": "REGIONAL" }
```

---

### `POST /webhook/firewall-block`

Block an IP on the on-premise firewall only.

**Request body**

```json
{
  "ip": "203.0.113.5",
  "comment": "credential-stuffing INC-1055",
  "dry_run": false
}
```

**Response — 200 OK**

```json
{ "status": "blocked", "ip": "203.0.113.5", "backend": "nftables" }
```

---

### `POST /webhook/rate-limit`

Apply per-IP rate limiting on the on-premise firewall.

**Request body**

```json
{
  "ip": "203.0.113.5",
  "rate": 10,
  "burst": 20
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `ip` | string | Yes | IPv4 or IPv6 address |
| `rate` | integer | Yes | Maximum packets per second |
| `burst` | integer | No | Burst allowance (default: 50) |

---

### `GET /webhook/blocklist-status`

Returns the current contents of the on-premise SOAR blocklist.

**Response — 200 OK**

```json
{
  "backend": "nftables",
  "table_dump": "...",
  "ts": "2026-03-16T12:00:00Z"
}
```

---

### `POST /webhook/incident`

Create or update an incident record in the SOC Dashboard.

**Request body**

```json
{
  "action": "create",
  "title": "Brute force from AS12345",
  "severity": "HIGH",
  "source_ip": "203.0.113.1",
  "event_type": "brute_force",
  "timeline": []
}
```

---

## Troubleshooting Guide

### n8n workflow does not trigger

1. Check that the workflow is **Active** in the n8n UI (green toggle).
2. Verify the webhook URL matches the path in your log shipper configuration.
3. Inspect **Executions** in the n8n UI for error details.
4. Confirm the `X-SOAR-Token` header value matches `N8N_WEBHOOK_SECRET`.
5. Check n8n container logs: `docker logs n8n --tail 200`.

---

### AWS WAF block fails with `WAFOptimisticLockException`

The script retries with exponential back-off (up to 5 attempts). If it still fails:

```bash
# Check current lock token
aws wafv2 get-ip-set \
  --name soar-blocklist-regional \
  --scope REGIONAL \
  --id <ip-set-id> \
  --region eu-west-1
```

If multiple processes are writing concurrently, serialise calls through the n8n queue or increase `MAX_LOCK_RETRIES` in `waf_auto_block.py`.

---

### IP set not found (`RuntimeError: IP set not found`)

The Terraform module must be applied before the Python script runs.

```bash
cd aws-waf/terraform
terraform apply
```

Then verify the set exists:

```bash
aws wafv2 list-ip-sets --scope REGIONAL --region eu-west-1
```

---

### On-prem firewall block has no effect

1. Check the backend in use: `sudo python3 /usr/local/sbin/firewall_manager.py status`
2. Verify the SOAR chain/table is installed:
   - nftables: `sudo nft list table inet soar`
   - iptables: `sudo iptables -n -L SOAR_BLOCK`
3. Ensure the INPUT chain jumps to SOAR_BLOCK:
   - iptables: `sudo iptables -n -L INPUT | grep SOAR_BLOCK`
4. Check the audit log for errors: `tail -20 /var/log/acmetocasino/firewall_audit.jsonl`

---

### ModSecurity blocks legitimate traffic

1. Check ModSecurity audit log: `/var/log/modsec_audit.log`
2. Identify the matching rule ID.
3. Whitelist the false positive in `acmetocasino-rules.conf`:

```apache
SecRuleRemoveById 942100
```

4. Or whitelist by IP:

```apache
SecRule REMOTE_ADDR "@ipMatch 192.168.1.0/24" \
  "id:9000001,phase:1,pass,nolog,ctl:ruleEngine=Off"
```

5. Reload Nginx: `sudo systemctl reload nginx`

---

### Slack notifications not arriving

1. Verify `SLACK_WEBHOOK_URL` is set and the Incoming Webhook is enabled in Slack.
2. Test the webhook directly:

```bash
curl -X POST "$SLACK_WEBHOOK_URL" \
  -H 'Content-type: application/json' \
  -d '{"text":"SOAR connectivity test"}'
```

3. Check for HTTP 429 (rate limit) in n8n execution logs.

---

### PagerDuty alert not firing for P1 events

1. Confirm `PAGERDUTY_ROUTING_KEY` is the **Events v2** integration key (32-character hex string).
2. Verify the n8n HTTP node is targeting `https://events.pagerduty.com/v2/enqueue`.
3. Check the PagerDuty service is not in **Maintenance Mode**.

---

### High false-positive rate for brute-force detection

Lower the sensitivity by raising the threshold in the **Severity Classifier** n8n node. Alternatively, add the affected IP ranges to the whitelist webhook:

```bash
curl -X POST http://n8n-host:5678/webhook/waf-unblock \
  -H "X-SOAR-Token: <token>" \
  -H "Content-Type: application/json" \
  -d '{"ip_cidr": "10.0.0.0/8", "scope": "REGIONAL", "reason": "internal-scanner"}'
```

---

## Security Considerations

### Webhook Authentication

Every inbound webhook request must carry `X-SOAR-Token`. Use a cryptographically random value of at least 32 bytes:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Rotate this token quarterly and after any staff departure.

### Network Segmentation

- Deploy n8n on a dedicated internal network segment. The webhook port (`5678`) must **not** be publicly reachable.
- Use a reverse proxy (Nginx/Caddy) with TLS termination in front of n8n.
- Restrict outbound access from the n8n host to only the required AWS API endpoints, Slack, PagerDuty, and JIRA.

### Principle of Least Privilege — AWS IAM

Create a dedicated IAM role for the SOAR system with only the permissions listed in the [Prerequisites](#prerequisites-and-dependencies) section. Do not use `AdministratorAccess` or any wildcard `wafv2:*` policy.

Prefer instance roles (EC2) or IRSA (EKS) over long-lived access keys. If access keys are unavoidable, store them in AWS Secrets Manager and rotate them automatically.

### Audit Logging

- All on-premise block/unblock actions are written to `/var/log/acmetocasino/firewall_audit.jsonl` with timestamp, action, source, and PID.
- AWS WAF actions publish custom CloudWatch metrics (`AcmeToCasino/WAF` / `IPBlockActions`).
- n8n execution history is retained for 30 days by default (configurable via `EXECUTIONS_DATA_MAX_AGE`).

Ship audit logs to a SIEM (Wazuh, Elastic Security, or Splunk) for long-term retention and correlation.

### Secret Management

Never commit credentials to the repository. Use environment variables injected at runtime, or a secrets manager:

- AWS: AWS Secrets Manager or Parameter Store
- On-prem: HashiCorp Vault
- Docker: Docker Secrets or a `.env` file with `chmod 600` and excluded from version control

### Supply Chain Security

Pin all Python dependencies to exact versions and validate checksums:

```
boto3==1.34.162
botocore==1.34.162
```

Use `pip-audit` to scan for known vulnerabilities before each deployment.

### Preventing Block Abuse

The direct block endpoints (`/webhook/waf-block`, `/webhook/firewall-block`) should only be accessible from trusted internal networks. Add IP allowlisting at the reverse proxy level:

```nginx
location /webhook/waf-block {
    allow 10.0.0.0/8;
    deny all;
    proxy_pass http://n8n:5678;
}
```

### Compliance

Block actions affecting EU/UK customers are subject to GDPR considerations — IP addresses are personal data. Retain block records for the minimum period required by your gambling regulator (typically 5 years) and ensure they are included in your Data Processing Agreement.

---

## Related Documentation

- [`docs/architecture.md`](docs/architecture.md) — Detailed architecture with Mermaid diagrams
- [`docs/runbook.md`](docs/runbook.md) — Operational runbook and incident response procedures
- [`docs/solutions-comparison.md`](docs/solutions-comparison.md) — Comparative analysis of security tooling for iGaming

## External References

- [n8n Documentation](https://docs.n8n.io/)
- [AWS WAF v2 Developer Guide](https://docs.aws.amazon.com/waf/latest/developerguide/)
- [AWS Shield Advanced](https://docs.aws.amazon.com/waf/latest/developerguide/shield-chapter.html)
- [ModSecurity Reference Manual v3](https://github.com/owasp-modsecurity/ModSecurity/wiki/Reference-Manual-(v3.x))
- [nftables Wiki](https://wiki.nftables.org/)
- [fail2ban Documentation](https://www.fail2ban.org/wiki/index.php/Main_Page)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
