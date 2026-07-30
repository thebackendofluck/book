#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 42, War Stories.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Log Analysis Patterns for iGaming Incident Investigation

Based on real incident response tooling used to investigate security events
at online gambling platforms. This script demonstrates the analytical patterns
used during actual incidents — the same patterns described in Chapter 34's
war stories.

Key investigation patterns:
  1. Attack source clustering — group malicious requests by source IP
  2. Temporal analysis — identify attack timing and duration
  3. Path traversal detection — find directory escape attempts
  4. API abuse detection — identify abnormal request rates
  5. ModSecurity event correlation — link WAF blocks to attack campaigns

SANITIZATION: All IPs use RFC 5737 ranges. Domains use example.com.
Derived from production log analysis tooling.
"""

import re
import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Tuple, Optional


class GamblingLogAnalyser:
    """
    Log analyser tailored for iGaming platform incident investigation.

    Unlike generic log analysis tools, this understands gambling-specific
    patterns: player API endpoints, payment gateway calls, game provider
    integrations, and regulatory audit trails.
    """

    # Gambling platform API patterns that warrant extra scrutiny
    SENSITIVE_ENDPOINTS = [
        r'/platform/usergateway/getbalance',
        r'/platform/usergateway/deposit',
        r'/platform/usergateway/withdraw',
        r'/platform/usergateway/userlogin',
        r'/platform/usergateway/id-number',
        r'/payment/process',
        r'/payment/callback',
        r'/admin/',
        r'/backoffice/',
        r'/api/v\d+/player/',
    ]

    # Known attack signatures in gambling platform logs
    ATTACK_PATTERNS = {
        'path_traversal': [
            r'\.%2e',                    # URL-encoded ..
            r'\.\.',                     # Direct path traversal
            r'/etc/passwd',              # Unix file read
            r'/bin/sh',                  # Shell execution
            r'cgi-bin/\.%2e',            # CGI escape
        ],
        'log4shell': [
            r'\$\{jndi:',               # JNDI injection
            r'\$\{\$\{lower:j',         # Obfuscated JNDI
            r'\$\{\$\{::-j',            # Character bypass
            r'jndi:(ldap|rmi|dns)://',   # Protocol variants
        ],
        'sql_injection': [
            r"'(\s)*(or|and|union|select|insert|drop|delete)",
            r';\s*(drop|delete|update|insert)',
            r'UNION\s+SELECT',
        ],
        'credential_stuffing': [
            r'userlogin.*HTTP/1\.[01]"\s+40[13]',  # Failed logins
        ],
        'api_abuse': [
            r'getbalance.*HTTP/1\.[01]"\s+200',     # Balance check floods
        ],
    }

    def __init__(self, log_file: str):
        self.log_file = log_file
        self.lines: List[str] = []
        self.host_hits: Counter = Counter()
        self.endpoint_hits: Counter = Counter()
        self.attack_events: Dict[str, List[dict]] = defaultdict(list)
        self.timeline: List[dict] = []

        self._parse_log()

    def _parse_log(self):
        """Parse the log file into structured data."""
        if not os.path.isfile(self.log_file):
            print(f'File "{self.log_file}" does not exist')
            sys.exit(1)

        with open(self.log_file, 'r', errors='replace') as f:
            self.lines = f.readlines()

        # Extract IPs and endpoints from Apache-style access logs
        access_pattern = re.compile(
            r'(?:client\s+)?(\d+\.\d+\.\d+\.\d+)'  # IP address
        )
        uri_pattern = re.compile(
            r'"(?:GET|POST|PUT|DELETE|OPTIONS|HEAD)\s+(\S+)\s+HTTP'
        )

        for line in self.lines:
            ip_match = access_pattern.search(line)
            if ip_match:
                self.host_hits[ip_match.group(1)] += 1

            uri_match = uri_pattern.search(line)
            if uri_match:
                self.endpoint_hits[uri_match.group(1)] += 1

    def detect_attacks(self) -> Dict[str, int]:
        """
        Scan all log lines against known attack signatures.

        Returns a summary of detected attack types and counts.
        In a gambling platform investigation, this is your first pass
        to understand what kind of incident you're dealing with.
        """
        summary = {}

        for attack_type, patterns in self.ATTACK_PATTERNS.items():
            count = 0
            for line in self.lines:
                for pattern in patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        count += 1
                        # Extract timestamp if available
                        ts_match = re.search(
                            r'\[(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2})', line
                        )
                        self.attack_events[attack_type].append({
                            'line': line.strip(),
                            'timestamp': ts_match.group(1) if ts_match else 'unknown',
                        })
                        break  # Count each line only once per attack type
            summary[attack_type] = count

        return summary

    def get_top_sources(self, count: int = 10) -> List[Tuple[str, int]]:
        """
        Identify the most active source IPs.

        In incident response, the top talkers list quickly reveals:
        - Scanning bots (very high request counts)
        - Credential stuffing sources (moderate counts, login endpoints)
        - Legitimate high-traffic sources (game providers, CDN nodes)
        """
        return self.host_hits.most_common(count)

    def get_sensitive_endpoint_access(self) -> Dict[str, List[str]]:
        """
        Find all access to gambling-sensitive endpoints.

        These are the endpoints that matter most during an incident:
        balance checks, withdrawals, login attempts, admin access.
        An attacker who reaches these has moved past reconnaissance.
        """
        results = defaultdict(list)

        for line in self.lines:
            for pattern in self.SENSITIVE_ENDPOINTS:
                if re.search(pattern, line, re.IGNORECASE):
                    ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
                    ip = ip_match.group(1) if ip_match else 'unknown'
                    results[pattern].append(ip)

        return results

    def temporal_analysis(self, ip: Optional[str] = None) -> Dict[str, int]:
        """
        Build a per-hour request histogram for a specific IP or all traffic.

        This reveals attack timing patterns. In gambling platforms:
        - Attacks during 2-6 AM local time target low-staffing windows
        - Spikes during major sporting events may be DDoS cover
        - Sustained low-rate scanning suggests APT reconnaissance
        """
        hourly = Counter()
        ts_pattern = re.compile(
            r'\[(\d{2}/\w{3}/\d{4}):(\d{2}):\d{2}:\d{2}'
        )

        for line in self.lines:
            if ip and ip not in line:
                continue
            ts_match = ts_pattern.search(line)
            if ts_match:
                hour_key = f"{ts_match.group(1)} {ts_match.group(2)}:00"
                hourly[hour_key] += 1

        return dict(sorted(hourly.items()))

    def modsec_analysis(self) -> Dict[str, int]:
        """
        Analyse ModSecurity events from error logs.

        ModSecurity is the first line of WAF defense in many gambling
        platforms. Understanding what it blocked (and what it missed)
        is critical for post-incident assessment.
        """
        modsec_events = Counter()
        modsec_pattern = re.compile(r'ModSecurity:\s+(.*?)\s+\[')

        for line in self.lines:
            match = modsec_pattern.search(line)
            if match:
                event_type = match.group(1)
                modsec_events[event_type] += 1

        return modsec_events

    def generate_incident_summary(self) -> str:
        """
        Generate a formatted incident investigation summary.

        This is the output format expected by gambling platform
        security teams and regulators during an active incident.
        """
        attacks = self.detect_attacks()
        top_sources = self.get_top_sources(5)
        sensitive = self.get_sensitive_endpoint_access()

        lines = [
            "=" * 60,
            "INCIDENT INVESTIGATION SUMMARY",
            "=" * 60,
            f"Log file: {self.log_file}",
            f"Total lines analysed: {len(self.lines)}",
            f"Unique source IPs: {len(self.host_hits)}",
            "",
            "--- Attack Detection ---",
        ]

        for attack_type, count in attacks.items():
            status = "DETECTED" if count > 0 else "Not found"
            lines.append(f"  {attack_type}: {count} events ({status})")

        lines.append("")
        lines.append("--- Top 5 Source IPs ---")
        for ip, count in top_sources:
            lines.append(f"  {ip}: {count} requests")

        lines.append("")
        lines.append("--- Sensitive Endpoint Access ---")
        for endpoint, ips in sensitive.items():
            unique_ips = set(ips)
            lines.append(f"  {endpoint}: {len(ips)} hits from {len(unique_ips)} IPs")

        lines.append("")
        lines.append("--- Recommended Actions ---")
        if attacks.get('path_traversal', 0) > 0:
            lines.append("  [!] Block path traversal source IPs at WAF level")
            lines.append("  [!] Verify Apache version patched for CVE-2021-41773")
        if attacks.get('log4shell', 0) > 0:
            lines.append("  [!] CRITICAL: Log4Shell exploitation detected")
            lines.append("  [!] Run log4shell_response.sh immediately")
            lines.append("  [!] Isolate affected services for forensic analysis")
        if attacks.get('credential_stuffing', 0) > 0:
            lines.append("  [!] Enable rate limiting on login endpoints")
            lines.append("  [!] Force password resets for accounts with failed attempts")
        if attacks.get('sql_injection', 0) > 0:
            lines.append("  [!] Review parameterized query usage in affected endpoints")

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="iGaming Platform Log Analyser — Incident Investigation"
    )
    parser.add_argument(
        'logfile', metavar='<log file>',
        help='Path to log file to analyse'
    )
    parser.add_argument(
        '-t', '--top', metavar='N', type=int, default=10,
        help='Show top N source IPs (default: 10)'
    )
    parser.add_argument(
        '--attacks', action='store_true',
        help='Run attack pattern detection'
    )
    parser.add_argument(
        '--timeline', metavar='IP',
        help='Show hourly request histogram for a specific IP'
    )
    parser.add_argument(
        '--modsec', action='store_true',
        help='Analyse ModSecurity events'
    )
    parser.add_argument(
        '--summary', action='store_true',
        help='Generate full incident investigation summary'
    )

    args = parser.parse_args()
    analyser = GamblingLogAnalyser(args.logfile)

    if args.summary:
        print(analyser.generate_incident_summary())
        return

    if args.attacks:
        print("Attack Detection Results")
        print("-" * 40)
        for attack_type, count in analyser.detect_attacks().items():
            print(f"  {attack_type}: {count} events")

    if args.top:
        print(f"\nTop {args.top} Source IPs")
        print("-" * 40)
        for ip, count in analyser.get_top_sources(args.top):
            print(f"  {ip}: {count} requests")

    if args.timeline:
        print(f"\nHourly Histogram for {args.timeline}")
        print("-" * 40)
        for hour, count in analyser.temporal_analysis(args.timeline).items():
            bar = "#" * min(count, 50)
            print(f"  {hour} | {bar} ({count})")

    if args.modsec:
        print("\nModSecurity Event Analysis")
        print("-" * 40)
        for event, count in analyser.modsec_analysis().items():
            print(f"  {event}: {count}")


if __name__ == "__main__":
    main()
