#!/usr/bin/env python3
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
Fairness metrics for iGaming AI model evaluation.

Implements demographic parity, equalized odds, and predictive parity
as required by the EU AI Act (Regulation 2024/1689) for high-risk AI
systems deployed in casino platforms.

These metrics assess whether AI models (AML scoring, fraud detection,
responsible gaming triggers) produce equitable outcomes across
protected demographic groups.

Usage:
    from fairness_metrics import demographic_parity, equalized_odds, predictive_parity

    dp = demographic_parity(predictions, group_labels)
    eo = equalized_odds(predictions, true_labels, group_labels)
    pp = predictive_parity(predictions, true_labels, group_labels)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GroupMetrics:
    """Per-group classification metrics."""

    group: str
    n: int
    positive_rate: float
    true_positive_rate: float
    false_positive_rate: float
    positive_predictive_value: float


@dataclass
class FairnessResult:
    """Result of a fairness metric computation."""

    metric_name: str
    value: float
    threshold: float
    passed: bool
    group_details: dict[str, Any] = field(default_factory=dict)
    description: str = ""



    @property
    def groups(self) -> dict[str, Any]:
        """Alias for group_details for backward compatibility."""
        # Transform group_details to match test expectations
        # Group names get tpr_X, tpr_Y format keys
        result = {}
        for group_name, metrics in self.group_details.items():
            for key, value in metrics.items():
                if key in ('tpr', 'fpr', 'ppv'):
                    result[f"{key}_{group_name}"] = value
        return result if result else self.group_details

def _validate_inputs(
    predictions: list[int],
    group_labels: list[str],
    true_labels: list[int] | None = None,
) -> None:
    """Validate that all input arrays have the same length and valid values."""
    n = len(predictions)
    if len(group_labels) != n:
        raise ValueError(
            f"predictions ({n}) and group_labels ({len(group_labels)}) must have same length"
        )
    if true_labels is not None and len(true_labels) != n:
        raise ValueError(
            f"predictions ({n}) and true_labels ({len(true_labels)}) must have same length"
        )
    pred_values = set(predictions)
    if not pred_values.issubset({0, 1}):
        raise ValueError(f"predictions must be binary (0/1), got values: {pred_values}")
    if true_labels is not None:
        label_values = set(true_labels)
        if not label_values.issubset({0, 1}):
            raise ValueError(f"true_labels must be binary (0/1), got values: {label_values}")


def _group_indices(group_labels: list[str]) -> dict[str, list[int]]:
    """Map group names to their indices in the array."""
    groups: dict[str, list[int]] = {}
    for i, g in enumerate(group_labels):
        groups.setdefault(g, []).append(i)
    return groups


def demographic_parity(
    predictions: list[int],
    group_labels: list[str],
    threshold: float = 0.05,
) -> FairnessResult:
    """Compute demographic parity difference.

    Measures whether the positive prediction rate is equal across groups.
    A fair model should have similar rates of flagging, restricting, or
    intervening regardless of demographic group.

    Demographic Parity Difference = max |P(Y_hat=1|G=a) - P(Y_hat=1|G=b)|
                                    over all pairs (a, b)

    Args:
        predictions: Binary predictions (0 or 1) for each sample.
        group_labels: Group membership label for each sample.
        threshold: Maximum acceptable difference (default 0.05 per EU AI Act guidance).

    Returns:
        FairnessResult with the maximum parity difference.
    """
    _validate_inputs(predictions, group_labels)
    groups = _group_indices(group_labels)

    rates: dict[str, float] = {}
    group_detail: dict[str, Any] = {}

    for group_name, indices in groups.items():
        group_preds = [predictions[i] for i in indices]
        rate = sum(group_preds) / len(group_preds) if group_preds else 0.0
        rates[group_name] = rate
        group_detail[group_name] = {
            "n": len(indices),
            "positive_rate": round(rate, 4),
            "positive_count": sum(group_preds),
        }

    all_rates = list(rates.values())
    if len(all_rates) < 2:
        max_diff = 0.0
    else:
        max_diff = max(all_rates) - min(all_rates)

    return FairnessResult(
        metric_name="demographic_parity",
        value=round(max_diff, 4),
        threshold=threshold,
        passed=max_diff <= threshold,
        group_details=group_detail,
        description=(
            f"Maximum positive rate difference across groups: {max_diff:.4f} "
            f"(threshold: {threshold}). "
            f"{'PASS' if max_diff <= threshold else 'FAIL'}"
        ),
    )


def equalized_odds(
    predictions: list[int],
    true_labels: list[int],
    group_labels: list[str],
    threshold: float = 0.10,
) -> FairnessResult:
    """Compute equalized odds difference.

    Measures whether TPR and FPR are equal across groups. A fair model
    should catch fraud (or trigger interventions) at similar rates across
    demographics, and should have similar false alarm rates.

    Equalized Odds Diff = max(|TPR_a - TPR_b|, |FPR_a - FPR_b|)
                          over all pairs (a, b)

    Args:
        predictions: Binary predictions (0 or 1).
        true_labels: Ground truth binary labels (0 or 1).
        group_labels: Group membership label for each sample.
        threshold: Maximum acceptable difference (default 0.10).

    Returns:
        FairnessResult with the maximum equalized odds difference.
    """
    _validate_inputs(predictions, group_labels, true_labels)
    groups = _group_indices(group_labels)

    tpr_by_group: dict[str, float] = {}
    fpr_by_group: dict[str, float] = {}
    group_detail: dict[str, Any] = {}

    for group_name, indices in groups.items():
        g_preds = [predictions[i] for i in indices]
        g_labels = [true_labels[i] for i in indices]

        tp = sum(1 for p, l in zip(g_preds, g_labels) if p == 1 and l == 1)
        fn = sum(1 for p, l in zip(g_preds, g_labels) if p == 0 and l == 1)
        fp = sum(1 for p, l in zip(g_preds, g_labels) if p == 1 and l == 0)
        tn = sum(1 for p, l in zip(g_preds, g_labels) if p == 0 and l == 0)

        tpr = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

        tpr_by_group[group_name] = tpr
        fpr_by_group[group_name] = fpr
        group_detail[group_name] = {
            "n": len(indices),
            "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "tpr": round(tpr, 4),
            "fpr": round(fpr, 4),
        }

    tpr_vals = list(tpr_by_group.values())
    fpr_vals = list(fpr_by_group.values())

    if len(tpr_vals) < 2:
        max_diff = 0.0
    else:
        tpr_diff = max(tpr_vals) - min(tpr_vals)
        fpr_diff = max(fpr_vals) - min(fpr_vals)
        max_diff = max(tpr_diff, fpr_diff)

    return FairnessResult(
        metric_name="equalized_odds",
        value=round(max_diff, 4),
        threshold=threshold,
        passed=max_diff <= threshold,
        group_details=group_detail,
        description=(
            f"Maximum TPR/FPR difference across groups: {max_diff:.4f} "
            f"(threshold: {threshold}). "
            f"{'PASS' if max_diff <= threshold else 'FAIL'}"
        ),
    )


def predictive_parity(
    predictions: list[int],
    true_labels: list[int],
    group_labels: list[str],
    threshold: float = 0.05,
) -> FairnessResult:
    """Compute predictive parity difference.

    Measures whether positive predictive value (precision) is equal across
    groups. When the model flags a player, the actual fraud rate should be
    similar regardless of demographic group.

    Predictive Parity Diff = max |PPV_a - PPV_b| over all pairs (a, b)

    Args:
        predictions: Binary predictions (0 or 1).
        true_labels: Ground truth binary labels (0 or 1).
        group_labels: Group membership label for each sample.
        threshold: Maximum acceptable difference (default 0.05).

    Returns:
        FairnessResult with the maximum PPV difference.
    """
    _validate_inputs(predictions, group_labels, true_labels)
    groups = _group_indices(group_labels)

    ppv_by_group: dict[str, float] = {}
    group_detail: dict[str, Any] = {}

    for group_name, indices in groups.items():
        g_preds = [predictions[i] for i in indices]
        g_labels = [true_labels[i] for i in indices]

        tp = sum(1 for p, l in zip(g_preds, g_labels) if p == 1 and l == 1)
        fp = sum(1 for p, l in zip(g_preds, g_labels) if p == 1 and l == 0)

        ppv = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        ppv_by_group[group_name] = ppv
        group_detail[group_name] = {
            "n": len(indices),
            "predicted_positive": sum(g_preds),
            "true_positive": tp,
            "false_positive": fp,
            "ppv": round(ppv, 4),
        }

    ppv_vals = list(ppv_by_group.values())
    if len(ppv_vals) < 2:
        max_diff = 0.0
    else:
        max_diff = max(ppv_vals) - min(ppv_vals)

    return FairnessResult(
        metric_name="predictive_parity",
        value=round(max_diff, 4),
        threshold=threshold,
        passed=max_diff <= threshold,
        group_details=group_detail,
        description=(
            f"Maximum PPV difference across groups: {max_diff:.4f} "
            f"(threshold: {threshold}). "
            f"{'PASS' if max_diff <= threshold else 'FAIL'}"
        ),
    )


def compute_group_metrics(
    predictions: list[int],
    true_labels: list[int],
    group_labels: list[str],
) -> list[GroupMetrics]:
    """Compute full classification metrics per demographic group.

    Useful for detailed reporting in model cards and regulator-facing
    transparency reports.
    """
    _validate_inputs(predictions, group_labels, true_labels)
    groups = _group_indices(group_labels)
    results: list[GroupMetrics] = []

    for group_name, indices in sorted(groups.items()):
        g_preds = [predictions[i] for i in indices]
        g_labels = [true_labels[i] for i in indices]
        n = len(indices)

        tp = sum(1 for p, l in zip(g_preds, g_labels) if p == 1 and l == 1)
        fn = sum(1 for p, l in zip(g_preds, g_labels) if p == 0 and l == 1)
        fp = sum(1 for p, l in zip(g_preds, g_labels) if p == 1 and l == 0)
        tn = sum(1 for p, l in zip(g_preds, g_labels) if p == 0 and l == 0)

        positive_rate = sum(g_preds) / n if n > 0 else 0.0
        tpr = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        ppv = tp / (tp + fp) if (tp + fp) > 0 else 0.0

        results.append(GroupMetrics(
            group=group_name,
            n=n,
            positive_rate=round(positive_rate, 4),
            true_positive_rate=round(tpr, 4),
            false_positive_rate=round(fpr, 4),
            positive_predictive_value=round(ppv, 4),
        ))

    return results


def demographic_parity_difference(
    predictions: list[int],
    group_labels: list[str],
    threshold: float = 0.05,
) -> FairnessResult:
    """Compute demographic parity difference (alias for demographic_parity).
    
    Returns the scalar difference rather than the full report.
    """
    result = demographic_parity(predictions, group_labels, threshold)
    return FairnessResult(
        metric_name="demographic_parity_difference",
        value=result.value,
        threshold=result.threshold,
        passed=result.passed,
        group_details=result.group_details,
        description=result.description,
    )


def equalized_odds_difference(
    predictions: list[int],
    true_labels: list[int],
    group_labels: list[str],
    threshold: float = 0.10,
) -> FairnessResult:
    """Compute equalized odds difference (alias for equalized_odds).
    
    Returns the scalar difference rather than the full report.
    """
    result = equalized_odds(predictions, true_labels, group_labels, threshold)
    return FairnessResult(
        metric_name="equalized_odds_difference",
        value=result.value,
        threshold=result.threshold,
        passed=result.passed,
        group_details=result.group_details,
        description=result.description,
    )


def calibration_error_by_group(
    probabilities: list[float],
    true_labels: list[int],
    group_labels: list[str],
    n_bins: int = 10,
) -> FairnessResult:
    """Compute calibration error across groups.
    
    Calibration measures whether predicted probabilities match actual positive rates.
    This variant computes error per group and returns the maximum.
    
    Args:
        probabilities: Predicted probabilities (0.0 to 1.0) for each sample.
        true_labels: Ground truth binary labels (0 or 1).
        group_labels: Group membership label for each sample.
        n_bins: Number of probability bins for calibration analysis.
    
    Returns:
        FairnessResult with maximum calibration error across groups.
    """
    _validate_inputs([int(p > 0.5) for p in probabilities], group_labels, true_labels)
    groups = _group_indices(group_labels)
    
    group_detail: dict[str, Any] = {}
    max_error = 0.0
    
    for group_name, indices in groups.items():
        g_probs = [probabilities[i] for i in indices]
        g_labels = [true_labels[i] for i in indices]
        
        # Compute calibration error as mean absolute difference between
        # predicted probability and actual positive rate in each bin
        errors = []
        for bin_idx in range(n_bins):
            lower = bin_idx / n_bins
            upper = (bin_idx + 1) / n_bins
            in_bin = [i for i, p in enumerate(g_probs) if lower <= p < upper]
            
            if in_bin:
                avg_prob = sum(g_probs[i] for i in in_bin) / len(in_bin)
                actual_pos_rate = sum(g_labels[i] for i in in_bin) / len(in_bin)
                errors.append(abs(avg_prob - actual_pos_rate))
        
        group_error = sum(errors) / len(errors) if errors else 0.0
        max_error = max(max_error, group_error)
        group_detail[group_name] = {
            "n": len(indices),
            "calibration_error": round(group_error, 4),
        }
    
    return FairnessResult(
        metric_name="calibration_error_by_group",
        value=round(max_error, 4),
        threshold=0.15,
        passed=max_error <= 0.15,
        group_details=group_detail,
        description=(
            f"Maximum calibration error across groups: {max_error:.4f} "
            f"(threshold: 0.15). {'PASS' if max_error <= 0.15 else 'FAIL'}"
        ),
    )


def disparate_impact_ratio(
    predictions: list[int],
    group_labels: list[str],
    threshold: float = 0.80,
) -> FairnessResult:
    """Compute disparate impact ratio.
    
    Also known as the 4/5 rule, this compares selection rates across groups.
    A fair model should have a ratio >= 0.80 (or close to 1.0 for perfect parity).
    
    Disparate Impact = min(positive_rate_a / positive_rate_b) 
                       over all pairs (a, b)
    
    Args:
        predictions: Binary predictions (0 or 1) for each sample.
        group_labels: Group membership label for each sample.
        threshold: Minimum acceptable ratio (default 0.80, the 4/5 rule).
    
    Returns:
        FairnessResult with the disparate impact ratio.
    """
    _validate_inputs(predictions, group_labels)
    groups = _group_indices(group_labels)
    
    rates: dict[str, float] = {}
    group_detail: dict[str, Any] = {}
    
    for group_name, indices in groups.items():
        group_preds = [predictions[i] for i in indices]
        rate = sum(group_preds) / len(group_preds) if group_preds else 0.0
        rates[group_name] = rate
        group_detail[group_name] = {
            "n": len(indices),
            "selection_rate": round(rate, 4),
            "selected_count": sum(group_preds),
        }
    
    all_rates = list(rates.values())
    if len(all_rates) < 2:
        ratio = 1.0
    else:
        min_rate = min(all_rates)
        max_rate = max(all_rates)
        # Avoid division by zero
        ratio = min_rate / max_rate if max_rate > 0 else 1.0
    
    return FairnessResult(
        metric_name="disparate_impact_ratio",
        value=round(ratio, 4),
        threshold=threshold,
        passed=ratio >= threshold,
        group_details=group_detail,
        description=(
            f"Disparate impact ratio (4/5 rule): {ratio:.4f} "
            f"(threshold: {threshold}). "
            f"{'PASS' if ratio >= threshold else 'FAIL'}"
        ),
    )
