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
Affiliate Tracking and Attribution System for iGaming
======================================================
Chapter 9: Marketing Technology and CRM Systems

Comprehensive affiliate tracking with multi-touch attribution providing:
- Click tracking with UTM parameter parsing and geo/device enrichment
- Multi-touch attribution models: first-touch, last-touch, linear, time-decay, position-based
- Affiliate performance reporting with ROI analysis
- Fraud detection using IP patterns, click spam detection, and bot traffic identification

Dependencies:
    pip install redis asyncpg aiohttp user-agents
"""

# Comprehensive affiliate tracking with multi-touch attribution
import hashlib
import urllib.parse
from typing import Dict, List, Optional, Tuple
import redis.asyncio as redis
import asyncpg  # ty:ignore[unresolved-import]
from datetime import datetime, timedelta
import json
import uuid
import logging
from dataclasses import dataclass

@dataclass
class AffiliateClick:
    click_id: str
    affiliate_id: str
    campaign_id: str
    customer_id: Optional[str]
    timestamp: datetime
    ip_address: str
    user_agent: str
    landing_page: str
    referrer: str
    utm_source: str
    utm_medium: str
    utm_campaign: str
    creative_id: Optional[str]
    sub_id: Optional[str]
    cost_per_click: float
    country: str
    device_type: str
    browser: str
    os: str

@dataclass
class AttributionResult:
    customer_journey: List[Dict]
    attributed_touchpoints: List[Dict]
    conversion_value: float
    attribution_model: str
    confidence_score: float

class AffiliateTrackingSystem:
    def __init__(self, redis_client: redis.Redis, db_pool: asyncpg.Pool):
        self.redis = redis_client
        self.db_pool = db_pool
        self.logger = logging.getLogger(__name__)

    async def track_click(self, click_data: Dict) -> str:
        """Track affiliate click with comprehensive attribution"""
        try:
            # Generate unique click ID
            click_id = self._generate_click_id(click_data)  # ty:ignore[unresolved-attribute]

            # Parse UTM parameters
            utm_params = self._parse_utm_parameters(click_data.get('url', ''))  # ty:ignore[unresolved-attribute]

            # Enrich with geo and device data
            enriched_data = await self._enrich_click_data(click_data)  # ty:ignore[unresolved-attribute]

            # Create click record
            click = AffiliateClick(
                click_id=click_id,
                affiliate_id=click_data['affiliate_id'],
                campaign_id=click_data['campaign_id'],
                customer_id=click_data.get('customer_id'),
                timestamp=datetime.now(),
                ip_address=enriched_data['ip_address'],
                user_agent=click_data['user_agent'],
                landing_page=click_data['landing_page'],
                referrer=click_data.get('referrer', ''),
                utm_source=utm_params.get('utm_source', ''),
                utm_medium=utm_params.get('utm_medium', ''),
                utm_campaign=utm_params.get('utm_campaign', ''),
                creative_id=click_data.get('creative_id'),
                sub_id=click_data.get('sub_id'),
                cost_per_click=float(click_data.get('cpc', 0)),
                country=enriched_data['country'],
                device_type=enriched_data['device_type'],
                browser=enriched_data['browser'],
                os=enriched_data['os']
            )

            # Store click data
            await self._store_click(click)  # ty:ignore[unresolved-attribute]

            # Set attribution cookie
            attribution_data = {
                'click_id': click_id,
                'affiliate_id': click.affiliate_id,
                'campaign_id': click.campaign_id,
                'timestamp': click.timestamp.isoformat(),
                'attribution_model': 'first_touch'
            }

            await self.redis.setex(
                f"attribution:{click_id}",
                86400 * 30,  # 30 days
                json.dumps(attribution_data)
            )

            # Track in real-time analytics
            await self._track_real_time_metrics(click)  # ty:ignore[unresolved-attribute]

            return click_id

        except Exception as e:
            self.logger.error(f"Failed to track click: {e}")
            raise

    async def register_conversion(
        self,
        customer_id: str,
        conversion_type: str,
        value: float,
        metadata: Dict
    ) -> AttributionResult:
        """Register conversion and perform attribution"""
        try:
            # Get customer journey
            customer_journey = await self._get_customer_journey(customer_id)  # ty:ignore[unresolved-attribute]

            if not customer_journey:
                return AttributionResult(
                    customer_journey=[],
                    attributed_touchpoints=[],
                    conversion_value=value,
                    attribution_model='none',
                    confidence_score=0.0
                )

            # Apply attribution models
            attribution_models = ['first_touch', 'last_touch', 'linear', 'time_decay', 'position_based']
            attribution_results = {}

            for model in attribution_models:
                attributed_touchpoints = self._apply_attribution_model(
                    customer_journey, value, model
                )
                attribution_results[model] = attributed_touchpoints

            # Select best attribution based on confidence
            best_model = self._select_best_attribution(attribution_results)  # ty:ignore[unresolved-attribute]

            # Store attribution result
            attribution_result = AttributionResult(
                customer_journey=customer_journey,
                attributed_touchpoints=attribution_results[best_model],
                conversion_value=value,
                attribution_model=best_model,
                confidence_score=self._calculate_attribution_confidence(customer_journey)  # ty:ignore[unresolved-attribute]
            )

            await self._store_attribution_result(customer_id, attribution_result)  # ty:ignore[unresolved-attribute]

            # Update affiliate commissions
            await self._update_affiliate_commissions(attribution_result)  # ty:ignore[unresolved-attribute]

            # Trigger real-time notifications
            await self._trigger_conversion_notifications(attribution_result)  # ty:ignore[unresolved-attribute]

            return attribution_result

        except Exception as e:
            self.logger.error(f"Failed to register conversion: {e}")
            raise

    def _apply_attribution_model(
        self,
        journey: List[Dict],
        total_value: float,
        model: str
    ) -> List[Dict]:
        """Apply different attribution models to customer journey"""
        touchpoints = []

        if model == 'first_touch':
            # 100% to first touch
            if journey:
                first_touch = journey[0]
                touchpoints.append({
                    'touchpoint': first_touch,
                    'attributed_value': total_value,
                    'weight': 1.0
                })

        elif model == 'last_touch':
            # 100% to last touch
            if journey:
                last_touch = journey[-1]
                touchpoints.append({
                    'touchpoint': last_touch,
                    'attributed_value': total_value,
                    'weight': 1.0
                })

        elif model == 'linear':
            # Equal distribution across all touches
            if journey:
                value_per_touch = total_value / len(journey)
                for touch in journey:
                    touchpoints.append({
                        'touchpoint': touch,
                        'attributed_value': value_per_touch,
                        'weight': 1.0 / len(journey)
                    })

        elif model == 'time_decay':
            # More weight to recent touches
            if journey:
                # Use half-life of 7 days
                half_life = 7 * 24 * 3600  # 7 days in seconds
                now = datetime.now().timestamp()

                # Calculate time-based weights
                weights = []
                for touch in journey:
                    time_diff = now - datetime.fromisoformat(touch['timestamp']).timestamp()
                    weight = 2 ** (-time_diff / half_life)
                    weights.append(weight)

                # Normalize weights
                total_weight = sum(weights)
                normalized_weights = [w / total_weight for w in weights]

                # Apply weights to value
                for i, touch in enumerate(journey):
                    touchpoints.append({
                        'touchpoint': touch,
                        'attributed_value': total_value * normalized_weights[i],
                        'weight': normalized_weights[i]
                    })

        elif model == 'position_based':
            # 40% to first touch, 40% to last touch, 20% distributed among middle
            if journey:
                if len(journey) == 1:
                    touchpoints.append({
                        'touchpoint': journey[0],
                        'attributed_value': total_value,
                        'weight': 1.0
                    })
                elif len(journey) == 2:
                    for touch in journey:
                        touchpoints.append({
                            'touchpoint': touch,
                            'attributed_value': total_value * 0.5,
                            'weight': 0.5
                        })
                else:
                    # First touch: 40%
                    touchpoints.append({
                        'touchpoint': journey[0],
                        'attributed_value': total_value * 0.4,
                        'weight': 0.4
                    })

                    # Last touch: 40%
                    touchpoints.append({
                        'touchpoint': journey[-1],
                        'attributed_value': total_value * 0.4,
                        'weight': 0.4
                    })

                    # Middle touches: 20% distributed equally
                    middle_value = total_value * 0.2 / (len(journey) - 2)
                    middle_weight = 0.2 / (len(journey) - 2)

                    for touch in journey[1:-1]:
                        touchpoints.append({
                            'touchpoint': touch,
                            'attributed_value': middle_value,
                            'weight': middle_weight
                        })

        return touchpoints

    async def get_affiliate_performance(
        self,
        affiliate_id: str,
        date_range: Tuple[datetime, datetime]
    ) -> Dict:
        """Get comprehensive affiliate performance metrics"""
        start_date, end_date = date_range

        async with self.db_pool.acquire() as conn:
            # Get basic metrics
            metrics = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_clicks,
                    COUNT(DISTINCT customer_id) as unique_customers,
                    SUM(cost_per_click) as total_cost,
                    AVG(cost_per_click) as avg_cpc,
                    COUNT(CASE WHEN customer_id IS NOT NULL THEN 1 END) as tracked_conversions
                FROM affiliate_clicks
                WHERE affiliate_id = $1
                AND timestamp BETWEEN $2 AND $3
            """, affiliate_id, start_date, end_date)

            # Get conversion metrics
            conversions = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_conversions,
                    SUM(conversion_value) as total_conversion_value,
                    AVG(conversion_value) as avg_conversion_value,
                    COUNT(DISTINCT customer_id) as unique_converting_customers
                FROM conversions
                WHERE affiliate_id = $1
                AND conversion_date BETWEEN $2 AND $3
            """, affiliate_id, start_date, end_date)

            # Get attribution metrics by model
            attribution_metrics = await conn.fetch("""
                SELECT
                    attribution_model,
                    COUNT(*) as conversions,
                    SUM(attributed_value) as attributed_revenue,
                    AVG(confidence_score) as avg_confidence
                FROM attribution_results
                WHERE affiliate_id = $1
                AND attribution_date BETWEEN $2 AND $3
                GROUP BY attribution_model
            """, affiliate_id, start_date, end_date)

            # Calculate ROI metrics
            total_cost = float(metrics['total_cost'] or 0)
            total_revenue = float(conversions['total_conversion_value'] or 0)

            roi = ((total_revenue - total_cost) / total_cost * 100) if total_cost > 0 else 0

            return {
                'affiliate_id': affiliate_id,
                'period': {
                    'start': start_date.isoformat(),
                    'end': end_date.isoformat()
                },
                'traffic_metrics': dict(metrics) if metrics else {},
                'conversion_metrics': dict(conversions) if conversions else {},
                'attribution_breakdown': [dict(row) for row in attribution_metrics],
                'roi_metrics': {
                    'total_cost': total_cost,
                    'total_revenue': total_revenue,
                    'roi_percentage': roi,
                    'cost_per_conversion': total_cost / max(conversions['total_conversions'], 1),
                    'revenue_per_click': total_revenue / max(metrics['total_clicks'], 1)
                },
                'quality_metrics': await self._calculate_quality_metrics(affiliate_id, date_range)  # ty:ignore[unresolved-attribute]
            }

    async def detect_fraud(self, click_data: Dict) -> Dict:
        """Detect affiliate fraud using ML models"""
        fraud_signals = []
        risk_score = 0.0

        # IP-based fraud detection
        ip_analysis = await self._analyze_ip_fraud_patterns(click_data['ip_address'])  # ty:ignore[unresolved-attribute]
        if ip_analysis['is_suspicious']:
            fraud_signals.append('suspicious_ip_patterns')
            risk_score += 0.3

        # Device fingerprinting
        device_fingerprint = self._generate_device_fingerprint(click_data)  # ty:ignore[unresolved-attribute]
        device_history = await self._get_device_history(device_fingerprint)  # ty:ignore[unresolved-attribute]

        if len(device_history) > 100:  # Unusually high activity
            fraud_signals.append('excessive_device_usage')
            risk_score += 0.2

        # Click spam detection
        recent_clicks = await self._get_recent_clicks(  # ty:ignore[unresolved-attribute]
            click_data['affiliate_id'],
            minutes=5
        )

        if len(recent_clicks) > 50:  # More than 50 clicks in 5 minutes
            fraud_signals.append('click_spam')
            risk_score += 0.4

        # Conversion rate analysis
        conversion_rate = await self._calculate_conversion_rate(  # ty:ignore[unresolved-attribute]
            click_data['affiliate_id'],
            days=7
        )

        if conversion_rate > 0.5:  # Unrealistically high conversion rate
            fraud_signals.append('unnatural_conversion_rate')
            risk_score += 0.3

        # Geographic consistency check
        geo_consistency = await self._check_geographic_consistency(  # ty:ignore[unresolved-attribute]
            click_data['affiliate_id']
        )

        if not geo_consistency['is_consistent']:
            fraud_signals.append('geographic_inconsistency')
            risk_score += 0.2

        # Bot detection
        bot_score = self._detect_bot_traffic(click_data['user_agent'])  # ty:ignore[unresolved-attribute]
        if bot_score > 0.7:
            fraud_signals.append('bot_traffic')
            risk_score += 0.5

        # Calculate final risk score (cap at 1.0)
        final_risk_score = min(risk_score, 1.0)

        return {
            'is_fraudulent': final_risk_score > 0.7,
            'risk_score': final_risk_score,
            'fraud_signals': fraud_signals,
            'recommendation': 'block' if final_risk_score > 0.8 else
                            'review' if final_risk_score > 0.5 else 'allow',
            'detailed_analysis': {
                'ip_analysis': ip_analysis,
                'device_analysis': device_history,
                'click_patterns': recent_clicks,
                'conversion_analysis': conversion_rate,
                'geographic_analysis': geo_consistency
            }
        }
