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
attribution_engine.py -- Multi-touch attribution for iGaming affiliate tracking.

Computes five attribution models simultaneously:
  1. First-touch: 100% credit to the first touchpoint
  2. Last-touch:  100% credit to the last touchpoint
  3. Linear:      Equal credit to all touchpoints
  4. Time-decay:  Exponential decay (half-life = 7 days); later touches get more credit
  5. Position-based (U-shaped): 40% first, 40% last, 20% shared middle

Use case: when a player deposits, all channels that touched that player in the
conversion window (typically 30 days) receive attribution credit.
Operators use this to reconcile affiliate commission and calculate channel ROI.

Chapter 37: Marketing Technology and CRM
"""

from __future__ import annotations

import math
from datetime import datetime


class AttributionEngine:
    """
    Multi-touch attribution calculator.

    Input:
        touchpoints: list of dicts, each with:
            - channel (str): 'affiliate', 'email', 'paid_search', 'direct', etc.
            - timestamp (datetime): UTC time of the touchpoint
        conversion_value (float): monetary value of the conversion (e.g., FTD amount)

    Output:
        dict of model_name -> dict of channel -> attributed_value

    Example::

        engine = AttributionEngine()
        result = engine.attribute(
            touchpoints=[
                {'channel': 'affiliate', 'timestamp': datetime(2026, 3, 1)},
                {'channel': 'paid_search', 'timestamp': datetime(2026, 3, 3)},
                {'channel': 'email', 'timestamp': datetime(2026, 3, 5)},
                {'channel': 'direct', 'timestamp': datetime(2026, 3, 7)},
            ],
            conversion_value=200.0
        )
        # result['first_touch'] == {'affiliate': 200.0}
        # result['last_touch']  == {'direct': 200.0}
        # result['linear']      == {'affiliate': 50.0, 'paid_search': 50.0, ...}
    """

    def attribute(
        self,
        touchpoints: list[dict[str, object]],
        conversion_value: float,
    ) -> dict[str, dict[str, float]]:
        """Compute attribution across all five models simultaneously."""
        n = len(touchpoints)
        if n == 0:
            return {}

        results: dict[str, dict[str, float]] = {}

        # 1. First-touch: 100% to the first channel
        first_channel = str(touchpoints[0]["channel"])
        results["first_touch"] = {first_channel: conversion_value}

        # 2. Last-touch: 100% to the last channel
        last_channel = str(touchpoints[-1]["channel"])
        results["last_touch"] = {last_channel: conversion_value}

        # 3. Linear: equal split across all touchpoints
        share = conversion_value / n
        linear: dict[str, float] = {}
        for tp in touchpoints:
            ch = str(tp["channel"])
            linear[ch] = linear.get(ch, 0.0) + share
        results["linear"] = linear

        # 4. Time-decay: exponential decay, half-life = 7 days
        results["time_decay"] = self._time_decay(
            touchpoints, conversion_value, half_life_days=7
        )

        # 5. Position-based (U-shaped): 40% first, 40% last, 20% middle
        results["position_based"] = self._position_based(touchpoints, conversion_value)

        return results

    # ---------------------------------------------------------------------------
    # Private model implementations
    # ---------------------------------------------------------------------------

    def _time_decay(
        self,
        touchpoints: list[dict[str, object]],
        value: float,
        half_life_days: float,
    ) -> dict[str, float]:
        """
        Time-decay attribution.

        Later touchpoints receive proportionally more credit.
        Weight of each touchpoint: exp(-lambda * age_in_seconds)
        where lambda = ln(2) / (half_life_days * 86400)
        """
        conversion_time = touchpoints[-1]["timestamp"]
        assert isinstance(conversion_time, datetime)

        decay_constant = math.log(2) / (half_life_days * 86400)

        weights: list[float] = []
        for tp in touchpoints:
            ts = tp["timestamp"]
            assert isinstance(ts, datetime)
            age_seconds = (conversion_time - ts).total_seconds()
            # age_seconds should be >= 0; handle edge cases
            weights.append(math.exp(-decay_constant * max(0.0, age_seconds)))

        total_weight = sum(weights)
        if total_weight == 0:
            # Fallback to linear if all weights are zero
            return {str(tp["channel"]): value / len(touchpoints) for tp in touchpoints}

        result: dict[str, float] = {}
        for tp, w in zip(touchpoints, weights):
            ch = str(tp["channel"])
            result[ch] = result.get(ch, 0.0) + value * (w / total_weight)

        return result

    def _position_based(
        self,
        touchpoints: list[dict[str, object]],
        value: float,
    ) -> dict[str, float]:
        """
        Position-based (U-shaped / 40/20/40) attribution.

        - First touchpoint: 40% of conversion value
        - Last touchpoint: 40% of conversion value
        - Middle touchpoints: 20% split equally

        When there is only 1 touchpoint: 100%.
        When there are 2 touchpoints: 50% each.
        """
        n = len(touchpoints)
        result: dict[str, float] = {}

        for i, tp in enumerate(touchpoints):
            ch = str(tp["channel"])

            if n == 1:
                credit = value
            elif n == 2:
                credit = value * 0.5
            elif i == 0:
                credit = value * 0.4
            elif i == n - 1:
                credit = value * 0.4
            else:
                # Middle: share 20% equally
                middle_count = n - 2
                credit = value * 0.2 / middle_count

            result[ch] = result.get(ch, 0.0) + credit

        return result
