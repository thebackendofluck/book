# On-Call Severity Definitions — Acme Casino Platform

**Platform:** new.acmetocasino.com  
**Infrastructure:** K3s cluster on your-k8s-host (6 nodes), Patroni PostgreSQL, Cloudflare CDN  
**Compliance:** iGaming regulated environment (player funds protection, AML/KYC obligations)

---

## Severity Levels

### P0 — Critical (Immediate Response, 24/7)

**Definition:** Complete service outage, data integrity risk, active security incident, or regulatory compliance violation. Player funds or platform availability are directly impacted.

**Triggers:**
- Casino platform completely unreachable (`new.acmetocasino.com` returns 5xx or no response for >2 consecutive health-check cycles)
- Database corruption or confirmed data loss (Patroni primary failure with data divergence)
- Security breach detected — Falco alert: unauthorized shell in container, data exfiltration pattern, privilege escalation
- Payment processing failure — PIX gateway down, transactions not completing
- Active DDoS attack — Cloudflare under attack mode triggered, origin servers overwhelmed
- SSL/TLS certificates expired on production (new.acmetocasino.com, api.acmetocasino.com)
- Gambling license compliance violation — AML/KYC bypass detected, underage access control failure
- Player funds at risk — wallet balance discrepancy, double-spend detection, reconciliation failure
- Wazuh SIEM: Rule 100200+ fired (critical intrusion detection)
- All K3s nodes simultaneously NotReady

**Response time:** < 15 minutes from alert firing to acknowledged  
**Notification channels:** Phone call + SMS + Slack `#incidents-p0` + Dashboard alert (your-dashboard.example.com)  
**Escalation:** If no acknowledgment within 15 minutes → auto-escalate to next on-call engineer; if no ack in 30 minutes → page Engineering Manager  
**On-call requirement:** 24 hours / 7 days per week  

**Runbooks:**
- Platform down: `/runbooks/p0-platform-down.md`
- Database corruption: `/runbooks/p0-db-corruption.md`
- Security breach: `/runbooks/p0-security-breach.md`
- PIX gateway failure: `/runbooks/p0-pix-gateway.md`
- DDoS response: `/runbooks/p0-ddos.md`
- SSL expiry: `/runbooks/p0-ssl-expiry.md`
- Compliance violation: `/runbooks/p0-compliance.md`
- Wallet discrepancy: `/runbooks/p0-wallet-discrepancy.md`

---

### P1 — High (Urgent — Business Hours + On-Call)

**Definition:** Partial platform degradation or security findings that materially affect players or the engineering team's ability to deploy safely. Not a complete outage but trending toward P0 if unaddressed.

**Triggers:**
- Partial platform degradation — error rate >10% on primary API endpoints over 5-minute window
- Betting engine processing delays — p95 latency >5 seconds sustained for >3 minutes
- Market watch service UNHEALTHY — odds feed stale (last update >60 seconds)
- Bot manager crash loop — `CrashLoopBackOff` for >3 restart cycles
- Security scan found CRITICAL vulnerability (CVSS ≥ 9.0) in a production image
- Database replication lag >30 seconds on Patroni standby
- Any single K3s node NotReady for >5 minutes
- CI/CD pipeline blocked on `main` branch for >2 hours (no deployments possible)
- Falco: anomalous network egress from casino pods (potential data exfiltration attempt, unconfirmed)
- DefectDojo: new CRITICAL finding in a production-deployed component

**Response time:** < 1 hour from alert firing  
**Notification channels:** Slack `#incidents-p1` + Dashboard alert + Email to on-call engineer  
**Escalation:** If no acknowledgment within 1 hour → severity upgraded to P0, phone call initiated  
**On-call requirement:** Business hours primary; on-call secondary for after-hours  

**Runbooks:**
- High error rate: `/runbooks/p1-error-rate.md`
- Betting engine latency: `/runbooks/p1-betting-latency.md`
- Market watch stale: `/runbooks/p1-market-watch.md`
- Bot manager crash loop: `/runbooks/p1-bot-manager.md`
- Critical CVE in prod: `/runbooks/p1-critical-cve.md`
- Patroni replication lag: `/runbooks/p1-patroni-lag.md`
- Node NotReady: `/runbooks/p1-node-notready.md`
- Pipeline blocked: `/runbooks/p1-pipeline-blocked.md`

---

### P2 — Medium (Business Hours)

**Definition:** Non-critical service degradation, security findings requiring remediation, or infrastructure conditions that will become P1 if not addressed within days.

**Triggers:**
- Non-critical service degradation (single non-core service unavailable, e.g., bonus engine)
- Security scan found >5 HIGH severity vulnerabilities (CVSS 7.0–8.9) in production images
- Staging environment broken — blocking developer testing
- TLS certificate expiring within 14 days on any production domain
- Disk usage >80% on any K3s node or PVC
- Memory usage sustained >85% on any pod for >15 minutes
- Dependency with known CVE (CVSS >7.0) not yet patched
- Failed deployment where auto-rollback succeeded (no player impact, but root cause unknown)
- DefectDojo: new HIGH finding in a production component
- Wazuh: rule 40100–100199 fired (high-severity SIEM event)
- Renovate/Dependabot PR open >7 days with HIGH severity advisory

**Response time:** < 8 business hours  
**Notification channels:** Slack `#incidents-p2` + Dashboard  
**SLA:** Fix or mitigate within 7 calendar days  
**Escalation:** If unresolved in 7 days → escalate to P1  

---

### P3 — Low (Planned Work)

**Definition:** Quality findings, non-urgent improvements, or maintenance tasks that carry no immediate risk but should be tracked and resolved within the sprint cycle.

**Triggers:**
- Code quality findings of MEDIUM severity in security scans or static analysis
- Non-critical dependency updates (Renovate/Dependabot PRs with no security advisory)
- Documentation gaps identified in runbooks, ADRs, or API specs
- Test coverage below threshold (<80% on critical paths)
- Cleanup of stale Kubernetes resources (orphaned ConfigMaps, old ReplicaSets)
- Performance optimization opportunities identified (non-SLA-breaching)
- Log noise — non-actionable WARNING log lines exceeding 100/minute
- DefectDojo: MEDIUM or LOW findings
- Wazuh: informational or low-priority rules (below 40100)

**Response time:** Next sprint planning session  
**Notification channels:** Dashboard only (weekly digest)  
**SLA:** Fix within 30 calendar days or accept as known risk  
**Escalation:** If unresolved in 30 days → review in architecture meeting  

---

## Escalation Matrix

```
Alert Fires
    │
    ├─ P0 ──► Primary On-Call (15 min to ack)
    │              │ No ack
    │              ▼
    │         Secondary On-Call (15 min to ack)
    │              │ No ack
    │              ▼
    │         Engineering Manager (phone)
    │              │ No ack in 30 min
    │              ▼
    │         CTO / VP Engineering (phone)
    │
    ├─ P1 ──► Primary On-Call (1 hr to ack)
    │              │ No ack
    │              ▼
    │         Secondary On-Call → upgraded to P0
    │
    ├─ P2 ──► Team channel (8 business hrs to ack)
    │              │ No ack / unresolved in 7 days
    │              ▼
    │         Upgraded to P1, Engineering Manager notified
    │
    └─ P3 ──► Dashboard queue → sprint backlog
```

---

## On-Call Rotation Schedule Template

```yaml
# Rotation: weekly, Sunday 00:00 UTC handoff
# Minimum engineers in pool: 3 (to avoid >1 week on in 3 weeks)
# Each engineer must complete P0 runbook certification before joining rotation

rotation:
  cadence: weekly
  handoff_day: Sunday
  handoff_time: "00:00 UTC"
  minimum_pool_size: 3
  roles:
    primary:
      description: First responder, must ack within SLA
      reachability:
        - slack
        - sms
        - phone_call
    secondary:
      description: Backup if primary does not ack; co-responder for P0
      reachability:
        - slack
        - sms
    manager_escalation:
      description: Engineering manager, called only on P0 escalation
      reachability:
        - phone_call

# Example 3-engineer rotation (replace with real names):
schedule:
  - week: "2026-W16"
    primary: "engineer-a"
    secondary: "engineer-b"
    manager: "eng-manager"
  - week: "2026-W17"
    primary: "engineer-b"
    secondary: "engineer-c"
    manager: "eng-manager"
  - week: "2026-W18"
    primary: "engineer-c"
    secondary: "engineer-a"
    manager: "eng-manager"

# Overrides (holidays, PTO):
overrides:
  - date: "2026-04-21"   # Tiradentes (Brazil)
    primary: "engineer-b"
    secondary: "engineer-c"
```

---

## Post-Incident Review Process

### Required for P0 and P1

All P0 incidents require a blameless post-mortem. P1 incidents require a lightweight review.

**Timeline:**
1. **Incident resolved** — Alert bot marks alert as `RESOLVED`, sends resolution notification
2. **Within 24 hours (P0) / 48 hours (P1)** — Incident commander opens post-mortem doc from template
3. **Within 72 hours** — Draft post-mortem circulated to engineering team for async review
4. **Within 5 business days** — Post-mortem meeting (30 min max), action items assigned with owners and due dates
5. **Within sprint** — Action items tracked in project board; at least one item must be completed before closing

**Post-Mortem Doc Template sections:**
```
## [P0/P1] Incident Title — YYYY-MM-DD

### Summary
One paragraph: what happened, customer impact, duration.

### Timeline (all times UTC)
| Time | Event |
|------|-------|
| HH:MM | Alert fired |
| HH:MM | Primary on-call acknowledged |
| HH:MM | Root cause identified |
| HH:MM | Mitigation applied |
| HH:MM | Service restored |
| HH:MM | Incident closed |

### Root Cause
Technical root cause. What failed and why.

### Contributing Factors
What conditions allowed this to happen or made it worse.

### Detection
How was the issue detected? Was alerting adequate?

### Resolution
What steps resolved the issue?

### Action Items
| Owner | Action | Due Date | Priority |
|-------|--------|----------|----------|

### What Went Well

### What Needs Improvement
```

**P2/P3:** No formal post-mortem. Close the alert with a one-line resolution note in the dashboard.

---

## Alert Deduplication and Flapping Policy

- Alerts must fire for >2 consecutive minutes before paging (prevents flapping)
- If the same alert fires and resolves >3 times within 30 minutes, it is treated as P1 minimum (flapping = systemic issue)
- Duplicate alerts for the same root cause are grouped into one incident in the dashboard
- Resolution notification is sent only after the alert is clear for >5 minutes (prevents false-positive resolutions)
