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
AI-Powered Addiction Detection System for iGaming
===================================================
Chapter 10: Responsible Gaming and Player Protection

Machine learning models for addiction risk prediction providing:
- Ensemble model combining neural network, random forest, and anomaly detection
- Financial behavior feature extraction (deposit velocity, loss ratios)
- Temporal behavior pattern analysis (session timing, late-night play)
- Graduated intervention system (5 levels from Awareness to Intervention)
- Batch processing monitoring queue with configurable intervals

Risk Levels:
    MINIMAL  (<0.2): Standard monitoring
    LOW      (0.2-0.4): Increased monitoring
    MEDIUM   (0.4-0.6): Enhanced monitoring
    HIGH     (0.6-0.8): Enhanced monitoring and limits
    CRITICAL (>0.8):   Immediate intervention

Dependencies:
    pip install tensorflow scikit-learn joblib redis numpy pandas
"""

# Machine learning model for addiction detection
import tensorflow as tf  # ty:ignore[unresolved-import]
import numpy as np
from sklearn.ensemble import RandomForestClassifier, IsolationForest
from sklearn.preprocessing import StandardScaler
import joblib
from typing import Dict, List, Optional, Tuple
import pandas as pd
import json
import asyncio
import redis.asyncio as redis
import logging
from datetime import datetime

class AddictionDetectionModel:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.models = {}
        self.scalers = {}
        self.feature_importance = {}
        self.load_models()

    def load_models(self):
        """Load pre-trained ML models"""
        # Load deep learning model
        self.models['neural_network'] = tf.keras.models.load_model(
            f"{self.model_path}/addiction_detection_nn.h5"
        )

        # Load ensemble model
        self.models['ensemble'] = joblib.load(
            f"{self.model_path}/addiction_ensemble.pkl"
        )

        # Load anomaly detection model
        self.models['anomaly'] = joblib.load(
            f"{self.model_path}/behavior_anomaly_detector.pkl"
        )

        # Load feature scalers
        self.scalers['behavioral'] = joblib.load(
            f"{self.model_path}/behavioral_scaler.pkl"
        )

        # Load feature importance
        with open(f"{self.model_path}/feature_importance.json", 'r') as f:
            self.feature_importance = json.load(f)

    def extract_features(self, customer_data: Dict) -> np.ndarray:
        """Extract behavioral features for addiction detection"""
        features = []

        # Financial behavior features
        financial_features = self._extract_financial_features(customer_data)
        features.extend(financial_features)

        # Temporal behavior features
        temporal_features = self._extract_temporal_features(customer_data)
        features.extend(temporal_features)

        # Gaming pattern features
        gaming_features = self._extract_gaming_features(customer_data)  # ty:ignore[unresolved-attribute]
        features.extend(gaming_features)

        # Psychological indicators
        psychological_features = self._extract_psychological_features(customer_data)  # ty:ignore[unresolved-attribute]
        features.extend(psychological_features)

        return np.array(features).reshape(1, -1)

    def _extract_financial_features(self, data: Dict) -> List[float]:
        """Extract financial behavior indicators"""
        features = []

        # Deposit velocity
        deposits = data.get('deposits', [])
        if deposits:
            deposit_amounts = [d['amount'] for d in deposits]
            deposit_times = [d['timestamp'] for d in deposits]

            # Calculate deposit acceleration
            if len(deposit_amounts) > 1:
                deposit_velocity = np.diff(deposit_amounts).mean()
                features.append(deposit_velocity)
            else:
                features.append(0.0)

            # Deposit frequency
            deposit_frequency = len(deposits) / 30  # per day average
            features.append(deposit_frequency)

            # Deposit amount variance
            deposit_variance = np.var(deposit_amounts)
            features.append(deposit_variance)

            # Largest deposit vs average
            avg_deposit = np.mean(deposit_amounts)
            max_deposit = max(deposit_amounts)
            features.append(max_deposit / avg_deposit if avg_deposit > 0 else 1.0)

        else:
            features.extend([0.0, 0.0, 0.0, 1.0])

        # Loss chasing indicators
        withdrawals = data.get('withdrawals', [])
        if deposits and withdrawals:
            total_deposited = sum(d['amount'] for d in deposits)
            total_withdrawn = sum(w['amount'] for w in withdrawals)
            loss_ratio = (total_deposited - total_withdrawn) / total_deposited
            features.append(max(0, loss_ratio))
        else:
            features.append(0.0)

        return features

    def _extract_temporal_features(self, data: Dict) -> List[float]:
        """Extract temporal behavior patterns"""
        features = []

        # Session timing patterns
        sessions = data.get('sessions', [])
        if sessions:
            session_durations = [s['duration_minutes'] for s in sessions]
            session_start_times = [s['start_time'] for s in sessions]

            # Average session duration
            avg_duration = np.mean(session_durations)
            features.append(avg_duration)

            # Session duration variance
            duration_variance = np.var(session_durations)
            features.append(duration_variance)

            # Late night sessions (2 AM - 6 AM)
            late_night_sessions = 0
            for start_time in session_start_times:
                hour = datetime.fromisoformat(start_time).hour
                if 2 <= hour < 6:
                    late_night_sessions += 1

            late_night_ratio = late_night_sessions / len(sessions)
            features.append(late_night_ratio)

            # Session frequency (sessions per day)
            session_frequency = len(sessions) / 30
            features.append(session_frequency)

            # Longest session duration
            max_duration = max(session_durations)
            features.append(max_duration)

        else:
            features.extend([0.0, 0.0, 0.0, 0.0, 0.0])

        # Days since last activity
        if sessions:
            last_session = max(session_start_times)
            days_since_last = (datetime.now() - datetime.fromisoformat(last_session)).days
            features.append(days_since_last)
        else:
            features.append(30.0)  # Default to 30 days

        return features

    def predict_addiction_risk(self, customer_features: np.ndarray) -> Dict:
        """Predict addiction risk using ensemble of models"""
        # Scale features
        scaled_features = self.scalers['behavioral'].transform(customer_features)

        # Neural network prediction
        nn_prediction = self.models['neural_network'].predict(scaled_features)[0][0]

        # Ensemble model prediction
        ensemble_prediction = self.models['ensemble'].predict_proba(scaled_features)[0][1]

        # Anomaly detection
        anomaly_score = self.models['anomaly'].decision_function(scaled_features)[0]

        # Combine predictions with weights
        final_risk_score = (
            0.4 * nn_prediction +
            0.5 * ensemble_prediction +
            0.1 * (1 / (1 + np.exp(-anomaly_score)))  # Sigmoid normalization
        )

        # Determine risk level
        if final_risk_score >= 0.8:
            risk_level = 'CRITICAL'
            recommended_action = 'Immediate intervention required'
        elif final_risk_score >= 0.6:
            risk_level = 'HIGH'
            recommended_action = 'Enhanced monitoring and limits'
        elif final_risk_score >= 0.4:
            risk_level = 'MEDIUM'
            recommended_action = 'Increased monitoring'
        elif final_risk_score >= 0.2:
            risk_level = 'LOW'
            recommended_action = 'Standard monitoring'
        else:
            risk_level = 'MINIMAL'
            recommended_action = 'Continue normal operations'

        # Feature importance for explanation
        important_features = self._get_important_features(customer_features)

        return {
            'risk_score': float(final_risk_score),
            'risk_level': risk_level,
            'model_predictions': {
                'neural_network': float(nn_prediction),
                'ensemble': float(ensemble_prediction),
                'anomaly_score': float(anomaly_score)
            },
            'recommended_action': recommended_action,
            'confidence': self._calculate_prediction_confidence(
                nn_prediction, ensemble_prediction, anomaly_score
            ),
            'important_features': important_features,
            'model_version': 'v2.1.0',
            'timestamp': datetime.now().isoformat()
        }

    def _get_important_features(self, features: np.ndarray) -> List[Dict]:
        """Get most important features contributing to prediction"""
        feature_names = [
            'deposit_velocity', 'deposit_frequency', 'deposit_variance',
            'max_deposit_ratio', 'loss_ratio', 'avg_session_duration',
            'session_variance', 'late_night_ratio', 'session_frequency',
            'max_session_duration', 'days_since_last_activity'
        ]

        # Get feature importance scores
        importance_scores = self.models['ensemble'].feature_importances_

        # Create feature importance list
        feature_importance = []
        for i, (name, score) in enumerate(zip(feature_names, importance_scores)):
            feature_importance.append({
                'feature': name,
                'importance': float(score),
                'value': float(features[0][i])
            })

        # Sort by importance and return top 5
        feature_importance.sort(key=lambda x: x['importance'], reverse=True)
        return feature_importance[:5]

    def _calculate_prediction_confidence(self, nn_pred: float, ensemble_pred: float, anomaly: float) -> float:
        """Calculate confidence score based on model agreement"""
        # High confidence when models agree
        model_agreement = 1 - abs(nn_pred - ensemble_pred)

        # Consider anomaly score (lower anomaly = higher confidence)
        anomaly_confidence = 1 / (1 + abs(anomaly))

        # Combine factors
        confidence = (0.7 * model_agreement + 0.3 * anomaly_confidence)

        return float(confidence)


# Real-time addiction monitoring service
class AddictionMonitoringService:
    def __init__(self, model: AddictionDetectionModel, redis_client: redis.Redis):
        self.model = model
        self.redis = redis_client
        self.monitoring_queue = asyncio.Queue()
        self.batch_size = 100
        self.monitoring_interval = 300  # 5 minutes
        self.logger = logging.getLogger(__name__)

    async def start_monitoring(self):
        """Start continuous addiction monitoring"""
        # Start background tasks
        asyncio.create_task(self._process_monitoring_queue())
        asyncio.create_task(self._periodic_batch_analysis())  # ty:ignore[unresolved-attribute]

        self.logger.info("Addiction monitoring service started")

    async def schedule_customer_analysis(self, customer_id: str, priority: str = 'normal'):
        """Schedule customer for addiction analysis"""
        await self.monitoring_queue.put({
            'customer_id': customer_id,
            'priority': priority,
            'timestamp': datetime.now().isoformat()
        })

    async def _process_monitoring_queue(self):
        """Process customers in monitoring queue"""
        while True:
            try:
                # Get batch of customers
                batch = []
                for _ in range(self.batch_size):
                    try:
                        item = self.monitoring_queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break

                if batch:
                    # Process batch
                    await self._analyze_customer_batch(batch)

                # Small delay to prevent busy waiting
                await asyncio.sleep(1)

            except Exception as e:
                self.logger.error(f"Error processing monitoring queue: {e}")
                await asyncio.sleep(10)

    async def _analyze_customer_batch(self, batch: List[Dict]):
        """Analyze batch of customers for addiction risk"""
        for item in batch:
            try:
                customer_id = item['customer_id']

                # Get customer data
                customer_data = await self._get_customer_analysis_data(customer_id)  # ty:ignore[unresolved-attribute]

                if not customer_data:
                    continue

                # Extract features
                features = self.model.extract_features(customer_data)

                # Predict addiction risk
                risk_prediction = self.model.predict_addiction_risk(features)

                # Store prediction
                await self._store_risk_prediction(customer_id, risk_prediction)  # ty:ignore[unresolved-attribute]

                # Take action based on risk level
                await self._handle_risk_prediction(customer_id, risk_prediction)

            except Exception as e:
                self.logger.error(f"Failed to analyze customer {item['customer_id']}: {e}")

    async def _handle_risk_prediction(self, customer_id: str, prediction: Dict):
        """Take action based on addiction risk prediction"""
        risk_level = prediction['risk_level']

        if risk_level == 'CRITICAL':
            # Immediate intervention
            await self._trigger_immediate_intervention(customer_id, prediction)

        elif risk_level == 'HIGH':
            # Enhanced monitoring and limits
            await self._apply_enhanced_monitoring(customer_id, prediction)  # ty:ignore[unresolved-attribute]

        elif risk_level == 'MEDIUM':
            # Standard monitoring
            await self._apply_standard_monitoring(customer_id, prediction)  # ty:ignore[unresolved-attribute]

        # Store for trend analysis
        await self._update_risk_history(customer_id, prediction)  # ty:ignore[unresolved-attribute]

    async def _trigger_immediate_intervention(self, customer_id: str, prediction: Dict):
        """Trigger immediate intervention for critical risk"""
        # Apply strict limits
        await self._apply_strict_limits(customer_id, {  # ty:ignore[unresolved-attribute]
            'daily_deposit_limit': 50,
            'session_time_limit': 60,  # minutes
            'bet_size_limit': 10
        })

        # Send responsible gaming message
        await self._send_intervention_message(customer_id, prediction)  # ty:ignore[unresolved-attribute]

        # Flag for human review
        await self._flag_for_human_review(customer_id, prediction)  # ty:ignore[unresolved-attribute]

        # Consider temporary suspension
        if prediction['risk_score'] > 0.9:
            await self._recommend_temporary_suspension(customer_id)  # ty:ignore[unresolved-attribute]


class GraduatedInterventionFramework:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.logger = logging.getLogger(__name__)
        self.intervention_levels = {
            1: {
                'name': 'Awareness',
                'triggers': ['risk_score > 0.3'],
                'actions': ['educational_message', 'reality_check'],
                'frequency': 'weekly'
            },
            2: {
                'name': 'Guidance',
                'triggers': ['risk_score > 0.5', 'deposit_frequency_high'],
                'actions': ['responsible_gaming_tips', 'budget_reminder'],
                'frequency': 'bi-weekly'
            },
            3: {
                'name': 'Assistance',
                'triggers': ['risk_score > 0.7', 'loss_chasing_detected'],
                'actions': ['deposit_limits_suggestion', 'time_limits', 'cooling_off_offer'],
                'frequency': 'immediate'
            },
            4: {
                'name': 'Protection',
                'triggers': ['risk_score > 0.8', 'multiple_alerts'],
                'actions': ['mandatory_limits', 'session_restrictions', 'human_review'],
                'frequency': 'immediate'
            },
            5: {
                'name': 'Intervention',
                'triggers': ['risk_score > 0.9', 'critical_behavior'],
                'actions': ['temporary_suspension', 'self_exclusion_recommendation', 'support_referral'],
                'frequency': 'immediate'
            }
        }

    async def apply_intervention(self, customer_id: str, risk_assessment: Dict):
        """Apply appropriate intervention level"""
        risk_score = risk_assessment['risk_score']
        triggers = risk_assessment.get('triggers', [])

        # Determine intervention level
        intervention_level = self._determine_intervention_level(risk_score, triggers)  # ty:ignore[unresolved-attribute]

        # Check if customer has been recently intervened
        if await self._has_recent_intervention(customer_id, intervention_level):  # ty:ignore[unresolved-attribute]
            return  # Avoid over-intervention

        # Apply intervention
        intervention_config = self.intervention_levels[intervention_level]

        for action in intervention_config['actions']:
            await self._execute_intervention_action(customer_id, action, intervention_level)

        # Log intervention
        await self._log_intervention(customer_id, intervention_level, risk_assessment)  # ty:ignore[unresolved-attribute]

        # Schedule follow-up
        await self._schedule_follow_up(customer_id, intervention_level)  # ty:ignore[unresolved-attribute]

    async def _execute_intervention_action(self, customer_id: str, action: str, level: int):
        """Execute specific intervention action"""
        if action == 'educational_message':
            await self._send_educational_message(customer_id, level)  # ty:ignore[unresolved-attribute]

        elif action == 'reality_check':
            await self._enable_reality_check(customer_id, interval=30)  # 30 minutes  # ty:ignore[unresolved-attribute]

        elif action == 'responsible_gaming_tips':
            await self._send_rg_tips(customer_id)  # ty:ignore[unresolved-attribute]

        elif action == 'budget_reminder':
            await self._send_budget_reminder(customer_id)  # ty:ignore[unresolved-attribute]

        elif action == 'deposit_limits_suggestion':
            await self._suggest_deposit_limits(customer_id)  # ty:ignore[unresolved-attribute]

        elif action == 'time_limits':
            await self._apply_time_limits(customer_id, max_hours=2)  # ty:ignore[unresolved-attribute]

        elif action == 'cooling_off_offer':
            await self._offer_cooling_off(customer_id)  # ty:ignore[unresolved-attribute]

        elif action == 'mandatory_limits':
            await self._apply_mandatory_limits(customer_id)  # ty:ignore[unresolved-attribute]

        elif action == 'session_restrictions':
            await self._restrict_sessions(customer_id, max_per_day=1)  # ty:ignore[unresolved-attribute]

        elif action == 'human_review':
            await self._flag_for_human_review(customer_id, priority='high')  # ty:ignore[unresolved-attribute]

        elif action == 'temporary_suspension':
            await self._apply_temporary_suspension(customer_id, hours=24)  # ty:ignore[unresolved-attribute]

        elif action == 'self_exclusion_recommendation':
            await self._recommend_self_exclusion(customer_id)  # ty:ignore[unresolved-attribute]

        elif action == 'support_referral':
            await self._refer_to_support_services(customer_id)  # ty:ignore[unresolved-attribute]
