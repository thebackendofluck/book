# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Notification Service — FastAPI application.

Endpoints:
  POST /notify                      Send a notification (ad-hoc or template-based)
  GET  /templates                   List all active templates
  POST /templates                   Create a new template
  GET  /history/{player_id}         Delivery history for a player
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, status

from dispatcher import build_notification_from_template, dispatch, render_template
from models import (
    Notification,
    NotificationRequest,
    NotificationStatus,
    NotificationType,
    Template,
)

app = FastAPI(
    title="Notification Service",
    description="Multi-channel notification delivery for iGaming: EMAIL, SMS, PUSH, IN_APP.",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# In-memory stores (replace with DB / Redis in production)
# ---------------------------------------------------------------------------
_templates: dict[str, Template] = {}
_history: list[Notification] = []


def _find_template_by_name(name: str) -> Template | None:
    for t in _templates.values():
        if t.name == name and t.active:
            return t
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/notify", response_model=Notification, status_code=status.HTTP_201_CREATED)
def send_notification(req: NotificationRequest) -> Notification:
    """
    Dispatch a notification to a player.

    Provide either ``template_name`` (to use a stored template) or a raw
    ``body`` (and optionally ``subject`` for EMAIL).  ``variables`` are
    interpolated into the template body using ``${key}`` syntax.
    """
    if req.template_name:
        tmpl = _find_template_by_name(req.template_name)
        if tmpl is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template '{req.template_name}' not found or inactive.",
            )
        notification = build_notification_from_template(
            tmpl, req.player_id, req.variables, req.metadata
        )
    elif req.body:
        rendered = render_template(req.body, req.variables)
        notification = Notification(
            player_id=req.player_id,
            channel=NotificationType(req.channel),
            subject=req.subject,
            rendered_body=rendered,
            metadata=req.metadata,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either 'template_name' or 'body'.",
        )

    dispatched = dispatch(notification)
    _history.append(dispatched)
    return dispatched


@app.get("/templates", response_model=list[Template])
def list_templates(active_only: bool = True) -> list[Template]:
    """List all templates, optionally filtering to active ones only."""
    templates = list(_templates.values())
    if active_only:
        templates = [t for t in templates if t.active]
    return templates


@app.post("/templates", response_model=Template, status_code=status.HTTP_201_CREATED)
def create_template(template: Template) -> Template:
    """Register a new notification template."""
    _templates[template.template_id] = template
    return template


@app.get("/history/{player_id}", response_model=list[Notification])
def get_player_history(player_id: str, limit: int = 50) -> list[Notification]:
    """Return the most recent notifications sent to a player."""
    player_notifs = [n for n in _history if n.player_id == player_id]
    return player_notifs[-limit:]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "templates_count": len(_templates),
        "history_count": len(_history),
    }
