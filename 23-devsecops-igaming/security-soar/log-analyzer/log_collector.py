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
Unified Log Collector for AcmeToCasino SOAR System.

Ingests log events from multiple sources, normalizes them into a common schema,
buffers them into configurable batches, and forwards to the n8n webhook pipeline.

Sources supported:
  - Nginx access/error logs (file tail)
  - Application logs (JSON structured)
  - Auth service logs (JSON structured)
  - AWS WAF logs (S3 polling via Kinesis Firehose deliveries)
  - Kafka topic consumer

Usage:
    python log_collector.py --config /etc/soar/config.yml
    python log_collector.py --config /etc/soar/config.yml --dry-run
    python log_collector.py --config /etc/soar/config.yml --source nginx_access
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import yaml
from botocore.exceptions import ClientError

# Optional Kafka support – import only when the collector is enabled.
try:
    from confluent_kafka import Consumer as KafkaConsumer
    from confluent_kafka import KafkaException
    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

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


log = _build_logger("log_collector")


# ---------------------------------------------------------------------------
# Common event schema
# ---------------------------------------------------------------------------

def _new_event(
    source: str,
    event_type: str,
    source_ip: str,
    raw: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """
    Construct a normalized event conforming to the SOAR common schema v1.0.

    Args:
        source:     Log source identifier (e.g. "nginx_access", "kafka").
        event_type: Semantic type (e.g. "http_request", "auth_failure").
        source_ip:  IPv4 or IPv6 address of the originating client.
        raw:        Original unparsed log line for forensic reference.
        **extra:    Any additional fields to merge into the event.

    Returns:
        A dictionary ready to be JSON-serialized and forwarded downstream.
    """
    return {
        "event_id": str(uuid.uuid4()),
        "schema_version": "1.0",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "source": source,
        "event_type": event_type,
        "source_ip": source_ip,
        "raw": raw[:16384],  # cap raw field to avoid oversized payloads
        **extra,
    }


# ---------------------------------------------------------------------------
# Nginx log parsers
# ---------------------------------------------------------------------------

# Combined Log Format:
# $remote_addr - $remote_user [$time_local] "$request" $status $bytes_sent
# "$http_referer" "$http_user_agent"
_NGINX_ACCESS_RE = re.compile(
    r'(?P<remote_addr>\S+) - (?P<remote_user>\S+) \[(?P<time_local>[^\]]+)\] '
    r'"(?P<request>[^"]*)" (?P<status>\d{3}) (?P<bytes_sent>\d+) '
    r'"(?P<referer>[^"]*)" "(?P<user_agent>[^"]*)"'
)

# Nginx error log: YYYY/MM/DD HH:MM:SS [level] pid#tid: *cid message
_NGINX_ERROR_RE = re.compile(
    r'(?P<date>\d{4}/\d{2}/\d{2}) (?P<time>\d{2}:\d{2}:\d{2}) '
    r'\[(?P<level>\w+)\] (?P<pid>\d+)#(?P<tid>\d+): '
    r'(?:\*\d+ )?(?P<message>.+?)(?:, client: (?P<client>\S+))?'
    r'(?:, server: (?P<server>\S+))?(?:, request: "(?P<request>[^"]*)")?$'
)


def _parse_nginx_access(line: str) -> dict[str, Any] | None:
    """
    Parse a single Nginx combined-format access log line.

    Returns a normalized event dict, or None if the line does not match.
    """
    m = _NGINX_ACCESS_RE.match(line.strip())
    if not m:
        return None
    g = m.groupdict()
    method = path = protocol = ""
    req_parts = g["request"].split(" ", 2)
    if len(req_parts) == 3:
        method, path, protocol = req_parts
    return _new_event(
        source="nginx_access",
        event_type="http_request",
        source_ip=g["remote_addr"],
        raw=line,
        http_method=method,
        http_path=path,
        http_protocol=protocol,
        http_status=int(g["status"]),
        bytes_sent=int(g["bytes_sent"]),
        referer=g["referer"],
        user_agent=g["user_agent"],
        remote_user=g["remote_user"],
        nginx_time_local=g["time_local"],
    )


def _parse_nginx_error(line: str) -> dict[str, Any] | None:
    """
    Parse a single Nginx error log line.

    Returns a normalized event dict, or None if the line does not match.
    """
    m = _NGINX_ERROR_RE.match(line.strip())
    if not m:
        return None
    g = m.groupdict()
    client_ip = g.get("client") or "0.0.0.0"
    return _new_event(
        source="nginx_error",
        event_type="nginx_error",
        source_ip=client_ip,
        raw=line,
        nginx_level=g["level"],
        nginx_message=g["message"],
        nginx_server=g.get("server") or "",
        http_request=g.get("request") or "",
    )


# ---------------------------------------------------------------------------
# JSON log parser (application + auth service)
# ---------------------------------------------------------------------------

def _parse_json_log(
    line: str,
    source: str,
    timestamp_field: str = "timestamp",
    timestamp_format: str = "%Y-%m-%dT%H:%M:%S.%fZ",
) -> dict[str, Any] | None:
    """
    Parse a single JSON-structured log line.

    Args:
        line:             Raw log line.
        source:           Source identifier injected into the normalized event.
        timestamp_field:  JSON key containing the event timestamp.
        timestamp_format: strptime format for the timestamp value.

    Returns:
        A normalized event dict, or None on parse failure.
    """
    try:
        data: dict[str, Any] = json.loads(line.strip())
    except json.JSONDecodeError:
        log.debug("Non-JSON line from %s: %.80s", source, line)
        return None

    source_ip: str = (
        data.get("ip")
        or data.get("source_ip")
        or data.get("client_ip")
        or data.get("remote_addr")
        or "0.0.0.0"
    )
    event_type: str = (
        data.get("event_type")
        or data.get("event")
        or data.get("type")
        or "app_log"
    )

    event = _new_event(
        source=source,
        event_type=event_type,
        source_ip=source_ip,
        raw=line,
        **{k: v for k, v in data.items() if k not in ("ip", "source_ip", "client_ip", "remote_addr")},
    )

    # Normalise the timestamp if present
    raw_ts = data.get(timestamp_field)
    if raw_ts:
        try:
            parsed_ts = datetime.strptime(str(raw_ts), timestamp_format).replace(tzinfo=timezone.utc)
            event["timestamp"] = parsed_ts.isoformat()
        except ValueError:
            pass  # keep ingest time as fallback

    return event


# ---------------------------------------------------------------------------
# AWS WAF log parser
# ---------------------------------------------------------------------------

def _parse_waf_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """
    Normalize a single AWS WAF Kinesis Firehose log record.

    Args:
        record: A Python dict deserialized from the WAF JSON delivery.

    Returns:
        A normalized event dict, or None when the record is missing required fields.
    """
    source_ip: str = (
        (record.get("httpRequest") or {}).get("clientIp")
        or record.get("clientIp")
        or "0.0.0.0"
    )
    action: str = record.get("action", "UNKNOWN")
    http_req: dict[str, Any] = record.get("httpRequest") or {}

    return _new_event(
        source="aws_waf",
        event_type="waf_request",
        source_ip=source_ip,
        raw=json.dumps(record)[:16384],
        waf_action=action,
        waf_terminated_rules=[r.get("ruleId") for r in record.get("terminatingRuleMatchDetails", [])],
        http_method=http_req.get("httpMethod", ""),
        http_uri=http_req.get("uri", ""),
        http_args=http_req.get("args", ""),
        country=record.get("httpRequest", {}).get("country", ""),
        webaclid=record.get("webaclId", ""),
        timestamp_waf=record.get("timestamp"),
    )


# ---------------------------------------------------------------------------
# File tailer utility
# ---------------------------------------------------------------------------

class _FileTailer:
    """
    Tail a log file and yield new lines as they are written.

    Handles file rotation (by inode change or truncation).

    Args:
        path:               Absolute path to the log file.
        poll_interval:      Seconds between checks when no new data is available.
        backfill_lines:     Number of existing lines to read on first open (0 = skip).
    """

    def __init__(self, path: str, poll_interval: float = 1.0, backfill_lines: int = 0) -> None:
        self._path = path
        self._poll = poll_interval
        self._backfill = backfill_lines
        self._file: Any = None
        self._inode: int = -1

    def _open_file(self) -> bool:
        """Open the log file, seeking to end. Returns True on success."""
        try:
            self._file = open(self._path, encoding="utf-8", errors="replace")
            self._inode = os.stat(self._path).st_ino
            self._file.seek(0, 2)  # seek to end; backfill handled in tail()
            return True
        except OSError as exc:
            log.warning("Cannot open log file %s: %s", self._path, exc)
            return False

    def _read_backfill(self) -> list[str]:
        """Return up to self._backfill lines from the end of the already-open file."""
        if self._file is None or self._backfill <= 0:
            return []
        try:
            self._file.seek(0)
            all_lines = self._file.readlines()
            self._file.seek(0, 2)
            start = max(0, len(all_lines) - self._backfill)
            return [ln.rstrip("\n") for ln in all_lines[start:]]
        except OSError:
            return []

    def tail(self) -> Generator[str, None, None]:
        """Yield new log lines indefinitely, starting from the configured position."""
        # Wait until the file becomes available
        while not self._open_file():
            time.sleep(self._poll)

        # Emit backfill lines when requested, then seek to end
        if self._backfill > 0:
            for bline in self._read_backfill():
                yield bline

        while True:
            if self._file is None:
                if not self._open_file():
                    time.sleep(self._poll)
                    continue

            line = self._file.readline()
            if line:
                yield line.rstrip("\n")
                continue

            # No new data – check for rotation
            try:
                current_inode = os.stat(self._path).st_ino
                current_size = os.stat(self._path).st_size
            except OSError:
                current_inode = -1
                current_size = 0

            file_pos = self._file.tell() if self._file is not None else 0
            if current_inode != self._inode or current_size < file_pos:
                log.info("Log file rotated: %s – reopening", self._path)
                self._file.close()
                self._file = None
                self._inode = -1
            else:
                time.sleep(self._poll)


# ---------------------------------------------------------------------------
# Abstract collector base
# ---------------------------------------------------------------------------

class BaseCollector(ABC):
    """
    Abstract base class for all log source collectors.

    Subclasses implement :meth:`collect` to yield normalized event dicts.

    Args:
        config:    Source-specific configuration block from config.yml.
        event_queue: Thread-safe queue to deposit normalized events into.
        dry_run:   When True, events are logged but not enqueued.
    """

    def __init__(
        self,
        config: dict[str, Any],
        event_queue: queue.Queue[dict[str, Any]],
        dry_run: bool = False,
    ) -> None:
        self._cfg = config
        self._queue = event_queue
        self._dry_run = dry_run
        self._stop_event = threading.Event()
        self._logger = _build_logger(f"collector.{self.name}")

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this collector."""

    @abstractmethod
    def collect(self) -> None:
        """
        Start collecting events.

        This method is called in a dedicated thread. It should loop until
        self._stop_event is set. Each parsed event should be deposited into
        self._queue via self._emit().
        """

    def _emit(self, event: dict[str, Any] | None) -> None:
        """Enqueue a normalized event (no-op when event is None or dry_run)."""
        if event is None:
            return
        if self._dry_run:
            self._logger.debug("DRY-RUN event: %s", json.dumps(event)[:200])
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._logger.warning("Event queue full – dropping event from %s", self.name)

    def stop(self) -> None:
        """Signal the collector to stop."""
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Concrete collectors
# ---------------------------------------------------------------------------

class NginxAccessCollector(BaseCollector):
    """Tails the Nginx combined-format access log and emits http_request events."""

    @property
    def name(self) -> str:
        return "nginx_access"

    def collect(self) -> None:
        path = self._cfg.get("path", "/var/log/nginx/access.log")
        poll = float(self._cfg.get("poll_interval_seconds", 1.0))
        backfill = int(self._cfg.get("backfill_lines", 0))
        tailer = _FileTailer(path, poll_interval=poll, backfill_lines=backfill)
        self._logger.info("Starting Nginx access log tail: %s", path)
        for line in tailer.tail():
            if self._stop_event.is_set():
                break
            self._emit(_parse_nginx_access(line))


class NginxErrorCollector(BaseCollector):
    """Tails the Nginx error log and emits nginx_error events."""

    @property
    def name(self) -> str:
        return "nginx_error"

    def collect(self) -> None:
        path = self._cfg.get("path", "/var/log/nginx/error.log")
        poll = float(self._cfg.get("poll_interval_seconds", 1.0))
        tailer = _FileTailer(path, poll_interval=poll)
        self._logger.info("Starting Nginx error log tail: %s", path)
        for line in tailer.tail():
            if self._stop_event.is_set():
                break
            self._emit(_parse_nginx_error(line))


class ApplicationLogCollector(BaseCollector):
    """Tails a JSON-structured application log file."""

    @property
    def name(self) -> str:
        return "application"

    def collect(self) -> None:
        path = self._cfg.get("path", "/var/log/app/application.log")
        poll = float(self._cfg.get("poll_interval_seconds", 1.0))
        ts_field = self._cfg.get("timestamp_field", "timestamp")
        ts_fmt = self._cfg.get("timestamp_format", "%Y-%m-%dT%H:%M:%S.%fZ")
        tailer = _FileTailer(path, poll_interval=poll)
        self._logger.info("Starting application log tail: %s", path)
        for line in tailer.tail():
            if self._stop_event.is_set():
                break
            self._emit(_parse_json_log(line, "application", ts_field, ts_fmt))


class AuthServiceCollector(BaseCollector):
    """Tails the auth service JSON log file."""

    @property
    def name(self) -> str:
        return "auth_service"

    def collect(self) -> None:
        path = self._cfg.get("path", "/var/log/app/auth.log")
        poll = float(self._cfg.get("poll_interval_seconds", 1.0))
        ts_field = self._cfg.get("timestamp_field", "ts")
        ts_fmt = self._cfg.get("timestamp_format", "%Y-%m-%dT%H:%M:%SZ")
        tailer = _FileTailer(path, poll_interval=poll)
        self._logger.info("Starting auth service log tail: %s", path)
        for line in tailer.tail():
            if self._stop_event.is_set():
                break
            self._emit(_parse_json_log(line, "auth_service", ts_field, ts_fmt))


class AwsWafCollector(BaseCollector):
    """
    Polls an S3 bucket for new AWS WAF log objects delivered by Kinesis Firehose.

    Objects are tracked via a local checkpoint file to avoid reprocessing.
    Supports both plain JSON and gzip-compressed Firehose deliveries.
    """

    @property
    def name(self) -> str:
        return "aws_waf"

    def collect(self) -> None:
        bucket = self._cfg.get("s3_bucket", "")
        prefix = self._cfg.get("s3_prefix", "waf-logs")
        region = self._cfg.get("aws_region", "eu-west-1")
        poll = float(self._cfg.get("poll_interval_seconds", 30))
        max_objects = int(self._cfg.get("max_objects_per_cycle", 100))
        lookback_hours = int(self._cfg.get("lookback_hours", 2))
        checkpoint_file = self._cfg.get("checkpoint_file", "/var/lib/soar/waf_checkpoint.json")

        if not bucket:
            self._logger.error("aws_waf collector requires s3_bucket to be set; disabling")
            return

        s3 = boto3.client("s3", region_name=region)
        processed: set[str] = self._load_checkpoint(checkpoint_file)
        self._logger.info("Starting AWS WAF S3 collector: s3://%s/%s", bucket, prefix)

        while not self._stop_event.is_set():
            try:
                new_keys = self._list_new_objects(s3, bucket, prefix, lookback_hours, processed, max_objects)
                for key in new_keys:
                    if self._stop_event.is_set():
                        break
                    count = self._process_object(s3, bucket, key)
                    processed.add(key)
                    self._logger.info("Processed WAF log s3://%s/%s (%d records)", bucket, key, count)
                if new_keys:
                    self._save_checkpoint(checkpoint_file, processed)
            except ClientError as exc:
                self._logger.error("S3 error in WAF collector: %s", exc)
            self._stop_event.wait(poll)

    def _list_new_objects(
        self,
        s3: Any,
        bucket: str,
        prefix: str,
        lookback_hours: int,
        processed: set[str],
        max_objects: int,
    ) -> list[str]:
        cutoff = datetime.now(tz=timezone.utc).timestamp() - lookback_hours * 3600
        paginator = s3.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                mtime: float = obj["LastModified"].timestamp()
                if mtime >= cutoff and key not in processed:
                    keys.append(key)
                    if len(keys) >= max_objects:
                        return keys
        return keys

    def _process_object(self, s3: Any, bucket: str, key: str) -> int:
        response = s3.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        if key.endswith(".gz"):
            body = gzip.decompress(body)
        count = 0
        for raw_line in body.decode("utf-8", errors="replace").splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            self._emit(_parse_waf_record(record))
            count += 1
        return count

    @staticmethod
    def _load_checkpoint(path: str) -> set[str]:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
                return set(data.get("processed_keys", []))
        except (OSError, json.JSONDecodeError):
            return set()

    @staticmethod
    def _save_checkpoint(path: str, processed: set[str]) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"processed_keys": list(processed)}, fh)


class KafkaCollector(BaseCollector):
    """
    Consumes security events from Kafka topics and normalizes them.

    Each Kafka message value must be a JSON object. The message's topic
    is mapped to the SOAR event_type if no explicit field is present.
    """

    @property
    def name(self) -> str:
        return "kafka"

    def collect(self) -> None:
        if not _KAFKA_AVAILABLE:
            self._logger.error("confluent-kafka is not installed; Kafka collector disabled")
            return

        brokers = self._cfg.get("bootstrap_servers", "localhost:9092")
        topics: list[str] = self._cfg.get("topics", ["security-events"])
        group_id = self._cfg.get("group_id", "soar-log-collector")
        poll_timeout = float(self._cfg.get("poll_timeout_ms", 1000)) / 1000.0
        security_protocol = self._cfg.get("security_protocol", "PLAINTEXT")

        consumer_conf: dict[str, Any] = {
            "bootstrap.servers": brokers,
            "group.id": group_id,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
            "security.protocol": security_protocol,
        }
        sasl_mechanism = self._cfg.get("sasl_mechanism", "")
        if sasl_mechanism:
            consumer_conf["sasl.mechanism"] = sasl_mechanism
            consumer_conf["sasl.username"] = self._cfg.get("sasl_username", "")
            consumer_conf["sasl.password"] = self._cfg.get("sasl_password", "")
        ssl_ca = self._cfg.get("ssl_ca_file", "")
        if ssl_ca:
            consumer_conf["ssl.ca.location"] = ssl_ca

        consumer = KafkaConsumer(consumer_conf)
        consumer.subscribe(topics)
        self._logger.info("Subscribed to Kafka topics: %s", topics)

        try:
            while not self._stop_event.is_set():
                msg = consumer.poll(timeout=poll_timeout)
                if msg is None:
                    continue
                if msg.error():
                    self._logger.warning("Kafka message error: %s", msg.error())
                    continue
                raw = msg.value().decode("utf-8", errors="replace") if msg.value() else ""
                event = _parse_json_log(raw, "kafka")
                if event:
                    event["kafka_topic"] = msg.topic()
                    event["kafka_partition"] = msg.partition()
                    event["kafka_offset"] = msg.offset()
                self._emit(event)
        except KafkaException as exc:
            self._logger.error("Kafka consumer error: %s", exc)
        finally:
            consumer.close()
            self._logger.info("Kafka consumer closed")


# ---------------------------------------------------------------------------
# Event batcher and forwarder
# ---------------------------------------------------------------------------

class EventBatcher:
    """
    Drains the normalized event queue, batches events, and forwards them.

    Batches are flushed when they reach ``max_batch_size`` OR when
    ``max_batch_age_seconds`` elapses since the first event in the batch,
    whichever comes first.

    Args:
        event_queue:        Source queue populated by collectors.
        sender:             Callable that accepts a list of event dicts and sends them.
        max_batch_size:     Maximum events per batch.
        max_batch_age_secs: Maximum seconds to hold a partial batch.
        workers:            Number of parallel sender threads.
    """

    def __init__(
        self,
        event_queue: queue.Queue[dict[str, Any]],
        sender: Any,
        max_batch_size: int = 500,
        max_batch_age_secs: float = 5.0,
        workers: int = 4,
    ) -> None:
        self._queue = event_queue
        self._sender = sender
        self._max_size = max_batch_size
        self._max_age = max_batch_age_secs
        self._send_queue: queue.Queue[list[dict[str, Any]]] = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        for i in range(workers):
            t = threading.Thread(target=self._send_worker, name=f"batcher-sender-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def run(self) -> None:
        """Drain the event queue and batch events. Blocks until stop() is called."""
        batch: list[dict[str, Any]] = []
        batch_start = time.monotonic()

        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.2)
                batch.append(event)
                self._queue.task_done()
            except queue.Empty:
                pass

            age = time.monotonic() - batch_start
            if batch and (len(batch) >= self._max_size or age >= self._max_age):
                self._dispatch(batch)
                batch = []
                batch_start = time.monotonic()

        # Flush remaining events on shutdown
        if batch:
            self._dispatch(batch)

    def _dispatch(self, batch: list[dict[str, Any]]) -> None:
        try:
            self._send_queue.put_nowait(list(batch))
        except queue.Full:
            log.warning("Send queue is full; dropping batch of %d events", len(batch))

    def _send_worker(self) -> None:
        while not self._stop.is_set():
            try:
                batch = self._send_queue.get(timeout=0.5)
                try:
                    self._sender(batch)
                except Exception as exc:  # noqa: BLE001
                    log.error("Sender failed for batch of %d events: %s", len(batch), exc)
                finally:
                    self._send_queue.task_done()
            except queue.Empty:
                continue

    def stop(self) -> None:
        """Signal the batcher to stop after flushing in-flight batches."""
        self._stop.set()


# ---------------------------------------------------------------------------
# n8n webhook sender (thin shim; full client is in n8n_webhook_sender.py)
# ---------------------------------------------------------------------------

class N8nBatchSender:
    """
    Sends event batches to the n8n webhook endpoint.

    Handles retries with exponential back-off and writes to a fallback
    JSONL file when the webhook is unreachable.

    Args:
        base_url:      n8n base URL (e.g. "http://n8n:5678").
        webhook_path:  Path to the event webhook (e.g. "/webhook/soar-events").
        api_key:       Optional n8n API key for authentication.
        timeout:       HTTP request timeout in seconds.
        max_retries:   Number of retry attempts before writing to fallback.
        fallback_file: Path to the JSONL fallback file.
    """

    def __init__(
        self,
        base_url: str,
        webhook_path: str,
        api_key: str = "",
        timeout: float = 10.0,
        max_retries: int = 5,
        fallback_file: str = "/var/lib/soar/n8n_fallback_queue.jsonl",
    ) -> None:
        self._url = f"{base_url.rstrip('/')}{webhook_path}"
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["X-N8N-API-KEY"] = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._fallback = fallback_file

    def __call__(self, batch: list[dict[str, Any]]) -> None:
        """Send a batch of normalized events to n8n."""
        import urllib.error
        import urllib.request

        payload = json.dumps({"events": batch}).encode("utf-8")
        backoff = 2.0
        for attempt in range(self._max_retries):
            try:
                req = urllib.request.Request(self._url, data=payload, headers=self._headers, method="POST")
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    if resp.status < 300:
                        log.debug("Sent batch of %d events to n8n (attempt %d)", len(batch), attempt + 1)
                        return
                    log.warning("n8n returned status %d on attempt %d", resp.status, attempt + 1)
            except (urllib.error.URLError, TimeoutError) as exc:
                log.warning("n8n unreachable on attempt %d: %s", attempt + 1, exc)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

        log.error("Failed to deliver batch of %d events after %d retries; writing to fallback", len(batch), self._max_retries)
        self._write_fallback(batch)

    def _write_fallback(self, batch: list[dict[str, Any]]) -> None:
        Path(self._fallback).parent.mkdir(parents=True, exist_ok=True)
        with open(self._fallback, "a", encoding="utf-8") as fh:
            for event in batch:
                fh.write(json.dumps(event) + "\n")


# ---------------------------------------------------------------------------
# Collector factory
# ---------------------------------------------------------------------------

_COLLECTOR_REGISTRY: dict[str, type[BaseCollector]] = {
    "nginx_access": NginxAccessCollector,
    "nginx_error": NginxErrorCollector,
    "application": ApplicationLogCollector,
    "auth_service": AuthServiceCollector,
    "aws_waf": AwsWafCollector,
    "kafka": KafkaCollector,
}


def _build_collectors(
    cfg: dict[str, Any],
    event_queue: queue.Queue[dict[str, Any]],
    dry_run: bool,
    source_filter: str | None,
) -> list[BaseCollector]:
    """Instantiate enabled collectors according to configuration."""
    collectors_cfg: dict[str, Any] = cfg.get("collectors", {})
    collectors: list[BaseCollector] = []
    for key, cls in _COLLECTOR_REGISTRY.items():
        source_cfg = collectors_cfg.get(key, {})
        enabled = _resolve_env(str(source_cfg.get("enabled", "true"))).lower() in ("true", "1", "yes")
        if not enabled:
            log.info("Collector '%s' is disabled in config", key)
            continue
        if source_filter and key != source_filter:
            continue
        collectors.append(cls(source_cfg, event_queue, dry_run))
        log.info("Registered collector: %s", key)
    return collectors


# ---------------------------------------------------------------------------
# Config loading helpers
# ---------------------------------------------------------------------------

def _resolve_env(value: str) -> str:
    """
    Expand environment variable references in config values.

    Supports the syntax ``${VAR_NAME:default_value}`` and ``${VAR_NAME}``.
    """
    pattern = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")

    def _replace(m: re.Match[str]) -> str:
        var, default = m.group(1), m.group(2) or ""
        return os.environ.get(var, default)

    return pattern.sub(_replace, value)


def _deep_resolve(obj: Any) -> Any:
    """Recursively resolve env vars in all string values of a config dict/list."""
    if isinstance(obj, dict):
        return {k: _deep_resolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_resolve(v) for v in obj]
    if isinstance(obj, str):
        return _resolve_env(obj)
    return obj


def load_config(path: str) -> dict[str, Any]:
    """
    Load and validate the SOAR YAML configuration file.

    Args:
        path: Absolute path to config.yml.

    Returns:
        Fully resolved configuration dictionary.

    Raises:
        SystemExit: When the file is missing or contains invalid YAML.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh) or {}
    except OSError as exc:
        log.error("Cannot read config file %s: %s", path, exc)
        sys.exit(1)
    except yaml.YAMLError as exc:
        log.error("Invalid YAML in config file %s: %s", path, exc)
        sys.exit(1)
    return _deep_resolve(raw)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run(cfg: dict[str, Any], dry_run: bool, source_filter: str | None = None) -> None:
    """
    Start all enabled collectors, the event batcher, and the n8n forwarder.

    Args:
        cfg:           Fully loaded and resolved configuration dict.
        dry_run:       When True, events are logged but not forwarded.
        source_filter: When set, only the named collector is started.
    """
    buffer_cfg = cfg.get("buffering", {})
    max_queue: int = int(buffer_cfg.get("queue_depth", 10000))
    max_batch: int = int(buffer_cfg.get("max_batch_size", 500))
    max_age: float = float(buffer_cfg.get("max_batch_age_seconds", 5))
    workers: int = int(buffer_cfg.get("sender_workers", 4))

    event_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_queue)

    # Build the n8n sender
    n8n_cfg = cfg.get("responders", {}).get("n8n", {})
    sender = N8nBatchSender(
        base_url=n8n_cfg.get("base_url", "http://n8n:5678"),
        webhook_path=n8n_cfg.get("event_webhook_path", "/webhook/soar-events"),
        api_key=n8n_cfg.get("api_key", ""),
        timeout=float(n8n_cfg.get("timeout_seconds", 10)),
        max_retries=int(n8n_cfg.get("max_retries", 5)),
        fallback_file=n8n_cfg.get("fallback_file", "/var/lib/soar/n8n_fallback_queue.jsonl"),
    )

    batcher = EventBatcher(event_queue, sender, max_batch, max_age, workers)
    collectors = _build_collectors(cfg, event_queue, dry_run, source_filter)

    if not collectors:
        log.error("No collectors are enabled or matched. Check configuration.")
        sys.exit(1)

    # Start each collector in its own daemon thread
    threads: list[threading.Thread] = []
    for collector in collectors:
        t = threading.Thread(target=collector.collect, name=f"collector-{collector.name}", daemon=True)
        t.start()
        threads.append(t)
        log.info("Started collector thread: %s", collector.name)

    # Run the batcher in the main thread (blocks until KeyboardInterrupt)
    try:
        batcher.run()
    except KeyboardInterrupt:
        log.info("Shutdown signal received – stopping collectors")
        for c in collectors:
            c.stop()
        batcher.stop()

    log.info("Log collector shut down cleanly")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AcmeToCasino SOAR unified log collector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        default="/etc/soar/config.yml",
        help="Path to the SOAR YAML configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and normalize events but do not forward them to n8n",
    )
    parser.add_argument(
        "--source",
        metavar="NAME",
        default=None,
        choices=list(_COLLECTOR_REGISTRY.keys()),
        help="Run only the named collector: %(choices)s",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config",
    )
    return parser


def main() -> None:
    """Entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    cfg = load_config(args.config)

    log_level = args.log_level or cfg.get("system", {}).get("log_level", "INFO")
    logging.getLogger().setLevel(getattr(logging, log_level.upper(), logging.INFO))

    log.info(
        "Starting SOAR log collector | env=%s dry_run=%s source=%s",
        cfg.get("system", {}).get("environment", "unknown"),
        args.dry_run,
        args.source or "all",
    )
    run(cfg, dry_run=args.dry_run, source_filter=args.source)


if __name__ == "__main__":
    main()
