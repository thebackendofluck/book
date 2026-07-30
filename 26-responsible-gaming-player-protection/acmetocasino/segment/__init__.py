# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
segment — Player Segmentation Domain
======================================

Classifies players into tiers and product segments, checks their eligibility
for games / bonuses / suppliers, and maintains a running model of each
player's value and risk profile.

Packages
--------
player_segment
    ``PlayerSegment`` — the canonical segment model carrying tier, lifetime
    value metrics, risk category, and product preferences.

eligibility
    ``EligibilityChecker`` — answers whether a player may play a game,
    receive a bonus, or access a supplier given their current segment and
    jurisdiction.

segment_service
    ``SegmentService`` — retrieves and updates player segments; recalculates
    tiers based on configurable brand thresholds.

event_sink
    ``SegmentEventSink`` — listens to platform activity events (deposits,
    withdrawals, round completions, session endings) and drives tier
    recalculation.

Typical import pattern::

    from acmetocasino.segment.player_segment import PlayerSegment, SegmentTier
    from acmetocasino.segment.eligibility import EligibilityChecker, EligibilityResult
    from acmetocasino.segment.segment_service import SegmentService
    from acmetocasino.segment.event_sink import SegmentEventSink
"""

from __future__ import annotations

from acmetocasino.segment.player_segment import (
    PlayerSegment,
    RiskCategory,
    SegmentTier,
)
from acmetocasino.segment.eligibility import EligibilityChecker, EligibilityResult
from acmetocasino.segment.segment_service import SegmentService
from acmetocasino.segment.event_sink import SegmentEventSink

__all__ = [
    "EligibilityChecker",
    "EligibilityResult",
    "PlayerSegment",
    "RiskCategory",
    "SegmentEventSink",
    "SegmentService",
    "SegmentTier",
]
