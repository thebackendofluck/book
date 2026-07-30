#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Slack Notifier for AcmeToCasino SOAR System.

Sends rich, structured Slack notifications for security events and alerts
with severity-aware formatting, thread-based incident tracking, and
interactive action buttons for quick response.

Features:
  - Block Kit message formatting with colour-coded severity indicators
  - Thread-based updates for ongoing incidents (avoids channel flooding)
  - Action buttons: Block IP, Whitelist IP, Escalate to PagerDuty
  - Rate limiting to respect Slack API limits
  - Fallback to plain text when Block Kit fails
  - Support for different channels by severity level

Usage as a library:
    from slack_notifier import SlackNotifier, build_notifier_from_config
    notifier = build_notifier_from_config(cfg)
    notifier.send_alert(alert_dict)
    notifier.update_incident_thread(alert_id, "IP blocked via WAF")

Usage as a CLI tool:
    python slack_notifier.py --config /etc/soar/config.yml send-test --severity high
    python slack_notifier.py --config /etc/soar/config.yml send-alert --file alert.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

import yaml


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


def _build_logger(name: str, level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger("slack_notifier")


# ---------------------------------------------------------------------------
# Severity metadata
# ---------------------------------------------------------------------------

# Maps severity level to (emoji, hex colour, Slack channel suffix)
_SEVERITY_META: dict[str, dict[str, str]] = {
    "critical": {"emoji": ":rotating_light:", "color": "#FF0000", "channel_key": "critical_channel"},
    "high":     {"emoji": ":warning:",        "color": "#FF6600", "channel_key": "default_channel"},
    "medium":   {"emoji": ":large_yellow_circle:", "color": "#FFC000", "channel_key": "default_channel"},
    "low":      {"emoji": ":information_source:", "color": "#0078D7", "channel_key": "default_channel"},
    "info":     {"emoji": ":white_circle:",   "color": "#AAAAAA", "channel_key": "info_channel"},
}

_SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


def _severity_index(severity: str) -> int:
    """Return the numeric rank of a severity level (lower = more severe)."""
    try:
        return _SEVERITY_ORDER.index(severity.lower())
    except ValueError:
        return len(_SEVERITY_ORDER)


# ---------------------------------------------------------------------------
# Block Kit message builders
# ---------------------------------------------------------------------------

def _truncate(text: str, max_len: int = 3000) -> str:
    """Truncate text to Slack's Block Kit text limit."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _format_evidence(evidence: dict[str, Any]) -> str:
    """Format alert evidence as a readable multi-line string for Slack."""
    if not evidence:
        return "_No additional evidence_"
    lines = []
    for key, value in evidence.items():
        k = key.replace("_", " ").title()
        if isinstance(value, list):
            v = ", ".join(str(i) for i in value[:10])
            if len(value) > 10:
                v += f" (+{len(value) - 10} more)"
        elif isinstance(value, float):
            v = f"{value:.2f}"
        else:
            v = str(value)
        lines.append(f"• *{k}*: {v}")
    return "\n".join(lines)


def _build_alert_blocks(alert: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build a Slack Block Kit message for a security alert.

    Args:
        alert: SOAR alert dict from the threat detector.

    Returns:
        A list of Slack Block Kit blocks.
    """
    severity = alert.get("severity", "info").lower()
    meta = _SEVERITY_META.get(severity, _SEVERITY_META["info"])
    emoji = meta["emoji"]

    alert_type = alert.get("alert_type", "unknown").replace("_", " ").title()
    source_ip = alert.get("source_ip", "unknown")
    description = alert.get("description", "No description provided.")
    alert_id = alert.get("alert_id", "")[:8]
    ts = alert.get("timestamp", datetime.now(tz=timezone.utc).isoformat())
    detector = alert.get("detector", "unknown")
    user_id = alert.get("user_id", "")
    country = alert.get("country_code", alert.get("geo_country_code", ""))
    evidence = alert.get("evidence", {})

    # Contextual metadata line
    meta_parts = [f"`{source_ip}`"]
    if country:
        meta_parts.append(f"Country: `{country}`")
    if user_id:
        meta_parts.append(f"User: `{user_id}`")
    meta_parts.append(f"Detector: `{detector}`")
    meta_parts.append(f"ID: `{alert_id}`")

    blocks: list[dict[str, Any]] = [
        # Header
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} {severity.upper()}: {alert_type}",
                "emoji": True,
            },
        },
        # Description
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate(description, 3000),
            },
        },
        # Metadata fields
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Source IP*\n{source_ip}"},
                {"type": "mrkdwn", "text": f"*Severity*\n{severity.upper()}"},
                {"type": "mrkdwn", "text": f"*Alert Type*\n{alert_type}"},
                {"type": "mrkdwn", "text": f"*Detector*\n{detector}"},
                {"type": "mrkdwn", "text": f"*Time (UTC)*\n{ts[:19].replace('T', ' ')}"},
                {"type": "mrkdwn", "text": f"*Alert ID*\n`{alert_id}`"},
            ],
        },
    ]

    # Evidence section
    if evidence:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Evidence*\n{_truncate(_format_evidence(evidence), 2000)}",
            },
        })

    # Action buttons
    action_elements: list[dict[str, Any]] = []

    if alert.get("should_block") or severity in ("critical", "high"):
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": ":no_entry: Block IP", "emoji": True},
            "style": "danger",
            "value": json.dumps({"action": "block_ip", "ip": source_ip, "alert_id": alert.get("alert_id", "")}),
            "action_id": "soar_block_ip",
        })

    action_elements.append({
        "type": "button",
        "text": {"type": "plain_text", "text": ":white_check_mark: Whitelist IP", "emoji": True},
        "value": json.dumps({"action": "whitelist_ip", "ip": source_ip, "alert_id": alert.get("alert_id", "")}),
        "action_id": "soar_whitelist_ip",
    })

    if severity in ("critical", "high"):
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": ":pager: Escalate", "emoji": True},
            "style": "primary",
            "value": json.dumps({"action": "escalate", "alert_id": alert.get("alert_id", "")}),
            "action_id": "soar_escalate",
        })

    if action_elements:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "actions",
            "elements": action_elements[:5],  # Slack limit: max 5 elements per actions block
        })

    # Footer
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": (
                    f"AcmeToCasino SOAR | {' | '.join(meta_parts)}"
                ),
            }
        ],
    })

    return blocks


def _build_thread_update_blocks(message: str, alert_id: str) -> list[dict[str, Any]]:
    """
    Build a compact Block Kit message for a thread update.

    Args:
        message:  Update message text.
        alert_id: The alert being updated.

    Returns:
        List of Slack Block Kit blocks.
    """
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":arrows_counterclockwise: *Update* | `{alert_id[:8]}` | {message}",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Updated at {datetime.now(tz=timezone.utc).strftime('%H:%M:%S UTC')}",
                }
            ],
        },
    ]


def _build_event_summary_blocks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Build a Slack Block Kit summary for a batch of low-severity events.

    Args:
        events: List of normalized SOAR events or alerts.

    Returns:
        List of Slack Block Kit blocks.
    """
    lines = []
    for ev in events[:20]:
        alert_type = ev.get("alert_type") or ev.get("event_type", "unknown")
        ip = ev.get("source_ip", "?")
        sev = ev.get("severity", "")
        meta = _SEVERITY_META.get(sev, _SEVERITY_META["info"])
        lines.append(f"{meta['emoji']} `{ip}` – {alert_type.replace('_', ' ').title()}")

    if len(events) > 20:
        lines.append(f"_...and {len(events) - 20} more_")

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Security Event Summary* – {len(events)} events\n" + "\n".join(lines),
            },
        }
    ]


# ---------------------------------------------------------------------------
# Slack API client
# ---------------------------------------------------------------------------

class SlackClient:
    """
    Minimal Slack Web API client (no external SDK dependency).

    Supports ``chat.postMessage``, ``chat.update``, and ``chat.postMessage``
    with ``thread_ts`` for thread replies.

    Args:
        bot_token: Slack Bot OAuth token (xoxb-...).
        timeout:   HTTP request timeout in seconds.
    """

    _BASE = "https://slack.com/api"

    def __init__(self, bot_token: str, timeout: float = 10.0) -> None:
        self._token = bot_token
        self._timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def post_message(
        self,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        thread_ts: str | None = None,
        color: str | None = None,
    ) -> dict[str, Any]:
        """
        Post a message to a Slack channel.

        Args:
            channel:   Channel ID or name (e.g. "#security-alerts").
            text:      Fallback plain text (required by Slack for notifications).
            blocks:    Block Kit blocks (optional).
            attachments: Legacy attachments used for coloured sidebars (optional).
            thread_ts: Parent message timestamp for thread replies.
            color:     Attachment sidebar colour (hex, e.g. "#FF0000").

        Returns:
            Slack API response dict.

        Raises:
            RuntimeError: On API errors or connection failures.
        """
        payload: dict[str, Any] = {
            "channel": channel,
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts
        if attachments:
            payload["attachments"] = attachments
        elif color:
            # Wrap blocks in a colored attachment for the sidebar accent
            payload["attachments"] = [
                {
                    "color": color,
                    "blocks": blocks or [],
                    "fallback": text,
                }
            ]
            del payload["blocks"]

        return self._call("chat.postMessage", payload)

    def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Update an existing Slack message.

        Args:
            channel: Channel ID.
            ts:      Message timestamp (from the original post_message response).
            text:    Updated fallback text.
            blocks:  Updated Block Kit blocks.

        Returns:
            Slack API response dict.
        """
        payload: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
        if blocks:
            payload["blocks"] = blocks
        return self._call("chat.update", payload)

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._BASE}/{method}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=self._headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                result: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
                if not result.get("ok"):
                    raise RuntimeError(
                        f"Slack API error on {method}: {result.get('error', 'unknown')}"
                    )
                return result
        except urllib.error.HTTPError as exc:
            body_text = exc.read(2048).decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"Slack HTTP {exc.code} on {method}: {body_text[:200]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Slack connection error on {method}: {exc}") from exc


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Token-bucket rate limiter for Slack message sending."""

    def __init__(self, rate_per_minute: int) -> None:
        self._interval = 60.0 / max(rate_per_minute, 1)
        self._lock = threading.Lock()
        self._last_send = 0.0

    def acquire(self) -> None:
        """Block until a send token is available."""
        with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_send)
            if wait > 0:
                time.sleep(wait)
            self._last_send = time.monotonic()


# ---------------------------------------------------------------------------
# Main notifier
# ---------------------------------------------------------------------------

class SlackNotifier:
    """
    High-level Slack notification manager for SOAR security events.

    Maintains a per-alert thread index so that updates to the same incident
    are posted as thread replies rather than new channel messages.

    Args:
        client:             SlackClient instance.
        default_channel:    Default Slack channel for security alerts.
        critical_channel:   Channel for critical/P1 alerts.
        info_channel:       Channel for informational events.
        min_severity:       Minimum severity level to send.
        rate_limit_per_min: Maximum Slack messages per minute.
        use_threads:        When True, updates use thread replies.
    """

    def __init__(
        self,
        client: SlackClient,
        default_channel: str = "#security-alerts",
        critical_channel: str = "#security-p1",
        info_channel: str = "#security-info",
        min_severity: str = "medium",
        rate_limit_per_min: int = 50,
        use_threads: bool = True,
    ) -> None:
        self._client = client
        self._channels = {
            "default_channel": default_channel,
            "critical_channel": critical_channel,
            "info_channel": info_channel,
        }
        self._min_severity_idx = _severity_index(min_severity)
        self._rate_limiter = _RateLimiter(rate_limit_per_min)
        self._use_threads = use_threads
        # alert_id -> {"channel": str, "ts": str}
        self._thread_map: dict[str, dict[str, str]] = {}
        self._thread_map_lock = threading.Lock()

    def send_alert(self, alert: dict[str, Any]) -> bool:
        """
        Send a security alert to the appropriate Slack channel.

        Routing is based on the alert's severity field. Alerts below the
        configured minimum severity are silently dropped.

        Args:
            alert: SOAR alert dict from the threat detector.

        Returns:
            True if the message was sent successfully.
        """
        severity = alert.get("severity", "info").lower()
        if _severity_index(severity) > self._min_severity_idx:
            log.debug("Alert severity '%s' below minimum – skipping Slack notification", severity)
            return False

        meta = _SEVERITY_META.get(severity, _SEVERITY_META["info"])
        channel_key = meta["channel_key"]
        channel = self._channels.get(channel_key, self._channels["default_channel"])

        alert_type = alert.get("alert_type", "unknown").replace("_", " ").title()
        source_ip = alert.get("source_ip", "unknown")
        fallback_text = (
            f"[{severity.upper()}] {alert_type} detected from {source_ip}: "
            f"{alert.get('description', '')[:200]}"
        )

        blocks = _build_alert_blocks(alert)
        color = meta["color"]

        self._rate_limiter.acquire()
        try:
            result = self._client.post_message(
                channel=channel,
                text=fallback_text,
                blocks=blocks,
                color=color,
            )
            ts = result.get("ts", "")
            alert_id = alert.get("alert_id", "")
            if alert_id and ts and self._use_threads:
                with self._thread_map_lock:
                    self._thread_map[alert_id] = {"channel": channel, "ts": ts}
            log.info(
                "Slack alert sent: severity=%s type=%s channel=%s ts=%s",
                severity,
                alert.get("alert_type", ""),
                channel,
                ts,
            )
            return True
        except RuntimeError as exc:
            log.error("Failed to send Slack alert: %s", exc)
            return False

    def update_incident_thread(self, alert_id: str, update_message: str) -> bool:
        """
        Post an update to the thread of an existing alert.

        Args:
            alert_id:       The alert_id of the original alert.
            update_message: Human-readable status update (e.g. "IP blocked via WAF").

        Returns:
            True if the thread reply was sent successfully.
        """
        if not self._use_threads:
            return False

        with self._thread_map_lock:
            thread_info = self._thread_map.get(alert_id)

        if not thread_info:
            log.debug("No thread found for alert_id=%s – sending as new message", alert_id)
            return False

        channel = thread_info["channel"]
        parent_ts = thread_info["ts"]
        blocks = _build_thread_update_blocks(update_message, alert_id)
        fallback = f"Update [{alert_id[:8]}]: {update_message}"

        self._rate_limiter.acquire()
        try:
            self._client.post_message(
                channel=channel,
                text=fallback,
                blocks=blocks,
                thread_ts=parent_ts,
            )
            log.info("Slack thread update sent: alert_id=%s channel=%s", alert_id[:8], channel)
            return True
        except RuntimeError as exc:
            log.error("Failed to send Slack thread update: %s", exc)
            return False

    def send_event_summary(self, events: list[dict[str, Any]], channel: str | None = None) -> bool:
        """
        Send a digest summary of multiple low-severity events.

        Args:
            events:  List of event or alert dicts.
            channel: Override channel (default: info_channel).

        Returns:
            True if the summary was sent successfully.
        """
        target = channel or self._channels["info_channel"]
        blocks = _build_event_summary_blocks(events)
        fallback = f"Security event summary: {len(events)} events"

        self._rate_limiter.acquire()
        try:
            self._client.post_message(channel=target, text=fallback, blocks=blocks)
            log.info("Sent event summary to %s (%d events)", target, len(events))
            return True
        except RuntimeError as exc:
            log.error("Failed to send event summary: %s", exc)
            return False

    def send_plain(self, channel: str, message: str) -> bool:
        """
        Send a plain-text message (for simple operational notifications).

        Args:
            channel: Target Slack channel.
            message: Plain text message body.

        Returns:
            True on success.
        """
        self._rate_limiter.acquire()
        try:
            self._client.post_message(channel=channel, text=message)
            return True
        except RuntimeError as exc:
            log.error("Failed to send plain message: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Async wrapper for queue-based delivery
# ---------------------------------------------------------------------------

class AsyncSlackNotifier:
    """
    Wraps SlackNotifier to provide non-blocking fire-and-forget sending.

    Events are enqueued and delivered by a background thread, preventing
    Slack API latency from blocking the threat detector hot path.

    Args:
        notifier:    Underlying SlackNotifier instance.
        queue_depth: Maximum queued notifications.
    """

    def __init__(self, notifier: SlackNotifier, queue_depth: int = 500) -> None:
        self._notifier = notifier
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_depth)
        self._stop = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._worker,
            name="slack-notifier-worker",
            daemon=True,
        )
        self._worker_thread.start()

    def send_alert(self, alert: dict[str, Any]) -> bool:
        """Enqueue an alert for async delivery. Returns False if queue is full."""
        try:
            self._queue.put_nowait({"_type": "alert", "data": alert})
            return True
        except queue.Full:
            log.warning("Slack notification queue full – dropping alert")
            return False

    def update_incident_thread(self, alert_id: str, message: str) -> bool:
        """Enqueue a thread update for async delivery."""
        try:
            self._queue.put_nowait({"_type": "thread_update", "alert_id": alert_id, "message": message})
            return True
        except queue.Full:
            return False

    def stop(self) -> None:
        """Stop the background worker after flushing queued notifications."""
        self._stop.set()
        self._queue.join()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                item_type = item.get("_type", "")
                if item_type == "alert":
                    self._notifier.send_alert(item["data"])
                elif item_type == "thread_update":
                    self._notifier.update_incident_thread(item["alert_id"], item["message"])
            except Exception as exc:  # noqa: BLE001
                log.error("Slack worker error: %s", exc)
            finally:
                self._queue.task_done()


# ---------------------------------------------------------------------------
# Config helpers and factory
# ---------------------------------------------------------------------------

def _resolve_env(value: str) -> str:
    pattern = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")

    def _replace(m: re.Match[str]) -> str:
        return os.environ.get(m.group(1), m.group(2) or "")

    return pattern.sub(_replace, value)


def _deep_resolve(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_resolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_resolve(v) for v in obj]
    if isinstance(obj, str):
        return _resolve_env(obj)
    return obj


def load_config(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh) or {}
    except OSError as exc:
        log.error("Cannot read config: %s", exc)
        sys.exit(1)
    except yaml.YAMLError as exc:
        log.error("Invalid YAML: %s", exc)
        sys.exit(1)
    return _deep_resolve(raw)


def build_notifier_from_config(cfg: dict[str, Any]) -> SlackNotifier:
    """
    Construct a SlackNotifier from the notifications.slack section of config.yml.

    Args:
        cfg: Fully loaded and env-resolved config dict.

    Returns:
        A configured SlackNotifier instance.
    """
    slack_cfg = cfg.get("notifications", {}).get("slack", {})
    bot_token = slack_cfg.get("bot_token", "")
    if not bot_token:
        log.warning("Slack bot_token is not configured – notifications will fail")

    client = SlackClient(bot_token=bot_token)
    return SlackNotifier(
        client=client,
        default_channel=slack_cfg.get("default_channel", "#security-alerts"),
        critical_channel=slack_cfg.get("critical_channel", "#security-p1"),
        info_channel=slack_cfg.get("info_channel", "#security-info"),
        min_severity=slack_cfg.get("min_severity", "medium"),
        rate_limit_per_min=int(slack_cfg.get("rate_limit_per_minute", 50)),
        use_threads=str(slack_cfg.get("use_threads", "true")).lower() in ("true", "1"),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AcmeToCasino SOAR Slack notifier",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        default="/etc/soar/config.yml",
        help="Path to SOAR YAML configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # send-test
    p_test = sub.add_parser("send-test", help="Send a synthetic test alert to Slack")
    p_test.add_argument(
        "--severity",
        default="high",
        choices=["critical", "high", "medium", "low", "info"],
        help="Severity level of the test alert",
    )
    p_test.add_argument(
        "--channel",
        default=None,
        help="Override target channel",
    )

    # send-alert
    p_alert = sub.add_parser("send-alert", help="Send an alert from a JSON file")
    p_alert.add_argument(
        "--file",
        required=True,
        metavar="FILE",
        help="Path to a JSON file containing a SOAR alert dict",
    )

    # send-plain
    p_plain = sub.add_parser("send-plain", help="Send a plain-text message")
    p_plain.add_argument("--channel", required=True, help="Target Slack channel")
    p_plain.add_argument("--message", required=True, help="Message text")

    return parser


def main() -> None:
    """Entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    cfg = load_config(args.config)
    log_level = args.log_level or cfg.get("system", {}).get("log_level", "INFO")
    logging.getLogger().setLevel(getattr(logging, log_level.upper(), logging.INFO))

    notifier = build_notifier_from_config(cfg)

    if args.command == "send-test":
        test_alert = {
            "alert_id": "test-" + datetime.now(tz=timezone.utc).strftime("%H%M%S"),
            "schema_version": "1.0",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "detector": "test",
            "alert_type": "brute_force",
            "severity": args.severity,
            "source_ip": "203.0.113.42",
            "description": (
                f"[TEST] This is a synthetic {args.severity.upper()} alert from "
                "the AcmeToCasino SOAR Slack notifier. No action required."
            ),
            "evidence": {
                "failure_count": 15,
                "window_seconds": 300,
                "test": True,
            },
            "should_block": args.severity in ("critical", "high"),
            "user_id": "player_12345",
            "country_code": "RU",
        }
        if args.channel:
            # Temporarily override channel routing by patching config
            notifier._channels["critical_channel"] = args.channel
            notifier._channels["default_channel"] = args.channel
            notifier._channels["info_channel"] = args.channel

        ok = notifier.send_alert(test_alert)
        log.info("Test alert sent: %s", ok)
        sys.exit(0 if ok else 1)

    elif args.command == "send-alert":
        try:
            with open(args.file, encoding="utf-8") as fh:
                alert = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Cannot read alert file %s: %s", args.file, exc)
            sys.exit(1)
        ok = notifier.send_alert(alert)
        sys.exit(0 if ok else 1)

    elif args.command == "send-plain":
        ok = notifier.send_plain(args.channel, args.message)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
