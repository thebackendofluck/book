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
Enterprise A/B Testing Framework for iGaming
=============================================
Chapter 9: Marketing Technology and CRM Systems

Statistical A/B testing infrastructure providing:
- Experiment creation with sample size calculation and power analysis
- Consistent variant assignment using hash-based deterministic bucketing
- Metric tracking with real-time statistical significance monitoring
- Comprehensive results reporting with confidence intervals
- Targeting rules and traffic allocation controls

Dependencies:
    pip install redis asyncpg numpy scipy
"""

# Enterprise A/B testing framework
import hashlib
import uuid
from typing import Dict, List, Optional
import redis.asyncio as redis
import asyncpg  # ty:ignore[unresolved-import]
from datetime import datetime
import json
import numpy as np
import logging

class ABTestingFramework:
    def __init__(self, redis_client: redis.Redis, db_pool: asyncpg.Pool):
        self.redis = redis_client
        self.db_pool = db_pool
        self.experiments = {}
        self.logger = logging.getLogger(__name__)

    async def create_experiment(
        self,
        experiment_config: Dict
    ) -> str:
        """Create new A/B experiment with statistical validation"""
        experiment_id = f"exp_{uuid.uuid4().hex[:8]}"

        # Validate experiment design
        validation = await self._validate_experiment_design(experiment_config)  # ty:ignore[unresolved-attribute]
        if not validation['valid']:
            raise ValueError(f"Invalid experiment: {validation['errors']}")

        # Calculate required sample size
        sample_size = self._calculate_sample_size(  # ty:ignore[unresolved-attribute]
            baseline_rate=experiment_config['baseline_conversion_rate'],
            minimum_detectable_effect=experiment_config['mde'],
            power=experiment_config.get('power', 0.8),
            significance_level=experiment_config.get('alpha', 0.05)
        )

        # Create experiment record
        experiment = {
            'id': experiment_id,
            'name': experiment_config['name'],
            'description': experiment_config['description'],
            'variants': experiment_config['variants'],
            'primary_metric': experiment_config['primary_metric'],
            'secondary_metrics': experiment_config.get('secondary_metrics', []),
            'targeting_rules': experiment_config.get('targeting_rules', {}),
            'sample_size': sample_size,
            'traffic_allocation': experiment_config.get('traffic_allocation', 1.0),
            'status': 'draft',
            'created_at': datetime.now().isoformat(),
            'expected_duration_days': self._estimate_duration(  # ty:ignore[unresolved-attribute]
                sample_size,
                experiment_config['daily_traffic']
            )
        }

        # Store experiment
        await self.redis.hset(
            f"experiment:{experiment_id}",
            mapping={k: json.dumps(v) if isinstance(v, (dict, list)) else v
                    for k, v in experiment.items()}
        )  # ty:ignore[invalid-await]

        return experiment_id

    async def assign_variant(
        self,
        experiment_id: str,
        customer_id: str,
        context: Dict
    ) -> str:
        """Assign customer to experiment variant"""
        # Check if customer is eligible
        if not await self._is_customer_eligible(experiment_id, customer_id, context):  # ty:ignore[unresolved-attribute]
            return 'control'

        # Check if already assigned
        assignment_key = f"assignment:{experiment_id}:{customer_id}"
        assigned_variant = await self.redis.get(assignment_key)

        if assigned_variant:
            return assigned_variant

        # Get experiment config
        experiment = await self._get_experiment(experiment_id)  # ty:ignore[unresolved-attribute]
        if not experiment or experiment['status'] != 'running':
            return 'control'

        # Check if experiment has reached sample size
        current_assignments = await self._get_assignment_count(experiment_id)  # ty:ignore[unresolved-attribute]
        if current_assignments >= experiment['sample_size']:
            return 'control'

        # Assign variant using consistent hashing
        variant = self._assign_variant_consistent(experiment_id, customer_id, experiment['variants'])

        # Store assignment
        await self.redis.setex(assignment_key, 86400 * 90, variant)  # 90 days

        # Track assignment
        await self._track_assignment(experiment_id, customer_id, variant, context)  # ty:ignore[unresolved-attribute]

        return variant

    def _assign_variant_consistent(
        self,
        experiment_id: str,
        customer_id: str,
        variants: List[Dict]
    ) -> str:
        """Consistently assign variant using hash function"""
        # Create hash from experiment ID and customer ID
        hash_input = f"{experiment_id}:{customer_id}"
        hash_value = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)

        # Calculate variant probabilities
        total_weight = sum(variant['traffic_percentage'] for variant in variants)

        # Find assigned variant
        cumulative_probability = 0
        hash_normalized = (hash_value % 1000000) / 1000000  # 6 decimal precision

        for variant in variants:
            variant_probability = variant['traffic_percentage'] / total_weight
            cumulative_probability += variant_probability

            if hash_normalized <= cumulative_probability:
                return variant['id']

        return variants[-1]['id']  # Fallback to last variant

    async def track_metric(
        self,
        experiment_id: str,
        customer_id: str,
        metric_name: str,
        metric_value: float,
        metadata: Optional[Dict] = None
    ):
        """Track experiment metric for statistical analysis"""
        # Get variant assignment
        variant = await self.get_assigned_variant(experiment_id, customer_id)  # ty:ignore[unresolved-attribute]

        if variant == 'control':
            return  # Not in experiment

        # Store metric
        metric_record = {
            'experiment_id': experiment_id,
            'customer_id': customer_id,
            'variant': variant,
            'metric_name': metric_name,
            'metric_value': metric_value,
            'timestamp': datetime.now().isoformat(),
            'metadata': metadata or {}
        }

        # Store in time-series database
        await self._store_metric(metric_record)  # ty:ignore[unresolved-attribute]

        # Check for statistical significance
        await self._check_statistical_significance(experiment_id, metric_name)  # ty:ignore[unresolved-attribute]

    async def get_experiment_results(self, experiment_id: str) -> Dict:
        """Get comprehensive experiment results with statistical analysis"""
        experiment = await self._get_experiment(experiment_id)  # ty:ignore[unresolved-attribute]
        if not experiment:
            return {'error': 'Experiment not found'}

        # Get metric data
        metrics_data = await self._get_experiment_metrics(experiment_id)  # ty:ignore[unresolved-attribute]

        # Calculate statistics for each variant
        results = {}
        for variant in experiment['variants']:
            variant_data = self._filter_variant_data(metrics_data, variant['id'])  # ty:ignore[unresolved-attribute]

            results[variant['id']] = {
                'sample_size': len(variant_data),
                'primary_metric': self._calculate_metric_statistics(
                    variant_data,
                    experiment['primary_metric']
                ),
                'secondary_metrics': {
                    metric: self._calculate_metric_statistics(variant_data, metric)
                    for metric in experiment['secondary_metrics']
                }
            }

        # Statistical significance testing
        significance_results = await self._perform_significance_testing(results)  # ty:ignore[unresolved-attribute]

        # Power analysis
        power_analysis = self._perform_power_analysis(results, experiment)  # ty:ignore[unresolved-attribute]

        return {
            'experiment_id': experiment_id,
            'experiment_name': experiment['name'],
            'status': experiment['status'],
            'results': results,
            'statistical_significance': significance_results,
            'power_analysis': power_analysis,
            'recommendations': self._generate_experiment_recommendations(  # ty:ignore[unresolved-attribute]
                results,
                significance_results
            )
        }

    def _calculate_metric_statistics(self, data: List[Dict], metric_name: str) -> Dict:
        """Calculate comprehensive statistics for a metric"""
        values = [d['metric_value'] for d in data if d['metric_name'] == metric_name]

        if not values:
            return {'error': 'No data available'}

        n = len(values)
        mean = np.mean(values)
        std_err = np.std(values) / np.sqrt(n)

        # Confidence interval (95%)
        confidence_interval = 1.96 * std_err

        return {
            'n': n,
            'mean': mean,
            'median': np.median(values),
            'std': np.std(values),
            'min': np.min(values),
            'max': np.max(values),
            'confidence_interval': [mean - confidence_interval, mean + confidence_interval],
            'conversion_rate': np.sum([1 for v in values if v > 0]) / n if metric_name == 'conversion' else None
        }
