#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 30, FinOps Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Compliance Reporter Module
==========================

Generates compliance reports in multiple formats for audit and review.
"""

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional


class ReportFormat(Enum):
    """Supported report formats"""
    HTML = "html"
    JSON = "json"
    MARKDOWN = "markdown"
    SARIF = "sarif"


class ComplianceReporter:
    """Generate compliance reports from scan results"""

    def __init__(self, output_dir: str = "./reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, result: Any, format: ReportFormat = ReportFormat.HTML) -> str:
        """Generate report in specified format"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if format == ReportFormat.HTML:
            return self._generate_html(result, timestamp)
        elif format == ReportFormat.JSON:
            return self._generate_json(result, timestamp)
        elif format == ReportFormat.MARKDOWN:
            return self._generate_markdown(result, timestamp)
        elif format == ReportFormat.SARIF:
            return self._generate_sarif(result, timestamp)
        else:
            return self._generate_html(result, timestamp)

    def _generate_html(self, result: Any, timestamp: str) -> str:
        """Generate HTML report"""
        output_path = self.output_dir / f"license_report_{timestamp}.html"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>License Compliance Report - {result.scan_id}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ color: #1a1a2e; border-bottom: 3px solid #4361ee; padding-bottom: 10px; }}
        h2 {{ color: #4361ee; margin-top: 30px; }}
        .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin: 20px 0; }}
        .metric {{ background: #f8f9fa; padding: 20px; border-radius: 8px; text-align: center; }}
        .metric-value {{ font-size: 2em; font-weight: bold; color: #1a1a2e; }}
        .metric-label {{ color: #666; margin-top: 5px; }}
        .score-high {{ color: #2ecc71; }}
        .score-medium {{ color: #f39c12; }}
        .score-low {{ color: #e74c3c; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #4361ee; color: white; }}
        tr:hover {{ background: #f5f5f5; }}
        .violation {{ background: #ffe6e6; }}
        .warning {{ background: #fff3cd; }}
        .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 0.85em; }}
        .badge-high {{ background: #e74c3c; color: white; }}
        .badge-medium {{ background: #f39c12; color: white; }}
        .badge-low {{ background: #2ecc71; color: white; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>License Compliance Report</h1>
        <p><strong>Scan ID:</strong> {result.scan_id}<br>
        <strong>Target:</strong> {result.target}<br>
        <strong>Timestamp:</strong> {result.timestamp}<br>
        <strong>Duration:</strong> {result.scan_duration_seconds:.2f}s</p>

        <div class="summary">
            <div class="metric">
                <div class="metric-value {'score-high' if result.compliance_score >= 80 else 'score-medium' if result.compliance_score >= 60 else 'score-low'}">{result.compliance_score:.1f}%</div>
                <div class="metric-label">Compliance Score</div>
            </div>
            <div class="metric">
                <div class="metric-value">{len(result.licenses)}</div>
                <div class="metric-label">Total Packages</div>
            </div>
            <div class="metric">
                <div class="metric-value score-low">{len(result.violations)}</div>
                <div class="metric-label">Violations</div>
            </div>
            <div class="metric">
                <div class="metric-value score-medium">{len(result.warnings)}</div>
                <div class="metric-label">Warnings</div>
            </div>
        </div>

        <h2>Violations</h2>
        {'<p>No violations found.</p>' if not result.violations else self._violations_table(result.violations)}

        <h2>Warnings</h2>
        {'<p>No warnings.</p>' if not result.warnings else self._warnings_table(result.warnings)}

        <h2>All Licenses</h2>
        {self._licenses_table(result.licenses)}

        <footer style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; color: #666; text-align: center;">
            Generated by iGaming License Scanner | Chapter 20: FinOps Deep Dive
        </footer>
    </div>
</body>
</html>"""

        with open(output_path, 'w') as f:
            f.write(html)

        return str(output_path)

    def _violations_table(self, violations: list) -> str:
        rows = ""
        for v in violations:
            rows += f"""<tr class="violation">
                <td><span class="badge badge-high">{v['severity']}</span></td>
                <td>{v['package']}</td>
                <td>{v['license']}</td>
                <td>{v['reason']}</td>
                <td>{v['recommendation']}</td>
            </tr>"""
        return f"""<table>
            <tr><th>Severity</th><th>Package</th><th>License</th><th>Reason</th><th>Recommendation</th></tr>
            {rows}
        </table>"""

    def _warnings_table(self, warnings: list) -> str:
        rows = ""
        for w in warnings:
            rows += f"""<tr class="warning">
                <td><span class="badge badge-medium">{w['severity']}</span></td>
                <td>{w['package']}</td>
                <td>{w['license']}</td>
                <td>{w['reason']}</td>
            </tr>"""
        return f"""<table>
            <tr><th>Severity</th><th>Package</th><th>License</th><th>Reason</th></tr>
            {rows}
        </table>"""

    def _licenses_table(self, licenses: list) -> str:
        rows = ""
        for lic in licenses:
            pkg_name = lic.package_name if hasattr(lic, 'package_name') else str(lic)
            pkg_version = lic.package_version if hasattr(lic, 'package_version') else 'unknown'
            spdx_id = lic.spdx_id if hasattr(lic, 'spdx_id') else 'unknown'
            risk = lic.risk_level.value if hasattr(lic, 'risk_level') else 'unknown'
            badge_class = 'badge-low' if risk == 'low' else 'badge-medium' if risk == 'medium' else 'badge-high'
            rows += f"""<tr>
                <td>{pkg_name}</td>
                <td>{pkg_version}</td>
                <td>{spdx_id}</td>
                <td><span class="badge {badge_class}">{risk}</span></td>
            </tr>"""
        return f"""<table>
            <tr><th>Package</th><th>Version</th><th>License</th><th>Risk Level</th></tr>
            {rows}
        </table>"""

    def _generate_json(self, result: Any, timestamp: str) -> str:
        """Generate JSON report"""
        output_path = self.output_dir / f"license_report_{timestamp}.json"

        report = {
            "scan_id": result.scan_id,
            "timestamp": str(result.timestamp),
            "target": result.target,
            "compliance_score": result.compliance_score,
            "total_packages": len(result.licenses),
            "violations": result.violations,
            "warnings": result.warnings,
            "licenses": [
                {
                    "package": lic.package_name if hasattr(lic, 'package_name') else str(lic),
                    "version": lic.package_version if hasattr(lic, 'package_version') else 'unknown',
                    "license": lic.spdx_id if hasattr(lic, 'spdx_id') else 'unknown',
                    "risk_level": lic.risk_level.value if hasattr(lic, 'risk_level') else 'unknown'
                }
                for lic in result.licenses
            ]
        }

        with open(output_path, 'w') as f:
            json.dump(report, f, indent=2)

        return str(output_path)

    def _generate_markdown(self, result: Any, timestamp: str) -> str:
        """Generate Markdown report"""
        output_path = self.output_dir / f"license_report_{timestamp}.md"

        md = f"""# License Compliance Report

**Scan ID:** {result.scan_id}
**Target:** {result.target}
**Timestamp:** {result.timestamp}
**Compliance Score:** {result.compliance_score:.1f}%

## Summary

| Metric | Value |
|--------|-------|
| Total Packages | {len(result.licenses)} |
| Violations | {len(result.violations)} |
| Warnings | {len(result.warnings)} |

## Violations

"""
        if result.violations:
            md += "| Severity | Package | License | Reason |\n|----------|---------|---------|--------|\n"
            for v in result.violations:
                md += f"| {v['severity']} | {v['package']} | {v['license']} | {v['reason']} |\n"
        else:
            md += "No violations found.\n"

        md += "\n## Warnings\n\n"
        if result.warnings:
            md += "| Severity | Package | License | Reason |\n|----------|---------|---------|--------|\n"
            for w in result.warnings:
                md += f"| {w['severity']} | {w['package']} | {w['license']} | {w['reason']} |\n"
        else:
            md += "No warnings.\n"

        with open(output_path, 'w') as f:
            f.write(md)

        return str(output_path)

    def _generate_sarif(self, result: Any, timestamp: str) -> str:
        """Generate SARIF format for IDE/CI integration"""
        output_path = self.output_dir / f"license_report_{timestamp}.sarif"

        sarif = {
            "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
            "version": "2.1.0",
            "runs": [{
                "tool": {
                    "driver": {
                        "name": "iGaming License Scanner",
                        "version": "1.0.0",
                        "informationUri": "https://github.com/igaming/license-scanner"
                    }
                },
                "results": [
                    {
                        "ruleId": "license-violation",
                        "level": "error",
                        "message": {"text": f"{v['reason']} - {v['package']}"},
                        "locations": [{"physicalLocation": {"artifactLocation": {"uri": v['package']}}}]
                    }
                    for v in result.violations
                ]
            }]
        }

        with open(output_path, 'w') as f:
            json.dump(sarif, f, indent=2)

        return str(output_path)
