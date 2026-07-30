# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Attack Evidence Collector for iGaming Platforms

Post-attack forensic tool that:
  1. Queries WAF logs in S3 via Athena for all IPs blocked during the attack
  2. Resolves ASN and organisation for each IP using MaxMind GeoIP2
  3. Groups IPs by ASN and generates structured abuse reports
  4. Saves evidence to S3 as JSON + formatted email templates
  5. Optionally sends abuse reports via Amazon SES

The collector runs post-attack (triggered by EventBridge after the
waf_shield_manager deactivates, or invoked manually by NOC staff).

AWS services used:
  - Athena (WAF log queries against S3)
  - S3 (evidence storage)
  - SES (abuse email delivery, optional)
  - SNS (NOC notification of evidence availability)

External libraries:
  - geoip2 (MaxMind ASN lookup — requires mmdb file in Lambda layer or /tmp)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from typing import Any

import boto3
from botocore.exceptions import ClientError

try:
    import geoip2.database
    import geoip2.errors

    GEOIP_AVAILABLE = True
except ImportError:
    GEOIP_AVAILABLE = False
    geoip2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")
ATHENA_DATABASE: str = os.environ.get("ATHENA_DATABASE", "waf_logs")
ATHENA_WAF_TABLE: str = os.environ.get("ATHENA_WAF_TABLE", "waf_blocked_requests")
ATHENA_RESULTS_BUCKET: str = os.environ.get("ATHENA_RESULTS_BUCKET", "")
EVIDENCE_BUCKET: str = os.environ.get("EVIDENCE_BUCKET", "")
EVIDENCE_PREFIX: str = os.environ.get("EVIDENCE_PREFIX", "attack-evidence/")
MAXMIND_DB_PATH: str = os.environ.get("MAXMIND_DB_PATH", "/opt/GeoLite2-ASN.mmdb")
SES_FROM_ADDRESS: str = os.environ.get("SES_FROM_ADDRESS", "")
SES_REGION: str = os.environ.get("SES_REGION", "us-east-1")
SNS_NOC_TOPIC_ARN: str = os.environ.get("SNS_NOC_TOPIC_ARN", "")
ATHENA_POLL_INTERVAL_SECONDS: int = int(os.environ.get("ATHENA_POLL_INTERVAL_SECONDS", "3"))
ATHENA_MAX_POLLS: int = int(os.environ.get("ATHENA_MAX_POLLS", "40"))
MAX_IPS_PER_ASN_IN_REPORT: int = int(os.environ.get("MAX_IPS_PER_ASN_IN_REPORT", "200"))

# Abuse contact lookup via RDAP / ARIN (fallback)
RDAP_TIMEOUT_SECONDS: int = int(os.environ.get("RDAP_TIMEOUT_SECONDS", "5"))


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass
class BlockedIPRecord:
    """A single blocked IP entry from WAF logs."""

    ip_address: str
    block_count: int
    first_seen: str
    last_seen: str
    rule_names: list[str] = field(default_factory=list)
    http_methods: list[str] = field(default_factory=list)
    uri_paths: list[str] = field(default_factory=list)
    user_agents: list[str] = field(default_factory=list)
    # Populated by ASN lookup
    asn: int = 0
    asn_org: str = ""
    country: str = ""
    abuse_contact: str = ""


@dataclass
class ASNGroup:
    """All blocked IPs attributed to a single ASN."""

    asn: int
    org_name: str
    abuse_contact: str
    country: str
    ip_records: list[BlockedIPRecord] = field(default_factory=list)
    total_blocked_requests: int = 0

    @property
    def unique_ip_count(self) -> int:
        return len(self.ip_records)


@dataclass
class EvidenceBundle:
    """Complete post-attack evidence package."""

    attack_id: str
    analysis_start: str
    analysis_end: str
    total_blocked_ips: int
    total_blocked_requests: int
    asn_groups: list[ASNGroup] = field(default_factory=list)
    s3_evidence_key: str = ""
    s3_report_keys: list[str] = field(default_factory=list)
    ses_emails_sent: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# AWS clients
# ---------------------------------------------------------------------------

_athena = None
_s3 = None
_ses = None
_sns = None
_geoip_reader: Any = None


def _athena_client() -> Any:
    global _athena
    if _athena is None:
        _athena = boto3.client("athena", region_name=AWS_REGION)
    return _athena


def _s3_client() -> Any:
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=AWS_REGION)
    return _s3


def _ses_client() -> Any:
    global _ses
    if _ses is None:
        _ses = boto3.client("ses", region_name=SES_REGION)
    return _ses


def _sns_client() -> Any:
    global _sns
    if _sns is None:
        _sns = boto3.client("sns", region_name=AWS_REGION)
    return _sns


def _get_geoip_reader() -> Any | None:
    """Return a cached GeoIP2 ASN reader, or None if unavailable."""
    global _geoip_reader
    if not GEOIP_AVAILABLE:
        return None
    if _geoip_reader is not None:
        return _geoip_reader
    if not os.path.exists(MAXMIND_DB_PATH):
        logger.warning("MaxMind database not found at %s", MAXMIND_DB_PATH)
        return None
    try:
        _geoip_reader = geoip2.database.Reader(MAXMIND_DB_PATH)
        logger.info("MaxMind GeoIP2 ASN database loaded from %s", MAXMIND_DB_PATH)
        return _geoip_reader
    except Exception as exc:
        logger.warning("Failed to open MaxMind database: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Athena WAF log query
# ---------------------------------------------------------------------------


def _run_athena_query(sql: str) -> str | None:
    if not ATHENA_RESULTS_BUCKET:
        logger.error("ATHENA_RESULTS_BUCKET not configured")
        return None
    try:
        resp = _athena_client().start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": ATHENA_DATABASE},
            ResultConfiguration={
                "OutputLocation": (
                    f"s3://{ATHENA_RESULTS_BUCKET}/athena-results/evidence-collector/"
                )
            },
        )
        return resp["QueryExecutionId"]
    except ClientError as exc:
        logger.error("Athena query start failed: %s", exc)
        return None


def _wait_athena_query(execution_id: str) -> bool:
    for _ in range(ATHENA_MAX_POLLS):
        try:
            resp = _athena_client().get_query_execution(QueryExecutionId=execution_id)
            state = resp["QueryExecution"]["Status"]["State"]
            if state == "SUCCEEDED":
                return True
            if state in ("FAILED", "CANCELLED"):
                reason = resp["QueryExecution"]["Status"].get("StateChangeReason", "")
                logger.error("Athena query %s: %s — %s", execution_id, state, reason)
                return False
        except ClientError as exc:
            logger.error("Athena poll failed: %s", exc)
            return False
        time.sleep(ATHENA_POLL_INTERVAL_SECONDS)

    logger.error("Athena query %s timed out", execution_id)
    return False


def _paginate_athena_results(execution_id: str) -> list[dict[str, str]]:
    """Fetch all result rows across multiple pages."""
    rows: list[dict[str, str]] = []
    headers: list[str] = []
    next_token: str | None = None
    first_page = True

    while True:
        try:
            kwargs: dict[str, Any] = {
                "QueryExecutionId": execution_id,
                "MaxResults": 1000,
            }
            if next_token:
                kwargs["NextToken"] = next_token

            resp = _athena_client().get_query_results(**kwargs)
            result_rows = resp.get("ResultSet", {}).get("Rows", [])

            if first_page and result_rows:
                headers = [col.get("VarCharValue", "") for col in result_rows[0]["Data"]]
                result_rows = result_rows[1:]
                first_page = False

            for row in result_rows:
                values = [cell.get("VarCharValue", "") for cell in row["Data"]]
                rows.append(dict(zip(headers, values)))

            next_token = resp.get("NextToken")
            if not next_token:
                break

        except ClientError as exc:
            logger.error("Athena results pagination failed: %s", exc)
            break

    return rows


def query_waf_blocked_ips(
    attack_start: str,
    attack_end: str,
) -> list[BlockedIPRecord]:
    """
    Query WAF logs in Athena for all blocked IPs between attack_start
    and attack_end (ISO-8601 strings).

    Expected WAF logs Athena table schema (AWS WAF Full Logs format):
      timestamp, action, httprequest.clientip, httprequest.uri,
      httprequest.httpmethod, terminatingruleid, useragent

    Returns a list of BlockedIPRecord, one per unique IP.
    """
    sql = f"""
        SELECT
            httprequest.clientip                            AS client_ip,
            COUNT(*)                                        AS block_count,
            MIN(timestamp)                                  AS first_seen,
            MAX(timestamp)                                  AS last_seen,
            ARRAY_AGG(DISTINCT terminatingruleid)           AS rule_names,
            ARRAY_AGG(DISTINCT httprequest.httpmethod)      AS http_methods,
            ARRAY_AGG(DISTINCT httprequest.uri LIMIT 10)    AS uri_samples,
            ARRAY_AGG(DISTINCT
                COALESCE(
                    httprequest.headers['user-agent'],
                    'UNKNOWN'
                ) LIMIT 5
            )                                               AS user_agents
        FROM {ATHENA_DATABASE}.{ATHENA_WAF_TABLE}
        WHERE action = 'BLOCK'
          AND from_iso8601_timestamp(timestamp) BETWEEN
              TIMESTAMP '{attack_start.replace("T", " ").split("+")[0]}'
              AND
              TIMESTAMP '{attack_end.replace("T", " ").split("+")[0]}'
        GROUP BY httprequest.clientip
        ORDER BY block_count DESC
    """

    logger.info("Querying WAF logs: %s → %s", attack_start, attack_end)
    exec_id = _run_athena_query(sql)
    if not exec_id:
        return []

    if not _wait_athena_query(exec_id):
        return []

    raw_rows = _paginate_athena_results(exec_id)
    records: list[BlockedIPRecord] = []

    for row in raw_rows:
        ip_str = row.get("client_ip", "").strip()
        if not ip_str:
            continue

        # Validate IP
        try:
            ip_address(ip_str)
        except ValueError:
            logger.debug("Skipping invalid IP: %s", ip_str)
            continue

        # Parse array columns — Athena returns them as "[a, b, c]" strings
        rule_names = _parse_athena_array(row.get("rule_names", "[]"))
        http_methods = _parse_athena_array(row.get("http_methods", "[]"))
        uri_paths = _parse_athena_array(row.get("uri_samples", "[]"))
        user_agents = _parse_athena_array(row.get("user_agents", "[]"))

        records.append(
            BlockedIPRecord(
                ip_address=ip_str,
                block_count=int(row.get("block_count", 0)),
                first_seen=row.get("first_seen", ""),
                last_seen=row.get("last_seen", ""),
                rule_names=rule_names,
                http_methods=http_methods,
                uri_paths=uri_paths,
                user_agents=user_agents,
            )
        )

    logger.info("WAF query returned %d unique blocked IPs", len(records))
    return records


def _parse_athena_array(value: str) -> list[str]:
    """
    Parse Athena ARRAY_AGG result string into a Python list.
    Athena returns arrays as "[item1, item2, ...]".
    """
    if not value or value in ("[]", "null", ""):
        return []
    value = value.strip("[]")
    # Handle quoted strings and bare strings
    try:
        parsed = json.loads(f"[{value}]")
        return [str(item) for item in parsed if item is not None]
    except (json.JSONDecodeError, ValueError):
        return [s.strip().strip('"').strip("'") for s in value.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# ASN resolution
# ---------------------------------------------------------------------------


def resolve_asn(ip_str: str) -> tuple[int, str, str]:
    """
    Resolve ASN, organisation name, and country for an IP.

    Tries MaxMind GeoIP2 first, then falls back to a basic socket lookup.

    Returns:
        (asn_number, org_name, country_iso)
    """
    reader = _get_geoip_reader()
    if reader is not None:
        try:
            resp = reader.asn(ip_str)
            asn_number = resp.autonomous_system_number or 0
            asn_org = resp.autonomous_system_organization or "UNKNOWN"
            return asn_number, asn_org, ""
        except geoip2.errors.AddressNotFoundError:
            pass
        except Exception as exc:
            logger.debug("MaxMind lookup failed for %s: %s", ip_str, exc)

    # Fallback: reverse DNS to get rough org context (best-effort)
    try:
        hostname = socket.gethostbyaddr(ip_str)[0]
        # Extract org hint from hostname suffix (e.g. amazonaws.com → AWS)
        return 0, hostname, ""
    except (socket.herror, socket.gaierror):
        pass

    return 0, "UNKNOWN", ""


def lookup_abuse_contact(asn: int, ip_str: str) -> str:
    """
    Look up abuse contact email for an ASN.

    Uses a static map for major providers first, then attempts RDAP.
    RDAP queries are wrapped with a short timeout to avoid Lambda timeouts.
    """
    # Static map for the largest cloud/hosting providers
    known_abuse_contacts: dict[str, str] = {
        "amazon": "abuse@amazon.com",
        "amazonaws": "abuse@amazon.com",
        "google": "network-abuse@google.com",
        "microsoft": "abuse@microsoft.com",
        "cloudflare": "abuse@cloudflare.com",
        "digitalocean": "abuse@digitalocean.com",
        "ovh": "abuse@ovh.net",
        "hetzner": "abuse@hetzner.com",
        "linode": "abuse@linode.com",
        "vultr": "abuse@vultr.com",
        "choopa": "abuse@vultr.com",
        "zenlayer": "abuse@zenlayer.com",
    }

    try:
        reader = _get_geoip_reader()
        if reader is not None:
            org_lower = reader.asn(ip_str).autonomous_system_organization.lower()
            for keyword, contact in known_abuse_contacts.items():
                if keyword in org_lower:
                    return contact
    except Exception:
        pass

    # Generic fallback
    return f"abuse+as{asn}@example.com" if asn else "abuse@example.com"


def enrich_records_with_asn(records: list[BlockedIPRecord]) -> list[BlockedIPRecord]:
    """
    Bulk-resolve ASN for all IP records.  Cached per-ASN to avoid redundant
    MaxMind lookups for IPs in the same subnet.
    """
    asn_cache: dict[str, tuple[int, str, str]] = {}

    for record in records:
        ip_str = record.ip_address

        # Use /24 as cache key to batch subnet neighbours
        try:
            net = str(ip_network(f"{ip_str}/24", strict=False).network_address)
            cache_key = net
        except ValueError:
            cache_key = ip_str

        if cache_key not in asn_cache:
            asn, org, country = resolve_asn(ip_str)
            abuse = lookup_abuse_contact(asn, ip_str)
            asn_cache[cache_key] = (asn, org, country)
        else:
            asn, org, country = asn_cache[cache_key]
            abuse = lookup_abuse_contact(asn, ip_str)

        record.asn = asn
        record.asn_org = org
        record.country = country
        record.abuse_contact = abuse

    logger.info("ASN enrichment complete for %d records", len(records))
    return records


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def group_by_asn(records: list[BlockedIPRecord]) -> list[ASNGroup]:
    """Group enriched IP records by ASN for abuse reporting."""
    asn_map: dict[int, ASNGroup] = {}

    for record in records:
        asn_key = record.asn or 0
        if asn_key not in asn_map:
            asn_map[asn_key] = ASNGroup(
                asn=record.asn,
                org_name=record.asn_org or "UNKNOWN",
                abuse_contact=record.abuse_contact,
                country=record.country,
            )
        group = asn_map[asn_key]
        group.ip_records.append(record)
        group.total_blocked_requests += record.block_count

    # Sort by total blocked requests descending
    groups = sorted(asn_map.values(), key=lambda g: g.total_blocked_requests, reverse=True)
    logger.info("Grouped into %d ASN groups", len(groups))
    return groups


def _format_abuse_email(
    group: ASNGroup,
    attack_id: str,
    attack_start: str,
    attack_end: str,
    victim_domain: str = "",
) -> str:
    """Generate a formatted abuse report email body."""
    ip_table_rows = []
    for r in group.ip_records[:MAX_IPS_PER_ASN_IN_REPORT]:
        ip_table_rows.append(
            f"  {r.ip_address:<18} {r.block_count:>8} requests  "
            f"{r.first_seen}  →  {r.last_seen}"
        )

    ip_table = "\n".join(ip_table_rows)
    if len(group.ip_records) > MAX_IPS_PER_ASN_IN_REPORT:
        ip_table += (
            f"\n  ... and {len(group.ip_records) - MAX_IPS_PER_ASN_IN_REPORT} more IPs "
            f"(see attached JSON)"
        )

    domain_line = f"Targeted service: {victim_domain}\n" if victim_domain else ""

    return f"""Dear Abuse Team,

We are writing to report a Distributed Denial of Service (DDoS) attack
that originated from IP addresses within your network (AS{group.asn} / {group.org_name}).

{domain_line}Attack reference:  {attack_id}
Attack window:     {attack_start}  →  {attack_end}
Total IPs from AS: {group.unique_ip_count}
Total requests:    {group.total_blocked_requests:,}

The following IP addresses from your autonomous system were observed
sending malicious traffic and have been blocked by our WAF:

  {"IP Address":<18} {"Requests":>8}  {"First Seen":<26}  Last Seen
  {"-" * 80}
{ip_table}

We respectfully request that you investigate and take action against
the responsible parties. Please ensure these IPs are reviewed for
compromise or abuse.

If you require further evidence or a full list of blocked IPs (as a
machine-readable JSON file), please contact us at the address below.

This report was generated automatically by our security infrastructure
in compliance with our incident response procedures.

Regards,
Security Operations
{victim_domain or "iGaming Platform"}
"""


def generate_reports(
    asn_groups: list[ASNGroup],
    attack_id: str,
    attack_start: str,
    attack_end: str,
    victim_domain: str = "",
) -> dict[int, str]:
    """
    Generate one formatted email template per ASN group.

    Returns:
        Dict mapping asn_number → formatted email body string.
    """
    return {
        group.asn: _format_abuse_email(
            group, attack_id, attack_start, attack_end, victim_domain
        )
        for group in asn_groups
    }


# ---------------------------------------------------------------------------
# S3 persistence
# ---------------------------------------------------------------------------


def save_evidence_to_s3(
    attack_id: str,
    bundle: EvidenceBundle,
    asn_groups: list[ASNGroup],
    email_templates: dict[int, str],
) -> list[str]:
    """
    Save all evidence to S3:
      - {prefix}/{attack_id}/evidence.json  — full structured evidence
      - {prefix}/{attack_id}/summary.csv    — per-IP CSV for analysts
      - {prefix}/{attack_id}/reports/{asn}.txt  — email template per ASN

    Returns:
        List of S3 keys written.
    """
    if not EVIDENCE_BUCKET:
        logger.warning("EVIDENCE_BUCKET not configured — skipping S3 save")
        return []

    keys_written: list[str] = []
    prefix = f"{EVIDENCE_PREFIX}{attack_id}/"

    # 1. Full JSON evidence
    evidence_json = json.dumps(
        {
            "attack_id": attack_id,
            "total_blocked_ips": bundle.total_blocked_ips,
            "total_blocked_requests": bundle.total_blocked_requests,
            "asn_groups": [
                {
                    "asn": g.asn,
                    "org_name": g.org_name,
                    "abuse_contact": g.abuse_contact,
                    "country": g.country,
                    "unique_ips": g.unique_ip_count,
                    "total_requests": g.total_blocked_requests,
                    "ips": [
                        {
                            "ip": r.ip_address,
                            "block_count": r.block_count,
                            "first_seen": r.first_seen,
                            "last_seen": r.last_seen,
                            "rule_names": r.rule_names,
                            "http_methods": r.http_methods,
                        }
                        for r in g.ip_records
                    ],
                }
                for g in asn_groups
            ],
            "timestamp": bundle.timestamp,
        },
        indent=2,
    )

    evidence_key = f"{prefix}evidence.json"
    try:
        _s3_client().put_object(
            Bucket=EVIDENCE_BUCKET,
            Key=evidence_key,
            Body=evidence_json.encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
        keys_written.append(evidence_key)
        logger.info("Evidence JSON saved: s3://%s/%s", EVIDENCE_BUCKET, evidence_key)
    except ClientError as exc:
        logger.error("Failed to save evidence JSON: %s", exc)
        bundle.errors.append(f"S3 evidence.json write failed: {exc}")

    # 2. CSV summary
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(
        [
            "ip_address",
            "asn",
            "org",
            "country",
            "block_count",
            "first_seen",
            "last_seen",
            "abuse_contact",
            "rules",
        ]
    )
    for group in asn_groups:
        for record in group.ip_records:
            writer.writerow(
                [
                    record.ip_address,
                    record.asn,
                    record.asn_org,
                    record.country,
                    record.block_count,
                    record.first_seen,
                    record.last_seen,
                    record.abuse_contact,
                    "|".join(record.rule_names),
                ]
            )

    csv_key = f"{prefix}summary.csv"
    try:
        _s3_client().put_object(
            Bucket=EVIDENCE_BUCKET,
            Key=csv_key,
            Body=csv_buffer.getvalue().encode("utf-8"),
            ContentType="text/csv",
            ServerSideEncryption="AES256",
        )
        keys_written.append(csv_key)
    except ClientError as exc:
        logger.error("Failed to save CSV summary: %s", exc)

    # 3. Email templates per ASN
    for asn, email_body in email_templates.items():
        report_key = f"{prefix}reports/as{asn}.txt"
        try:
            _s3_client().put_object(
                Bucket=EVIDENCE_BUCKET,
                Key=report_key,
                Body=email_body.encode("utf-8"),
                ContentType="text/plain",
                ServerSideEncryption="AES256",
            )
            keys_written.append(report_key)
        except ClientError as exc:
            logger.warning("Failed to save report for AS%d: %s", asn, exc)

    logger.info("Saved %d files to S3 prefix %s%s", len(keys_written), EVIDENCE_BUCKET, prefix)
    return keys_written


# ---------------------------------------------------------------------------
# SES delivery
# ---------------------------------------------------------------------------


def send_abuse_reports_via_ses(
    asn_groups: list[ASNGroup],
    email_templates: dict[int, str],
    attack_id: str,
    min_requests_threshold: int = 1000,
) -> list[str]:
    """
    Send abuse report emails via SES for ASN groups above the minimum
    request threshold.  Only sends to groups where we have a valid
    abuse contact address.

    Args:
        asn_groups:               All ASN groups from the evidence bundle.
        email_templates:          Pre-generated email bodies keyed by ASN.
        attack_id:                For Subject line correlation.
        min_requests_threshold:   Minimum blocked requests to warrant emailing.

    Returns:
        List of abuse contact addresses that were emailed.
    """
    if not SES_FROM_ADDRESS:
        logger.info("SES_FROM_ADDRESS not configured — skipping email delivery")
        return []

    sent_to: list[str] = []

    for group in asn_groups:
        if group.total_blocked_requests < min_requests_threshold:
            continue
        if not group.abuse_contact or group.abuse_contact.endswith("@example.com"):
            continue
        if group.asn not in email_templates:
            continue

        email_body = email_templates[group.asn]
        subject = (
            f"[Abuse Report] DDoS Attack from AS{group.asn} ({group.org_name}) — {attack_id}"
        )

        try:
            _ses_client().send_email(
                Source=SES_FROM_ADDRESS,
                Destination={"ToAddresses": [group.abuse_contact]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": email_body, "Charset": "UTF-8"}},
                },
            )
            sent_to.append(group.abuse_contact)
            logger.info(
                "Abuse report sent to %s (AS%d, %d IPs, %d requests)",
                group.abuse_contact,
                group.asn,
                group.unique_ip_count,
                group.total_blocked_requests,
            )
            # SES send rate limit: 14 emails/sec on sandbox, 50K/day on prod
            time.sleep(0.1)
        except ClientError as exc:
            logger.warning(
                "SES send failed to %s: %s", group.abuse_contact, exc
            )

    logger.info("Abuse reports sent to %d contacts", len(sent_to))
    return sent_to


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def collect_evidence(
    attack_id: str,
    attack_start: str,
    attack_end: str,
    victim_domain: str = "",
    send_emails: bool = False,
    min_email_threshold: int = 1000,
) -> EvidenceBundle:
    """
    Full evidence collection pipeline:
      1. Query WAF logs via Athena
      2. Enrich with ASN data
      3. Group by ASN
      4. Generate abuse report templates
      5. Save to S3
      6. Optionally send via SES
      7. Notify NOC

    Args:
        attack_id:            Unique attack identifier.
        attack_start:         ISO-8601 attack start timestamp.
        attack_end:           ISO-8601 attack end timestamp.
        victim_domain:        Domain of the targeted service (for reports).
        send_emails:          Whether to send abuse reports via SES.
        min_email_threshold:  Minimum requests to trigger an SES send.

    Returns:
        EvidenceBundle with full pipeline results.
    """
    bundle = EvidenceBundle(
        attack_id=attack_id,
        analysis_start=attack_start,
        analysis_end=attack_end,
        total_blocked_ips=0,
        total_blocked_requests=0,
    )

    logger.info("Starting evidence collection for attack %s", attack_id)

    # Step 1: Query WAF logs
    records = query_waf_blocked_ips(attack_start, attack_end)
    if not records:
        logger.warning("No blocked IP records found for attack %s", attack_id)
        bundle.errors.append("No WAF blocked IP records found")
        return bundle

    bundle.total_blocked_ips = len(records)
    bundle.total_blocked_requests = sum(r.block_count for r in records)

    # Step 2: Enrich with ASN
    records = enrich_records_with_asn(records)

    # Step 3: Group by ASN
    asn_groups = group_by_asn(records)
    bundle.asn_groups = asn_groups

    # Step 4: Generate email templates
    email_templates = generate_reports(
        asn_groups, attack_id, attack_start, attack_end, victim_domain
    )

    # Step 5: Save to S3
    bundle.s3_report_keys = save_evidence_to_s3(
        attack_id, bundle, asn_groups, email_templates
    )

    # Step 6: Optionally send via SES
    if send_emails:
        bundle.ses_emails_sent = send_abuse_reports_via_ses(
            asn_groups, email_templates, attack_id, min_email_threshold
        )

    # Step 7: Notify NOC
    if SNS_NOC_TOPIC_ARN:
        try:
            _sns_client().publish(
                TopicArn=SNS_NOC_TOPIC_ARN,
                Subject=f"[EVIDENCE] Attack evidence ready — {attack_id}",
                Message=json.dumps(
                    {
                        "attack_id": attack_id,
                        "total_blocked_ips": bundle.total_blocked_ips,
                        "total_blocked_requests": bundle.total_blocked_requests,
                        "asn_groups_count": len(asn_groups),
                        "s3_keys": bundle.s3_report_keys[:10],
                        "emails_sent": bundle.ses_emails_sent,
                        "errors": bundle.errors,
                        "timestamp": bundle.timestamp,
                    },
                    indent=2,
                ),
            )
        except ClientError as exc:
            logger.warning("NOC notification failed: %s", exc)

    logger.info(
        "Evidence collection complete: %d IPs, %d ASNs, %d S3 files, %d emails",
        bundle.total_blocked_ips,
        len(asn_groups),
        len(bundle.s3_report_keys),
        len(bundle.ses_emails_sent),
    )
    return bundle


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Expected event:
      {
        "attack_id":           "attack-20260331-001",
        "attack_start":        "2026-03-31T14:00:00Z",
        "attack_end":          "2026-03-31T15:30:00Z",
        "victim_domain":       "casino.example.com",
        "send_emails":         false,
        "min_email_threshold": 1000
      }
    """
    logger.info("Evidence collector invoked: %s", json.dumps(event, default=str))

    attack_id = event.get("attack_id", f"attack-{int(time.time())}")
    attack_start = event.get("attack_start", "")
    attack_end = event.get("attack_end", "")

    if not attack_start or not attack_end:
        return {
            "error": "attack_start and attack_end are required (ISO-8601)",
            "event": event,
        }

    bundle = collect_evidence(
        attack_id=attack_id,
        attack_start=attack_start,
        attack_end=attack_end,
        victim_domain=event.get("victim_domain", ""),
        send_emails=bool(event.get("send_emails", False)),
        min_email_threshold=int(event.get("min_email_threshold", 1000)),
    )

    return {
        "attack_id": bundle.attack_id,
        "total_blocked_ips": bundle.total_blocked_ips,
        "total_blocked_requests": bundle.total_blocked_requests,
        "asn_groups_count": len(bundle.asn_groups),
        "top_asns": [
            {
                "asn": g.asn,
                "org": g.org_name,
                "ips": g.unique_ip_count,
                "requests": g.total_blocked_requests,
            }
            for g in bundle.asn_groups[:10]
        ],
        "s3_evidence_keys": bundle.s3_report_keys,
        "emails_sent": bundle.ses_emails_sent,
        "errors": bundle.errors,
        "timestamp": bundle.timestamp,
    }
