# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Multi-Channel Alerting Service

Evaluates fraud scores and system metrics against configurable rules,
then dispatches alerts via email, Slack, SMS, or webhook.  Includes
cooldown logic to prevent alert fatigue and Redis-backed history.

Reference implementation for Chapter 41: Anti-Fraud System Deep Dive.
"""

import asyncio
import json
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import aiohttp
import redis.asyncio as redis
import structlog
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Fraud Detection -- Alerting Service",
    description="Real-time multi-channel alerting for fraud events",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=["http://localhost:3000", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_client: Optional[redis.Redis] = None
alert_rules: Dict[str, "AlertRule"] = {}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AlertRule(BaseModel):
    rule_id: str
    name: str
    description: str
    condition: str          # ">", ">=", "<", "<=", "=="
    threshold: float
    severity: str           # critical, high, medium, low
    channels: List[str]     # email, slack, sms, webhook
    cooldown_minutes: int = 60
    enabled: bool = True


class AlertTrigger(BaseModel):
    rule_id: str
    player_id: Optional[str] = None
    value: float
    context: Optional[Dict[str, Any]] = None
    source: str = "system"


class AlertResponse(BaseModel):
    alert_id: str
    rule_id: str
    status: str             # triggered, suppressed, cooldown, no_trigger
    channels_used: List[str]
    timestamp: str


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")  # ty:ignore[deprecated]
async def startup_event():
    global redis_client
    redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)
    await redis_client.ping()  # ty:ignore[invalid-await]
    await _load_default_rules()
    logger.info("Alerting service ready")


@app.on_event("shutdown")  # ty:ignore[deprecated]
async def shutdown_event():
    if redis_client:
        await redis_client.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    ok = redis_client is not None
    if ok:
        try:
            await redis_client.ping()  # ty:ignore[invalid-await, unresolved-attribute]
        except Exception:
            ok = False
    return {
        "status": "healthy" if ok else "unhealthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/metrics")
async def metrics():
    return generate_latest()


@app.post("/api/v1/alerts/trigger", response_model=AlertResponse)
async def trigger_alert(req: AlertTrigger, bg: BackgroundTasks):
    """Evaluate a value against a rule and dispatch alerts if triggered."""

    if req.rule_id not in alert_rules:
        raise HTTPException(404, detail=f"Rule '{req.rule_id}' not found")

    rule = alert_rules[req.rule_id]
    now_iso = datetime.now(timezone.utc).isoformat()
    ts = int(datetime.now(timezone.utc).timestamp())

    if not rule.enabled:
        return AlertResponse(
            alert_id=f"suppressed_{req.rule_id}_{ts}",
            rule_id=req.rule_id, status="suppressed",
            channels_used=[], timestamp=now_iso,
        )

    if await _in_cooldown(req.rule_id, rule.cooldown_minutes):
        return AlertResponse(
            alert_id=f"cooldown_{req.rule_id}_{ts}",
            rule_id=req.rule_id, status="cooldown",
            channels_used=[], timestamp=now_iso,
        )

    if not _evaluate(req.value, rule.condition, rule.threshold):
        return AlertResponse(
            alert_id=f"no_trigger_{req.rule_id}_{ts}",
            rule_id=req.rule_id, status="no_trigger",
            channels_used=[], timestamp=now_iso,
        )

    alert_id = f"alert_{req.rule_id}_{ts}"
    alert_data = {
        "alert_id": alert_id,
        "rule_id": req.rule_id,
        "player_id": req.player_id,
        "value": req.value,
        "threshold": rule.threshold,
        "severity": rule.severity,
        "status": "triggered",
        "channels": rule.channels,
        "context": req.context or {},
        "source": req.source,
        "timestamp": now_iso,
    }

    await _store_alert(alert_data)
    bg.add_task(_send_notifications, alert_data, rule)

    return AlertResponse(
        alert_id=alert_id,
        rule_id=req.rule_id,
        status="triggered",
        channels_used=rule.channels,
        timestamp=now_iso,
    )


@app.get("/api/v1/alerts/rules")
async def list_rules():
    return {
        "rules": [
            {"rule_id": r.rule_id, "name": r.name, "severity": r.severity,
             "enabled": r.enabled, "channels": r.channels}
            for r in alert_rules.values()
        ],
        "total": len(alert_rules),
    }


@app.post("/api/v1/alerts/rules")
async def create_rule(rule: AlertRule):
    if rule.rule_id in alert_rules:
        raise HTTPException(409, detail=f"Rule '{rule.rule_id}' exists")
    alert_rules[rule.rule_id] = rule
    return {"status": "created", "rule_id": rule.rule_id}


@app.get("/api/v1/alerts/history")
async def get_history(limit: int = 100, severity: Optional[str] = None, hours: int = 24):
    if not redis_client:
        return {"alerts": [], "total": 0}

    raw = await redis_client.lrange("alert_history", 0, limit - 1)  # ty:ignore[invalid-await]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    alerts = []
    for item in raw:
        a = json.loads(item)
        if datetime.fromisoformat(a["timestamp"]) < cutoff:
            continue
        if severity and a.get("severity") != severity:
            continue
        alerts.append(a)
    return {"alerts": alerts[:limit], "total": len(alerts)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _load_default_rules():
    defaults = [
        AlertRule(
            rule_id="high_fraud_score", name="High Fraud Score",
            description="Fraud score exceeds threshold",
            condition=">", threshold=0.8, severity="critical",
            channels=["email", "slack"], cooldown_minutes=30,
        ),
        AlertRule(
            rule_id="unusual_velocity", name="Unusual Transaction Velocity",
            description="High transaction frequency detected",
            condition=">", threshold=10, severity="high",
            channels=["email"], cooldown_minutes=60,
        ),
        AlertRule(
            rule_id="large_amount", name="Large Transaction Amount",
            description="Transaction amount exceeds limit",
            condition=">", threshold=5000, severity="medium",
            channels=["email"], cooldown_minutes=120,
        ),
        AlertRule(
            rule_id="high_error_rate", name="High System Error Rate",
            description="System error rate above tolerance",
            condition=">", threshold=0.05, severity="high",
            channels=["email", "slack"], cooldown_minutes=15,
        ),
        AlertRule(
            rule_id="model_drift", name="Model Performance Drop",
            description="Model accuracy dropped significantly",
            condition="<", threshold=0.8, severity="medium",
            channels=["email"], cooldown_minutes=1440,
        ),
    ]
    for rule in defaults:
        alert_rules[rule.rule_id] = rule
    logger.info(f"Loaded {len(defaults)} default alert rules")


async def _in_cooldown(rule_id: str, minutes: int) -> bool:
    if not redis_client:
        return False
    last = await redis_client.get(f"alert_cooldown:{rule_id}")
    if last:
        end = datetime.fromisoformat(last) + timedelta(minutes=minutes)
        if datetime.now(timezone.utc) < end:
            return True
    return False


def _evaluate(value: float, condition: str, threshold: float) -> bool:
    ops = {">": value > threshold, ">=": value >= threshold,
           "<": value < threshold, "<=": value <= threshold,
           "==": abs(value - threshold) < 0.001}
    return ops.get(condition, False)


async def _store_alert(data: Dict[str, Any]):
    if not redis_client:
        return
    key = f"alert:{data['alert_id']}"
    await redis_client.set(key, json.dumps(data))
    await redis_client.lpush("alert_history", json.dumps(data))  # ty:ignore[invalid-await]
    await redis_client.ltrim("alert_history", 0, 999)  # ty:ignore[invalid-await]
    await redis_client.expire(key, 30 * 86400)


async def _send_notifications(data: Dict[str, Any], rule: AlertRule):
    await redis_client.set(  # ty:ignore[unresolved-attribute]
        f"alert_cooldown:{data['rule_id']}",
        datetime.now(timezone.utc).isoformat(),
    )
    await redis_client.expire(f"alert_cooldown:{data['rule_id']}", 86400)  # ty:ignore[unresolved-attribute]

    for ch in rule.channels:
        try:
            if ch == "email":
                await _send_email(data, rule)
            elif ch == "slack":
                await _send_slack(data, rule)
            elif ch == "sms":
                logger.info("SMS alert placeholder", alert_id=data["alert_id"])
            elif ch == "webhook":
                await _send_webhook(data, rule)
        except Exception as e:
            logger.error(f"Failed to send via {ch}", error=str(e))

    data["status"] = "sent"
    await _store_alert(data)


async def _send_email(data: Dict[str, Any], rule: AlertRule):
    msg = MIMEMultipart()
    msg["From"] = "alerts@fraud-detection.local"
    msg["To"] = "security-team@example.com"
    msg["Subject"] = f"Fraud Alert: {rule.name}"
    body = (
        f"Rule: {rule.name}\nSeverity: {rule.severity.upper()}\n"
        f"Value: {data['value']:.4f}  Threshold: {data['threshold']:.4f}\n"
        f"Player: {data.get('player_id', 'N/A')}\nTime: {data['timestamp']}"
    )
    msg.attach(MIMEText(body, "plain"))
    server = smtplib.SMTP("localhost", 1025)  # MailHog for dev
    server.send_message(msg)
    server.quit()


async def _send_slack(data: Dict[str, Any], rule: AlertRule):
    webhook = "https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK"
    payload = {
        "text": f"Fraud Alert: {rule.name}",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": rule.name}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Severity:* {rule.severity.upper()}"},
                {"type": "mrkdwn", "text": f"*Value:* {data['value']:.4f}"},
                {"type": "mrkdwn", "text": f"*Player:* {data.get('player_id', 'N/A')}"},
            ]},
        ],
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(webhook, json=payload) as r:
            if r.status != 200:
                raise Exception(f"Slack webhook returned {r.status}")


async def _send_webhook(data: Dict[str, Any], rule: AlertRule):
    url = "https://your-webhook-endpoint.example.com/alerts"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json={"alert": data, "rule": rule.dict()}) as r:  # ty:ignore[deprecated]
            if r.status not in (200, 201, 202):
                raise Exception(f"Webhook returned {r.status}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("alerting_service:app", host="0.0.0.0", port=8083, reload=True)
