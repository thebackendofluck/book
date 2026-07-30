# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Internal Alerts — Domain Models
# Source: Production casino platform (sanitized)
# Chapter 35 - Incident Management
# =============================================================================

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AlertStatus(str, Enum):
    PENDING = "Pending"
    SENT = "Sent"
    ERROR = "Error"


class AlertMessage(BaseModel):
    """Kafka message payload consumed from the alerts topic."""
    alert_type: str
    user_id: Optional[int] = None
    brand_id: Optional[int] = None
    params: Optional[dict[str, Any]] = None


class Alert(BaseModel):
    """Persistent alert record stored in the database."""
    id: Optional[int] = None
    alert_type: str
    user_id: Optional[int] = None
    brand_id: Optional[int] = None
    params: Optional[dict[str, Any]] = None
    status: AlertStatus = AlertStatus.PENDING
    last_update: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_message(cls, msg: AlertMessage) -> "Alert":
        return cls(
            alert_type=msg.alert_type,
            user_id=msg.user_id,
            brand_id=msg.brand_id,
            params=msg.params,
            status=AlertStatus.PENDING,
        )


class AlertType(BaseModel):
    """Configuration row defining how an alert type should be handled."""
    name: str
    recipient: str          # logical recipient name (e.g., "compliance-team")
    template_name: str
    jurisdiction_id: Optional[str] = None
    max_frequency_seconds: int      # minimum gap between identical alerts


class EmailAddress(BaseModel):
    name: str
    jurisdiction_id: Optional[str]
    address: str


class MailerAlertParams(BaseModel):
    recipient: str
    user_id: Optional[int]
    brand_id: Optional[int]
    params: Optional[dict[str, Any]]


class MailerRequest(BaseModel):
    template_name: str
    params: MailerAlertParams


class MailerResponse(BaseModel):
    status: bool
    message: Optional[str] = None
