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
cf-audit-report.py
Generate a weekly Cloudflare Access audit report.
Pulls access logs, policy evaluations, and device enrollments.

Usage: python3 cf-audit-report.py [--days 7] [--output report.html]

Environment variables:
    CF_API_TOKEN    - Cloudflare API token
    CF_ACCOUNT_ID   - Cloudflare account ID

Chapter 23 — DevSecOps for iGaming
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
API_BASE = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"


def cf_api(path: str) -> dict:
    """Authenticated GET request to Cloudflare API."""
    url = f"{API_BASE}{path}"
    req = Request(url)
    req.add_header("Authorization", f"Bearer {CF_API_TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        print(f"API error: {e.code} for {url}", file=sys.stderr)
        return {"result": [], "success": False}


def generate_report(days: int) -> dict:
    """Build audit report from Cloudflare API data."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days": days,
        "applications": [],
        "devices": {"total": 0, "active": 0, "by_platform": defaultdict(int)},
        "access_events": {"total": 0, "allowed": 0, "blocked": 0, "by_app": defaultdict(int)},
        "findings": [],
    }

    # Fetch Access applications
    apps_data = cf_api("/access/apps")
    apps = apps_data.get("result", [])
    report["applications"] = [
        {"name": a["name"], "domain": a.get("domain", ""), "id": a["id"]}
        for a in apps
    ]

    # Fetch Access logs (audit events)
    logs_data = cf_api(f"/access/logs/access_requests?since={cutoff.isoformat()}&limit=1000")
    for log_entry in logs_data.get("result", []):
        report["access_events"]["total"] += 1
        action = log_entry.get("action", "")
        app_name = log_entry.get("app_name", "unknown")
        report["access_events"]["by_app"][app_name] += 1

        if action == "login":
            report["access_events"]["allowed"] += 1
        elif action in ("block", "deny"):
            report["access_events"]["blocked"] += 1
            # Blocked access is a finding
            report["findings"].append({
                "severity": "HIGH" if "database" in app_name.lower() else "MEDIUM",
                "finding": f"Blocked access to {app_name} by {log_entry.get('user_email', 'unknown')}",
                "time": log_entry.get("created_at", ""),
                "detail": f"Reason: {log_entry.get('action_detail', 'policy denied')}",
            })

    # Fetch enrolled devices
    devices_data = cf_api("/devices")
    for device in devices_data.get("result", []):
        report["devices"]["total"] += 1
        platform = device.get("device_type", "unknown")
        report["devices"]["by_platform"][platform] += 1
        last_seen = device.get("last_seen", "")
        if last_seen:
            try:
                ls_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                if ls_dt > cutoff:
                    report["devices"]["active"] += 1
            except ValueError:
                pass

    # Summary findings
    blocked = report["access_events"]["blocked"]
    if blocked > 0:
        report["findings"].insert(0, {
            "severity": "HIGH",
            "finding": f"{blocked} access attempts were blocked in the past {days} days",
            "time": "",
            "detail": "Review blocked attempts for potential unauthorized access",
        })

    return report


def render_html(report: dict) -> str:
    """Render HTML audit report."""
    findings_rows = ""
    for f in report["findings"][:50]:
        color = {"HIGH": "#e74c3c", "MEDIUM": "#f39c12", "INFO": "#3498db"}.get(f["severity"], "#95a5a6")
        findings_rows += f"""<tr>
            <td><span style="color:{color};font-weight:bold">{f['severity']}</span></td>
            <td>{f['finding']}</td>
            <td>{f.get('time', '')[:19]}</td>
        </tr>"""

    app_rows = ""
    for app_name, count in sorted(report["access_events"]["by_app"].items(), key=lambda x: -x[1]):
        app_rows += f"<tr><td>{app_name}</td><td>{count}</td></tr>"

    platform_rows = ""
    for platform, count in sorted(report["devices"]["by_platform"].items()):
        platform_rows += f"<tr><td>{platform}</td><td>{count}</td></tr>"

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Cloudflare Access Audit - {report['generated_at'][:10]}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 40px; color: #333; }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #f96; padding-bottom: 10px; }}
        h2 {{ color: #2c3e50; margin-top: 30px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
        th {{ background: #f8f9fa; }}
        .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin: 20px 0; }}
        .stat {{ background: #f8f9fa; padding: 20px; border-radius: 8px; text-align: center; }}
        .stat .number {{ font-size: 2em; font-weight: bold; color: #2c3e50; }}
        .stat .label {{ color: #7f8c8d; margin-top: 5px; }}
    </style>
</head>
<body>
    <h1>Cloudflare Access Audit Report</h1>
    <p>Generated: {report['generated_at'][:19]} UTC | Period: {report['period_days']} days</p>

    <div class="summary">
        <div class="stat"><div class="number">{report['access_events']['total']}</div><div class="label">Total Access Events</div></div>
        <div class="stat"><div class="number">{report['access_events']['allowed']}</div><div class="label">Allowed</div></div>
        <div class="stat"><div class="number">{report['access_events']['blocked']}</div><div class="label">Blocked</div></div>
        <div class="stat"><div class="number">{report['devices']['total']}</div><div class="label">Enrolled Devices</div></div>
    </div>

    <h2>Findings</h2>
    <table><tr><th>Severity</th><th>Finding</th><th>Time</th></tr>{findings_rows}</table>

    <h2>Access Events by Application</h2>
    <table><tr><th>Application</th><th>Events</th></tr>{app_rows}</table>

    <h2>Devices by Platform</h2>
    <table><tr><th>Platform</th><th>Count</th></tr>{platform_rows}</table>

    <p style="color:#95a5a6;margin-top:40px;font-size:0.9em">
        Report generated by cf-audit-report.py | AcmeToCasino Security Operations
    </p>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Cloudflare Access audit report")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--output", default="cf-audit-report.html")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not CF_API_TOKEN or not CF_ACCOUNT_ID:
        print("ERROR: Set CF_API_TOKEN and CF_ACCOUNT_ID", file=sys.stderr)
        sys.exit(1)

    report = generate_report(args.days)

    if args.json:
        report["devices"]["by_platform"] = dict(report["devices"]["by_platform"])
        report["access_events"]["by_app"] = dict(report["access_events"]["by_app"])
        output = json.dumps(report, indent=2)
    else:
        output = render_html(report)

    with open(args.output, "w") as f:
        f.write(output)

    print(f"Report written to {args.output}")
    print(f"Findings: {len(report['findings'])}")


if __name__ == "__main__":
    main()
