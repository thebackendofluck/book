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
alert-manager.py - Suricata alert routing daemon for iGaming IDS deployments.

Tails /var/log/suricata/eve.json and routes alerts to Slack channels or
PagerDuty based on SID range and severity. Enforces per-SID rate limiting
(max 10 alerts / SID / 5 minutes) and indexes all alerts in Elasticsearch.

Environment variables:
    SLACK_WEBHOOK_URL   - Default Slack incoming webhook (fallback)
    PAGERDUTY_KEY       - PagerDuty Events API v2 integration key
    ELASTIC_HOST        - Elasticsearch base URL (default: http://localhost:9200)
    ELASTIC_PASSWORD    - Elasticsearch password (user: elastic)
"""

import json
import logging
import os
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from threading import Event
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("alert-manager")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EVE_LOG = "/var/log/suricata/eve.json"
POLL_INTERVAL = 0.5  # seconds between tail polls

RATE_LIMIT_MAX = 10        # max alerts per SID in the window
RATE_LIMIT_WINDOW = 300    # window size in seconds (5 minutes)

ELASTIC_INDEX = "suricata-alerts"

# SID range → Slack channel mapping
# Ranges are inclusive on both ends.
CHANNEL_MAP = [
    (9_000_001, 9_000_099, "#fraud-alerts"),       # payment fraud
    (9_000_100, 9_000_199, "#security-ops"),        # API abuse
    (9_000_200, 9_000_299, "#security-ops"),        # account security
    (9_000_300, 9_000_399, "#aml-compliance"),      # AML
    (9_000_400, 9_000_499, "#compliance-alerts"),   # regulatory compliance
]

SLACK_WEBHOOKS: dict[str, str] = {
    "#fraud-alerts":      os.environ.get("SLACK_WEBHOOK_FRAUD", ""),
    "#security-ops":      os.environ.get("SLACK_WEBHOOK_SECOPS", ""),
    "#aml-compliance":    os.environ.get("SLACK_WEBHOOK_AML", ""),
    "#compliance-alerts": os.environ.get("SLACK_WEBHOOK_COMPLIANCE", ""),
}

# Fall back to the generic webhook for all channels if specific ones are unset
_DEFAULT_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")
for _k in SLACK_WEBHOOKS:
    if not SLACK_WEBHOOKS[_k]:
        SLACK_WEBHOOKS[_k] = _DEFAULT_WEBHOOK

PAGERDUTY_KEY = os.environ.get("PAGERDUTY_KEY", "")
PAGERDUTY_URL = "https://events.pagerduty.com/v2/enqueue"

ELASTIC_HOST = os.environ.get("ELASTIC_HOST", "http://localhost:9200")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD", "")
ELASTIC_USER = "elastic"

# Shutdown event set by SIGTERM handler
_shutdown = Event()


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _handle_sigterm(signum, frame):  # noqa: ANN001
    log.info("Received SIGTERM – initiating graceful shutdown")
    _shutdown.set()


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Sliding-window rate limiter keyed on SID."""

    def __init__(self, max_events: int, window_seconds: int) -> None:
        self._max = max_events
        self._window = window_seconds
        # sid -> list[timestamp]
        self._buckets: dict[int, list[float]] = defaultdict(list)

    def is_allowed(self, sid: int) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        bucket = self._buckets[sid]
        # Evict expired timestamps
        self._buckets[sid] = [t for t in bucket if t > cutoff]
        if len(self._buckets[sid]) >= self._max:
            return False
        self._buckets[sid].append(now)
        return True


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def channel_for_sid(sid: int) -> Optional[str]:
    for lo, hi, channel in CHANNEL_MAP:
        if lo <= sid <= hi:
            return channel
    return None


def is_critical(alert: dict) -> bool:
    severity = alert.get("alert", {}).get("severity", 99)
    # Suricata severity: 1 = high, 2 = medium, 3 = low
    return severity == 1


# ---------------------------------------------------------------------------
# Notification dispatchers
# ---------------------------------------------------------------------------

def _post_slack(webhook: str, payload: dict) -> None:
    if not webhook:
        log.warning("Slack webhook not configured – dropping notification")
        return
    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Slack POST failed: %s", exc)


def send_slack_alert(channel: str, alert: dict) -> None:
    webhook = SLACK_WEBHOOKS.get(channel, _DEFAULT_WEBHOOK)
    event_type = alert.get("alert", {}).get("category", "Unknown")
    signature = alert.get("alert", {}).get("signature", "")
    sid = alert.get("alert", {}).get("signature_id", 0)
    severity = alert.get("alert", {}).get("severity", 99)
    src_ip = alert.get("src_ip", "?")
    dest_ip = alert.get("dest_ip", "?")
    proto = alert.get("proto", "?")
    timestamp = alert.get("timestamp", "")

    severity_label = {1: ":red_circle: CRITICAL", 2: ":large_yellow_circle: MEDIUM", 3: ":large_green_circle: LOW"}.get(severity, ":white_circle: UNKNOWN")

    payload = {
        "channel": channel,
        "username": "Suricata IDS",
        "icon_emoji": ":shield:",
        "attachments": [
            {
                "color": "#FF0000" if severity == 1 else "#FFA500" if severity == 2 else "#36a64f",
                "title": f"[SID {sid}] {signature}",
                "fields": [
                    {"title": "Category",  "value": event_type,     "short": True},
                    {"title": "Severity",  "value": severity_label, "short": True},
                    {"title": "Source",    "value": src_ip,         "short": True},
                    {"title": "Dest",      "value": dest_ip,        "short": True},
                    {"title": "Protocol",  "value": proto,          "short": True},
                    {"title": "Timestamp", "value": timestamp,      "short": True},
                ],
                "footer": "iGaming IDS | Suricata",
            }
        ],
    }
    log.info("Routing SID %d → %s (severity %d)", sid, channel, severity)
    _post_slack(webhook, payload)


def send_pagerduty_alert(alert: dict) -> None:
    if not PAGERDUTY_KEY:
        log.warning("PAGERDUTY_KEY not set – skipping PagerDuty escalation")
        return

    sid = alert.get("alert", {}).get("signature_id", 0)
    signature = alert.get("alert", {}).get("signature", "Unknown")
    src_ip = alert.get("src_ip", "?")
    dest_ip = alert.get("dest_ip", "?")

    payload = {
        "routing_key": PAGERDUTY_KEY,
        "event_action": "trigger",
        "dedup_key": f"suricata-sid-{sid}-{src_ip}",
        "payload": {
            "summary": f"[CRITICAL] Suricata SID {sid}: {signature}",
            "source": src_ip,
            "severity": "critical",
            "timestamp": alert.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "custom_details": {
                "sid": sid,
                "src_ip": src_ip,
                "dest_ip": dest_ip,
                "proto": alert.get("proto", "?"),
                "category": alert.get("alert", {}).get("category", ""),
                "raw": json.dumps(alert),
            },
        },
        "client": "Suricata IDS – iGaming",
    }

    try:
        resp = requests.post(PAGERDUTY_URL, json=payload, timeout=10)
        resp.raise_for_status()
        log.info("PagerDuty alert triggered for SID %d from %s", sid, src_ip)
    except requests.RequestException as exc:
        log.error("PagerDuty POST failed: %s", exc)


# ---------------------------------------------------------------------------
# Elasticsearch indexer
# ---------------------------------------------------------------------------

def index_in_elasticsearch(alert: dict) -> None:
    if not ELASTIC_HOST:
        return

    ts = alert.get("timestamp", datetime.now(timezone.utc).isoformat())
    # Build index name with date suffix for ILM compatibility
    date_suffix = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    index = f"{ELASTIC_INDEX}-{date_suffix}"
    url = f"{ELASTIC_HOST}/{index}/_doc"

    doc = {
        "@timestamp": ts,
        "sid": alert.get("alert", {}).get("signature_id"),
        "signature": alert.get("alert", {}).get("signature"),
        "category": alert.get("alert", {}).get("category"),
        "severity": alert.get("alert", {}).get("severity"),
        "src_ip": alert.get("src_ip"),
        "dest_ip": alert.get("dest_ip"),
        "src_port": alert.get("src_port"),
        "dest_port": alert.get("dest_port"),
        "proto": alert.get("proto"),
        "action": alert.get("alert", {}).get("action"),
        "raw": alert,
    }

    auth = (ELASTIC_USER, ELASTIC_PASSWORD) if ELASTIC_PASSWORD else None
    try:
        resp = requests.post(url, json=doc, auth=auth, timeout=5)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Elasticsearch index failed: %s", exc)


# ---------------------------------------------------------------------------
# EVE log tailer
# ---------------------------------------------------------------------------

def tail_eve(path: str):
    """Generator that yields new lines appended to *path* (tail -F semantics)."""
    log.info("Opening EVE log: %s", path)
    try:
        fh = open(path, "r")  # noqa: WPS515
    except FileNotFoundError:
        log.error("EVE log not found: %s – waiting for file to appear", path)
        while not _shutdown.is_set():
            time.sleep(2)
            if os.path.exists(path):
                fh = open(path, "r")  # noqa: WPS515
                break
        else:
            return

    # Seek to end on startup to avoid replaying historical events
    fh.seek(0, 2)
    log.info("Tailing %s from current EOF", path)

    try:
        while not _shutdown.is_set():
            line = fh.readline()
            if not line:
                # Check if file has been rotated
                try:
                    if os.stat(path).st_ino != os.fstat(fh.fileno()).st_ino:
                        log.info("EVE log rotated – reopening")
                        fh.close()
                        fh = open(path, "r")  # noqa: WPS515
                except OSError:
                    pass
                time.sleep(POLL_INTERVAL)
                continue
            yield line.rstrip("\n")
    finally:
        fh.close()


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def process_alert(alert: dict, rate_limiter: RateLimiter) -> None:
    if alert.get("event_type") != "alert":
        return

    sid: int = alert.get("alert", {}).get("signature_id", 0)
    if sid == 0:
        return

    # Always index regardless of rate-limit so dashboards stay accurate
    index_in_elasticsearch(alert)

    if not rate_limiter.is_allowed(sid):
        log.debug("Rate limit reached for SID %d – suppressing notification", sid)
        return

    channel = channel_for_sid(sid)
    if channel:
        send_slack_alert(channel, alert)

    if is_critical(alert):
        send_pagerduty_alert(alert)


def main() -> None:
    log.info("alert-manager starting (PID %d)", os.getpid())

    rate_limiter = RateLimiter(
        max_events=RATE_LIMIT_MAX,
        window_seconds=RATE_LIMIT_WINDOW,
    )

    for raw_line in tail_eve(EVE_LOG):
        if _shutdown.is_set():
            break
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            log.debug("Non-JSON line skipped: %s", raw_line[:120])
            continue
        try:
            process_alert(event, rate_limiter)
        except Exception as exc:  # noqa: BLE001
            log.exception("Unhandled error processing alert: %s", exc)

    log.info("alert-manager stopped")


if __name__ == "__main__":
    main()
