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
Isolation Forest Anomaly Detection for Fraud
==============================================

Detects novel fraud patterns that supervised models have never seen.

Why Isolation Forest for iGaming fraud:
  - Fraudsters constantly evolve: new attack vectors appear weekly
  - Supervised models only detect patterns present in training data
  - Isolation Forest flags ANY anomaly, including zero-day fraud patterns
  - No labeled data required: trains on "normal" behavior distribution
  - Fast training and inference (O(n log n))
  - Low memory footprint for production deployment

Detection targets:
  - Novel bot patterns not in training data
  - New money laundering techniques (e.g., crypto layering)
  - Emerging multi-accounting schemes
  - Unknown exploit patterns on new game launches
  - Unusual player behavior that does not match any known category

Scoring: Anomaly scores range from -1 (most anomalous) to +1 (most normal).
We normalize to [0, 1] where 1 = most anomalous for consistency with other models.
"""

import json
import logging
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fraud.ml.isolation_forest")


FEATURE_NAMES = [
    "tx_count_1h", "tx_count_24h", "inter_event_ms", "inter_event_std_10",
    "rolling_avg_amount_20", "amount_cv_20", "bot_timing_score",
    "hour_deviation_zscore", "hours_since_last_activity",
    "bet_size_ratio", "bet_size_volatility", "martingale_count_10",
    "unique_games_played", "game_switch_count_20",
    "unique_payment_methods_30d", "failed_deposit_count_1h",
    "deposit_to_play_ratio", "velocity_24h_count",
    "ip_is_vpn", "ip_is_datacenter", "multi_account_device_count",
]


class IsolationForestFraudDetector:
    """
    Isolation Forest anomaly detector tuned for iGaming fraud patterns.

    Key design decisions:
      - contamination=0.02: assumes ~2% of training data is anomalous
      - max_features=0.8: high feature sampling for comprehensive anomaly detection
      - n_estimators=200: balance between detection power and inference speed
      - max_samples=min(256, n_samples): subsample for efficiency on large datasets

    The model learns the "shape" of normal player behavior and flags
    anything that falls outside this learned distribution.
    """

    def __init__(
        self,
        model_dir: str = "./models/isolation_forest",
        contamination: float = 0.02,
        random_state: int = 42,
    ):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.contamination = contamination
        self.random_state = random_state
        self.model: Optional[IsolationForest] = None
        self.scaler: Optional[StandardScaler] = None
        self.feature_names = FEATURE_NAMES
        self.training_metadata: dict = {}

    def train(
        self,
        X_train: np.ndarray,
        y_train: Optional[np.ndarray] = None,
        X_val: Optional[np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
        feature_names: Optional[list[str]] = None,
    ) -> dict:
        """
        Train Isolation Forest on normal behavior data.

        For best results, train primarily on LEGITIMATE transactions.
        The model learns what "normal" looks like, so fraudulent samples
        in training data should be minimal (contamination parameter).

        Args:
            X_train: Training features (ideally mostly legitimate transactions)
            y_train: Optional labels for evaluation (not used in training)
            X_val: Optional validation set
            y_val: Optional validation labels for metric calculation
            feature_names: Feature column names

        Returns:
            Dict with training metrics and anomaly statistics
        """
        if feature_names:
            self.feature_names = feature_names

        start_time = time.time()

        # Standardize features (Isolation Forest is not scale-invariant
        # when features have very different ranges)
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X_train)

        logger.info("Training Isolation Forest on %d samples...", len(X_train))

        self.model = IsolationForest(
            n_estimators=200,
            max_samples=min(256, len(X_train)),  # Subsample for each tree
            contamination=self.contamination,
            max_features=0.8,
            bootstrap=False,
            n_jobs=-1,
            random_state=self.random_state,
            verbose=1,
        )

        self.model.fit(X_scaled)

        train_time = time.time() - start_time

        # Compute anomaly scores on training data
        train_scores = self._normalize_scores(self.model.decision_function(X_scaled))
        train_predictions = self.model.predict(X_scaled)
        n_anomalies = np.sum(train_predictions == -1)

        metrics = {
            "training_time_seconds": round(train_time, 2),
            "n_samples": len(X_train),
            "n_anomalies_detected": int(n_anomalies),
            "anomaly_rate": round(n_anomalies / len(X_train), 4),
            "score_mean": round(float(np.mean(train_scores)), 4),
            "score_std": round(float(np.std(train_scores)), 4),
            "score_p95": round(float(np.percentile(train_scores, 95)), 4),
            "score_p99": round(float(np.percentile(train_scores, 99)), 4),
        }

        # If validation labels available, compute supervised metrics
        if X_val is not None and y_val is not None:
            val_metrics = self._evaluate(X_val, y_val)
            metrics.update(val_metrics)

        self.training_metadata = {
            "model_type": "isolation_forest",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "contamination": self.contamination,
            "n_estimators": 200,
            "feature_count": len(self.feature_names),
            "feature_names": self.feature_names,
            "metrics": metrics,
        }

        logger.info(
            "Training complete: %d anomalies (%.2f%%) in %.1fs",
            n_anomalies, (n_anomalies / len(X_train)) * 100, train_time,
        )

        return metrics

    def _normalize_scores(self, raw_scores: np.ndarray) -> np.ndarray:
        """
        Normalize Isolation Forest scores to [0, 1] range.

        Raw scores: negative = more anomalous, positive = more normal
        Normalized: 0 = normal, 1 = highly anomalous

        This normalization makes scores compatible with the ensemble scorer
        which expects all models to output probabilities in [0, 1].
        """
        # Negate so higher = more anomalous
        negated = -raw_scores
        # Min-max normalize to [0, 1]
        min_score = negated.min()
        max_score = negated.max()
        if max_score - min_score == 0:
            return np.zeros_like(negated)
        return (negated - min_score) / (max_score - min_score)

    def _evaluate(self, X_val: np.ndarray, y_val: np.ndarray) -> dict:
        """Evaluate against labeled validation data."""
        X_scaled = self.scaler.transform(X_val)  # ty:ignore[unresolved-attribute]
        scores = self._normalize_scores(self.model.decision_function(X_scaled))  # ty:ignore[unresolved-attribute]

        metrics = {}
        try:
            metrics["val_auc_roc"] = round(roc_auc_score(y_val, scores), 4)
            metrics["val_aucpr"] = round(average_precision_score(y_val, scores), 4)
        except ValueError as e:
            logger.warning("Cannot compute AUC metrics: %s", e)

        # Detection rate at different thresholds
        for threshold in [0.7, 0.8, 0.9, 0.95]:
            predicted_anomalies = (scores >= threshold).astype(int)
            if np.sum(y_val == 1) > 0:
                recall = np.sum((predicted_anomalies == 1) & (y_val == 1)) / np.sum(y_val == 1)
                metrics[f"recall_at_{threshold}"] = round(float(recall), 4)
            if np.sum(predicted_anomalies == 1) > 0:
                precision = np.sum((predicted_anomalies == 1) & (y_val == 1)) / np.sum(predicted_anomalies == 1)
                metrics[f"precision_at_{threshold}"] = round(float(precision), 4)

        return metrics

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Predict anomaly scores normalized to [0, 1].

        Returns:
            Array of anomaly scores where 1 = highly anomalous
        """
        if self.model is None or self.scaler is None:
            raise ValueError("Model not trained/loaded")
        X_scaled = self.scaler.transform(X)
        raw_scores = self.model.decision_function(X_scaled)
        return self._normalize_scores(raw_scores)

    def predict_with_explanation(self, X: np.ndarray) -> list[dict]:
        """
        Predict with per-feature anomaly contribution.

        For each sample, identifies which features contribute most to
        the anomaly score. Essential for analyst investigation.
        """
        if self.model is None or self.scaler is None:
            raise ValueError("Model not trained/loaded")

        X_scaled = self.scaler.transform(X)
        overall_scores = self.predict(X)

        explanations = []
        for i in range(len(X)):
            # Approximate feature contributions by measuring score change
            # when each feature is replaced with its mean value
            baseline_score = overall_scores[i]
            contributions = {}

            for j, fname in enumerate(self.feature_names):
                if j >= X_scaled.shape[1]:
                    break
                # Replace feature j with mean (0 after standardization)
                X_modified = X_scaled[i:i+1].copy()
                X_modified[0, j] = 0.0
                modified_score = self._normalize_scores(
                    self.model.decision_function(X_modified)
                )[0]
                # Contribution = how much anomaly score drops when feature is normalized
                contributions[fname] = round(float(baseline_score - modified_score), 4)

            # Sort by absolute contribution
            sorted_contributions = sorted(
                contributions.items(), key=lambda x: abs(x[1]), reverse=True
            )

            explanations.append({
                "anomaly_score": round(float(baseline_score), 4),
                "is_anomaly": bool(baseline_score > 0.8),
                "top_contributing_features": [
                    {"feature": name, "contribution": contrib}
                    for name, contrib in sorted_contributions[:5]
                ],
            })

        return explanations

    def save_model(self, version: str = "latest") -> str:
        """Save model to disk."""
        model_path = self.model_dir / f"iforest_fraud_{version}"
        model_path.mkdir(parents=True, exist_ok=True)

        with open(model_path / "model.pkl", "wb") as f:
            pickle.dump(self.model, f)
        with open(model_path / "scaler.pkl", "wb") as f:
            pickle.dump(self.scaler, f)
        with open(model_path / "metadata.json", "w") as f:
            json.dump(self.training_metadata, f, indent=2)

        logger.info("Model saved to %s", model_path)
        return str(model_path)

    def load_model(self, version: str = "latest") -> None:
        """Load model from disk."""
        model_path = self.model_dir / f"iforest_fraud_{version}"
        with open(model_path / "model.pkl", "rb") as f:
            self.model = pickle.load(f)
        with open(model_path / "scaler.pkl", "rb") as f:
            self.scaler = pickle.load(f)
        with open(model_path / "metadata.json", "r") as f:
            self.training_metadata = json.load(f)
        self.feature_names = self.training_metadata.get("feature_names", FEATURE_NAMES)


def main():
    """Train Isolation Forest on synthetic data."""
    np.random.seed(42)
    n_samples = 50_000
    n_features = len(FEATURE_NAMES)

    X = np.random.randn(n_samples, n_features)
    y = np.zeros(n_samples)
    fraud_idx = np.random.choice(n_samples, int(n_samples * 0.02), replace=False)
    y[fraud_idx] = 1
    # Inject anomalous patterns
    X[fraud_idx] += np.random.uniform(2, 5, size=(len(fraud_idx), n_features))

    split = int(n_samples * 0.8)
    detector = IsolationForestFraudDetector()
    metrics = detector.train(X[:split], y[:split], X[split:], y[split:])

    # Predict with explanations
    explanations = detector.predict_with_explanation(X[split:split+3])
    for i, exp in enumerate(explanations):
        logger.info("Sample %d: score=%.4f anomaly=%s", i, exp["anomaly_score"], exp["is_anomaly"])
        for feat in exp["top_contributing_features"]:
            logger.info("  %s: %.4f", feat["feature"], feat["contribution"])

    detector.save_model("demo")


if __name__ == "__main__":
    main()
