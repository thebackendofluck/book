# SOAR Architecture — AcmeToCasino iGaming Platform

This document describes the architecture of the Security Orchestration, Automation and Response (SOAR) system, with detailed flow diagrams for each major operational scenario.

---

## Table of Contents

1. [High-Level SOAR Architecture](#1-high-level-soar-architecture)
2. [Threat Detection Flow](#2-threat-detection-flow)
3. [Brute Force Response Sequence](#3-brute-force-response-sequence)
4. [DDoS Mitigation Flow](#4-ddos-mitigation-flow)
5. [AWS WAF + On-Prem Integration](#5-aws-waf--on-prem-integration)
6. [Incident Lifecycle](#6-incident-lifecycle)
7. [Component Interaction Reference](#7-component-interaction-reference)

---

## 1. High-Level SOAR Architecture

This diagram shows all major components and their groupings. Arrows represent data flow direction.

```mermaid
graph TB
  subgraph "Log Sources"
    nginx[Nginx Access Logs]
    app[Application Logs]
    waf_logs[AWS WAF Logs]
    auth[Auth Service Logs]
    kafka[Kafka Events]
  end

  subgraph "Log Shipping"
    filebeat[Filebeat / Fluent Bit]
  end

  subgraph "n8n SOAR Engine"
    webhook[Webhook Receiver<br/>/webhook/security-events]
    analyzer[Threat Analyzer<br/>Pattern Matching]
    classifier[Severity Classifier<br/>P1 / P2 / P3 / P4]
    responder[Auto Responder<br/>Decision Router]
    queue[Action Queue<br/>Rate-limited]
  end

  subgraph "Defense Layer — Cloud"
    aws_waf[AWS WAF v2<br/>soar-blocklist-cloudfront<br/>soar-blocklist-regional]
    shield[AWS Shield Advanced<br/>L3/L4 DDoS]
    cloudwatch[CloudWatch<br/>AcmeToCasino/WAF metrics]
  end

  subgraph "Defense Layer — On-Premise"
    modsec[ModSecurity v3<br/>HTTP Layer]
    nftables[nftables / iptables<br/>inet soar table]
    fail2ban[fail2ban<br/>Auth Events]
  end

  subgraph "Notification"
    slack[Slack<br/>#security-alerts]
    pager[PagerDuty<br/>P1 On-call]
    email[Email<br/>Security Team]
    jira[JIRA / Linear<br/>Analyst Tickets]
  end

  subgraph "Dashboard"
    soc[SOC Dashboard<br/>Real-time Incidents]
    grafana[Grafana<br/>WAF Metrics]
  end

  nginx --> filebeat
  app --> filebeat
  auth --> filebeat
  waf_logs --> filebeat
  kafka --> filebeat

  filebeat --> webhook
  webhook --> analyzer
  analyzer --> classifier
  classifier --> responder
  responder --> queue

  queue --> aws_waf
  queue --> shield
  queue --> modsec
  queue --> nftables
  queue --> fail2ban

  aws_waf --> cloudwatch
  cloudwatch --> grafana
  cloudwatch --> soc

  queue --> slack
  queue --> pager
  queue --> email
  queue --> jira

  soc --> grafana
```

### Key Design Principles

- **Defense in depth**: Every automated block is applied simultaneously at the cloud edge (AWS WAF) and on-premises (nftables/iptables), so a single layer failure does not leave the platform exposed.
- **Non-blocking queue**: The n8n action queue rate-limits outbound API calls to avoid overwhelming AWS WAF's write API or triggering CloudWatch throttling.
- **Fail-safe**: If n8n is unreachable, fail2ban and ModSecurity continue operating autonomously using their own local rules.
- **Auditability**: Every action produces a structured audit record in `/var/log/acmetocasino/firewall_audit.jsonl` and a CloudWatch metric.

---

## 2. Threat Detection Flow

This flowchart shows the complete decision tree from raw log ingestion through to the final response action.

```mermaid
flowchart TD
  A([Log event received<br/>via webhook]) --> B{Valid token?}
  B -- No --> Z1([401 Rejected])
  B -- Yes --> C[Parse event fields<br/>source_ip · event_type · timestamp]

  C --> D{Source IP in<br/>whitelist?}
  D -- Yes --> Z2([Log only — no action])
  D -- No --> E[Enrich IP<br/>GeoIP · ASN · Threat Intel]

  E --> F{Known bad actor?<br/>Threat Intel match}
  F -- Yes --> G[Override to P1 CRITICAL]
  F -- No --> H{Evaluate event type}

  H -- brute_force --> I1[Count failed attempts<br/>in 60-second window]
  H -- ddos --> I2[Measure request rate<br/>packets/sec]
  H -- sql_injection --> I3[Match payload patterns<br/>OWASP CRS rules]
  H -- account_takeover --> I4[Correlate with<br/>session anomalies]
  H -- credential_stuffing --> I5[Check password spray<br/>patterns across accounts]
  H -- geo_violation --> I6[Compare against<br/>licensed territory list]

  G --> J{Classify Severity}
  I1 --> J
  I2 --> J
  I3 --> J
  I4 --> J
  I5 --> J
  I6 --> J

  J -- P1 Critical --> K1[Block ALL layers<br/>WAF + nftables + fail2ban]
  J -- P2 High --> K2[Block WAF + nftables<br/>Rate-limit on-prem]
  J -- P3 Medium --> K3[Rate-limit WAF<br/>Alert only on-prem]
  J -- P4 Low --> K4[Log + JIRA ticket<br/>Analyst review]

  K1 --> L1[Page PagerDuty<br/>Notify Slack]
  K2 --> L2[Notify Slack<br/>Create JIRA]
  K3 --> L3[Notify Slack]
  K4 --> L4[JIRA ticket only]

  K1 --> M[Publish CloudWatch<br/>IPBlockActions metric]
  K2 --> M
  K3 --> M

  M --> N([Execution complete<br/>Audit record written])
  L1 --> N
  L2 --> N
  L3 --> N
  L4 --> N
```

### Whitelist Evaluation

Before any classification, the source IP is checked against:

1. The AWS WAF IP sets `whs_ipset` (CLOUDFRONT) and `UsAndLicencees` (REGIONAL) — existing whitelist sets defined in `infra-terraform/waf.tf`
2. An internal n8n whitelist node containing known-good IP ranges (office networks, internal scanners, payment gateway callbacks)

If the IP matches any whitelist, the event is logged but no defensive action is taken.

### Threat Intelligence Enrichment

The enrichment step queries:

- **GeoIP database** (MaxMind GeoLite2): resolves country code and ASN
- **Internal threat intelligence feed**: JSON feed updated every hour by the threat-intel pipeline
- **AbuseIPDB** (optional, P1/P2 events): queries reputation score via REST API

---

## 3. Brute Force Response Sequence

This sequence diagram shows the exact order of operations when a brute-force attack is detected at P2 (HIGH) severity.

```mermaid
sequenceDiagram
  autonumber

  participant LS as Log Shipper<br/>(Filebeat)
  participant WH as n8n Webhook
  participant AN as Threat Analyzer
  participant CL as Severity Classifier
  participant WAF as AWS WAF v2<br/>(waf_auto_block.py)
  participant FW as On-Prem Firewall<br/>(firewall_manager.py)
  participant F2B as fail2ban
  participant CW as CloudWatch
  participant SL as Slack
  participant JR as JIRA

  LS->>WH: POST /webhook/security-events<br/>{ event_type: brute_force, ip: x.x.x.x, attempts: 30 }
  WH->>AN: Validate token, parse fields
  AN->>AN: Count events in 60s window<br/>Check IP against whitelist
  AN->>CL: Forward enriched event
  CL->>CL: 30 attempts/min → P2 HIGH
  CL->>WAF: block --ip x.x.x.x/32 --scope REGIONAL
  WAF->>WAF: Fetch soar-blocklist-regional<br/>Append IP + LockToken update
  WAF-->>CL: 200 OK (blocked)
  WAF->>CW: PutMetricData IPBlockActions/blocked
  CL->>FW: POST /webhook/firewall-block { ip: x.x.x.x }
  FW->>FW: nft add element inet soar blocklist { x.x.x.x }<br/>Write audit record
  FW-->>CL: 200 OK (blocked)
  CL->>F2B: POST /webhook/fail2ban-ban { ip: x.x.x.x, jail: acme-login }
  F2B->>F2B: fail2ban-client set acme-login banip x.x.x.x
  F2B-->>CL: 200 OK (banned)
  CL->>SL: POST incoming webhook<br/>{ text: "P2 Brute Force — x.x.x.x blocked" }
  SL-->>CL: 200 OK
  CL->>JR: POST /rest/api/3/issue<br/>{ summary: "Brute Force INC-NNNN" }
  JR-->>CL: 201 Created { key: INC-NNNN }
  CL-->>WH: 200 { status: accepted, actions: [waf_block, fw_block, f2b_ban, slack, jira] }
  WH-->>LS: 200 OK
```

### Automatic Unblock

After a configurable cool-down period (default: 24 hours for P2, 72 hours for P1), the n8n scheduled unblock workflow runs:

1. Queries the JIRA ticket for analyst sign-off
2. If the ticket is resolved or the IP's threat score has dropped below threshold, calls `POST /webhook/waf-unblock` and `POST /webhook/firewall-block` with a removal flag
3. Publishes an `unblocked` CloudWatch metric

---

## 4. DDoS Mitigation Flow

This flowchart shows the multi-level escalation used to absorb volumetric attacks.

```mermaid
flowchart TD
  A([DDoS event detected<br/>Source IP + request rate]) --> B{Request rate<br/>per source IP}

  B -- "< 500 req/min" --> C[P4 Low<br/>Monitor only]
  B -- "500–2,000 req/min" --> D[P3 Medium<br/>Apply WAF rate limit]
  B -- "2,000–10,000 req/min" --> E[P2 High<br/>Block source IP]
  B -- "> 10,000 req/min" --> F[P1 Critical<br/>Full DDoS response]

  D --> D1[Update soar-rate-limit rule<br/>to 500 req / 5 min window]
  D1 --> D2[Slack alert #security-alerts]

  E --> E1[Block IP in AWS WAF<br/>CLOUDFRONT + REGIONAL]
  E1 --> E2[Block on nftables<br/>inet soar blocklist]
  E2 --> E3[Slack + JIRA ticket]

  F --> F1{Single source IP?}
  F1 -- Yes --> F2[Block IP — all layers]
  F1 -- No<br/>Distributed --> F3[Block ASN range<br/>in AWS WAF]

  F2 --> F4[Enable AWS Shield Advanced<br/>DDoS Response Team notification]
  F3 --> F4

  F4 --> F5[Reduce WAF rate limit<br/>to 100 req / 5 min]
  F5 --> F6[Page PagerDuty<br/>Escalate to on-call SRE]
  F6 --> F7{Attack persists<br/>> 15 minutes?}

  F7 -- Yes --> F8[Contact AWS DRT<br/>via Shield console]
  F7 -- No --> F9[Monitor — auto-unblock<br/>after 1-hour quiet period]
  F8 --> F9

  C --> Z([Incident closed<br/>Audit logged])
  D2 --> Z
  E3 --> Z
  F9 --> Z
```

### L7 vs L3/L4 DDoS

| Attack Type | Primary Mitigation | Secondary Mitigation |
|---|---|---|
| HTTP flood (L7) | AWS WAF rate-based rules | ModSecurity connection limits |
| TCP SYN flood (L4) | AWS Shield Advanced | nftables connection tracking drop |
| UDP amplification (L3) | AWS Shield Advanced | On-prem ISP null-route request |
| Slowloris | Nginx `limit_req` + ModSecurity | fail2ban slow-connection jail |

---

## 5. AWS WAF + On-Prem Integration

This diagram shows how the cloud and on-premise defensive layers coordinate, and the data paths for the two Python management scripts.

```mermaid
graph LR
  subgraph "Internet Traffic"
    user[End User / Attacker]
  end

  subgraph "AWS Cloud Edge"
    cf[CloudFront Distribution]
    cf_waf[AWS WAF v2<br/>CLOUDFRONT scope<br/>soar-blocklist-cloudfront]
    alb[Application Load Balancer]
    alb_waf[AWS WAF v2<br/>REGIONAL scope<br/>soar-blocklist-regional]
    shield[AWS Shield Advanced]
  end

  subgraph "On-Premise DMZ"
    nginx_proxy[Nginx Reverse Proxy<br/>+ ModSecurity v3]
    nft[nftables<br/>inet soar table<br/>blocklist / blocklist6]
    ipset[ipset<br/>soar_blocklist]
  end

  subgraph "Application Tier"
    app_server[App Servers]
    auth_server[Auth Service]
  end

  subgraph "SOAR Control Plane"
    n8n[n8n Engine]
    waf_script["waf_auto_block.py<br/>CLOUDFRONT + REGIONAL"]
    fw_script["firewall_manager.py<br/>nftables / iptables"]
    cw[CloudWatch Metrics]
  end

  user --> shield
  shield --> cf
  cf --> cf_waf
  cf_waf -- "Blocked IPs dropped" --> X1[ ]
  cf_waf -- "Allowed traffic" --> alb
  alb --> alb_waf
  alb_waf -- "Blocked IPs dropped" --> X2[ ]
  alb_waf -- "Allowed traffic" --> nft
  nft -- "Blocked IPs dropped" --> X3[ ]
  nft --> nginx_proxy
  nginx_proxy -- "ModSec violations blocked" --> X4[ ]
  nginx_proxy --> app_server
  nginx_proxy --> auth_server

  n8n --> waf_script
  waf_script --> cf_waf
  waf_script --> alb_waf
  waf_script --> cw

  n8n --> fw_script
  fw_script --> nft
  fw_script --> ipset

  app_server --> n8n
  auth_server --> n8n
  cf_waf --> n8n
```

### Lock-Token Management

AWS WAF v2 uses optimistic locking: every update must include the current `LockToken`. The `waf_auto_block.py` script handles this transparently with exponential back-off (up to 5 retries, 2–10 second delays). Concurrent modifications from multiple n8n worker instances are safe.

### Scope Routing

| Scope | Script Target | AWS Region |
|---|---|---|
| `CLOUDFRONT` | `soar-blocklist-cloudfront` | `us-east-1` (hardcoded — CloudFront requirement) |
| `REGIONAL` | `soar-blocklist-regional` | Configurable via `--region` flag / `AWS_REGION` env var |

---

## 6. Incident Lifecycle

This state diagram shows all states an incident passes through from first detection to post-mortem closure.

```mermaid
stateDiagram-v2
  [*] --> Detection: Log event received

  Detection --> Triage: SOAR classifier\nassigns severity

  Triage --> AutoResponse: P1 / P2\nClear threat signal
  Triage --> AnalystReview: P3 / P4\nAmbiguous signal

  AutoResponse --> Mitigation: Automated blocks\napplied on all layers
  AnalystReview --> Mitigation: Analyst confirms\nthreat and approves block
  AnalystReview --> FalsePositive: Analyst marks\nas false positive

  FalsePositive --> Closed: IP added to whitelist\nNo action taken

  Mitigation --> Monitoring: Attack traffic\ndrops below threshold

  Monitoring --> Recovery: Quiet period\n(P1: 72h, P2: 24h)
  Monitoring --> Escalation: Attack continues\nor resurfaces

  Escalation --> Mitigation: Additional blocks\napplied (ASN range,\nShield DRT engaged)

  Recovery --> AutoUnblock: n8n scheduled workflow\nchecks analyst sign-off
  AutoUnblock --> PostMortem: IP removed from\nall blocklists

  PostMortem --> Closed: Timeline documented\nRule tuning applied\nJIRA resolved

  Closed --> [*]
```

### State Definitions

| State | Description | Responsible Party |
|---|---|---|
| **Detection** | SOAR receives an event from a log shipper or WAF | Automated |
| **Triage** | Threat analyzer and severity classifier evaluate the event | Automated |
| **AutoResponse** | Blocks applied immediately without human approval | Automated (n8n) |
| **AnalystReview** | JIRA ticket created; analyst must approve or dismiss | SOC Analyst |
| **FalsePositive** | IP confirmed as legitimate; added to whitelist | SOC Analyst |
| **Mitigation** | Active blocks in place; traffic being dropped at cloud edge and on-prem | Automated |
| **Monitoring** | Attack traffic has subsided; SOAR watches for recurrence | Automated |
| **Escalation** | Attack continues beyond mitigation; additional layers engaged | SRE + AWS DRT |
| **Recovery** | Quiet period elapsed; auto-unblock workflow preparing | Automated |
| **AutoUnblock** | IP removed from all blocklists; metrics updated | Automated |
| **PostMortem** | Timeline documented; detection rules tuned if needed | SOC Lead |
| **Closed** | JIRA ticket resolved; incident archived | SOC Analyst |

---

## 7. Component Interaction Reference

### Port and Protocol Map

| Source | Destination | Protocol | Port | Purpose |
|---|---|---|---|---|
| Log shippers | n8n webhook | HTTPS | 5678 | Security event ingestion |
| n8n | AWS WAF API | HTTPS | 443 | IP set updates |
| n8n | CloudWatch API | HTTPS | 443 | Metric publishing |
| n8n | On-prem `firewall_manager.py` | SSH / local exec | 22 / local | Firewall block commands |
| n8n | Slack API | HTTPS | 443 | Notifications |
| n8n | PagerDuty API | HTTPS | 443 | On-call alerts |
| n8n | JIRA API | HTTPS | 443 | Ticket creation |
| Grafana | CloudWatch | HTTPS | 443 | Metric queries |
| SOC Dashboard | n8n REST API | HTTPS | 5678 | Execution history |

### Script Invocation Reference

| Script | Invoked By | Key Commands |
|---|---|---|
| `aws-waf/waf_auto_block.py` | n8n HTTP node / CLI | `block`, `unblock`, `create-rate-rule`, `update-rate-rule` |
| `onprem-firewall/firewall_manager.py` | n8n SSH node / CLI | `block-ip`, `unblock-ip`, `block-cidr`, `rate-limit`, `list`, `flush`, `status` |

### Data Flow Summary

```
External Traffic
       ↓
AWS Shield (L3/L4 DDoS absorption)
       ↓
CloudFront + AWS WAF (CLOUDFRONT scope — soar-blocklist-cloudfront)
       ↓
ALB + AWS WAF (REGIONAL scope — soar-blocklist-regional)
       ↓
nftables SOAR table (inet soar blocklist/blocklist6)
       ↓
Nginx + ModSecurity v3 (HTTP-layer rules)
       ↓
Application / Auth Service
```

Each layer independently blocks threats, ensuring that if any single layer is misconfigured or temporarily unavailable, the remaining layers continue to protect the platform.
