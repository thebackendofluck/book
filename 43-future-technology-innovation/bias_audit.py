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
bias_audit.py -- Bias detection pipeline for EU AI Act compliance.

Orchestrates the full bias audit workflow:
  1. Accept model predictions + ground-truth labels + protected attribute arrays
  2. Compute fairness metrics via fairness_metrics.py (pure computation layer)
  3. Check results against configurable thresholds (defaults from chapter 43b)
  4. Generate a structured FairnessReport with pass/fail status, affected groups,
     and remediation suggestions
  5. Optionally persist results to the ModelRegistry

Usage::

    from bias_audit import BiasAuditor

    auditor = BiasAuditor()
    results = auditor.audit(
        model_name="responsible_gaming_trigger_v2.1",
        predictions=rg_predictions,
        labels=actual_problem_gambling_labels,
        protected_attributes={
            "age_group": player_age_groups,
            "gender": player_genders,
            "jurisdiction": player_jurisdictions,
        },
        thresholds={
            "demographic_parity": 0.05,
            "equalized_odds": 0.10,
            "predictive_parity": 0.05,
        },
    )

    if not results.all_passed:
        print(f"BIAS DETECTED in {results.failed_metrics}")
        print(f"Affected groups: {results.affected_groups}")
        print(f"Remediation: {results.remediation_suggestions}")
        sys.exit(1)

Chapter 43b: AI Governance for iGaming Platforms under the EU AI Act
Script reference: new-platform/scripts/ai-governance/bias_audit.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from fairness_metrics import (
    AttributeFairnessReport,
    compute_attribute_report,
)


# ---------------------------------------------------------------------------
# Report data structures
# ---------------------------------------------------------------------------

@dataclass
class FairnessReport:
    """
    Top-level bias audit result for a model across all protected attributes.

    This is the object returned by BiasAuditor.audit(). It aggregates per-attribute
    results and provides convenience properties used by deployment gate checks.
    """
    model_name: str
    audit_timestamp: str
    auditor: str
    attribute_reports: dict[str, AttributeFairnessReport]
    thresholds: dict[str, float]

    # Remediation metadata (populated by _generate_remediation)
    remediation_suggestions: list[str] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        """True only if every attribute report passes all three metrics."""
        return all(r.all_passed for r in self.attribute_reports.values())

    @property
    def failed_metrics(self) -> list[str]:
        """List of 'attribute:metric' strings that failed."""
        failed = []
        for attr_name, report in self.attribute_reports.items():
            for metric in report.failed_metrics:
                failed.append(f"{attr_name}:{metric}")
        return failed

    @property
    def affected_groups(self) -> list[dict[str, str]]:
        """List of {attribute, group_a, group_b, metric} dicts for failures."""
        affected = []
        for attr_name, report in self.attribute_reports.items():
            for result in [
                report.demographic_parity,
                report.equalized_odds,
                report.predictive_parity,
            ]:
                if not result.passed:
                    affected.append({
                        "attribute": attr_name,
                        "group_a": result.group_a,
                        "group_b": result.group_b,
                        "metric": result.metric_name,
                        "difference": f"{result.difference:.4f}",
                        "threshold": f"{result.threshold:.4f}",
                    })
        return affected

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "model_name": self.model_name,
            "audit_timestamp": self.audit_timestamp,
            "auditor": self.auditor,
            "thresholds": self.thresholds,
            "overall_pass_fail": "PASS" if self.all_passed else "FAIL",
            "failed_metrics": self.failed_metrics,
            "affected_groups": self.affected_groups,
            "remediation_suggestions": self.remediation_suggestions,
            "per_attribute": {
                attr: {
                    "passed": report.all_passed,
                    "n_groups": report.n_groups,
                    "demographic_parity": {
                        "difference": report.demographic_parity.difference,
                        "threshold": report.demographic_parity.threshold,
                        "passed": report.demographic_parity.passed,
                        "worst_pair": (
                            report.demographic_parity.group_a,
                            report.demographic_parity.group_b,
                        ),
                    },
                    "equalized_odds": {
                        "difference": report.equalized_odds.difference,
                        "threshold": report.equalized_odds.threshold,
                        "passed": report.equalized_odds.passed,
                        "worst_pair": (
                            report.equalized_odds.group_a,
                            report.equalized_odds.group_b,
                        ),
                    },
                    "predictive_parity": {
                        "difference": report.predictive_parity.difference,
                        "threshold": report.predictive_parity.threshold,
                        "passed": report.predictive_parity.passed,
                        "worst_pair": (
                            report.predictive_parity.group_a,
                            report.predictive_parity.group_b,
                        ),
                    },
                    "group_details": [
                        {
                            "group": gm.group_value,
                            "n": gm.n,
                            "positive_rate": round(gm.positive_rate, 4),
                            "tpr": round(gm.tpr, 4),
                            "fpr": round(gm.fpr, 4),
                            "ppv": round(gm.ppv, 4),
                        }
                        for gm in report.group_metrics
                    ],
                }
                for attr, report in self.attribute_reports.items()
            },
        }


# ---------------------------------------------------------------------------
# BiasAuditor
# ---------------------------------------------------------------------------

class BiasAuditor:
    """
    Orchestrates EU AI Act bias audits for iGaming AI systems.

    Supports auditing any binary classification model (AML scoring, fraud
    detection, responsible gaming triggers, KYC verification) across any
    set of protected attributes.

    Default thresholds follow the guidance in Chapter 43b:
      - Demographic parity: 0.05
      - Equalized odds:     0.10
      - Predictive parity:  0.05

    For models deployed in jurisdictions with stricter requirements (e.g.,
    UKGC Outcome 8), lower the thresholds to 0.03/0.07/0.03.
    """

    DEFAULT_THRESHOLDS: dict[str, float] = {
        "demographic_parity": 0.05,
        "equalized_odds": 0.10,
        "predictive_parity": 0.05,
    }

    def __init__(self, auditor_id: str = "bias-audit-system") -> None:
        """
        Args:
            auditor_id: Identifier of the auditor (system or human analyst name).
        """
        self.auditor_id = auditor_id

    def audit(
        self,
        model_name: str,
        predictions: Sequence[int],
        labels: Sequence[int],
        protected_attributes: dict[str, Sequence[str]],
        thresholds: Optional[dict[str, float]] = None,
    ) -> FairnessReport:
        """
        Run a complete bias audit across all provided protected attributes.

        Args:
            model_name:           Name + version of the model being audited
            predictions:          Binary predictions from the model (0 or 1)
            labels:               Ground-truth binary labels (0 or 1)
            protected_attributes: Mapping of attribute_name -> list of group values,
                                  one per sample. Must align with predictions/labels.
                                  Example:
                                    {
                                        "gender": ["M", "F", "M", "NB", ...],
                                        "age_group": ["18-25", "26-35", ...],
                                        "jurisdiction": ["MGA", "UKGC", ...],
                                    }
            thresholds:           Per-metric thresholds (overrides defaults).

        Returns:
            FairnessReport with results for all protected attributes.

        Raises:
            ValueError: If predictions, labels, and attribute arrays have different lengths.
        """
        n = len(predictions)
        if len(labels) != n:
            raise ValueError(
                f"predictions length ({n}) != labels length ({len(labels)})"
            )
        for attr_name, attr_values in protected_attributes.items():
            if len(attr_values) != n:
                raise ValueError(
                    f"protected_attributes[{attr_name!r}] length ({len(attr_values)}) "
                    f"!= predictions length ({n})"
                )

        effective_thresholds = dict(self.DEFAULT_THRESHOLDS)
        if thresholds:
            effective_thresholds.update(thresholds)

        attribute_reports: dict[str, AttributeFairnessReport] = {}
        for attr_name, attr_values in protected_attributes.items():
            groups = self._group_data(attr_values, labels, predictions)
            if len(groups) < 2:
                # Cannot compute pairwise metrics with a single group — skip with warning
                print(
                    f"WARNING: attribute {attr_name!r} has only one group in the data "
                    f"({list(groups.keys())[0]!r}). Skipping bias audit for this attribute.",
                    file=sys.stderr,
                )
                continue
            attribute_reports[attr_name] = compute_attribute_report(
                attribute_name=attr_name,
                groups=groups,
                thresholds=effective_thresholds,
            )

        report = FairnessReport(
            model_name=model_name,
            audit_timestamp=datetime.now(tz=timezone.utc).isoformat(),
            auditor=self.auditor_id,
            attribute_reports=attribute_reports,
            thresholds=effective_thresholds,
        )
        report.remediation_suggestions = self._generate_remediation(report)
        return report

    def _group_data(
        self,
        attr_values: Sequence[str],
        labels: Sequence[int],
        predictions: Sequence[int],
    ) -> dict[str, tuple[list[int], list[int]]]:
        """
        Split labels and predictions by group value.

        Returns:
            {group_value: (labels_for_group, predictions_for_group)}
        """
        groups: dict[str, tuple[list[int], list[int]]] = {}
        for group_val, label, pred in zip(attr_values, labels, predictions):
            if group_val not in groups:
                groups[group_val] = ([], [])
            groups[group_val][0].append(label)
            groups[group_val][1].append(pred)
        return groups

    def _generate_remediation(self, report: FairnessReport) -> list[str]:
        """
        Generate actionable remediation suggestions for each failed metric.

        Suggestions are based on established fairness remediation techniques:
          - Demographic parity failures → resampling / reweighting
          - Equalized odds failures → threshold optimization per group
          - Predictive parity failures → calibration per group

        Note: No single debiasing technique satisfies all three metrics
        simultaneously (impossibility theorem). The suggestions guide the
        compliance team to choose the right technique based on the business
        context (regulatory requirements vs. operational impact).
        """
        suggestions = []

        for attr_name, attr_report in report.attribute_reports.items():
            if attr_report.demographic_parity.passed is False:
                worst = attr_report.demographic_parity
                suggestions.append(
                    f"[{attr_name}] Demographic parity failure between groups "
                    f"'{worst.group_a}' and '{worst.group_b}' "
                    f"(diff={worst.difference:.4f}, threshold={worst.threshold:.4f}). "
                    f"Remediation: inspect training data for over/under-representation of "
                    f"group '{worst.group_a}'; apply resampling (SMOTE) or instance "
                    f"reweighting in the training pipeline. "
                    f"Check for proxy features (postcode, payment method type) that "
                    f"encode the protected attribute implicitly."
                )

            if attr_report.equalized_odds.passed is False:
                worst = attr_report.equalized_odds
                suggestions.append(
                    f"[{attr_name}] Equalized odds failure between groups "
                    f"'{worst.group_a}' and '{worst.group_b}' "
                    f"(diff={worst.difference:.4f}, threshold={worst.threshold:.4f}). "
                    f"Remediation: apply group-specific decision thresholds (threshold "
                    f"optimization) to equalise TPR and FPR across groups. This is "
                    f"particularly common when one group has a lower base rate -- "
                    f"review whether the model has learned a genuine risk signal or a "
                    f"spurious correlation with the protected attribute."
                )

            if attr_report.predictive_parity.passed is False:
                worst = attr_report.predictive_parity
                suggestions.append(
                    f"[{attr_name}] Predictive parity failure between groups "
                    f"'{worst.group_a}' and '{worst.group_b}' "
                    f"(diff={worst.difference:.4f}, threshold={worst.threshold:.4f}). "
                    f"Remediation: apply post-hoc calibration (Platt scaling or "
                    f"isotonic regression) per group. A PPV disparity means the model "
                    f"is less reliable for one group -- a 'fraud' prediction carries "
                    f"different evidential weight depending on the player's demographic."
                )

        if not suggestions:
            suggestions.append(
                "All fairness metrics passed. No remediation required. "
                "Record this result in the model registry and proceed to deployment."
            )

        return suggestions

    def print_summary(self, report: FairnessReport) -> None:
        """Print a human-readable audit summary to stdout."""
        width = 66
        border = "=" * width
        print(border)
        print(f"  BIAS AUDIT REPORT: {report.model_name}")
        print(f"  Timestamp: {report.audit_timestamp}")
        print(f"  Auditor: {report.auditor}")
        print(border)

        for attr_name, attr_report in report.attribute_reports.items():
            print(f"\n  Protected attribute: {attr_name} ({attr_report.n_groups} groups)")
            print(f"  {'Metric':<25} {'Difference':>12} {'Threshold':>10} {'Status':>8}")
            print(f"  {'-'*25} {'-'*12} {'-'*10} {'-'*8}")

            for result in [
                attr_report.demographic_parity,
                attr_report.equalized_odds,
                attr_report.predictive_parity,
            ]:
                status = "PASS" if result.passed else "FAIL"
                print(
                    f"  {result.metric_name:<25} {result.difference:>12.4f} "
                    f"{result.threshold:>10.4f} {status:>8}"
                )

        print(f"\n  {border}")
        overall = "PASS" if report.all_passed else "FAIL"
        print(f"  OVERALL: {overall}")
        if not report.all_passed:
            print(f"\n  Failed: {', '.join(report.failed_metrics)}")
            print("\n  Remediation:")
            for suggestion in report.remediation_suggestions:
                # Wrap long lines
                words = suggestion.split()
                line = "    "
                for word in words:
                    if len(line) + len(word) > 72:
                        print(line)
                        line = "    " + word + " "
                    else:
                        line += word + " "
                if line.strip():
                    print(line)
        print(border)
