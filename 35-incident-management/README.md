<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 35: Incident Management

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 35 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

This directory contains the implementation scripts for Chapter 35 - Incident Management for iGaming platforms.

## Directory Structure

```
scripts/chapter-35/
├── README.md                           # This file
├── incident-management/                # Python incident management framework
│   ├── __init__.py                     # Module exports
│   ├── incident_response.py            # Automated incident detection & response
│   ├── postmortem_framework.py         # Blameless postmortem system
│   ├── maintenance_management.py       # Maintenance window scheduling
│   └── change_management.py            # Enterprise change control
└── cloudsentinel/                      # React/TypeScript monitoring dashboard
    ├── App.tsx                         # Main React application
    ├── index.tsx                       # Entry point
    ├── types.ts                        # TypeScript interfaces
    ├── services/
    │   ├── geminiService.ts            # AI service (optional)
    │   └── jiraService.ts              # Jira integration (optional)
    ├── components/
    │   ├── Header.tsx                  # Dashboard header
    │   ├── StatusCard.tsx              # Provider status cards
    │   └── TerraformAnalyzer.tsx       # Terraform code analyzer
    ├── .env.local                      # Environment configuration
    ├── Dockerfile                      # Container build
    └── docker-compose.yml              # Docker orchestration
```

## Components Overview

### Incident Management Framework (Python)

Enterprise-grade incident management specifically designed for iGaming platforms where incidents can cost millions in revenue and trigger regulatory scrutiny.

| Module | Description | Lines |
|--------|-------------|-------|
| `incident_response.py` | Automated detection, severity classification, auto-remediation, multi-channel alerting | ~450 |
| `postmortem_framework.py` | Blameless postmortems, root cause analysis, action item tracking | ~300 |
| `maintenance_management.py` | Maintenance scheduling, conflict detection, pre/post checks | ~350 |
| `change_management.py` | Change requests, risk classification, regulatory notifications | ~400 |

### CloudSentinel Dashboard (React/TypeScript)

Web dashboard for cloud provider monitoring, Terraform analysis, and incident ticketing. **All integrations are optional** - the application works fully standalone.

| Feature | Description | Optional Integration |
|---------|-------------|---------------------|
| Cloud Status | Monitor AWS, GCP, Azure health | Gemini AI for summaries |
| Terraform Guard | Analyze IaC for breaking changes | Gemini AI for risk detection |
| Provider News | Track API changes and deprecations | Gemini AI for curation |
| Incident Tickets | Auto-create Jira tickets | Jira Cloud/Server |

**LLM Integration (Optional):**

| Feature | With AI | Without AI |
|---------|---------|------------|
| Cloud Status | Real-time AI-summarized | Static + links to official pages |
| Terraform Analysis | AI deprecation detection | Basic resource extraction |
| Provider News | AI-curated updates | Placeholder message |

**Jira Integration (Optional):**

| Feature | Description |
|---------|-------------|
| Auto-Create Tickets | Create incident tickets when provider status changes |
| Priority Mapping | Map severity to Jira priorities (P1-P4) |
| Duplicate Detection | Search existing tickets before creating new ones |
| Terraform Tickets | Create tasks for high-risk infrastructure changes |

## Installation

### Python Incident Management

```bash
# Using pip
pip install redis asyncio

# Using uv (recommended)
uv pip install redis asyncio

# For development
uv pip install redis asyncio pytest pytest-asyncio
```

### CloudSentinel Dashboard

```bash
cd cloudsentinel

# Install dependencies
npm install

# Run development server (no API key needed)
npm run dev

# Build for production
npm run build
```

### Docker Deployment

```bash
cd cloudsentinel

# Build and run without AI
docker-compose up -d

# Run with AI features
VITE_GEMINI_API_KEY=your-key docker-compose up -d
```

## Usage Examples

### Incident Detection and Response

```python
import asyncio
import redis.asyncio as redis
from incident_management import (
    IncidentManagementSystem,
    IncidentSeverity,
    IncidentStatus,
)

async def main():
    # Initialize Redis client
    redis_client = await redis.from_url("redis://localhost:6379")

    # Create incident management system
    ims = IncidentManagementSystem(
        redis_client=redis_client,
        config={
            "slack_webhook": "https://hooks.slack.com/...",
            "pagerduty_key": "your-pd-key",
        }
    )

    # Detect incident from monitoring alert
    alert_data = {
        "title": "Payment Gateway Timeout",
        "description": "Payment processing latency exceeded 5s threshold",
        "affected_services": ["payment"],
        "affected_users": 15000,
        "service_criticality": "critical",
        "error_rate": 0.25,
        "alert_type": "payment_timeout",
    }

    incident = await ims.detect_incident(alert_data)

    if incident:
        print(f"Incident created: {incident.id}")
        print(f"Severity: {incident.severity.name}")
        print(f"Status: {incident.status.value}")

        # Acknowledge incident
        await ims.acknowledge_incident(incident.id, "oncall-engineer")

        # Update status after mitigation
        await ims.update_incident_status(
            incident.id,
            IncidentStatus.MITIGATED,
            "oncall-engineer",
            "Switched to backup payment processor"
        )

asyncio.run(main())
```

### Postmortem Creation

```python
from incident_management import PostmortemFramework

async def create_postmortem(incident_id: str):
    framework = PostmortemFramework(config={})

    # Generate postmortem template
    postmortem = await framework.create_postmortem_template(incident_id)

    # Conduct blameless postmortem
    results = await framework.conduct_blameless_postmortem(postmortem)

    print("Discussion Questions:")
    for q in results["questions_discussed"]:
        print(f"  - {q}")

    print("\nAction Items:")
    for item in results["action_items_created"]:
        print(f"  [{item['priority'].upper()}] {item['title']}")

    # Generate regulatory report
    report = framework.generate_regulatory_report(postmortem)
    print(report)
```

### Change Management

```python
from incident_management import (
    ChangeManagementSystem,
    ChangeLevel,
    ChangeType,
)

async def submit_change():
    cms = ChangeManagementSystem(redis_client, config={})

    change_data = {
        "title": "Update Payment Gateway SDK",
        "description": "Upgrade Stripe SDK to v10.0.0",
        "change_type": "software_update",
        "requested_by": "dev-team",
        "business_justification": "Security patches and performance improvements",
        "technical_details": {
            "current_version": "9.5.0",
            "target_version": "10.0.0",
            "breaking_changes": ["Updated webhook signature validation"],
        },
        "risk_assessment": {
            "probability": "low",
            "impact": "medium",
            "mitigation": "Staged rollout with monitoring",
        },
        "impact_analysis": {
            "services": ["payment"],
            "downtime": "0 minutes (rolling deployment)",
        },
        "test_plan": {
            "unit_tests": True,
            "integration_tests": True,
            "load_tests": True,
        },
        "rollback_plan": "Revert to Stripe SDK 9.5.0 via Helm rollback",
        "affected_components": ["payment-service"],
        "affected_services": ["payment"],
    }

    change = await cms.submit_change_request(change_data)

    if change:
        print(f"Change submitted: {change.id}")
        print(f"Level: {change.level.name}")  # LEVEL_3 for payment
        # Level 3 changes require CAB approval and regulatory notification
```

## Incident Response SLAs

| Severity | Acknowledge | Mitigate | Resolve |
|----------|-------------|----------|---------|
| Critical | 5 min | 30 min | 4 hours |
| High | 15 min | 1 hour | 8 hours |
| Medium | 1 hour | 4 hours | 24 hours |
| Low | 4 hours | 24 hours | 7 days |

## Auto-Remediation Actions

The incident management system includes automated remediation for common incident patterns:

| Incident Type | Auto-Actions |
|---------------|--------------|
| `database_high_cpu` | Scale up instance, enable query optimization, add read replicas |
| `cache_miss_rate` | Increase cache size, adjust TTL, enable cache warming |
| `payment_timeout` | Switch to backup processor, adjust timeouts, enable circuit breaker |
| `cdn_down` | Switch to backup CDN, update DNS, enable failover routing |

## Change Levels (Regulatory)

| Level | Impact | Approval | Notification |
|-------|--------|----------|--------------|
| Level 1 | No impact | Auto-approved | None |
| Level 2 | Low impact | Team lead | Stakeholders |
| Level 3 | High impact | CAB + Executive | Regulatory bodies (UKGC, MGA) |

Level 3 services: `payment`, `gaming_logic`, `player_data`, `regulatory_reporting`, `rng`, `authentication`

## Integration Points

### With Monitoring Systems

```python
# Prometheus AlertManager webhook
@app.post("/webhook/alertmanager")
async def handle_alert(alert: dict):
    incident = await ims.detect_incident({
        "title": alert["labels"]["alertname"],
        "description": alert["annotations"]["description"],
        "affected_services": [alert["labels"]["service"]],
        "service_criticality": alert["labels"]["severity"],
    })
    return {"incident_id": incident.id if incident else None}
```

### With Slack

```python
# Slack interactive message handler
@app.post("/webhook/slack")
async def handle_slack_action(payload: dict):
    action = payload["actions"][0]
    incident_id = action["value"]

    if action["action_id"] == "acknowledge":
        await ims.acknowledge_incident(incident_id, payload["user"]["id"])

    elif action["action_id"] == "mitigate":
        await ims.update_incident_status(
            incident_id,
            IncidentStatus.MITIGATED,
            payload["user"]["id"]
        )
```

## Verification

```bash
# Type check Python modules
ty check incident-management/*.py

# Verify CloudSentinel builds
cd cloudsentinel && npm run build

# Run CloudSentinel with Docker
docker-compose up -d && docker-compose logs
```

## Related Chapters

- **Chapter 31**: Infrastructure Performance Monitoring
- **Chapter 34**: Data Analytics Platform
- **Chapter 19**: Anti-Fraud System

## License

MIT
