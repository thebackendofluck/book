# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Confluence API client for iGaming documentation management.

Automates creation and maintenance of runbooks, deployment notes, PIR pages,
compliance documentation, and architecture diagrams within Confluence spaces
used by iGaming platform engineering teams.

Usage:
    client = ConfluenceClient.from_env()
    client.publish_deployment_note(version="2.14.0", changes=[...])
    client.create_pir_page(incident_key="CASINO-501", ...)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from atlassian import Confluence  # ty: ignore[unresolved-import]

logger = logging.getLogger(__name__)

# Template directory (relative to this module)
TEMPLATE_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DeploymentInfo:
    """Metadata for a single deployment."""
    version: str
    environment: str
    timestamp: str
    commit_sha: str
    deployer: str
    changes: list[str] = field(default_factory=list)
    rollback_plan: str = ""
    affected_services: list[str] = field(default_factory=list)
    jira_tickets: list[str] = field(default_factory=list)


@dataclass
class IncidentData:
    """Structured incident data for PIR page generation."""
    incident_key: str
    title: str
    severity: str
    started_at: str
    detected_at: str
    resolved_at: str
    duration_minutes: int
    root_cause: str
    impact: str
    timeline: list[dict[str, str]]
    action_items: list[dict[str, str]]
    jurisdiction: str = ""
    affected_games: list[str] = field(default_factory=list)
    player_impact: int = 0
    regulatory_notification_required: bool = False


@dataclass
class ComplianceReport:
    """Quarterly compliance report data."""
    quarter: str               # e.g. "2026-Q1"
    jurisdiction: str
    total_incidents: int
    sla_compliance_pct: float
    player_complaints: int
    complaints_resolved_in_sla: int
    rtp_audits_passed: int
    rtp_audits_total: int
    regulatory_submissions: list[dict[str, str]]
    notable_events: list[str]


# ---------------------------------------------------------------------------
# Confluence client
# ---------------------------------------------------------------------------

class ConfluenceClient:
    """Confluence API client for iGaming platform documentation.

    Manages runbooks, deployment notes, PIR pages, compliance docs,
    and operational playbooks in a structured Confluence space.
    """

    # Default page hierarchy within the Confluence space
    PARENT_PAGES = {
        "runbooks": "Operational Runbooks",
        "deployments": "Deployment Notes",
        "pir": "Post-Incident Reviews",
        "compliance": "Compliance Documentation",
        "architecture": "Architecture",
        "playbooks": "Operational Playbooks",
        "audit_trail": "Regulatory Audit Trail",
    }

    def __init__(
        self,
        url: str,
        username: str,
        api_token: str,
        space_key: str = "OPS",
    ) -> None:
        self._url = url
        self._space_key = space_key

        self._confluence = Confluence(
            url=url,
            username=username,
            password=api_token,
            cloud=True,
        )
        logger.info("Connected to Confluence at %s (space=%s)", url, space_key)

    @classmethod
    def from_env(cls, space_key: str = "OPS") -> ConfluenceClient:
        """Create a client from environment variables.

        Expected env vars:
            CONFLUENCE_URL       — e.g. https://company.atlassian.net/wiki
            CONFLUENCE_USERNAME  — email address
            CONFLUENCE_API_TOKEN
            CONFLUENCE_SPACE_KEY (optional, defaults to OPS)
        """
        return cls(
            url=os.environ["CONFLUENCE_URL"],
            username=os.environ["CONFLUENCE_USERNAME"],
            api_token=os.environ["CONFLUENCE_API_TOKEN"],
            space_key=os.environ.get("CONFLUENCE_SPACE_KEY", space_key),
        )

    # -- Runbook generation -------------------------------------------------

    def generate_runbook_from_incident(
        self,
        incident: IncidentData,
        *,
        additional_steps: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Auto-generate a runbook page from incident data.

        Creates a structured runbook under the Runbooks parent page with
        detection steps, diagnosis procedures, mitigation actions, and
        verification checks derived from the incident's timeline.

        Returns the created page metadata.
        """
        title = f"Runbook: {incident.title}"

        # Build runbook content from incident timeline
        detection_steps = []
        mitigation_steps = []
        for entry in incident.timeline:
            if "detect" in entry.get("action", "").lower():
                detection_steps.append(entry["action"])
            elif any(kw in entry.get("action", "").lower() for kw in ("fix", "mitigat", "restart", "rollback")):
                mitigation_steps.append(entry["action"])

        body = self._render_runbook_html(
            incident=incident,
            detection_steps=detection_steps or ["Monitor alerting dashboards for anomalies"],
            mitigation_steps=mitigation_steps or ["Follow incident response procedure"],
            additional_steps=additional_steps or [],
        )

        parent_id = self._get_or_create_parent("runbooks")
        page = self._create_or_update_page(title, body, parent_id=parent_id)
        logger.info("Generated runbook page: %s (id=%s)", title, page["id"])
        return page

    # -- Deployment notes ---------------------------------------------------

    def publish_deployment_note(self, deployment: DeploymentInfo) -> dict[str, Any]:
        """Publish a deployment note after a release.

        Creates a timestamped page under Deployment Notes with version info,
        change list, affected services, linked Jira tickets, and rollback plan.
        """
        title = (
            f"Deploy {deployment.version} to {deployment.environment} "
            f"— {deployment.timestamp[:10]}"
        )

        body = self._render_deployment_html(deployment)
        parent_id = self._get_or_create_parent("deployments")
        page = self._create_or_update_page(title, body, parent_id=parent_id)
        logger.info("Published deployment note: %s", title)
        return page

    # -- Architecture diagrams ----------------------------------------------

    def update_architecture_page(
        self,
        title: str,
        diagram_storage_xml: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Create or update an architecture diagram page.

        The diagram_storage_xml should be Confluence storage format (XHTML)
        containing the diagram macro or embedded image.
        """
        body = "<h2>Overview</h2>"
        if description:
            body += f"<p>{self._escape_html(description)}</p>"
        body += f"<h2>Architecture Diagram</h2>{diagram_storage_xml}"
        body += (
            f'<p><em>Last updated: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}'
            f"</em></p>"
        )

        parent_id = self._get_or_create_parent("architecture")
        page = self._create_or_update_page(title, body, parent_id=parent_id)
        logger.info("Updated architecture page: %s", title)
        return page

    # -- Compliance documentation -------------------------------------------

    def generate_compliance_page(self, report: ComplianceReport) -> dict[str, Any]:
        """Generate a quarterly compliance documentation page.

        Includes incident statistics, SLA compliance rates, player complaint
        metrics, RTP audit results, and regulatory submissions — formatted
        for review by compliance officers and regulators.
        """
        title = f"Compliance Report — {report.quarter} ({report.jurisdiction})"

        template_path = TEMPLATE_DIR / "compliance_report_template.html"
        if template_path.exists():
            body = template_path.read_text()
            body = self._fill_compliance_template(body, report)
        else:
            body = self._render_compliance_html(report)

        parent_id = self._get_or_create_parent("compliance")
        page = self._create_or_update_page(title, body, parent_id=parent_id)
        logger.info("Generated compliance page: %s", title)
        return page

    # -- Operational playbook sync ------------------------------------------

    def sync_playbooks_from_git(
        self,
        playbook_dir: str | Path,
        *,
        file_glob: str = "*.md",
    ) -> list[dict[str, Any]]:
        """Sync operational playbooks from a Git repository to Confluence.

        Reads Markdown files from the specified directory, converts them to
        Confluence storage format, and creates/updates pages under the
        Operational Playbooks parent page.

        Returns list of created/updated page metadata.
        """
        playbook_path = Path(playbook_dir)
        pages: list[dict[str, Any]] = []
        parent_id = self._get_or_create_parent("playbooks")

        for md_file in sorted(playbook_path.glob(file_glob)):
            content = md_file.read_text(encoding="utf-8")
            title = self._extract_title_from_markdown(content, md_file.stem)
            html_body = self._markdown_to_confluence_storage(content)

            # Add sync metadata
            html_body += (
                "<hr/>"
                f"<p><em>Synced from Git: {md_file.name} | "
                f'{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</em></p>'
            )

            page = self._create_or_update_page(title, html_body, parent_id=parent_id)
            pages.append(page)
            logger.info("Synced playbook: %s -> %s", md_file.name, title)

        logger.info("Synced %d playbooks from %s", len(pages), playbook_dir)
        return pages

    # -- Post-Incident Review (PIR) -----------------------------------------

    def create_pir_page(self, incident: IncidentData) -> dict[str, Any]:
        """Create a Post-Incident Review page from incident data.

        Uses the PIR template with structured sections: summary, timeline,
        root cause analysis, impact assessment, action items, and lessons
        learned. Includes iGaming-specific fields for jurisdiction and
        regulatory notification status.
        """
        title = f"PIR: {incident.incident_key} — {incident.title}"

        template_path = TEMPLATE_DIR / "pir_template.html"
        if template_path.exists():
            body = template_path.read_text()
            body = self._fill_pir_template(body, incident)
        else:
            body = self._render_pir_html(incident)

        parent_id = self._get_or_create_parent("pir")
        page = self._create_or_update_page(title, body, parent_id=parent_id)
        logger.info("Created PIR page: %s (id=%s)", title, page["id"])
        return page

    def create_pir_stub(
        self,
        incident_key: str,
        title: str,
        severity: str,
    ) -> dict[str, Any]:
        """Create a minimal PIR stub page immediately after an incident.

        This is called by the webhook handler when a PagerDuty incident is
        created, providing a placeholder that the incident commander can
        fill in during and after the incident.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        body = f"""
        <h2>Post-Incident Review (Stub)</h2>
        <ac:structured-macro ac:name="warning">
            <ac:rich-text-body>
                <p>This PIR page was auto-created and needs to be completed
                after the incident is resolved.</p>
            </ac:rich-text-body>
        </ac:structured-macro>
        <table>
            <tr><th>Incident</th><td>{self._escape_html(incident_key)}</td></tr>
            <tr><th>Title</th><td>{self._escape_html(title)}</td></tr>
            <tr><th>Severity</th><td>{self._escape_html(severity)}</td></tr>
            <tr><th>Created</th><td>{now}</td></tr>
            <tr><th>Status</th><td>IN PROGRESS - Awaiting resolution</td></tr>
        </table>
        <h2>Timeline</h2>
        <p><em>To be filled during incident...</em></p>
        <h2>Root Cause</h2>
        <p><em>To be determined...</em></p>
        <h2>Action Items</h2>
        <p><em>To be determined...</em></p>
        """

        parent_id = self._get_or_create_parent("pir")
        page_title = f"PIR: {incident_key} — {title}"
        page = self._create_or_update_page(page_title, body, parent_id=parent_id)
        logger.info("Created PIR stub: %s", page_title)
        return page

    # -- Regulatory audit trail ---------------------------------------------

    def add_audit_entry(
        self,
        action: str,
        details: str,
        *,
        actor: str,
        jurisdiction: str,
        ticket_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Append an entry to the regulatory audit trail page.

        Maintains a running log of compliance-relevant actions (config changes,
        RTP adjustments, self-exclusion overrides, etc.) in an append-only
        Confluence page per jurisdiction per year.
        """
        now = datetime.now(timezone.utc)
        year = now.strftime("%Y")
        page_title = f"Audit Trail — {jurisdiction} — {year}"

        parent_id = self._get_or_create_parent("audit_trail")

        # Try to get existing page
        existing = self._confluence.get_page_by_title(
            self._space_key, page_title
        )

        ticket_link = f" (Jira: {ticket_key})" if ticket_key else ""
        new_entry = (
            f"<tr>"
            f"<td>{now.strftime('%Y-%m-%d %H:%M:%S UTC')}</td>"
            f"<td>{self._escape_html(actor)}</td>"
            f"<td>{self._escape_html(action)}{ticket_link}</td>"
            f"<td>{self._escape_html(details)}</td>"
            f"</tr>"
        )

        if existing:
            current_body = self._confluence.get_page_by_id(
                existing["id"], expand="body.storage"
            )["body"]["storage"]["value"]
            # Insert new entry before closing </tbody>
            if "</tbody>" in current_body:
                body = current_body.replace("</tbody>", f"{new_entry}</tbody>")
            else:
                body = current_body + new_entry
        else:
            body = (
                f"<h2>Regulatory Audit Trail — {jurisdiction} — {year}</h2>"
                "<table>"
                "<thead><tr>"
                "<th>Timestamp</th><th>Actor</th><th>Action</th><th>Details</th>"
                "</tr></thead>"
                f"<tbody>{new_entry}</tbody>"
                "</table>"
            )

        page = self._create_or_update_page(page_title, body, parent_id=parent_id)
        logger.info("Added audit entry: %s by %s (%s)", action, actor, jurisdiction)
        return page

    # -- Internal helpers ---------------------------------------------------

    def _get_or_create_parent(self, section: str) -> Optional[int]:
        """Get or create a parent page for a documentation section."""
        title = self.PARENT_PAGES.get(section, section)
        existing = self._confluence.get_page_by_title(self._space_key, title)
        if existing:
            return int(existing["id"])

        page = self._confluence.create_page(
            space=self._space_key,
            title=title,
            body=f"<p>Auto-generated parent page for {title.lower()}.</p>",
        )
        logger.info("Created parent page: %s (id=%s)", title, page["id"])
        return int(page["id"])

    def _create_or_update_page(
        self,
        title: str,
        body: str,
        *,
        parent_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Create a new page or update an existing one by title."""
        existing = self._confluence.get_page_by_title(self._space_key, title)

        if existing:
            page = self._confluence.update_page(
                page_id=existing["id"],
                title=title,
                body=body,
            )
            logger.debug("Updated page: %s", title)
        else:
            page = self._confluence.create_page(
                space=self._space_key,
                title=title,
                body=body,
                parent_id=parent_id,
            )
            logger.debug("Created page: %s", title)

        return page

    # -- HTML rendering -----------------------------------------------------

    def _render_runbook_html(
        self,
        incident: IncidentData,
        detection_steps: list[str],
        mitigation_steps: list[str],
        additional_steps: list[str],
    ) -> str:
        """Render runbook content as Confluence storage format HTML."""
        detection_list = "".join(f"<li>{self._escape_html(s)}</li>" for s in detection_steps)
        mitigation_list = "".join(f"<li>{self._escape_html(s)}</li>" for s in mitigation_steps)
        additional_list = "".join(f"<li>{self._escape_html(s)}</li>" for s in additional_steps)

        games = ", ".join(incident.affected_games) if incident.affected_games else "N/A"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        return f"""
        <ac:structured-macro ac:name="info">
            <ac:rich-text-body>
                <p>Auto-generated from incident {self._escape_html(incident.incident_key)}.
                Review and customize before using in production.</p>
            </ac:rich-text-body>
        </ac:structured-macro>

        <h2>Overview</h2>
        <table>
            <tr><th>Derived from</th><td>{self._escape_html(incident.incident_key)}: {self._escape_html(incident.title)}</td></tr>
            <tr><th>Severity</th><td>{self._escape_html(incident.severity)}</td></tr>
            <tr><th>Affected Games</th><td>{self._escape_html(games)}</td></tr>
            <tr><th>Jurisdiction</th><td>{self._escape_html(incident.jurisdiction or 'All')}</td></tr>
            <tr><th>Last Updated</th><td>{now}</td></tr>
        </table>

        <h2>Detection</h2>
        <ol>{detection_list}</ol>

        <h2>Diagnosis</h2>
        <ol>
            <li>Check monitoring dashboards for the affected service</li>
            <li>Review recent deployments and configuration changes</li>
            <li>Examine application logs for error patterns</li>
            <li>Verify database and cache health</li>
        </ol>

        <h2>Mitigation</h2>
        <ol>{mitigation_list}</ol>

        <h2>Verification</h2>
        <ol>
            <li>Confirm alerting has returned to normal</li>
            <li>Verify player-facing functionality is operational</li>
            <li>Check RTP calculations are within expected range</li>
            <li>Confirm no regulatory SLA breaches</li>
        </ol>

        {"<h2>Additional Steps</h2><ol>" + additional_list + "</ol>" if additional_list else ""}

        <h2>Escalation</h2>
        <p>If mitigation steps do not resolve the issue within 30 minutes:</p>
        <ol>
            <li>Escalate to on-call engineering lead</li>
            <li>Notify compliance team if player funds or RTP affected</li>
            <li>Consider emergency maintenance window</li>
        </ol>
        """

    def _render_deployment_html(self, deployment: DeploymentInfo) -> str:
        """Render deployment note as Confluence storage format HTML."""
        changes_list = "".join(
            f"<li>{self._escape_html(c)}</li>" for c in deployment.changes
        )
        services_list = "".join(
            f"<li>{self._escape_html(s)}</li>" for s in deployment.affected_services
        )
        tickets_list = "".join(
            f"<li>{self._escape_html(t)}</li>" for t in deployment.jira_tickets
        )

        return f"""
        <h2>Deployment Summary</h2>
        <table>
            <tr><th>Version</th><td>{self._escape_html(deployment.version)}</td></tr>
            <tr><th>Environment</th><td>{self._escape_html(deployment.environment)}</td></tr>
            <tr><th>Timestamp</th><td>{self._escape_html(deployment.timestamp)}</td></tr>
            <tr><th>Commit</th><td><code>{self._escape_html(deployment.commit_sha[:12])}</code></td></tr>
            <tr><th>Deployed by</th><td>{self._escape_html(deployment.deployer)}</td></tr>
        </table>

        <h2>Changes</h2>
        <ul>{changes_list or '<li>No changes listed</li>'}</ul>

        <h2>Affected Services</h2>
        <ul>{services_list or '<li>None specified</li>'}</ul>

        <h2>Linked Tickets</h2>
        <ul>{tickets_list or '<li>None</li>'}</ul>

        <h2>Rollback Plan</h2>
        <p>{self._escape_html(deployment.rollback_plan or 'Standard rollback procedure — redeploy previous version.')}</p>

        <h2>Post-Deployment Verification</h2>
        <ac:structured-macro ac:name="tasklist">
            <ac:rich-text-body>
                <ac:task><ac:task-body>Health check endpoints returning 200</ac:task-body></ac:task>
                <ac:task><ac:task-body>Error rate below threshold</ac:task-body></ac:task>
                <ac:task><ac:task-body>RTP calculations verified</ac:task-body></ac:task>
                <ac:task><ac:task-body>Player-facing smoke tests passed</ac:task-body></ac:task>
            </ac:rich-text-body>
        </ac:structured-macro>
        """

    def _render_pir_html(self, incident: IncidentData) -> str:
        """Render a PIR page as Confluence storage format HTML."""
        timeline_rows = "".join(
            f"<tr><td>{self._escape_html(e.get('time', ''))}</td>"
            f"<td>{self._escape_html(e.get('action', ''))}</td>"
            f"<td>{self._escape_html(e.get('actor', ''))}</td></tr>"
            for e in incident.timeline
        )
        action_rows = "".join(
            f"<tr><td>{self._escape_html(a.get('action', ''))}</td>"
            f"<td>{self._escape_html(a.get('owner', ''))}</td>"
            f"<td>{self._escape_html(a.get('due_date', ''))}</td>"
            f"<td>{self._escape_html(a.get('status', 'Open'))}</td></tr>"
            for a in incident.action_items
        )
        games = ", ".join(incident.affected_games) if incident.affected_games else "N/A"

        reg_notice = ""
        if incident.regulatory_notification_required:
            reg_notice = """
            <ac:structured-macro ac:name="warning">
                <ac:rich-text-body>
                    <p><strong>Regulatory Notification Required</strong> — This incident
                    requires notification to the gaming regulator within the jurisdiction's
                    mandated timeframe.</p>
                </ac:rich-text-body>
            </ac:structured-macro>
            """

        return f"""
        {reg_notice}
        <h2>Incident Summary</h2>
        <table>
            <tr><th>Incident</th><td>{self._escape_html(incident.incident_key)}</td></tr>
            <tr><th>Title</th><td>{self._escape_html(incident.title)}</td></tr>
            <tr><th>Severity</th><td>{self._escape_html(incident.severity)}</td></tr>
            <tr><th>Jurisdiction</th><td>{self._escape_html(incident.jurisdiction or 'N/A')}</td></tr>
            <tr><th>Started</th><td>{self._escape_html(incident.started_at)}</td></tr>
            <tr><th>Detected</th><td>{self._escape_html(incident.detected_at)}</td></tr>
            <tr><th>Resolved</th><td>{self._escape_html(incident.resolved_at)}</td></tr>
            <tr><th>Duration</th><td>{incident.duration_minutes} minutes</td></tr>
            <tr><th>Affected Games</th><td>{self._escape_html(games)}</td></tr>
            <tr><th>Player Impact</th><td>{incident.player_impact} players affected</td></tr>
        </table>

        <h2>Impact</h2>
        <p>{self._escape_html(incident.impact)}</p>

        <h2>Timeline</h2>
        <table>
            <thead><tr><th>Time</th><th>Action</th><th>Actor</th></tr></thead>
            <tbody>{timeline_rows}</tbody>
        </table>

        <h2>Root Cause</h2>
        <p>{self._escape_html(incident.root_cause)}</p>

        <h2>Action Items</h2>
        <table>
            <thead><tr><th>Action</th><th>Owner</th><th>Due Date</th><th>Status</th></tr></thead>
            <tbody>{action_rows}</tbody>
        </table>

        <h2>Lessons Learned</h2>
        <ul>
            <li><em>What went well?</em> — To be discussed in PIR meeting</li>
            <li><em>What could be improved?</em> — To be discussed in PIR meeting</li>
            <li><em>Where did we get lucky?</em> — To be discussed in PIR meeting</li>
        </ul>
        """

    def _render_compliance_html(self, report: ComplianceReport) -> str:
        """Render compliance report as Confluence storage format HTML."""
        submissions = "".join(
            f"<tr><td>{self._escape_html(s.get('date', ''))}</td>"
            f"<td>{self._escape_html(s.get('type', ''))}</td>"
            f"<td>{self._escape_html(s.get('status', ''))}</td></tr>"
            for s in report.regulatory_submissions
        )
        events = "".join(
            f"<li>{self._escape_html(e)}</li>" for e in report.notable_events
        )

        complaints_pct = (
            (report.complaints_resolved_in_sla / report.player_complaints * 100)
            if report.player_complaints > 0 else 100.0
        )

        return f"""
        <h2>Compliance Summary — {self._escape_html(report.quarter)}</h2>
        <table>
            <tr><th>Jurisdiction</th><td>{self._escape_html(report.jurisdiction)}</td></tr>
            <tr><th>Reporting Period</th><td>{self._escape_html(report.quarter)}</td></tr>
        </table>

        <h2>Incident Metrics</h2>
        <table>
            <tr><th>Total Incidents</th><td>{report.total_incidents}</td></tr>
            <tr><th>SLA Compliance</th><td>{report.sla_compliance_pct:.1f}%</td></tr>
        </table>

        <h2>Player Complaints</h2>
        <table>
            <tr><th>Total Complaints</th><td>{report.player_complaints}</td></tr>
            <tr><th>Resolved Within SLA</th><td>{report.complaints_resolved_in_sla}</td></tr>
            <tr><th>SLA Compliance</th><td>{complaints_pct:.1f}%</td></tr>
        </table>

        <h2>RTP Audit Results</h2>
        <table>
            <tr><th>Audits Passed</th><td>{report.rtp_audits_passed} / {report.rtp_audits_total}</td></tr>
            <tr><th>Pass Rate</th><td>{(report.rtp_audits_passed / max(report.rtp_audits_total, 1) * 100):.1f}%</td></tr>
        </table>

        <h2>Regulatory Submissions</h2>
        <table>
            <thead><tr><th>Date</th><th>Type</th><th>Status</th></tr></thead>
            <tbody>{submissions or '<tr><td colspan="3">No submissions this quarter</td></tr>'}</tbody>
        </table>

        <h2>Notable Events</h2>
        <ul>{events or '<li>No notable events</li>'}</ul>
        """

    def _fill_pir_template(self, template: str, incident: IncidentData) -> str:
        """Fill PIR template with incident data."""
        replacements = {
            "{{INCIDENT_KEY}}": incident.incident_key,
            "{{TITLE}}": incident.title,
            "{{SEVERITY}}": incident.severity,
            "{{STARTED_AT}}": incident.started_at,
            "{{DETECTED_AT}}": incident.detected_at,
            "{{RESOLVED_AT}}": incident.resolved_at,
            "{{DURATION_MINUTES}}": str(incident.duration_minutes),
            "{{ROOT_CAUSE}}": incident.root_cause,
            "{{IMPACT}}": incident.impact,
            "{{JURISDICTION}}": incident.jurisdiction or "N/A",
            "{{PLAYER_IMPACT}}": str(incident.player_impact),
            "{{AFFECTED_GAMES}}": ", ".join(incident.affected_games) or "N/A",
            "{{REGULATORY_NOTIFICATION}}": "Yes" if incident.regulatory_notification_required else "No",
        }

        result = template
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, self._escape_html(value))
        return result

    def _fill_compliance_template(self, template: str, report: ComplianceReport) -> str:
        """Fill compliance template with report data."""
        replacements = {
            "{{QUARTER}}": report.quarter,
            "{{JURISDICTION}}": report.jurisdiction,
            "{{TOTAL_INCIDENTS}}": str(report.total_incidents),
            "{{SLA_COMPLIANCE_PCT}}": f"{report.sla_compliance_pct:.1f}",
            "{{PLAYER_COMPLAINTS}}": str(report.player_complaints),
            "{{COMPLAINTS_RESOLVED_IN_SLA}}": str(report.complaints_resolved_in_sla),
            "{{RTP_AUDITS_PASSED}}": str(report.rtp_audits_passed),
            "{{RTP_AUDITS_TOTAL}}": str(report.rtp_audits_total),
        }

        result = template
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)
        return result

    @staticmethod
    def _markdown_to_confluence_storage(md_content: str) -> str:
        """Convert Markdown to Confluence storage format (basic conversion).

        Handles headings, bold, italic, code blocks, lists, and links.
        For full fidelity, consider using a dedicated library like markdown2
        or pandoc.
        """
        html = md_content

        # Remove YAML frontmatter
        html = re.sub(r"^---\n.*?\n---\n", "", html, flags=re.DOTALL)

        # Headings (### before ## before #)
        html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
        html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
        html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)

        # Code blocks
        html = re.sub(
            r"```(\w*)\n(.*?)```",
            r'<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">\1</ac:parameter>'
            r"<ac:plain-text-body><![CDATA[\2]]></ac:plain-text-body></ac:structured-macro>",
            html,
            flags=re.DOTALL,
        )

        # Inline code
        html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)

        # Bold and italic
        html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
        html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)

        # Unordered lists
        html = re.sub(r"^[*-] (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)

        # Links
        html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html)

        # Wrap loose <li> in <ul>
        html = re.sub(
            r"((?:<li>.*?</li>\n?)+)",
            r"<ul>\1</ul>",
            html,
        )

        # Paragraphs (lines not already wrapped in tags)
        lines = html.split("\n")
        result = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("<"):
                result.append(f"<p>{stripped}</p>")
            else:
                result.append(line)

        return "\n".join(result)

    @staticmethod
    def _extract_title_from_markdown(content: str, fallback: str) -> str:
        """Extract the first H1 heading from Markdown content."""
        match = re.search(r"^# (.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else fallback.replace("-", " ").title()

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    client = ConfluenceClient.from_env()

    # Example: create a PIR page
    sample_incident = IncidentData(
        incident_key="CASINO-501",
        title="RTP deviation on Mega Moolah",
        severity="SEV2",
        started_at="2026-03-10T14:30:00Z",
        detected_at="2026-03-10T14:35:00Z",
        resolved_at="2026-03-10T15:45:00Z",
        duration_minutes=75,
        root_cause="Faulty RNG seed rotation caused biased outcomes",
        impact="1,200 players affected. Estimated EUR 18,000 in excess payouts.",
        timeline=[
            {"time": "14:30", "action": "RTP alert triggered by monitoring", "actor": "System"},
            {"time": "14:35", "action": "On-call engineer acknowledged", "actor": "J. Smith"},
            {"time": "14:50", "action": "Root cause identified: RNG seed issue", "actor": "J. Smith"},
            {"time": "15:00", "action": "Fix deployed: RNG seed rotation corrected", "actor": "J. Smith"},
            {"time": "15:45", "action": "RTP returned to normal range", "actor": "System"},
        ],
        action_items=[
            {"action": "Add RNG seed rotation monitoring", "owner": "Platform Team", "due_date": "2026-03-17"},
            {"action": "Review all game RNG configurations", "owner": "Game Team", "due_date": "2026-03-24"},
        ],
        jurisdiction="MGA",
        affected_games=["mega-moolah"],
        player_impact=1200,
        regulatory_notification_required=True,
    )

    page = client.create_pir_page(sample_incident)
    print(f"Created PIR page: {page.get('id')}")
