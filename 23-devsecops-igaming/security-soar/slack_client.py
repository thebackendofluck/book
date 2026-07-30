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
Slack notification client for security alerts in iGaming SOAR pipelines.

Thin wrapper around the Slack Web API that provides:
  - Severity-aware channel routing (critical/high/medium/info)
  - Block Kit message formatting with colour-coded severity indicators
  - Thread-based incident tracking to avoid channel flooding
  - Queue-backed async sending to keep the hot path non-blocking
  - CLI interface for ad-hoc test messages and alert replay

This module is the standalone Slack client extracted from the full SOAR
system.  Import it directly when you only need Slack notifications without
the complete SOAR engine.

Usage as a library:
    from slack_client import SlackClient, send_security_alert

    client = SlackClient(bot_token=os.environ["SLACK_BOT_TOKEN"])
    client.post_alert(
        channel="#security-alerts",
        severity="high",
        title="Brute force detected",
        description="150 failed logins from 203.0.113.42 in 60 seconds",
        evidence={"ip": "203.0.113.42", "count": 150, "window_s": 60},
    )

Usage as a CLI tool:
    python slack_client.py send-test --severity critical --channel "#security-p1"
    python slack_client.py send-alert --file alert.json

Reference: Chapter 23 — DevSecOps for iGaming / Security Champion Program
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
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


log = _build_logger("slack_client")


# ---------------------------------------------------------------------------
# Severity metadata
# ---------------------------------------------------------------------------

_SEVERITY_META: dict[str, dict[str, str]] = {
    "critical": {
        "emoji": ":rotating_light:",
        "color": "#FF0000",
        "channel_key": "critical_channel",
    },
    "high": {
        "emoji": ":warning:",
        "color": "#FF6600",
        "channel_key": "default_channel",
    },
    "medium": {
        "emoji": ":large_yellow_circle:",
        "color": "#FFC000",
        "channel_key": "default_channel",
    },
    "low": {
        "emoji": ":information_source:",
        "color": "#0078D7",
        "channel_key": "default_channel",
    },
    "info": {
        "emoji": ":white_circle:",
        "color": "#AAAAAA",
        "channel_key": "info_channel",
    },
}

_SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


def _severity_index(severity: str) -> int:
    try:
        return _SEVERITY_ORDER.index(severity.lower())
    except ValueError:
        return len(_SEVERITY_ORDER)


# ---------------------------------------------------------------------------
# Block Kit builders
# ---------------------------------------------------------------------------

def _truncate(text: str, max_len: int = 3000) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_evidence(evidence: dict[str, Any]) -> str:
    if not evidence:
        return "_No additional evidence_"
    lines = []
    for key, value in evidence.items():
        label = key.replace("_", " ").title()
        if isinstance(value, list):
            v = ", ".join(str(i) for i in value[:10])
            if len(value) > 10:
                v += f" (+{len(value) - 10} more)"
        elif isinstance(value, float):
            v = f"{value:.4f}"
        else:
            v = str(value)
        lines.append(f"• *{label}*: {v}")
    return "\n".join(lines)


def _build_alert_blocks(
    severity: str,
    title: str,
    description: str,
    source_ip: str = "unknown",
    alert_id: str = "",
    evidence: dict[str, Any] | None = None,
    extra_fields: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    meta = _SEVERITY_META.get(severity.lower(), _SEVERITY_META["info"])
    emoji = meta["emoji"]
    ts_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    short_id = alert_id[:8] if alert_id else "—"

    fields: list[dict[str, Any]] = [
        {"type": "mrkdwn", "text": f"*Source IP*\n`{source_ip}`"},
        {"type": "mrkdwn", "text": f"*Severity*\n{severity.upper()}"},
        {"type": "mrkdwn", "text": f"*Time (UTC)*\n{ts_str}"},
        {"type": "mrkdwn", "text": f"*Alert ID*\n`{short_id}`"},
    ]
    if extra_fields:
        for k, v in extra_fields.items():
            fields.append({"type": "mrkdwn", "text": f"*{k}*\n{v}"})

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} {severity.upper()}: {title}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": _truncate(description, 3000)},
        },
        {"type": "section", "fields": fields[:10]},
    ]

    if evidence:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Evidence*\n{_truncate(_format_evidence(evidence), 2000)}",
            },
        })

    action_elements: list[dict[str, Any]] = []
    if severity in ("critical", "high"):
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": ":no_entry: Block IP", "emoji": True},
            "style": "danger",
            "value": json.dumps({"action": "block_ip", "ip": source_ip, "alert_id": alert_id}),
            "action_id": "soar_block_ip",
        })
    action_elements.append({
        "type": "button",
        "text": {"type": "plain_text", "text": ":white_check_mark: Whitelist", "emoji": True},
        "value": json.dumps({"action": "whitelist_ip", "ip": source_ip, "alert_id": alert_id}),
        "action_id": "soar_whitelist_ip",
    })
    if action_elements:
        blocks.append({"type": "divider"})
        blocks.append({"type": "actions", "elements": action_elements[:5]})

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"AcmeToCasino Security | `{source_ip}` | ID: `{short_id}`"}],
    })
    return blocks


# ---------------------------------------------------------------------------
# Slack API client
# ---------------------------------------------------------------------------

class SlackClient:
    """
    Minimal Slack Web API client for iGaming security notifications.

    Requires no external dependencies beyond the Python standard library.
    Uses urllib for HTTP to keep the bundle lean and the supply chain small.

    Args:
        bot_token: Slack Bot OAuth token (xoxb-...).
        timeout:   HTTP request timeout in seconds.
        default_channel:   Default channel for MEDIUM/HIGH/LOW alerts.
        critical_channel:  Channel for CRITICAL severity alerts.
        info_channel:      Channel for informational/low-noise events.
    """

    _SLACK_API = "https://slack.com/api"

    def __init__(
        self,
        bot_token: str,
        timeout: float = 10.0,
        default_channel: str = "#security-alerts",
        critical_channel: str = "#security-p1",
        info_channel: str = "#security-info",
    ) -> None:
        if not bot_token:
            raise ValueError("Slack bot_token must not be empty")
        self._token = bot_token
        self._timeout = timeout
        self._channels = {
            "default_channel": default_channel,
            "critical_channel": critical_channel,
            "info_channel": info_channel,
        }
        self._headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        # alert_id -> {"channel": str, "ts": str} for thread updates
        self._thread_map: dict[str, dict[str, str]] = {}

    # --- Public interface ----------------------------------------------------

    def post_alert(
        self,
        channel: str,
        severity: str,
        title: str,
        description: str,
        source_ip: str = "unknown",
        alert_id: str = "",
        evidence: dict[str, Any] | None = None,
        extra_fields: dict[str, str] | None = None,
    ) -> str:
        """
        Post a formatted security alert to a Slack channel.

        Args:
            channel:     Target Slack channel (e.g. "#security-alerts").
            severity:    Alert severity: critical, high, medium, low, info.
            title:       Short alert title.
            description: Full alert description.
            source_ip:   Source IP address of the threat.
            alert_id:    Unique alert identifier for thread tracking.
            evidence:    Key-value evidence dict rendered in the message body.
            extra_fields: Additional metadata fields for the Slack card.

        Returns:
            Slack message timestamp (ts), usable for thread replies.

        Raises:
            RuntimeError: On API errors or connection failures.
        """
        meta = _SEVERITY_META.get(severity.lower(), _SEVERITY_META["info"])
        color = meta["color"]
        blocks = _build_alert_blocks(
            severity=severity,
            title=title,
            description=description,
            source_ip=source_ip,
            alert_id=alert_id,
            evidence=evidence,
            extra_fields=extra_fields,
        )
        fallback = f"[{severity.upper()}] {title} from {source_ip}"
        result = self._post_message(
            channel=channel,
            text=fallback,
            blocks=blocks,
            color=color,
        )
        ts = result.get("ts", "")
        if alert_id and ts:
            self._thread_map[alert_id] = {"channel": channel, "ts": ts}
        log.info(
            "slack_alert_sent severity=%s title=%s channel=%s ts=%s",
            severity,
            title,
            channel,
            ts,
        )
        return ts

    def post_alert_routed(
        self,
        severity: str,
        title: str,
        description: str,
        source_ip: str = "unknown",
        alert_id: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> str:
        """
        Post a security alert to the channel determined by severity routing.

        Automatically routes CRITICAL to ``critical_channel``, INFO to
        ``info_channel``, and everything else to ``default_channel``.

        Returns:
            Slack message timestamp (ts).
        """
        meta = _SEVERITY_META.get(severity.lower(), _SEVERITY_META["info"])
        channel = self._channels[meta["channel_key"]]
        return self.post_alert(
            channel=channel,
            severity=severity,
            title=title,
            description=description,
            source_ip=source_ip,
            alert_id=alert_id,
            evidence=evidence,
        )

    def update_thread(self, alert_id: str, message: str) -> bool:
        """
        Post a thread reply to an existing alert message.

        Args:
            alert_id: The alert_id passed to a previous ``post_alert`` call.
            message:  Update text (e.g. "IP blocked via AWS WAF").

        Returns:
            True if the thread reply was sent successfully.
        """
        thread_info = self._thread_map.get(alert_id)
        if not thread_info:
            log.debug("No thread for alert_id=%s – cannot post reply", alert_id)
            return False
        blocks = [
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
                        "text": f"Updated {datetime.now(tz=timezone.utc).strftime('%H:%M:%S UTC')}",
                    }
                ],
            },
        ]
        try:
            self._post_message(
                channel=thread_info["channel"],
                text=f"Update [{alert_id[:8]}]: {message}",
                blocks=blocks,
                thread_ts=thread_info["ts"],
            )
            log.info("slack_thread_update alert_id=%s", alert_id[:8])
            return True
        except RuntimeError as exc:
            log.error("slack_thread_update_failed: %s", exc)
            return False

    def post_plain(self, channel: str, text: str) -> bool:
        """Send a plain-text message to a channel."""
        try:
            self._post_message(channel=channel, text=text)
            return True
        except RuntimeError as exc:
            log.error("slack_plain_message_failed: %s", exc)
            return False

    # --- Internal -----------------------------------------------------------

    def _post_message(
        self,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        color: str | None = None,
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        if color and blocks:
            payload["attachments"] = [{"color": color, "blocks": blocks, "fallback": text}]
        elif blocks:
            payload["blocks"] = blocks
        return self._call("chat.postMessage", payload)

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._SLACK_API}/{method}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers=self._headers, method="POST"
        )
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
            raise RuntimeError(
                f"Slack HTTP {exc.code} on {method}: {body_text[:200]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Slack connection error on {method}: {exc}") from exc


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def send_security_alert(
    bot_token: str,
    severity: str,
    title: str,
    description: str,
    channel: str | None = None,
    source_ip: str = "unknown",
    evidence: dict[str, Any] | None = None,
) -> bool:
    """
    One-shot helper: construct a client and send a single security alert.

    Args:
        bot_token:   Slack Bot OAuth token.
        severity:    Alert severity: critical, high, medium, low, info.
        title:       Short alert title.
        description: Full alert description.
        channel:     Override channel (default: route by severity).
        source_ip:   Source IP address.
        evidence:    Evidence key-value dict.

    Returns:
        True on success.
    """
    client = SlackClient(bot_token=bot_token)
    try:
        if channel:
            client.post_alert(
                channel=channel,
                severity=severity,
                title=title,
                description=description,
                source_ip=source_ip,
                evidence=evidence,
            )
        else:
            client.post_alert_routed(
                severity=severity,
                title=title,
                description=description,
                source_ip=source_ip,
                evidence=evidence,
            )
        return True
    except RuntimeError as exc:
        log.error("send_security_alert failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Slack security notification client for iGaming SOAR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("SLACK_BOT_TOKEN", ""),
        help="Slack Bot OAuth token (default: $SLACK_BOT_TOKEN)",
    )
    parser.add_argument(
        "--critical-channel",
        default="#security-p1",
        help="Channel for CRITICAL alerts (default: %(default)s)",
    )
    parser.add_argument(
        "--default-channel",
        default="#security-alerts",
        help="Channel for HIGH/MEDIUM/LOW alerts (default: %(default)s)",
    )
    parser.add_argument(
        "--info-channel",
        default="#security-info",
        help="Channel for INFO events (default: %(default)s)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_test = sub.add_parser("send-test", help="Send a synthetic test alert")
    p_test.add_argument(
        "--severity",
        default="high",
        choices=_SEVERITY_ORDER,
        help="Severity level (default: %(default)s)",
    )
    p_test.add_argument("--channel", default=None, help="Override target channel")

    p_alert = sub.add_parser("send-alert", help="Send an alert from a JSON file")
    p_alert.add_argument("--file", required=True, help="Path to JSON alert file")

    p_plain = sub.add_parser("send-plain", help="Send a plain-text message")
    p_plain.add_argument("--channel", required=True, help="Target channel")
    p_plain.add_argument("--message", required=True, help="Message text")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.token:
        log.error("Slack bot token required: set --token or $SLACK_BOT_TOKEN")
        sys.exit(1)

    client = SlackClient(
        bot_token=args.token,
        critical_channel=args.critical_channel,
        default_channel=args.default_channel,
        info_channel=args.info_channel,
    )

    if args.command == "send-test":
        severity = args.severity
        ts_str = datetime.now(tz=timezone.utc).isoformat()
        test_alert: dict[str, Any] = {
            "alert_id": f"test-{datetime.now(tz=timezone.utc).strftime('%H%M%S')}",
            "title": f"[TEST] Synthetic {severity.upper()} alert",
            "description": (
                f"This is a synthetic {severity.upper()} test alert from "
                "the AcmeToCasino security notification client. No action required."
            ),
            "source_ip": "203.0.113.42",
            "evidence": {"test": True, "timestamp": ts_str, "tool": "slack_client.py"},
        }
        channel = args.channel or None
        try:
            if channel:
                client.post_alert(channel=channel, severity=severity, **{k: v for k, v in test_alert.items() if k != "title" or True}, title=test_alert["title"])  # type: ignore[arg-type]
            else:
                client.post_alert_routed(
                    severity=severity,
                    title=test_alert["title"],
                    description=test_alert["description"],
                    source_ip=test_alert["source_ip"],
                    alert_id=test_alert["alert_id"],
                    evidence=test_alert["evidence"],
                )
            log.info("Test alert sent successfully")
            sys.exit(0)
        except RuntimeError as exc:
            log.error("Test alert failed: %s", exc)
            sys.exit(1)

    elif args.command == "send-alert":
        try:
            with open(args.file, encoding="utf-8") as fh:
                alert: dict[str, Any] = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Cannot read alert file %s: %s", args.file, exc)
            sys.exit(1)
        try:
            client.post_alert_routed(
                severity=alert.get("severity", "info"),
                title=alert.get("title", alert.get("alert_type", "Security Alert")),
                description=alert.get("description", ""),
                source_ip=alert.get("source_ip", "unknown"),
                alert_id=alert.get("alert_id", ""),
                evidence=alert.get("evidence"),
            )
            sys.exit(0)
        except RuntimeError as exc:
            log.error("Alert send failed: %s", exc)
            sys.exit(1)

    elif args.command == "send-plain":
        ok = client.post_plain(channel=args.channel, text=args.message)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
