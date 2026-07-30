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
Real-Time Behavioral Monitoring System for iGaming
====================================================
Chapter 10: Responsible Gaming and Player Protection

Production-ready behavioral monitoring system providing:
- Real-time analysis of deposit frequency, bet size increases, session duration
- Loss chasing detection and irregular hours monitoring
- Composite risk scoring with automated action triggers
- CRITICAL/HIGH risk automated interventions (limits, session termination)
- Integration with compliance team notifications

Dependencies:
    pip install redis asyncpg numpy
"""

# Real-time behavioral monitoring system
import asyncio
import numpy as np
from typing import Dict, List, Optional, Tuple
import redis.asyncio as redis
import asyncpg  # ty:ignore[unresolved-import]
from datetime import datetime, timedelta
import json
import uuid
import logging
from dataclasses import dataclass
from enum import Enum

class RiskLevel(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

class BehaviorType(Enum):
    DEPOSIT_FREQUENCY = "deposit_frequency"
    BET_SIZE_INCREASE = "bet_size_increase"
    SESSION_DURATION = "session_duration"
    CHASE_LOSSES = "chase_losses"
    IRREGULAR_HOURS = "irregular_hours"
    MULTIPLE_ACCOUNTS = "multiple_accounts"
    SELF_EXCLUSION_ATTEMPTS = "self_exclusion_attempts"

@dataclass
class BehavioralAlert:
    alert_id: str
    customer_id: str
    behavior_type: BehaviorType
    risk_level: RiskLevel
    score: float
    threshold_exceeded: float
    evidence: Dict
    timestamp: datetime
    recommended_action: str
    auto_triggered: bool

class RealTimeBehaviorMonitor:
    def __init__(self, redis_client: redis.Redis, db_pool: asyncpg.Pool):
        self.redis = redis_client
        self.db_pool = db_pool
        self.logger = logging.getLogger(__name__)

        # Behavioral thresholds (configurable per jurisdiction)
        self.thresholds = {
            'deposit_frequency': {
                'daily_max': 5,
                'weekly_max': 20,
                'monthly_max': 60
            },
            'bet_size_increase': {
                'daily_multiplier': 3.0,
                'weekly_multiplier': 5.0
            },
            'session_duration': {
                'daily_max_hours': 8,
                'continuous_max_hours': 4
            },
            'chase_losses': {
                'consecutive_deposits': 3,
                'loss_threshold': 0.8  # 80% of deposits lost
            },
            'irregular_hours': {
                'late_night_sessions': 3,  # Sessions after 2 AM
                'early_morning_sessions': 3  # Sessions before 6 AM
            }
        }

        # Risk scoring weights
        self.risk_weights = {
            BehaviorType.DEPOSIT_FREQUENCY: 0.25,
            BehaviorType.BET_SIZE_INCREASE: 0.30,
            BehaviorType.SESSION_DURATION: 0.20,
            BehaviorType.CHASE_LOSSES: 0.35,
            BehaviorType.IRREGULAR_HOURS: 0.15,
            BehaviorType.MULTIPLE_ACCOUNTS: 0.40,
            BehaviorType.SELF_EXCLUSION_ATTEMPTS: 0.50
        }

    async def analyze_behavior(self, customer_id: str, event_data: Dict) -> Optional[BehavioralAlert]:
        """Analyze customer behavior in real-time"""
        try:
            # Get customer baseline
            baseline = await self._get_customer_baseline(customer_id)  # ty:ignore[unresolved-attribute]

            # Analyze different behavior patterns
            alerts = []

            # Check deposit frequency
            deposit_alert = await self._check_deposit_frequency(customer_id, event_data)
            if deposit_alert:
                alerts.append(deposit_alert)

            # Check bet size increases
            bet_alert = await self._check_bet_size_increases(customer_id, event_data)
            if bet_alert:
                alerts.append(bet_alert)

            # Check session duration
            session_alert = await self._check_session_duration(customer_id, event_data)  # ty:ignore[unresolved-attribute]
            if session_alert:
                alerts.append(session_alert)

            # Check for loss chasing
            chase_alert = await self._check_loss_chasing(customer_id, event_data)
            if chase_alert:
                alerts.append(chase_alert)

            # Check irregular playing hours
            hours_alert = await self._check_irregular_hours(customer_id, event_data)  # ty:ignore[unresolved-attribute]
            if hours_alert:
                alerts.append(hours_alert)

            # Calculate composite risk score
            if alerts:
                composite_alert = self._calculate_composite_risk(alerts)  # ty:ignore[unresolved-attribute]

                # Take automated action if necessary
                if composite_alert.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                    await self._take_automated_action(composite_alert)

                # Store alert
                await self._store_alert(composite_alert)  # ty:ignore[unresolved-attribute]

                # Send to compliance team
                await self._notify_compliance_team(composite_alert)  # ty:ignore[unresolved-attribute]

                return composite_alert

            return None

        except Exception as e:
            self.logger.error(f"Behavior analysis failed for {customer_id}: {e}")
            return None

    async def _check_deposit_frequency(self, customer_id: str, event_data: Dict) -> Optional[BehavioralAlert]:
        """Check for excessive deposit frequency"""
        if event_data.get('event_type') != 'deposit':
            return None

        # Get deposit history
        deposit_history = await self._get_deposit_history(customer_id, days=30)  # ty:ignore[unresolved-attribute]

        # Calculate frequency metrics
        now = datetime.now()
        today_deposits = sum(1 for d in deposit_history if d['timestamp'].date() == now.date())
        this_week_deposits = sum(1 for d in deposit_history if d['timestamp'] >= now - timedelta(days=7))
        this_month_deposits = len(deposit_history)

        # Check against thresholds
        thresholds = self.thresholds['deposit_frequency']

        risk_score = 0
        exceeded_thresholds = []

        if today_deposits > thresholds['daily_max']:
            risk_score += 0.4
            exceeded_thresholds.append(f"daily:{today_deposits}")

        if this_week_deposits > thresholds['weekly_max']:
            risk_score += 0.3
            exceeded_thresholds.append(f"weekly:{this_week_deposits}")

        if this_month_deposits > thresholds['monthly_max']:
            risk_score += 0.3
            exceeded_thresholds.append(f"monthly:{this_month_deposits}")

        if risk_score > 0:
            return BehavioralAlert(
                alert_id=f"deposit_freq_{uuid.uuid4().hex[:8]}",
                customer_id=customer_id,
                behavior_type=BehaviorType.DEPOSIT_FREQUENCY,
                risk_level=self._calculate_risk_level(risk_score),
                score=risk_score,
                threshold_exceeded=max([today_deposits, this_week_deposits, this_month_deposits]),
                evidence={
                    'today_deposits': today_deposits,
                    'this_week_deposits': this_week_deposits,
                    'this_month_deposits': this_month_deposits,
                    'thresholds': thresholds
                },
                timestamp=datetime.now(),
                recommended_action=self._get_recommended_action(BehaviorType.DEPOSIT_FREQUENCY, risk_score),
                auto_triggered=True
            )

        return None

    async def _check_bet_size_increases(self, customer_id: str, event_data: Dict) -> Optional[BehavioralAlert]:
        """Check for rapid bet size increases"""
        if event_data.get('event_type') != 'bet_placed':
            return None

        bet_amount = float(event_data.get('bet_amount', 0))
        if bet_amount == 0:
            return None

        # Get historical betting patterns
        betting_history = await self._get_betting_history(customer_id, days=7)  # ty:ignore[unresolved-attribute]

        if not betting_history:
            return None

        # Calculate average bet sizes
        daily_avg = self._calculate_daily_average_bet(betting_history, days=1)  # ty:ignore[unresolved-attribute]
        weekly_avg = self._calculate_daily_average_bet(betting_history, days=7)  # ty:ignore[unresolved-attribute]

        # Check for significant increases
        thresholds = self.thresholds['bet_size_increase']

        risk_score = 0
        exceeded_multiplier = 1.0

        if daily_avg > 0 and bet_amount > daily_avg * thresholds['daily_multiplier']:
            risk_score += 0.6
            exceeded_multiplier = max(exceeded_multiplier, bet_amount / daily_avg)

        if weekly_avg > 0 and bet_amount > weekly_avg * thresholds['weekly_multiplier']:
            risk_score += 0.4
            exceeded_multiplier = max(exceeded_multiplier, bet_amount / weekly_avg)

        if risk_score > 0:
            return BehavioralAlert(
                alert_id=f"bet_increase_{uuid.uuid4().hex[:8]}",
                customer_id=customer_id,
                behavior_type=BehaviorType.BET_SIZE_INCREASE,
                risk_level=self._calculate_risk_level(risk_score),
                score=risk_score,
                threshold_exceeded=exceeded_multiplier,
                evidence={
                    'current_bet': bet_amount,
                    'daily_avg': daily_avg,
                    'weekly_avg': weekly_avg,
                    'multiplier_exceeded': exceeded_multiplier,
                    'thresholds': thresholds
                },
                timestamp=datetime.now(),
                recommended_action=self._get_recommended_action(BehaviorType.BET_SIZE_INCREASE, risk_score),
                auto_triggered=True
            )

        return None

    async def _check_loss_chasing(self, customer_id: str, event_data: Dict) -> Optional[BehavioralAlert]:
        """Check for loss chasing behavior"""
        if event_data.get('event_type') != 'deposit':
            return None

        # Get recent financial history
        recent_history = await self._get_financial_history(customer_id, days=7)  # ty:ignore[unresolved-attribute]

        if len(recent_history) < 3:
            return None

        # Analyze for loss chasing patterns
        consecutive_deposits = 0
        total_deposited = 0
        total_lost = 0

        for event in reversed(recent_history[-10:]):  # Last 10 events
            if event['type'] == 'deposit':
                consecutive_deposits += 1
                total_deposited += event['amount']
            elif event['type'] == 'withdrawal':
                break  # Reset pattern
            elif event['type'] == 'bet_settled' and event['result'] == 'loss':
                total_lost += event['amount']

        # Check against thresholds
        thresholds = self.thresholds['chase_losses']

        risk_score = 0

        if consecutive_deposits >= thresholds['consecutive_deposits']:
            risk_score += 0.6

            # Check if most of deposits are lost
            loss_ratio = total_lost / total_deposited if total_deposited > 0 else 0

            if loss_ratio > thresholds['loss_threshold']:
                risk_score += 0.4

        if risk_score > 0:
            return BehavioralAlert(
                alert_id=f"chase_losses_{uuid.uuid4().hex[:8]}",
                customer_id=customer_id,
                behavior_type=BehaviorType.CHASE_LOSSES,
                risk_level=self._calculate_risk_level(risk_score),
                score=risk_score,
                threshold_exceeded=consecutive_deposits,
                evidence={
                    'consecutive_deposits': consecutive_deposits,
                    'total_deposited': total_deposited,
                    'total_lost': total_lost,
                    'loss_ratio': loss_ratio,
                    'thresholds': thresholds
                },
                timestamp=datetime.now(),
                recommended_action=self._get_recommended_action(BehaviorType.CHASE_LOSSES, risk_score),
                auto_triggered=True
            )

        return None

    async def _take_automated_action(self, alert: BehavioralAlert):
        """Take automated action based on risk level"""
        if alert.risk_level == RiskLevel.HIGH:
            # Apply deposit limits
            await self._apply_temporary_limits(  # ty:ignore[unresolved-attribute]
                alert.customer_id,
                deposit_limit=100,  # Daily limit in USD
                session_limit=2     # Hours per session
            )

            # Send responsible gaming message
            await self._send_responsible_gaming_message(alert.customer_id, alert)  # ty:ignore[unresolved-attribute]

        elif alert.risk_level == RiskLevel.CRITICAL:
            # Immediate session termination
            await self._terminate_sessions(alert.customer_id)  # ty:ignore[unresolved-attribute]

            # Apply strict limits
            await self._apply_strict_limits(alert.customer_id)  # ty:ignore[unresolved-attribute]

            # Flag for immediate human review
            await self._flag_for_immediate_review(alert)  # ty:ignore[unresolved-attribute]

            # Consider self-exclusion recommendation
            await self._recommend_self_exclusion(alert.customer_id)  # ty:ignore[unresolved-attribute]

    def _calculate_risk_level(self, risk_score: float) -> RiskLevel:
        """Convert risk score to risk level"""
        if risk_score >= 0.8:
            return RiskLevel.CRITICAL
        elif risk_score >= 0.6:
            return RiskLevel.HIGH
        elif risk_score >= 0.3:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    def _get_recommended_action(self, behavior_type: BehaviorType, risk_score: float) -> str:
        """Get recommended action based on behavior and risk"""
        if risk_score >= 0.8:
            return "Immediate intervention required - consider temporary suspension"
        elif risk_score >= 0.6:
            return "Apply limits and send responsible gaming information"
        elif risk_score >= 0.3:
            return "Monitor closely and send educational content"
        else:
            return "Continue monitoring"

    async def get_customer_risk_profile(self, customer_id: str) -> Dict:
        """Get comprehensive risk profile for a customer"""
        # Get recent alerts
        recent_alerts = await self._get_recent_alerts(customer_id, days=30)  # ty:ignore[unresolved-attribute]

        # Calculate risk metrics
        total_alerts = len(recent_alerts)
        high_risk_alerts = sum(1 for alert in recent_alerts if alert.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL])

        # Get current limits and restrictions
        current_limits = await self._get_current_limits(customer_id)  # ty:ignore[unresolved-attribute]

        # Calculate behavioral patterns
        patterns = await self._analyze_behavioral_patterns(customer_id)  # ty:ignore[unresolved-attribute]

        # Get compliance history
        compliance_history = await self._get_compliance_history(customer_id)  # ty:ignore[unresolved-attribute]

        return {
            'customer_id': customer_id,
            'risk_score': self._calculate_overall_risk_score(recent_alerts),  # ty:ignore[unresolved-attribute]
            'risk_level': self._determine_overall_risk_level(recent_alerts),  # ty:ignore[unresolved-attribute]
            'alert_summary': {
                'total_alerts_30d': total_alerts,
                'high_risk_alerts_30d': high_risk_alerts,
                'alert_breakdown': self._breakdown_alerts_by_type(recent_alerts)  # ty:ignore[unresolved-attribute]
            },
            'current_restrictions': current_limits,
            'behavioral_patterns': patterns,
            'compliance_history': compliance_history,
            'recommendations': self._generate_risk_recommendations(recent_alerts, patterns)  # ty:ignore[unresolved-attribute]
        }
