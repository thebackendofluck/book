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
Random Forest Fraud Detection Model Training
==============================================

Complementary supervised model to XGBoost for ensemble diversity.

Why Random Forest alongside XGBoost:
  - Different inductive bias: bagging vs boosting reduces ensemble correlation
  - More robust to noisy features (common in real-time event streams)
  - Provides uncertainty estimation via prediction variance across trees
  - Less prone to overfitting on small fraud datasets
  - Decision paths are more interpretable for regulatory explainability

Random Forest excels at:
  - Detecting fraud patterns that are combinations of a few strong signals
  - Providing stable predictions even with missing or noisy features
  - Generating reliable feature importance for compliance reports
  - Estimating prediction confidence (variance across trees)

This model outputs:
  - Fraud probability [0.0, 1.0]
  - Confidence score based on tree agreement
  - Feature importance for each prediction (SHAP-ready)
"""

import json
import logging
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import (
    classification_report,
    roc_auc_score,
    average_precision_score,
    precision_recall_curve,
    confusion_matrix,
    f1_score,
)
from sklearn.calibration import CalibratedClassifierCV

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fraud.ml.random_forest")


# Feature list shared with XGBoost for ensemble compatibility
FEATURE_NAMES = [
    # Velocity
    "tx_count_1h", "tx_count_24h", "tx_count_7d",
    "deposit_amount_1h", "deposit_amount_24h",
    "inter_event_ms", "inter_event_mean_10", "inter_event_std_10",
    "rolling_avg_amount_20", "rolling_max_amount_50", "amount_cv_20",
    "bot_timing_score",
    # Temporal
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "is_weekend", "hour_deviation_zscore",
    "long_absence_flag", "hours_since_last_activity",
    # Behavioral
    "bet_size_ratio", "bet_size_volatility", "bet_to_total_ratio",
    "martingale_count_10", "unique_games_played", "game_switch_count_20",
    "cumulative_wagered",
    # Payment
    "unique_payment_methods_30d", "failed_deposit_count_1h",
    "chargeback_history_count", "deposit_to_play_ratio",
    "is_first_deposit", "velocity_24h_count", "velocity_24h_amount_eur",
    # Device/Geo
    "ip_is_vpn", "ip_is_tor", "ip_is_datacenter",
    "ip_country_mismatch", "device_age_hours", "multi_account_device_count",
]


class RandomForestFraudTrainer:
    """
    Random Forest training pipeline optimized for fraud detection.

    Key optimizations:
      - class_weight='balanced_subsample': handles imbalance per bootstrap sample
      - High max_features (sqrt): each tree sees enough features for complex patterns
      - min_samples_leaf=20: prevents overfitting to individual fraud cases
      - n_estimators=500: enough trees for stable probability estimates
    """

    def __init__(
        self,
        model_dir: str = "./models/random_forest",
        random_state: int = 42,
    ):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.random_state = random_state
        self.model: Optional[RandomForestClassifier] = None
        self.calibrated_model = None
        self.feature_names = FEATURE_NAMES
        self.training_metadata: dict = {}

    def train(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: np.ndarray,
        y_val: np.ndarray,
        feature_names: Optional[list[str]] = None,
    ) -> dict:
        """
        Train Random Forest with fraud-optimized parameters.

        Args:
            X_train: Training features
            y_train: Training labels (0=legitimate, 1=fraud)
            X_val: Validation features
            y_val: Validation labels
            feature_names: Column names

        Returns:
            Dict with evaluation metrics
        """
        if feature_names:
            self.feature_names = feature_names

        start_time = time.time()
        n_fraud = np.sum(y_train == 1)
        fraud_ratio = n_fraud / len(y_train)
        logger.info(
            "Training RF: %d samples (%.2f%% fraud)",
            len(y_train), fraud_ratio * 100,
        )

        self.model = RandomForestClassifier(
            n_estimators=500,
            max_depth=20,
            min_samples_split=10,
            min_samples_leaf=20,         # Prevent single-case overfitting
            max_features="sqrt",
            class_weight="balanced_subsample",  # Re-balance each bootstrap sample
            bootstrap=True,
            oob_score=True,              # Out-of-bag score (free validation)
            n_jobs=-1,                   # Use all CPU cores
            random_state=self.random_state,
            verbose=1,
        )

        self.model.fit(X_train, y_train)

        # Probability calibration
        logger.info("Calibrating probabilities...")
        self.calibrated_model = CalibratedClassifierCV(
            self.model, method="isotonic", cv=3
        )
        self.calibrated_model.fit(X_val, y_val)

        train_time = time.time() - start_time
        metrics = self._evaluate(X_val, y_val)
        metrics["training_time_seconds"] = round(train_time, 2)
        metrics["oob_score"] = round(self.model.oob_score_, 4)

        self.training_metadata = {
            "model_type": "random_forest",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "n_estimators": 500,
            "feature_count": len(self.feature_names),
            "feature_names": self.feature_names,
            "metrics": metrics,
        }

        logger.info("Training complete in %.1fs (OOB=%.4f)", train_time, self.model.oob_score_)
        return metrics

    def _evaluate(self, X_val: np.ndarray, y_val: np.ndarray) -> dict:
        """Compute evaluation metrics."""
        y_prob = self.calibrated_model.predict_proba(X_val)[:, 1]  # ty:ignore[unresolved-attribute]

        # Optimal threshold via F1
        precision, recall, thresholds = precision_recall_curve(y_val, y_prob)
        f1_scores = 2 * (precision * recall) / (precision + recall + 1e-8)
        optimal_idx = np.argmax(f1_scores)
        optimal_threshold = thresholds[optimal_idx] if optimal_idx < len(thresholds) else 0.5

        y_pred = (y_prob >= optimal_threshold).astype(int)
        cm = confusion_matrix(y_val, y_pred)

        metrics = {
            "auc_roc": round(roc_auc_score(y_val, y_prob), 4),
            "aucpr": round(average_precision_score(y_val, y_prob), 4),
            "f1_score": round(f1_score(y_val, y_pred), 4),
            "optimal_threshold": round(float(optimal_threshold), 4),
            "precision_at_optimal": round(float(precision[optimal_idx]), 4),
            "recall_at_optimal": round(float(recall[optimal_idx]), 4),
            "confusion_matrix": {
                "true_negatives": int(cm[0][0]),
                "false_positives": int(cm[0][1]),
                "false_negatives": int(cm[1][0]),
                "true_positives": int(cm[1][1]),
            },
        }

        logger.info("\n%s", classification_report(y_val, y_pred,
                    target_names=["Legitimate", "Fraud"]))
        return metrics

    def predict_with_confidence(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Predict fraud probability with confidence estimation.

        Confidence is measured by agreement among trees:
        - High confidence: most trees agree on the prediction
        - Low confidence: trees are split (50/50), indicating ambiguous case

        This is valuable for routing: low-confidence high-risk predictions
        should go to human analysts for review.

        Returns:
            (probabilities, confidence_scores) both shape (n_samples,)
        """
        if self.model is None:
            raise ValueError("Model not trained/loaded")

        # Get individual tree predictions
        tree_predictions = np.array([
            tree.predict_proba(X)[:, 1]
            for tree in self.model.estimators_
        ])

        # Probability = mean across trees
        probabilities = self.calibrated_model.predict_proba(X)[:, 1]  # ty:ignore[unresolved-attribute]

        # Confidence = 1 - normalized variance across trees
        # Low variance = high agreement = high confidence
        variance = np.var(tree_predictions, axis=0)
        max_variance = 0.25  # Maximum possible variance for binary classification
        confidence = 1.0 - (variance / max_variance)
        confidence = np.clip(confidence, 0.0, 1.0)

        return probabilities, confidence

    def get_feature_importance(self, top_n: int = 20) -> list[dict]:
        """Get feature importance from impurity-based and permutation analysis."""
        if self.model is None:
            raise ValueError("Model not trained")

        importances = self.model.feature_importances_
        indices = np.argsort(importances)[::-1][:top_n]

        return [
            {
                "feature": self.feature_names[i] if i < len(self.feature_names) else f"f{i}",
                "importance": round(float(importances[i]), 4),
                "std": round(float(np.std([
                    tree.feature_importances_[i] for tree in self.model.estimators_
                ])), 4),
            }
            for i in indices
        ]

    def save_model(self, version: str = "latest") -> str:
        """Save model to disk."""
        model_path = self.model_dir / f"rf_fraud_{version}"
        model_path.mkdir(parents=True, exist_ok=True)

        with open(model_path / "model.pkl", "wb") as f:
            pickle.dump(self.model, f)
        with open(model_path / "calibrated_model.pkl", "wb") as f:
            pickle.dump(self.calibrated_model, f)
        with open(model_path / "metadata.json", "w") as f:
            json.dump(self.training_metadata, f, indent=2)

        logger.info("Model saved to %s", model_path)
        return str(model_path)

    def load_model(self, version: str = "latest") -> None:
        """Load model from disk."""
        model_path = self.model_dir / f"rf_fraud_{version}"
        with open(model_path / "model.pkl", "rb") as f:
            self.model = pickle.load(f)
        with open(model_path / "calibrated_model.pkl", "rb") as f:
            self.calibrated_model = pickle.load(f)
        with open(model_path / "metadata.json", "r") as f:
            self.training_metadata = json.load(f)
        self.feature_names = self.training_metadata.get("feature_names", FEATURE_NAMES)
        logger.info("Model loaded from %s", model_path)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict fraud probability."""
        return self.calibrated_model.predict_proba(X)[:, 1]  # ty:ignore[unresolved-attribute]


# =============================================================================
# Entry point
# =============================================================================

def main():
    """Train Random Forest on synthetic data."""
    np.random.seed(42)
    n_samples = 50_000
    n_features = len(FEATURE_NAMES)
    fraud_rate = 0.02

    X = np.random.randn(n_samples, n_features)
    y = np.zeros(n_samples)
    fraud_idx = np.random.choice(n_samples, int(n_samples * fraud_rate), replace=False)
    y[fraud_idx] = 1
    X[fraud_idx, 0] += 2.5
    X[fraud_idx, 10] += 1.5

    split = int(n_samples * 0.8)
    trainer = RandomForestFraudTrainer()
    metrics = trainer.train(X[:split], y[:split], X[split:], y[split:])

    # Predict with confidence
    probs, confidence = trainer.predict_with_confidence(X[split:split+5])
    for i in range(5):
        logger.info(
            "Sample %d: prob=%.4f confidence=%.4f actual=%d",
            i, probs[i], confidence[i], int(y[split+i]),
        )

    trainer.save_model("demo")


if __name__ == "__main__":
    main()
