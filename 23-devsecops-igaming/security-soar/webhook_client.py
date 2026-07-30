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
Generic webhook client for CI/CD and security notifications in iGaming pipelines.

Provides a production-grade HTTP webhook client with:
  - Retry with exponential back-off and jitter
  - HMAC-SHA256 payload signing (for webhook receivers that verify signatures)
  - Configurable timeout and auth headers
  - JSONL-file fallback queue when the target is unreachable
  - Support for multiple webhook targets with per-target routing
  - Non-blocking fire-and-forget mode via background thread queue

Suitable for sending notifications to:
  - PagerDuty Events API v2
  - Grafana OnCall webhooks
  - OpsGenie alert API
  - Custom CI/CD notification endpoints
  - n8n webhooks (see security-soar/integrations/n8n_webhook_sender.py for a
    more specialised n8n client)

Usage as a library:
    from webhook_client import WebhookClient

    client = WebhookClient(
        url="https://events.pagerduty.com/v2/enqueue",
        headers={"Authorization": "Token token=YOUR_API_KEY"},
        signing_secret="shared-secret",    # optional HMAC signing
    )
    client.send({"event_action": "trigger", "payload": {...}})

Usage as a CLI:
    python webhook_client.py send --url https://... --payload-file event.json
    python webhook_client.py replay-fallback --fallback /var/lib/soar/fallback.jsonl

Reference: Chapter 23 — DevSecOps for iGaming / CI/CD Security Pipeline
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import queue
import random
import sys
import threading
import time
import urllib.error
import urllib.request
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


log = _build_logger("webhook_client")


# ---------------------------------------------------------------------------
# HMAC payload signing
# ---------------------------------------------------------------------------

def _sign_payload(payload: bytes, secret: str, algorithm: str = "sha256") -> str:
    """
    Compute an HMAC signature over a JSON payload.

    Args:
        payload:   UTF-8 encoded JSON bytes.
        secret:    Shared signing secret.
        algorithm: Hash algorithm (sha256 or sha512).

    Returns:
        Hex-encoded HMAC digest, e.g. "sha256=abc123...".
    """
    digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload,
        digestmod=getattr(hashlib, algorithm),
    ).hexdigest()
    return f"{algorithm}={digest}"


# ---------------------------------------------------------------------------
# Delivery result
# ---------------------------------------------------------------------------

@dataclass
class DeliveryResult:
    """Outcome of a single webhook delivery attempt."""
    success: bool
    status_code: int = 0
    response_body: str = ""
    error: str = ""
    attempt: int = 1
    duration_ms: float = 0.0
    url: str = ""


def _http_post(
    url: str,
    payload: bytes,
    headers: dict[str, str],
    timeout: float,
) -> DeliveryResult:
    """
    Perform a single HTTP POST.

    Args:
        url:     Target URL.
        payload: Pre-serialised JSON bytes.
        headers: Request headers (Content-Type, auth, signatures).
        timeout: Request timeout in seconds.

    Returns:
        DeliveryResult describing the outcome.
    """
    start = time.monotonic()
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(8192).decode("utf-8", errors="replace")
            duration = (time.monotonic() - start) * 1000
            return DeliveryResult(
                success=resp.status < 300,
                status_code=resp.status,
                response_body=body,
                duration_ms=duration,
                url=url,
            )
    except urllib.error.HTTPError as exc:
        duration = (time.monotonic() - start) * 1000
        body = exc.read(4096).decode("utf-8", errors="replace") if exc.fp else ""
        return DeliveryResult(
            success=False,
            status_code=exc.code,
            response_body=body,
            error=str(exc),
            duration_ms=duration,
            url=url,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        duration = (time.monotonic() - start) * 1000
        return DeliveryResult(success=False, error=str(exc), duration_ms=duration, url=url)


# ---------------------------------------------------------------------------
# Client configuration
# ---------------------------------------------------------------------------

@dataclass
class WebhookConfig:
    """
    Configuration for WebhookClient.

    Attributes:
        url:                 Primary webhook URL.
        headers:             Static HTTP request headers (auth tokens etc.).
        signing_secret:      Optional HMAC-SHA256 signing secret.
        signing_header:      Header name for the HMAC signature.
        timeout_seconds:     Per-request timeout.
        max_retries:         Maximum delivery attempts before fallback.
        backoff_base:        Exponential back-off base delay (seconds).
        backoff_max:         Maximum back-off delay (seconds).
        jitter_factor:       Jitter fraction applied to back-off.
        queue_depth:         Async queue capacity.
        fallback_file:       JSONL file path for failed deliveries.
        user_agent:          User-Agent header value.
    """
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    signing_secret: str = ""
    signing_header: str = "X-Hub-Signature-256"
    timeout_seconds: float = 10.0
    max_retries: int = 5
    backoff_base: float = 1.0
    backoff_max: float = 60.0
    jitter_factor: float = 0.25
    queue_depth: int = 5000
    fallback_file: str = "/var/lib/soar/webhook_fallback.jsonl"
    user_agent: str = "AcmeToCasino-WebhookClient/1.0"


# ---------------------------------------------------------------------------
# Synchronous client
# ---------------------------------------------------------------------------

class WebhookClient:
    """
    HTTP webhook client with retry, HMAC signing, and fallback queue.

    This is the synchronous client.  For non-blocking delivery, wrap it with
    :class:`AsyncWebhookClient`.

    Args:
        url:             Target webhook URL.
        headers:         Additional HTTP headers (auth tokens etc.).
        signing_secret:  Optional shared secret for HMAC-SHA256 signing.
        timeout:         Per-request timeout in seconds.
        max_retries:     Maximum delivery attempts.
        fallback_file:   Path to JSONL fallback file for failed deliveries.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        signing_secret: str = "",
        signing_header: str = "X-Hub-Signature-256",
        timeout: float = 10.0,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        fallback_file: str = "/var/lib/soar/webhook_fallback.jsonl",
    ) -> None:
        self._cfg = WebhookConfig(
            url=url,
            headers=headers or {},
            signing_secret=signing_secret,
            signing_header=signing_header,
            timeout_seconds=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
            fallback_file=fallback_file,
        )
        self._base_headers = {
            "Content-Type": "application/json",
            "User-Agent": self._cfg.user_agent,
            **self._cfg.headers,
        }
        self._stats: dict[str, int] = {
            "sent": 0,
            "failed": 0,
            "retried": 0,
            "fallback_written": 0,
        }

    def send(self, payload: dict[str, Any]) -> bool:
        """
        Send a JSON payload to the configured webhook URL with retry.

        Args:
            payload: Dict that will be JSON-serialised and POSTed.

        Returns:
            True if delivery succeeded within the retry budget.
        """
        body = json.dumps(payload).encode("utf-8")
        headers = dict(self._base_headers)
        if self._cfg.signing_secret:
            headers[self._cfg.signing_header] = _sign_payload(body, self._cfg.signing_secret)

        backoff = self._cfg.backoff_base
        for attempt in range(1, self._cfg.max_retries + 1):
            result = _http_post(self._cfg.url, body, headers, self._cfg.timeout_seconds)
            result.attempt = attempt

            if result.success:
                self._stats["sent"] += 1
                log.info(
                    "webhook_delivered attempt=%d status=%d duration_ms=%.0f url=%s",
                    attempt,
                    result.status_code,
                    result.duration_ms,
                    self._cfg.url,
                )
                return True

            log.warning(
                "webhook_delivery_failed attempt=%d/%d status=%d error=%s",
                attempt,
                self._cfg.max_retries,
                result.status_code,
                result.error[:100],
            )
            self._stats["retried"] += 1

            if attempt < self._cfg.max_retries:
                jitter = random.uniform(0, backoff * self._cfg.jitter_factor)
                sleep_time = min(backoff + jitter, self._cfg.backoff_max)
                time.sleep(sleep_time)
                backoff = min(backoff * 2, self._cfg.backoff_max)

        # All retries exhausted
        log.error(
            "webhook_exhausted_retries retries=%d url=%s",
            self._cfg.max_retries,
            self._cfg.url,
        )
        self._write_fallback(payload)
        self._stats["failed"] += 1
        return False

    def stats(self) -> dict[str, int]:
        """Return a copy of delivery statistics."""
        return dict(self._stats)

    def _write_fallback(self, payload: dict[str, Any]) -> None:
        Path(self._cfg.fallback_file).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._cfg.fallback_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload) + "\n")
            self._stats["fallback_written"] += 1
        except OSError as exc:
            log.error("webhook_fallback_write_error file=%s error=%s", self._cfg.fallback_file, exc)


# ---------------------------------------------------------------------------
# Async (queue-backed) client
# ---------------------------------------------------------------------------

class AsyncWebhookClient:
    """
    Non-blocking wrapper around WebhookClient.

    Events are enqueued and delivered by background worker threads.
    The calling thread is never blocked by HTTP latency or retries.

    Args:
        client:       Underlying WebhookClient.
        queue_depth:  Maximum queued payloads.
        workers:      Number of parallel delivery threads.
    """

    def __init__(
        self,
        client: WebhookClient,
        queue_depth: int = 5000,
        workers: int = 2,
    ) -> None:
        self._client = client
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_depth)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        for i in range(workers):
            t = threading.Thread(
                target=self._worker,
                name=f"webhook-sender-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

    def send(self, payload: dict[str, Any]) -> bool:
        """
        Enqueue a payload for async delivery.

        Returns:
            True if enqueued successfully, False if the queue is full.
        """
        try:
            self._queue.put_nowait(payload)
            return True
        except queue.Full:
            log.warning("webhook_queue_full — dropping payload")
            return False

    def stop(self, timeout: float = 30.0) -> None:
        """Signal workers to stop and wait for the queue to drain."""
        self._stop.set()
        try:
            self._queue.join()
        except Exception:  # noqa: BLE001
            pass

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                payload = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._client.send(payload)
            except Exception as exc:  # noqa: BLE001
                log.error("webhook_worker_error: %s", exc)
            finally:
                self._queue.task_done()


# ---------------------------------------------------------------------------
# Fallback queue replay
# ---------------------------------------------------------------------------

def replay_fallback(
    client: WebhookClient,
    fallback_file: str,
    delete_on_full_success: bool = True,
) -> int:
    """
    Replay all events in a JSONL fallback file to the webhook.

    Successfully delivered events are removed; failed events remain for the
    next replay run.

    Args:
        client:                 WebhookClient to use for delivery.
        fallback_file:          Path to the JSONL fallback file.
        delete_on_full_success: Delete the file after a 100% successful replay.

    Returns:
        Number of events successfully delivered.
    """
    path = Path(fallback_file)
    if not path.exists():
        log.info("replay_fallback: no fallback file at %s", fallback_file)
        return 0

    events: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("replay_fallback: skipping corrupt line: %.80s", line)

    if not events:
        return 0

    log.info("replay_fallback: replaying %d events from %s", len(events), fallback_file)
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for event in events:
        if client.send(event):
            successes.append(event)
        else:
            failures.append(event)

    if failures:
        with open(path, "w", encoding="utf-8") as fh:
            for event in failures:
                fh.write(json.dumps(event) + "\n")
        log.warning(
            "replay_fallback: %d succeeded, %d still failing",
            len(successes),
            len(failures),
        )
    else:
        if delete_on_full_success:
            path.unlink(missing_ok=True)
        log.info("replay_fallback: all %d events delivered", len(successes))

    return len(successes)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generic webhook client for iGaming CI/CD and security notifications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("WEBHOOK_URL", ""),
        help="Target webhook URL (default: $WEBHOOK_URL)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("WEBHOOK_TOKEN", ""),
        help="Bearer token for Authorization header (default: $WEBHOOK_TOKEN)",
    )
    parser.add_argument(
        "--secret",
        default=os.environ.get("WEBHOOK_SIGNING_SECRET", ""),
        help="HMAC signing secret (default: $WEBHOOK_SIGNING_SECRET)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Request timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=5,
        help="Maximum delivery attempts (default: %(default)s)",
    )
    parser.add_argument(
        "--fallback",
        default="/var/lib/soar/webhook_fallback.jsonl",
        help="JSONL fallback file path (default: %(default)s)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_send = sub.add_parser("send", help="Send a JSON payload to the webhook")
    p_send.add_argument(
        "--payload-file",
        required=True,
        metavar="FILE",
        help="Path to a JSON file containing the payload",
    )

    p_replay = sub.add_parser("replay-fallback", help="Replay the JSONL fallback queue")
    p_replay.add_argument(
        "--keep",
        action="store_true",
        help="Keep the fallback file after a full successful replay",
    )

    p_test = sub.add_parser("send-test", help="Send a synthetic test payload")
    p_test.add_argument(
        "--event-type",
        default="security_alert",
        help="event_type field in the test payload (default: %(default)s)",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.url and args.command != "replay-fallback":
        log.error("Webhook URL required: set --url or $WEBHOOK_URL")
        sys.exit(1)

    extra_headers: dict[str, str] = {}
    if args.token:
        extra_headers["Authorization"] = f"Bearer {args.token}"

    client = WebhookClient(
        url=args.url or "http://localhost",
        headers=extra_headers,
        signing_secret=args.secret,
        timeout=args.timeout,
        max_retries=args.retries,
        fallback_file=args.fallback,
    )

    if args.command == "send":
        try:
            with open(args.payload_file, encoding="utf-8") as fh:
                payload: dict[str, Any] = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Cannot read payload file %s: %s", args.payload_file, exc)
            sys.exit(1)
        ok = client.send(payload)
        sys.exit(0 if ok else 1)

    elif args.command == "replay-fallback":
        count = replay_fallback(
            client=client,
            fallback_file=args.fallback,
            delete_on_full_success=not args.keep,
        )
        log.info("Replayed %d events from fallback queue", count)

    elif args.command == "send-test":
        test_payload: dict[str, Any] = {
            "event_id": f"test-{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            "schema_version": "1.0",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "source": "webhook_client_test",
            "event_type": args.event_type,
            "severity": "info",
            "message": "AcmeToCasino webhook client test — no action required",
            "environment": os.environ.get("ENVIRONMENT", "development"),
        }
        ok = client.send(test_payload)
        log.info("Test payload sent: %s | stats=%s", ok, client.stats())
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
