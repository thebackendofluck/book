# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Ensemble Fraud Scorer with Adaptive Weight Adjustment
=======================================================

Combines predictions from all fraud detection models into a single,
calibrated fraud score using adaptive ensemble weighting.

Ensemble members:
  1. XGBoost (supervised)      - Detects known fraud patterns
  2. Random Forest (supervised) - Complementary detection, confidence estimation
  3. Isolation Forest (unsup.)  - Detects novel/unknown anomalies
  4. Autoencoder (unsup.)       - Detects behavioral deviations
  5. LSTM (temporal)            - Detects sequential fraud patterns

Why ensemble for fraud detection:
  - No single model catches all fraud types
  - Reduces false positives (multiple models must agree for high scores)
  - Adapts to concept drift (weights shift toward best-performing model)
  - Provides robustness (if one model degrades, others compensate)
  - Different models excel at different fraud types

Adaptive weighting strategy:
  - Initial weights based on validation AUC-PR
  - Weights updated daily based on analyst feedback (confirmed fraud/false positive)
  - Exponential moving average prevents oscillation
  - Minimum weight floor (0.05) ensures no model is fully ignored
"""

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fraud.ensemble")


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class EnsembleConfig:
    """Ensemble scorer configuration."""
    # Model identifiers and initial weights (based on typical AUC-PR)
    model_weights: dict[str, float] = field(default_factory=lambda: {
        "xgboost": 0.30,
        "random_forest": 0.25,
        "isolation_forest": 0.15,
        "autoencoder": 0.15,
        "lstm_sequence": 0.15,
    })
    # Minimum weight per model (prevent total exclusion)
    min_weight: float = 0.05
    # EMA smoothing for weight updates
    ema_alpha: float = 0.1
    # Score thresholds for alert levels
    thresholds: dict[str, float] = field(default_factory=lambda: {
        "critical": 0.85,
        "high": 0.65,
        "medium": 0.40,
        "low": 0.20,
    })
    # Path for persisting weights
    weights_path: str = "./models/ensemble/weights.json"


# =============================================================================
# Ensemble Scorer
# =============================================================================

class EnsembleFraudScorer:
    """
    Weighted ensemble scorer combining all fraud detection models.

    Scoring flow:
      1. Collect predictions from all models
      2. Apply model-specific weights
      3. Compute weighted average score
      4. Apply calibration (ensure score distributions match expected fraud rates)
      5. Classify into alert levels
      6. Return enriched result with per-model breakdown
    """

    def __init__(self, config: Optional[EnsembleConfig] = None):
        self.config = config or EnsembleConfig()
        self.weights = dict(self.config.model_weights)
        self._normalize_weights()

        # Performance tracking for adaptive weighting
        self._model_performance: dict[str, list[dict]] = {
            name: [] for name in self.weights
        }
        self._weight_history: list[dict] = []

        # Load persisted weights if available
        self._load_weights()

        logger.info("Ensemble scorer initialized with weights: %s", self.weights)

    def _normalize_weights(self) -> None:
        """Normalize weights to sum to 1.0."""
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v / total for k, v in self.weights.items()}

    def score(self, model_predictions: dict[str, float]) -> dict:
        """
        Compute ensemble fraud score from individual model predictions.

        Args:
            model_predictions: Dict of model_name -> fraud_probability
                Example: {
                    "xgboost": 0.82,
                    "random_forest": 0.75,
                    "isolation_forest": 0.60,
                    "autoencoder": 0.45,
                    "lstm_sequence": 0.88,
                }

        Returns:
            Dict with ensemble score, alert level, and per-model breakdown:
            {
                "ensemble_score": 0.74,
                "alert_level": "HIGH",
                "model_scores": { ... per-model details ... },
                "model_agreement": 0.85,
                "confidence": "HIGH",
            }
        """
        # Validate inputs
        available_models = {
            name: score for name, score in model_predictions.items()
            if name in self.weights and score is not None
        }

        if not available_models:
            logger.warning("No valid model predictions received")
            return {
                "ensemble_score": 0.0,
                "alert_level": "NONE",
                "error": "No valid model predictions",
            }

        # Compute weighted ensemble score
        # Only use weights for models that provided predictions
        active_weights = {k: self.weights[k] for k in available_models}
        weight_sum = sum(active_weights.values())

        ensemble_score = sum(
            available_models[name] * (active_weights[name] / weight_sum)
            for name in available_models
        )

        # Compute model agreement (how much models agree)
        # High agreement + high score = high confidence
        # Low agreement + high score = needs human review
        scores = list(available_models.values())
        if len(scores) > 1:
            agreement = 1.0 - np.std(scores) / 0.5  # Normalize by max possible std
            agreement = max(0.0, min(1.0, agreement))
        else:
            agreement = 1.0

        # Determine alert level
        alert_level = self._classify_alert_level(ensemble_score)

        # Determine confidence based on agreement and number of models
        model_coverage = len(available_models) / len(self.weights)
        confidence_score = agreement * model_coverage
        if confidence_score > 0.8:
            confidence = "HIGH"
        elif confidence_score > 0.5:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Build per-model breakdown
        model_breakdown = {}
        for name in self.weights:
            if name in available_models:
                effective_weight = active_weights[name] / weight_sum
                model_breakdown[name] = {
                    "score": round(available_models[name], 4),
                    "weight": round(effective_weight, 4),
                    "weighted_contribution": round(
                        available_models[name] * effective_weight, 4
                    ),
                    "status": "active",
                }
            else:
                model_breakdown[name] = {
                    "score": None,
                    "weight": round(self.weights[name], 4),
                    "weighted_contribution": 0.0,
                    "status": "unavailable",
                }

        # Detect disagreement patterns that need investigation
        disagreement_flag = None
        if len(scores) >= 3:
            supervised_scores = [
                available_models.get("xgboost", 0),
                available_models.get("random_forest", 0),
            ]
            unsupervised_scores = [
                available_models.get("isolation_forest", 0),
                available_models.get("autoencoder", 0),
            ]

            avg_supervised = np.mean([s for s in supervised_scores if s > 0])
            avg_unsupervised = np.mean([s for s in unsupervised_scores if s > 0])

            # Unsupervised high + supervised low = potentially novel fraud pattern
            if avg_unsupervised > 0.7 and avg_supervised < 0.3:
                disagreement_flag = "NOVEL_PATTERN"
                logger.warning(
                    "Novel fraud pattern detected: unsupervised=%.2f supervised=%.2f",
                    avg_unsupervised, avg_supervised,
                )

        return {
            "ensemble_score": round(ensemble_score, 4),
            "alert_level": alert_level,
            "model_scores": model_breakdown,
            "model_agreement": round(agreement, 4),
            "confidence": confidence,
            "models_available": len(available_models),
            "models_total": len(self.weights),
            "disagreement_flag": disagreement_flag,
            "scored_at": datetime.now(timezone.utc).isoformat(),
        }

    def _classify_alert_level(self, score: float) -> str:
        """Classify score into alert level."""
        if score >= self.config.thresholds["critical"]:
            return "CRITICAL"
        elif score >= self.config.thresholds["high"]:
            return "HIGH"
        elif score >= self.config.thresholds["medium"]:
            return "MEDIUM"
        elif score >= self.config.thresholds["low"]:
            return "LOW"
        return "NONE"

    # -------------------------------------------------------------------------
    # Adaptive Weight Adjustment
    # -------------------------------------------------------------------------

    def record_feedback(
        self,
        model_predictions: dict[str, float],
        actual_label: int,
        case_id: str,
    ) -> None:
        """
        Record analyst feedback for adaptive weight adjustment.

        When an analyst confirms or rejects a fraud alert, we record
        which models were correct, allowing weight adjustment.

        Args:
            model_predictions: The original model predictions for this case
            actual_label: 1 if confirmed fraud, 0 if false positive
            case_id: Unique case identifier for audit trail
        """
        for name, score in model_predictions.items():
            if name not in self._model_performance:
                continue

            # Evaluate each model's prediction at its threshold
            predicted = 1 if score >= 0.5 else 0
            is_correct = predicted == actual_label

            self._model_performance[name].append({
                "case_id": case_id,
                "score": score,
                "predicted": predicted,
                "actual": actual_label,
                "correct": is_correct,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        logger.info(
            "Feedback recorded for case %s: actual=%d (%s)",
            case_id, actual_label,
            "FRAUD" if actual_label == 1 else "LEGITIMATE",
        )

    def update_weights(self, lookback_days: int = 30) -> dict[str, float]:
        """
        Update model weights based on recent analyst feedback.

        Uses precision-weighted scoring to favor models that:
          1. Correctly identify fraud (true positives)
          2. Avoid false alarms (low false positives)

        Weight adjustment uses EMA to prevent sudden changes.

        Returns:
            Updated weights dict
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        new_weights = {}

        for name, records in self._model_performance.items():
            # Filter to recent feedback
            recent = [
                r for r in records
                if datetime.fromisoformat(r["timestamp"]) > cutoff
            ]

            if len(recent) < 20:
                # Not enough feedback to adjust; keep current weight
                new_weights[name] = self.weights.get(name, 0.2)
                continue

            # Compute precision and recall
            tp = sum(1 for r in recent if r["correct"] and r["actual"] == 1)
            fp = sum(1 for r in recent if not r["correct"] and r["actual"] == 0)
            fn = sum(1 for r in recent if not r["correct"] and r["actual"] == 1)
            tn = sum(1 for r in recent if r["correct"] and r["actual"] == 0)

            precision = tp / max(tp + fp, 1)
            recall = tp / max(tp + fn, 1)

            # Combined performance score (F1-like)
            if precision + recall > 0:
                performance = 2 * precision * recall / (precision + recall)
            else:
                performance = 0.0

            # EMA update
            current_weight = self.weights.get(name, 0.2)
            updated_weight = (
                self.config.ema_alpha * performance +
                (1 - self.config.ema_alpha) * current_weight
            )

            # Apply minimum weight floor
            updated_weight = max(updated_weight, self.config.min_weight)
            new_weights[name] = updated_weight

            logger.info(
                "Model %s: precision=%.3f recall=%.3f performance=%.3f weight: %.3f -> %.3f",
                name, precision, recall, performance, current_weight, updated_weight,
            )

        # Normalize
        self.weights = new_weights
        self._normalize_weights()

        # Record weight history
        self._weight_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "weights": dict(self.weights),
            "n_feedback_records": sum(len(v) for v in self._model_performance.values()),
        })

        # Persist
        self._save_weights()

        logger.info("Updated ensemble weights: %s", {
            k: round(v, 4) for k, v in self.weights.items()
        })

        return dict(self.weights)

    # -------------------------------------------------------------------------
    # Batch Scoring
    # -------------------------------------------------------------------------

    def score_batch(
        self,
        batch_predictions: list[dict[str, float]],
    ) -> list[dict]:
        """
        Score a batch of events efficiently.

        Args:
            batch_predictions: List of model prediction dicts

        Returns:
            List of ensemble results
        """
        return [self.score(preds) for preds in batch_predictions]

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def _save_weights(self) -> None:
        """Persist weights to disk."""
        path = Path(self.config.weights_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "weights": self.weights,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "history": self._weight_history[-10:],  # Keep last 10 updates
            }, f, indent=2)

    def _load_weights(self) -> None:
        """Load persisted weights if available."""
        path = Path(self.config.weights_path)
        if path.exists():
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                self.weights = data.get("weights", self.weights)
                self._weight_history = data.get("history", [])
                self._normalize_weights()
                logger.info("Loaded persisted weights from %s", path)
            except Exception as e:
                logger.warning("Failed to load weights: %s", e)

    def get_weight_history(self) -> list[dict]:
        """Return weight adjustment history for monitoring/dashboards."""
        return self._weight_history


# =============================================================================
# Entry point
# =============================================================================

def main():
    """Demo: ensemble scoring with multiple model predictions."""
    scorer = EnsembleFraudScorer()

    # Simulate different fraud scenarios
    scenarios = [
        {
            "name": "Clear fraud (all models agree)",
            "predictions": {
                "xgboost": 0.92, "random_forest": 0.88,
                "isolation_forest": 0.85, "autoencoder": 0.78,
                "lstm_sequence": 0.91,
            },
        },
        {
            "name": "Legitimate transaction",
            "predictions": {
                "xgboost": 0.05, "random_forest": 0.08,
                "isolation_forest": 0.12, "autoencoder": 0.10,
                "lstm_sequence": 0.03,
            },
        },
        {
            "name": "Novel fraud (unsupervised detects, supervised misses)",
            "predictions": {
                "xgboost": 0.15, "random_forest": 0.22,
                "isolation_forest": 0.88, "autoencoder": 0.82,
                "lstm_sequence": 0.65,
            },
        },
        {
            "name": "Ambiguous case (models disagree)",
            "predictions": {
                "xgboost": 0.72, "random_forest": 0.35,
                "isolation_forest": 0.45, "autoencoder": 0.28,
                "lstm_sequence": 0.80,
            },
        },
        {
            "name": "Partial model availability",
            "predictions": {
                "xgboost": 0.68, "random_forest": 0.72,
                "isolation_forest": None, "autoencoder": None,
                "lstm_sequence": 0.55,
            },
        },
    ]

    for scenario in scenarios:
        logger.info("\n=== %s ===", scenario["name"])
        result = scorer.score(scenario["predictions"])  # ty:ignore[invalid-argument-type]
        logger.info("Score: %.4f | Level: %s | Confidence: %s | Agreement: %.4f",
                    result["ensemble_score"], result["alert_level"],
                    result["confidence"], result["model_agreement"])
        if result.get("disagreement_flag"):
            logger.info("DISAGREEMENT: %s", result["disagreement_flag"])
        for model, details in result["model_scores"].items():
            logger.info("  %s: score=%s weight=%.3f contrib=%.4f",
                       model, details["score"], details["weight"],
                       details["weighted_contribution"])


if __name__ == "__main__":
    main()
