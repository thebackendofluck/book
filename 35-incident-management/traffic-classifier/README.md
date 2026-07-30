# Chapter 35 — DDoS vs Marketing Campaign Traffic Classifier

A production-grade automated triage and response system for iGaming platforms.
Solves the core problem: when a 30–50x traffic spike hits, is it a DDoS attack
or a legitimate marketing campaign? The wrong decision costs either $29K+ in
wasted marketing spend (false positive: block real users) or platform downtime
(false negative: serve the attack).

---

## Architecture Overview

```
Nginx / Cloudflare
       │  traffic metrics (req/s, IPs, geos, UAs…)
       ▼
┌─────────────────────┐
│  traffic_classifier │  POST /classify  → ClassificationResult
│  (FastAPI :8080)    │  GET  /status
│                     │  GET  /history
└────────┬────────────┘
         │  ClassificationResult
         ▼
┌─────────────────────┐       ┌──────────────────────┐
│ response_orchestrat │──────▶│  Cloudflare API       │  Under Attack / Cache Rules
│ or.py               │       │  AWS WAF + ASG + SNS  │
│                     │──────▶│  Redis blacklist       │
│                     │──────▶│  PagerDuty / Wazuh    │
└────────┬────────────┘       └──────────────────────┘
         │ CAMPAIGN detected
         ▼
┌─────────────────────┐       ┌──────────────────────┐
│ autoscaler.py       │──────▶│  On-Prem Docker Comp  │  SSH + docker compose scale
│                     │       │  AWS ASG / ECS        │  set_desired_capacity
│                     │──────▶│  Cloudflare KV warm   │
└─────────────────────┘       └──────────────────────┘

┌─────────────────────┐
│ marketing_calendar  │  Redis-backed CRUD for campaign schedule
│ (FastAPI :8081)     │  classifier reads this to avoid false positives
└─────────────────────┘

┌─────────────────────┐
│ isp_abuse_report.py │  Post-incident: resolve ASNs, group by ISP,
│                     │  generate RFC-5321 abuse emails
└─────────────────────┘
```

---

## Files

| File | Purpose |
|---|---|
| `traffic_classifier.py` | FastAPI classification engine (:8080) |
| `response_orchestrator.py` | Automated response playbooks |
| `marketing_calendar.py` | Redis-backed campaign schedule API (:8081) |
| `autoscaler.py` | Multi-platform scale-up/down automation |
| `isp_abuse_report.py` | Post-incident ISP abuse report generator |
| `test_classifier.py` | Unit + integration tests (5 realistic scenarios) |
| `dashboard_widget.html` | Real-time NOC dashboard with override buttons |
| `requirements.txt` | Python dependencies |

---

## Quick Start

### 1. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start Redis

```bash
docker run -d -p 6379:6379 redis:7-alpine
```

### 3. Configure environment

```bash
# Required for production; all have safe defaults for local dev
export REDIS_URL="redis://localhost:6379/0"

# Cloudflare
export CF_API_TOKEN="your-token"
export CF_ZONE_ID="your-zone-id"
export CF_ACCOUNT_ID="your-account-id"

# AWS
export AWS_REGION="us-east-1"
export AWS_ASG_NAME="igaming-asg"
export AWS_WAF_IPSET_ID="your-ipset-id"
export AWS_WAF_IPSET_NAME="ddos-blocklist"
export AWS_SNS_TOPIC_ARN="arn:aws:sns:us-east-1:123456789012:noc-alerts"

# Alerting
export PAGERDUTY_ROUTING_KEY="your-routing-key"

# Wazuh SIEM
export WAZUH_MANAGER_URL="https://wazuh001.internal:55000"
export WAZUH_API_USER="wazuh"
export WAZUH_API_PASSWORD="your-password"

# On-premises SSH
export ONPREM_HOSTS="10.0.10.10,10.0.10.11"
export SSH_USER="deploy"
export SSH_KEY_PATH="~/.ssh/id_ed25519"
export DOCKER_COMPOSE_DIR="/opt/igaming"

# ISP abuse reporting
export VICTIM_IP="203.0.113.10"
export VICTIM_DOMAIN="acmetocasino.com"
export REPORTER_NAME="Security Team"
export REPORTER_EMAIL="security@acmetocasino.com"
export IPINFO_TOKEN="your-ipinfo-token"
```

### 4. Start the services

```bash
# Classifier API
python traffic_classifier.py

# Marketing Calendar API (separate terminal)
python marketing_calendar.py

# Open dashboard in browser
open dashboard_widget.html
```

### 5. Run tests

```bash
pytest test_classifier.py -v
```

---

## API Reference

### Classifier — POST /classify

```json
{
  "metrics": {
    "requests_per_second": 15000,
    "unique_ips": 85000,
    "total_requests": 900000,
    "ua_diversity": 0.12,
    "path_diversity": 0.08,
    "tls_fingerprint_diversity": 0.10,
    "avg_session_depth": 1.2,
    "top_geo_concentration": 0.72,
    "datacenter_ip_ratio": 0.88,
    "conversion_rate": 0.0,
    "registration_rate": 0.0,
    "request_timing_regularity": 0.94,
    "referrer_presence": 0.02,
    "new_ip_ratio": 0.91,
    "dominant_geo": null
  }
}
```

**Response:**

```json
{
  "traffic_class": "ATTACK",
  "confidence": 0.91,
  "fingerprint": { "normalized_score": 0.97, "ua_diversity": 0.12, ... },
  "campaign_active": false,
  "campaign_name": null,
  "explanation": [
    "85%+ datacenter IPs with <15% UA diversity — high-confidence botnet.",
    "Attack score 0.97 exceeds threshold 0.65."
  ],
  "recommended_action": "ENABLE_DDOS_PROTECTION_IMMEDIATELY"
}
```

### Marketing Calendar — POST /campaigns

```json
{
  "name": "Carnival Bonus 2026",
  "campaign_type": "PAID_SOCIAL",
  "start_time": 1740672000,
  "end_time": 1740844800,
  "expected_traffic_multiplier": 12.0,
  "target_geos": ["BR", "AR"],
  "landing_pages": ["/promo/carnival", "/bonus/deposit"]
}
```

---

## Classification Logic

The engine computes a **weighted signal vector** from 10 traffic signals.
Each signal is normalised to [0, 1]:

| Signal | Direction | Weight | Rationale |
|---|---|---|---|
| `ua_diversity` | High = legit | -2.0 | Real browsers are diverse; botnets reuse UAs |
| `path_diversity` | High = legit | -1.5 | Real users browse; bots hit 1-2 endpoints |
| `session_depth` | High = legit | -1.8 | Real users click multiple pages |
| `geo_concentration` | High = suspicious | +1.2 | Campaigns target specific geos; DDoS is random or DC-concentrated |
| `conversion_signals` | High = legit | -3.0 | Bots never register or deposit — strongest signal |
| `request_timing_regularity` | High = attack | +2.5 | Machine-regular timing is inhuman |
| `tls_fingerprint_diversity` | High = legit | -1.5 | Real browsers have diverse JA3 hashes |
| `referrer_presence` | High = legit | -1.8 | Ad/social campaigns carry referrer headers |
| `datacenter_ip_ratio` | High = attack | +2.0 | Rented VM/DC IPs indicate botnet |
| `new_ip_ratio` | High = suspicious | +1.0 | Mass new IPs suggest freshly-provisioned botnet |

The raw score passes through a sigmoid to produce a normalised attack
probability (0 = definitely legitimate, 1 = definitely attack).

**Rule-based overrides** cap or floor the score before final classification:
- Conversion/registration rate > threshold → cap score at 0.30
- Datacenter ratio > 85% AND UA diversity < 15% → floor score at 0.85
- Active marketing campaign in calendar → reduce score by 0.15

---

## Response Playbooks

### ATTACK (confidence ≥ 0.80)

All actions fire concurrently:

1. Cloudflare: enable "Under Attack" mode
2. AWS WAF: add attacking IPs to IP set
3. Redis: blacklist IPs with 24h TTL
4. Disk: save evidence JSON for post-incident analysis
5. PagerDuty: trigger critical incident
6. AWS SNS: publish to NOC topic
7. Wazuh: log structured SIEM event

**Graduated response** (confidence 0.65–0.79): rate-limit only, no block.

### MARKETING_CAMPAIGN (confidence ≥ 0.70)

1. Redis: set "Campaign Mode Active" banner for dashboard
2. Redis: increase per-IP rate limit to 600 rpm
3. Cloudflare: add cache-everything rule for `/promo/*`
4. AWS ASG: increase desired capacity to profile target
5. Wazuh: log informational SIEM event

### UNKNOWN

1. Redis: apply graduated rate limit (120 rpm)
2. PagerDuty: warning-severity incident for NOC
3. AWS SNS: notification requesting manual classification

Never blocks on UNKNOWN — always waits for human confirmation.

---

## Scale Profiles

| Profile | On-Prem Replicas | AWS ASG | CF Cache TTL | Use Case |
|---|---|---|---|---|
| `normal` | api:2, fe:2 | 2 | 5 min | Baseline |
| `campaign_small` | api:4, fe:4 | 4 | 1 h | Small paid campaign (2x) |
| `campaign_large` | api:10, fe:8 | 10 | 2 h | Major campaign (5x) |
| `event_mega` | api:20, fe:16 | 20 | 4 h | World Cup / Olympics (10x) |
| `attack_lockdown` | api:2, fe:2 | 2 | 24 h | Under attack — minimise origin |

All platforms scale concurrently. Scale-down is scheduled automatically
with a configurable grace period (default: 10–30 min depending on profile).

---

## ISP Abuse Reports

After a confirmed DDoS, generate and optionally send abuse reports:

```bash
# Generate from evidence file (saved by orchestrator)
python isp_abuse_report.py /var/log/traffic-classifier/evidence/attack_20260331_120000/

# Generate and auto-send via SMTP
ABUSE_AUTO_SEND=true python isp_abuse_report.py attack_evidence.json
```

The reporter:
1. Resolves ASN/ISP for every attacking IP (ipinfo.io API, with caching)
2. Groups IPs by ASN
3. Generates RFC-5321-compliant abuse email per ISP
4. Saves all evidence + email drafts to the evidence directory
5. Optionally sends via SMTP

---

## Dashboard

Open `dashboard_widget.html` directly in a browser (no build step required).
Configure the API endpoints via browser console globals before the page loads:

```html
<script>
  window.TC_API_BASE      = 'https://classifier.internal';
  window.TC_CALENDAR_BASE = 'https://calendar.internal';
  window.TC_POLL_INTERVAL = 3000;  // ms
</script>
```

The dashboard provides:
- Real-time status banner (NORMAL / CAMPAIGN / ATTACK / UNKNOWN)
- Traffic fingerprint radar chart (9 signals)
- Live metrics with RPS sparkline
- Classifier explanation text
- Marketing calendar (active + upcoming campaigns)
- Classification history table (last 15 decisions with confidence scores)
- One-click NOC override buttons ("This is a Campaign" / "This is an Attack" / "False Alarm")

All user-visible strings pass through DOMPurify before rendering.
Override buttons use a server-side allowlist to prevent classification injection.
