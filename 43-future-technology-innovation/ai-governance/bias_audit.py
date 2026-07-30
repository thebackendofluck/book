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
Bias detection and fairness audit pipeline for iGaming AI models.

Runs demographic parity, equalized odds, and predictive parity checks
across multiple protected attributes (gender, age group, jurisdiction,
payment method). Generates audit reports for EU AI Act (Regulation
2024/1689) compliance and model registry integration.

Usage:
    auditor = BiasAuditor()
    report = auditor.audit(
        model_name="aml_scoring_v3.2",
        predictions=[1, 0, 1, ...],
        true_labels=[1, 0, 0, ...],
        protected_attributes={"gender": ["M", "F", "M", ...], ...},
    )

    if not report.all_passed:
        print(report.summary())
        sys.exit(1)  # Block deployment

CLI:
    python bias_audit.py \
        --model-name aml_scoring_v3.2 \
        --predictions-file predictions.csv \
        --labels-file labels.csv \
        --protected-attrs gender,age_group,jurisdiction \
        --output-dir ./audits/
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fairness_metrics import (  # ty:ignore[unresolved-import]
    FairnessResult,
    compute_group_metrics,
    demographic_parity,
    equalized_odds,
    predictive_parity,
)


@dataclass
class FairnessReport:
    """Complete fairness audit report for a model."""

    report_id: str
    model_name: str
    audit_date: str
    sample_size: int
    protected_attributes: list[str]
    results: dict[str, dict[str, FairnessResult]]  # attr -> metric -> result
    all_passed: bool = True
    failed_metrics: list[str] = field(default_factory=list)
    affected_groups: list[str] = field(default_factory=list)
    remediation_suggestions: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable summary of the audit."""
        lines = [
            f"Fairness Audit Report: {self.model_name}",
            f"Date: {self.audit_date}",
            f"Sample size: {self.sample_size}",
            f"Protected attributes tested: {', '.join(self.protected_attributes)}",
            "",
        ]

        for attr, metrics in self.results.items():
            lines.append(f"  Attribute: {attr}")
            for metric_name, result in metrics.items():
                status = "PASS" if result.passed else "FAIL"
                lines.append(
                    f"    [{status}] {metric_name}: {result.value:.4f} "
                    f"(threshold: {result.threshold})"
                )
            lines.append("")

        if self.all_passed:
            lines.append("OVERALL: PASS -- All fairness metrics within thresholds.")
        else:
            lines.append("OVERALL: FAIL")
            lines.append(f"  Failed metrics: {', '.join(self.failed_metrics)}")
            lines.append(f"  Affected groups: {', '.join(self.affected_groups)}")
            lines.append("  Remediation suggestions:")
            for suggestion in self.remediation_suggestions:
                lines.append(f"    - {suggestion}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        results_dict: dict[str, Any] = {}
        for attr, metrics in self.results.items():
            results_dict[attr] = {}
            for metric_name, result in metrics.items():
                results_dict[attr][metric_name] = {
                    "value": result.value,
                    "threshold": result.threshold,
                    "passed": result.passed,
                    "description": result.description,
                    "group_details": result.group_details,
                }

        return {
            "report_id": self.report_id,
            "model_name": self.model_name,
            "audit_date": self.audit_date,
            "sample_size": self.sample_size,
            "protected_attributes": self.protected_attributes,
            "results": results_dict,
            "all_passed": self.all_passed,
            "failed_metrics": self.failed_metrics,
            "affected_groups": self.affected_groups,
            "remediation_suggestions": self.remediation_suggestions,
        }


class BiasAuditor:
    """Runs fairness audits on AI model predictions.

    Computes demographic parity, equalized odds, and predictive parity
    across all specified protected attributes. Generates a comprehensive
    report suitable for regulatory filing and model registry integration.
    """

    DEFAULT_THRESHOLDS: dict[str, float] = {
        "demographic_parity": 0.05,
        "equalized_odds": 0.10,
        "predictive_parity": 0.05,
    }

    def __init__(
        self,
        thresholds: dict[str, float] | None = None,
    ) -> None:
        self.thresholds = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}

    def audit(
        self,
        model_name: str,
        predictions: list[int],
        true_labels: list[int],
        protected_attributes: dict[str, list[str]],
        thresholds: dict[str, float] | None = None,
    ) -> FairnessReport:
        """Run a full fairness audit across all protected attributes.

        Args:
            model_name: Name of the model being audited.
            predictions: Binary predictions (0/1) from the model.
            true_labels: Ground truth binary labels (0/1).
            protected_attributes: Dict mapping attribute name to per-sample
                group labels. Example: {"gender": ["M", "F", "M", ...]}
            thresholds: Optional override for fairness thresholds.

        Returns:
            FairnessReport with all metric results and pass/fail status.
        """
        active_thresholds = {**self.thresholds, **(thresholds or {})}
        all_results: dict[str, dict[str, FairnessResult]] = {}
        all_passed = True
        failed_metrics: list[str] = []
        affected_groups: list[str] = []

        for attr_name, group_labels in protected_attributes.items():
            attr_results: dict[str, FairnessResult] = {}

            # Demographic parity
            dp = demographic_parity(
                predictions, group_labels,
                threshold=active_thresholds["demographic_parity"],
            )
            attr_results["demographic_parity"] = dp
            if not dp.passed:
                all_passed = False
                failed_metrics.append(f"{attr_name}/demographic_parity")
                # Identify worst groups
                rates = {g: d["positive_rate"] for g, d in dp.group_details.items()}
                max_g = max(rates, key=rates.get)  # type: ignore[arg-type]
                min_g = min(rates, key=rates.get)  # type: ignore[arg-type]
                affected_groups.extend([f"{attr_name}={max_g}", f"{attr_name}={min_g}"])

            # Equalized odds
            eo = equalized_odds(
                predictions, true_labels, group_labels,
                threshold=active_thresholds["equalized_odds"],
            )
            attr_results["equalized_odds"] = eo
            if not eo.passed:
                all_passed = False
                failed_metrics.append(f"{attr_name}/equalized_odds")

            # Predictive parity
            pp = predictive_parity(
                predictions, true_labels, group_labels,
                threshold=active_thresholds["predictive_parity"],
            )
            attr_results["predictive_parity"] = pp
            if not pp.passed:
                all_passed = False
                failed_metrics.append(f"{attr_name}/predictive_parity")

            all_results[attr_name] = attr_results

        # Generate remediation suggestions
        remediation: list[str] = []
        if not all_passed:
            remediation.append(
                "Review training data for representation bias in affected groups"
            )
            remediation.append(
                "Consider re-sampling or re-weighting training data to improve balance"
            )
            remediation.append(
                "Evaluate whether protected attributes or proxies are leaking into features"
            )
            remediation.append(
                "Run SHAP analysis on affected groups to identify discriminatory features"
            )
            remediation.append(
                "Consider threshold adjustment per group (post-processing calibration)"
            )

        # De-duplicate
        affected_groups = sorted(set(affected_groups))

        return FairnessReport(
            report_id=str(uuid.uuid4()),
            model_name=model_name,
            audit_date=datetime.now(timezone.utc).isoformat(),
            sample_size=len(predictions),
            protected_attributes=list(protected_attributes.keys()),
            results=all_results,
            all_passed=all_passed,
            failed_metrics=failed_metrics,
            affected_groups=affected_groups,
            remediation_suggestions=remediation,
        )


def _load_csv_column(filepath: str, column: int = 0) -> list[str]:
    """Load a single column from a CSV file."""
    values: list[str] = []
    with open(filepath) as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row in reader:
            if row:
                values.append(row[column].strip())
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description="Bias Audit Pipeline")
    parser.add_argument("--model-name", required=True, help="Model name for the report")
    parser.add_argument("--predictions-file", required=True, help="CSV with predictions column")
    parser.add_argument("--labels-file", required=True, help="CSV with true labels column")
    parser.add_argument("--protected-attrs", required=True,
                        help="Comma-separated attribute names (columns in predictions CSV)")
    parser.add_argument("--output-dir", default="./audits", help="Output directory for reports")
    parser.add_argument("--dp-threshold", type=float, default=0.05)
    parser.add_argument("--eo-threshold", type=float, default=0.10)
    parser.add_argument("--pp-threshold", type=float, default=0.05)
    args = parser.parse_args()

    # Load data
    predictions_raw = _load_csv_column(args.predictions_file, 0)
    predictions = [int(v) for v in predictions_raw]
    labels_raw = _load_csv_column(args.labels_file, 0)
    true_labels = [int(v) for v in labels_raw]

    attr_names = [a.strip() for a in args.protected_attrs.split(",")]

    # Load protected attributes from predictions file (columns after prediction)
    protected_attributes: dict[str, list[str]] = {name: [] for name in attr_names}
    with open(args.predictions_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            for attr in attr_names:
                protected_attributes[attr].append(row.get(attr, "unknown"))

    # Run audit
    auditor = BiasAuditor(thresholds={
        "demographic_parity": args.dp_threshold,
        "equalized_odds": args.eo_threshold,
        "predictive_parity": args.pp_threshold,
    })

    report = auditor.audit(
        model_name=args.model_name,
        predictions=predictions,
        true_labels=true_labels,
        protected_attributes=protected_attributes,
    )

    # Output
    print(report.summary())

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"bias_audit_{args.model_name}_{report.report_id[:8]}.json"
    with open(output_file, "w") as f:
        json.dump(report.to_dict(), f, indent=2)
    print(f"\nReport saved to: {output_file}")

    if not report.all_passed:
        print("\nBias audit FAILED. Deployment should be blocked.")
        sys.exit(1)
    else:
        print("\nBias audit PASSED.")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Public / governance compat layer
# ---------------------------------------------------------------------------
#
# The governance chapter tests use a single-protected-attribute API that
# returns a flat list of metric results with overall pass/fail flagging.
# Rather than carry two parallel classes, we extend `BiasAuditor` with an
# alternate `audit()` signature and a small `SimpleBiasReport` dataclass.
#
# Detection rules:
#   * demographic_parity_difference: |max rate - min rate| <= disparity_threshold
#   * disparate_impact_ratio:        min(pos_rate) / max(pos_rate) >= 1 - disparity_threshold
#   * equalized_odds_difference:     requires labels
#   * calibration_error_by_group:    requires probabilities
#
# With just `predictions` and `groups`, only the first two are runnable
# (hence `len(metric_results) == 2` in the no-labels test). With labels
# + probabilities all four are computed.

try:  # numpy is already required by the main audit path
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None  # type: ignore[assignment]


@dataclass
class MetricResult:
    """One fairness metric outcome in a simple audit report."""

    metric_name: str
    value: float
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "value": self.value,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class SimpleBiasReport:
    """Flat single-attribute bias audit report (governance API)."""

    model_name: str
    model_version: str
    audit_date: str
    sample_size: int
    metric_results: list[MetricResult]
    overall_pass: bool
    flagged_metrics: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "audit_date": self.audit_date,
            "sample_size": self.sample_size,
            "metric_results": [m.to_dict() for m in self.metric_results],
            "overall_pass": self.overall_pass,
            "flagged_metrics": list(self.flagged_metrics),
        }


def _bias_auditor_new_init(
    self: BiasAuditor,
    thresholds: dict[str, float] | None = None,
    disparity_threshold: float | None = None,
) -> None:
    """Init that accepts both the old thresholds dict and the new single
    `disparity_threshold` used by the governance tests."""
    self.thresholds = {**BiasAuditor.DEFAULT_THRESHOLDS, **(thresholds or {})}
    self.disparity_threshold = (
        disparity_threshold if disparity_threshold is not None else 0.10
    )


BiasAuditor.__init__ = _bias_auditor_new_init  # type: ignore[method-assign]


# Capture the original, attribute-dict-based audit() so we can still
# delegate into it when the caller uses the old signature.
_original_audit = BiasAuditor.audit


def _audit(
    self: BiasAuditor,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Dual-signature audit entry point.

    Dispatches on the keyword set. The governance API uses
    `groups=...` while the original multi-attribute API uses
    `protected_attributes=...`.
    """
    if "groups" in kwargs and "protected_attributes" not in kwargs:
        return _audit_simple(self, **kwargs)
    return _original_audit(self, *args, **kwargs)


def _group_rate(predictions: Any, groups: Any) -> dict[str, float]:
    """Positive-class rate per group, computed via numpy for speed."""
    assert _np is not None
    preds = _np.asarray(predictions)
    grp = _np.asarray(groups)
    rates: dict[str, float] = {}
    for g in _np.unique(grp):
        mask = grp == g
        n = int(mask.sum())
        if n == 0:
            continue
        rates[str(g)] = float(preds[mask].mean())
    return rates


def _audit_simple(
    self: BiasAuditor,
    predictions: Any,
    groups: Any,
    labels: Any | None = None,
    probabilities: Any | None = None,
    model_name: str = "",
    model_version: str = "1.0",
) -> SimpleBiasReport:
    """Compute the governance API's flat bias report.

    Always runs demographic parity and disparate impact. Adds
    equalized odds if `labels` is provided and calibration error if
    `probabilities` is provided -- so a minimal call with just
    predictions+groups yields 2 metrics, and a full call yields 4.
    """
    assert _np is not None, "numpy is required for SimpleBiasReport"

    preds_arr = _np.asarray(predictions)
    n = int(preds_arr.shape[0])
    metric_results: list[MetricResult] = []

    rates = _group_rate(preds_arr, groups)
    dp_value = float(max(rates.values()) - min(rates.values())) if rates else 0.0
    dp_passed = dp_value <= self.disparity_threshold
    metric_results.append(MetricResult(
        metric_name="demographic_parity_difference",
        value=dp_value,
        passed=dp_passed,
        detail={"group_rates": rates},
    ))

    max_rate = max(rates.values()) if rates else 0.0
    min_rate = min(rates.values()) if rates else 0.0
    di_value = float(min_rate / max_rate) if max_rate > 0 else 1.0
    # Four-fifths rule: ratios below 0.80 indicate disparate impact.
    di_passed = di_value >= 0.80
    metric_results.append(MetricResult(
        metric_name="disparate_impact_ratio",
        value=di_value,
        passed=di_passed,
        detail={"group_rates": rates},
    ))

    if labels is not None:
        labels_arr = _np.asarray(labels)
        groups_arr = _np.asarray(groups)
        tpr: dict[str, float] = {}
        fpr: dict[str, float] = {}
        for g in _np.unique(groups_arr):
            mask = groups_arr == g
            y = labels_arr[mask]
            p = preds_arr[mask]
            pos = int((y == 1).sum())
            neg = int((y == 0).sum())
            tpr[str(g)] = float(((p == 1) & (y == 1)).sum() / pos) if pos else 0.0
            fpr[str(g)] = float(((p == 1) & (y == 0)).sum() / neg) if neg else 0.0
        tpr_diff = (max(tpr.values()) - min(tpr.values())) if tpr else 0.0
        fpr_diff = (max(fpr.values()) - min(fpr.values())) if fpr else 0.0
        eo_value = float(max(tpr_diff, fpr_diff))
        eo_passed = eo_value <= self.disparity_threshold
        metric_results.append(MetricResult(
            metric_name="equalized_odds_difference",
            value=eo_value,
            passed=eo_passed,
            detail={"tpr": tpr, "fpr": fpr},
        ))

    if probabilities is not None:
        # Brier-style per-group calibration error, then take the spread
        # across groups as the headline number.
        probs_arr = _np.asarray(probabilities, dtype=float)
        labels_for_cal = _np.asarray(
            labels if labels is not None else preds_arr, dtype=float,
        )
        groups_arr = _np.asarray(groups)
        per_group: dict[str, float] = {}
        for g in _np.unique(groups_arr):
            mask = groups_arr == g
            if mask.sum() == 0:
                continue
            per_group[str(g)] = float(
                _np.abs(probs_arr[mask] - labels_for_cal[mask]).mean()
            )
        cal_value = (
            float(max(per_group.values()) - min(per_group.values()))
            if per_group else 0.0
        )
        cal_passed = cal_value <= self.disparity_threshold
        metric_results.append(MetricResult(
            metric_name="calibration_error_by_group",
            value=cal_value,
            passed=cal_passed,
            detail={"per_group": per_group},
        ))

    flagged = [m.metric_name for m in metric_results if not m.passed]
    return SimpleBiasReport(
        model_name=model_name,
        model_version=model_version,
        audit_date=datetime.now(timezone.utc).isoformat(),
        sample_size=n,
        metric_results=metric_results,
        overall_pass=not flagged,
        flagged_metrics=flagged,
    )


BiasAuditor.audit = _audit  # type: ignore[method-assign]


def _report_to_json(self: BiasAuditor, report: Any) -> str:
    """Serialize either a SimpleBiasReport or a FairnessReport."""
    if isinstance(report, SimpleBiasReport):
        return json.dumps(report.to_dict(), indent=2, default=str)
    if hasattr(report, "to_dict"):
        return json.dumps(report.to_dict(), indent=2, default=str)
    return json.dumps(report, indent=2, default=str)


BiasAuditor.report_to_json = _report_to_json  # type: ignore[method-assign]
