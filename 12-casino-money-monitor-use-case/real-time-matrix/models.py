# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Real-Time Matrix (RTMX) — Domain Models
# Source: Production casino platform (sanitized)
# Chapter 12 - Casino Money Monitor
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Score trigger constants (Kafka message types)
# ---------------------------------------------------------------------------

BONUS_REQUESTS_THRESHOLD_REACHED = "apply-score-triggered-by-rtmx-bonus-requests"
COMPLAINTS_THRESHOLD_REACHED = "apply-score-triggered-by-rtmx-complaint"
CHASING_LOSSES_DEPOSITS_CRITERIA_MET = "apply-score-triggered-by-rtmx-chasing-losses-deposits"
CHASING_LOSSES_DEBITS_CRITERIA_MET = "apply-score-triggered-by-rtmx-chasing-losses-debits"


# ---------------------------------------------------------------------------
# Core event info (common fields on every Kafka message)
# ---------------------------------------------------------------------------


@dataclass
class CoreEventInfo:
    event_type: str
    user_id: int
    timestamp: datetime
    global_id: Optional[int] = None


@dataclass
class BoComment:
    """Back-office comment attached to a player account."""
    core_info: CoreEventInfo
    comment_type: str
    body: str


@dataclass
class TransactionDetails:
    currency: str
    total_amount: int   # minor units
    is_increment: bool  # True = credit, False = debit


@dataclass
class AccountsEvent:
    """Financial event emitted by the accounts system."""
    core_info: CoreEventInfo
    transaction_details: TransactionDetails


@dataclass
class RtmxScoreMessage:
    user_id: int
    matrix_event: str
    params: dict[str, Any]


@dataclass
class RgActionableCaseMessage:
    """Responsible Gaming actionable case — triggers a workflow."""
    global_id: Optional[int]
    user_id: Optional[int]
    brand_id: Optional[int]
    stream_tag: str
    description: str
    params: dict[str, Any]
    created_at: datetime


@dataclass
class MatrixScoreType:
    matrix_id: str
    id: int
    label: str
    calculate_on: str
    metric_period_days: int
    condition: str
