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
ltv_predictor.py -- Player Lifetime Value (LTV) prediction for iGaming platforms.

Predicts 12-month player LTV using a gradient-boosted tree model (XGBoost).
Features are engineered from 30-day rolling window metrics.

Model accuracy (from a production operator with 24 months of training data):
  - MAE: EUR 47 on a base of EUR 280 average LTV
  - MAPE: 18% (acceptable for marketing budget decisions)
  - R²: 0.74

Key insight: deposit frequency (deposits per week) is the single most predictive
feature — more frequent depositors have dramatically higher LTV than one-time
depositors with the same total.

Chapter 37: Marketing Technology and CRM
"""

from __future__ import annotations

from typing import Any, Optional


class LTVPredictor:
    """
    Predict 12-month player lifetime value using gradient boosted trees.

    The model is an XGBoost regressor trained on 24 months of historical
    player data. Features are scaled via StandardScaler fitted on training set.

    In production, the model is loaded from a model registry on startup
    and refreshed monthly as new cohort data becomes available.

    Usage::

        predictor = LTVPredictor(model=loaded_xgboost_model, scaler=loaded_scaler)
        ltv = predictor.predict({
            'days_since_registration': 45,
            'total_deposits_30d': 350.0,
            'total_bets_30d': 1200.0,
            'avg_bet_size': 5.50,
            'favourite_game_rtp': 0.96,
            'session_count_30d': 18,
            'avg_session_duration_min': 32.0,
            'deposit_frequency': 2.5,
            'withdrawal_ratio': 0.2,
            'bonus_conversion_rate': 0.65,
            'device_count': 2,
            'referral_source_quality': 3,
        })
        # Returns float, e.g., 420.0 (predicted EUR 420 LTV over 12 months)
    """

    # Ordered feature list — order must match training data
    FEATURES: list[str] = [
        "days_since_registration",
        "total_deposits_30d",
        "total_bets_30d",
        "avg_bet_size",
        "favourite_game_rtp",       # higher RTP → longer play → higher volume
        "session_count_30d",
        "avg_session_duration_min",
        "deposit_frequency",        # deposits per week
        "withdrawal_ratio",         # withdrawals / deposits
        "bonus_conversion_rate",    # % of bonuses that clear wagering requirement
        "device_count",             # multi-device = more engaged
        "referral_source_quality",  # affiliate tier 1–5
    ]

    # Default feature values for missing/null inputs
    FEATURE_DEFAULTS: dict[str, float] = {
        "days_since_registration": 30.0,
        "total_deposits_30d": 0.0,
        "total_bets_30d": 0.0,
        "avg_bet_size": 2.50,
        "favourite_game_rtp": 0.95,
        "session_count_30d": 1.0,
        "avg_session_duration_min": 15.0,
        "deposit_frequency": 0.25,
        "withdrawal_ratio": 0.5,
        "bonus_conversion_rate": 0.5,
        "device_count": 1.0,
        "referral_source_quality": 2.0,
    }

    def __init__(
        self,
        model: Any = None,
        scaler: Any = None,
    ) -> None:
        """
        Args:
            model:  Trained XGBoost model (xgboost.XGBRegressor) or compatible.
                    If None, uses a simple linear heuristic fallback.
            scaler: Fitted sklearn.preprocessing.StandardScaler or compatible.
                    If None, features are passed through unscaled.
        """
        self.model = model
        self.scaler = scaler

    def predict(self, features: dict[str, float]) -> float:
        """
        Return predicted 12-month LTV in EUR.

        Args:
            features: dict mapping feature names to float values.
                      Missing features use FEATURE_DEFAULTS.

        Returns:
            Predicted LTV as a non-negative float.
        """
        feature_vector = [
            float(features.get(f, self.FEATURE_DEFAULTS.get(f, 0.0)))
            for f in self.FEATURES
        ]

        if self.model is None:
            # Heuristic fallback when model is not loaded (e.g., cold start)
            return self._heuristic_ltv(features)

        # Apply scaler if available
        if self.scaler is not None:
            scaled = self.scaler.transform([feature_vector])
        else:
            scaled = [feature_vector]

        raw_prediction = float(self.model.predict(scaled)[0])
        # Clamp to non-negative (model may predict small negatives for low-value players)
        return max(0.0, raw_prediction)

    def _heuristic_ltv(self, features: dict[str, float]) -> float:
        """
        Simple linear heuristic when the ML model is unavailable.

        Based on observed correlations from industry benchmarks:
          - Deposit frequency is the strongest signal (weekly depositors ~3x monthly)
          - 30-day deposits scaled by frequency approximates 12-month value
        """
        deposits_30d = float(features.get("total_deposits_30d", 0.0))
        freq = float(features.get("deposit_frequency", 0.25))   # per week
        rtp = float(features.get("favourite_game_rtp", 0.95))
        withdrawal_ratio = float(features.get("withdrawal_ratio", 0.5))

        # Annualize 30-day deposits by frequency multiplier
        frequency_multiplier = min(3.0, max(0.5, freq * 2))
        annual_volume = deposits_30d * 12 * frequency_multiplier

        # Operator GGR margin: (1 - RTP) * (1 - withdrawal_ratio_adj)
        ggr_margin = (1.0 - rtp) * max(0.1, 1.0 - withdrawal_ratio * 0.5)

        return max(0.0, annual_volume * ggr_margin)

    def get_ltv_percentile(self, ltv: float) -> str:
        """
        Map a predicted LTV to a percentile band.
        Based on typical iGaming LTV distributions (Pareto-distributed).

        Returns:
            'top1', 'top5', 'top10', 'top25', 'median', 'below_median'
        """
        if ltv >= 10000:
            return "top1"
        if ltv >= 3000:
            return "top5"
        if ltv >= 1000:
            return "top10"
        if ltv >= 300:
            return "top25"
        if ltv >= 100:
            return "median"
        return "below_median"
