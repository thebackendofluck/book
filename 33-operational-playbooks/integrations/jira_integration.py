# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Jira API client for iGaming platform operations.

Provides incident management, change request workflows, deployment tracking,
and SLA enforcement for regulated online gaming environments. Integrates with
PagerDuty alerts, CI/CD pipelines, and compliance reporting.

Usage:
    client = JiraClient.from_env()
    incident = client.create_incident_from_alert(alert_payload)
    client.transition_ticket(incident.key, TicketTransition.INVESTIGATING)
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from jira import JIRA, Issue  # ty: ignore[unresolved-import]
from jira.exceptions import JIRAError  # ty: ignore[unresolved-import]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    """Incident severity aligned with iGaming regulatory requirements."""
    SEV1 = "SEV1"  # Platform-wide outage, player funds at risk
    SEV2 = "SEV2"  # Major feature degraded, RTP deviation detected
    SEV3 = "SEV3"  # Minor feature issue, single game affected
    SEV4 = "SEV4"  # Cosmetic / low-impact issue


class TicketTransition(str, Enum):
    """Standard workflow transitions for iGaming operations."""
    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    INVESTIGATING = "Investigating"
    MITIGATING = "Mitigating"
    REVIEW = "Review"
    RESOLVED = "Resolved"
    PIR = "Post-Incident Review"
    DONE = "Done"
    # Change request transitions
    REQUESTED = "Requested"
    APPROVED = "Approved"
    SCHEDULED = "Scheduled"
    DEPLOYED = "Deployed"
    VERIFIED = "Verified"


class IssueType(str, Enum):
    """Custom issue types for iGaming projects."""
    INCIDENT = "Incident"
    CHANGE_REQUEST = "Change Request"
    COMPLIANCE_TASK = "Compliance Task"
    PLAYER_REPORT = "Player Report"
    GAME_ISSUE = "Game Issue"
    STORY = "Story"
    BUG = "Bug"
    TASK = "Task"


@dataclass
class IGamingFields:
    """Custom field values specific to iGaming operations."""
    jurisdiction: Optional[str] = None          # e.g. "MGA", "UKGC", "Curacao"
    severity: Optional[Severity] = None
    affected_games: list[str] = field(default_factory=list)
    player_impact: Optional[int] = None         # estimated affected player count
    rtp_impact: Optional[float] = None          # RTP deviation percentage
    game_id: Optional[str] = None
    regulatory_deadline: Optional[datetime] = None


@dataclass
class SLAPolicy:
    """SLA definitions for regulated incident response."""
    severity: Severity
    response_time: timedelta
    resolution_time: timedelta
    escalation_after: timedelta
    regulatory_report_required: bool = False

    def is_response_breached(self, created: datetime) -> bool:
        """Check whether the response SLA has been breached."""
        now = datetime.now(timezone.utc)
        return (now - created) > self.response_time

    def is_resolution_breached(self, created: datetime) -> bool:
        """Check whether the resolution SLA has been breached."""
        now = datetime.now(timezone.utc)
        return (now - created) > self.resolution_time


# Default SLA policies per severity
DEFAULT_SLA_POLICIES: dict[Severity, SLAPolicy] = {
    Severity.SEV1: SLAPolicy(
        severity=Severity.SEV1,
        response_time=timedelta(minutes=15),
        resolution_time=timedelta(hours=4),
        escalation_after=timedelta(minutes=30),
        regulatory_report_required=True,
    ),
    Severity.SEV2: SLAPolicy(
        severity=Severity.SEV2,
        response_time=timedelta(hours=1),
        resolution_time=timedelta(hours=12),
        escalation_after=timedelta(hours=2),
        regulatory_report_required=True,
    ),
    Severity.SEV3: SLAPolicy(
        severity=Severity.SEV3,
        response_time=timedelta(hours=4),
        resolution_time=timedelta(days=2),
        escalation_after=timedelta(hours=8),
    ),
    Severity.SEV4: SLAPolicy(
        severity=Severity.SEV4,
        response_time=timedelta(hours=24),
        resolution_time=timedelta(days=5),
        escalation_after=timedelta(days=2),
    ),
}

# Player complaint SLA (regulatory requirement: 24h response)
PLAYER_COMPLAINT_SLA = SLAPolicy(
    severity=Severity.SEV3,
    response_time=timedelta(hours=24),
    resolution_time=timedelta(days=5),
    escalation_after=timedelta(hours=12),
    regulatory_report_required=True,
)


# ---------------------------------------------------------------------------
# Jira client
# ---------------------------------------------------------------------------

class JiraClient:
    """Jira API client tailored for iGaming platform operations.

    Supports API-token and OAuth authentication, custom iGaming fields,
    SLA tracking, and bulk sprint management.
    """

    # Mapping from custom field display names to Jira internal IDs.
    # These must match the Jira instance configuration (see setup_jira_project.py).
    CUSTOM_FIELD_MAP: dict[str, str] = {
        "jurisdiction": "customfield_10100",
        "severity_level": "customfield_10101",
        "affected_games": "customfield_10102",
        "player_impact": "customfield_10103",
        "rtp_impact": "customfield_10104",
        "game_id": "customfield_10105",
        "regulatory_deadline": "customfield_10106",
    }

    def __init__(
        self,
        server: str,
        username: str,
        api_token: str,
        project_key: str = "CASINO",
        *,
        sla_policies: Optional[dict[Severity, SLAPolicy]] = None,
    ) -> None:
        self._server = server
        self._project_key = project_key
        self._sla_policies = sla_policies or DEFAULT_SLA_POLICIES

        self._jira = JIRA(
            server=server,
            basic_auth=(username, api_token),
            options={"verify": True},
        )
        logger.info("Connected to Jira at %s as %s", server, username)

    # -- Factory methods ----------------------------------------------------

    @classmethod
    def from_env(cls, project_key: str = "CASINO") -> JiraClient:
        """Create a client from environment variables.

        Expected env vars:
            JIRA_SERVER   — e.g. https://company.atlassian.net
            JIRA_USERNAME — email address
            JIRA_API_TOKEN
            JIRA_PROJECT_KEY (optional, defaults to CASINO)
        """
        return cls(
            server=os.environ["JIRA_SERVER"],
            username=os.environ["JIRA_USERNAME"],
            api_token=os.environ["JIRA_API_TOKEN"],
            project_key=os.environ.get("JIRA_PROJECT_KEY", project_key),
        )

    @classmethod
    def from_oauth(
        cls,
        server: str,
        access_token: str,
        access_token_secret: str,
        consumer_key: str,
        key_cert: str,
        project_key: str = "CASINO",
    ) -> JiraClient:
        """Create a client using OAuth 1.0a credentials.

        Typically used for server-to-server integrations where API tokens
        are not available.
        """
        instance = cls.__new__(cls)
        instance._server = server
        instance._project_key = project_key
        instance._sla_policies = DEFAULT_SLA_POLICIES

        oauth_dict = {
            "access_token": access_token,
            "access_token_secret": access_token_secret,
            "consumer_key": consumer_key,
            "key_cert": key_cert,
        }
        instance._jira = JIRA(server=server, oauth=oauth_dict)
        logger.info("Connected to Jira at %s via OAuth", server)
        return instance

    # -- Incident management ------------------------------------------------

    def create_incident_from_alert(
        self,
        alert: dict[str, Any],
        *,
        igaming_fields: Optional[IGamingFields] = None,
    ) -> Issue:
        """Create a Jira incident from a PagerDuty or monitoring alert.

        Args:
            alert: Monitoring alert payload. Expected keys:
                - title: Alert title
                - description: Alert details
                - severity: SEV1-SEV4 string
                - source: Originating system (e.g. "pagerduty", "datadog")
                - incident_key: External incident identifier
            igaming_fields: Optional iGaming-specific field values.

        Returns:
            The created Jira issue.
        """
        severity = Severity(alert.get("severity", "SEV3"))
        sla = self._sla_policies[severity]

        fields: dict[str, Any] = {
            "project": {"key": self._project_key},
            "summary": f"[{severity.value}] {alert['title']}",
            "description": self._build_incident_description(alert),
            "issuetype": {"name": IssueType.INCIDENT.value},
            "priority": {"name": self._severity_to_priority(severity)},
            "labels": ["auto-created", "monitoring", alert.get("source", "unknown")],
        }

        if igaming_fields:
            fields.update(self._map_igaming_fields(igaming_fields))

        # Set regulatory deadline based on SLA
        if sla.regulatory_report_required:
            deadline = datetime.now(timezone.utc) + sla.resolution_time
            fields[self.CUSTOM_FIELD_MAP["regulatory_deadline"]] = (
                deadline.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
            )

        issue = self._jira.create_issue(fields=fields)
        logger.info(
            "Created incident %s from alert '%s' (severity=%s)",
            issue.key, alert["title"], severity.value,
        )

        # Add external incident link
        if "incident_key" in alert:
            self._jira.add_comment(
                issue.key,
                f"Linked to external incident: {alert['incident_key']}\n"
                f"Source: {alert.get('source', 'monitoring')}",
            )

        return issue

    def create_player_complaint(
        self,
        player_id: str,
        subject: str,
        description: str,
        jurisdiction: str,
        *,
        game_id: Optional[str] = None,
    ) -> Issue:
        """Create a player complaint ticket with regulatory SLA tracking.

        Player complaints must be acknowledged within 24 hours per most
        gaming regulatory frameworks (UKGC, MGA, etc.).
        """
        deadline = datetime.now(timezone.utc) + PLAYER_COMPLAINT_SLA.response_time

        fields: dict[str, Any] = {
            "project": {"key": self._project_key},
            "summary": f"[Player Complaint] {subject}",
            "description": (
                f"*Player ID:* {player_id}\n"
                f"*Jurisdiction:* {jurisdiction}\n\n"
                f"{description}"
            ),
            "issuetype": {"name": IssueType.PLAYER_REPORT.value},
            "priority": {"name": "High"},
            "labels": ["player-complaint", f"jurisdiction-{jurisdiction.lower()}"],
            self.CUSTOM_FIELD_MAP["jurisdiction"]: jurisdiction,
            self.CUSTOM_FIELD_MAP["regulatory_deadline"]: (
                deadline.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
            ),
        }

        if game_id:
            fields[self.CUSTOM_FIELD_MAP["game_id"]] = game_id

        issue = self._jira.create_issue(fields=fields)
        logger.info(
            "Created player complaint %s for player %s (jurisdiction=%s, deadline=%s)",
            issue.key, player_id, jurisdiction, deadline.isoformat(),
        )
        return issue

    # -- Change request management ------------------------------------------

    def create_change_request(
        self,
        title: str,
        description: str,
        *,
        deployment_target: str = "production",
        affected_services: Optional[list[str]] = None,
        igaming_fields: Optional[IGamingFields] = None,
        rollback_plan: Optional[str] = None,
    ) -> Issue:
        """Create a change request for a deployment or configuration change.

        Args:
            title: Short description of the change.
            description: Full change details including scope and impact.
            deployment_target: Environment (production, staging, etc.).
            affected_services: List of services impacted.
            igaming_fields: iGaming-specific metadata.
            rollback_plan: How to revert if the change fails.

        Returns:
            The created Jira issue.
        """
        body = (
            f"h2. Change Description\n{description}\n\n"
            f"h2. Deployment Target\n{deployment_target}\n\n"
            f"h2. Affected Services\n"
        )
        if affected_services:
            body += "\n".join(f"* {s}" for s in affected_services)
        else:
            body += "_(none specified)_"

        if rollback_plan:
            body += f"\n\nh2. Rollback Plan\n{rollback_plan}"

        fields: dict[str, Any] = {
            "project": {"key": self._project_key},
            "summary": f"[CR] {title}",
            "description": body,
            "issuetype": {"name": IssueType.CHANGE_REQUEST.value},
            "labels": ["change-request", f"env-{deployment_target}"],
        }

        if igaming_fields:
            fields.update(self._map_igaming_fields(igaming_fields))

        issue = self._jira.create_issue(fields=fields)
        logger.info("Created change request %s: %s", issue.key, title)
        return issue

    # -- Ticket lifecycle ---------------------------------------------------

    def transition_ticket(self, issue_key: str, target_status: TicketTransition) -> None:
        """Transition a ticket to the specified status.

        Finds the matching transition by name and executes it. Raises
        ValueError if the transition is not available from the current state.
        """
        transitions = self._jira.transitions(issue_key)
        target_name = target_status.value

        for t in transitions:
            if t["name"].lower() == target_name.lower():
                self._jira.transition_issue(issue_key, t["id"])
                logger.info("Transitioned %s -> %s", issue_key, target_name)
                return

        available = [t["name"] for t in transitions]
        raise ValueError(
            f"Transition '{target_name}' not available for {issue_key}. "
            f"Available: {available}"
        )

    def add_deployment_comment(
        self,
        issue_key: str,
        *,
        version: str,
        environment: str,
        commit_sha: str,
        pipeline_url: Optional[str] = None,
        deployer: Optional[str] = None,
    ) -> None:
        """Add a deployment information comment to a Jira ticket.

        Called by CI/CD pipelines after successful deployment.
        """
        lines = [
            "h3. Deployment Information",
            f"||Field||Value||",
            f"|Version|{version}|",
            f"|Environment|{environment}|",
            f"|Commit|{{monospace}}{commit_sha[:12]}{{monospace}}|",
        ]
        if pipeline_url:
            lines.append(f"|Pipeline|[View Build|{pipeline_url}]|")
        if deployer:
            lines.append(f"|Deployed by|{deployer}|")
        lines.append(f"|Timestamp|{datetime.now(timezone.utc).isoformat()}|")

        self._jira.add_comment(issue_key, "\n".join(lines))
        logger.info("Added deployment comment to %s (version=%s)", issue_key, version)

    # -- Git integration ----------------------------------------------------

    @staticmethod
    def extract_ticket_from_branch(branch_name: str) -> Optional[str]:
        """Extract a Jira ticket ID from a Git branch name.

        Supports patterns like:
            CASINO-123-fix-rtp-calculation
            feature/CASINO-456-new-game
            bugfix/CASINO-789
        """
        match = re.search(r"([A-Z][A-Z0-9]+-\d+)", branch_name)
        return match.group(1) if match else None

    def link_commit_to_ticket(
        self,
        branch_name: str,
        commit_sha: str,
        commit_message: str,
        *,
        repo_url: Optional[str] = None,
    ) -> Optional[str]:
        """Auto-link a Git commit to its Jira ticket based on branch name.

        Returns the ticket key if linked, None otherwise.
        """
        ticket_key = self.extract_ticket_from_branch(branch_name)
        if not ticket_key:
            logger.debug("No ticket ID found in branch: %s", branch_name)
            return None

        commit_link = commit_sha[:12]
        if repo_url:
            commit_link = f"[{commit_sha[:12]}|{repo_url}/commit/{commit_sha}]"

        self._jira.add_comment(
            ticket_key,
            f"Commit {commit_link}\n{{quote}}{commit_message}{{quote}}",
        )
        logger.info("Linked commit %s to %s", commit_sha[:12], ticket_key)
        return ticket_key

    # -- Bulk / sprint operations -------------------------------------------

    def bulk_transition(
        self,
        jql: str,
        target_status: TicketTransition,
        *,
        max_results: int = 200,
        comment: Optional[str] = None,
    ) -> list[str]:
        """Transition all tickets matching a JQL query.

        Useful for sprint management: e.g., close all Done tickets at
        sprint end, or move all Scheduled items to In Progress.

        Returns list of transitioned issue keys.
        """
        issues = self._jira.search_issues(jql, maxResults=max_results)
        transitioned: list[str] = []

        for issue in issues:
            try:
                self.transition_ticket(issue.key, target_status)
                if comment:
                    self._jira.add_comment(issue.key, comment)
                transitioned.append(issue.key)
            except (ValueError, JIRAError) as exc:
                logger.warning("Failed to transition %s: %s", issue.key, exc)

        logger.info(
            "Bulk transition: %d/%d tickets moved to %s",
            len(transitioned), len(issues), target_status.value,
        )
        return transitioned

    def get_sprint_tickets(
        self,
        sprint_name: Optional[str] = None,
        *,
        board_id: Optional[int] = None,
    ) -> list[Issue]:
        """Get all tickets in the current (or named) sprint."""
        if sprint_name:
            jql = (
                f'project = {self._project_key} '
                f'AND sprint = "{sprint_name}" ORDER BY priority DESC'
            )
        else:
            jql = (
                f"project = {self._project_key} "
                f"AND sprint in openSprints() ORDER BY priority DESC"
            )
        return list(self._jira.search_issues(jql, maxResults=500))

    def close_sprint_done_tickets(self, sprint_name: Optional[str] = None) -> list[str]:
        """Close all 'Done' tickets in a sprint — typical sprint cleanup."""
        if sprint_name:
            jql = (
                f'project = {self._project_key} AND sprint = "{sprint_name}" '
                f'AND status = "Done"'
            )
        else:
            jql = (
                f"project = {self._project_key} AND sprint in openSprints() "
                f'AND status = "Done"'
            )
        return self.bulk_transition(
            jql,
            TicketTransition.DONE,
            comment="Auto-closed at sprint end.",
        )

    # -- SLA tracking -------------------------------------------------------

    def check_sla_compliance(self, issue_key: str) -> dict[str, Any]:
        """Check SLA compliance for an incident ticket.

        Returns a dict with response/resolution SLA status, time remaining
        or time exceeded, and whether regulatory reporting is required.
        """
        issue = self._jira.issue(issue_key)
        created_str = issue.fields.created
        created = datetime.fromisoformat(created_str.replace("+0000", "+00:00"))

        severity_field = getattr(
            issue.fields,
            self.CUSTOM_FIELD_MAP["severity_level"].replace("customfield_", "customfield_"),
            None,
        )
        severity = Severity(severity_field) if severity_field else Severity.SEV3
        sla = self._sla_policies[severity]

        now = datetime.now(timezone.utc)
        response_elapsed = now - created
        resolution_elapsed = now - created

        return {
            "issue_key": issue_key,
            "severity": severity.value,
            "created": created.isoformat(),
            "response_sla": {
                "target": str(sla.response_time),
                "elapsed": str(response_elapsed),
                "breached": sla.is_response_breached(created),
                "remaining": str(max(sla.response_time - response_elapsed, timedelta(0))),
            },
            "resolution_sla": {
                "target": str(sla.resolution_time),
                "elapsed": str(resolution_elapsed),
                "breached": sla.is_resolution_breached(created),
                "remaining": str(max(sla.resolution_time - resolution_elapsed, timedelta(0))),
            },
            "escalation_after": str(sla.escalation_after),
            "needs_escalation": response_elapsed > sla.escalation_after,
            "regulatory_report_required": sla.regulatory_report_required,
        }

    def get_breached_sla_tickets(self) -> list[dict[str, Any]]:
        """Find all open incidents that have breached their SLA."""
        jql = (
            f"project = {self._project_key} "
            f'AND issuetype = "Incident" '
            f'AND status NOT IN ("Resolved", "Done", "Post-Incident Review")'
        )
        issues = self._jira.search_issues(jql, maxResults=500)
        breached: list[dict[str, Any]] = []

        for issue in issues:
            compliance = self.check_sla_compliance(issue.key)
            if compliance["response_sla"]["breached"] or compliance["resolution_sla"]["breached"]:
                breached.append(compliance)

        return breached

    # -- Helpers ------------------------------------------------------------

    def _build_incident_description(self, alert: dict[str, Any]) -> str:
        """Format a monitoring alert into a Jira description."""
        lines = [
            "h2. Incident Details",
            f"*Source:* {alert.get('source', 'monitoring')}",
            f"*Alert Key:* {alert.get('incident_key', 'N/A')}",
            f"*Severity:* {alert.get('severity', 'SEV3')}",
            f"*Triggered at:* {alert.get('triggered_at', datetime.now(timezone.utc).isoformat())}",
            "",
            "h2. Description",
            alert.get("description", "No description provided."),
            "",
            "h2. Impact",
            alert.get("impact", "Under investigation."),
        ]
        if "runbook_url" in alert:
            lines.extend(["", f"h2. Runbook\n[View Runbook|{alert['runbook_url']}]"])
        return "\n".join(lines)

    @staticmethod
    def _severity_to_priority(severity: Severity) -> str:
        """Map internal severity to Jira priority name."""
        mapping = {
            Severity.SEV1: "Highest",
            Severity.SEV2: "High",
            Severity.SEV3: "Medium",
            Severity.SEV4: "Low",
        }
        return mapping[severity]

    def _map_igaming_fields(self, fields: IGamingFields) -> dict[str, Any]:
        """Convert IGamingFields to Jira custom-field dict."""
        mapped: dict[str, Any] = {}
        if fields.jurisdiction:
            mapped[self.CUSTOM_FIELD_MAP["jurisdiction"]] = fields.jurisdiction
        if fields.severity:
            mapped[self.CUSTOM_FIELD_MAP["severity_level"]] = fields.severity.value
        if fields.affected_games:
            mapped[self.CUSTOM_FIELD_MAP["affected_games"]] = ", ".join(fields.affected_games)
        if fields.player_impact is not None:
            mapped[self.CUSTOM_FIELD_MAP["player_impact"]] = fields.player_impact
        if fields.rtp_impact is not None:
            mapped[self.CUSTOM_FIELD_MAP["rtp_impact"]] = fields.rtp_impact
        if fields.game_id:
            mapped[self.CUSTOM_FIELD_MAP["game_id"]] = fields.game_id
        if fields.regulatory_deadline:
            mapped[self.CUSTOM_FIELD_MAP["regulatory_deadline"]] = (
                fields.regulatory_deadline.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
            )
        return mapped


# ---------------------------------------------------------------------------
# CLI entry point (for testing / manual use)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    client = JiraClient.from_env()

    # Example: create incident from alert
    sample_alert = {
        "title": "High RTP deviation detected on Mega Moolah",
        "description": "RTP observed at 103.2% over last 10,000 spins (expected 96.5%)",
        "severity": "SEV2",
        "source": "pagerduty",
        "incident_key": "PD-2026-0042",
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "impact": "Potential financial exposure. Estimated loss rate: EUR 2,400/hour.",
        "runbook_url": "https://wiki.internal/runbooks/rtp-deviation",
    }

    issue = client.create_incident_from_alert(
        sample_alert,
        igaming_fields=IGamingFields(
            jurisdiction="MGA",
            severity=Severity.SEV2,
            affected_games=["mega-moolah"],
            player_impact=1200,
            rtp_impact=6.7,
        ),
    )
    print(f"Created: {issue.key}")

    # Check SLA
    sla = client.check_sla_compliance(issue.key)
    print(json.dumps(sla, indent=2))
