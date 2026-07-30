# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""FastAPI webhook receiver for cross-system automation in iGaming operations.

Receives webhooks from Jira, GitLab/GitHub, and PagerDuty, then orchestrates
automated workflows: ticket transitions, Confluence page creation, deployment
pipeline triggers, and audit logging.

Usage:
    uvicorn webhook_handler:app --host 0.0.0.0 --port 8080

Environment variables:
    JIRA_SERVER, JIRA_USERNAME, JIRA_API_TOKEN
    CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN
    WEBHOOK_SECRET           — shared secret for signature verification
    GITLAB_WEBHOOK_SECRET    — GitLab webhook token
    GITHUB_WEBHOOK_SECRET    — GitHub webhook secret
    PAGERDUTY_WEBHOOK_SECRET — PagerDuty webhook signing key
    DEPLOYMENT_PIPELINE_URL  — URL to trigger deployment pipeline
    DEPLOYMENT_PIPELINE_TOKEN — Auth token for pipeline trigger
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request  # ty: ignore[unresolved-import]
from fastapi.middleware.trustedhost import TrustedHostMiddleware  # ty: ignore[unresolved-import]
from pydantic import BaseModel, Field  # ty: ignore[unresolved-import]

from confluence_integration import ConfluenceClient
from jira_integration import (
    IGamingFields,
    JiraClient,
    Severity,
    TicketTransition,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="iGaming Webhook Handler",
    description="Cross-system automation for iGaming platform operations",
    version="1.0.0",
)

# Trusted hosts (restrict in production)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])


# ---------------------------------------------------------------------------
# Rate limiting (in-memory, per-source)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple in-memory rate limiter for webhook endpoints.

    Tracks request counts per source within a sliding window. In production,
    replace with Redis-backed rate limiting.
    """

    def __init__(self, max_requests: int = 100, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, source: str) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        now = time.time()
        cutoff = now - self.window_seconds

        # Prune old entries
        self._requests[source] = [
            t for t in self._requests[source] if t > cutoff
        ]

        if len(self._requests[source]) >= self.max_requests:
            return False

        self._requests[source].append(now)
        return True


rate_limiter = RateLimiter(max_requests=100, window_seconds=60)


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

class AuditLogger:
    """Structured audit logger for webhook events.

    Logs all incoming webhooks and resulting actions for compliance and
    debugging purposes. In production, send to a centralized logging
    system (ELK, Datadog, etc.).
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger("audit")

    def log_webhook_received(
        self,
        source: str,
        event_type: str,
        payload_summary: str,
        *,
        remote_addr: Optional[str] = None,
    ) -> None:
        """Log an incoming webhook event."""
        self._logger.info(
            "WEBHOOK_RECEIVED source=%s event=%s summary=%s remote=%s",
            source, event_type, payload_summary, remote_addr or "unknown",
        )

    def log_action_taken(
        self,
        source: str,
        action: str,
        details: str,
        *,
        success: bool = True,
    ) -> None:
        """Log an automated action triggered by a webhook."""
        level = "INFO" if success else "ERROR"
        self._logger.log(
            logging.INFO if success else logging.ERROR,
            "ACTION_%s source=%s action=%s details=%s",
            "SUCCESS" if success else "FAILED", source, action, details,
        )

    def log_signature_failure(
        self,
        source: str,
        remote_addr: Optional[str] = None,
    ) -> None:
        """Log a webhook signature verification failure."""
        self._logger.warning(
            "SIGNATURE_FAILED source=%s remote=%s",
            source, remote_addr or "unknown",
        )


audit = AuditLogger()


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def verify_gitlab_token(token: str, secret: str) -> bool:
    """Verify GitLab webhook token (simple string comparison)."""
    return hmac.compare_digest(token, secret)


def verify_pagerduty_signature(
    payload: bytes,
    signatures: str,
    secret: str,
) -> bool:
    """Verify PagerDuty webhook v3 signature.

    PagerDuty sends comma-separated signatures; we check if any match.
    """
    expected = hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()

    for sig in signatures.split(","):
        sig = sig.strip()
        if sig.startswith("v1="):
            if hmac.compare_digest(sig[3:], expected):
                return True
    return False


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class WebhookResponse(BaseModel):
    """Standard webhook response."""
    status: str = "ok"
    message: str = ""
    actions_taken: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    timestamp: str = ""
    version: str = "1.0.0"


# ---------------------------------------------------------------------------
# Lazy-initialized clients
# ---------------------------------------------------------------------------

_jira_client: Optional[JiraClient] = None
_confluence_client: Optional[ConfluenceClient] = None


def get_jira_client() -> JiraClient:
    """Get or create the Jira client singleton."""
    global _jira_client
    if _jira_client is None:
        _jira_client = JiraClient.from_env()
    return _jira_client


def get_confluence_client() -> ConfluenceClient:
    """Get or create the Confluence client singleton."""
    global _confluence_client
    if _confluence_client is None:
        _confluence_client = ConfluenceClient.from_env()
    return _confluence_client


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint for load balancer / monitoring."""
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Jira webhooks
# ---------------------------------------------------------------------------

@app.post("/webhooks/jira", response_model=WebhookResponse)
async def handle_jira_webhook(request: Request) -> WebhookResponse:
    """Handle Jira webhook events (issue created, updated, transitioned).

    Supported events:
        - jira:issue_created — log and optionally create Confluence pages for epics
        - jira:issue_updated — track field changes for audit trail
        - jira:issue_deleted — log for compliance
    """
    if not rate_limiter.check("jira"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    body = await request.json()
    event = body.get("webhookEvent", "unknown")
    issue_data = body.get("issue", {})
    issue_key = issue_data.get("key", "unknown")
    issue_type = issue_data.get("fields", {}).get("issuetype", {}).get("name", "")

    audit.log_webhook_received(
        source="jira",
        event_type=event,
        payload_summary=f"{issue_key} ({issue_type})",
        remote_addr=request.client.host if request.client else None,
    )

    actions: list[str] = []

    # Auto-create Confluence page for new epics
    if event == "jira:issue_created" and issue_type == "Epic":
        try:
            from jira_confluence_sync import JiraConfluenceSync
            sync = JiraConfluenceSync.from_env()
            sync.auto_create_confluence_page_for_epic(issue_key)
            actions.append(f"Created Confluence page for epic {issue_key}")
            audit.log_action_taken("jira", "create_confluence_page", issue_key)
        except Exception as exc:
            logger.error("Failed to create Confluence page for %s: %s", issue_key, exc)
            audit.log_action_taken(
                "jira", "create_confluence_page", str(exc), success=False,
            )

    # Track status transitions for audit
    if event == "jira:issue_updated":
        changelog = body.get("changelog", {})
        for item in changelog.get("items", []):
            if item.get("field") == "status":
                old_status = item.get("fromString", "")
                new_status = item.get("toString", "")
                actions.append(
                    f"{issue_key} transitioned: {old_status} -> {new_status}"
                )
                audit.log_action_taken(
                    "jira", "status_transition",
                    f"{issue_key}: {old_status} -> {new_status}",
                )

    # Trigger deployment pipeline when ticket closed (if labeled)
    if event == "jira:issue_updated":
        new_status = ""
        changelog = body.get("changelog", {})
        for item in changelog.get("items", []):
            if item.get("field") == "status":
                new_status = item.get("toString", "")

        labels = issue_data.get("fields", {}).get("labels", [])
        if new_status == "Done" and "auto-deploy" in labels:
            triggered = await _trigger_deployment_pipeline(issue_key)
            if triggered:
                actions.append(f"Triggered deployment pipeline for {issue_key}")

    return WebhookResponse(
        status="ok",
        message=f"Processed Jira event: {event}",
        actions_taken=actions,
    )


# ---------------------------------------------------------------------------
# GitLab / GitHub webhooks
# ---------------------------------------------------------------------------

@app.post("/webhooks/gitlab", response_model=WebhookResponse)
async def handle_gitlab_webhook(
    request: Request,
    x_gitlab_token: Optional[str] = Header(None),
) -> WebhookResponse:
    """Handle GitLab webhook events (push, merge request, pipeline).

    Workflows:
        - MR merged -> transition Jira ticket -> update Confluence
        - Pipeline success -> add deployment info to Jira
        - Push -> link commits to Jira tickets
    """
    if not rate_limiter.check("gitlab"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Verify token
    secret = os.environ.get("GITLAB_WEBHOOK_SECRET", "")
    if secret and x_gitlab_token:
        if not verify_gitlab_token(x_gitlab_token, secret):
            audit.log_signature_failure(
                "gitlab",
                request.client.host if request.client else None,
            )
            raise HTTPException(status_code=401, detail="Invalid webhook token")

    body = await request.json()
    event_type = body.get("object_kind", "unknown")

    audit.log_webhook_received(
        source="gitlab",
        event_type=event_type,
        payload_summary=body.get("project", {}).get("name", "unknown"),
        remote_addr=request.client.host if request.client else None,
    )

    actions: list[str] = []

    if event_type == "merge_request":
        actions.extend(await _handle_gitlab_merge_request(body))
    elif event_type == "push":
        actions.extend(await _handle_gitlab_push(body))
    elif event_type == "pipeline":
        actions.extend(await _handle_gitlab_pipeline(body))

    return WebhookResponse(
        status="ok",
        message=f"Processed GitLab event: {event_type}",
        actions_taken=actions,
    )


@app.post("/webhooks/github", response_model=WebhookResponse)
async def handle_github_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
) -> WebhookResponse:
    """Handle GitHub webhook events (push, pull request, workflow run).

    Workflows:
        - PR merged -> transition Jira ticket -> update Confluence
        - Workflow success -> add deployment info to Jira
        - Push -> link commits to Jira tickets
    """
    if not rate_limiter.check("github"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    raw_body = await request.body()

    # Verify signature
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if secret and x_hub_signature_256:
        if not verify_github_signature(raw_body, x_hub_signature_256, secret):
            audit.log_signature_failure(
                "github",
                request.client.host if request.client else None,
            )
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    body = await request.json()
    event_type = request.headers.get("X-GitHub-Event", "unknown")

    audit.log_webhook_received(
        source="github",
        event_type=event_type,
        payload_summary=body.get("repository", {}).get("full_name", "unknown"),
        remote_addr=request.client.host if request.client else None,
    )

    actions: list[str] = []

    if event_type == "pull_request" and body.get("action") == "closed" and body.get("pull_request", {}).get("merged"):
        actions.extend(await _handle_github_pr_merged(body))
    elif event_type == "push":
        actions.extend(await _handle_github_push(body))
    elif event_type == "workflow_run" and body.get("action") == "completed":
        actions.extend(await _handle_github_workflow(body))

    return WebhookResponse(
        status="ok",
        message=f"Processed GitHub event: {event_type}",
        actions_taken=actions,
    )


# ---------------------------------------------------------------------------
# PagerDuty webhooks
# ---------------------------------------------------------------------------

@app.post("/webhooks/pagerduty", response_model=WebhookResponse)
async def handle_pagerduty_webhook(
    request: Request,
    x_pagerduty_signature: Optional[str] = Header(None),
) -> WebhookResponse:
    """Handle PagerDuty webhook events (incident triggered, resolved).

    Workflow:
        - Incident triggered -> create Jira incident -> create Confluence PIR stub
        - Incident resolved -> transition Jira ticket -> update PIR page
    """
    if not rate_limiter.check("pagerduty"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    raw_body = await request.body()

    # Verify signature
    secret = os.environ.get("PAGERDUTY_WEBHOOK_SECRET", "")
    if secret and x_pagerduty_signature:
        if not verify_pagerduty_signature(raw_body, x_pagerduty_signature, secret):
            audit.log_signature_failure(
                "pagerduty",
                request.client.host if request.client else None,
            )
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    body = await request.json()
    actions: list[str] = []

    # PagerDuty v3 webhook format
    for message in body.get("messages", []):
        event = message.get("event", {})
        event_type = event.get("event_type", "")
        incident = event.get("data", {})

        audit.log_webhook_received(
            source="pagerduty",
            event_type=event_type,
            payload_summary=incident.get("title", "unknown"),
            remote_addr=request.client.host if request.client else None,
        )

        if event_type == "incident.triggered":
            actions.extend(await _handle_pagerduty_incident_triggered(incident))
        elif event_type == "incident.resolved":
            actions.extend(await _handle_pagerduty_incident_resolved(incident))

    return WebhookResponse(
        status="ok",
        message="Processed PagerDuty webhook",
        actions_taken=actions,
    )


# ---------------------------------------------------------------------------
# Workflow handlers
# ---------------------------------------------------------------------------

async def _handle_gitlab_merge_request(body: dict[str, Any]) -> list[str]:
    """Handle GitLab merge request merged -> Jira transition + Confluence update."""
    actions: list[str] = []
    mr = body.get("object_attributes", {})

    if mr.get("state") != "merged":
        return actions

    branch = mr.get("source_branch", "")
    jira_client = get_jira_client()
    ticket_key = jira_client.extract_ticket_from_branch(branch)

    if not ticket_key:
        return actions

    # Transition Jira ticket to Review
    try:
        jira_client.transition_ticket(ticket_key, TicketTransition.REVIEW)
        actions.append(f"Transitioned {ticket_key} to Review")
        audit.log_action_taken("gitlab", "jira_transition", f"{ticket_key} -> Review")
    except Exception as exc:
        logger.error("Failed to transition %s: %s", ticket_key, exc)
        audit.log_action_taken("gitlab", "jira_transition", str(exc), success=False)

    # Add MR info as comment
    try:
        jira_client.add_deployment_comment(
            ticket_key,
            version=mr.get("title", "unknown"),
            environment="staging",
            commit_sha=mr.get("merge_commit_sha", "unknown"),
            pipeline_url=mr.get("url", ""),
            deployer=body.get("user", {}).get("username", "unknown"),
        )
        actions.append(f"Added MR comment to {ticket_key}")
    except Exception as exc:
        logger.error("Failed to add comment to %s: %s", ticket_key, exc)

    return actions


async def _handle_gitlab_push(body: dict[str, Any]) -> list[str]:
    """Handle GitLab push -> link commits to Jira tickets."""
    actions: list[str] = []
    branch = body.get("ref", "").replace("refs/heads/", "")
    jira_client = get_jira_client()
    repo_url = body.get("project", {}).get("web_url", "")

    for commit in body.get("commits", []):
        ticket_key = jira_client.link_commit_to_ticket(
            branch,
            commit.get("id", ""),
            commit.get("message", ""),
            repo_url=repo_url,
        )
        if ticket_key:
            actions.append(f"Linked commit {commit['id'][:12]} to {ticket_key}")

    return actions


async def _handle_gitlab_pipeline(body: dict[str, Any]) -> list[str]:
    """Handle GitLab pipeline success -> add deployment info to Jira."""
    actions: list[str] = []
    pipeline = body.get("object_attributes", {})

    if pipeline.get("status") != "success":
        return actions

    branch = pipeline.get("ref", "")
    jira_client = get_jira_client()
    ticket_key = jira_client.extract_ticket_from_branch(branch)

    if ticket_key:
        try:
            jira_client.add_deployment_comment(
                ticket_key,
                version=f"pipeline-{pipeline.get('id', 'unknown')}",
                environment=pipeline.get("ref", "unknown"),
                commit_sha=pipeline.get("sha", "unknown"),
                pipeline_url=body.get("project", {}).get("web_url", "")
                + f"/-/pipelines/{pipeline.get('id', '')}",
            )
            actions.append(f"Added pipeline info to {ticket_key}")
        except Exception as exc:
            logger.error("Failed to add pipeline info to %s: %s", ticket_key, exc)

    return actions


async def _handle_github_pr_merged(body: dict[str, Any]) -> list[str]:
    """Handle GitHub PR merged -> Jira transition + Confluence update."""
    actions: list[str] = []
    pr = body.get("pull_request", {})
    branch = pr.get("head", {}).get("ref", "")

    jira_client = get_jira_client()
    ticket_key = jira_client.extract_ticket_from_branch(branch)

    if not ticket_key:
        return actions

    try:
        jira_client.transition_ticket(ticket_key, TicketTransition.REVIEW)
        actions.append(f"Transitioned {ticket_key} to Review")
    except Exception as exc:
        logger.error("Failed to transition %s: %s", ticket_key, exc)

    try:
        jira_client.add_deployment_comment(
            ticket_key,
            version=pr.get("title", "unknown"),
            environment="staging",
            commit_sha=pr.get("merge_commit_sha", "unknown"),
            pipeline_url=pr.get("html_url", ""),
            deployer=pr.get("user", {}).get("login", "unknown"),
        )
        actions.append(f"Added PR comment to {ticket_key}")
    except Exception as exc:
        logger.error("Failed to add comment to %s: %s", ticket_key, exc)

    return actions


async def _handle_github_push(body: dict[str, Any]) -> list[str]:
    """Handle GitHub push -> link commits to Jira tickets."""
    actions: list[str] = []
    branch = body.get("ref", "").replace("refs/heads/", "")
    jira_client = get_jira_client()
    repo_url = body.get("repository", {}).get("html_url", "")

    for commit in body.get("commits", []):
        ticket_key = jira_client.link_commit_to_ticket(
            branch,
            commit.get("id", ""),
            commit.get("message", ""),
            repo_url=repo_url,
        )
        if ticket_key:
            actions.append(f"Linked commit {commit['id'][:12]} to {ticket_key}")

    return actions


async def _handle_github_workflow(body: dict[str, Any]) -> list[str]:
    """Handle GitHub workflow completed -> add info to Jira."""
    actions: list[str] = []
    workflow = body.get("workflow_run", {})

    if workflow.get("conclusion") != "success":
        return actions

    branch = workflow.get("head_branch", "")
    jira_client = get_jira_client()
    ticket_key = jira_client.extract_ticket_from_branch(branch)

    if ticket_key:
        try:
            jira_client.add_deployment_comment(
                ticket_key,
                version=f"workflow-{workflow.get('id', 'unknown')}",
                environment=branch,
                commit_sha=workflow.get("head_sha", "unknown"),
                pipeline_url=workflow.get("html_url", ""),
            )
            actions.append(f"Added workflow info to {ticket_key}")
        except Exception as exc:
            logger.error("Failed to add workflow info to %s: %s", ticket_key, exc)

    return actions


async def _handle_pagerduty_incident_triggered(incident: dict[str, Any]) -> list[str]:
    """PagerDuty incident triggered -> create Jira incident + Confluence PIR stub."""
    actions: list[str] = []
    title = incident.get("title", "Unknown Incident")
    severity_map = {
        "P1": "SEV1", "P2": "SEV2", "P3": "SEV3", "P4": "SEV4",
    }
    urgency = incident.get("urgency", "low")
    pd_priority = incident.get("priority", {}).get("summary", "P3")
    severity = severity_map.get(pd_priority, "SEV3" if urgency == "low" else "SEV2")

    alert_payload = {
        "title": title,
        "description": incident.get("description", title),
        "severity": severity,
        "source": "pagerduty",
        "incident_key": incident.get("id", ""),
        "triggered_at": incident.get("created_at", datetime.now(timezone.utc).isoformat()),
    }

    # Create Jira incident
    try:
        jira_client = get_jira_client()
        jira_issue = jira_client.create_incident_from_alert(alert_payload)
        actions.append(f"Created Jira incident {jira_issue.key}")
        audit.log_action_taken("pagerduty", "create_jira_incident", jira_issue.key)
    except Exception as exc:
        logger.error("Failed to create Jira incident: %s", exc)
        audit.log_action_taken(
            "pagerduty", "create_jira_incident", str(exc), success=False,
        )
        return actions

    # Create Confluence PIR stub
    try:
        confluence = get_confluence_client()
        pir_page = confluence.create_pir_stub(
            incident_key=jira_issue.key,
            title=title,
            severity=severity,
        )
        actions.append(f"Created PIR stub page (id={pir_page.get('id', 'unknown')})")
        audit.log_action_taken("pagerduty", "create_pir_stub", jira_issue.key)
    except Exception as exc:
        logger.error("Failed to create PIR stub: %s", exc)
        audit.log_action_taken(
            "pagerduty", "create_pir_stub", str(exc), success=False,
        )

    return actions


async def _handle_pagerduty_incident_resolved(incident: dict[str, Any]) -> list[str]:
    """PagerDuty incident resolved -> transition Jira + update PIR."""
    actions: list[str] = []
    title = incident.get("title", "Unknown")

    # Find the corresponding Jira incident by searching for the PD incident ID
    pd_id = incident.get("id", "")
    if not pd_id:
        return actions

    try:
        jira_client = get_jira_client()
        jql = (
            f'project = CASINO AND issuetype = "Incident" '
            f'AND text ~ "{pd_id}"'
        )
        issues = jira_client._jira.search_issues(jql, maxResults=1)

        if issues:
            issue = issues[0]
            jira_client.transition_ticket(issue.key, TicketTransition.RESOLVED)
            actions.append(f"Resolved Jira incident {issue.key}")
            audit.log_action_taken("pagerduty", "resolve_jira_incident", issue.key)
    except Exception as exc:
        logger.error("Failed to resolve Jira incident for PD %s: %s", pd_id, exc)

    return actions


async def _trigger_deployment_pipeline(issue_key: str) -> bool:
    """Trigger a deployment pipeline via API call.

    Called when a Jira ticket with the 'auto-deploy' label is moved to Done.
    """
    pipeline_url = os.environ.get("DEPLOYMENT_PIPELINE_URL")
    pipeline_token = os.environ.get("DEPLOYMENT_PIPELINE_TOKEN")

    if not pipeline_url or not pipeline_token:
        logger.warning("Deployment pipeline not configured, skipping trigger")
        return False

    try:
        import httpx  # ty: ignore[unresolved-import]

        async with httpx.AsyncClient() as client:
            response = await client.post(
                pipeline_url,
                json={"ref": "main", "variables": {"JIRA_TICKET": issue_key}},
                headers={"PRIVATE-TOKEN": pipeline_token},
                timeout=30,
            )
            response.raise_for_status()
            logger.info("Triggered deployment pipeline for %s", issue_key)
            audit.log_action_taken("jira", "trigger_pipeline", issue_key)
            return True
    except Exception as exc:
        logger.error("Failed to trigger pipeline for %s: %s", issue_key, exc)
        audit.log_action_taken("jira", "trigger_pipeline", str(exc), success=False)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn  # ty: ignore[unresolved-import]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    uvicorn.run(
        "webhook_handler:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        reload=False,
        access_log=True,
    )
