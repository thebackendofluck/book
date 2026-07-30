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
Notification service data models.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class NotificationType(str, Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    PUSH = "PUSH"
    IN_APP = "IN_APP"


class NotificationStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


class Template(BaseModel):
    template_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    channel: NotificationType
    subject: str | None = None        # Email only
    body: str                         # Jinja2-style: {{ player_name }}, {{ amount }}
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    active: bool = True

    model_config = {"use_enum_values": True}


class NotificationRequest(BaseModel):
    player_id: str
    channel: NotificationType
    template_name: str | None = None
    subject: str | None = None
    body: str | None = None           # Used when template_name is absent
    variables: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Notification(BaseModel):
    notification_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    player_id: str
    channel: NotificationType
    template_id: str | None = None
    subject: str | None = None
    rendered_body: str
    status: NotificationStatus = NotificationStatus.PENDING
    error_message: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": True}
