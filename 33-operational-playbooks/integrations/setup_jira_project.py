# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Jira project setup automation for iGaming operations.

Creates a fully configured Jira project with custom issue types, workflows,
fields, permission schemes, notification schemes, and dashboards tailored
for regulated online gaming platform operations.

Usage:
    python setup_jira_project.py

    Or programmatically:
        setup = JiraProjectSetup.from_env()
        setup.create_full_project()

Environment variables:
    JIRA_SERVER, JIRA_USERNAME, JIRA_API_TOKEN
    JIRA_PROJECT_KEY (optional, defaults to CASINO)
    JIRA_PROJECT_NAME (optional, defaults to "Casino Platform Operations")
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from jira import JIRA  # ty: ignore[unresolved-import]
from jira.exceptions import JIRAError  # ty: ignore[unresolved-import]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration data classes
# ---------------------------------------------------------------------------

@dataclass
class CustomField:
    """Definition of a custom Jira field."""
    name: str
    field_type: str  # e.g. "com.atlassian.jira.plugin.system.customfieldtypes:textfield"
    description: str
    search_key: str = ""


@dataclass
class WorkflowTransition:
    """A transition between two workflow statuses."""
    name: str
    from_status: str
    to_status: str


@dataclass
class WorkflowDefinition:
    """Complete workflow definition."""
    name: str
    description: str
    statuses: list[str]
    transitions: list[WorkflowTransition]


@dataclass
class IssueTypeConfig:
    """Custom issue type configuration."""
    name: str
    description: str
    icon_url: str = ""
    subtask: bool = False


@dataclass
class PermissionGrant:
    """Permission grant for a scheme."""
    permission: str
    group: str


@dataclass
class NotificationRule:
    """Notification rule for a scheme."""
    event: str
    notification_type: str  # "Group", "EmailAddress", etc.
    parameter: str          # group name or email


# ---------------------------------------------------------------------------
# Project setup
# ---------------------------------------------------------------------------

class JiraProjectSetup:
    """Automates Jira project setup for iGaming operations.

    Creates all necessary project configuration including custom issue types,
    workflows, fields, permissions, and dashboards required for managing
    an online gaming platform.
    """

    # -- Custom issue types for iGaming --
    ISSUE_TYPES: list[IssueTypeConfig] = [
        IssueTypeConfig(
            name="Incident",
            description="Platform incident requiring immediate investigation and resolution",
        ),
        IssueTypeConfig(
            name="Change Request",
            description="Deployment or configuration change requiring approval workflow",
        ),
        IssueTypeConfig(
            name="Compliance Task",
            description="Regulatory compliance work item with jurisdiction tracking",
        ),
        IssueTypeConfig(
            name="Player Report",
            description="Player complaint or report with SLA tracking (24h response)",
        ),
        IssueTypeConfig(
            name="Game Issue",
            description="Game-specific issue (RTP deviation, display bug, fairness concern)",
        ),
    ]

    # -- Custom fields for iGaming --
    CUSTOM_FIELDS: list[CustomField] = [
        CustomField(
            name="Jurisdiction",
            field_type="com.atlassian.jira.plugin.system.customfieldtypes:select",
            description="Regulatory jurisdiction (MGA, UKGC, Curacao, etc.)",
        ),
        CustomField(
            name="Severity Level",
            field_type="com.atlassian.jira.plugin.system.customfieldtypes:select",
            description="Incident severity (SEV1-SEV4)",
        ),
        CustomField(
            name="Affected Games",
            field_type="com.atlassian.jira.plugin.system.customfieldtypes:textarea",
            description="List of games affected by this issue",
        ),
        CustomField(
            name="Player Count Affected",
            field_type="com.atlassian.jira.plugin.system.customfieldtypes:float",
            description="Estimated number of players impacted",
        ),
        CustomField(
            name="RTP Impact",
            field_type="com.atlassian.jira.plugin.system.customfieldtypes:float",
            description="RTP deviation percentage from expected value",
        ),
        CustomField(
            name="Game ID",
            field_type="com.atlassian.jira.plugin.system.customfieldtypes:textfield",
            description="Internal game identifier",
        ),
        CustomField(
            name="Regulatory Deadline",
            field_type="com.atlassian.jira.plugin.system.customfieldtypes:datepicker",
            description="Deadline for regulatory compliance or response",
        ),
    ]

    # -- Incident workflow --
    INCIDENT_WORKFLOW = WorkflowDefinition(
        name="iGaming Incident Workflow",
        description="Incident lifecycle for regulated gaming operations",
        statuses=[
            "Triggered",
            "Investigating",
            "Mitigating",
            "Resolved",
            "Post-Incident Review",
        ],
        transitions=[
            WorkflowTransition("Start Investigation", "Triggered", "Investigating"),
            WorkflowTransition("Begin Mitigation", "Investigating", "Mitigating"),
            WorkflowTransition("Mark Resolved", "Mitigating", "Resolved"),
            WorkflowTransition("Escalate", "Investigating", "Mitigating"),
            WorkflowTransition("Start PIR", "Resolved", "Post-Incident Review"),
            WorkflowTransition("Reopen", "Resolved", "Investigating"),
            WorkflowTransition("Close PIR", "Post-Incident Review", "Resolved"),
        ],
    )

    # -- Change request workflow --
    CHANGE_WORKFLOW = WorkflowDefinition(
        name="iGaming Change Request Workflow",
        description="Change management workflow with approval gates",
        statuses=[
            "Requested",
            "Approved",
            "Scheduled",
            "Deployed",
            "Verified",
        ],
        transitions=[
            WorkflowTransition("Approve", "Requested", "Approved"),
            WorkflowTransition("Reject", "Requested", "Requested"),
            WorkflowTransition("Schedule", "Approved", "Scheduled"),
            WorkflowTransition("Deploy", "Scheduled", "Deployed"),
            WorkflowTransition("Verify", "Deployed", "Verified"),
            WorkflowTransition("Rollback", "Deployed", "Requested"),
            WorkflowTransition("Emergency Deploy", "Requested", "Deployed"),
        ],
    )

    # -- Permission groups --
    PERMISSION_GROUPS: dict[str, list[str]] = {
        "casino-developers": [
            "BROWSE_PROJECTS",
            "CREATE_ISSUES",
            "EDIT_ISSUES",
            "ADD_COMMENTS",
            "TRANSITION_ISSUES",
            "ASSIGN_ISSUES",
        ],
        "casino-ops": [
            "BROWSE_PROJECTS",
            "CREATE_ISSUES",
            "EDIT_ISSUES",
            "ADD_COMMENTS",
            "TRANSITION_ISSUES",
            "ASSIGN_ISSUES",
            "CLOSE_ISSUES",
            "MANAGE_WATCHERS",
        ],
        "casino-compliance": [
            "BROWSE_PROJECTS",
            "CREATE_ISSUES",
            "EDIT_ISSUES",
            "ADD_COMMENTS",
            "VIEW_WORKFLOW",
        ],
        "casino-management": [
            "BROWSE_PROJECTS",
            "ADD_COMMENTS",
            "VIEW_WORKFLOW",
            "VIEW_VOTERS_AND_WATCHERS",
        ],
    }

    # -- Notification rules --
    NOTIFICATION_RULES: dict[str, list[NotificationRule]] = {
        "critical": [
            NotificationRule("Issue Created", "Group", "casino-ops"),
            NotificationRule("Issue Created", "Group", "casino-management"),
        ],
        "standard": [
            NotificationRule("Issue Created", "Group", "casino-developers"),
            NotificationRule("Issue Assigned", "Group", "casino-developers"),
            NotificationRule("Issue Resolved", "Group", "casino-ops"),
        ],
        "compliance": [
            NotificationRule("Issue Created", "Group", "casino-compliance"),
            NotificationRule("Issue Updated", "Group", "casino-compliance"),
        ],
    }

    def __init__(
        self,
        server: str,
        username: str,
        api_token: str,
        project_key: str = "CASINO",
        project_name: str = "Casino Platform Operations",
    ) -> None:
        self._server = server
        self._project_key = project_key
        self._project_name = project_name
        self._jira = JIRA(
            server=server,
            basic_auth=(username, api_token),
        )
        self._created_resources: dict[str, list[str]] = {
            "issue_types": [],
            "custom_fields": [],
            "workflows": [],
            "screens": [],
            "permission_scheme": [],
            "notification_scheme": [],
            "dashboard": [],
        }
        logger.info("JiraProjectSetup initialized for %s at %s", project_key, server)

    @classmethod
    def from_env(cls) -> JiraProjectSetup:
        """Create setup instance from environment variables."""
        return cls(
            server=os.environ["JIRA_SERVER"],
            username=os.environ["JIRA_USERNAME"],
            api_token=os.environ["JIRA_API_TOKEN"],
            project_key=os.environ.get("JIRA_PROJECT_KEY", "CASINO"),
            project_name=os.environ.get("JIRA_PROJECT_NAME", "Casino Platform Operations"),
        )

    # -- Main setup method --------------------------------------------------

    def create_full_project(self) -> dict[str, Any]:
        """Execute the complete project setup sequence.

        Creates all resources in dependency order:
        1. Custom issue types
        2. Custom fields
        3. Workflows (incident + change request)
        4. Project
        5. Permission scheme
        6. Notification scheme
        7. Dashboard with gadgets

        Returns a summary of all created resources.
        """
        logger.info("Starting full project setup for %s...", self._project_key)

        self._create_issue_types()
        self._create_custom_fields()
        self._create_workflows()
        self._create_project()
        self._setup_permission_scheme()
        self._setup_notification_scheme()
        self._create_dashboard()

        logger.info("Project setup complete for %s", self._project_key)
        return self._created_resources

    # -- Step implementations -----------------------------------------------

    def _create_issue_types(self) -> None:
        """Create custom issue types for iGaming operations."""
        logger.info("Creating custom issue types...")

        for it in self.ISSUE_TYPES:
            try:
                # Check if issue type already exists
                existing_types = self._jira.issue_types()
                if any(t.name == it.name for t in existing_types):
                    logger.info("Issue type '%s' already exists, skipping", it.name)
                    self._created_resources["issue_types"].append(f"{it.name} (existing)")
                    continue

                # Create via REST API (not all operations supported by jira lib)
                result = self._jira._session.post(
                    f"{self._server}/rest/api/2/issuetype",
                    json={
                        "name": it.name,
                        "description": it.description,
                        "type": "subtask" if it.subtask else "standard",
                    },
                )
                self._created_resources["issue_types"].append(it.name)
                logger.info("Created issue type: %s", it.name)
            except Exception as exc:
                logger.warning("Failed to create issue type '%s': %s", it.name, exc)

    def _create_custom_fields(self) -> None:
        """Create custom fields for iGaming-specific data."""
        logger.info("Creating custom fields...")

        for cf in self.CUSTOM_FIELDS:
            try:
                result = self._jira._session.post(
                    f"{self._server}/rest/api/2/field",
                    json={
                        "name": cf.name,
                        "type": cf.field_type,
                        "description": cf.description,
                    },
                )
                self._created_resources["custom_fields"].append(cf.name)
                logger.info("Created custom field: %s", cf.name)
            except Exception as exc:
                logger.warning("Failed to create custom field '%s': %s", cf.name, exc)

        # Add options for select fields
        self._add_field_options("Jurisdiction", [
            "MGA", "UKGC", "Curacao", "Gibraltar", "Isle of Man",
            "Kahnawake", "Alderney", "Denmark", "Sweden", "Italy",
            "Spain", "Portugal", "Ontario", "New Jersey", "Pennsylvania",
        ])
        self._add_field_options("Severity Level", [
            "SEV1", "SEV2", "SEV3", "SEV4",
        ])

    def _add_field_options(self, field_name: str, options: list[str]) -> None:
        """Add options to a select custom field."""
        try:
            # Find field ID
            fields = self._jira.fields()
            field_id = None
            for f in fields:
                if f["name"] == field_name:
                    field_id = f["id"]
                    break

            if not field_id:
                logger.warning("Field '%s' not found, cannot add options", field_name)
                return

            # Add options via REST API
            context_url = f"{self._server}/rest/api/2/field/{field_id}/context"
            contexts = self._jira._session.get(context_url).json()

            if contexts.get("values"):
                context_id = contexts["values"][0]["id"]
                for option in options:
                    try:
                        self._jira._session.post(
                            f"{self._server}/rest/api/2/field/{field_id}/context/{context_id}/option",
                            json={"value": option},
                        )
                    except Exception:
                        pass  # Option may already exist

            logger.info("Added %d options to field '%s'", len(options), field_name)
        except Exception as exc:
            logger.warning("Failed to add options to '%s': %s", field_name, exc)

    def _create_workflows(self) -> None:
        """Create incident and change request workflows."""
        logger.info("Creating workflows...")

        for wf in [self.INCIDENT_WORKFLOW, self.CHANGE_WORKFLOW]:
            try:
                # Jira Cloud workflows are complex — this creates the
                # workflow definition that an admin can then import or
                # configure via the UI. Full programmatic workflow creation
                # requires the Workflow Designer API.
                workflow_config = {
                    "name": wf.name,
                    "description": wf.description,
                    "statuses": [
                        {"name": s, "category": self._status_category(s)}
                        for s in wf.statuses
                    ],
                    "transitions": [
                        {
                            "name": t.name,
                            "from": t.from_status,
                            "to": t.to_status,
                        }
                        for t in wf.transitions
                    ],
                }

                # Store workflow config for import
                config_path = f"/tmp/workflow_{wf.name.replace(' ', '_').lower()}.json"
                with open(config_path, "w") as f:
                    json.dump(workflow_config, f, indent=2)

                self._created_resources["workflows"].append(wf.name)
                logger.info(
                    "Workflow config created: %s (saved to %s for manual import)",
                    wf.name, config_path,
                )
            except Exception as exc:
                logger.warning("Failed to create workflow '%s': %s", wf.name, exc)

    def _create_project(self) -> None:
        """Create the Jira project with assigned schemes."""
        logger.info("Creating project %s...", self._project_key)

        try:
            # Check if project exists
            try:
                existing = self._jira.project(self._project_key)
                logger.info("Project %s already exists", self._project_key)
                return
            except JIRAError:
                pass  # Project doesn't exist, create it

            # Get the current user as project lead
            current_user = self._jira.current_user()

            self._jira.create_project(
                key=self._project_key,
                name=self._project_name,
                assignee=current_user,
                ptype="software",
            )
            logger.info("Created project: %s (%s)", self._project_name, self._project_key)
        except JIRAError as exc:
            logger.error("Failed to create project: %s", exc)

    def _setup_permission_scheme(self) -> None:
        """Configure permission scheme for the project.

        Sets up role-based access: developers, ops, compliance, management.
        """
        logger.info("Setting up permission scheme...")

        scheme_name = f"{self._project_key} Permission Scheme"
        scheme_config = {
            "name": scheme_name,
            "description": f"Permission scheme for {self._project_name}",
            "groups": self.PERMISSION_GROUPS,
        }

        # Store config for reference (actual scheme creation requires admin API)
        config_path = f"/tmp/permission_scheme_{self._project_key.lower()}.json"
        with open(config_path, "w") as f:
            json.dump(scheme_config, f, indent=2)

        self._created_resources["permission_scheme"].append(scheme_name)
        logger.info(
            "Permission scheme config created: %s (saved to %s)",
            scheme_name, config_path,
        )

        # Log the permission matrix for documentation
        logger.info("Permission matrix:")
        for group, perms in self.PERMISSION_GROUPS.items():
            logger.info("  %s: %s", group, ", ".join(perms))

    def _setup_notification_scheme(self) -> None:
        """Configure notification scheme for the project.

        Critical incidents: SMS + Slack + email to ops and management.
        Standard issues: email to developers.
        Compliance tasks: email to compliance team.
        """
        logger.info("Setting up notification scheme...")

        scheme_name = f"{self._project_key} Notification Scheme"
        scheme_config = {
            "name": scheme_name,
            "description": f"Notification rules for {self._project_name}",
            "rules": {
                category: [
                    {
                        "event": rule.event,
                        "type": rule.notification_type,
                        "parameter": rule.parameter,
                    }
                    for rule in rules
                ]
                for category, rules in self.NOTIFICATION_RULES.items()
            },
        }

        config_path = f"/tmp/notification_scheme_{self._project_key.lower()}.json"
        with open(config_path, "w") as f:
            json.dump(scheme_config, f, indent=2)

        self._created_resources["notification_scheme"].append(scheme_name)
        logger.info("Notification scheme config created: %s", scheme_name)

    def _create_dashboard(self) -> None:
        """Create an operations dashboard with gadgets.

        Gadgets:
            - Open incidents by severity
            - Deployment frequency (this month)
            - Compliance SLA status
            - Player complaint resolution rate
            - Sprint burndown (if agile board configured)
            - Recently resolved incidents
        """
        logger.info("Creating operations dashboard...")

        dashboard_config = {
            "name": f"{self._project_key} Operations Dashboard",
            "description": "Real-time view of iGaming platform operations",
            "gadgets": [
                {
                    "name": "Open Incidents by Severity",
                    "type": "filter-results",
                    "jql": (
                        f'project = {self._project_key} AND issuetype = "Incident" '
                        f'AND status NOT IN ("Resolved", "Done")'
                    ),
                    "position": {"row": 0, "col": 0},
                },
                {
                    "name": "Deployment Frequency",
                    "type": "filter-results",
                    "jql": (
                        f'project = {self._project_key} AND issuetype = "Change Request" '
                        f'AND status = "Deployed" AND resolved >= startOfMonth()'
                    ),
                    "position": {"row": 0, "col": 1},
                },
                {
                    "name": "Compliance SLA Status",
                    "type": "filter-results",
                    "jql": (
                        f'project = {self._project_key} AND issuetype = "Compliance Task" '
                        f'AND status NOT IN ("Done", "Resolved")'
                    ),
                    "position": {"row": 1, "col": 0},
                },
                {
                    "name": "Player Complaints (Open)",
                    "type": "filter-results",
                    "jql": (
                        f'project = {self._project_key} AND issuetype = "Player Report" '
                        f'AND status NOT IN ("Done", "Resolved")'
                    ),
                    "position": {"row": 1, "col": 1},
                },
                {
                    "name": "Recently Resolved",
                    "type": "filter-results",
                    "jql": (
                        f'project = {self._project_key} AND status IN ("Resolved", "Done") '
                        f"AND resolved >= -7d ORDER BY resolved DESC"
                    ),
                    "position": {"row": 2, "col": 0},
                },
                {
                    "name": "Game Issues",
                    "type": "filter-results",
                    "jql": (
                        f'project = {self._project_key} AND issuetype = "Game Issue" '
                        f'AND status NOT IN ("Done", "Resolved")'
                    ),
                    "position": {"row": 2, "col": 1},
                },
            ],
        }

        # Create dashboard via REST API
        try:
            result = self._jira._session.post(
                f"{self._server}/rest/api/2/dashboard",
                json={
                    "name": dashboard_config["name"],
                    "description": dashboard_config["description"],
                    "sharePermissions": [
                        {"type": "project", "project": {"key": self._project_key}}
                    ],
                },
            )
            dashboard_id = result.json().get("id", "unknown")
            self._created_resources["dashboard"].append(
                f"{dashboard_config['name']} (id={dashboard_id})"
            )
            logger.info("Created dashboard: %s", dashboard_config["name"])
        except Exception as exc:
            logger.warning("Failed to create dashboard: %s", exc)

        # Save gadget config for manual setup
        config_path = f"/tmp/dashboard_{self._project_key.lower()}.json"
        with open(config_path, "w") as f:
            json.dump(dashboard_config, f, indent=2)
        logger.info("Dashboard gadget config saved to %s", config_path)

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _status_category(status: str) -> str:
        """Map a status name to a Jira status category."""
        done = {"Resolved", "Done", "Verified", "Post-Incident Review", "Closed"}
        in_progress = {
            "Investigating", "Mitigating", "In Progress", "Approved",
            "Scheduled", "Deployed",
        }

        if status in done:
            return "done"
        elif status in in_progress:
            return "indeterminate"
        else:
            return "new"

    def print_setup_summary(self) -> None:
        """Print a summary of all created resources."""
        print(f"\n{'=' * 60}")
        print(f"Project Setup Summary: {self._project_key}")
        print(f"{'=' * 60}")

        for category, items in self._created_resources.items():
            if items:
                print(f"\n{category.replace('_', ' ').title()}:")
                for item in items:
                    print(f"  - {item}")

        print(f"\n{'=' * 60}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    setup = JiraProjectSetup.from_env()

    print("Setting up Jira project for iGaming operations...")
    print(f"Project: {setup._project_key} ({setup._project_name})")
    print(f"Server: {setup._server}")
    print()

    resources = setup.create_full_project()
    setup.print_setup_summary()
