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
personalisation_engine.py -- Real-time offer personalisation for iGaming.

Scores available offers against a player's profile and real-time session
context, returning a ranked list of the most relevant promotions, game
recommendations, and UI treatments.

Architecture:
  1. Feature extraction from profile + session context
  2. Parallel scoring: TensorFlow model + business rules filter
  3. Deduplication by category (one offer per category in response)
  4. Return top-N scored and filtered offers

The model is a TensorFlow SavedModel trained on historical offer
acceptance rates segmented by player profile features.

Chapter 37: Marketing Technology and CRM
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Optional

from customer_profile import CustomerProfile


if TYPE_CHECKING:
    # Avoid importing heavy deps at module level for environments without TF
    pass


class PersonalisationEngine:
    """
    Real-time personalisation engine for offers, bonuses, and game recommendations.

    Args:
        model_path:   Path to TensorFlow SavedModel directory.
                      If None, uses a rule-based scoring fallback.
        redis_client: Async Redis client for caching candidate offers.
    """

    # Feature dimension expected by the TF model
    PLAYER_FEATURE_DIM = 7
    OFFER_FEATURE_DIM = 5

    def __init__(self, model_path: Optional[str] = None, redis_client: Any = None) -> None:
        self.model_path = model_path
        self.redis = redis_client
        self._model: Any = None

        if model_path is not None:
            self._load_model(model_path)

    def _load_model(self, path: str) -> None:
        """Load TensorFlow SavedModel. Called lazily on first use if not at init."""
        try:
            import tensorflow as tf  # type: ignore[import-untyped]  # noqa: PLC0415
            self._model = tf.saved_model.load(path)
        except ImportError:
            # TensorFlow not available — fall back to heuristic scoring
            self._model = None

    async def rank_offers(
        self,
        profile: CustomerProfile,
        context: dict[str, Any],
        max_offers: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Score and rank offers for a player given their profile and session context.

        Args:
            profile:    Enriched customer profile.
            context:    Real-time session context:
                        {'device': 'mobile', 'hour': 20, 'game_id': 'book_of_dead', ...}
            max_offers: Maximum number of offers to return.

        Returns:
            List of offer dicts with 'score' field added, sorted by score descending,
            deduplicated by category, limited to max_offers.
        """
        # 1. Extract player features
        player_features = self.extract_features(profile, context)

        # 2. Get candidate offers (from Redis cache or DB fallback)
        candidates = await self._get_candidate_offers(profile)

        if not candidates:
            return []

        # 3. Score each candidate
        scored: list[dict[str, Any]] = []
        for offer in candidates:
            score = self._score_offer(player_features, offer)
            scored.append({**offer, "score": score})

        # 4. Apply business rules filter
        filtered = self._apply_rules(scored, profile)

        # 5. Sort by score descending
        filtered.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)

        # 6. Deduplicate by category and return top N
        return self._deduplicate(filtered, max_offers)

    def extract_features(
        self,
        profile: CustomerProfile,
        context: dict[str, Any],
    ) -> list[float]:
        """
        Extract a fixed-length feature vector from player profile + session context.

        Feature order must match the model's expected input:
          [total_deposits, total_bets, ltv_estimate, churn_probability,
           is_mobile, time_of_day_sin, net_gaming_revenue]
        """
        return [
            float(profile.total_deposits),
            float(profile.total_bets),
            float(profile.ltv_estimate),
            float(profile.churn_probability),
            1.0 if context.get("device") == "mobile" else 0.0,
            self._time_of_day_encoding(int(context.get("hour", 12))),
            float(profile.net_gaming_revenue),
        ]

    def _offer_features(self, offer: dict[str, Any]) -> list[float]:
        """
        Extract fixed-length feature vector from an offer dict.

        Feature order: [offer_value, wagering_req, category_encoded,
                        vip_only_flag, rg_restricted_flag]
        """
        category_map = {"deposit_bonus": 1, "free_spins": 2, "cashback": 3, "game_rec": 4}
        return [
            float(offer.get("value_eur", 0)),
            float(offer.get("wagering_requirement", 1)),
            float(category_map.get(str(offer.get("category", "")), 0)),
            1.0 if offer.get("vip_only") else 0.0,
            1.0 if offer.get("rg_restricted") else 0.0,
        ]

    def _score_offer(
        self,
        player_features: list[float],
        offer: dict[str, Any],
    ) -> float:
        """Score an offer using the TF model or heuristic fallback."""
        if self._model is not None:
            try:
                import tensorflow as tf  # type: ignore[import-untyped]  # noqa: PLC0415
                offer_feats = self._offer_features(offer)
                combined = tf.constant([player_features + offer_feats], dtype=tf.float32)
                return float(self._model(combined).numpy()[0][0])
            except Exception:  # noqa: BLE001
                pass  # Fall through to heuristic

        # Heuristic: score = offer value / wagering_requirement * churn_weight
        churn_weight = 1.0 + player_features[3]  # boost for high-churn players
        value = float(offer.get("value_eur", 0))
        wagering = max(1.0, float(offer.get("wagering_requirement", 35)))
        return (value / wagering) * churn_weight

    def _apply_rules(
        self,
        offers: list[dict[str, Any]],
        profile: CustomerProfile,
    ) -> list[dict[str, Any]]:
        """
        Filter offers based on business rules:
          - No marketing consent → exclude all promotional offers
          - High RG risk → exclude all bonus offers
          - VIP-only offers → require is_vip
          - Jurisdiction restrictions → match player's jurisdiction
        """
        if profile.should_suppress_marketing:
            # Player opted out of marketing or is high RG risk — no bonuses
            return []

        result = []
        for offer in offers:
            # Skip explicitly excluded offers
            if offer.get("excluded"):
                continue

            # VIP-only check
            if offer.get("vip_only") and not profile.is_vip:
                continue

            # Jurisdiction check
            offer_jurisdiction = offer.get("jurisdiction", "ALL")
            if offer_jurisdiction != "ALL":
                player_segs = set(profile.segments)
                if offer_jurisdiction not in player_segs:
                    continue

            result.append(offer)

        return result

    async def _get_candidate_offers(
        self,
        profile: CustomerProfile,
    ) -> list[dict[str, Any]]:
        """
        Retrieve candidate offers from Redis cache.
        Falls back to an empty list if Redis is unavailable.
        """
        if self.redis is None:
            return []

        try:
            import json  # noqa: PLC0415
            raw = await self.redis.get("offers:active")
            if raw:
                return json.loads(raw)  # type: ignore[no-any-return]
        except Exception:  # noqa: BLE001
            pass

        return []

    @staticmethod
    def _time_of_day_encoding(hour: int) -> float:
        """Cyclical encoding of hour: sin(2π * hour / 24). Range: -1.0 to 1.0."""
        return math.sin(2 * math.pi * hour / 24)

    @staticmethod
    def _deduplicate(offers: list[dict[str, Any]], max_n: int) -> list[dict[str, Any]]:
        """
        Return top-N offers with at most one per category.
        Preserves score ordering (first occurrence wins for each category).
        """
        seen_categories: set[str] = set()
        result: list[dict[str, Any]] = []

        for offer in offers:
            cat = str(offer.get("category", "general"))
            if cat not in seen_categories:
                seen_categories.add(cat)
                result.append(offer)
            if len(result) >= max_n:
                break

        return result
