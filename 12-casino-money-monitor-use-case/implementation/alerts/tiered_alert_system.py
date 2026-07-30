#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Casino Money Monitor - Tiered Alert System
=============================================
Chapter 5 Implementation: Checklist Item #4

Implements a three-tier alert system (Warning / Critical / Emergency) with
automatic escalation for casino financial operations.

Alert Flow:
    Metric Breach -> Evaluate Rules -> Route by Tier -> Escalate if unacknowledged

Tier Definitions:
    WARNING   - Informational, auto-resolves. Slack/email to ops team.
                Example: Liquidity ratio drops below 2.5x
    CRITICAL  - Requires acknowledgement within 15 min. Pages on-call.
                Example: Liquidity ratio drops below 1.5x (regulatory minimum)
    EMERGENCY - Immediate action. Calls CEO + CFO + compliance. Auto-triggers
                maintenance mode if conditions persist 10 min.
                Example: Liquidity ratio drops below 1.0x, negative net position

Escalation:
    WARNING   -> 30 min unacked -> CRITICAL
    CRITICAL  -> 15 min unacked -> EMERGENCY
    EMERGENCY -> 10 min unacked -> Auto maintenance mode + regulator notification

PCI DSS Compliance Notes:
- Requirement 10.6: Review logs and security events daily
- Requirement 12.10: Incident response plan integration
- All alert data encrypted in transit (Req 4.1)

Dependencies:
    pip install pydantic redis httpx jinja2
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional, Callable, Awaitable
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger("alert_system")

# ---------------------------------------------------------------------------
# Alert Configuration
# ---------------------------------------------------------------------------

class AlertTier(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class AlertStatus(str, Enum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    AUTO_RESOLVED = "auto_resolved"


class EscalationConfig(BaseModel):
    """Escalation timing per tier."""
    warning_to_critical_minutes: int = 30
    critical_to_emergency_minutes: int = 15
    emergency_auto_action_minutes: int = 10


class NotificationChannel(str, Enum):
    SLACK = "slack"
    EMAIL = "email"
    SMS = "sms"
    PAGERDUTY = "pagerduty"
    PHONE_CALL = "phone_call"
    TEAMS = "teams"
    WEBHOOK = "webhook"


# ---------------------------------------------------------------------------
# Alert Models
# ---------------------------------------------------------------------------

class AlertRule(BaseModel):
    """Defines a financial alert rule with thresholds."""
    rule_id: str
    name: str
    description: str
    metric: str                          # e.g., "liquidity_ratio", "pending_withdrawals_eur"
    condition: str                       # lt, gt, eq, gte, lte
    warning_threshold: Optional[Decimal] = None
    critical_threshold: Optional[Decimal] = None
    emergency_threshold: Optional[Decimal] = None
    enabled: bool = True
    cooldown_minutes: int = 5            # min time between repeated alerts
    auto_resolve: bool = True            # resolve when metric returns to normal


class Alert(BaseModel):
    """An active or historical alert instance."""
    alert_id: str = Field(default_factory=lambda: str(uuid4()))
    rule_id: str
    rule_name: str
    tier: AlertTier
    status: AlertStatus = AlertStatus.ACTIVE
    metric: str
    metric_value: Decimal
    threshold: Decimal
    message: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    escalated_at: Optional[datetime] = None
    escalated_from: Optional[str] = None    # previous tier
    notifications_sent: list[str] = []
    metadata: dict = {}


class ContactGroup(BaseModel):
    """Group of contacts for a specific notification tier."""
    name: str
    tier: AlertTier
    channels: list[NotificationChannel]
    contacts: list[dict]  # {"name": "...", "email": "...", "phone": "...", "slack_id": "..."}


# ---------------------------------------------------------------------------
# Notification Dispatchers
# ---------------------------------------------------------------------------

class NotificationDispatcher:
    """
    Sends alert notifications via configured channels.
    Production: integrate with PagerDuty, Slack, Twilio, SendGrid, etc.
    """

    async def send_slack(self, alert: Alert, webhook_url: str, channel: str = "#treasury-alerts"):
        """Send alert to Slack channel."""
        color_map = {
            AlertTier.WARNING: "#FFA500",    # orange
            AlertTier.CRITICAL: "#FF0000",   # red
            AlertTier.EMERGENCY: "#8B0000",  # dark red
        }

        payload = {
            "channel": channel,
            "attachments": [{
                "color": color_map.get(alert.tier, "#808080"),
                "title": f"[{alert.tier.value.upper()}] {alert.rule_name}",
                "text": alert.message,
                "fields": [
                    {"title": "Metric", "value": alert.metric, "short": True},
                    {"title": "Value", "value": f"{alert.metric_value:,.2f}", "short": True},
                    {"title": "Threshold", "value": f"{alert.threshold:,.2f}", "short": True},
                    {"title": "Alert ID", "value": alert.alert_id[:8], "short": True},
                ],
                "footer": "Casino Money Monitor",
                "ts": int(alert.created_at.timestamp()),
            }],
        }

        # In production:
        # async with httpx.AsyncClient() as client:
        #     await client.post(webhook_url, json=payload)
        logger.info(f"Slack notification sent: {alert.tier.value} - {alert.rule_name}")

    async def send_email(self, alert: Alert, recipients: list[str]):
        """Send alert via email (SendGrid/SES)."""
        subject = f"[{alert.tier.value.upper()}] Casino Money Monitor: {alert.rule_name}"
        body = f"""
        Alert: {alert.rule_name}
        Tier: {alert.tier.value.upper()}
        Time: {alert.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}

        {alert.message}

        Metric: {alert.metric}
        Current Value: {alert.metric_value:,.2f}
        Threshold: {alert.threshold:,.2f}

        Alert ID: {alert.alert_id}

        --- Action Required ---
        {'IMMEDIATE ACTION REQUIRED' if alert.tier == AlertTier.EMERGENCY else
         'Please acknowledge within 15 minutes' if alert.tier == AlertTier.CRITICAL else
         'Monitor and take action if needed'}

        Acknowledge: https://backoffice.casino.internal/alerts/{alert.alert_id}/ack
        """
        # In production: send via SendGrid/SES
        logger.info(f"Email sent to {recipients}: {subject}")

    async def send_sms(self, alert: Alert, phone_numbers: list[str]):
        """Send SMS via Twilio for critical/emergency alerts."""
        message = (
            f"[{alert.tier.value.upper()}] {alert.rule_name}: "
            f"{alert.metric}={alert.metric_value:,.2f} "
            f"(threshold: {alert.threshold:,.2f}). "
            f"ID: {alert.alert_id[:8]}"
        )
        # In production: Twilio API call
        logger.info(f"SMS sent to {phone_numbers}: {message[:80]}...")

    async def send_pagerduty(self, alert: Alert, routing_key: str):
        """Trigger PagerDuty incident for on-call rotation."""
        severity_map = {
            AlertTier.WARNING: "warning",
            AlertTier.CRITICAL: "critical",
            AlertTier.EMERGENCY: "critical",
        }

        payload = {
            "routing_key": routing_key,
            "event_action": "trigger",
            "payload": {
                "summary": f"{alert.rule_name}: {alert.message}",
                "severity": severity_map[alert.tier],
                "source": "casino-money-monitor",
                "component": alert.metric,
                "custom_details": {
                    "metric_value": str(alert.metric_value),
                    "threshold": str(alert.threshold),
                    "alert_id": alert.alert_id,
                },
            },
        }
        # In production: POST to https://events.pagerduty.com/v2/enqueue
        logger.info(f"PagerDuty incident triggered: {alert.rule_name}")

    async def phone_call(self, alert: Alert, phone_numbers: list[str]):
        """Automated phone call for emergency tier (Twilio Voice)."""
        twiml = f"""
        <Response>
            <Say voice="alice">
                Emergency alert from Casino Money Monitor.
                {alert.rule_name}.
                {alert.message}.
                Press 1 to acknowledge.
            </Say>
            <Gather numDigits="1" action="/alert/{alert.alert_id}/ack-voice"/>
        </Response>
        """
        logger.info(f"Emergency phone call initiated to {phone_numbers}")


# ---------------------------------------------------------------------------
# Alert Engine
# ---------------------------------------------------------------------------

class TieredAlertSystem:
    """
    Core alert engine with automatic escalation.

    Features:
    - Rule-based threshold evaluation
    - Three-tier severity classification
    - Automatic escalation on unacknowledged alerts
    - Cooldown to prevent alert fatigue
    - Auto-resolve when metrics normalize
    - Audit trail for all alert lifecycle events
    """

    def __init__(self, escalation_config: Optional[EscalationConfig] = None):
        self.config = escalation_config or EscalationConfig()
        self.dispatcher = NotificationDispatcher()

        self._rules: dict[str, AlertRule] = {}
        self._active_alerts: dict[str, Alert] = {}
        self._alert_history: list[Alert] = []
        self._last_fired: dict[str, datetime] = {}  # rule_id -> last fire time

        # Contact groups per tier
        self._contact_groups: dict[AlertTier, ContactGroup] = {}

        # Callbacks
        self._on_emergency: Optional[Callable[[Alert], Awaitable[None]]] = None

        # Load default casino financial rules
        self._load_default_rules()
        self._load_default_contacts()

    def _load_default_rules(self):
        """Load standard financial alert rules for casino operations."""
        rules = [
            AlertRule(
                rule_id="LCR",
                name="Liquidity Coverage Ratio",
                description="Available cash / total exposure ratio",
                metric="liquidity_ratio",
                condition="lt",
                warning_threshold=Decimal("2.5"),
                critical_threshold=Decimal("1.5"),
                emergency_threshold=Decimal("1.0"),
            ),
            AlertRule(
                rule_id="NET_LIQ",
                name="Net Liquidity Position",
                description="Available cash minus total exposure (EUR)",
                metric="net_liquidity_eur",
                condition="lt",
                warning_threshold=Decimal("500000"),
                critical_threshold=Decimal("100000"),
                emergency_threshold=Decimal("0"),
            ),
            AlertRule(
                rule_id="PEND_WD",
                name="Pending Withdrawal Queue",
                description="Total pending withdrawals value (EUR)",
                metric="pending_withdrawals_eur",
                condition="gt",
                warning_threshold=Decimal("500000"),
                critical_threshold=Decimal("1000000"),
                emergency_threshold=Decimal("2000000"),
            ),
            AlertRule(
                rule_id="WD_DELAY",
                name="Withdrawal Processing Delay",
                description="Oldest pending withdrawal age (hours)",
                metric="oldest_withdrawal_hours",
                condition="gt",
                warning_threshold=Decimal("12"),
                critical_threshold=Decimal("24"),
                emergency_threshold=Decimal("48"),
            ),
            AlertRule(
                rule_id="PSP_HEALTH",
                name="Payment Provider Down",
                description="Number of payment providers offline",
                metric="psp_providers_down",
                condition="gt",
                warning_threshold=Decimal("0"),
                critical_threshold=Decimal("1"),
                emergency_threshold=Decimal("3"),
            ),
            AlertRule(
                rule_id="FX_EXPOSURE",
                name="FX Exposure Concentration",
                description="Single currency exposure as % of total",
                metric="max_fx_concentration_pct",
                condition="gt",
                warning_threshold=Decimal("40"),
                critical_threshold=Decimal("60"),
                emergency_threshold=Decimal("80"),
            ),
            AlertRule(
                rule_id="CHARGEBACK",
                name="Chargeback Rate",
                description="Rolling 30-day chargeback rate (%)",
                metric="chargeback_rate_pct",
                condition="gt",
                warning_threshold=Decimal("0.5"),     # Visa threshold: 0.9%
                critical_threshold=Decimal("0.75"),
                emergency_threshold=Decimal("0.9"),    # program enrollment
            ),
            AlertRule(
                rule_id="BANK_SYNC",
                name="Bank Balance Sync Stale",
                description="Minutes since last successful bank sync",
                metric="bank_sync_age_minutes",
                condition="gt",
                warning_threshold=Decimal("30"),
                critical_threshold=Decimal("60"),
                emergency_threshold=Decimal("120"),
            ),
            AlertRule(
                rule_id="JACKPOT_LIQ",
                name="Jackpot Liquidity Coverage",
                description="Cash available / total jackpot pool ratio",
                metric="jackpot_coverage_ratio",
                condition="lt",
                warning_threshold=Decimal("3.0"),
                critical_threshold=Decimal("1.5"),
                emergency_threshold=Decimal("1.0"),
            ),
            AlertRule(
                rule_id="DAILY_GGR",
                name="Daily GGR Anomaly",
                description="GGR deviation from 30-day average (%)",
                metric="ggr_deviation_pct",
                condition="gt",
                warning_threshold=Decimal("30"),
                critical_threshold=Decimal("50"),
                emergency_threshold=Decimal("75"),
            ),
        ]
        for rule in rules:
            self._rules[rule.rule_id] = rule

    def _load_default_contacts(self):
        """Load default contact groups."""
        self._contact_groups = {
            AlertTier.WARNING: ContactGroup(
                name="Operations Team",
                tier=AlertTier.WARNING,
                channels=[NotificationChannel.SLACK, NotificationChannel.EMAIL],
                contacts=[
                    {"name": "Treasury Ops", "email": "treasury-ops@casino.com", "slack_id": "#treasury-alerts"},
                ],
            ),
            AlertTier.CRITICAL: ContactGroup(
                name="On-Call + Management",
                tier=AlertTier.CRITICAL,
                channels=[NotificationChannel.SLACK, NotificationChannel.EMAIL,
                          NotificationChannel.SMS, NotificationChannel.PAGERDUTY],
                contacts=[
                    {"name": "On-Call Treasury", "phone": "+44700000001", "email": "oncall@casino.com"},
                    {"name": "Head of Finance", "phone": "+44700000002", "email": "cfo@casino.com"},
                ],
            ),
            AlertTier.EMERGENCY: ContactGroup(
                name="Executive + Compliance",
                tier=AlertTier.EMERGENCY,
                channels=[NotificationChannel.PHONE_CALL, NotificationChannel.SMS,
                          NotificationChannel.SLACK, NotificationChannel.EMAIL,
                          NotificationChannel.PAGERDUTY],
                contacts=[
                    {"name": "CEO", "phone": "+44700000003", "email": "ceo@casino.com"},
                    {"name": "CFO", "phone": "+44700000002", "email": "cfo@casino.com"},
                    {"name": "Head of Compliance", "phone": "+44700000004", "email": "compliance@casino.com"},
                    {"name": "MLRO", "phone": "+44700000005", "email": "mlro@casino.com"},
                ],
            ),
        }

    # ---- Evaluation ----

    async def evaluate_metrics(self, metrics: dict[str, Decimal]):
        """
        Evaluate all rules against current metrics.
        Call this on every dashboard refresh cycle.
        """
        for rule_id, rule in self._rules.items():
            if not rule.enabled:
                continue

            value = metrics.get(rule.metric)
            if value is None:
                continue

            tier = self._determine_tier(rule, value)

            if tier:
                await self._fire_alert(rule, tier, value)
            elif rule.auto_resolve:
                self._auto_resolve(rule_id)

    def _determine_tier(self, rule: AlertRule, value: Decimal) -> Optional[AlertTier]:
        """Determine which alert tier the current value triggers."""
        thresholds = [
            (AlertTier.EMERGENCY, rule.emergency_threshold),
            (AlertTier.CRITICAL, rule.critical_threshold),
            (AlertTier.WARNING, rule.warning_threshold),
        ]

        for tier, threshold in thresholds:
            if threshold is None:
                continue

            triggered = False
            if rule.condition == "lt":
                triggered = value < threshold
            elif rule.condition == "lte":
                triggered = value <= threshold
            elif rule.condition == "gt":
                triggered = value > threshold
            elif rule.condition == "gte":
                triggered = value >= threshold
            elif rule.condition == "eq":
                triggered = value == threshold

            if triggered:
                return tier

        return None

    async def _fire_alert(self, rule: AlertRule, tier: AlertTier, value: Decimal):
        """Create and dispatch an alert."""
        now = datetime.now(timezone.utc)

        # Cooldown check
        last = self._last_fired.get(rule.rule_id)
        if last and (now - last).total_seconds() < rule.cooldown_minutes * 60:
            return

        # Check if same alert already active at same or higher tier
        existing = self._active_alerts.get(rule.rule_id)
        if existing and existing.status == AlertStatus.ACTIVE:
            tier_order = {AlertTier.WARNING: 0, AlertTier.CRITICAL: 1, AlertTier.EMERGENCY: 2}
            if tier_order.get(existing.tier, 0) >= tier_order.get(tier, 0):
                return  # already at same or higher tier

        threshold = {
            AlertTier.WARNING: rule.warning_threshold,
            AlertTier.CRITICAL: rule.critical_threshold,
            AlertTier.EMERGENCY: rule.emergency_threshold,
        }.get(tier, Decimal("0"))

        alert = Alert(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            tier=tier,
            metric=rule.metric,
            metric_value=value,
            threshold=threshold or Decimal("0"),
            message=f"{rule.description}: current={value:,.2f}, threshold={threshold:,.2f}",
            escalated_from=existing.tier.value if existing else None,
        )

        self._active_alerts[rule.rule_id] = alert
        self._last_fired[rule.rule_id] = now

        await self._dispatch_notifications(alert)

        # Emergency callback (e.g., trigger maintenance mode)
        if tier == AlertTier.EMERGENCY and self._on_emergency:
            await self._on_emergency(alert)

        logger.warning(f"Alert fired: [{tier.value}] {rule.name} = {value:,.2f}")

    def _auto_resolve(self, rule_id: str):
        """Auto-resolve alert when metric returns to normal."""
        alert = self._active_alerts.get(rule_id)
        if alert and alert.status == AlertStatus.ACTIVE:
            alert.status = AlertStatus.AUTO_RESOLVED
            alert.resolved_at = datetime.now(timezone.utc)
            self._alert_history.append(alert)
            del self._active_alerts[rule_id]
            logger.info(f"Alert auto-resolved: {alert.rule_name}")

    async def _dispatch_notifications(self, alert: Alert):
        """Send notifications to the appropriate contact group."""
        group = self._contact_groups.get(alert.tier)
        if not group:
            return

        for channel in group.channels:
            try:
                if channel == NotificationChannel.SLACK:
                    await self.dispatcher.send_slack(
                        alert, webhook_url="https://hooks.slack.com/...",
                        channel="#treasury-alerts",
                    )
                elif channel == NotificationChannel.EMAIL:
                    emails = [c.get("email", "") for c in group.contacts if c.get("email")]
                    await self.dispatcher.send_email(alert, emails)
                elif channel == NotificationChannel.SMS:
                    phones = [c.get("phone", "") for c in group.contacts if c.get("phone")]
                    await self.dispatcher.send_sms(alert, phones)
                elif channel == NotificationChannel.PAGERDUTY:
                    await self.dispatcher.send_pagerduty(alert, routing_key="R0...")
                elif channel == NotificationChannel.PHONE_CALL:
                    phones = [c.get("phone", "") for c in group.contacts if c.get("phone")]
                    await self.dispatcher.phone_call(alert, phones)

                alert.notifications_sent.append(channel.value)
            except Exception as e:
                logger.error(f"Failed to send {channel.value} notification: {e}")

    # ---- Escalation Loop ----

    async def run_escalation_loop(self):
        """Background loop that escalates unacknowledged alerts."""
        while True:
            await asyncio.sleep(60)  # check every minute
            now = datetime.now(timezone.utc)

            for rule_id, alert in list(self._active_alerts.items()):
                if alert.status != AlertStatus.ACTIVE:
                    continue

                age = (now - alert.created_at).total_seconds() / 60  # minutes

                if alert.tier == AlertTier.WARNING and age >= self.config.warning_to_critical_minutes:
                    logger.warning(f"Escalating {alert.rule_name} from WARNING to CRITICAL")
                    alert.tier = AlertTier.CRITICAL
                    alert.escalated_at = now
                    alert.status = AlertStatus.ESCALATED
                    await self._fire_alert(self._rules[rule_id], AlertTier.CRITICAL, alert.metric_value)

                elif alert.tier == AlertTier.CRITICAL and age >= (
                    self.config.warning_to_critical_minutes + self.config.critical_to_emergency_minutes
                ):
                    logger.warning(f"Escalating {alert.rule_name} from CRITICAL to EMERGENCY")
                    alert.tier = AlertTier.EMERGENCY
                    alert.escalated_at = now
                    alert.status = AlertStatus.ESCALATED
                    await self._fire_alert(self._rules[rule_id], AlertTier.EMERGENCY, alert.metric_value)

    # ---- Management ----

    def acknowledge(self, alert_id: str, user: str) -> bool:
        """Acknowledge an alert to stop escalation."""
        for alert in self._active_alerts.values():
            if alert.alert_id == alert_id:
                alert.status = AlertStatus.ACKNOWLEDGED
                alert.acknowledged_at = datetime.now(timezone.utc)
                alert.acknowledged_by = user
                logger.info(f"Alert {alert_id} acknowledged by {user}")
                return True
        return False

    def resolve(self, alert_id: str, user: str) -> bool:
        """Manually resolve an alert."""
        for rule_id, alert in list(self._active_alerts.items()):
            if alert.alert_id == alert_id:
                alert.status = AlertStatus.RESOLVED
                alert.resolved_at = datetime.now(timezone.utc)
                self._alert_history.append(alert)
                del self._active_alerts[rule_id]
                logger.info(f"Alert {alert_id} resolved by {user}")
                return True
        return False

    def set_emergency_callback(self, callback: Callable[[Alert], Awaitable[None]]):
        """Set callback for emergency-tier alerts (e.g., trigger maintenance mode)."""
        self._on_emergency = callback

    @property
    def active_alerts(self) -> list[Alert]:
        return list(self._active_alerts.values())

    @property
    def history(self) -> list[Alert]:
        return list(self._alert_history)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

async def main():
    """Demonstrate the alert system with sample casino metrics."""
    system = TieredAlertSystem()

    # Simulate emergency callback
    async def on_emergency(alert: Alert):
        print(f"\n*** EMERGENCY CALLBACK: {alert.rule_name} ***")
        print(f"*** AUTO-TRIGGERING MAINTENANCE MODE ***\n")

    system.set_emergency_callback(on_emergency)

    # Scenario 1: Normal operations
    print("=== Scenario 1: Normal Operations ===")
    await system.evaluate_metrics({
        "liquidity_ratio": Decimal("3.5"),
        "net_liquidity_eur": Decimal("2000000"),
        "pending_withdrawals_eur": Decimal("150000"),
        "oldest_withdrawal_hours": Decimal("4"),
        "psp_providers_down": Decimal("0"),
        "chargeback_rate_pct": Decimal("0.3"),
    })
    print(f"Active alerts: {len(system.active_alerts)}")

    # Scenario 2: Liquidity pressure
    print("\n=== Scenario 2: Liquidity Under Pressure ===")
    await system.evaluate_metrics({
        "liquidity_ratio": Decimal("1.8"),         # WARNING (< 2.5)
        "net_liquidity_eur": Decimal("80000"),      # CRITICAL (< 100K)
        "pending_withdrawals_eur": Decimal("1200000"),  # CRITICAL (> 1M)
        "oldest_withdrawal_hours": Decimal("30"),   # CRITICAL (> 24h)
        "psp_providers_down": Decimal("2"),         # CRITICAL (> 1)
        "chargeback_rate_pct": Decimal("0.8"),      # CRITICAL (> 0.75)
    })

    for alert in system.active_alerts:
        print(f"  [{alert.tier.value.upper():9s}] {alert.rule_name}: {alert.metric_value}")

    # Scenario 3: Emergency
    print("\n=== Scenario 3: Emergency - Negative Liquidity ===")
    await system.evaluate_metrics({
        "liquidity_ratio": Decimal("0.7"),          # EMERGENCY (< 1.0)
        "net_liquidity_eur": Decimal("-150000"),     # EMERGENCY (< 0)
        "pending_withdrawals_eur": Decimal("2500000"),  # EMERGENCY (> 2M)
    })

    for alert in system.active_alerts:
        print(f"  [{alert.tier.value.upper():9s}] {alert.rule_name}: {alert.metric_value}")


if __name__ == "__main__":
    asyncio.run(main())
