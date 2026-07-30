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
Alerting Service for Fraud Detection

This service implements real-time alerting for fraud detection based on
model predictions, system metrics, and business rules.
"""

import os
import asyncio
import json
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, List, Optional
import structlog

import aiohttp
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import redis.asyncio as redis

from src.data_ingestion.metrics import MetricsCollector

logger = structlog.get_logger(__name__)

# Initialize FastAPI app

# Browser origins allowed to call this service. A wildcard combined with
# allow_credentials lets any site read authenticated responses, so the
# origins have to be named.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app = FastAPI(
    title="Fraud Detection - Alerting Service",
    description="Real-time alerting system for fraud detection",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize components
metrics_collector = MetricsCollector()

# Global variables for async components
redis_client: Optional[redis.Redis] = None
alert_rules = {}


class AlertRule(BaseModel):
    """Alert rule configuration"""

    rule_id: str
    name: str
    description: str
    condition: str
    threshold: float
    severity: str  # critical, high, medium, low
    channels: List[str]  # email, slack, sms, webhook
    cooldown_minutes: int = 60
    enabled: bool = True


class AlertTrigger(BaseModel):
    """Alert trigger request"""

    rule_id: str
    player_id: Optional[str] = None
    value: float
    context: Optional[Dict[str, Any]] = None
    source: str = "system"


class AlertResponse(BaseModel):
    """Alert response"""

    alert_id: str
    rule_id: str
    status: str  # triggered, suppressed, sent
    channels_used: List[str]
    timestamp: str


class AlertHistory(BaseModel):
    """Alert history entry"""

    alert_id: str
    rule_id: str
    player_id: Optional[str]
    value: float
    threshold: float
    severity: str
    status: str
    channels: List[str]
    context: Optional[Dict[str, Any]]
    timestamp: str


@app.on_event("startup")  # ty:ignore[deprecated]
async def startup_event():
    """Initialize async components on startup"""

    global redis_client, alert_rules

    try:
        # Initialize Redis client
        redis_client = redis.Redis(
            host="redis",
            port=6379,
            max_connections=10,
            decode_responses=True
        )

        # Test Redis connection
        await redis_client.ping()  # ty:ignore[invalid-await]
        logger.info("Redis connection established")

        # Load default alert rules
        await load_default_alert_rules()

    except Exception as e:
        logger.error("Failed to initialize alerting service", error=str(e))
        raise


@app.on_event("shutdown")  # ty:ignore[deprecated]
async def shutdown_event():
    """Clean up async components on shutdown"""

    global redis_client

    if redis_client:
        await redis_client.close()
        logger.info("Redis connection closed")


@app.get("/health")
async def health_check():
    """Health check endpoint"""

    global redis_client

    redis_healthy = redis_client is not None

    if redis_healthy:
        try:
            await redis_client.ping()  # ty:ignore[invalid-await, unresolved-attribute]
        except Exception: 
            redis_healthy = False

    status = "healthy" if redis_healthy else "unhealthy"

    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {
            "redis": "healthy" if redis_healthy else "unhealthy"
        }
    }


@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""

    global redis_client, alert_rules

    ready = redis_client is not None and len(alert_rules) > 0

    return {
        "status": "ready" if ready else "not ready",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus metrics endpoint"""

    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return generate_latest(), {"Content-Type": CONTENT_TYPE_LATEST}


@app.post("/api/v1/alerts/trigger", response_model=AlertResponse)
async def trigger_alert(request: AlertTrigger, background_tasks: BackgroundTasks):
    """Trigger an alert based on a rule"""

    try:
        # Validate rule exists
        if request.rule_id not in alert_rules:
            raise HTTPException(status_code=404, detail=f"Alert rule '{request.rule_id}' not found")

        rule = alert_rules[request.rule_id]

        if not rule.enabled:
            return AlertResponse(
                alert_id=f"suppressed_{request.rule_id}_{int(datetime.now(timezone.utc).timestamp())}",
                rule_id=request.rule_id,
                status="suppressed",
                channels_used=[],
                timestamp=datetime.now(timezone.utc).isoformat()
            )

        # Check cooldown period
        if await check_alert_cooldown(request.rule_id, rule.cooldown_minutes):
            return AlertResponse(
                alert_id=f"cooldown_{request.rule_id}_{int(datetime.now(timezone.utc).timestamp())}",
                rule_id=request.rule_id,
                status="cooldown",
                channels_used=[],
                timestamp=datetime.now(timezone.utc).isoformat()
            )

        # Evaluate condition
        if not evaluate_alert_condition(request.value, rule.condition, rule.threshold):
            return AlertResponse(
                alert_id=f"no_trigger_{request.rule_id}_{int(datetime.now(timezone.utc).timestamp())}",
                rule_id=request.rule_id,
                status="no_trigger",
                channels_used=[],
                timestamp=datetime.now(timezone.utc).isoformat()
            )

        # Create alert
        alert_id = f"alert_{request.rule_id}_{int(datetime.now(timezone.utc).timestamp())}"
        alert_timestamp = datetime.now(timezone.utc).isoformat()

        alert_data: Dict[str, Any] = {
            "alert_id": alert_id,
            "rule_id": request.rule_id,
            "player_id": request.player_id,
            "value": request.value,
            "threshold": rule.threshold,
            "severity": rule.severity,
            "status": "triggered",
            "channels": rule.channels,
            "context": request.context or {},
            "source": request.source,
            "timestamp": alert_timestamp
        }

        # Store alert in Redis
        await store_alert(alert_data)

        # Send alert asynchronously
        background_tasks.add_task(send_alert_notifications, alert_data, rule)

        # Update metrics
        metrics_collector.increment_counter("alerts_triggered_total", {"severity": rule.severity})

        return AlertResponse(
            alert_id=alert_id,
            rule_id=request.rule_id,
            status="triggered",
            channels_used=rule.channels,
            timestamp=alert_timestamp
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error triggering alert", error=str(e), rule_id=request.rule_id)
        metrics_collector.increment_counter("alert_trigger_errors_total", {"error_type": "processing"})
        raise HTTPException(status_code=500, detail=f"Alert trigger failed: {str(e)}")


@app.get("/api/v1/alerts/rules")
async def list_alert_rules():
    """List all alert rules"""

    global alert_rules

    return {
        "rules": [
            {
                "rule_id": rule.rule_id,
                "name": rule.name,
                "description": rule.description,
                "severity": rule.severity,
                "enabled": rule.enabled,
                "channels": rule.channels
            }
            for rule in alert_rules.values()
        ],
        "total": len(alert_rules)
    }


@app.get("/api/v1/alerts/history")
async def get_alert_history(
    limit: int = 100,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    hours: int = 24
):
    """Get alert history"""

    try:
        alerts = await get_alerts_from_history(limit, severity, status, hours)

        return {
            "alerts": alerts,
            "total": len(alerts),
            "filter": {
                "severity": severity,
                "status": status,
                "hours": hours,
                "limit": limit
            }
        }

    except Exception as e:
        logger.error("Error retrieving alert history", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve alert history")


@app.post("/api/v1/alerts/rules")
async def create_alert_rule(rule: AlertRule):
    """Create a new alert rule"""

    global alert_rules

    if rule.rule_id in alert_rules:
        raise HTTPException(status_code=409, detail=f"Alert rule '{rule.rule_id}' already exists")

    alert_rules[rule.rule_id] = rule

    # Store in Redis for persistence
    await store_alert_rule(rule)

    return {
        "status": "created",
        "rule_id": rule.rule_id,
        "message": f"Alert rule '{rule.rule_id}' created successfully"
    }


@app.put("/api/v1/alerts/rules/{rule_id}")
async def update_alert_rule(rule_id: str, rule: AlertRule):
    """Update an existing alert rule"""

    global alert_rules

    if rule_id not in alert_rules:
        raise HTTPException(status_code=404, detail=f"Alert rule '{rule_id}' not found")

    if rule.rule_id != rule_id:
        raise HTTPException(status_code=400, detail="Rule ID mismatch")

    alert_rules[rule_id] = rule
    await store_alert_rule(rule)

    return {
        "status": "updated",
        "rule_id": rule_id,
        "message": f"Alert rule '{rule_id}' updated successfully"
    }


@app.delete("/api/v1/alerts/rules/{rule_id}")
async def delete_alert_rule(rule_id: str):
    """Delete an alert rule"""

    global alert_rules

    if rule_id not in alert_rules:
        raise HTTPException(status_code=404, detail=f"Alert rule '{rule_id}' not found")

    del alert_rules[rule_id]

    # Remove from Redis
    global redis_client
    if redis_client:
        await redis_client.delete(f"alert_rule:{rule_id}")

    return {
        "status": "deleted",
        "rule_id": rule_id,
        "message": f"Alert rule '{rule_id}' deleted successfully"
    }


async def load_default_alert_rules():
    """Load default alert rules"""

    global alert_rules

    default_rules = [
        AlertRule(
            rule_id="high_fraud_score",
            name="High Fraud Score",
            description="Alert when fraud score exceeds threshold",
            condition=">",
            threshold=0.8,
            severity="critical",
            channels=["email", "slack"],
            cooldown_minutes=30
        ),
        AlertRule(
            rule_id="unusual_transaction_velocity",
            name="Unusual Transaction Velocity",
            description="Alert on high transaction frequency",
            condition=">",
            threshold=10,
            severity="high",
            channels=["email"],
            cooldown_minutes=60
        ),
        AlertRule(
            rule_id="large_transaction_amount",
            name="Large Transaction Amount",
            description="Alert on unusually large transactions",
            condition=">",
            threshold=5000,
            severity="medium",
            channels=["email"],
            cooldown_minutes=120
        ),
        AlertRule(
            rule_id="system_high_error_rate",
            name="High System Error Rate",
            description="Alert when system error rate is too high",
            condition=">",
            threshold=0.05,
            severity="high",
            channels=["email", "slack"],
            cooldown_minutes=15
        ),
        AlertRule(
            rule_id="model_performance_drop",
            name="Model Performance Drop",
            description="Alert when model accuracy drops significantly",
            condition="<",
            threshold=0.8,
            severity="medium",
            channels=["email"],
            cooldown_minutes=1440  # 24 hours
        )
    ]

    for rule in default_rules:
        alert_rules[rule.rule_id] = rule
        await store_alert_rule(rule)

    logger.info(f"Loaded {len(default_rules)} default alert rules")


async def check_alert_cooldown(rule_id: str, cooldown_minutes: int) -> bool:
    """Check if alert is in cooldown period"""

    global redis_client

    if not redis_client:
        return False

    cooldown_key = f"alert_cooldown:{rule_id}"
    last_alert_time = await redis_client.get(cooldown_key)

    if last_alert_time:
        last_time = datetime.fromisoformat(last_alert_time)
        cooldown_end = last_time + timedelta(minutes=cooldown_minutes)

        if datetime.now(timezone.utc) < cooldown_end:
            return True  # Still in cooldown

    return False


def evaluate_alert_condition(value: float, condition: str, threshold: float) -> bool:
    """Evaluate alert condition"""

    if condition == ">":
        return value > threshold
    elif condition == ">=":
        return value >= threshold
    elif condition == "<":
        return value < threshold
    elif condition == "<=":
        return value <= threshold
    elif condition == "==":
        return abs(value - threshold) < 0.001  # Approximate equality
    else:
        logger.warning(f"Unknown condition: {condition}")
        return False


async def send_alert_notifications(alert_data: Dict[str, Any], rule: AlertRule):
    """Send alert notifications to configured channels"""

    try:
        # Update cooldown timestamp
        await update_alert_cooldown(alert_data["rule_id"])

        # Send to each configured channel
        for channel in rule.channels:
            try:
                if channel == "email":
                    await send_email_alert(alert_data, rule)
                elif channel == "slack":
                    await send_slack_alert(alert_data, rule)
                elif channel == "sms":
                    await send_sms_alert(alert_data, rule)
                elif channel == "webhook":
                    await send_webhook_alert(alert_data, rule)
                else:
                    logger.warning(f"Unknown alert channel: {channel}")

                metrics_collector.increment_counter("alerts_sent_total", {"channel": channel})

            except Exception as e:
                logger.error(f"Failed to send alert via {channel}", error=str(e))
                metrics_collector.increment_counter("alert_send_errors_total", {"channel": channel})

        # Mark alert as sent
        alert_data["status"] = "sent"
        await store_alert(alert_data)

    except Exception as e:
        logger.error("Error sending alert notifications", error=str(e))


async def send_email_alert(alert_data: Dict[str, Any], rule: AlertRule):
    """Send alert via email"""

    try:
        # Email configuration (should be from environment variables)
        smtp_server = "localhost"
        smtp_port = 1025  # MailHog for development
        from_email = "alerts@fraud-detection.local"
        to_emails = ["security@casino.local", "fraud-team@casino.local"]

        # Create message
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = ", ".join(to_emails)
        msg['Subject'] = f"Fraud Alert: {rule.name}"

        # Email body
        body = f"""
Fraud Detection Alert

Rule: {rule.name}
Severity: {rule.severity.upper()}
Description: {rule.description}

Details:
- Alert ID: {alert_data['alert_id']}
- Rule ID: {alert_data['rule_id']}
- Value: {alert_data['value']:.4f}
- Threshold: {alert_data['threshold']:.4f}
- Player ID: {alert_data.get('player_id', 'N/A')}
- Timestamp: {alert_data['timestamp']}

Context: {json.dumps(alert_data.get('context', {}), indent=2)}

This is an automated alert from the Fraud Detection System.
        """

        msg.attach(MIMEText(body, 'plain'))

        # Send email (using MailHog for development)
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.send_message(msg)
        server.quit()

        logger.info("Email alert sent", alert_id=alert_data['alert_id'], recipients=to_emails)

    except Exception as e:
        logger.error("Failed to send email alert", error=str(e))
        raise


async def send_slack_alert(alert_data: Dict[str, Any], rule: AlertRule):
    """Send alert via Slack webhook"""

    try:
        webhook_url = "https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK"  # Should be from config

        payload = {
            "text": f"🚨 Fraud Alert: {rule.name}",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🚨 {rule.name}"
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Severity:* {rule.severity.upper()}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Value:* {alert_data['value']:.4f}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Player:* {alert_data.get('player_id', 'N/A')}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Time:* {alert_data['timestamp']}"
                        }
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": rule.description
                    }
                }
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as response:
                if response.status != 200:
                    raise Exception(f"Slack webhook failed: {response.status}")

        logger.info("Slack alert sent", alert_id=alert_data['alert_id'])

    except Exception as e:
        logger.error("Failed to send Slack alert", error=str(e))
        raise


async def send_sms_alert(alert_data: Dict[str, Any], rule: AlertRule):
    """Send alert via SMS (placeholder implementation)"""

    # This would integrate with Twilio, Nexmo, etc.
    logger.info("SMS alert would be sent", alert_id=alert_data['alert_id'])
    # Implementation would use actual SMS service


async def send_webhook_alert(alert_data: Dict[str, Any], rule: AlertRule):
    """Send alert via webhook"""

    try:
        webhook_url = "https://your-webhook-endpoint.com/alerts"  # Should be from config

        payload = {
            "alert": alert_data,
            "rule": {
                "rule_id": rule.rule_id,
                "name": rule.name,
                "severity": rule.severity
            }
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as response:
                if response.status not in [200, 201, 202]:
                    raise Exception(f"Webhook failed: {response.status}")

        logger.info("Webhook alert sent", alert_id=alert_data['alert_id'])

    except Exception as e:
        logger.error("Failed to send webhook alert", error=str(e))
        raise


async def store_alert(alert_data: Dict[str, Any]):
    """Store alert in Redis"""

    global redis_client

    if not redis_client:
        return

    try:
        alert_key = f"alert:{alert_data['alert_id']}"
        await redis_client.set(alert_key, json.dumps(alert_data))

        # Add to history list
        history_key = "alert_history"
        await redis_client.lpush(history_key, json.dumps(alert_data))  # ty:ignore[invalid-await]

        # Keep only last 1000 alerts
        await redis_client.ltrim(history_key, 0, 999)  # ty:ignore[invalid-await]

        # Set expiration (30 days)
        await redis_client.expire(alert_key, 30 * 24 * 60 * 60)

    except Exception as e:
        logger.error("Failed to store alert", error=str(e))


async def store_alert_rule(rule: AlertRule):
    """Store alert rule in Redis"""

    global redis_client

    if not redis_client:
        return

    try:
        rule_key = f"alert_rule:{rule.rule_id}"
        rule_data = {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "description": rule.description,
            "condition": rule.condition,
            "threshold": rule.threshold,
            "severity": rule.severity,
            "channels": rule.channels,
            "cooldown_minutes": rule.cooldown_minutes,
            "enabled": rule.enabled
        }

        await redis_client.set(rule_key, json.dumps(rule_data))

    except Exception as e:
        logger.error("Failed to store alert rule", error=str(e))


async def update_alert_cooldown(rule_id: str):
    """Update alert cooldown timestamp"""

    global redis_client

    if not redis_client:
        return

    try:
        cooldown_key = f"alert_cooldown:{rule_id}"
        await redis_client.set(cooldown_key, datetime.now(timezone.utc).isoformat())

        # Set expiration (longer than max cooldown)
        await redis_client.expire(cooldown_key, 24 * 60 * 60)  # 24 hours

    except Exception as e:
        logger.error("Failed to update alert cooldown", error=str(e))


async def get_alerts_from_history(
    limit: int,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    hours: int = 24
) -> List[Dict[str, Any]]:
    """Get alerts from history with filtering"""

    global redis_client

    if not redis_client:
        return []

    try:
        history_key = "alert_history"
        alerts_json = await redis_client.lrange(history_key, 0, limit - 1)  # ty:ignore[invalid-await]

        alerts = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)

        for alert_json in alerts_json:
            try:
                alert = json.loads(alert_json)
                alert_time = datetime.fromisoformat(alert["timestamp"])

                # Apply filters
                if alert_time < cutoff_time:
                    continue

                if severity and alert.get("severity") != severity:
                    continue

                if status and alert.get("status") != status:
                    continue

                alerts.append(alert)

            except Exception: 
                continue

        return alerts[:limit]

    except Exception as e:
        logger.error("Failed to get alerts from history", error=str(e))
        return []


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8083,
        reload=True
    )