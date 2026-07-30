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
# Complaints Stream
# Source: Production casino platform (sanitized)
# Chapter 12 - Casino Money Monitor
#
# Detects when a player files multiple complaints within a configurable
# time window (default: 30 days) and emits a responsible-gaming
# actionable case message when the threshold is crossed.
# =============================================================================

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from models import (
    BoComment,
    COMPLAINTS_THRESHOLD_REACHED,
    RgActionableCaseMessage,
    RtmxScoreMessage,
)

logger = logging.getLogger(__name__)

COMPLAINTS_WINDOW_DAYS: int = int(os.getenv("COMPLAINTS_WINDOW", "30"))
AUDIT_SINK_SWITCH: bool = os.getenv("COMPLAINTS_AUDIT_SINK_SWITCH", "true") == "true"
INTERACTIONS_SINK_SWITCH: bool = os.getenv("COMPLAINTS_INTERACTIONS_SINK_SWITCH", "false") == "true"

COMPLAINT_TYPE = "player referred to complaints"


class ComplaintsProcessor:
    """
    Processes BoComment events from Kafka and maintains a sliding-window
    count of complaint filings per player (keyed by global_id).

    When the count of complaints within the window crosses the configured
    matrix threshold, an RgActionableCaseMessage is emitted.

    State is held in memory; for a production Kafka Streams deployment
    this would be backed by a RocksDB state store.
    """

    def __init__(self, matrix_scorer=None) -> None:
        # { global_id: [timestamp, ...] } — timestamps of complaint events
        self._complaint_windows: dict[int, list[datetime]] = defaultdict(list)
        self._matrix_scorer = matrix_scorer

    def _window_duration(self) -> timedelta:
        return timedelta(days=COMPLAINTS_WINDOW_DAYS)

    def _is_complaint(self, comment: BoComment) -> bool:
        return comment.comment_type.lower() == COMPLAINT_TYPE

    def _trim_window(self, global_id: int, reference_time: datetime) -> None:
        cutoff = reference_time - self._window_duration()
        self._complaint_windows[global_id] = [
            ts for ts in self._complaint_windows[global_id] if ts >= cutoff
        ]

    def _threshold_reached(self, count: int) -> bool:
        """
        In production, the matrix scorer evaluates a named rule and
        returns the matching score IDs. Here we apply a simple default
        threshold of 1 (any complaint filing triggers an alert).
        """
        if self._matrix_scorer is not None:
            matches = self._matrix_scorer.check_scores(
                COMPLAINTS_THRESHOLD_REACHED,
                {"filedComplaintsCount": count},
            )
            return bool(matches)
        return count >= 1

    def process(self, comment: BoComment) -> Optional[RgActionableCaseMessage]:
        if not self._is_complaint(comment):
            return None
        if comment.core_info.global_id is None:
            return None
        if not AUDIT_SINK_SWITCH:
            return None

        global_id = comment.core_info.global_id
        now = comment.core_info.timestamp
        self._trim_window(global_id, now)
        self._complaint_windows[global_id].append(now)
        count = len(self._complaint_windows[global_id])

        logger.debug("Complaint count for global_id=%s: %d", global_id, count)

        if not self._threshold_reached(count):
            return None

        logger.debug("Actionable complaint case for global_id=%s count=%d", global_id, count)

        return RgActionableCaseMessage(
            global_id=global_id,
            user_id=comment.core_info.user_id,
            brand_id=None,
            stream_tag="complaints-stream",
            description=f"User has filed a complaint for {count} time(s)",
            params={
                "complaintsWindow": COMPLAINTS_WINDOW_DAYS,
                "matrixEvent": COMPLAINTS_THRESHOLD_REACHED,
                "matrixData": {
                    "filedComplaintsCount": count,
                },
            },
            created_at=datetime.now(timezone.utc),
        )
