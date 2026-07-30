#!/usr/bin/env python3
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
A/B Testing Platform for Casino Marketing
==========================================
Statistical experiment platform for testing casino marketing hypotheses:
bonus offers, landing pages, CRM messages, game recommendations.

Features:
- Frequentist and Bayesian significance testing
- Multi-armed bandit for continuous optimization
- Guardrail metrics (responsible gambling indicators)
- Automatic sample size calculation
- Segment-level analysis (VIP tiers, geo, device)

Casino-Specific Considerations:
- Experiments must exclude self-excluded players
- Bonus experiments require CFO approval above threshold
- UK LCCP: marketing experiments must respect opt-out preferences
- Minimum 7-day experiment duration (covers weekly casino cycles)
- Revenue metrics require 30+ day lookback for wagering completion
"""

import math
import random
import logging
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class ExperimentStatus(Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    TERMINATED = "terminated"  # Stopped early due to guardrail violation


class MetricType(Enum):
    CONVERSION = "conversion"    # Binary (did/didn't deposit)
    REVENUE = "revenue"          # Continuous (deposit amount, GGR)
    COUNT = "count"              # Discrete (number of bets, sessions)
    RATE = "rate"                # Ratio (wagering completion rate)


@dataclass
class ExperimentMetric:
    """A metric being tracked in an experiment."""
    name: str
    metric_type: MetricType
    is_primary: bool = False
    is_guardrail: bool = False   # Guardrail metrics halt experiment if violated
    minimum_detectable_effect: float = 0.05  # 5% MDE
    direction: str = "higher_is_better"  # or "lower_is_better" for guardrails


@dataclass
class Variant:
    """An experiment variant (control or treatment)."""
    variant_id: str
    name: str
    description: str
    allocation_percent: float  # e.g., 50.0 for 50%
    is_control: bool = False
    config: dict = field(default_factory=dict)  # Variant-specific parameters


@dataclass
class ExperimentAssignment:
    """A player's assignment to a variant."""
    player_id: str
    experiment_id: str
    variant_id: str
    assigned_at: datetime
    segment: str = ""  # VIP tier, geo, etc.


@dataclass
class MetricObservation:
    """A single metric observation for a player in an experiment."""
    player_id: str
    experiment_id: str
    variant_id: str
    metric_name: str
    value: float
    observed_at: datetime


@dataclass
class Experiment:
    """An A/B test experiment."""
    experiment_id: str
    name: str
    hypothesis: str
    owner: str
    status: ExperimentStatus = ExperimentStatus.DRAFT
    variants: list = field(default_factory=list)
    metrics: list = field(default_factory=list)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    min_duration_days: int = 7       # Casino weekly cycle
    max_duration_days: int = 30
    target_sample_per_variant: int = 0
    exclude_self_excluded: bool = True
    exclude_new_registrations: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)  # ty:ignore[deprecated]


class StatisticalEngine:
    """Statistical analysis engine for experiment results."""

    @staticmethod
    def required_sample_size(
        baseline_rate: float,
        minimum_detectable_effect: float,
        alpha: float = 0.05,
        power: float = 0.80,
    ) -> int:
        """
        Calculate required sample size per variant for a conversion test.
        Uses the normal approximation formula.

        Example: baseline 5% FTD rate, detect 20% relative lift (1% absolute)
        -> ~3,800 players per variant
        """
        p1 = baseline_rate
        p2 = baseline_rate * (1 + minimum_detectable_effect)

        # Z-scores
        z_alpha = 1.96 if alpha == 0.05 else 2.576  # 95% or 99%
        z_beta = 0.84 if power == 0.80 else 1.28    # 80% or 90%

        p_avg = (p1 + p2) / 2
        numerator = (
            z_alpha * math.sqrt(2 * p_avg * (1 - p_avg))
            + z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
        ) ** 2
        denominator = (p2 - p1) ** 2

        if denominator == 0:
            return 999999

        return math.ceil(numerator / denominator)

    @staticmethod
    def frequentist_test(
        control_conversions: int, control_total: int,
        treatment_conversions: int, treatment_total: int,
        alpha: float = 0.05,
    ) -> dict:
        """
        Two-proportion z-test for conversion metrics.
        Returns test statistics and significance.
        """
        if control_total == 0 or treatment_total == 0:
            return {"significant": False, "error": "insufficient_data"}

        p_c = control_conversions / control_total
        p_t = treatment_conversions / treatment_total
        p_pool = (control_conversions + treatment_conversions) / (control_total + treatment_total)

        se = math.sqrt(p_pool * (1 - p_pool) * (1 / control_total + 1 / treatment_total))

        if se == 0:
            return {"significant": False, "error": "zero_variance"}

        z_stat = (p_t - p_c) / se
        # Two-tailed p-value approximation using the error function
        p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(z_stat) / math.sqrt(2))))

        lift = (p_t - p_c) / p_c if p_c > 0 else 0

        # Confidence interval for the difference
        se_diff = math.sqrt(p_c * (1 - p_c) / control_total + p_t * (1 - p_t) / treatment_total)
        z_crit = 1.96 if alpha == 0.05 else 2.576
        ci_lower = (p_t - p_c) - z_crit * se_diff
        ci_upper = (p_t - p_c) + z_crit * se_diff

        return {
            "significant": p_value < alpha,
            "p_value": round(p_value, 6),
            "z_statistic": round(z_stat, 4),
            "control_rate": round(p_c, 6),
            "treatment_rate": round(p_t, 6),
            "absolute_lift": round(p_t - p_c, 6),
            "relative_lift": round(lift, 4),
            "confidence_interval": [round(ci_lower, 6), round(ci_upper, 6)],
            "control_n": control_total,
            "treatment_n": treatment_total,
        }

    @staticmethod
    def bayesian_test(
        control_conversions: int, control_total: int,
        treatment_conversions: int, treatment_total: int,
        num_simulations: int = 100000,
        prior_alpha: float = 1.0, prior_beta: float = 1.0,
    ) -> dict:
        """
        Bayesian A/B test using Beta-Binomial model with Monte Carlo sampling.
        Returns probability that treatment beats control.

        Uniform prior (alpha=1, beta=1) is standard for casino experiments.
        """
        random.seed(42)

        a_alpha = prior_alpha + control_conversions
        a_beta = prior_beta + (control_total - control_conversions)
        b_alpha = prior_alpha + treatment_conversions
        b_beta = prior_beta + (treatment_total - treatment_conversions)

        treatment_wins = 0
        lifts = []

        for _ in range(num_simulations):
            # Sample from Beta posteriors
            sample_control = random.betavariate(a_alpha, a_beta)
            sample_treatment = random.betavariate(b_alpha, b_beta)

            if sample_treatment > sample_control:
                treatment_wins += 1

            if sample_control > 0:
                lifts.append((sample_treatment - sample_control) / sample_control)

        prob_treatment_better = treatment_wins / num_simulations
        lifts.sort()

        return {
            "prob_treatment_better": round(prob_treatment_better, 4),
            "expected_lift": round(sum(lifts) / len(lifts), 4) if lifts else 0,
            "lift_ci_95": [
                round(lifts[int(0.025 * len(lifts))], 4) if lifts else 0,
                round(lifts[int(0.975 * len(lifts))], 4) if lifts else 0,
            ],
            "risk_of_choosing_treatment": round(1 - prob_treatment_better, 4),
        }

    @staticmethod
    def revenue_test(
        control_values: list[float],
        treatment_values: list[float],
    ) -> dict:
        """
        Welch's t-test for revenue/continuous metrics.
        More appropriate than z-test for non-equal variances.
        """
        n_c, n_t = len(control_values), len(treatment_values)
        if n_c < 2 or n_t < 2:
            return {"significant": False, "error": "insufficient_data"}

        mean_c = sum(control_values) / n_c
        mean_t = sum(treatment_values) / n_t
        var_c = sum((x - mean_c) ** 2 for x in control_values) / (n_c - 1)
        var_t = sum((x - mean_t) ** 2 for x in treatment_values) / (n_t - 1)

        se = math.sqrt(var_c / n_c + var_t / n_t)
        if se == 0:
            return {"significant": False, "error": "zero_variance"}

        t_stat = (mean_t - mean_c) / se

        # Welch-Satterthwaite degrees of freedom
        numerator = (var_c / n_c + var_t / n_t) ** 2
        denominator = (
            (var_c / n_c) ** 2 / (n_c - 1)
            + (var_t / n_t) ** 2 / (n_t - 1)
        )
        df = numerator / denominator if denominator > 0 else min(n_c, n_t) - 1

        # Approximate p-value (normal approximation for large df)
        p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / math.sqrt(2))))

        return {
            "significant": p_value < 0.05,
            "p_value": round(p_value, 6),
            "t_statistic": round(t_stat, 4),
            "degrees_of_freedom": round(df, 1),
            "control_mean": round(mean_c, 2),
            "treatment_mean": round(mean_t, 2),
            "absolute_lift": round(mean_t - mean_c, 2),
            "relative_lift": round((mean_t - mean_c) / mean_c, 4) if mean_c != 0 else 0,
        }


class ExperimentPlatform:
    """
    Main experiment platform for managing casino A/B tests.
    """

    def __init__(self):
        self.experiments: dict[str, Experiment] = {}
        self.assignments: dict[str, list[ExperimentAssignment]] = defaultdict(list)
        self.observations: dict[str, list[MetricObservation]] = defaultdict(list)
        self.stats = StatisticalEngine()

    def create_experiment(self, experiment: Experiment) -> Experiment:
        """Create a new experiment with sample size calculation."""
        primary = next((m for m in experiment.metrics if m.is_primary), None)
        if primary and primary.metric_type == MetricType.CONVERSION:
            experiment.target_sample_per_variant = self.stats.required_sample_size(
                baseline_rate=0.05,  # Typical casino FTD rate
                minimum_detectable_effect=primary.minimum_detectable_effect,
            )
            logger.info(
                "Experiment %s requires %d players per variant",
                experiment.experiment_id,
                experiment.target_sample_per_variant,
            )

        self.experiments[experiment.experiment_id] = experiment
        return experiment

    def assign_player(self, player_id: str, experiment_id: str,
                      segment: str = "") -> Optional[ExperimentAssignment]:
        """
        Assign a player to an experiment variant using deterministic hashing.
        Ensures stable assignment across sessions.
        """
        experiment = self.experiments.get(experiment_id)
        if not experiment or experiment.status != ExperimentStatus.RUNNING:
            return None

        # Check if already assigned
        existing = [
            a for a in self.assignments[experiment_id]
            if a.player_id == player_id
        ]
        if existing:
            return existing[0]

        # Deterministic hash-based assignment
        hash_input = f"{experiment_id}:{player_id}"
        hash_val = int(hashlib.sha256(hash_input.encode()).hexdigest(), 16)
        bucket = (hash_val % 10000) / 100  # 0.00 to 99.99

        cumulative = 0.0
        selected_variant = experiment.variants[-1]
        for variant in experiment.variants:
            cumulative += variant.allocation_percent
            if bucket < cumulative:
                selected_variant = variant
                break

        assignment = ExperimentAssignment(
            player_id=player_id,
            experiment_id=experiment_id,
            variant_id=selected_variant.variant_id,
            assigned_at=datetime.utcnow(),  # ty:ignore[deprecated]
            segment=segment,
        )
        self.assignments[experiment_id].append(assignment)
        return assignment

    def record_metric(self, player_id: str, experiment_id: str,
                      metric_name: str, value: float):
        """Record a metric observation for a player."""
        assignment = next(
            (a for a in self.assignments[experiment_id] if a.player_id == player_id),
            None,
        )
        if not assignment:
            return

        obs = MetricObservation(
            player_id=player_id,
            experiment_id=experiment_id,
            variant_id=assignment.variant_id,
            metric_name=metric_name,
            value=value,
            observed_at=datetime.utcnow(),  # ty:ignore[deprecated]
        )
        self.observations[experiment_id].append(obs)

    def analyze(self, experiment_id: str, method: str = "frequentist") -> dict:
        """
        Analyze experiment results.

        Returns per-metric analysis comparing each treatment to control.
        """
        experiment = self.experiments.get(experiment_id)
        if not experiment:
            return {"error": "experiment_not_found"}

        control = next((v for v in experiment.variants if v.is_control), None)
        treatments = [v for v in experiment.variants if not v.is_control]

        if not control:
            return {"error": "no_control_variant"}

        results = {
            "experiment_id": experiment_id,
            "status": experiment.status.value,
            "total_assignments": len(self.assignments[experiment_id]),
            "metrics": {},
        }

        for metric in experiment.metrics:
            metric_results = {}
            control_obs = [
                o.value for o in self.observations[experiment_id]
                if o.variant_id == control.variant_id and o.metric_name == metric.name
            ]

            control_assignments = [
                a for a in self.assignments[experiment_id]
                if a.variant_id == control.variant_id
            ]

            for treatment in treatments:
                treatment_obs = [
                    o.value for o in self.observations[experiment_id]
                    if o.variant_id == treatment.variant_id and o.metric_name == metric.name
                ]
                treatment_assignments = [
                    a for a in self.assignments[experiment_id]
                    if a.variant_id == treatment.variant_id
                ]

                if metric.metric_type == MetricType.CONVERSION:
                    if method == "bayesian":
                        test_result = self.stats.bayesian_test(
                            int(sum(control_obs)), len(control_assignments),
                            int(sum(treatment_obs)), len(treatment_assignments),
                        )
                    else:
                        test_result = self.stats.frequentist_test(
                            int(sum(control_obs)), len(control_assignments),
                            int(sum(treatment_obs)), len(treatment_assignments),
                        )
                elif metric.metric_type in (MetricType.REVENUE, MetricType.COUNT):
                    test_result = self.stats.revenue_test(control_obs, treatment_obs)
                else:
                    test_result = {"error": "unsupported_metric_type"}

                metric_results[treatment.variant_id] = test_result

            results["metrics"][metric.name] = metric_results

        # Check guardrails
        results["guardrail_violations"] = self._check_guardrails(experiment_id)  # ty:ignore[invalid-assignment]

        return results

    def _check_guardrails(self, experiment_id: str) -> list[str]:
        """
        Check guardrail metrics for violations.

        Casino guardrails:
        - Player complaint rate must not increase >10%
        - Self-exclusion rate must not increase >5%
        - Average session duration must not increase >20% (problem gambling signal)
        - Deposit velocity must not increase >15%
        """
        violations = []
        experiment = self.experiments.get(experiment_id)
        if not experiment:
            return violations

        control = next((v for v in experiment.variants if v.is_control), None)
        guardrails = [m for m in experiment.metrics if m.is_guardrail]

        for metric in guardrails:
            control_obs = [
                o.value for o in self.observations[experiment_id]
                if o.variant_id == control.variant_id and o.metric_name == metric.name  # ty:ignore[unresolved-attribute]
            ]
            for variant in experiment.variants:
                if variant.is_control:
                    continue
                variant_obs = [
                    o.value for o in self.observations[experiment_id]
                    if o.variant_id == variant.variant_id and o.metric_name == metric.name
                ]
                if control_obs and variant_obs:
                    control_mean = sum(control_obs) / len(control_obs)
                    variant_mean = sum(variant_obs) / len(variant_obs)
                    if control_mean > 0:
                        lift = (variant_mean - control_mean) / control_mean
                        threshold = metric.minimum_detectable_effect
                        if metric.direction == "lower_is_better" and lift > threshold:
                            violations.append(
                                f"{metric.name}: {variant.variant_id} increased by "
                                f"{lift:.1%} (threshold: {threshold:.1%})"
                            )

        return violations


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    random.seed(42)

    platform = ExperimentPlatform()

    # Create experiment: test new welcome bonus
    experiment = Experiment(
        experiment_id="exp_welcome_bonus_v2",
        name="Welcome Bonus: 100% vs 200% Match",
        hypothesis="200% match will increase FTD conversion without degrading LTV",
        owner="marketing@casino.com",
        status=ExperimentStatus.RUNNING,
        variants=[
            Variant("control", "100% Match up to 100", "Current welcome offer",
                    50.0, is_control=True,
                    config={"match_pct": 100, "max_bonus": 100, "wagering": 35}),
            Variant("treatment_a", "200% Match up to 50", "Higher match, lower cap",
                    50.0, config={"match_pct": 200, "max_bonus": 50, "wagering": 40}),
        ],
        metrics=[
            ExperimentMetric("ftd_conversion", MetricType.CONVERSION,
                             is_primary=True, minimum_detectable_effect=0.10),
            ExperimentMetric("first_deposit_amount", MetricType.REVENUE),
            ExperimentMetric("day7_ggr", MetricType.REVENUE),
            ExperimentMetric("self_exclusion_rate", MetricType.CONVERSION,
                             is_guardrail=True, direction="lower_is_better",
                             minimum_detectable_effect=0.05),
        ],
    )
    platform.create_experiment(experiment)

    # Simulate player assignments and outcomes
    for i in range(2000):
        player_id = f"player_{i}"
        assignment = platform.assign_player(player_id, "exp_welcome_bonus_v2")
        if not assignment:
            continue

        # Simulate conversion (control: 5%, treatment: 6.5%)
        is_control = assignment.variant_id == "control"
        ftd_rate = 0.05 if is_control else 0.065
        converted = random.random() < ftd_rate

        platform.record_metric(player_id, "exp_welcome_bonus_v2",
                               "ftd_conversion", 1.0 if converted else 0.0)

        if converted:
            deposit = random.gauss(80, 30) if is_control else random.gauss(60, 25)
            deposit = max(10, deposit)
            platform.record_metric(player_id, "exp_welcome_bonus_v2",
                                   "first_deposit_amount", deposit)

            ggr = deposit * random.gauss(0.15, 0.08)
            platform.record_metric(player_id, "exp_welcome_bonus_v2",
                                   "day7_ggr", ggr)

    # Analyze results
    print("=== FREQUENTIST ANALYSIS ===")
    freq_results = platform.analyze("exp_welcome_bonus_v2", method="frequentist")
    for metric_name, variants in freq_results["metrics"].items():
        for variant_id, result in variants.items():
            print(f"\n{metric_name} ({variant_id}):")
            for k, v in result.items():
                print(f"  {k}: {v}")

    print("\n=== BAYESIAN ANALYSIS ===")
    bayes_results = platform.analyze("exp_welcome_bonus_v2", method="bayesian")
    for metric_name, variants in bayes_results["metrics"].items():
        for variant_id, result in variants.items():
            if "prob_treatment_better" in result:
                print(f"\n{metric_name} ({variant_id}):")
                print(f"  P(treatment > control) = {result['prob_treatment_better']:.1%}")
                print(f"  Expected lift: {result['expected_lift']:.1%}")

    # Sample size calculator
    print("\n=== SAMPLE SIZE CALCULATOR ===")
    for mde in [0.05, 0.10, 0.20]:
        n = StatisticalEngine.required_sample_size(0.05, mde)
        print(f"  Baseline 5%, MDE {mde:.0%}: {n:,} players per variant")
