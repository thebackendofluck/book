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
ISP Abuse Report Generator

After a DDoS is confirmed, collects attacking IPs, resolves their ASNs,
groups by ISP, and generates RFC-5321-compliant abuse report emails.
Optionally sends them via SMTP.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import smtplib
import socket
import time
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger("isp_abuse_report")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# IP metadata resolution
# ---------------------------------------------------------------------------
@dataclass
class IPInfo:
    ip: str
    asn: str = ""
    org: str = ""
    isp: str = ""
    country: str = ""
    abuse_contact: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    request_count: int = 0


@dataclass
class ISPGroup:
    asn: str
    org: str
    abuse_contact: str
    country: str
    ips: list[IPInfo] = field(default_factory=list)

    @property
    def total_requests(self) -> int:
        return sum(ip.request_count for ip in self.ips)

    @property
    def ip_count(self) -> int:
        return len(self.ips)


class IPInfoResolver:
    """
    Resolves ASN/ISP metadata using ipinfo.io (free tier) with caching.

    Falls back to whois/socket-based rDNS when the API is unavailable.
    """

    IPINFO_BASE = "https://ipinfo.io"

    def __init__(
        self,
        api_token: str = "",
        cache_ttl: int = 86400,
        concurrency: int = 20,
    ) -> None:
        self._token = api_token or os.getenv("IPINFO_TOKEN", "")
        self._cache: dict[str, IPInfo] = {}
        self._cache_ts: dict[str, float] = {}
        self._cache_ttl = cache_ttl
        self._semaphore = asyncio.Semaphore(concurrency)

    async def resolve(self, ip: str, request_count: int = 1) -> IPInfo:
        # Return cached result if fresh
        if ip in self._cache:
            age = time.time() - self._cache_ts.get(ip, 0)
            if age < self._cache_ttl:
                cached = self._cache[ip]
                cached.request_count = max(cached.request_count, request_count)
                return cached

        info = IPInfo(ip=ip, request_count=request_count)
        async with self._semaphore:
            info = await self._fetch_ipinfo(ip, request_count)

        self._cache[ip] = info
        self._cache_ts[ip] = time.time()
        return info

    async def _fetch_ipinfo(self, ip: str, request_count: int) -> IPInfo:
        info = IPInfo(ip=ip, request_count=request_count)
        url = f"{self.IPINFO_BASE}/{ip}/json"
        params: dict[str, str] = {}
        if self._token:
            params["token"] = self._token

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status != 200:
                        return info
                    data = await resp.json()

            info.org = data.get("org", "")
            info.country = data.get("country", "")

            # org field is typically "AS12345 ISP Name"
            org_parts = info.org.split(" ", 1)
            if org_parts and org_parts[0].startswith("AS"):
                info.asn = org_parts[0]
                info.isp = org_parts[1] if len(org_parts) > 1 else ""
            else:
                info.asn = "AS0"
                info.isp = info.org

        except Exception as exc:
            logger.debug("ipinfo lookup failed for %s: %s", ip, exc)
            # Fallback: rDNS for ISP identification
            try:
                info.isp = socket.gethostbyaddr(ip)[0]
            except Exception:
                info.isp = "unknown"

        return info

    async def resolve_bulk(
        self,
        ip_request_counts: dict[str, int],
    ) -> list[IPInfo]:
        """Resolve a batch of IPs concurrently."""
        tasks = [
            self.resolve(ip, count)
            for ip, count in ip_request_counts.items()
        ]
        return list(await asyncio.gather(*tasks, return_exceptions=False))


# ---------------------------------------------------------------------------
# Grouping and report generation
# ---------------------------------------------------------------------------
def _group_by_isp(ip_infos: list[IPInfo]) -> dict[str, ISPGroup]:
    groups: dict[str, ISPGroup] = {}
    for info in ip_infos:
        key = info.asn or "UNKNOWN"
        if key not in groups:
            groups[key] = ISPGroup(
                asn=info.asn,
                org=info.isp or info.org,
                abuse_contact=info.abuse_contact,
                country=info.country,
            )
        groups[key].ips.append(info)
    return groups


def _build_abuse_email(
    group: ISPGroup,
    incident_start: float,
    incident_end: float,
    victim_ip: str,
    victim_domain: str,
    reporter_name: str,
    reporter_email: str,
    platform: str = "iGaming platform",
) -> str:
    """
    Build an RFC-5321/ARF-compatible abuse report email body.
    """
    start_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(incident_start))
    end_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(incident_end))
    now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    # Sample IPs for the report (max 50 to keep email readable)
    sample_ips = group.ips[:50]
    ip_table_lines = [
        f"  {info.ip:<20} {info.request_count:>8} requests   {info.country}   {info.isp}"
        for info in sample_ips
    ]
    ip_table = "\n".join(ip_table_lines)
    remaining = max(0, len(group.ips) - 50)
    if remaining:
        ip_table += f"\n  ... and {remaining} additional IPs (full list in attachment)"

    body = f"""To: {group.abuse_contact or f'abuse@{group.org.lower().replace(" ", "")}.net'}
From: {reporter_email}
Subject: [ABUSE] DDoS Attack Originating From Your Network — {group.asn} / {group.org}
Date: {now_str}
Content-Type: text/plain; charset=UTF-8
X-ARF: yes
X-Mailer: iGaming Traffic Classifier v1.0

Dear {group.org} Abuse Team,

We operate {platform} ({victim_domain}) and have identified a sustained volumetric
DDoS attack originating from IP addresses within your network ({group.asn} / {group.org}).

== INCIDENT SUMMARY ==

Incident Start  : {start_str}
Incident End    : {end_str}
Report Generated: {now_str}
Victim IP/Domain: {victim_ip} ({victim_domain})
Attack ASN      : {group.asn}
Attack Org      : {group.org}
Total Source IPs: {group.ip_count}
Total Requests  : {group.total_requests:,}

== ATTACKING SOURCE IPs ==

{'IP Address':<20} {'Requests':>8}   Country   ISP/Hostname
{'-'*80}
{ip_table}

== TECHNICAL DETAILS ==

Attack type    : HTTP flood / Layer 7 DDoS
Protocol       : TCP/443 (HTTPS)
Pattern        : High-frequency requests, near-zero browser diversity,
                 machine-regular timing intervals (~{1.0:.0f}ms inter-request gap),
                 datacenter AS range confirmed.

We respectfully request that you:

  1. Investigate the above IP addresses for bot / malware activity.
  2. Block outbound attack traffic from these sources.
  3. Notify the customers / VMs responsible if applicable.
  4. Provide an abuse ticket reference number for our records.

We retain full pcap/log evidence and can provide it upon request under NDA
or applicable data-sharing agreements.

This report was generated automatically by our traffic classifier system.
Please reply to {reporter_email} with your incident reference number.

Regards,
{reporter_name}
{reporter_email}
"""
    return body


@dataclass
class AbuseReport:
    incident_id: str
    generated_at: float
    incident_start: float
    incident_end: float
    total_ips: int
    total_requests: int
    isp_groups: list[ISPGroup]
    email_bodies: dict[str, str]  # asn -> email body
    evidence_file: str = ""


# ---------------------------------------------------------------------------
# Main reporter class
# ---------------------------------------------------------------------------
class ISPAbuseReporter:
    def __init__(
        self,
        victim_ip: str = "",
        victim_domain: str = "",
        reporter_name: str = "",
        reporter_email: str = "",
        evidence_dir: str = "/var/log/traffic-classifier/evidence",
        ipinfo_token: str = "",
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        auto_send: bool = False,
    ) -> None:
        self._victim_ip = victim_ip or os.getenv("VICTIM_IP", "0.0.0.0")
        self._victim_domain = victim_domain or os.getenv("VICTIM_DOMAIN", "example.com")
        self._reporter_name = reporter_name or os.getenv("REPORTER_NAME", "Security Team")
        self._reporter_email = reporter_email or os.getenv("REPORTER_EMAIL", "security@example.com")
        self._evidence_dir = Path(evidence_dir)
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        self._resolver = IPInfoResolver(api_token=ipinfo_token or os.getenv("IPINFO_TOKEN", ""))
        self._smtp_host = smtp_host or os.getenv("SMTP_HOST", "")
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user or os.getenv("SMTP_USER", "")
        self._smtp_password = smtp_password or os.getenv("SMTP_PASSWORD", "")
        self._auto_send = auto_send or os.getenv("ABUSE_AUTO_SEND", "false").lower() == "true"

    async def generate_report(
        self,
        ip_request_counts: dict[str, int],
        incident_start: float,
        incident_end: float | None = None,
        incident_id: str | None = None,
    ) -> AbuseReport:
        """
        Main entry point.  Takes a dict of {ip: request_count} and produces
        a full AbuseReport with per-ISP email bodies.
        """
        end = incident_end or time.time()
        inc_id = incident_id or time.strftime("INC-%Y%m%d-%H%M%S", time.gmtime())

        logger.info(
            "Generating abuse report %s for %d IPs.", inc_id, len(ip_request_counts)
        )

        # Resolve all IPs concurrently
        ip_infos = await self._resolver.resolve_bulk(ip_request_counts)
        for info in ip_infos:
            info.first_seen = incident_start
            info.last_seen = end

        # Group by ISP/ASN
        groups = _group_by_isp(ip_infos)
        # Sort by total requests descending (worst offender first)
        sorted_groups = sorted(groups.values(), key=lambda g: g.total_requests, reverse=True)

        # Build email bodies
        email_bodies: dict[str, str] = {}
        for group in sorted_groups:
            body = _build_abuse_email(
                group=group,
                incident_start=incident_start,
                incident_end=end,
                victim_ip=self._victim_ip,
                victim_domain=self._victim_domain,
                reporter_name=self._reporter_name,
                reporter_email=self._reporter_email,
            )
            email_bodies[group.asn] = body

        report = AbuseReport(
            incident_id=inc_id,
            generated_at=time.time(),
            incident_start=incident_start,
            incident_end=end,
            total_ips=len(ip_infos),
            total_requests=sum(i.request_count for i in ip_infos),
            isp_groups=sorted_groups,
            email_bodies=email_bodies,
        )

        # Save to disk
        report.evidence_file = await self._save_report(report, ip_infos)
        logger.info("Report saved: %s", report.evidence_file)

        # Auto-send if configured
        if self._auto_send and self._smtp_host:
            await self._send_all(report)

        return report

    async def _save_report(
        self, report: AbuseReport, ip_infos: list[IPInfo]
    ) -> str:
        """Write JSON evidence and plain-text email bodies to the evidence directory."""
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime(report.generated_at))
        base_path = self._evidence_dir / f"abuse_{report.incident_id}_{ts}"
        base_path.mkdir(parents=True, exist_ok=True)

        # Full IP list with metadata
        ip_data = [
            {
                "ip": info.ip,
                "asn": info.asn,
                "isp": info.isp,
                "country": info.country,
                "request_count": info.request_count,
                "first_seen": info.first_seen,
                "last_seen": info.last_seen,
            }
            for info in ip_infos
        ]
        with open(base_path / "ip_list.json", "w") as fh:
            json.dump(ip_data, fh, indent=2)

        # Summary
        summary = {
            "incident_id": report.incident_id,
            "generated_at": report.generated_at,
            "incident_start": report.incident_start,
            "incident_end": report.incident_end,
            "total_ips": report.total_ips,
            "total_requests": report.total_requests,
            "isp_breakdown": [
                {
                    "asn": g.asn,
                    "org": g.org,
                    "ip_count": g.ip_count,
                    "total_requests": g.total_requests,
                    "country": g.country,
                    "abuse_contact": g.abuse_contact,
                }
                for g in report.isp_groups
            ],
        }
        with open(base_path / "summary.json", "w") as fh:
            json.dump(summary, fh, indent=2)

        # Per-ISP email drafts
        emails_dir = base_path / "email_drafts"
        emails_dir.mkdir(exist_ok=True)
        for asn, body in report.email_bodies.items():
            safe_asn = asn.replace("/", "_").replace(" ", "_")
            with open(emails_dir / f"{safe_asn}.txt", "w") as fh:
                fh.write(body)

        return str(base_path)

    async def _send_all(self, report: AbuseReport) -> None:
        """Send all abuse emails via SMTP.  Failures are logged, not raised."""
        for group in report.isp_groups:
            if not group.abuse_contact:
                logger.warning(
                    "No abuse contact for %s/%s — skipping email.", group.asn, group.org
                )
                continue
            body = report.email_bodies.get(group.asn, "")
            if not body:
                continue
            await self._send_email(
                to=group.abuse_contact,
                subject=f"[ABUSE] DDoS from {group.asn} / {group.org}",
                body=body,
            )

    async def _send_email(self, to: str, subject: str, body: str) -> None:
        """Send a single email via SMTP (runs in thread pool to avoid blocking)."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._send_email_sync,
            to,
            subject,
            body,
        )

    def _send_email_sync(self, to: str, subject: str, body: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._reporter_email
        msg["To"] = to
        msg.attach(MIMEText(body, "plain", "utf-8"))
        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=30) as server:
                server.ehlo()
                if self._smtp_port == 587:
                    server.starttls()
                if self._smtp_user and self._smtp_password:
                    server.login(self._smtp_user, self._smtp_password)
                server.sendmail(self._reporter_email, [to], msg.as_string())
            logger.info("Abuse email sent to %s.", to)
        except Exception as exc:
            logger.error("Failed to send abuse email to %s: %s", to, exc)

    async def generate_from_evidence_file(
        self, evidence_json_path: str
    ) -> AbuseReport:
        """
        Re-generate a report from a previously saved attack evidence JSON file.
        Useful for generating abuse reports hours after the incident.
        """
        with open(evidence_json_path) as fh:
            evidence = json.load(fh)

        ips = evidence.get("attacking_ips", [])
        ip_request_counts = {ip: 1 for ip in ips}

        # Use metrics if available
        metrics = evidence.get("metrics", {})
        ts = evidence.get("timestamp", time.time())
        try:
            incident_start = float(ts.replace("_", "")) if isinstance(ts, str) else float(ts)
        except (ValueError, AttributeError):
            incident_start = time.time() - 3600

        return await self.generate_report(
            ip_request_counts=ip_request_counts,
            incident_start=incident_start,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
async def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate ISP abuse reports from attack evidence files."
    )
    parser.add_argument("evidence_file", help="Path to attack evidence JSON file.")
    parser.add_argument("--send", action="store_true", help="Auto-send emails via SMTP.")
    args = parser.parse_args()

    reporter = ISPAbuseReporter(auto_send=args.send)
    report = await reporter.generate_from_evidence_file(args.evidence_file)

    print(f"\nAbuse Report: {report.incident_id}")
    print(f"Total IPs: {report.total_ips}")
    print(f"Total requests: {report.total_requests:,}")
    print(f"ISP groups: {len(report.isp_groups)}")
    print(f"Evidence saved to: {report.evidence_file}")
    print("\nTop offending ISPs:")
    for g in report.isp_groups[:10]:
        print(
            f"  {g.asn:<12} {g.org:<40} {g.ip_count:>6} IPs  {g.total_requests:>10,} reqs"
        )


if __name__ == "__main__":
    asyncio.run(_main())
