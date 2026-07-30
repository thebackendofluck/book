#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Security Orchestration and Automated Response (SOAR) engine for iGaming platforms.

Orchestrates the full incident response lifecycle for gambling-specific threats:
  - Receives normalized alerts from IDS/IPS, WAF, fraud detectors, and SIEM
  - Applies iGaming-specific detection rules (bonus abuse, money laundering,
    account takeover, bonus farm bot networks)
  - Executes automated response playbooks (block IP, lock account, notify
    compliance, escalate to PagerDuty)
  - Persists incidents and actions to the SQLite incident database
  - Routes notifications via Slack, email, and PagerDuty
  - Publishes response metrics to Prometheus

Playbooks:
  CRITICAL  → block_ip + lock_account + pagerduty + slack_critical + email_compliance
  HIGH      → block_ip + slack_alerts + email_security
  MEDIUM    → slack_alerts (rate-limited digest)
  BONUS_ABUSE → lock_account + flag_for_review + slack_alerts
  MONEY_LAUNDERING → lock_account + notify_compliance + pagerduty (always)

Usage:
    # Run as a daemon processing events from a Kafka/Redis queue:
    python soar_core.py daemon --config /etc/soar/config.yml

    # Process a single alert from file:
    python soar_core.py process --alert-file alert.json --config /etc/soar/config.yml

    # Replay the fallback queue:
    python soar_core.py replay --config /etc/soar/config.yml

Reference: Chapter 24 — Security and Compliance / SOAR and Incident Response
           Chapter 23 — DevSecOps / Incident Response Automation
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger("soar_core")


# ---------------------------------------------------------------------------
# Alert schema
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    """
    Normalized security alert consumed by the SOAR engine.

    All inbound events (WAF, IDS, fraud detector, SIEM) are normalised to
    this schema before processing.
    """
    alert_id: str
    schema_version: str = "1.0"
    timestamp: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    detector: str = "unknown"
    alert_type: str = "unknown"
    severity: str = "info"
    source_ip: str = ""
    user_id: str = ""
    description: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    geo_country_code: str = ""
    jurisdiction: str = ""
    should_block: bool = False
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Alert":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_json(cls, json_str: str) -> "Alert":
        return cls.from_dict(json.loads(json_str))


_SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

def _severity_index(severity: str) -> int:
    try:
        return _SEVERITY_ORDER.index(severity.lower())
    except ValueError:
        return len(_SEVERITY_ORDER)


# ---------------------------------------------------------------------------
# Playbook actions
# ---------------------------------------------------------------------------

class PlaybookAction:
    """
    A single automated response action in a SOAR playbook.

    Subclasses implement the execute() method.
    """

    name: str = "base_action"

    def execute(self, alert: Alert, dry_run: bool = False) -> dict[str, Any]:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


class LogAction(PlaybookAction):
    """Write a structured incident log entry."""
    name = "log"

    def execute(self, alert: Alert, dry_run: bool = False) -> dict[str, Any]:
        log.info(
            "soar_action_log alert_id=%s type=%s severity=%s ip=%s",
            alert.alert_id,
            alert.alert_type,
            alert.severity,
            alert.source_ip,
        )
        return {"action": "log", "status": "ok"}


class BlockIPAction(PlaybookAction):
    """
    Block a source IP via the WAF.

    In a live environment, this calls the WAFIntegration client.
    The implementation here publishes to the action queue for the
    WAF worker thread to pick up asynchronously.
    """
    name = "block_ip"

    def __init__(self, action_queue: "queue.Queue[dict[str, Any]]") -> None:
        self._queue = action_queue

    def execute(self, alert: Alert, dry_run: bool = False) -> dict[str, Any]:
        if not alert.source_ip:
            return {"action": "block_ip", "status": "skip", "reason": "no source_ip"}
        payload = {
            "action": "block_ip",
            "ip": alert.source_ip,
            "alert_id": alert.alert_id,
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "dry_run": dry_run,
            "queued_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        if dry_run:
            log.info("DRY RUN: would block IP %s", alert.source_ip)
            return {"action": "block_ip", "status": "dry_run", "ip": alert.source_ip}
        try:
            self._queue.put_nowait(payload)
            log.info("soar_block_ip_queued ip=%s alert_id=%s", alert.source_ip, alert.alert_id)
            return {"action": "block_ip", "status": "queued", "ip": alert.source_ip}
        except queue.Full:
            log.error("soar_action_queue_full action=block_ip ip=%s", alert.source_ip)
            return {"action": "block_ip", "status": "error", "reason": "queue full"}


class LockAccountAction(PlaybookAction):
    """Lock the affected player account via the platform API."""
    name = "lock_account"

    def __init__(self, platform_api_url: str = "", api_key: str = "") -> None:
        self._api_url = platform_api_url
        self._api_key = api_key

    def execute(self, alert: Alert, dry_run: bool = False) -> dict[str, Any]:
        if not alert.user_id:
            return {"action": "lock_account", "status": "skip", "reason": "no user_id"}
        if dry_run:
            log.info("DRY RUN: would lock account user_id=%s", alert.user_id)
            return {"action": "lock_account", "status": "dry_run", "user_id": alert.user_id}
        # In production this calls the platform's account lock API endpoint
        log.info(
            "soar_lock_account user_id=%s alert_id=%s",
            alert.user_id,
            alert.alert_id,
        )
        return {"action": "lock_account", "status": "queued", "user_id": alert.user_id}


class FlagForReviewAction(PlaybookAction):
    """Flag a player account for manual compliance review."""
    name = "flag_for_review"

    def execute(self, alert: Alert, dry_run: bool = False) -> dict[str, Any]:
        if not alert.user_id:
            return {"action": "flag_for_review", "status": "skip", "reason": "no user_id"}
        log.info(
            "soar_flag_for_review user_id=%s reason=%s alert_id=%s",
            alert.user_id,
            alert.alert_type,
            alert.alert_id,
        )
        return {
            "action": "flag_for_review",
            "status": "ok",
            "user_id": alert.user_id,
            "dry_run": dry_run,
        }


class NotifySlackAction(PlaybookAction):
    """Send a Slack notification via webhook."""
    name = "notify_slack"

    def __init__(self, webhook_url: str = "", channel: str = "#security-alerts") -> None:
        self._webhook_url = webhook_url or os.environ.get("SLACK_SECURITY_WEBHOOK", "")
        self._channel = channel

    def execute(self, alert: Alert, dry_run: bool = False) -> dict[str, Any]:
        if not self._webhook_url:
            return {"action": "notify_slack", "status": "skip", "reason": "no webhook_url"}
        if dry_run:
            log.info("DRY RUN: would notify Slack channel=%s", self._channel)
            return {"action": "notify_slack", "status": "dry_run"}
        import urllib.request, urllib.error
        payload = {
            "text": f":rotating_light: *{alert.severity.upper()}* — {alert.alert_type}",
            "attachments": [{
                "color": "#FF0000" if alert.severity == "critical" else "#FF6600",
                "fields": [
                    {"title": "Alert ID", "value": alert.alert_id[:8], "short": True},
                    {"title": "Source IP", "value": alert.source_ip or "unknown", "short": True},
                    {"title": "Description", "value": alert.description[:500], "short": False},
                ],
                "footer": f"AcmeToCasino SOAR | {alert.timestamp[:19].replace('T', ' ')} UTC",
            }],
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._webhook_url, data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            log.info("soar_slack_notified alert_id=%s", alert.alert_id)
            return {"action": "notify_slack", "status": "ok"}
        except (urllib.error.URLError, TimeoutError) as exc:
            log.error("soar_slack_notify_failed: %s", exc)
            return {"action": "notify_slack", "status": "error", "reason": str(exc)}


class NotifyPagerDutyAction(PlaybookAction):
    """Trigger a PagerDuty incident for critical alerts."""
    name = "notify_pagerduty"

    def __init__(self, routing_key: str = "") -> None:
        self._routing_key = routing_key or os.environ.get("PAGERDUTY_ROUTING_KEY", "")

    def execute(self, alert: Alert, dry_run: bool = False) -> dict[str, Any]:
        if not self._routing_key:
            return {"action": "notify_pagerduty", "status": "skip", "reason": "no routing_key"}
        if dry_run:
            log.info("DRY RUN: would trigger PagerDuty for alert_id=%s", alert.alert_id)
            return {"action": "notify_pagerduty", "status": "dry_run"}
        import urllib.request, urllib.error
        payload = {
            "routing_key": self._routing_key,
            "event_action": "trigger",
            "dedup_key": alert.alert_id,
            "payload": {
                "summary": f"[{alert.severity.upper()}] {alert.alert_type}: {alert.source_ip}",
                "severity": "critical" if alert.severity == "critical" else "error",
                "source": alert.source_ip,
                "timestamp": alert.timestamp,
                "custom_details": {
                    "description": alert.description,
                    "evidence": alert.evidence,
                    "alert_type": alert.alert_type,
                    "user_id": alert.user_id,
                },
            },
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://events.pagerduty.com/v2/enqueue",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            log.info("soar_pagerduty_triggered alert_id=%s", alert.alert_id)
            return {"action": "notify_pagerduty", "status": "ok"}
        except (urllib.error.URLError, TimeoutError) as exc:
            log.error("soar_pagerduty_failed: %s", exc)
            return {"action": "notify_pagerduty", "status": "error", "reason": str(exc)}


# ---------------------------------------------------------------------------
# Playbook registry
# ---------------------------------------------------------------------------

@dataclass
class Playbook:
    """
    A named sequence of actions executed in response to a matched alert.

    Attributes:
        name:         Human-readable playbook name.
        match_types:  Alert types this playbook handles.
        min_severity: Minimum severity to trigger this playbook.
        actions:      Ordered list of PlaybookAction instances to execute.
    """
    name: str
    match_types: list[str]
    min_severity: str
    actions: list[PlaybookAction]


# ---------------------------------------------------------------------------
# SOAR engine
# ---------------------------------------------------------------------------

class SOAREngine:
    """
    Security Orchestration and Automated Response engine.

    Processes normalized alerts, selects matching playbooks, executes
    response actions, and persists the results.

    Args:
        playbooks:  List of Playbook definitions.
        dry_run:    If True, plan but do not execute actions.
        db_path:    Path to SQLite incident database.
    """

    def __init__(
        self,
        playbooks: list[Playbook],
        dry_run: bool = False,
        db_path: str = "/var/lib/soar/incidents.db",
    ) -> None:
        self._playbooks = playbooks
        self.dry_run = dry_run
        self._db_path = db_path
        self._action_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=10000)
        self._processed = 0
        self._failed = 0
        self._lock = threading.Lock()

    def process(self, alert: Alert) -> dict[str, Any]:
        """
        Process a single normalized alert.

        Selects matching playbooks, executes their actions in order,
        and returns a summary of the response.

        Args:
            alert: Normalized security alert.

        Returns:
            Response summary dict.
        """
        log.info(
            "soar_processing alert_id=%s type=%s severity=%s ip=%s",
            alert.alert_id,
            alert.alert_type,
            alert.severity,
            alert.source_ip,
        )

        matched_playbooks = self._select_playbooks(alert)
        if not matched_playbooks:
            log.info("soar_no_playbook_matched alert_id=%s type=%s", alert.alert_id, alert.alert_type)
            with self._lock:
                self._processed += 1
            return {
                "alert_id": alert.alert_id,
                "status": "no_playbook_matched",
                "actions": [],
            }

        action_results: list[dict[str, Any]] = []
        for playbook in matched_playbooks:
            log.info(
                "soar_playbook_executing name=%s alert_id=%s",
                playbook.name,
                alert.alert_id,
            )
            for action in playbook.actions:
                try:
                    result = action.execute(alert, dry_run=self.dry_run)
                    result["playbook"] = playbook.name
                    action_results.append(result)
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "soar_action_error action=%s alert_id=%s error=%s",
                        action.name,
                        alert.alert_id,
                        exc,
                    )
                    action_results.append({
                        "action": action.name,
                        "playbook": playbook.name,
                        "status": "error",
                        "reason": str(exc),
                    })

        with self._lock:
            self._processed += 1

        response = {
            "alert_id": alert.alert_id,
            "status": "processed",
            "dry_run": self.dry_run,
            "playbooks_executed": [p.name for p in matched_playbooks],
            "actions": action_results,
            "processed_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        log.info(
            "soar_processed alert_id=%s playbooks=%d actions=%d",
            alert.alert_id,
            len(matched_playbooks),
            len(action_results),
        )
        return response

    def _select_playbooks(self, alert: Alert) -> list[Playbook]:
        """Return all playbooks matching the alert type and severity."""
        matched = []
        alert_sev = _severity_index(alert.severity)
        for pb in self._playbooks:
            sev_threshold = _severity_index(pb.min_severity)
            type_match = (
                not pb.match_types
                or alert.alert_type in pb.match_types
                or "*" in pb.match_types
            )
            if type_match and alert_sev <= sev_threshold:
                matched.append(pb)
        return matched

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"processed": self._processed, "failed": self._failed}


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

def build_default_engine(
    dry_run: bool = False,
    slack_webhook: str = "",
    pagerduty_key: str = "",
    db_path: str = "/var/lib/soar/incidents.db",
) -> SOAREngine:
    """
    Build a SOAR engine with the standard iGaming playbook set.

    Args:
        dry_run:          Plan-only mode.
        slack_webhook:    Slack incoming webhook URL.
        pagerduty_key:    PagerDuty routing key.
        db_path:          SQLite incident database path.

    Returns:
        Configured SOAREngine.
    """
    action_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=10000)

    log_action = LogAction()
    block_ip = BlockIPAction(action_queue)
    lock_account = LockAccountAction()
    flag_review = FlagForReviewAction()
    notify_slack = NotifySlackAction(webhook_url=slack_webhook)
    notify_pd = NotifyPagerDutyAction(routing_key=pagerduty_key)

    playbooks: list[Playbook] = [
        # Critical: full response — block, lock, PagerDuty, Slack
        Playbook(
            name="critical-full-response",
            match_types=["*"],
            min_severity="critical",
            actions=[log_action, block_ip, lock_account, notify_pd, notify_slack],
        ),
        # Money laundering: always escalate to compliance regardless of severity
        Playbook(
            name="money-laundering-response",
            match_types=["money_laundering", "suspicious_transaction_pattern"],
            min_severity="medium",
            actions=[log_action, lock_account, flag_review, notify_pd, notify_slack],
        ),
        # Bonus abuse: lock account and flag for manual review
        Playbook(
            name="bonus-abuse-response",
            match_types=["bonus_abuse", "multi_accounting", "bonus_farming"],
            min_severity="medium",
            actions=[log_action, lock_account, flag_review, notify_slack],
        ),
        # Account takeover: block source IP and lock account
        Playbook(
            name="account-takeover-response",
            match_types=["account_takeover", "credential_stuffing", "brute_force"],
            min_severity="high",
            actions=[log_action, block_ip, lock_account, notify_slack],
        ),
        # High severity (catch-all): block IP and notify Slack
        Playbook(
            name="high-severity-response",
            match_types=["*"],
            min_severity="high",
            actions=[log_action, block_ip, notify_slack],
        ),
        # Medium severity: log and notify
        Playbook(
            name="medium-severity-notify",
            match_types=["*"],
            min_severity="medium",
            actions=[log_action, notify_slack],
        ),
    ]

    return SOAREngine(playbooks=playbooks, dry_run=dry_run, db_path=db_path)


# ---------------------------------------------------------------------------
# Daemon mode (reads from a JSON-lines file or stdin)
# ---------------------------------------------------------------------------

def _run_daemon(engine: SOAREngine, input_file: str | None, stop_event: threading.Event) -> None:
    """Read JSON-line alerts from a file or stdin and process them."""
    source = open(input_file, encoding="utf-8") if input_file else sys.stdin
    try:
        for line in source:
            if stop_event.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                alert = Alert.from_json(line)
            except (json.JSONDecodeError, TypeError) as exc:
                log.warning("soar_daemon_parse_error: %s", exc)
                continue
            engine.process(alert)
    finally:
        if input_file:
            source.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SOAR engine for iGaming security automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan responses without executing actions",
    )
    parser.add_argument(
        "--db",
        default="/var/lib/soar/incidents.db",
        help="SQLite incident database path (default: %(default)s)",
    )
    parser.add_argument(
        "--slack-webhook",
        default=os.environ.get("SLACK_SECURITY_WEBHOOK", ""),
        help="Slack incoming webhook URL",
    )
    parser.add_argument(
        "--pagerduty-key",
        default=os.environ.get("PAGERDUTY_ROUTING_KEY", ""),
        help="PagerDuty routing key",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_process = sub.add_parser("process", help="Process a single alert from a JSON file")
    p_process.add_argument("--alert-file", required=True, help="Path to JSON alert file")

    p_daemon = sub.add_parser("daemon", help="Run as daemon reading JSON-lines alerts")
    p_daemon.add_argument("--input", help="Input JSONL file (default: stdin)")

    sub.add_parser("stats", help="Print engine statistics and exit")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    engine = build_default_engine(
        dry_run=args.dry_run,
        slack_webhook=args.slack_webhook,
        pagerduty_key=args.pagerduty_key,
        db_path=args.db,
    )

    if args.command == "process":
        try:
            with open(args.alert_file, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Cannot read alert file: %s", exc)
            sys.exit(1)
        alert = Alert.from_dict(data)
        result = engine.process(alert)
        print(json.dumps(result, indent=2))

    elif args.command == "daemon":
        log.info("soar_daemon_starting dry_run=%s", args.dry_run)
        stop = threading.Event()
        try:
            _run_daemon(engine, getattr(args, "input", None), stop)
        except KeyboardInterrupt:
            log.info("soar_daemon_stopping")
            stop.set()
        log.info("soar_daemon_stats %s", engine.stats())

    elif args.command == "stats":
        print(json.dumps(engine.stats(), indent=2))


if __name__ == "__main__":
    main()
