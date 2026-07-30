# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""alert-bot.py — Casino Platform On-Call Alert Bot

Monitors: Prometheus/Alertmanager, GitLab CI, DefectDojo, Falco, Wazuh, SSL
Classifies: P0 / P1 / P2 / P3
Notifies:   Dashboard (your-dashboard.example.com), Slack, Email, Phone (Twilio)
Escalates:  Auto-escalation on SLA breach via background scheduler
State:      Redis

Usage:
    uv run alert-bot.py
    # or
    uvicorn alert-bot:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import smtplib
import ssl as ssl_lib
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Any, AsyncIterator

import httpx
import redis.asyncio as aioredis
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("alert-bot")

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

CONFIG_PATH = os.environ.get("ALERT_CONFIG_PATH", "/app/alert-config.yaml")


def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} placeholders in config values."""
    if isinstance(value, str):
        import re
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(i) for i in value]
    return value


def load_config() -> dict[str, Any]:
    """Load and expand environment variables in alert-config.yaml."""
    with open(CONFIG_PATH) as f:
        raw = yaml.safe_load(f)
    return _expand_env(raw)


CONFIG: dict[str, Any] = {}

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class AlertStatus(str, Enum):
    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class Alert(BaseModel):
    """Represents a classified alert tracked in Redis."""

    alert_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    fingerprint: str
    rule_id: str
    severity: Severity
    source: str
    title: str
    description: str
    labels: dict[str, str] = Field(default_factory=dict)
    status: AlertStatus = AlertStatus.FIRING
    fired_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_at: datetime | None = None
    escalated_at: datetime | None = None
    escalation_count: int = 0
    notification_sent: bool = False
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(
        json_encoders={datetime: lambda v: v.isoformat()},
    )


class AckRequest(BaseModel):
    acknowledged_by: str = Field(..., description="Name or Slack handle of the engineer acknowledging")


class AlertSummary(BaseModel):
    alert_id: str
    severity: Severity
    title: str
    status: AlertStatus
    fired_at: datetime
    acknowledged_at: datetime | None = None


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

REDIS_CLIENT: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return the shared async Redis client, initialising if needed."""
    global REDIS_CLIENT
    if REDIS_CLIENT is None:
        cfg = CONFIG.get("redis", {})
        REDIS_CLIENT = aioredis.Redis(
            host=cfg.get("host", "localhost"),
            port=int(cfg.get("port", 6379)),
            db=int(cfg.get("db", 0)),
            password=cfg.get("password") or None,
            decode_responses=True,
        )
    return REDIS_CLIENT


def _alert_key(alert_id: str) -> str:
    prefix = CONFIG.get("redis", {}).get("key_prefix", "alert_bot:")
    return f"{prefix}alert:{alert_id}"


def _fingerprint_key(fingerprint: str) -> str:
    prefix = CONFIG.get("redis", {}).get("key_prefix", "alert_bot:")
    return f"{prefix}fp:{fingerprint}"


async def save_alert(alert: Alert) -> None:
    """Persist alert to Redis as JSON."""
    r = await get_redis()
    ttl_days = int(CONFIG.get("redis", {}).get("alert_ttl_days", 30))
    data = alert.model_dump_json()
    await r.set(_alert_key(alert.alert_id), data, ex=ttl_days * 86400)
    # fingerprint → alert_id mapping (for dedup)
    await r.set(_fingerprint_key(alert.fingerprint), alert.alert_id, ex=ttl_days * 86400)


async def load_alert(alert_id: str) -> Alert | None:
    """Load alert from Redis by ID."""
    r = await get_redis()
    raw = await r.get(_alert_key(alert_id))
    if raw is None:
        return None
    return Alert.model_validate_json(raw)


async def find_alert_by_fingerprint(fingerprint: str) -> Alert | None:
    """Return an existing firing alert with the same fingerprint, if any."""
    r = await get_redis()
    alert_id = await r.get(_fingerprint_key(fingerprint))
    if alert_id is None:
        return None
    return await load_alert(alert_id)


async def list_firing_alerts() -> list[Alert]:
    """Return all alerts currently in FIRING or ESCALATED state."""
    r = await get_redis()
    prefix = CONFIG.get("redis", {}).get("key_prefix", "alert_bot:")
    keys = await r.keys(f"{prefix}alert:*")
    alerts: list[Alert] = []
    for key in keys:
        raw = await r.get(key)
        if raw:
            try:
                alert = Alert.model_validate_json(raw)
                if alert.status in (AlertStatus.FIRING, AlertStatus.ESCALATED):
                    alerts.append(alert)
            except Exception:
                pass
    return alerts


# ---------------------------------------------------------------------------
# Alert classification
# ---------------------------------------------------------------------------


def _compute_fingerprint(rule_id: str, labels: dict[str, str]) -> str:
    """Deterministic fingerprint for deduplication."""
    payload = json.dumps({"rule_id": rule_id, "labels": labels}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def classify_prometheus_alert(payload: dict[str, Any]) -> list[Alert]:
    """Parse Alertmanager webhook payload into Alert objects."""
    alerts: list[Alert] = []
    rules = CONFIG.get("classification_rules", [])
    for raw_alert in payload.get("alerts", []):
        alert_name = raw_alert.get("labels", {}).get("alertname", "")
        matched_rule: dict[str, Any] | None = None
        for rule in rules:
            if rule.get("source") != "prometheus":
                continue
            cond = rule.get("condition", {})
            if "alert_name" in cond and cond["alert_name"] == alert_name:
                matched_rule = rule
                break
            if "alert_name_contains_any" in cond:
                if any(s in alert_name for s in cond["alert_name_contains_any"]):
                    matched_rule = rule
                    break
        if matched_rule is None:
            continue
        labels = raw_alert.get("labels", {})
        fp = _compute_fingerprint(matched_rule["id"], labels)
        alert = Alert(
            fingerprint=fp,
            rule_id=matched_rule["id"],
            severity=Severity(matched_rule["severity"]),
            source="prometheus",
            title=f"[{matched_rule['severity']}] {matched_rule['description']}",
            description=matched_rule.get("description", alert_name),
            labels=labels,
            raw_payload=raw_alert,
        )
        alerts.append(alert)
    return alerts


def classify_falco_alert(payload: dict[str, Any]) -> Alert | None:
    """Classify a Falco webhook event into an Alert."""
    priority = payload.get("priority", "").upper()
    rule_name = payload.get("rule", "")
    output = payload.get("output", "")

    rules = CONFIG.get("classification_rules", [])
    for rule in rules:
        if rule.get("source") != "falco":
            continue
        cond = rule.get("condition", {})
        if cond.get("priority", "").upper() != priority:
            continue
        if "rule_contains_any" in cond:
            if not any(s in rule_name or s in output for s in cond["rule_contains_any"]):
                continue
        labels = {"falco_rule": rule_name, "priority": priority}
        fp = _compute_fingerprint(rule["id"], labels)
        return Alert(
            fingerprint=fp,
            rule_id=rule["id"],
            severity=Severity(rule["severity"]),
            source="falco",
            title=f"[{rule['severity']}] Falco: {rule_name}",
            description=rule.get("description", output[:200]),
            labels=labels,
            raw_payload=payload,
        )
    return None


def classify_wazuh_alert(payload: dict[str, Any]) -> Alert | None:
    """Classify a Wazuh alert by rule level."""
    rule_level = int(payload.get("rule", {}).get("level", 0))
    rule_id_wazuh = str(payload.get("rule", {}).get("id", ""))
    description = payload.get("rule", {}).get("description", "Wazuh alert")

    rules = CONFIG.get("classification_rules", [])
    for rule in rules:
        if rule.get("source") != "wazuh":
            continue
        cond = rule.get("condition", {})
        gte = cond.get("rule_level_gte", 0)
        lt = cond.get("rule_level_lt", 999)
        if gte <= rule_level < lt:
            labels = {"wazuh_rule_id": rule_id_wazuh, "level": str(rule_level)}
            fp = _compute_fingerprint(rule["id"], labels)
            return Alert(
                fingerprint=fp,
                rule_id=rule["id"],
                severity=Severity(rule["severity"]),
                source="wazuh",
                title=f"[{rule['severity']}] Wazuh: {description}",
                description=description,
                labels=labels,
                raw_payload=payload,
            )
    return None


def classify_ssl_alert(domain: str, days_remaining: int) -> Alert | None:
    """Classify an SSL expiry finding."""
    rules = CONFIG.get("classification_rules", [])
    for rule in rules:
        if rule.get("source") != "ssl":
            continue
        cond = rule.get("condition", {})
        max_days = cond.get("days_until_expiry", None)
        lte = cond.get("days_until_expiry_lte", None)
        gt = cond.get("days_until_expiry_gt", None)
        matched = False
        if max_days is not None and days_remaining <= max_days:
            matched = True
        if lte is not None and gt is not None and gt < days_remaining <= lte:
            matched = True
        if matched:
            labels = {"domain": domain, "days_remaining": str(days_remaining)}
            fp = _compute_fingerprint(rule["id"], labels)
            return Alert(
                fingerprint=fp,
                rule_id=rule["id"],
                severity=Severity(rule["severity"]),
                source="ssl",
                title=f"[{rule['severity']}] SSL cert expiry: {domain} ({days_remaining}d remaining)",
                description=rule.get("description", f"Certificate for {domain} expires in {days_remaining} days"),
                labels=labels,
                raw_payload={"domain": domain, "days_remaining": days_remaining},
            )
    return None


# ---------------------------------------------------------------------------
# Notification dispatch
# ---------------------------------------------------------------------------


async def send_slack(alert: Alert) -> None:
    """Send a Slack webhook notification."""
    cfg = CONFIG.get("notifications", {}).get("slack", {})
    if not cfg.get("enabled", False):
        return
    webhook_url = cfg.get("webhook_url", "")
    if not webhook_url or webhook_url.startswith("${"):
        logger.warning("Slack webhook URL not configured; skipping")
        return
    channels = cfg.get("channels", {})
    channel = channels.get(alert.severity.value, "#incidents-p3")
    mentions = cfg.get("mention", {}).get(alert.severity.value, "")
    color_map = {"P0": "#FF0000", "P1": "#FF8C00", "P2": "#FFD700", "P3": "#36A64F"}
    color = color_map.get(alert.severity.value, "#808080")
    body = {
        "channel": channel,
        "attachments": [
            {
                "color": color,
                "title": alert.title,
                "text": f"{mentions}\n{alert.description}",
                "fields": [
                    {"title": "Severity", "value": alert.severity.value, "short": True},
                    {"title": "Source", "value": alert.source, "short": True},
                    {"title": "Alert ID", "value": alert.alert_id, "short": True},
                    {"title": "Fired At", "value": alert.fired_at.strftime("%Y-%m-%d %H:%M UTC"), "short": True},
                ],
                "footer": "Acme Casino Alert Bot",
                "ts": int(alert.fired_at.timestamp()),
            }
        ],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(webhook_url, json=body)
        if resp.status_code != 200:
            logger.error("Slack notification failed: %s", resp.text)


async def send_dashboard(alert: Alert) -> None:
    """POST alert to the configured dashboard webhook."""
    cfg = CONFIG.get("notifications", {}).get("dashboard", {})
    if not cfg.get("enabled", False):
        return
    url = cfg.get("url", "")
    token = cfg.get("token", "")
    if not url or url.startswith("${"):
        logger.warning("Dashboard webhook URL not configured; skipping")
        return
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "alert_id": alert.alert_id,
        "severity": alert.severity.value,
        "title": alert.title,
        "description": alert.description,
        "source": alert.source,
        "status": alert.status.value,
        "fired_at": alert.fired_at.isoformat(),
        "labels": alert.labels,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code >= 400:
                logger.error("Dashboard notification failed (%s): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Dashboard notification error: %s", exc)


def send_email_sync(alert: Alert, recipients: list[str]) -> None:
    """Send email notification (synchronous; run in thread pool)."""
    cfg = CONFIG.get("notifications", {}).get("email", {})
    smtp_host = cfg.get("smtp_host", "")
    smtp_port = int(cfg.get("smtp_port", 587))
    smtp_user = cfg.get("smtp_user", "")
    smtp_pass = cfg.get("smtp_password", "")
    from_addr = cfg.get("from_address", "alerts@acmetocasino.com")
    if not smtp_host or smtp_host.startswith("${"):
        logger.warning("SMTP not configured; skipping email")
        return
    subject = f"[{alert.severity.value}] {alert.title}"
    body_text = (
        f"Alert ID: {alert.alert_id}\n"
        f"Severity: {alert.severity.value}\n"
        f"Source: {alert.source}\n"
        f"Fired At: {alert.fired_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"{alert.description}\n\n"
        f"Labels: {json.dumps(alert.labels, indent=2)}\n\n"
        f"Ack URL: https://alert-bot.internal/ack/{alert.alert_id}\n"
    )
    for recipient in recipients:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = recipient
        msg.attach(MIMEText(body_text, "plain"))
        try:
            context = ssl_lib.create_default_context()
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.ehlo()
                server.starttls(context=context)
                server.login(smtp_user, smtp_pass)
                server.sendmail(from_addr, recipient, msg.as_string())
        except Exception as exc:
            logger.error("Email to %s failed: %s", recipient, exc)


async def send_email(alert: Alert) -> None:
    """Async wrapper for email sending."""
    cfg = CONFIG.get("notifications", {}).get("email", {})
    if not cfg.get("enabled", False):
        return
    recipients = cfg.get("recipients", {}).get(alert.severity.value, [])
    if not recipients:
        return
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, send_email_sync, alert, recipients)


async def send_phone_sms(alert: Alert) -> None:
    """Send SMS (and optionally voice call) via Twilio for P0/P1."""
    cfg = CONFIG.get("notifications", {}).get("phone", {})
    if not cfg.get("enabled", False):
        return
    account_sid = cfg.get("account_sid", "")
    auth_token = cfg.get("auth_token", "")
    from_number = cfg.get("from_number", "")
    if not account_sid or account_sid.startswith("${"):
        logger.warning("Twilio not configured; skipping phone/SMS")
        return
    sms_recipients = cfg.get("sms_recipients", {}).get(alert.severity.value, [])
    call_recipients = cfg.get("call_recipients", {}).get(alert.severity.value, [])
    message_body = f"[{alert.severity.value}] {alert.title} — ACK: https://alert-bot.internal/ack/{alert.alert_id}"
    base_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
    auth = (account_sid, auth_token)
    async with httpx.AsyncClient(timeout=15, auth=auth) as client:
        for number in sms_recipients:
            try:
                await client.post(
                    f"{base_url}/Messages.json",
                    data={"From": from_number, "To": number, "Body": message_body},
                )
            except Exception as exc:
                logger.error("SMS to %s failed: %s", number, exc)
        for number in call_recipients:
            try:
                twiml_url = (
                    f"http://twimlets.com/message?Message="
                    f"Alert+{alert.severity.value}+{alert.title.replace(' ', '+')}"
                )
                await client.post(
                    f"{base_url}/Calls.json",
                    data={"From": from_number, "To": number, "Url": twiml_url},
                )
            except Exception as exc:
                logger.error("Phone call to %s failed: %s", number, exc)


async def dispatch_notifications(alert: Alert) -> None:
    """Send all configured notifications for an alert based on severity."""
    severity = alert.severity.value
    logger.info("Dispatching notifications for %s alert %s", severity, alert.alert_id)
    tasks = [send_dashboard(alert), send_slack(alert), send_email(alert)]
    if severity in ("P0", "P1"):
        tasks.append(send_phone_sms(alert))
    await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# SSL certificate checker
# ---------------------------------------------------------------------------


async def check_ssl_expiry() -> None:
    """Check TLS certificate expiry for all configured domains."""
    cfg_sources = CONFIG.get("sources", {}).get("ssl", {})
    domains = cfg_sources.get("domains", [])
    for domain in domains:
        try:
            days = await _get_ssl_days_remaining(domain)
            alert = classify_ssl_alert(domain, days)
            if alert:
                await process_new_alert(alert)
        except Exception as exc:
            logger.error("SSL check failed for %s: %s", domain, exc)


async def _get_ssl_days_remaining(domain: str) -> int:
    """Return days until SSL certificate expiry for a domain (synchronous socket call)."""
    import datetime as dt
    loop = asyncio.get_running_loop()

    def _check() -> int:
        context = ssl_lib.create_default_context()
        with socket.create_connection((domain, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert: dict[str, Any] = ssock.getpeercert() or {}  # type: ignore[assignment]
                expiry_str: str = str(cert.get("notAfter", ""))
                expiry = dt.datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                return (expiry - datetime.now(timezone.utc)).days

    return await loop.run_in_executor(None, _check)


# ---------------------------------------------------------------------------
# Prometheus / Alertmanager poller
# ---------------------------------------------------------------------------


async def poll_alertmanager() -> None:
    """Fetch firing alerts from Alertmanager and classify them."""
    url = CONFIG.get("sources", {}).get("prometheus", {}).get("alertmanager_url", "")
    if not url or url.startswith("${"):
        return
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{url}/api/v2/alerts?active=true&silenced=false&inhibited=false")
            if resp.status_code != 200:
                logger.warning("Alertmanager returned %s", resp.status_code)
                return
            raw_alerts = resp.json()
            fake_payload = {"alerts": raw_alerts}
            classified = classify_prometheus_alert(fake_payload)
            for alert in classified:
                await process_new_alert(alert)
        except Exception as exc:
            logger.error("Alertmanager poll failed: %s", exc)


# ---------------------------------------------------------------------------
# DefectDojo poller
# ---------------------------------------------------------------------------


async def poll_defectdojo() -> None:
    """Check DefectDojo for new findings and classify them."""
    cfg = CONFIG.get("sources", {}).get("defectdojo", {})
    url = cfg.get("url", "")
    token = cfg.get("token", "")
    if not url or url.startswith("${"):
        return
    headers = {"Authorization": f"Token {token}"}
    severities_to_check = ["Critical", "High", "Medium"]
    async with httpx.AsyncClient(timeout=15) as client:
        for sev in severities_to_check:
            try:
                resp = await client.get(
                    f"{url}/api/v2/findings/",
                    headers=headers,
                    params={
                        "severity": sev,
                        "active": True,
                        "verified": True,
                        "limit": 50,
                        "ordering": "-date",
                    },
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for finding in data.get("results", []):
                    _classify_defectdojo_finding(finding)
            except Exception as exc:
                logger.error("DefectDojo poll failed (severity=%s): %s", sev, exc)


def _classify_defectdojo_finding(finding: dict[str, Any]) -> Alert | None:
    """Map a DefectDojo finding to an Alert."""
    sev = finding.get("severity", "").title()
    title = finding.get("title", "Unknown finding")
    cve = finding.get("cve", "")
    description = finding.get("description", title)[:300]
    severity_map = {"Critical": Severity.P1, "High": Severity.P2, "Medium": Severity.P3, "Low": Severity.P3}
    if sev not in severity_map:
        return None
    alert_severity = severity_map[sev]
    # Critical in prod → P1; handled by p1_critical_cve_prod rule
    labels = {"dojo_severity": sev, "cve": cve, "finding_id": str(finding.get("id", ""))}
    fp = _compute_fingerprint(f"defectdojo_{sev}", labels)
    return Alert(
        fingerprint=fp,
        rule_id=f"defectdojo_{sev.lower()}",
        severity=alert_severity,
        source="defectdojo",
        title=f"[{alert_severity.value}] DefectDojo {sev}: {title}",
        description=description,
        labels=labels,
        raw_payload=finding,
    )


# ---------------------------------------------------------------------------
# GitLab CI poller
# ---------------------------------------------------------------------------


async def poll_gitlab() -> None:
    """Check GitLab pipeline status for watched branches."""
    cfg = CONFIG.get("sources", {}).get("gitlab", {})
    url = cfg.get("url", "")
    token = cfg.get("token", "")
    project_id = cfg.get("project_id", "")
    branches = cfg.get("watched_branches", ["main"])
    threshold_minutes = int(cfg.get("pipeline_blocked_threshold_minutes", 120))
    if not url or url.startswith("${"):
        return
    headers = {"PRIVATE-TOKEN": token}
    async with httpx.AsyncClient(timeout=15) as client:
        for branch in branches:
            try:
                resp = await client.get(
                    f"{url}/api/v4/projects/{project_id}/pipelines",
                    headers=headers,
                    params={"ref": branch, "status": "failed", "order_by": "updated_at", "per_page": 1},
                )
                if resp.status_code != 200:
                    continue
                pipelines = resp.json()
                if not pipelines:
                    continue
                pipeline = pipelines[0]
                updated_at_str = pipeline.get("updated_at", "")
                if not updated_at_str:
                    continue
                updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                age_minutes = (datetime.now(timezone.utc) - updated_at).total_seconds() / 60
                if age_minutes >= threshold_minutes:
                    labels = {"branch": branch, "pipeline_id": str(pipeline.get("id", ""))}
                    fp = _compute_fingerprint("p1_pipeline_blocked", labels)
                    severity = Severity.P1 if branch == "main" else Severity.P2
                    alert = Alert(
                        fingerprint=fp,
                        rule_id="p1_pipeline_blocked" if branch == "main" else "p2_staging_broken",
                        severity=severity,
                        source="gitlab",
                        title=f"[{severity.value}] CI pipeline blocked on {branch} for {int(age_minutes)}min",
                        description=f"GitLab pipeline on '{branch}' has been in failed state for {int(age_minutes)} minutes.",
                        labels=labels,
                        raw_payload=pipeline,
                    )
                    await process_new_alert(alert)
            except Exception as exc:
                logger.error("GitLab poll failed (branch=%s): %s", branch, exc)


# ---------------------------------------------------------------------------
# Core alert processing
# ---------------------------------------------------------------------------


async def process_new_alert(alert: Alert) -> None:
    """Deduplicate, persist, and notify for a new alert."""
    dedup_cfg = CONFIG.get("deduplication", {})
    window_minutes = int(dedup_cfg.get("window_minutes", 30))

    existing = await find_alert_by_fingerprint(alert.fingerprint)
    if existing and existing.status in (AlertStatus.FIRING, AlertStatus.ESCALATED):
        # Check flapping: if we've seen this alert fire+resolve multiple times, upgrade severity
        flapping_key = f"flap:{alert.fingerprint}"
        r = await get_redis()
        flap_count = await r.incr(flapping_key)
        await r.expire(flapping_key, window_minutes * 60)
        threshold = int(dedup_cfg.get("flapping_threshold", 3))
        if flap_count > threshold and existing.severity not in (Severity.P0, Severity.P1):
            logger.warning("Alert %s is flapping (%d times); upgrading severity", alert.fingerprint, flap_count)
            existing.severity = Severity.P1
            existing.title = f"[FLAPPING→P1] {existing.title}"
            await save_alert(existing)
            await dispatch_notifications(existing)
        logger.debug("Deduplicated alert %s (existing %s)", alert.fingerprint, existing.alert_id)
        return

    # New unique alert
    await save_alert(alert)
    logger.info("New alert: %s [%s] from %s", alert.alert_id, alert.severity.value, alert.source)
    await dispatch_notifications(alert)


async def escalate_overdue_alerts() -> None:
    """Check for unacknowledged alerts past their SLA and escalate."""
    firing_alerts = await list_firing_alerts()
    escalation_cfg = CONFIG.get("escalation", {})
    now = datetime.now(timezone.utc)
    for alert in firing_alerts:
        if alert.status == AlertStatus.ACKNOWLEDGED:
            continue
        sev_cfg = escalation_cfg.get(alert.severity.value, {})
        ack_timeout_min = sev_cfg.get("ack_timeout_minutes", None)
        if ack_timeout_min is None:
            continue
        age_minutes = (now - alert.fired_at).total_seconds() / 60
        if age_minutes >= ack_timeout_min:
            steps = sev_cfg.get("escalation_steps", [])
            for step in steps:
                delay = step.get("delay_minutes", 0)
                if age_minutes >= delay and alert.escalation_count < steps.index(step) + 1:
                    logger.warning(
                        "Escalating alert %s [%s] after %.0f minutes without ack",
                        alert.alert_id,
                        alert.severity.value,
                        age_minutes,
                    )
                    alert.status = AlertStatus.ESCALATED
                    alert.escalated_at = now
                    alert.escalation_count += 1
                    # Upgrade severity if configured
                    if step.get("action") == "upgrade_to_P0":
                        alert.severity = Severity.P0
                        alert.title = f"[ESCALATED→P0] {alert.title}"
                    await save_alert(alert)
                    await dispatch_notifications(alert)
                    break


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialise config, Redis, schedulers on startup; tear down on shutdown."""
    global CONFIG
    CONFIG = load_config()
    logger.info("Configuration loaded from %s", CONFIG_PATH)

    # Verify Redis connection
    r = await get_redis()
    await r.ping()
    logger.info("Redis connection OK")

    poll_cfg = CONFIG.get("polling", {})

    # Register polling jobs
    scheduler.add_job(poll_alertmanager, "interval", seconds=int(poll_cfg.get("prometheus", 60)), id="prometheus")
    scheduler.add_job(poll_gitlab, "interval", seconds=int(poll_cfg.get("gitlab_ci", 120)), id="gitlab")
    scheduler.add_job(poll_defectdojo, "interval", seconds=int(poll_cfg.get("defectdojo", 300)), id="defectdojo")
    scheduler.add_job(check_ssl_expiry, "interval", seconds=int(poll_cfg.get("ssl", 3600)), id="ssl")
    scheduler.add_job(escalate_overdue_alerts, "interval", seconds=60, id="escalation")

    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        if REDIS_CLIENT:
            await REDIS_CLIENT.aclose()


app = FastAPI(
    title="Casino Platform On-Call Alert Bot",
    description="Monitors, classifies, and escalates alerts for new.acmetocasino.com",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Webhook endpoints (for Falco, Wazuh, Alertmanager push mode)
# ---------------------------------------------------------------------------


@app.post("/webhook/falco", summary="Receive Falco runtime security alerts")
async def falco_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """Falco webhook receiver. Configure Falco to POST to this endpoint."""
    payload = await request.json()
    alert = classify_falco_alert(payload)
    if alert:
        background_tasks.add_task(process_new_alert, alert)
        return JSONResponse({"status": "queued", "severity": alert.severity.value})
    return JSONResponse({"status": "ignored"})


@app.post("/webhook/alertmanager", summary="Receive Alertmanager webhook notifications")
async def alertmanager_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """Alertmanager webhook receiver (alternative to polling)."""
    payload = await request.json()
    alerts = classify_prometheus_alert(payload)
    for alert in alerts:
        background_tasks.add_task(process_new_alert, alert)
    return JSONResponse({"status": "queued", "count": len(alerts)})


@app.post("/webhook/wazuh", summary="Receive Wazuh SIEM alerts")
async def wazuh_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """Wazuh webhook receiver. Configure Wazuh integration to POST here."""
    payload = await request.json()
    alert = classify_wazuh_alert(payload)
    if alert:
        background_tasks.add_task(process_new_alert, alert)
        return JSONResponse({"status": "queued", "severity": alert.severity.value})
    return JSONResponse({"status": "ignored"})


# ---------------------------------------------------------------------------
# Acknowledgment and management endpoints
# ---------------------------------------------------------------------------


@app.post("/ack/{alert_id}", summary="Acknowledge an alert")
async def acknowledge_alert(alert_id: str, body: AckRequest) -> JSONResponse:
    """
    Mark an alert as acknowledged.

    Engineers call this from the dashboard link or Slack button.
    Stops auto-escalation for the alert.
    """
    alert = await load_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    if alert.status == AlertStatus.RESOLVED:
        return JSONResponse({"status": "already_resolved", "alert_id": alert_id})
    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_at = datetime.now(timezone.utc)
    alert.acknowledged_by = body.acknowledged_by
    await save_alert(alert)
    logger.info("Alert %s acknowledged by %s", alert_id, body.acknowledged_by)
    # Notify dashboard of ack
    await send_dashboard(alert)
    return JSONResponse(
        {
            "status": "acknowledged",
            "alert_id": alert_id,
            "acknowledged_by": body.acknowledged_by,
            "acknowledged_at": alert.acknowledged_at.isoformat(),
        }
    )


@app.post("/resolve/{alert_id}", summary="Manually resolve an alert")
async def resolve_alert(alert_id: str, background_tasks: BackgroundTasks) -> JSONResponse:
    """Manually mark an alert as resolved (also sent automatically from Alertmanager)."""
    alert = await load_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = datetime.now(timezone.utc)
    await save_alert(alert)
    logger.info("Alert %s resolved", alert_id)
    background_tasks.add_task(send_dashboard, alert)
    background_tasks.add_task(send_slack, alert)
    return JSONResponse({"status": "resolved", "alert_id": alert_id})


@app.get("/alerts", summary="List all firing and escalated alerts", response_model=list[AlertSummary])
async def get_alerts() -> list[AlertSummary]:
    """Return a summary list of all currently active alerts."""
    alerts = await list_firing_alerts()
    return [
        AlertSummary(
            alert_id=a.alert_id,
            severity=a.severity,
            title=a.title,
            status=a.status,
            fired_at=a.fired_at,
            acknowledged_at=a.acknowledged_at,
        )
        for a in sorted(alerts, key=lambda x: (x.severity.value, x.fired_at))
    ]


@app.get("/alerts/{alert_id}", summary="Get full alert details")
async def get_alert(alert_id: str) -> Alert:
    """Return the full Alert object for a given ID."""
    alert = await load_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return alert


@app.get("/health", summary="Health check")
async def health() -> JSONResponse:
    """Liveness probe endpoint."""
    try:
        r = await get_redis()
        await r.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    status = "ok" if redis_ok else "degraded"
    return JSONResponse({"status": status, "redis": redis_ok, "scheduler": scheduler.running})


@app.get("/oncall", summary="Current on-call engineer")
async def get_oncall() -> JSONResponse:
    """Return the current on-call primary and secondary based on rotation schedule."""
    schedule = CONFIG.get("oncall_schedule", {}).get("rotation", [])
    now = datetime.now(timezone.utc).date()
    primary = secondary = None
    for entry in schedule:
        week_start = datetime.strptime(entry["week_start"], "%Y-%m-%d").date()
        week_end = week_start + timedelta(days=7)
        if week_start <= now < week_end:
            primary = entry.get("primary")
            secondary = entry.get("secondary")
            break
    if primary is None and schedule:
        # Fall back to last entry
        entry = schedule[-1]
        primary = entry.get("primary")
        secondary = entry.get("secondary")
    return JSONResponse(
        {
            "primary": primary,
            "secondary": secondary,
            "manager": CONFIG.get("oncall_schedule", {}).get("manager"),
        }
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    srv_cfg = (CONFIG or load_config()).get("server", {})
    uvicorn.run(
        "alert-bot:app",
        host=srv_cfg.get("host", "0.0.0.0"),
        port=int(srv_cfg.get("port", 8080)),
        log_level=srv_cfg.get("log_level", "info").lower(),
        reload=False,
    )
