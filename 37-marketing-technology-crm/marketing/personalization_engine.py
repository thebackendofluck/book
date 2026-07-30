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
Real-Time Personalization Engine for iGaming
==============================================
Chapter 9: Marketing Technology and CRM Systems

ML-powered personalization engine providing:
- Real-time offer scoring using TensorFlow and sklearn models
- Customer feature extraction from CDP profiles
- Contextual feature engineering (device, time, location)
- Business rule application and offer filtering
- Offer performance tracking for model improvement

Dependencies:
    pip install tensorflow scikit-learn joblib redis
"""

# Real-time personalization engine with ML models
import tensorflow as tf  # ty:ignore[unresolved-import]
import numpy as np
from sklearn.preprocessing import StandardScaler
import joblib
from typing import Any, Dict, List, Optional, Tuple
import redis.asyncio as redis
import asyncio
from dataclasses import dataclass
import json
import time
import logging
from datetime import datetime

@dataclass
class PersonalizationRequest:
    customer_id: str
    context: Dict[str, Any]  # page, device, time, location
    available_offers: List[Dict]
    real_time_signals: Dict[str, float]

@dataclass
class PersonalizationResult:
    offers: List[Dict]
    confidence_scores: List[float]
    model_version: str
    explanation: str
    latency_ms: int

class RealTimePersonalizationEngine:
    def __init__(self, redis_client: redis.Redis, model_path: str):
        self.redis = redis_client
        self.model_path = model_path
        self.models = {}
        self.scalers = {}
        self.feature_cache = {}
        self.logger = logging.getLogger(__name__)

    async def initialize(self):
        """Load ML models and initialize components"""
        # Load recommendation model
        self.models['recommendation'] = tf.keras.models.load_model(
            f"{self.model_path}/recommendation_model.h5"
        )

        # Load offer propensity model
        self.models['propensity'] = joblib.load(
            f"{self.model_path}/propensity_model.pkl"
        )

        # Load feature scalers
        self.scalers['recommendation'] = joblib.load(
            f"{self.model_path}/recommendation_scaler.pkl"
        )

        # Load offer embeddings
        with open(f"{self.model_path}/offer_embeddings.json", 'r') as f:
            self.offer_embeddings = json.load(f)

        self.logger.info("Personalization engine initialized")

    async def personalize_offers(
        self,
        request: PersonalizationRequest
    ) -> PersonalizationResult:
        """Generate personalized offers in real-time"""
        start_time = time.time()

        try:
            # Get customer features
            customer_features = await self._get_customer_features(request.customer_id)

            # Get contextual features
            context_features = self._extract_context_features(request.context)

            # Get real-time behavioral signals
            behavioral_features = await self._get_behavioral_signals(request.customer_id)

            # Combine all features
            feature_vector = self._combine_features(  # ty:ignore[unresolved-attribute]
                customer_features,
                context_features,
                behavioral_features
            )

            # Generate offer scores
            offer_scores = await self._score_offers(
                feature_vector,
                request.available_offers
            )

            # Apply business rules and constraints
            filtered_offers = self._apply_business_rules(
                offer_scores,
                request.customer_id,
                request.context
            )

            # Rank and select top offers
            selected_offers = self._rank_offers(filtered_offers)  # ty:ignore[unresolved-attribute]

            # Generate explanation
            explanation = self._generate_explanation(
                selected_offers,
                customer_features
            )

            latency_ms = int((time.time() - start_time) * 1000)

            return PersonalizationResult(
                offers=selected_offers,
                confidence_scores=[offer['confidence'] for offer in selected_offers],
                model_version='v2.1.0',
                explanation=explanation,
                latency_ms=latency_ms
            )

        except Exception as e:
            self.logger.error(f"Personalization failed: {e}")
            # Fallback to default offers
            return self._get_fallback_offers(request.available_offers)  # ty:ignore[unresolved-attribute]

    async def _get_customer_features(self, customer_id: str) -> np.ndarray:
        """Extract customer features from profile and history"""
        # Check cache first
        cache_key = f"customer_features:{customer_id}"
        cached_features = await self.redis.get(cache_key)

        if cached_features:
            return np.array(json.loads(cached_features))

        # Get customer profile from CDP
        profile = await self._fetch_customer_profile(customer_id)  # ty:ignore[unresolved-attribute]

        if not profile:
            return self._get_default_features()  # ty:ignore[unresolved-attribute]

        # Extract features
        features = []

        # Demographic features
        features.extend([
            self._encode_country(profile.get('country', 'unknown')),  # ty:ignore[unresolved-attribute]
            self._encode_age_group(profile.get('date_of_birth')),  # ty:ignore[unresolved-attribute]
            profile.get('vip_level', 0),
        ])

        # Behavioral features
        features.extend([
            float(profile.get('lifetime_value', 0)),
            float(profile.get('total_deposits', 0)),
            float(profile.get('total_withdrawals', 0)),
            self._calculate_activity_score(profile),  # ty:ignore[unresolved-attribute]
            self._calculate_risk_score(profile),  # ty:ignore[unresolved-attribute]
        ])

        # Preference features
        preferences = profile.get('preferences', {})
        features.extend([
            float(preferences.get('max_bet_size', 0)),
            float(preferences.get('preferred_game_types', [])),
            float(preferences.get('communication_channel', 0)),
        ])

        # Temporal features
        features.extend([
            self._calculate_days_since_registration(profile),  # ty:ignore[unresolved-attribute]
            self._calculate_session_frequency(profile),  # ty:ignore[unresolved-attribute]
            self._calculate_avg_session_duration(profile),  # ty:ignore[unresolved-attribute]
        ])

        feature_array = np.array(features).reshape(1, -1)

        # Scale features
        scaled_features = self.scalers['recommendation'].transform(feature_array)

        # Cache for 5 minutes
        await self.redis.setex(
            cache_key,
            300,
            json.dumps(scaled_features.tolist())
        )

        return scaled_features

    def _extract_context_features(self, context: Dict) -> np.ndarray:
        """Extract contextual features"""
        features = []

        # Time-based features
        current_hour = datetime.now().hour
        features.extend([
            np.sin(2 * np.pi * current_hour / 24),  # Hour sin
            np.cos(2 * np.pi * current_hour / 24),  # Hour cos
            self._encode_day_of_week(datetime.now().weekday()),  # ty:ignore[unresolved-attribute]
            self._encode_month(datetime.now().month),  # ty:ignore[unresolved-attribute]
        ])

        # Device features
        device_type = context.get('device_type', 'desktop')
        features.extend([
            self._encode_device_type(device_type),  # ty:ignore[unresolved-attribute]
            1.0 if context.get('is_mobile', False) else 0.0,
            1.0 if context.get('is_tablet', False) else 0.0,
        ])

        # Page/location features
        page_category = context.get('page_category', 'unknown')
        features.extend([
            self._encode_page_category(page_category),  # ty:ignore[unresolved-attribute]
            1.0 if context.get('is_casino_page', False) else 0.0,
            1.0 if context.get('is_sports_page', False) else 0.0,
            1.0 if context.get('is_promotions_page', False) else 0.0,
        ])

        return np.array(features).reshape(1, -1)

    async def _get_behavioral_signals(self, customer_id: str) -> np.ndarray:
        """Get real-time behavioral signals"""
        signals = []

        # Recent activity signals
        recent_events = await self._get_recent_events(customer_id, minutes=30)  # ty:ignore[unresolved-attribute]

        # Calculate click-through rate for recent offers
        offer_ctr = self._calculate_offer_ctr(recent_events)  # ty:ignore[unresolved-attribute]
        signals.append(offer_ctr)

        # Calculate deposit intent score
        deposit_intent = self._calculate_deposit_intent(recent_events)  # ty:ignore[unresolved-attribute]
        signals.append(deposit_intent)

        # Calculate churn risk score
        churn_risk = await self._calculate_churn_risk(customer_id)  # ty:ignore[unresolved-attribute]
        signals.append(churn_risk)

        # Calculate bonus sensitivity
        bonus_sensitivity = await self._calculate_bonus_sensitivity(customer_id)  # ty:ignore[unresolved-attribute]
        signals.append(bonus_sensitivity)

        # Real-time engagement score
        engagement_score = self._calculate_engagement_score(recent_events)  # ty:ignore[unresolved-attribute]
        signals.append(engagement_score)

        return np.array(signals).reshape(1, -1)

    async def _score_offers(
        self,
        feature_vector: np.ndarray,
        available_offers: List[Dict]
    ) -> List[Dict]:
        """Score each offer using ML models"""
        scored_offers = []

        for offer in available_offers:
            # Get offer embedding
            offer_embedding = self.offer_embeddings.get(offer['id'],
                self._get_default_offer_embedding())  # ty:ignore[unresolved-attribute]

            # Combine features
            combined_features = np.concatenate([
                feature_vector.flatten(),
                np.array(offer_embedding)
            ]).reshape(1, -1)

            # Get propensity score
            propensity_score = self.models['propensity'].predict_proba(
                combined_features
            )[0][1]

            # Get recommendation score
            recommendation_score = self.models['recommendation'].predict(
                combined_features
            )[0][0]

            # Combine scores with weights
            final_score = (
                0.6 * propensity_score +
                0.4 * recommendation_score
            )

            scored_offers.append({
                **offer,
                'score': final_score,
                'confidence': propensity_score,
                'propensity_score': propensity_score,
                'recommendation_score': recommendation_score
            })

        return scored_offers

    def _apply_business_rules(
        self,
        offers: List[Dict],
        customer_id: str,
        context: Dict
    ) -> List[Dict]:
        """Apply business rules and constraints"""
        filtered_offers = []

        for offer in offers:
            # Skip if offer doesn't meet minimum score
            if offer['score'] < 0.3:
                continue

            # Check jurisdiction restrictions
            if not self._is_offer_allowed_in_jurisdiction(offer, context.get('country')):  # ty:ignore[unresolved-attribute]
                continue

            # Check customer eligibility
            if not self._is_customer_eligible_for_offer(offer, customer_id):  # ty:ignore[unresolved-attribute]
                continue

            # Check offer frequency limits
            if self._has_customer_reached_offer_limit(customer_id, offer['id']):  # ty:ignore[unresolved-attribute]
                continue

            # Apply dynamic pricing based on customer value
            offer = self._apply_dynamic_pricing(offer, customer_id)  # ty:ignore[unresolved-attribute]

            filtered_offers.append(offer)

        return filtered_offers

    def _generate_explanation(
        self,
        selected_offers: List[Dict],
        customer_features: np.ndarray
    ) -> str:
        """Generate human-readable explanation for recommendations"""
        if not selected_offers:
            return "No personalized offers available at this time."

        top_offer = selected_offers[0]

        explanations = []

        if top_offer['confidence'] > 0.8:
            explanations.append("Based on your gaming preferences")

        if 'vip' in top_offer.get('target_segments', []):
            explanations.append("Exclusive offer for VIP players")

        if top_offer.get('score', 0) > 0.7:
            explanations.append("High match with your interests")

        return "Selected for you because: " + ", ".join(explanations)

    async def track_offer_performance(
        self,
        customer_id: str,
        offer_id: str,
        action: str
    ):
        """Track offer performance for model improvement"""
        timestamp = datetime.now().isoformat()

        # Store interaction
        await self.redis.zadd(
            f"offer_interactions:{customer_id}",
            {f"{offer_id}:{action}:{timestamp}": int(time.time())}
        )

        # Update offer performance metrics
        if action == 'accepted':
            await self.redis.hincrby(f"offer_metrics:{offer_id}", 'acceptances', 1)  # ty:ignore[invalid-await]
        elif action == 'viewed':
            await self.redis.hincrby(f"offer_metrics:{offer_id}", 'views', 1)  # ty:ignore[invalid-await]

        # Trigger model retraining if enough data
        total_interactions = await self.redis.zcard(f"offer_interactions:{customer_id}")
        if total_interactions > 1000:
            await self._trigger_model_update(customer_id)  # ty:ignore[unresolved-attribute]
