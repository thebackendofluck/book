# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Bidirectional sync between Jira and Confluence for iGaming operations.

Synchronizes epic status, sprint metrics, incident-to-PIR links, and
management dashboards between Jira and Confluence. Designed to run as
a scheduled job (cron) or triggered by webhooks.

Usage:
    sync = JiraConfluenceSync.from_env()
    sync.sync_epic_status_to_confluence()
    sync.update_sprint_metrics()
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from atlassian import Confluence  # ty: ignore[unresolved-import]
from jira import JIRA, Issue  # ty: ignore[unresolved-import]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class EpicStatus:
    """Snapshot of a Jira epic's status for Confluence sync."""
    key: str
    summary: str
    status: str
    priority: str
    assignee: str
    story_points_total: float
    story_points_done: float
    child_issues_total: int
    child_issues_done: int
    labels: list[str] = field(default_factory=list)
    confluence_page_id: Optional[str] = None


@dataclass
class SprintMetrics:
    """Sprint velocity and burndown data."""
    sprint_name: str
    sprint_id: int
    start_date: str
    end_date: str
    committed_points: float
    completed_points: float
    velocity: float
    stories_committed: int
    stories_completed: int
    bugs_found: int
    bugs_resolved: int
    carryover_points: float


@dataclass
class DashboardData:
    """Aggregated data for management reporting dashboard."""
    generated_at: str
    open_incidents: list[dict[str, Any]]
    deployment_frequency: dict[str, int]    # environment -> count this month
    sla_compliance: dict[str, float]        # severity -> compliance percentage
    sprint_velocity_trend: list[dict[str, Any]]
    compliance_status: dict[str, str]       # jurisdiction -> status
    top_issues: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Sync engine
# ---------------------------------------------------------------------------

class JiraConfluenceSync:
    """Bidirectional sync between Jira and Confluence.

    Bridges project management data in Jira with documentation and
    reporting pages in Confluence for iGaming platform operations.
    """

    def __init__(
        self,
        jira_server: str,
        jira_username: str,
        jira_token: str,
        confluence_url: str,
        confluence_username: str,
        confluence_token: str,
        *,
        jira_project_key: str = "CASINO",
        confluence_space_key: str = "OPS",
    ) -> None:
        self._jira_project = jira_project_key
        self._confluence_space = confluence_space_key

        self._jira = JIRA(
            server=jira_server,
            basic_auth=(jira_username, jira_token),
        )
        self._confluence = Confluence(
            url=confluence_url,
            username=confluence_username,
            password=confluence_token,
            cloud=True,
        )
        logger.info(
            "JiraConfluenceSync initialized (jira=%s, confluence=%s)",
            jira_server, confluence_url,
        )

    @classmethod
    def from_env(cls) -> JiraConfluenceSync:
        """Create sync engine from environment variables.

        Expected env vars:
            JIRA_SERVER, JIRA_USERNAME, JIRA_API_TOKEN
            CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN
            JIRA_PROJECT_KEY (optional), CONFLUENCE_SPACE_KEY (optional)
        """
        return cls(
            jira_server=os.environ["JIRA_SERVER"],
            jira_username=os.environ["JIRA_USERNAME"],
            jira_token=os.environ["JIRA_API_TOKEN"],
            confluence_url=os.environ["CONFLUENCE_URL"],
            confluence_username=os.environ["CONFLUENCE_USERNAME"],
            confluence_token=os.environ["CONFLUENCE_API_TOKEN"],
            jira_project_key=os.environ.get("JIRA_PROJECT_KEY", "CASINO"),
            confluence_space_key=os.environ.get("CONFLUENCE_SPACE_KEY", "OPS"),
        )

    # -- Epic status sync ---------------------------------------------------

    def sync_epic_status_to_confluence(
        self,
        *,
        parent_page_title: str = "Project Tracker",
    ) -> dict[str, Any]:
        """Sync all Jira epic statuses to a Confluence project tracker page.

        Creates or updates a tracker page with a table showing each epic's
        status, progress, and story point completion. Designed for program
        management visibility.

        Returns the updated Confluence page metadata.
        """
        epics = self._fetch_all_epics()
        html = self._render_epic_tracker(epics)

        parent = self._get_or_create_page(parent_page_title)
        page_title = f"{self._jira_project} — Epic Status Tracker"

        page = self._create_or_update_page(
            page_title, html, parent_id=int(parent["id"])
        )
        logger.info("Synced %d epics to Confluence tracker", len(epics))
        return page

    def auto_create_confluence_page_for_epic(
        self,
        epic_key: str,
        *,
        parent_page_title: str = "Project Epics",
    ) -> dict[str, Any]:
        """Auto-create a Confluence page when a new Jira epic is created.

        Called by the webhook handler when an epic-type issue is created.
        The page includes the epic summary, description, acceptance criteria,
        and a placeholder for technical design notes.
        """
        epic = self._jira.issue(epic_key)
        title = f"Epic: {epic.key} — {epic.fields.summary}"

        description = getattr(epic.fields, "description", "") or ""
        assignee = getattr(epic.fields.assignee, "displayName", "Unassigned") if epic.fields.assignee else "Unassigned"
        labels = ", ".join(epic.fields.labels) if epic.fields.labels else "None"

        html = f"""
        <h2>Epic Overview</h2>
        <table>
            <tr><th>Key</th><td>{self._escape(epic.key)}</td></tr>
            <tr><th>Summary</th><td>{self._escape(epic.fields.summary)}</td></tr>
            <tr><th>Status</th><td>{self._escape(str(epic.fields.status))}</td></tr>
            <tr><th>Assignee</th><td>{self._escape(assignee)}</td></tr>
            <tr><th>Labels</th><td>{self._escape(labels)}</td></tr>
        </table>

        <h2>Description</h2>
        <p>{self._escape(description) if description else '<em>No description provided</em>'}</p>

        <h2>Acceptance Criteria</h2>
        <p><em>To be defined by product owner...</em></p>

        <h2>Technical Design</h2>
        <p><em>To be filled by engineering team...</em></p>

        <h2>Child Issues</h2>
        <p><em>Will be updated automatically as stories are created.</em></p>

        <hr/>
        <p><em>Auto-generated from Jira epic {self._escape(epic.key)} |
        {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</em></p>
        """

        parent = self._get_or_create_page(parent_page_title)
        page = self._create_or_update_page(title, html, parent_id=int(parent["id"]))
        logger.info("Created Confluence page for epic %s", epic_key)
        return page

    # -- Sprint velocity / burndown -----------------------------------------

    def update_sprint_metrics(
        self,
        board_id: int,
        *,
        parent_page_title: str = "Sprint Reports",
        num_sprints: int = 6,
    ) -> dict[str, Any]:
        """Update Confluence with sprint velocity and burndown data.

        Fetches the last N sprints from the specified board, calculates
        velocity trends, and publishes a formatted report page.
        """
        sprints = self._fetch_recent_sprints(board_id, num_sprints)
        metrics = [self._calculate_sprint_metrics(s) for s in sprints]
        html = self._render_sprint_report(metrics)

        parent = self._get_or_create_page(parent_page_title)
        page_title = f"{self._jira_project} — Sprint Velocity Report"
        page = self._create_or_update_page(page_title, html, parent_id=int(parent["id"]))
        logger.info("Updated sprint metrics: %d sprints", len(metrics))
        return page

    # -- Incident ↔ PIR linking ---------------------------------------------

    def link_incident_to_pir(
        self,
        incident_key: str,
        pir_page_id: str,
    ) -> None:
        """Link a Jira incident ticket to its Confluence PIR page.

        Adds a remote link on the Jira issue pointing to the Confluence PIR,
        and adds a comment on the Jira ticket with the PIR URL.
        """
        pir_page = self._confluence.get_page_by_id(pir_page_id)
        pir_title = pir_page.get("title", "Post-Incident Review")
        pir_url = f"{self._confluence.url}/pages/viewpage.action?pageId={pir_page_id}"

        # Add remote link to Jira issue
        self._jira.add_remote_link(
            incident_key,
            destination={
                "url": pir_url,
                "title": pir_title,
            },
        )

        # Add comment with link
        self._jira.add_comment(
            incident_key,
            f"PIR page created: [{pir_title}|{pir_url}]",
        )
        logger.info("Linked %s to PIR page %s", incident_key, pir_page_id)

    def sync_incident_pir_status(self) -> list[dict[str, str]]:
        """Find incidents missing PIR pages and create stubs.

        Scans for resolved incidents that don't have a linked PIR page
        and creates stub pages for them. Returns list of created stubs.
        """
        jql = (
            f"project = {self._jira_project} "
            f'AND issuetype = "Incident" '
            f'AND status = "Resolved" '
            f"AND labels NOT IN (pir-created)"
        )
        issues = self._jira.search_issues(jql, maxResults=100)
        created_stubs: list[dict[str, str]] = []

        for issue in issues:
            severity = getattr(issue.fields, "priority", None)
            sev_name = severity.name if severity else "Unknown"

            page = self._create_pir_stub(
                incident_key=issue.key,
                title=str(issue.fields.summary),
                severity=sev_name,
            )

            # Mark as processed
            issue.update(fields={"labels": issue.fields.labels + ["pir-created"]})
            self.link_incident_to_pir(issue.key, page["id"])

            created_stubs.append({
                "incident_key": issue.key,
                "pir_page_id": page["id"],
                "title": str(issue.fields.summary),
            })

        logger.info("Created %d PIR stubs for resolved incidents", len(created_stubs))
        return created_stubs

    # -- Dashboard / management reporting -----------------------------------

    def export_dashboard_data(self) -> DashboardData:
        """Export aggregated dashboard data for management reporting.

        Collects open incidents, deployment frequency, SLA compliance,
        sprint velocity trends, and compliance status into a single
        data structure for rendering.
        """
        now = datetime.now(timezone.utc)

        # Open incidents
        incidents_jql = (
            f"project = {self._jira_project} "
            f'AND issuetype = "Incident" '
            f'AND status NOT IN ("Resolved", "Done")'
        )
        open_incidents = [
            {
                "key": i.key,
                "summary": str(i.fields.summary),
                "priority": i.fields.priority.name if i.fields.priority else "Unknown",
                "status": str(i.fields.status),
                "created": i.fields.created,
            }
            for i in self._jira.search_issues(incidents_jql, maxResults=50)
        ]

        # Deployment frequency (change requests resolved this month)
        deploy_jql = (
            f"project = {self._jira_project} "
            f'AND issuetype = "Change Request" '
            f'AND status = "Deployed" '
            f'AND resolved >= startOfMonth()'
        )
        deployments = self._jira.search_issues(deploy_jql, maxResults=200)
        deployment_freq: dict[str, int] = {}
        for dep in deployments:
            env = "production"  # Default; could parse from labels
            for label in dep.fields.labels:
                if label.startswith("env-"):
                    env = label.replace("env-", "")
            deployment_freq[env] = deployment_freq.get(env, 0) + 1

        return DashboardData(
            generated_at=now.isoformat(),
            open_incidents=open_incidents,
            deployment_frequency=deployment_freq,
            sla_compliance={},  # Populated by SLA check integration
            sprint_velocity_trend=[],  # Populated by sprint metrics
            compliance_status={},  # Populated by compliance integration
            top_issues=open_incidents[:5],
        )

    def publish_management_dashboard(
        self,
        *,
        parent_page_title: str = "Management Dashboard",
    ) -> dict[str, Any]:
        """Publish a management reporting dashboard to Confluence.

        Aggregates data from Jira and publishes a formatted dashboard page
        with incident status, deployment metrics, SLA compliance, and
        sprint performance.
        """
        data = self.export_dashboard_data()
        html = self._render_dashboard(data)

        parent = self._get_or_create_page(parent_page_title)
        page_title = f"{self._jira_project} — Operations Dashboard"
        page = self._create_or_update_page(page_title, html, parent_id=int(parent["id"]))
        logger.info("Published management dashboard")
        return page

    # -- Internal helpers ---------------------------------------------------

    def _fetch_all_epics(self) -> list[EpicStatus]:
        """Fetch all epics from the Jira project with child issue counts."""
        jql = f"project = {self._jira_project} AND issuetype = Epic ORDER BY priority DESC"
        issues = self._jira.search_issues(jql, maxResults=200)
        epics: list[EpicStatus] = []

        for issue in issues:
            # Fetch child issues for this epic
            children_jql = f'"Epic Link" = {issue.key}'
            children = self._jira.search_issues(children_jql, maxResults=500)

            done_statuses = {"Done", "Resolved", "Closed"}
            children_done = sum(
                1 for c in children if str(c.fields.status) in done_statuses
            )

            # Sum story points
            total_points = sum(
                getattr(c.fields, "story_points", 0) or 0 for c in children
            )
            done_points = sum(
                getattr(c.fields, "story_points", 0) or 0
                for c in children
                if str(c.fields.status) in done_statuses
            )

            assignee = (
                issue.fields.assignee.displayName
                if issue.fields.assignee
                else "Unassigned"
            )

            epics.append(EpicStatus(
                key=issue.key,
                summary=str(issue.fields.summary),
                status=str(issue.fields.status),
                priority=issue.fields.priority.name if issue.fields.priority else "Medium",
                assignee=assignee,
                story_points_total=total_points,
                story_points_done=done_points,
                child_issues_total=len(children),
                child_issues_done=children_done,
                labels=issue.fields.labels or [],
            ))

        return epics

    def _fetch_recent_sprints(self, board_id: int, count: int) -> list[Any]:
        """Fetch the most recent closed sprints from a board."""
        sprints = self._jira.sprints(board_id, state="closed", maxResults=count)
        return sorted(sprints, key=lambda s: s.startDate if hasattr(s, "startDate") else "", reverse=True)[:count]

    def _calculate_sprint_metrics(self, sprint: Any) -> SprintMetrics:
        """Calculate velocity and burndown metrics for a sprint."""
        jql = (
            f"project = {self._jira_project} "
            f'AND sprint = "{sprint.name}"'
        )
        issues = self._jira.search_issues(jql, maxResults=500)

        done_statuses = {"Done", "Resolved", "Closed"}
        completed = [i for i in issues if str(i.fields.status) in done_statuses]

        committed_points = sum(getattr(i.fields, "story_points", 0) or 0 for i in issues)
        completed_points = sum(getattr(i.fields, "story_points", 0) or 0 for i in completed)

        bugs = [i for i in issues if str(i.fields.issuetype) == "Bug"]
        bugs_resolved = [b for b in bugs if str(b.fields.status) in done_statuses]

        return SprintMetrics(
            sprint_name=sprint.name,
            sprint_id=sprint.id,
            start_date=getattr(sprint, "startDate", ""),
            end_date=getattr(sprint, "endDate", ""),
            committed_points=committed_points,
            completed_points=completed_points,
            velocity=completed_points,
            stories_committed=len(issues),
            stories_completed=len(completed),
            bugs_found=len(bugs),
            bugs_resolved=len(bugs_resolved),
            carryover_points=committed_points - completed_points,
        )

    def _create_pir_stub(
        self,
        incident_key: str,
        title: str,
        severity: str,
    ) -> dict[str, Any]:
        """Create a minimal PIR stub page."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        html = f"""
        <ac:structured-macro ac:name="warning">
            <ac:rich-text-body>
                <p>PIR stub auto-created. Complete this page after incident resolution.</p>
            </ac:rich-text-body>
        </ac:structured-macro>
        <table>
            <tr><th>Incident</th><td>{self._escape(incident_key)}</td></tr>
            <tr><th>Title</th><td>{self._escape(title)}</td></tr>
            <tr><th>Severity</th><td>{self._escape(severity)}</td></tr>
            <tr><th>Created</th><td>{now}</td></tr>
        </table>
        <h2>Timeline</h2><p><em>Pending...</em></p>
        <h2>Root Cause</h2><p><em>Pending...</em></p>
        <h2>Action Items</h2><p><em>Pending...</em></p>
        """
        parent = self._get_or_create_page("Post-Incident Reviews")
        page_title = f"PIR: {incident_key} — {title}"
        return self._create_or_update_page(page_title, html, parent_id=int(parent["id"]))

    # -- Rendering ----------------------------------------------------------

    def _render_epic_tracker(self, epics: list[EpicStatus]) -> str:
        """Render epic status tracker as Confluence HTML."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        rows = ""
        for e in epics:
            progress = (
                f"{e.story_points_done}/{e.story_points_total} SP "
                f"({e.child_issues_done}/{e.child_issues_total} issues)"
            )
            pct = (
                int(e.story_points_done / e.story_points_total * 100)
                if e.story_points_total > 0 else 0
            )
            status_color = self._status_color(e.status)
            rows += (
                f"<tr>"
                f"<td>{self._escape(e.key)}</td>"
                f"<td>{self._escape(e.summary)}</td>"
                f'<td><span style="color:{status_color};font-weight:bold">'
                f"{self._escape(e.status)}</span></td>"
                f"<td>{self._escape(e.assignee)}</td>"
                f"<td>{progress}</td>"
                f"<td>{pct}%</td>"
                f"</tr>"
            )

        return f"""
        <p><em>Last synced: {now}</em></p>
        <table>
            <thead><tr>
                <th>Key</th><th>Epic</th><th>Status</th>
                <th>Assignee</th><th>Progress</th><th>%</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>
        """

    def _render_sprint_report(self, metrics: list[SprintMetrics]) -> str:
        """Render sprint velocity report as Confluence HTML."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        rows = ""
        for m in metrics:
            completion_pct = (
                int(m.completed_points / m.committed_points * 100)
                if m.committed_points > 0 else 0
            )
            rows += (
                f"<tr>"
                f"<td>{self._escape(m.sprint_name)}</td>"
                f"<td>{m.start_date[:10] if m.start_date else 'N/A'}</td>"
                f"<td>{m.end_date[:10] if m.end_date else 'N/A'}</td>"
                f"<td>{m.committed_points}</td>"
                f"<td>{m.completed_points}</td>"
                f"<td>{completion_pct}%</td>"
                f"<td>{m.carryover_points}</td>"
                f"<td>{m.bugs_found} / {m.bugs_resolved}</td>"
                f"</tr>"
            )

        avg_velocity = (
            sum(m.velocity for m in metrics) / len(metrics)
            if metrics else 0
        )

        return f"""
        <h2>Sprint Velocity Trend</h2>
        <p><em>Last updated: {now}</em></p>
        <p><strong>Average Velocity:</strong> {avg_velocity:.1f} story points/sprint</p>
        <table>
            <thead><tr>
                <th>Sprint</th><th>Start</th><th>End</th>
                <th>Committed</th><th>Completed</th><th>%</th>
                <th>Carryover</th><th>Bugs (found/resolved)</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>
        """

    def _render_dashboard(self, data: DashboardData) -> str:
        """Render management dashboard as Confluence HTML."""
        # Open incidents table
        incident_rows = "".join(
            f"<tr><td>{self._escape(i['key'])}</td>"
            f"<td>{self._escape(i['summary'])}</td>"
            f"<td>{self._escape(i['priority'])}</td>"
            f"<td>{self._escape(i['status'])}</td></tr>"
            for i in data.open_incidents
        )

        # Deployment frequency
        deploy_rows = "".join(
            f"<tr><td>{self._escape(env)}</td><td>{count}</td></tr>"
            for env, count in data.deployment_frequency.items()
        )

        return f"""
        <p><em>Generated: {self._escape(data.generated_at)}</em></p>

        <h2>Open Incidents ({len(data.open_incidents)})</h2>
        <table>
            <thead><tr><th>Key</th><th>Summary</th><th>Priority</th><th>Status</th></tr></thead>
            <tbody>{incident_rows or '<tr><td colspan="4">No open incidents</td></tr>'}</tbody>
        </table>

        <h2>Deployment Frequency (This Month)</h2>
        <table>
            <thead><tr><th>Environment</th><th>Deployments</th></tr></thead>
            <tbody>{deploy_rows or '<tr><td colspan="2">No deployments this month</td></tr>'}</tbody>
        </table>

        <h2>SLA Compliance</h2>
        <p><em>Data from SLA tracking integration</em></p>

        <h2>Sprint Performance</h2>
        <p><em>See Sprint Velocity Report for details</em></p>
        """

    # -- Utilities ----------------------------------------------------------

    def _get_or_create_page(self, title: str) -> dict[str, Any]:
        """Get a page by title or create it if it doesn't exist."""
        existing = self._confluence.get_page_by_title(self._confluence_space, title)
        if existing:
            return existing
        return self._confluence.create_page(
            space=self._confluence_space,
            title=title,
            body=f"<p>Auto-generated parent page for {self._escape(title.lower())}.</p>",
        )

    def _create_or_update_page(
        self,
        title: str,
        body: str,
        *,
        parent_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Create or update a Confluence page by title."""
        existing = self._confluence.get_page_by_title(self._confluence_space, title)
        if existing:
            return self._confluence.update_page(
                page_id=existing["id"], title=title, body=body,
            )
        return self._confluence.create_page(
            space=self._confluence_space,
            title=title,
            body=body,
            parent_id=parent_id,
        )

    @staticmethod
    def _escape(text: str) -> str:
        """Escape HTML special characters."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    @staticmethod
    def _status_color(status: str) -> str:
        """Map Jira status to a display color."""
        colors = {
            "To Do": "#999999",
            "In Progress": "#0052CC",
            "In Review": "#FF8B00",
            "Done": "#36B37E",
            "Resolved": "#36B37E",
            "Closed": "#36B37E",
        }
        return colors.get(status, "#333333")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    sync = JiraConfluenceSync.from_env()

    print("Syncing epic statuses...")
    sync.sync_epic_status_to_confluence()

    print("Publishing management dashboard...")
    sync.publish_management_dashboard()

    print("Checking for incidents needing PIR pages...")
    stubs = sync.sync_incident_pir_status()
    for stub in stubs:
        print(f"  Created PIR stub for {stub['incident_key']}")

    print("Done.")
