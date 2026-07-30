# SOAR Operational Runbook — AcmeToCasino iGaming Platform

This runbook is the authoritative reference for Security Operations Centre (SOC) analysts and on-call SREs managing the AcmeToCasino SOAR system. It covers alert response procedures, manual override steps, escalation paths, and system maintenance tasks.

**Classification: INTERNAL — RESTRICTED**
Distribute only to authorised personnel. Do not commit secrets or personal contact information to this file.

---

## Table of Contents

1. [Alert Response Procedures](#1-alert-response-procedures)
   - 1.1 [Brute Force / Credential Stuffing](#11-brute-force--credential-stuffing)
   - 1.2 [DDoS / Volumetric Attack](#12-ddos--volumetric-attack)
   - 1.3 [SQL Injection / XSS](#13-sql-injection--xss)
   - 1.4 [Account Takeover](#14-account-takeover)
   - 1.5 [Geo-Restriction Violation](#15-geo-restriction-violation)
   - 1.6 [Fraud / Bonus Abuse](#16-fraud--bonus-abuse)
2. [Manual Override Procedures](#2-manual-override-procedures)
3. [Emergency Escalation](#3-emergency-escalation)
4. [Permanently Block an IP](#4-permanently-block-an-ip)
5. [Whitelist a False Positive](#5-whitelist-a-false-positive)
6. [Tune Detection Thresholds](#6-tune-detection-thresholds)
7. [Add New Detection Rules](#7-add-new-detection-rules)
8. [Disaster Recovery Procedure](#8-disaster-recovery-procedure)

---

## 1. Alert Response Procedures

### 1.1 Brute Force / Credential Stuffing

**Trigger condition**: >= 20 failed login attempts per minute from a single IP, or a distributed spray pattern across >= 10 accounts.

**SOAR automated response**:
- P1/P2: Immediate block on AWS WAF (REGIONAL) + nftables + fail2ban
- P3: WAF rate-limit only
- Slack notification + JIRA ticket created

**Analyst steps**:

1. **Acknowledge the alert** in Slack (`#security-alerts`) by reacting with `:eyes:`.

2. **Review the JIRA ticket** (link is in the Slack message). Check:
   - Is this a known IP range (office VPN, payment gateway, partner API)?
   - Is the targeted account real or a honeypot?

3. **Assess scope**: Open the SOC Dashboard → *Brute Force* tab. Check if the attack is distributed (multiple IPs hitting the same account) — this indicates credential stuffing.

4. **If distributed attack**: Run the geo-correlation query in Grafana:
   ```
   AcmeToCasino/WAF | IPBlockActions | filter Scope=REGIONAL | group by ip
   ```

5. **Block additional IPs** if needed:
   ```bash
   python3 /usr/local/sbin/waf_auto_block.py block --ip <cidr> --scope REGIONAL
   ```

6. **Force password reset** for compromised accounts via the admin panel: *Users → Bulk Actions → Force Password Reset*.

7. **Notify Compliance** if more than 50 accounts were targeted (potential data breach notification obligation under GDPR Article 33 — 72-hour reporting window starts now).

8. **Close the JIRA ticket** with:
   - Root cause (botnet, TOR exit node, VPN, known threat actor)
   - Number of accounts affected
   - Actions taken

**Auto-unblock schedule**: 24 hours (P2), 72 hours (P1). Override: see [Section 5](#5-whitelist-a-false-positive).

---

### 1.2 DDoS / Volumetric Attack

**Trigger condition**: > 2,000 requests/minute from a single IP, or > 50,000 total requests/minute platform-wide (L7), or CloudWatch metric `DDoSDetected` from AWS Shield.

**SOAR automated response**:
- P2: Block source IP on all layers
- P1: Block IP + restrict WAF rate limit to 100 req/5 min + PagerDuty page

**Analyst steps**:

1. **Verify the attack is real**: check CloudWatch dashboard *WAF — Request Rate*. Distinguish between a DDoS and a legitimate traffic spike (marketing campaign, game launch).

2. **Identify attack type**:
   - Single source IP → standard IP block (already automated)
   - ASN range → block the entire /24 or ASN via WAF
   - Distributed (> 100 IPs) → enable AWS Shield Advanced response and contact AWS DRT

3. **Block an ASN range**:
   ```bash
   # Block a /24 on WAF (CLOUDFRONT covers CloudFront, REGIONAL covers ALB)
   python3 /usr/local/sbin/waf_auto_block.py block \
     --ip 203.0.113.0/24 --scope CLOUDFRONT
   python3 /usr/local/sbin/waf_auto_block.py block \
     --ip 203.0.113.0/24 --scope REGIONAL
   ```

4. **Activate AWS Shield DRT (P1 only)**:
   - Log in to AWS Console → Shield → *Overview* → *Contact DRT*
   - Alternatively call the AWS Shield emergency hotline (see [Section 3](#3-emergency-escalation))

5. **Lower the WAF rate limit temporarily**:
   ```bash
   python3 /usr/local/sbin/waf_auto_block.py update-rate-rule \
     --acl-id <id> --acl-name <name> --scope REGIONAL --limit 500
   ```

6. **Monitor recovery**: Attack is considered resolved when request rate returns to baseline for 15 consecutive minutes.

7. **Restore rate limit** after the attack subsides:
   ```bash
   python3 /usr/local/sbin/waf_auto_block.py update-rate-rule \
     --acl-id <id> --acl-name <name> --scope REGIONAL --limit 2000
   ```

8. **Post-incident**: Document attack vectors, peak rate, duration, and origin ASNs in the JIRA ticket.

---

### 1.3 SQL Injection / XSS

**Trigger condition**: ModSecurity detects OWASP CRS rule matches for SQLi (rule group 942xxx) or XSS (rule group 941xxx), or AWS WAF `AWSManagedRulesSQLiRuleSet` blocks a request.

**SOAR automated response**:
- P2/P1: Block source IP on WAF + nftables
- P3: Block on nftables only; WAF rate-limit

**Analyst steps**:

1. **Review the blocked request**: Check ModSecurity audit log:
   ```bash
   grep -A 20 "SOAR_DROP" /var/log/modsec_audit.log | tail -100
   ```

2. **Assess if the application was vulnerable**: Check the application error log for any unexpected query outputs or stack traces. If vulnerable, escalate to the development team immediately.

3. **Check for exfiltration indicators**: Look for unusually large response bodies (> 100 KB) from the targeted endpoint in Nginx access logs:
   ```bash
   awk '$10 > 102400 {print}' /var/log/nginx/access.log | tail -50
   ```

4. **Block persistent attackers** with an extended block period by adding a comment to the JIRA ticket and manually setting the unblock date:
   ```bash
   # Permanent block — see Section 4
   ```

5. **If a genuine vulnerability was exploited**: Follow the Incident Response Plan (IRP) — notify the CISO and initiate a security review of the affected endpoint.

---

### 1.4 Account Takeover

**Trigger condition**: Unusual login from a new device/country after a successful authentication, or anomaly score from the Auth Service >= 0.8.

**SOAR automated response**:
- P1/P2: Suspend the account + block the source IP
- P3: Flag account for review + rate-limit IP

**Analyst steps**:

1. **Verify the account compromise**: Log in to admin panel → *User Management* → search by account ID. Check:
   - Last login IP and location
   - Recent withdrawal or profile change activity
   - KYC documents modified

2. **Suspend the account** if compromise is confirmed:
   - Admin panel: *Users → Account Actions → Suspend*
   - Reason: `security-review`

3. **Reset session tokens**:
   - Admin panel: *Users → Sessions → Invalidate All*

4. **Contact the player** via verified email address to confirm whether the login was legitimate.

5. **If funds were withdrawn**: Escalate to the Payments team and Compliance immediately. This may trigger AML reporting obligations.

6. **Notify Responsible Gambling** if the compromised account shows problematic gambling indicators.

---

### 1.5 Geo-Restriction Violation

**Trigger condition**: A request originates from a jurisdiction where AcmeToCasino is not licensed (geo-blocking violation).

**SOAR automated response**:
- P2: Block IP + log jurisdiction violation

**Analyst steps**:

1. **Verify the GeoIP data** — GeoIP databases have a ~1–2% error rate. Check the IP against multiple sources:
   - [ipinfo.io](https://ipinfo.io)
   - [MaxMind GeoIP2 lookup](https://www.maxmind.com/en/geoip2-precision-demo)

2. **If GeoIP is incorrect**: Whitelist the IP (see [Section 5](#5-whitelist-a-false-positive)) and submit a correction to MaxMind.

3. **If the violation is confirmed**: Log the incident in the Compliance register. Repeat violations from the same ASN may require an escalating block pattern.

4. **Check for VPN/proxy usage**: Some violations occur through VPN exit nodes. Block the specific exit node IP but do not block the entire VPN provider range without compliance sign-off.

---

### 1.6 Fraud / Bonus Abuse

**Trigger condition**: Risk engine score >= 0.75, multiple accounts sharing the same device fingerprint or IP address, or velocity check on bonus claims.

**SOAR automated response**:
- P2: Flag accounts for review, rate-limit IP, create JIRA ticket
- P1: Suspend accounts, block IP

**Analyst steps**:

1. **Review the risk engine report** linked in the JIRA ticket.

2. **Cross-reference accounts** sharing the same IP or device:
   - Admin panel → *Fraud Tools → Account Cluster Analysis*

3. **Verify KYC documents** for all flagged accounts.

4. **If fraud confirmed**: Suspend accounts, void fraudulently obtained bonuses, and refer to the Legal team for further action.

5. **If unconfirmed**: Escalate the account for enhanced monitoring (EM level 2) and require additional KYC verification before allowing withdrawals.

---

## 2. Manual Override Procedures

### Override: Pause the SOAR Engine

If the SOAR engine is producing false positives at scale, pause all automated blocking without stopping the n8n service:

1. Set the environment variable `SOAR_DRY_RUN=true` and restart n8n:
   ```bash
   docker exec n8n n8n update:workflow --all --active=false
   ```
   Or via the n8n UI: **Workflows → Select All → Deactivate**.

2. Notify the team in `#security-alerts` that SOAR is in manual mode.

3. Monitor logs manually until the root cause of false positives is resolved.

4. Re-enable SOAR:
   ```bash
   docker exec n8n n8n update:workflow --all --active=true
   ```

---

### Override: Manually Block an IP Immediately

```bash
# Block on AWS WAF (both scopes)
python3 /usr/local/sbin/waf_auto_block.py block --ip <ip>/32 --scope CLOUDFRONT
python3 /usr/local/sbin/waf_auto_block.py block --ip <ip>/32 --scope REGIONAL

# Block on on-prem firewall
sudo python3 /usr/local/sbin/firewall_manager.py block-ip --ip <ip> --comment "manual-INC-NNNN"
```

---

### Override: Manually Unblock an IP

```bash
# Remove from AWS WAF
python3 /usr/local/sbin/waf_auto_block.py unblock --ip <ip>/32 --scope CLOUDFRONT
python3 /usr/local/sbin/waf_auto_block.py unblock --ip <ip>/32 --scope REGIONAL

# Remove from on-prem firewall
sudo python3 /usr/local/sbin/firewall_manager.py unblock-ip --ip <ip>
```

---

### Override: Flush All SOAR-Managed Blocks

**Use with extreme caution. This removes all automated protective rules.**

```bash
# On-prem only
sudo python3 /usr/local/sbin/firewall_manager.py flush
# Prompts: "This will flush ALL SOAR rules. Type 'yes' to confirm:"
```

AWS WAF IP sets must be cleared manually via the console or CLI. There is no bulk-flush API.

---

### Override: Temporarily Switch to COUNT Mode on WAF

During a maintenance window or false-positive investigation, switch the WAF to count-only mode (logs without blocking):

```bash
python3 /usr/local/sbin/waf_auto_block.py update-rate-rule \
  --acl-id <id> --acl-name <name> --scope REGIONAL --action COUNT
```

Remember to switch back to BLOCK mode when done:

```bash
python3 /usr/local/sbin/waf_auto_block.py update-rate-rule \
  --acl-id <id> --acl-name <name> --scope REGIONAL --action BLOCK
```

---

## 3. Emergency Escalation

### Escalation Tiers

| Tier | Trigger | Who to Contact | SLA |
|---|---|---|---|
| **Tier 1** | P3/P4 alerts, single IP brute force | SOC Analyst on duty | 30 minutes |
| **Tier 2** | P2 alerts, distributed attack | SOC Lead + SRE on-call | 15 minutes |
| **Tier 3** | P1 alert, active DDoS, account compromise at scale | CISO + Head of Engineering | 5 minutes (PagerDuty page) |
| **Tier 4** | Data breach suspected, regulatory impact | CISO + Legal + DPO | Immediate — 24/7 hotline |

### Contact List

Store actual contacts in your secrets manager or HR system — do not commit to this file.

| Role | Contact Method | Notes |
|---|---|---|
| SOC Lead | PagerDuty schedule: `acme-soc-lead` | 24/7 on-call rotation |
| SRE On-call | PagerDuty schedule: `acme-sre-oncall` | 24/7 on-call rotation |
| CISO | Direct phone + PagerDuty escalation policy `acme-exec` | P1 only |
| AWS Shield DRT | AWS Console → Shield → Contact DRT | Requires Shield Advanced subscription |
| AWS Premium Support | `+1-206-266-4064` | Enterprise Support plan required |
| Data Protection Officer (DPO) | Secure email via HR system | GDPR breach notifications |
| Regulatory Authority | Per-jurisdiction contact list in Compliance wiki | See Compliance runbook |

### Incident Declaration

To declare a Major Incident:

1. Send to `#major-incidents` Slack channel: `@here INCIDENT DECLARED: <brief description> — Severity: P<N>`
2. Open a war-room call: use the link pinned in `#major-incidents`
3. Assign roles: Incident Commander, Communications Lead, Technical Lead
4. Create the Major Incident JIRA ticket with label `major-incident`
5. Update status every 30 minutes in Slack until resolved

---

## 4. Permanently Block an IP

A "permanent" block requires bypassing the 24/72-hour auto-unblock schedule. The recommended approach is to add the IP to the permanent deny-list in the Terraform WAF configuration rather than the SOAR-managed IP set.

### Procedure

1. **Document justification** in a JIRA ticket with:
   - IP address and CIDR
   - Reason for permanent block
   - Date of first attack
   - Approval from SOC Lead

2. **Add to Terraform permanent blocklist** (`aws-waf/terraform/waf_static_rules.tf`):
   ```hcl
   # Permanent SOAR blocks — approved by SOC Lead
   # INC-NNNN — <reason> — <date>
   "203.0.113.1/32",
   ```
   Submit a pull request for review and apply via CI/CD.

3. **Tag the IP in the SOAR IP set** with a permanent tag to prevent auto-unblock (edit the JIRA ticket description to include `permanent=true`). The n8n auto-unblock workflow skips IPs with this tag.

4. **Document in the block registry**: Update the internal blocklist spreadsheet (link in the Security wiki) with:
   - IP / CIDR
   - Attack type
   - Block date
   - JIRA ticket
   - Reviewer

5. **For ASN-level blocks**: Requires CISO sign-off due to potential for collateral blocking of legitimate users.

---

## 5. Whitelist a False Positive

False positives arise when a legitimate IP is blocked by an automated rule. Common causes: office VPN exit nodes, payment gateway callback IPs, third-party API integrations, GeoIP errors.

### Procedure

1. **Identify the source** of the false positive from the JIRA ticket and audit log.

2. **Verify legitimacy** of the IP:
   - Reverse DNS lookup
   - Confirm with the relevant team (payments, partners, QA)
   - Check against known-good IP lists

3. **Immediate unblock** (operational):
   ```bash
   # Remove from AWS WAF
   python3 /usr/local/sbin/waf_auto_block.py unblock --ip <ip>/32 --scope CLOUDFRONT
   python3 /usr/local/sbin/waf_auto_block.py unblock --ip <ip>/32 --scope REGIONAL

   # Remove from on-prem firewall
   sudo python3 /usr/local/sbin/firewall_manager.py unblock-ip --ip <ip>
   ```

4. **Add to permanent whitelist** to prevent re-blocking:

   a. **AWS WAF whitelist** (edit `infra-terraform/waf.tf` — existing `whs_ipset` or `UsAndLicencees` sets):
   ```hcl
   # Add to the relevant IP set
   "203.0.113.1/32",
   ```
   Submit a pull request. This is the most reliable method.

   b. **n8n whitelist node**: Add the IP or CIDR to the "Whitelist Check" node in each active n8n workflow. This prevents future SOAR actions but does not protect against direct ModSecurity or fail2ban blocks.

   c. **ModSecurity whitelist** (for ModSecurity-specific false positives):
   ```apache
   # /etc/nginx/modsecurity/acmetocasino-whitelist.conf
   SecRule REMOTE_ADDR "@ipMatch 203.0.113.1" \
     "id:9000010,phase:1,pass,nolog,\
     ctl:ruleEngine=Off,\
     msg:'Whitelisted IP — INC-NNNN'"
   sudo systemctl reload nginx
   ```

5. **Update the JIRA ticket**: Mark as `false-positive`, note the whitelist action taken, and close.

6. **Tune the detection rule** if the same false positive type is expected to recur (see [Section 6](#6-tune-detection-thresholds)).

---

## 6. Tune Detection Thresholds

Detection thresholds are configured in n8n workflow nodes. Changes should be tested in staging before applying to production.

### Locate the Threshold

1. Open n8n UI → select the relevant workflow (e.g., `brute-force-response`)
2. Click the **Severity Classifier** node
3. Expand the **Parameters** panel

### Default Thresholds

| Alert Type | Parameter | Default | Location |
|---|---|---|---|
| Brute force P1 | `bf_p1_threshold` | 100 attempts/min | Brute Force workflow → Classifier node |
| Brute force P2 | `bf_p2_threshold` | 50 attempts/min | Brute Force workflow → Classifier node |
| DDoS P1 | `ddos_p1_rps` | 10,000 req/min | DDoS workflow → Classifier node |
| DDoS P2 | `ddos_p2_rps` | 2,000 req/min | DDoS workflow → Classifier node |
| WAF rate limit | `rate_limit_requests` | 2,000 / 5 min | WAF script default in `waf_auto_block.py` |
| Auto-unblock P1 | `unblock_p1_hours` | 72 hours | Scheduled Unblock workflow |
| Auto-unblock P2 | `unblock_p2_hours` | 24 hours | Scheduled Unblock workflow |

### Tuning Process

1. **Create a JIRA ticket** describing the reason for the change and expected impact.

2. **Test in staging**: Deploy the modified workflow to the staging n8n instance first. Use the `--dry-run` flag on both scripts to simulate without applying changes.

3. **Monitor false-positive and false-negative rates** for 24 hours in staging.

4. **Apply to production**: Export the updated workflow JSON and import it into the production n8n instance.

5. **Document the change**: Update the threshold table above in this runbook via pull request.

6. **Review outcomes** after 7 days: check the ratio of P1/P2 auto-blocks that were later marked as false positives. A ratio > 10% indicates thresholds are too sensitive.

### When to Lower Sensitivity (raise thresholds)

- False positive rate > 10%
- Legitimate users (e.g., gaming cafe with shared IP) are being blocked
- A new marketing campaign is driving high-volume legitimate traffic

### When to Raise Sensitivity (lower thresholds)

- Repeated attacks slipping through at medium severity
- New threat intelligence indicates lower-threshold attacks are succeeding in the industry
- Penetration test reveals detection gaps

---

## 7. Add New Detection Rules

### Overview

New detection rules are implemented as n8n workflows. Each workflow must:
1. Receive events via the standard webhook endpoint (`/webhook/security-events`)
2. Implement the standard whitelist check
3. Classify severity using the shared P1–P4 scale
4. Apply actions through the standard action nodes (WAF block, firewall block, notify)
5. Write an audit record

### Step-by-Step: Add a New Detection Rule

**Example**: Detect suspicious withdrawal velocity (fraud indicator)

**Step 1 — Define the rule**

Create a JIRA ticket with:
- Rule name: `suspicious-withdrawal-velocity`
- Trigger: > 3 withdrawal attempts from the same IP within 10 minutes
- Severity: P2 HIGH by default
- Response: Rate-limit IP + create JIRA ticket + Slack alert

**Step 2 — Create the n8n workflow**

1. In the n8n UI, duplicate the closest existing workflow (e.g., `brute-force-response.json`)
2. Rename it to `withdrawal-velocity-response`
3. Modify the **Threat Analyzer** node: update the pattern matching logic
4. Update the **Severity Classifier** node with the new thresholds
5. Test with a manual trigger using the **Execute Workflow** button

**Step 3 — Add the event type to the dispatcher**

In the `security-events-dispatcher` workflow, add a routing rule:

```javascript
// In the Switch node, add a new case:
case 'suspicious_withdrawal':
  return [{ json: $input.item.json }];  // routes to the new workflow
```

**Step 4 — Update the log shipper**

Ensure the payment service emits structured events with `event_type: suspicious_withdrawal`:

```json
{
  "event_type": "suspicious_withdrawal",
  "source_ip": "203.0.113.5",
  "account_id": "USR-12345",
  "withdrawal_count": 4,
  "timeframe_minutes": 10,
  "severity": "HIGH",
  "timestamp": "2026-03-16T12:00:00Z"
}
```

**Step 5 — Test end-to-end**

```bash
curl -X POST http://n8n-host:5678/webhook/security-events \
  -H "X-SOAR-Token: <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "suspicious_withdrawal",
    "source_ip": "203.0.113.5",
    "account_id": "USR-12345",
    "withdrawal_count": 4,
    "timeframe_minutes": 10,
    "severity": "HIGH",
    "timestamp": "2026-03-16T12:00:00Z"
  }'
```

Verify that the expected actions are triggered (WAF rate-limit, Slack notification, JIRA ticket).

**Step 6 — Add a ModSecurity rule (if HTTP-layer detection is needed)**

```apache
# /etc/nginx/modsecurity/acmetocasino-rules.conf
SecRule REQUEST_URI "@beginsWith /api/v1/withdrawals" \
  "id:9001001,\
  phase:2,\
  pass,\
  nolog,\
  setvar:ip.withdrawal_count=+1,\
  expirevar:ip.withdrawal_count=600"

SecRule IP:WITHDRAWAL_COUNT "@gt 3" \
  "id:9001002,\
  phase:2,\
  deny,\
  status:429,\
  msg:'Suspicious withdrawal velocity',\
  logdata:'%{ip.withdrawal_count} attempts in 10 min'"
```

**Step 7 — Document the rule**

Update this runbook's alert response procedures section with a new entry for the rule, and submit a pull request.

---

## 8. Disaster Recovery Procedure

This procedure covers recovery from two failure scenarios: (A) the n8n SOAR engine is unavailable, and (B) a complete security infrastructure failure.

---

### Scenario A — n8n SOAR Engine Unavailable

**Impact**: Automated blocking stops. Manual intervention is required. Existing blocks remain in place.

**Step 1 — Verify the failure scope**

```bash
# Check n8n container status
docker ps -a | grep n8n

# Check logs
docker logs n8n --tail 100

# Check database connectivity
docker exec n8n n8n --version
```

**Step 2 — Attempt quick recovery**

```bash
# Restart the container
docker compose restart n8n

# Wait 30 seconds, then check health
curl -f http://localhost:5678/healthz
```

**Step 3 — If the database is corrupted**

```bash
# Stop n8n
docker compose stop n8n

# Restore from the last nightly backup
pg_restore -h localhost -U n8n -d n8n /backup/n8n-$(date +%Y%m%d).dump

# Restart n8n
docker compose start n8n
```

**Step 4 — Activate manual monitoring mode**

While n8n is down, assign an analyst to monitor the following logs in real time:

```bash
# Nginx access log — high-rate IPs
tail -f /var/log/nginx/access.log | awk '{print $1}' | sort | uniq -c | sort -rn | head -20

# Auth service failed logins
tail -f /var/log/auth.log | grep "Failed password"

# fail2ban bans
tail -f /var/log/fail2ban.log
```

**Step 5 — Manual block procedure (if n8n is down)**

```bash
# Block IP manually — WAF
python3 /usr/local/sbin/waf_auto_block.py block --ip <ip>/32 --scope REGIONAL

# Block IP manually — on-prem
sudo python3 /usr/local/sbin/firewall_manager.py block-ip --ip <ip> --comment "manual-n8n-outage"
```

**Step 6 — Restore n8n from backup (full restore)**

```bash
# Deploy fresh n8n instance
docker compose down
docker compose pull
docker compose up -d

# Import all workflows from backup JSON files
for f in n8n-workflows/backups/*.json; do
  curl -X POST http://localhost:5678/api/v1/workflows \
    -H "X-N8N-API-KEY: $N8N_API_KEY" \
    -H "Content-Type: application/json" \
    -d @"$f"
done
```

---

### Scenario B — Complete Security Infrastructure Failure

**Trigger**: Both WAF and on-prem firewall are unresponsive, or a major cloud incident has taken down the AWS WAF service.

**Immediate actions (first 5 minutes)**:

1. **Page the SRE on-call** via PagerDuty if not already done.
2. **Check AWS Service Health Dashboard**: `https://health.aws.amazon.com/health/status`
3. **Enable Emergency Lockdown Mode** on the platform (admin panel → *Security → Emergency Lockdown*):
   - Restricts access to registered users only
   - Disables new account registrations
   - Enables CAPTCHA on all login endpoints

**Short-term mitigation (5–30 minutes)**:

4. **Contact AWS Support** if WAFv2 is confirmed degraded. Reference your enterprise support plan.

5. **Route traffic through Cloudflare** (backup CDN/WAF) if available. Update DNS A records to point to the Cloudflare proxy IP.

6. **Enable fail2ban aggressive mode**:
   ```bash
   sudo fail2ban-client set acme-login maxretry 5
   sudo fail2ban-client set acme-login findtime 60
   sudo fail2ban-client set acme-login bantime 86400
   ```

7. **Enable Nginx `limit_req` emergency rules**:
   ```nginx
   # /etc/nginx/conf.d/emergency_rate_limit.conf
   limit_req_zone $binary_remote_addr zone=emergency:10m rate=10r/m;
   limit_req zone=emergency burst=5 nodelay;
   ```
   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

**Recovery (30 minutes – 4 hours)**:

8. **Restore AWS WAF** via Terraform when AWS confirms the issue is resolved:
   ```bash
   cd aws-waf/terraform
   terraform apply -var-file=vars/production.tfvars
   ```

9. **Verify IP set contents** are intact after restoration:
   ```bash
   aws wafv2 get-ip-set \
     --name soar-blocklist-regional \
     --scope REGIONAL --id <id> \
     --region eu-west-1
   ```

10. **Re-enable SOAR** workflows in n8n.

11. **Disable emergency mode** on the platform admin panel.

12. **Post-incident review**: Schedule a post-mortem within 48 hours. Document the outage timeline, impact, and preventive measures.

---

### Backup and Recovery Targets

| Component | Backup Frequency | Recovery Time Objective (RTO) | Recovery Point Objective (RPO) |
|---|---|---|---|
| n8n PostgreSQL database | Nightly + WAL streaming | 2 hours | 1 hour |
| n8n workflow JSON files | Version control (git) | 30 minutes | Last commit |
| AWS WAF Terraform state | S3 remote state + versioning | 1 hour | Last apply |
| firewall audit logs | Nightly to S3 | N/A (audit only) | 24 hours |
| ModSecurity rules | Version control (git) | 30 minutes | Last commit |

---

## Appendix: Quick Reference Card

### Common Commands

```bash
# Block IP — all layers
python3 /usr/local/sbin/waf_auto_block.py block --ip <ip>/32 --scope CLOUDFRONT
python3 /usr/local/sbin/waf_auto_block.py block --ip <ip>/32 --scope REGIONAL
sudo python3 /usr/local/sbin/firewall_manager.py block-ip --ip <ip> --comment "<reason>"

# Unblock IP — all layers
python3 /usr/local/sbin/waf_auto_block.py unblock --ip <ip>/32 --scope CLOUDFRONT
python3 /usr/local/sbin/waf_auto_block.py unblock --ip <ip>/32 --scope REGIONAL
sudo python3 /usr/local/sbin/firewall_manager.py unblock-ip --ip <ip>

# Check current blocklist (on-prem)
sudo python3 /usr/local/sbin/firewall_manager.py list --output json

# Check n8n health
curl -f http://localhost:5678/healthz

# View WAF block metrics
aws cloudwatch get-metric-statistics \
  --namespace AcmeToCasino/WAF \
  --metric-name IPBlockActions \
  --dimensions Name=Action,Value=blocked \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 --statistics Sum
```

### Severity Quick Reference

| Severity | Auto-Block | Notification | Auto-Unblock |
|---|---|---|---|
| P1 Critical | All layers | PagerDuty + Slack | 72 hours |
| P2 High | WAF + firewall | Slack + JIRA | 24 hours |
| P3 Medium | WAF rate-limit | Slack | 6 hours |
| P4 Low | None | JIRA ticket | N/A |
