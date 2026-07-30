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
tailscale-audit.py
Generate a weekly Tailscale access audit report for compliance.
Outputs HTML report suitable for compliance officers and security teams.

Usage: python3 tailscale-audit.py [--days 7] [--output report.html]

Environment variables:
    TAILSCALE_API_KEY   - API key with read access
    TAILSCALE_TAILNET   - Tailnet name (default: acmetocasino.com)

Chapter 23 — DevSecOps for iGaming
"""

import argparse
import base64
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

API_BASE = "https://api.tailscale.com/api/v2"
TAILNET = os.environ.get("TAILSCALE_TAILNET", "acmetocasino.com")
API_KEY = os.environ.get("TAILSCALE_API_KEY", "")

# Thresholds for anomaly detection
UNUSUAL_HOURS_START = 1   # 1 AM
UNUSUAL_HOURS_END = 5     # 5 AM
NEW_DEVICE_ALERT = True
STALE_DEVICE_DAYS = 30


def api_get(path: str) -> dict:
    """Make an authenticated GET request to the Tailscale API."""
    url = f"{API_BASE}{path}"
    req = Request(url)
    # Basic auth with API key as username, empty password
    creds = base64.b64encode(f"{API_KEY}:".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        print(f"API error: {e.code} {e.reason} for {url}", file=sys.stderr)
        sys.exit(1)


def parse_ts(ts_str: str) -> datetime:
    """Parse an ISO 8601 timestamp from the Tailscale API."""
    # Handle both 'Z' suffix and '+00:00'
    ts_str = ts_str.replace("Z", "+00:00")
    return datetime.fromisoformat(ts_str)


def generate_report(days: int = 7) -> dict:
    """Collect data and generate audit report structure."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Fetch all devices
    devices_data = api_get(f"/tailnet/{TAILNET}/devices")
    devices = devices_data.get("devices", [])

    # Fetch all keys
    keys_data = api_get(f"/tailnet/{TAILNET}/keys")
    keys = keys_data.get("keys", [])

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days": days,
        "cutoff": cutoff.isoformat(),
        "total_devices": len(devices),
        "active_devices": 0,
        "stale_devices": [],
        "new_devices": [],
        "devices_by_user": defaultdict(list),
        "devices_by_os": defaultdict(int),
        "unusual_activity": [],
        "active_keys": 0,
        "expiring_keys_7d": [],
        "findings": [],
    }

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=STALE_DEVICE_DAYS)

    for device in devices:
        user = device.get("user", "unknown")
        hostname = device.get("hostname", "unknown")
        device_os = device.get("os", "unknown")
        last_seen_str = device.get("lastSeen", "")
        created_str = device.get("created", "")

        report["devices_by_user"][user].append(hostname)
        report["devices_by_os"][device_os] += 1

        if last_seen_str:
            last_seen = parse_ts(last_seen_str)

            # Active in reporting period
            if last_seen > cutoff:
                report["active_devices"] += 1

            # Stale device detection
            if last_seen < stale_cutoff:
                report["stale_devices"].append({
                    "user": user,
                    "hostname": hostname,
                    "last_seen": last_seen_str,
                    "days_stale": (now - last_seen).days,
                })

            # Unusual hours activity
            if UNUSUAL_HOURS_START <= last_seen.hour < UNUSUAL_HOURS_END:
                if last_seen > cutoff:
                    report["unusual_activity"].append({
                        "user": user,
                        "hostname": hostname,
                        "time": last_seen_str,
                        "reason": f"Activity at {last_seen.strftime('%H:%M')} UTC",
                    })

        # New device detection
        if created_str:
            created = parse_ts(created_str)
            if created > cutoff:
                report["new_devices"].append({
                    "user": user,
                    "hostname": hostname,
                    "created": created_str,
                    "os": device_os,
                })

    # Key analysis
    for key in keys:
        if not key.get("revoked", False):
            report["active_keys"] += 1
            expires = key.get("expires", "")
            if expires:
                exp_dt = parse_ts(expires)
                if exp_dt < now + timedelta(days=7):
                    report["expiring_keys_7d"].append({
                        "id": key["id"],
                        "description": key.get("description", ""),
                        "expires": expires,
                    })

    # Generate findings
    if report["stale_devices"]:
        report["findings"].append({
            "severity": "MEDIUM",
            "finding": f"{len(report['stale_devices'])} stale devices not seen in {STALE_DEVICE_DAYS}+ days",
            "recommendation": "Review and remove stale devices to reduce attack surface",
        })

    if report["unusual_activity"]:
        report["findings"].append({
            "severity": "HIGH",
            "finding": f"{len(report['unusual_activity'])} connections during unusual hours ({UNUSUAL_HOURS_START}:00-{UNUSUAL_HOURS_END}:00 UTC)",
            "recommendation": "Verify these connections with the users involved",
        })

    if report["new_devices"]:
        report["findings"].append({
            "severity": "INFO",
            "finding": f"{len(report['new_devices'])} new devices registered in the past {days} days",
            "recommendation": "Verify all new devices were authorized through onboarding process",
        })

    for user, devs in report["devices_by_user"].items():
        if len(devs) > 5:
            report["findings"].append({
                "severity": "MEDIUM",
                "finding": f"User {user} has {len(devs)} devices registered",
                "recommendation": "Review whether all devices are still needed",
            })

    if report["expiring_keys_7d"]:
        report["findings"].append({
            "severity": "HIGH",
            "finding": f"{len(report['expiring_keys_7d'])} auth keys expiring within 7 days",
            "recommendation": "Rotate keys before expiry to avoid access disruption",
        })

    return report


def render_html(report: dict) -> str:
    """Render the audit report as HTML."""
    findings_html = ""
    severity_colors = {"HIGH": "#e74c3c", "MEDIUM": "#f39c12", "INFO": "#3498db"}
    for f in report["findings"]:
        color = severity_colors.get(f["severity"], "#95a5a6")
        findings_html += f"""
        <tr>
            <td><span style="color:{color};font-weight:bold">{f['severity']}</span></td>
            <td>{f['finding']}</td>
            <td>{f['recommendation']}</td>
        </tr>"""

    stale_html = ""
    for d in report["stale_devices"][:20]:
        stale_html += f"<tr><td>{d['user']}</td><td>{d['hostname']}</td><td>{d['days_stale']} days</td></tr>"

    new_html = ""
    for d in report["new_devices"]:
        new_html += f"<tr><td>{d['user']}</td><td>{d['hostname']}</td><td>{d['os']}</td><td>{d['created'][:10]}</td></tr>"

    unusual_html = ""
    for a in report["unusual_activity"]:
        unusual_html += f"<tr><td>{a['user']}</td><td>{a['hostname']}</td><td>{a['time'][:19]}</td><td>{a['reason']}</td></tr>"

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Tailscale Audit Report - {report['generated_at'][:10]}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 40px; color: #333; }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
        h2 {{ color: #2c3e50; margin-top: 30px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
        th {{ background: #f8f9fa; font-weight: 600; }}
        .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin: 20px 0; }}
        .stat {{ background: #f8f9fa; padding: 20px; border-radius: 8px; text-align: center; }}
        .stat .number {{ font-size: 2em; font-weight: bold; color: #2c3e50; }}
        .stat .label {{ color: #7f8c8d; margin-top: 5px; }}
    </style>
</head>
<body>
    <h1>Tailscale Network Audit Report</h1>
    <p>Generated: {report['generated_at'][:19]} UTC | Period: {report['period_days']} days</p>

    <div class="summary">
        <div class="stat"><div class="number">{report['total_devices']}</div><div class="label">Total Devices</div></div>
        <div class="stat"><div class="number">{report['active_devices']}</div><div class="label">Active Devices</div></div>
        <div class="stat"><div class="number">{len(report['stale_devices'])}</div><div class="label">Stale Devices</div></div>
        <div class="stat"><div class="number">{report['active_keys']}</div><div class="label">Active Keys</div></div>
    </div>

    <h2>Findings</h2>
    <table><tr><th>Severity</th><th>Finding</th><th>Recommendation</th></tr>{findings_html}</table>

    <h2>New Devices (Past {report['period_days']} Days)</h2>
    <table><tr><th>User</th><th>Hostname</th><th>OS</th><th>Created</th></tr>{new_html}</table>

    <h2>Unusual Activity</h2>
    <table><tr><th>User</th><th>Hostname</th><th>Time (UTC)</th><th>Reason</th></tr>{unusual_html}</table>

    <h2>Stale Devices (Not Seen in {STALE_DEVICE_DAYS}+ Days)</h2>
    <table><tr><th>User</th><th>Hostname</th><th>Days Stale</th></tr>{stale_html}</table>

    <h2>Devices by OS</h2>
    <table><tr><th>Operating System</th><th>Count</th></tr>
    {"".join(f"<tr><td>{os}</td><td>{count}</td></tr>" for os, count in sorted(report['devices_by_os'].items()))}</table>

    <p style="color:#95a5a6;margin-top:40px;font-size:0.9em">
        Report generated by tailscale-audit.py | AcmeToCasino Security Operations
    </p>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Tailscale weekly audit report")
    parser.add_argument("--days", type=int, default=7, help="Reporting period in days")
    parser.add_argument("--output", default="tailscale-audit-report.html", help="Output file")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of HTML")
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: Set TAILSCALE_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)

    report = generate_report(days=args.days)

    if args.json:
        # Convert defaultdict to regular dict for JSON serialization
        report["devices_by_user"] = dict(report["devices_by_user"])
        report["devices_by_os"] = dict(report["devices_by_os"])
        output = json.dumps(report, indent=2)
    else:
        output = render_html(report)

    with open(args.output, "w") as f:
        f.write(output)

    print(f"Report written to {args.output}")
    print(f"Findings: {len(report['findings'])}")
    for finding in report["findings"]:
        print(f"  [{finding['severity']}] {finding['finding']}")


if __name__ == "__main__":
    main()
