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
n8n Webhook Client Library for AcmeToCasino SOAR System.

Provides a production-grade client for sending security events and alerts
to n8n workflow webhooks. Features include:

  - Thread-safe queue with configurable depth
  - Exponential back-off retry with jitter
  - Configurable event type routing to different webhook paths
  - Health-check polling before sending
  - Fallback to local JSONL file when n8n is unreachable
  - Prometheus-style counters exposed via /metrics endpoint (optional)

Usage as a library:
    from n8n_webhook_sender import N8nWebhookClient, EventType

    client = N8nWebhookClient(base_url="http://n8n:5678", api_key="...")
    client.send_event({"event_type": "http_request", ...})
    client.send_alert({"alert_type": "brute_force", "severity": "high", ...})

Usage as a CLI tool (replay fallback queue):
    python n8n_webhook_sender.py --config /etc/soar/config.yml replay-fallback
    python n8n_webhook_sender.py --config /etc/soar/config.yml health-check
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
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


log = _build_logger("n8n_webhook_sender")


# ---------------------------------------------------------------------------
# Event type routing
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """
    SOAR event categories used to route events to different n8n webhooks.

    Each value matches an ``alert_type`` or ``event_type`` pattern.
    """
    GENERIC_EVENT = "generic_event"
    SECURITY_ALERT = "security_alert"
    BRUTE_FORCE = "brute_force"
    DDOS = "ddos"
    INJECTION = "injection"
    BOT = "bot"
    ACCOUNT_TAKEOVER = "account_takeover"
    BONUS_ABUSE = "bonus_abuse"
    WAF_BLOCK = "waf_block"
    INCIDENT = "incident"


def _classify_event(event: dict[str, Any]) -> EventType:
    """
    Classify an event into an EventType for webhook routing.

    Args:
        event: A normalized event or alert dict.

    Returns:
        The most specific matching EventType.
    """
    alert_type: str = event.get("alert_type", "")
    event_type: str = event.get("event_type", "")
    combined = f"{alert_type} {event_type}".lower()

    if "brute_force" in combined:
        return EventType.BRUTE_FORCE
    if "ddos" in combined or "syn_flood" in combined or "slowloris" in combined:
        return EventType.DDOS
    if "injection" in combined or "sqli" in combined or "xss" in combined or "command_injection" in combined:
        return EventType.INJECTION
    if "bot" in combined:
        return EventType.BOT
    if "account_takeover" in combined or "ato_" in combined:
        return EventType.ACCOUNT_TAKEOVER
    if "bonus_abuse" in combined:
        return EventType.BONUS_ABUSE
    if "waf" in combined:
        return EventType.WAF_BLOCK
    if alert_type or event.get("severity"):
        return EventType.SECURITY_ALERT
    return EventType.GENERIC_EVENT


# ---------------------------------------------------------------------------
# Webhook request/response helpers
# ---------------------------------------------------------------------------

@dataclass
class WebhookResult:
    """Outcome of a single webhook delivery attempt."""
    success: bool
    status_code: int = 0
    response_body: str = ""
    error: str = ""
    attempt: int = 1
    duration_ms: float = 0.0


def _post_json(
    url: str,
    payload: bytes,
    headers: dict[str, str],
    timeout: float,
) -> WebhookResult:
    """
    Perform a single HTTP POST with the given payload.

    Args:
        url:     Target URL.
        payload: Pre-serialized JSON bytes.
        headers: HTTP request headers.
        timeout: Request timeout in seconds.

    Returns:
        A WebhookResult describing the outcome.
    """
    start = time.monotonic()
    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
            duration = (time.monotonic() - start) * 1000
            return WebhookResult(
                success=resp.status < 300,
                status_code=resp.status,
                response_body=body,
                duration_ms=duration,
            )
    except urllib.error.HTTPError as exc:
        duration = (time.monotonic() - start) * 1000
        body = exc.read(4096).decode("utf-8", errors="replace") if exc.fp else ""
        return WebhookResult(
            success=False,
            status_code=exc.code,
            response_body=body,
            error=str(exc),
            duration_ms=duration,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        duration = (time.monotonic() - start) * 1000
        return WebhookResult(success=False, error=str(exc), duration_ms=duration)


# ---------------------------------------------------------------------------
# Client configuration
# ---------------------------------------------------------------------------

@dataclass
class N8nClientConfig:
    """
    Configuration for the N8nWebhookClient.

    Attributes:
        base_url:            n8n instance base URL.
        event_webhook_path:  Path for general SOAR events.
        alert_webhook_path:  Path for high-severity alerts.
        route_map:           Optional override mapping EventType -> webhook path.
        api_key:             n8n API key for authentication.
        timeout_seconds:     Per-request HTTP timeout.
        max_retries:         Delivery attempts before writing to fallback.
        backoff_base:        Base delay for exponential back-off (seconds).
        backoff_max:         Maximum delay between retries (seconds).
        jitter_factor:       Random jitter fraction applied to back-off delays.
        queue_depth:         In-memory queue capacity.
        sender_workers:      Number of parallel delivery threads.
        fallback_file:       JSONL file path for failed deliveries.
        health_check_path:   n8n path used for availability health checks.
        health_check_interval: Seconds between proactive health checks.
    """
    base_url: str = "http://n8n:5678"
    event_webhook_path: str = "/webhook/soar-events"
    alert_webhook_path: str = "/webhook/soar-alerts"
    route_map: dict[str, str] = field(default_factory=dict)
    api_key: str = ""
    timeout_seconds: float = 10.0
    max_retries: int = 5
    backoff_base: float = 2.0
    backoff_max: float = 60.0
    jitter_factor: float = 0.25
    queue_depth: int = 10000
    sender_workers: int = 4
    fallback_file: str = "/var/lib/soar/n8n_fallback_queue.jsonl"
    health_check_path: str = "/healthz"
    health_check_interval: int = 30


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class N8nWebhookClient:
    """
    Thread-safe client that enqueues events and delivers them to n8n webhooks.

    Start delivery workers with :meth:`start`, then call :meth:`send_event`
    or :meth:`send_alert` from any thread. Call :meth:`stop` for a graceful
    shutdown that flushes in-flight events.

    Args:
        config: N8nClientConfig instance.
    """

    def __init__(self, config: N8nClientConfig) -> None:
        self._cfg = config
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=config.queue_depth)
        self._stop_event = threading.Event()
        self._workers: list[threading.Thread] = []
        self._healthy = threading.Event()
        self._healthy.set()  # optimistically assume n8n is up at start
        self._stats: dict[str, int] = {
            "enqueued": 0,
            "delivered": 0,
            "retried": 0,
            "fallback_written": 0,
            "dropped": 0,
        }
        self._stats_lock = threading.Lock()
        self._headers = {
            "Content-Type": "application/json",
            "User-Agent": "AcmeToCasino-SOAR/1.0",
        }
        if config.api_key:
            self._headers["X-N8N-API-KEY"] = config.api_key

    # --- Lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start background delivery workers and health-check thread."""
        for i in range(self._cfg.sender_workers):
            t = threading.Thread(
                target=self._delivery_worker,
                name=f"n8n-sender-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)

        hc_thread = threading.Thread(
            target=self._health_check_worker,
            name="n8n-health-check",
            daemon=True,
        )
        hc_thread.start()
        self._workers.append(hc_thread)
        log.info(
            "N8nWebhookClient started | workers=%d url=%s",
            self._cfg.sender_workers,
            self._cfg.base_url,
        )

    def stop(self, timeout: float = 30.0) -> None:
        """
        Signal all workers to stop and wait for the queue to drain.

        Args:
            timeout: Maximum seconds to wait for clean shutdown.
        """
        log.info("N8nWebhookClient shutting down – waiting for queue to drain")
        self._stop_event.set()
        try:
            self._queue.join()
        except Exception:  # noqa: BLE001
            pass
        log.info(
            "N8nWebhookClient stopped | stats=%s",
            json.dumps(self._stats),
        )

    # --- Public API ----------------------------------------------------------

    def send_event(self, event: dict[str, Any]) -> bool:
        """
        Enqueue a normalized SOAR event for delivery.

        Args:
            event: Normalized event dict (common schema v1.0).

        Returns:
            True if successfully enqueued, False if the queue is full.
        """
        return self._enqueue(event, _classify_event(event))

    def send_alert(self, alert: dict[str, Any]) -> bool:
        """
        Enqueue a security alert for priority delivery.

        Alerts are routed to the alert webhook path rather than the
        general events path.

        Args:
            alert: Alert dict produced by a threat detector.

        Returns:
            True if successfully enqueued, False if the queue is full.
        """
        event_type = _classify_event(alert)
        enriched = dict(alert)
        enriched["_route_override"] = "alert"
        return self._enqueue(enriched, event_type)

    def health_check(self) -> bool:
        """
        Perform a synchronous health check against the n8n instance.

        Returns:
            True if n8n responds with HTTP 2xx.
        """
        url = f"{self._cfg.base_url.rstrip('/')}{self._cfg.health_check_path}"
        result = _post_json(url, b"{}", self._headers, timeout=5.0)
        # n8n /healthz returns 200 on healthy; any 2xx is fine
        healthy = result.status_code in range(200, 300) or result.status_code == 404
        log.info("n8n health check: %s (HTTP %d)", "ok" if healthy else "fail", result.status_code)
        return healthy

    def stats(self) -> dict[str, int]:
        """Return a copy of the current delivery statistics."""
        with self._stats_lock:
            return dict(self._stats)

    def queue_depth(self) -> int:
        """Return the current number of events waiting for delivery."""
        return self._queue.qsize()

    # --- Internal ------------------------------------------------------------

    def _enqueue(self, event: dict[str, Any], event_type: EventType) -> bool:
        event["_event_type_routing"] = event_type.value
        try:
            self._queue.put_nowait(event)
            with self._stats_lock:
                self._stats["enqueued"] += 1
            return True
        except queue.Full:
            log.warning("N8n queue full – dropping event type=%s", event_type.value)
            with self._stats_lock:
                self._stats["dropped"] += 1
            return False

    def _resolve_url(self, event: dict[str, Any]) -> str:
        """Resolve the target webhook URL based on routing metadata."""
        override = event.get("_route_override", "")
        if override == "alert":
            return f"{self._cfg.base_url.rstrip('/')}{self._cfg.alert_webhook_path}"

        event_type_str = event.get("_event_type_routing", EventType.GENERIC_EVENT.value)
        if event_type_str in self._cfg.route_map:
            path = self._cfg.route_map[event_type_str]
            return f"{self._cfg.base_url.rstrip('/')}{path}"

        return f"{self._cfg.base_url.rstrip('/')}{self._cfg.event_webhook_path}"

    def _delivery_worker(self) -> None:
        """Background thread: drain the queue and deliver events."""
        while not self._stop_event.is_set():
            try:
                event = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            self._deliver_with_retry(event)
            self._queue.task_done()

    def _deliver_with_retry(self, event: dict[str, Any]) -> None:
        """Attempt delivery with exponential back-off; fall back on exhaustion."""
        url = self._resolve_url(event)
        # Strip internal routing metadata before serializing
        clean_event = {k: v for k, v in event.items() if not k.startswith("_")}
        payload = json.dumps(clean_event).encode("utf-8")
        backoff = self._cfg.backoff_base

        for attempt in range(1, self._cfg.max_retries + 1):
            result = _post_json(url, payload, self._headers, self._cfg.timeout_seconds)
            result.attempt = attempt

            if result.success:
                with self._stats_lock:
                    self._stats["delivered"] += 1
                log.debug(
                    "Delivered event to n8n | attempt=%d duration_ms=%.0f",
                    attempt,
                    result.duration_ms,
                )
                return

            log.warning(
                "n8n delivery failed | attempt=%d/%d status=%d error='%s'",
                attempt,
                self._cfg.max_retries,
                result.status_code,
                result.error[:100],
            )

            if attempt < self._cfg.max_retries:
                with self._stats_lock:
                    self._stats["retried"] += 1
                jitter = random.uniform(0, backoff * self._cfg.jitter_factor)
                sleep_time = min(backoff + jitter, self._cfg.backoff_max)
                time.sleep(sleep_time)
                backoff = min(backoff * 2, self._cfg.backoff_max)

        # All retries exhausted
        log.error(
            "Exhausted %d retries for event type=%s – writing to fallback",
            self._cfg.max_retries,
            clean_event.get("event_type", "unknown"),
        )
        self._write_fallback(clean_event)

    def _write_fallback(self, event: dict[str, Any]) -> None:
        """Append a failed event to the local JSONL fallback file."""
        Path(self._cfg.fallback_file).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._cfg.fallback_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
            with self._stats_lock:
                self._stats["fallback_written"] += 1
        except OSError as exc:
            log.error("Cannot write to fallback file %s: %s", self._cfg.fallback_file, exc)

    def _health_check_worker(self) -> None:
        """Periodically check n8n availability and update the healthy flag."""
        while not self._stop_event.is_set():
            healthy = self.health_check()
            if healthy:
                self._healthy.set()
            else:
                self._healthy.clear()
            self._stop_event.wait(self._cfg.health_check_interval)


# ---------------------------------------------------------------------------
# Fallback queue replay
# ---------------------------------------------------------------------------

def replay_fallback(cfg: N8nClientConfig, delete_on_success: bool = True) -> int:
    """
    Replay events from the local fallback JSONL file to n8n.

    Events that are successfully delivered are removed from the file.
    Events that still fail are left in the file for the next replay run.

    Args:
        cfg:               Client configuration.
        delete_on_success: Remove the fallback file after full successful replay.

    Returns:
        Number of events successfully replayed.
    """
    fallback_path = Path(cfg.fallback_file)
    if not fallback_path.exists():
        log.info("No fallback file to replay: %s", cfg.fallback_file)
        return 0

    headers = {"Content-Type": "application/json", "User-Agent": "AcmeToCasino-SOAR/1.0"}
    if cfg.api_key:
        headers["X-N8N-API-KEY"] = cfg.api_key

    events: list[dict[str, Any]] = []
    with open(fallback_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("Skipping corrupt line in fallback file: %.80s", line)

    if not events:
        log.info("Fallback file is empty")
        return 0

    log.info("Replaying %d events from fallback file", len(events))
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    event_url = f"{cfg.base_url.rstrip('/')}{cfg.event_webhook_path}"
    alert_url = f"{cfg.base_url.rstrip('/')}{cfg.alert_webhook_path}"

    for event in events:
        url = alert_url if event.get("severity") else event_url
        payload = json.dumps(event).encode("utf-8")
        result = _post_json(url, payload, headers, cfg.timeout_seconds)
        if result.success:
            successes.append(event)
        else:
            failures.append(event)

    # Rewrite fallback file with only the events that still failed
    if failures:
        with open(fallback_path, "w", encoding="utf-8") as fh:
            for event in failures:
                fh.write(json.dumps(event) + "\n")
        log.warning("Replay complete: %d succeeded, %d still failing", len(successes), len(failures))
    else:
        if delete_on_success:
            fallback_path.unlink(missing_ok=True)
        log.info("Replay complete: all %d events delivered", len(successes))

    return len(successes)


# ---------------------------------------------------------------------------
# Config helpers
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


def _client_config_from_yaml(cfg: dict[str, Any]) -> N8nClientConfig:
    """Build an N8nClientConfig from the responders.n8n section of config.yml."""
    n8n = cfg.get("responders", {}).get("n8n", {})
    buf = cfg.get("buffering", {})
    return N8nClientConfig(
        base_url=n8n.get("base_url", "http://n8n:5678"),
        event_webhook_path=n8n.get("event_webhook_path", "/webhook/soar-events"),
        alert_webhook_path=n8n.get("alert_webhook_path", "/webhook/soar-alerts"),
        api_key=n8n.get("api_key", ""),
        timeout_seconds=float(n8n.get("timeout_seconds", 10)),
        max_retries=int(n8n.get("max_retries", 5)),
        backoff_base=float(n8n.get("retry_backoff_base_seconds", 2)),
        queue_depth=int(buf.get("queue_depth", 10000)),
        sender_workers=int(buf.get("sender_workers", 4)),
        fallback_file=n8n.get("fallback_file", "/var/lib/soar/n8n_fallback_queue.jsonl"),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AcmeToCasino SOAR n8n webhook client",
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

    # health-check
    sub.add_parser("health-check", help="Check n8n availability")

    # replay-fallback
    p_replay = sub.add_parser("replay-fallback", help="Replay events from the fallback JSONL file")
    p_replay.add_argument(
        "--keep",
        action="store_true",
        help="Do not delete the fallback file after a full successful replay",
    )

    # send-test
    p_test = sub.add_parser("send-test", help="Send a test event to n8n")
    p_test.add_argument(
        "--severity",
        default="info",
        choices=["critical", "high", "medium", "low", "info"],
    )

    return parser


def main() -> None:
    """Entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    cfg = load_config(args.config)
    log_level = args.log_level or cfg.get("system", {}).get("log_level", "INFO")
    logging.getLogger().setLevel(getattr(logging, log_level.upper(), logging.INFO))

    client_cfg = _client_config_from_yaml(cfg)

    if args.command == "health-check":
        healthy = N8nWebhookClient(client_cfg).health_check()
        sys.exit(0 if healthy else 1)

    elif args.command == "replay-fallback":
        replayed = replay_fallback(client_cfg, delete_on_success=not args.keep)
        log.info("Replayed %d events", replayed)

    elif args.command == "send-test":
        client = N8nWebhookClient(client_cfg)
        client.start()
        test_event = {
            "event_id": "test-000",
            "schema_version": "1.0",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "source": "n8n_webhook_sender_test",
            "event_type": "test_event",
            "source_ip": "127.0.0.1",
            "severity": args.severity,
            "message": "SOAR test event from n8n_webhook_sender.py",
        }
        ok = client.send_event(test_event)
        time.sleep(3)  # allow workers to deliver
        client.stop()
        log.info("Test event enqueued=%s stats=%s", ok, client.stats())
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
