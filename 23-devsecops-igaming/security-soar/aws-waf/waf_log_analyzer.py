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
AWS WAF Log Analyzer for AcmeToCasino iGaming Platform.

Reads WAF logs from the S3 bucket referenced in infra-terraform/waf.tf
(variable: waf-log-bucket), performs pattern analysis, produces a structured
threat intelligence report, and optionally forwards it to an n8n webhook for
SOAR pipeline consumption.

Log format: AWS WAF v2 writes one JSON object per line, gzip-compressed,
under the prefix  AWSLogs/<account>/<region>/...

Analysis performed:
  - Top N blocked source IPs
  - Top N triggered rule names
  - Geographic distribution of blocked requests (country code from WAF logs)
  - Attack timeline (requests per hour)
  - iGaming-specific signals: bonus-abuse paths, multi-account indicators

Usage:
    # Analyse last 24 hours, write report to stdout
    python waf_log_analyzer.py analyze \
        --bucket acmetocasino-waf-logs \
        --hours 24

    # Analyse and forward to n8n webhook
    python waf_log_analyzer.py analyze \
        --bucket acmetocasino-waf-logs \
        --hours 6 \
        --webhook https://n8n.internal/webhook/waf-threat-intel

    # List available log prefixes (useful for debugging)
    python waf_log_analyzer.py list-prefixes \
        --bucket acmetocasino-waf-logs \
        --prefix AWSLogs/
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import logging
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Logging – structured JSON
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level":     record.levelname,
            "logger":    record.name,
            "message":   record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str = __name__) -> logging.Logger:
    handler = logging.StreamHandler(sys.stderr)   # stderr keeps stdout clean for report JSON
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger("waf_log_analyzer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TOP_N       = 20
DEFAULT_HOURS       = 24
DEFAULT_LOG_PREFIX  = "AWSLogs/"
WEBHOOK_TIMEOUT_S   = 10

# Paths that are indicators of iGaming-specific abuse patterns.
BONUS_ABUSE_PATHS: tuple[str, ...] = (
    "/bonus",
    "/promo",
    "/free-spin",
    "/claim",
    "/voucher",
    "/referral",
    "/signup-bonus",
    "/welcome-offer",
)

# URI patterns associated with multi-accounting probes.
MULTI_ACCOUNT_PATHS: tuple[str, ...] = (
    "/register",
    "/signup",
    "/create-account",
    "/kyc",
    "/verification",
)

# ---------------------------------------------------------------------------
# S3 log fetching
# ---------------------------------------------------------------------------

def _s3_client(region: str | None = None) -> Any:
    return boto3.client("s3", region_name=region or boto3.session.Session().region_name)


def _list_log_keys(
    s3: Any,
    bucket: str,
    prefix: str,
    since: datetime,
) -> list[str]:
    """
    Return all S3 object keys under *prefix* with LastModified >= *since*.

    Uses a paginator to handle buckets with large numbers of objects.
    """
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[str] = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["LastModified"].replace(tzinfo=timezone.utc) >= since:
                keys.append(obj["Key"])

    log.info("Found %d log objects modified since %s under s3://%s/%s", len(keys), since.isoformat(), bucket, prefix)
    return keys


def _fetch_and_parse_log(s3: Any, bucket: str, key: str) -> list[dict[str, Any]]:
    """
    Download a WAF log object from S3, decompress if gzipped, and parse JSON lines.

    Returns a list of parsed log records (empty on parse errors).
    """
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        raw_bytes: bytes = obj["Body"].read()
    except ClientError as exc:
        log.warning("Failed to fetch s3://%s/%s: %s", bucket, key, exc)
        return []

    # Decompress if gzipped (WAF logs are always gzip by default)
    if key.endswith(".gz"):
        try:
            raw_bytes = gzip.decompress(raw_bytes)
        except OSError:
            pass  # Not actually gzipped, proceed with raw bytes

    records: list[dict[str, Any]] = []
    for line in raw_bytes.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            log.debug("Skipping malformed log line in %s: %s", key, exc)

    return records


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_logs(
    bucket: str,
    hours: int = DEFAULT_HOURS,
    prefix: str = DEFAULT_LOG_PREFIX,
    top_n: int = DEFAULT_TOP_N,
    region: str | None = None,
) -> dict[str, Any]:
    """
    Fetch WAF logs from S3 and compute threat intelligence metrics.

    Returns a report dict containing:
      - metadata: analysis window, record counts
      - top_blocked_ips: list of {ip, count}
      - top_triggered_rules: list of {rule, count}
      - geo_distribution: list of {country, count}
      - hourly_timeline: dict[hour_iso -> count]
      - igaming_signals: bonus_abuse_ips, multi_account_ips

    Args:
        bucket:  S3 bucket name (waf-log-bucket terraform variable value).
        hours:   Number of hours to look back.
        prefix:  S3 key prefix for WAF logs.
        top_n:   Number of top entries to include per category.
        region:  AWS region override.
    """
    s3         = _s3_client(region)
    since      = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    keys       = _list_log_keys(s3, bucket, prefix, since)

    total_records  = 0
    blocked_records = 0

    ip_counter:    Counter[str] = Counter()
    rule_counter:  Counter[str] = Counter()
    geo_counter:   Counter[str] = Counter()
    hour_counter:  Counter[str] = Counter()

    # iGaming signals: IP -> set of matched paths
    bonus_ips:    dict[str, set[str]] = defaultdict(set)
    multi_acct_ips: dict[str, set[str]] = defaultdict(set)

    for key in keys:
        records = _fetch_and_parse_log(s3, bucket, key)
        for rec in records:
            total_records += 1
            action       = rec.get("action", "")
            source_ip    = _extract_ip(rec)
            timestamp_ms = rec.get("timestamp", 0)
            uri          = _extract_uri(rec)
            country      = _extract_country(rec)

            if action != "BLOCK":
                continue

            blocked_records += 1
            ip_counter[source_ip] += 1

            for term_rule in rec.get("terminatingRuleId", ""), *_non_term_rules(rec):
                if term_rule:
                    rule_counter[term_rule] += 1

            if country:
                geo_counter[country] += 1

            if timestamp_ms:
                hour_str = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:00Z")
                hour_counter[hour_str] += 1

            _check_igaming_signal(source_ip, uri, bonus_ips, multi_acct_ips)

    log.info(
        "Analysis complete: %d total records, %d blocked, across %d log files",
        total_records, blocked_records, len(keys),
    )

    report: dict[str, Any] = {
        "metadata": {
            "generated_at":    datetime.now(tz=timezone.utc).isoformat(),
            "analysis_window_hours": hours,
            "since":           since.isoformat(),
            "bucket":          bucket,
            "log_files_scanned": len(keys),
            "total_records":   total_records,
            "blocked_records": blocked_records,
        },
        "top_blocked_ips": [
            {"ip": ip, "count": cnt}
            for ip, cnt in ip_counter.most_common(top_n)
        ],
        "top_triggered_rules": [
            {"rule": rule, "count": cnt}
            for rule, cnt in rule_counter.most_common(top_n)
        ],
        "geo_distribution": [
            {"country": country, "count": cnt}
            for country, cnt in geo_counter.most_common(top_n)
        ],
        "hourly_timeline": dict(
            sorted(hour_counter.items())
        ),
        "igaming_signals": {
            "bonus_abuse": [
                {"ip": ip, "matched_paths": sorted(paths)}
                for ip, paths in sorted(bonus_ips.items(), key=lambda kv: len(kv[1]), reverse=True)[:top_n]
            ],
            "multi_account_probing": [
                {"ip": ip, "matched_paths": sorted(paths)}
                for ip, paths in sorted(multi_acct_ips.items(), key=lambda kv: len(kv[1]), reverse=True)[:top_n]
            ],
        },
    }

    return report


# ---------------------------------------------------------------------------
# Log field extraction helpers
# ---------------------------------------------------------------------------

def _extract_ip(record: dict[str, Any]) -> str:
    """Extract source IP address from a WAF log record."""
    # WAF v2 JSON log structure: httpRequest.clientIp
    http_req = record.get("httpRequest", {})
    return http_req.get("clientIp", "unknown")


def _extract_uri(record: dict[str, Any]) -> str:
    """Extract request URI from a WAF log record."""
    http_req = record.get("httpRequest", {})
    return http_req.get("uri", "")


def _extract_country(record: dict[str, Any]) -> str:
    """Extract country code from a WAF log record."""
    http_req = record.get("httpRequest", {})
    return http_req.get("country", "")


def _non_term_rules(record: dict[str, Any]) -> list[str]:
    """Return rule names from non-terminating rule matches."""
    names: list[str] = []
    for match in record.get("nonTerminatingMatchingRules", []):
        names.append(match.get("ruleId", ""))
    for match in record.get("ruleGroupList", []):
        for rule in match.get("nonTerminatingMatchingRules", []):
            names.append(rule.get("ruleId", ""))
    return names


def _check_igaming_signal(
    ip: str,
    uri: str,
    bonus_ips: dict[str, set[str]],
    multi_acct_ips: dict[str, set[str]],
) -> None:
    """Update iGaming signal maps based on the request URI."""
    uri_lower = uri.lower()
    for path in BONUS_ABUSE_PATHS:
        if path in uri_lower:
            bonus_ips[ip].add(path)
    for path in MULTI_ACCOUNT_PATHS:
        if path in uri_lower:
            multi_acct_ips[ip].add(path)


# ---------------------------------------------------------------------------
# n8n webhook forwarding
# ---------------------------------------------------------------------------

def _send_to_webhook(report: dict[str, Any], webhook_url: str) -> None:
    """
    POST the threat intelligence report to an n8n webhook endpoint.

    Uses the stdlib urllib to avoid additional runtime dependencies.

    Args:
        report:      Serialisable report dict.
        webhook_url: Full HTTPS URL of the n8n webhook trigger node.
    """
    payload = json.dumps(report).encode("utf-8")
    req = urllib.request.Request(
        url=webhook_url,
        data=payload,
        method="POST",
        headers={
            "Content-Type":   "application/json",
            "Content-Length": str(len(payload)),
            "User-Agent":     "AcmeToCasino-WAF-Analyzer/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_S) as resp:
            status = resp.status
            body   = resp.read().decode("utf-8", errors="replace")
        if 200 <= status < 300:
            log.info("Webhook delivered successfully: HTTP %d – %s", status, body[:200])
        else:
            log.warning("Webhook returned non-2xx status %d: %s", status, body[:200])
    except OSError as exc:
        log.error("Failed to deliver report to webhook %s: %s", webhook_url, exc)
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WAF log analyzer and threat intelligence reporter for AcmeToCasino",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--region", default=None, help="AWS region")

    sub = parser.add_subparsers(dest="command", required=True)

    # analyze
    p_analyze = sub.add_parser("analyze", help="Fetch WAF logs from S3 and produce a threat report")
    p_analyze.add_argument("--bucket",  required=True, help="S3 bucket name (waf-log-bucket)")
    p_analyze.add_argument("--hours",   type=int, default=DEFAULT_HOURS,
                            help="Look-back window in hours (default: %(default)s)")
    p_analyze.add_argument("--prefix",  default=DEFAULT_LOG_PREFIX,
                            help="S3 key prefix for WAF logs (default: %(default)s)")
    p_analyze.add_argument("--top-n",   type=int, default=DEFAULT_TOP_N,
                            help="Number of top entries per category (default: %(default)s)")
    p_analyze.add_argument("--webhook", default=None,
                            help="n8n webhook URL to POST the report to (optional)")
    p_analyze.add_argument("--output",  default="-",
                            help="File path to write report JSON (default: stdout)")

    # list-prefixes
    p_list = sub.add_parser("list-prefixes", help="List S3 key prefixes for debugging")
    p_list.add_argument("--bucket",  required=True, help="S3 bucket name")
    p_list.add_argument("--prefix",  default=DEFAULT_LOG_PREFIX, help="Key prefix to list")
    p_list.add_argument("--limit",   type=int, default=50, help="Max keys to display")

    return parser


def main() -> None:
    """Entry point for CLI execution."""
    parser = _build_parser()
    args   = parser.parse_args()

    try:
        if args.command == "analyze":
            report = analyze_logs(
                bucket=args.bucket,
                hours=args.hours,
                prefix=args.prefix,
                top_n=args.top_n,
                region=args.region,
            )

            report_json = json.dumps(report, indent=2)

            if args.output == "-":
                print(report_json)
            else:
                with open(args.output, "w", encoding="utf-8") as fh:
                    fh.write(report_json)
                log.info("Report written to %s", args.output)

            if args.webhook:
                _send_to_webhook(report, args.webhook)

        elif args.command == "list-prefixes":
            s3 = _s3_client(args.region)
            paginator = s3.get_paginator("list_objects_v2")
            count = 0
            for page in paginator.paginate(Bucket=args.bucket, Prefix=args.prefix):
                for obj in page.get("Contents", []):
                    print(obj["Key"])
                    count += 1
                    if count >= args.limit:
                        log.info("Displayed %d keys (limit reached)", count)
                        return
            log.info("Listed %d keys under s3://%s/%s", count, args.bucket, args.prefix)

    except (ClientError, RuntimeError, OSError) as exc:
        log.error("Command '%s' failed: %s", args.command, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
