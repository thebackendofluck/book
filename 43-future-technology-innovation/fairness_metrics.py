# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
fairness_metrics.py -- Statistical fairness computations for EU AI Act compliance.

Pure-function library with no side effects. All functions operate on numpy arrays
or lists of binary labels/predictions. Designed to be imported by bias_audit.py
and any other module that needs fairness computations.

Metrics implemented:
  - Demographic Parity Difference (target < 0.05 for high-risk)
  - Equalized Odds Difference     (target < 0.10 for high-risk)
  - Predictive Parity Difference  (target < 0.05 for high-risk)

Formulas from Chapter 43b: AI Governance for iGaming Platforms under the EU AI Act
Script reference: new-platform/scripts/ai-governance/fairness_metrics.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GroupMetrics:
    """Confusion-matrix-derived metrics for a single demographic group."""
    group_name: str
    group_value: str
    n: int                          # total samples in group
    n_positive: int                 # ground-truth positives
    n_predicted_positive: int       # predicted positives (regardless of truth)
    tp: int                         # true positives
    fp: int                         # false positives
    tn: int                         # true negatives
    fn: int                         # false negatives

    @property
    def positive_rate(self) -> float:
        """P(Y_hat=1 | group) — used for demographic parity."""
        return self.n_predicted_positive / self.n if self.n > 0 else 0.0

    @property
    def tpr(self) -> float:
        """True positive rate (recall, sensitivity)."""
        return self.tp / self.n_positive if self.n_positive > 0 else 0.0

    @property
    def fpr(self) -> float:
        """False positive rate."""
        n_negative = self.n - self.n_positive
        return self.fp / n_negative if n_negative > 0 else 0.0

    @property
    def ppv(self) -> float:
        """Positive predictive value (precision) — used for predictive parity."""
        return self.tp / self.n_predicted_positive if self.n_predicted_positive > 0 else 0.0


@dataclass
class FairnessResult:
    """
    Result of a single pairwise fairness comparison between two groups.

    For multi-group attributes (age, jurisdiction), multiple pairwise comparisons
    are performed and the worst-case difference is reported.
    """
    metric_name: str                    # 'demographic_parity', 'equalized_odds', etc.
    attribute_name: str                 # 'gender', 'age_group', 'jurisdiction'
    group_a: str                        # reference group value
    group_b: str                        # comparison group value
    value_a: float                      # metric value for group A
    value_b: float                      # metric value for group B
    difference: float                   # |value_a - value_b|
    threshold: float                    # configured pass/fail threshold
    passed: bool                        # difference < threshold


@dataclass
class AttributeFairnessReport:
    """
    Aggregated fairness results for a single protected attribute (e.g., 'gender').

    Reports the worst-case pairwise comparison across all group pairs.
    """
    attribute_name: str
    n_groups: int
    demographic_parity: FairnessResult
    equalized_odds: FairnessResult
    predictive_parity: FairnessResult
    group_metrics: list[GroupMetrics] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return (
            self.demographic_parity.passed
            and self.equalized_odds.passed
            and self.predictive_parity.passed
        )

    @property
    def failed_metrics(self) -> list[str]:
        failed = []
        if not self.demographic_parity.passed:
            failed.append("demographic_parity")
        if not self.equalized_odds.passed:
            failed.append("equalized_odds")
        if not self.predictive_parity.passed:
            failed.append("predictive_parity")
        return failed


# ---------------------------------------------------------------------------
# Low-level metric functions (pure, no side effects)
# ---------------------------------------------------------------------------

def compute_group_metrics(
    group_name: str,
    group_value: str,
    labels: Sequence[int],
    predictions: Sequence[int],
) -> GroupMetrics:
    """
    Compute confusion matrix components for a single group.

    Args:
        group_name:  Attribute name (e.g., 'gender')
        group_value: Group label (e.g., 'F')
        labels:      Ground-truth binary labels (0 or 1)
        predictions: Binary predictions (0 or 1)

    Returns:
        GroupMetrics dataclass with all confusion matrix components.
    """
    n = len(labels)
    tp = fp = tn = fn = 0
    for y_true, y_pred in zip(labels, predictions):
        if y_true == 1 and y_pred == 1:
            tp += 1
        elif y_true == 0 and y_pred == 1:
            fp += 1
        elif y_true == 0 and y_pred == 0:
            tn += 1
        else:
            fn += 1

    return GroupMetrics(
        group_name=group_name,
        group_value=group_value,
        n=n,
        n_positive=tp + fn,
        n_predicted_positive=tp + fp,
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
    )


def demographic_parity(
    group_a: GroupMetrics,
    group_b: GroupMetrics,
    threshold: float = 0.05,
) -> FairnessResult:
    """
    Compute demographic parity difference between two groups.

    Demographic Parity Difference = |P(Y_hat=1 | G=a) - P(Y_hat=1 | G=b)|
    Target for high-risk iGaming AI: < 0.05

    A model satisfies demographic parity if the rate of positive predictions
    is similar across demographic groups, regardless of the actual label
    distribution.

    Args:
        group_a:   Metrics for the reference group
        group_b:   Metrics for the comparison group
        threshold: Pass/fail threshold (default 0.05 per EU AI Act guidance)

    Returns:
        FairnessResult with the computed difference and pass/fail status.
    """
    diff = abs(group_a.positive_rate - group_b.positive_rate)
    return FairnessResult(
        metric_name="demographic_parity",
        attribute_name=group_a.group_name,
        group_a=group_a.group_value,
        group_b=group_b.group_value,
        value_a=group_a.positive_rate,
        value_b=group_b.positive_rate,
        difference=diff,
        threshold=threshold,
        passed=diff < threshold,
    )


def equalized_odds(
    group_a: GroupMetrics,
    group_b: GroupMetrics,
    threshold: float = 0.10,
) -> FairnessResult:
    """
    Compute equalized odds difference between two groups.

    Equalized Odds Difference = max(|TPR_a - TPR_b|, |FPR_a - FPR_b|)
    Target for high-risk iGaming AI: < 0.10

    A model satisfies equalized odds if both the true positive rate AND the
    false positive rate are similar across groups. Equalized odds is strictly
    stronger than just equalizing TPR (equal opportunity) because it also
    constrains FPR.

    Args:
        group_a:   Metrics for the reference group
        group_b:   Metrics for the comparison group
        threshold: Pass/fail threshold (default 0.10 per EU AI Act guidance)

    Returns:
        FairnessResult with the worst-case difference (max of TPR/FPR diff).
    """
    tpr_diff = abs(group_a.tpr - group_b.tpr)
    fpr_diff = abs(group_a.fpr - group_b.fpr)
    diff = max(tpr_diff, fpr_diff)

    return FairnessResult(
        metric_name="equalized_odds",
        attribute_name=group_a.group_name,
        group_a=group_a.group_value,
        group_b=group_b.group_value,
        value_a=group_a.tpr,   # report TPR as the primary value
        value_b=group_b.tpr,
        difference=diff,
        threshold=threshold,
        passed=diff < threshold,
    )


def predictive_parity(
    group_a: GroupMetrics,
    group_b: GroupMetrics,
    threshold: float = 0.05,
) -> FairnessResult:
    """
    Compute predictive parity (precision parity) between two groups.

    Predictive Parity Difference = |PPV_a - PPV_b|
    Target for high-risk iGaming AI: < 0.05

    A model satisfies predictive parity if, when it predicts a positive outcome
    (e.g., "this transaction is suspicious"), the actual rate of true positives
    is similar across demographic groups. This ensures the model is equally
    reliable across groups -- a prediction of 'fraud' carries the same weight
    regardless of which demographic group the player belongs to.

    Args:
        group_a:   Metrics for the reference group
        group_b:   Metrics for the comparison group
        threshold: Pass/fail threshold (default 0.05 per EU AI Act guidance)

    Returns:
        FairnessResult with the PPV difference and pass/fail status.
    """
    diff = abs(group_a.ppv - group_b.ppv)
    return FairnessResult(
        metric_name="predictive_parity",
        attribute_name=group_a.group_name,
        group_a=group_a.group_value,
        group_b=group_b.group_value,
        value_a=group_a.ppv,
        value_b=group_b.ppv,
        difference=diff,
        threshold=threshold,
        passed=diff < threshold,
    )


# ---------------------------------------------------------------------------
# Multi-group aggregation (worst-case pairwise)
# ---------------------------------------------------------------------------

def worst_case_pairwise(
    group_metrics_list: list[GroupMetrics],
    metric_fn: object,  # Callable[[GroupMetrics, GroupMetrics, float], FairnessResult]
    threshold: float,
) -> FairnessResult:
    """
    Find the worst-case pairwise comparison across all group pairs.

    For an attribute with k groups, there are k*(k-1)/2 pairwise comparisons.
    Returns the comparison with the largest difference (most unfair pair).

    Args:
        group_metrics_list: All groups for one protected attribute
        metric_fn:          One of: demographic_parity, equalized_odds, predictive_parity
        threshold:          Pass/fail threshold

    Returns:
        FairnessResult for the worst-case group pair.

    Raises:
        ValueError: If fewer than 2 groups are provided.
    """
    if len(group_metrics_list) < 2:
        raise ValueError("Need at least 2 groups for pairwise comparison")

    worst: FairnessResult | None = None
    for i, gm_a in enumerate(group_metrics_list):
        for gm_b in group_metrics_list[i + 1:]:
            result = metric_fn(gm_a, gm_b, threshold)  # type: ignore[operator]
            if worst is None or result.difference > worst.difference:
                worst = result

    assert worst is not None  # guaranteed by len check above
    return worst


def compute_attribute_report(
    attribute_name: str,
    groups: dict[str, tuple[list[int], list[int]]],
    thresholds: dict[str, float] | None = None,
) -> AttributeFairnessReport:
    """
    Compute a complete fairness report for a single protected attribute.

    Args:
        attribute_name: Name of the protected attribute (e.g., 'gender')
        groups:         Mapping of group_value -> (labels, predictions)
                        e.g., {'M': ([0,1,...], [0,0,...]), 'F': ([1,0,...], [1,1,...])}
        thresholds:     Override thresholds for each metric. Defaults:
                        {'demographic_parity': 0.05, 'equalized_odds': 0.10,
                         'predictive_parity': 0.05}

    Returns:
        AttributeFairnessReport with worst-case pairwise results for all three metrics.
    """
    effective_thresholds = {
        "demographic_parity": 0.05,
        "equalized_odds": 0.10,
        "predictive_parity": 0.05,
    }
    if thresholds:
        effective_thresholds.update(thresholds)

    group_metrics_list = [
        compute_group_metrics(attribute_name, group_val, labels, preds)
        for group_val, (labels, preds) in groups.items()
    ]

    dp = worst_case_pairwise(
        group_metrics_list,
        demographic_parity,
        effective_thresholds["demographic_parity"],
    )
    eo = worst_case_pairwise(
        group_metrics_list,
        equalized_odds,
        effective_thresholds["equalized_odds"],
    )
    pp = worst_case_pairwise(
        group_metrics_list,
        predictive_parity,
        effective_thresholds["predictive_parity"],
    )

    return AttributeFairnessReport(
        attribute_name=attribute_name,
        n_groups=len(group_metrics_list),
        demographic_parity=dp,
        equalized_odds=eo,
        predictive_parity=pp,
        group_metrics=group_metrics_list,
    )


# ---------------------------------------------------------------------------
# Population Stability Index (for drift detection)
# ---------------------------------------------------------------------------

def population_stability_index(
    reference: Sequence[float],
    current: Sequence[float],
    n_bins: int = 10,
) -> float:
    """
    Compute the Population Stability Index (PSI) between two score distributions.

    PSI is used in continuous monitoring (Article 15) to detect concept drift.
    When a model's score distribution shifts significantly from its training
    distribution, it may be operating outside its validated envelope.

    Interpretation:
        PSI < 0.10:  No significant change
        PSI 0.10-0.25: Moderate change, investigate
        PSI > 0.25:  Significant shift, consider retraining

    Args:
        reference: Model scores from the reference period (training or baseline)
        current:   Model scores from the current production period
        n_bins:    Number of bins for histogram comparison (default 10)

    Returns:
        PSI value (non-negative float)
    """
    if not reference or not current:
        return 0.0

    # Build bins from the combined range
    all_scores = list(reference) + list(current)
    min_score = min(all_scores)
    max_score = max(all_scores)
    if min_score == max_score:
        return 0.0

    bin_width = (max_score - min_score) / n_bins
    bins = [min_score + i * bin_width for i in range(n_bins + 1)]
    bins[-1] = max_score + 1e-9  # ensure max value included

    def _bin_proportions(scores: Sequence[float]) -> list[float]:
        counts = [0] * n_bins
        for s in scores:
            for j in range(n_bins):
                if bins[j] <= s < bins[j + 1]:
                    counts[j] += 1
                    break
        total = sum(counts)
        # Clip to small epsilon to avoid log(0)
        return [max(c / total, 1e-10) for c in counts]

    ref_props = _bin_proportions(reference)
    cur_props = _bin_proportions(current)

    psi = sum(
        (cur - ref) * math.log(cur / ref)
        for ref, cur in zip(ref_props, cur_props)
    )
    return psi
