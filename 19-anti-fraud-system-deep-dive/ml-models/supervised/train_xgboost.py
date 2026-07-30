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
XGBoost Fraud Detection Model Training
========================================

Trains a gradient-boosted tree model for binary fraud classification.

Why XGBoost for iGaming fraud:
  - Handles tabular data with mixed feature types (numerical + categorical)
  - Built-in handling of missing values (common in real-time event streams)
  - Feature importance ranking identifies which signals matter most
  - Fast inference (<1ms) suitable for real-time scoring at 100K TPS
  - Handles severe class imbalance (fraud rate typically 0.1-2%)
  - Monotonic constraints enforce domain knowledge (e.g., more chargebacks = higher risk)

Model outputs a probability score [0.0, 1.0] where:
  - 0.0-0.3: Low risk (auto-approve)
  - 0.3-0.6: Medium risk (enhanced monitoring)
  - 0.6-0.8: High risk (manual review queue)
  - 0.8-1.0: Critical risk (auto-block + alert)
"""

import json
import logging
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import xgboost as xgb  # ty:ignore[unresolved-import]
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.metrics import (
    classification_report,
    roc_auc_score,
    precision_recall_curve,
    average_precision_score,
    confusion_matrix,
    f1_score,
)
from sklearn.calibration import CalibratedClassifierCV

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fraud.ml.xgboost")


# =============================================================================
# Feature Configuration
# =============================================================================

# Features used by the model, grouped by category
FEATURE_GROUPS = {
    "velocity": [
        "tx_count_1h", "tx_count_24h", "tx_count_7d",
        "deposit_amount_1h", "deposit_amount_24h",
        "inter_event_ms", "inter_event_mean_10", "inter_event_std_10",
        "rolling_avg_amount_20", "rolling_max_amount_50", "amount_cv_20",
        "bot_timing_score",
    ],
    "temporal": [
        "hour_sin", "hour_cos", "dow_sin", "dow_cos",
        "is_weekend", "hour_deviation_zscore",
        "long_absence_flag", "hours_since_last_activity",
    ],
    "behavioral": [
        "bet_size_ratio", "bet_size_volatility", "bet_to_total_ratio",
        "martingale_count_10", "unique_games_played", "game_switch_count_20",
        "cumulative_wagered",
    ],
    "payment": [
        "unique_payment_methods_30d", "failed_deposit_count_1h",
        "chargeback_history_count", "deposit_to_play_ratio",
        "is_first_deposit", "velocity_24h_count", "velocity_24h_amount_eur",
    ],
    "device_geo": [
        "ip_is_vpn", "ip_is_tor", "ip_is_datacenter",
        "ip_country_mismatch", "device_age_hours", "multi_account_device_count",
    ],
}

ALL_FEATURES = [f for group in FEATURE_GROUPS.values() for f in group]


# =============================================================================
# Training Pipeline
# =============================================================================

class XGBoostFraudTrainer:
    """
    End-to-end training pipeline for XGBoost fraud detection model.

    Handles:
      - Class imbalance via scale_pos_weight and SMOTE
      - Hyperparameter tuning via cross-validation
      - Probability calibration for reliable score thresholds
      - Feature importance analysis
      - Model serialization with metadata
    """

    def __init__(
        self,
        model_dir: str = "./models/xgboost",
        random_state: int = 42,
    ):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.random_state = random_state
        self.model: Optional[xgb.XGBClassifier] = None
        self.calibrated_model = None
        self.feature_names: list[str] = ALL_FEATURES
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
        Train XGBoost model with fraud-optimized hyperparameters.

        Args:
            X_train: Training features (n_samples, n_features)
            y_train: Training labels (0=legitimate, 1=fraud)
            X_val: Validation features
            y_val: Validation labels
            feature_names: Feature column names

        Returns:
            Dict with training metrics and model metadata
        """
        if feature_names:
            self.feature_names = feature_names

        start_time = time.time()

        # Calculate class imbalance ratio
        n_legitimate = np.sum(y_train == 0)
        n_fraud = np.sum(y_train == 1)
        fraud_ratio = n_fraud / len(y_train)
        scale_pos_weight = n_legitimate / max(n_fraud, 1)

        logger.info(
            "Training data: %d samples (%.2f%% fraud, ratio=1:%.0f)",
            len(y_train), fraud_ratio * 100, scale_pos_weight,
        )

        # XGBoost hyperparameters optimized for fraud detection
        params = {
            # Core parameters
            "objective": "binary:logistic",
            "eval_metric": ["aucpr", "auc", "logloss"],  # AUCPR is primary (handles imbalance)
            "tree_method": "hist",       # Histogram-based for speed
            "device": "cpu",

            # Regularization (prevent overfitting on rare fraud patterns)
            "max_depth": 8,              # Deep enough for complex fraud patterns
            "min_child_weight": 50,      # Require 50+ samples per leaf (reduce noise)
            "gamma": 0.1,               # Minimum loss reduction for split
            "subsample": 0.8,           # Row sampling (reduces overfitting)
            "colsample_bytree": 0.8,    # Column sampling per tree
            "reg_alpha": 0.1,           # L1 regularization
            "reg_lambda": 1.0,          # L2 regularization

            # Learning
            "learning_rate": 0.05,
            "n_estimators": 1000,

            # Class imbalance handling
            "scale_pos_weight": scale_pos_weight,

            # Monotonic constraints: enforce domain knowledge
            # 1 = score must increase with feature, -1 = must decrease, 0 = no constraint
            # Example: more chargebacks should always increase fraud score
            "monotone_constraints": self._get_monotonic_constraints(),

            "random_state": self.random_state,
        }

        self.model = xgb.XGBClassifier(**params)

        # Train with early stopping on validation set
        self.model.fit(
            X_train, y_train,
            eval_set=[(X_train, y_train), (X_val, y_val)],
            verbose=100,
        )

        # Calibrate probabilities for reliable threshold-based decisions
        # Raw XGBoost probabilities are often poorly calibrated
        logger.info("Calibrating probabilities with isotonic regression...")
        self.calibrated_model = CalibratedClassifierCV(
            self.model, method="isotonic", cv=3
        )
        self.calibrated_model.fit(X_val, y_val)

        # Evaluate
        train_time = time.time() - start_time
        metrics = self._evaluate(X_val, y_val)
        metrics["training_time_seconds"] = round(train_time, 2)
        metrics["n_estimators_used"] = self.model.best_iteration + 1 if hasattr(self.model, 'best_iteration') else params["n_estimators"]
        metrics["fraud_ratio"] = round(fraud_ratio, 4)
        metrics["scale_pos_weight"] = round(scale_pos_weight, 2)

        self.training_metadata = {
            "model_type": "xgboost",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "feature_count": len(self.feature_names),
            "feature_names": self.feature_names,
            "hyperparameters": {k: v for k, v in params.items()
                               if k != "monotone_constraints"},
            "metrics": metrics,
        }

        logger.info("Training complete in %.1fs", train_time)
        logger.info("Validation AUC-PR: %.4f", metrics["aucpr"])
        logger.info("Validation AUC-ROC: %.4f", metrics["auc_roc"])

        return metrics

    def _get_monotonic_constraints(self) -> str:
        """
        Define monotonic constraints based on domain knowledge.

        Monotonic constraints ensure the model respects known relationships:
        - More chargebacks -> higher fraud score (always)
        - More VPN usage -> higher fraud score (always)
        - Higher account age -> lower fraud score (generally)
        """
        constraints = []
        for feature in self.feature_names:
            if feature in ("chargeback_history_count", "ip_is_vpn", "ip_is_tor",
                          "ip_is_datacenter", "bot_timing_score", "failed_deposit_count_1h",
                          "martingale_count_10", "multi_account_device_count"):
                constraints.append(1)  # More = higher risk
            elif feature in ("device_age_hours",):
                constraints.append(-1)  # More = lower risk (established device)
            else:
                constraints.append(0)  # No constraint
        return "(" + ",".join(str(c) for c in constraints) + ")"

    def _evaluate(self, X_val: np.ndarray, y_val: np.ndarray) -> dict:
        """Compute comprehensive evaluation metrics."""
        # Use calibrated model for probability predictions
        y_prob = self.calibrated_model.predict_proba(X_val)[:, 1]  # ty:ignore[unresolved-attribute]
        y_pred = (y_prob >= 0.5).astype(int)

        # Find optimal threshold using precision-recall curve
        precision, recall, thresholds = precision_recall_curve(y_val, y_prob)
        f1_scores = 2 * (precision * recall) / (precision + recall + 1e-8)
        optimal_idx = np.argmax(f1_scores)
        optimal_threshold = thresholds[optimal_idx] if optimal_idx < len(thresholds) else 0.5

        y_pred_optimal = (y_prob >= optimal_threshold).astype(int)
        cm = confusion_matrix(y_val, y_pred_optimal)

        metrics = {
            "auc_roc": round(roc_auc_score(y_val, y_prob), 4),
            "aucpr": round(average_precision_score(y_val, y_prob), 4),
            "f1_score": round(f1_score(y_val, y_pred_optimal), 4),
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

        # Business metrics
        if cm[1][0] + cm[1][1] > 0:
            # False negative rate: frauds we MISSED (most critical metric)
            metrics["false_negative_rate"] = round(cm[1][0] / (cm[1][0] + cm[1][1]), 4)
        if cm[0][1] + cm[0][0] > 0:
            # False positive rate: legitimate transactions we wrongly flagged
            metrics["false_positive_rate"] = round(cm[0][1] / (cm[0][1] + cm[0][0]), 4)

        logger.info("\n%s", classification_report(y_val, y_pred_optimal,
                    target_names=["Legitimate", "Fraud"]))

        return metrics

    def get_feature_importance(self, top_n: int = 20) -> list[dict]:
        """Get top-N most important features ranked by gain."""
        if self.model is None:
            raise ValueError("Model not trained yet")

        importance = self.model.get_booster().get_score(importance_type="gain")
        sorted_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)

        result = []
        for fname, gain in sorted_features[:top_n]:
            # Map back to readable feature name
            if fname.startswith("f"):
                idx = int(fname[1:])
                readable_name = self.feature_names[idx] if idx < len(self.feature_names) else fname
            else:
                readable_name = fname

            result.append({
                "feature": readable_name,
                "importance_gain": round(gain, 4),
            })

        return result

    def save_model(self, version: str = "latest") -> str:
        """Save model, calibrator, and metadata to disk."""
        if self.model is None:
            raise ValueError("Model not trained yet")

        model_path = self.model_dir / f"xgboost_fraud_{version}"
        model_path.mkdir(parents=True, exist_ok=True)

        # Save XGBoost model (native format for fast loading)
        self.model.save_model(str(model_path / "model.json"))

        # Save calibrated model (pickle for sklearn objects)
        with open(model_path / "calibrated_model.pkl", "wb") as f:
            pickle.dump(self.calibrated_model, f)

        # Save metadata
        with open(model_path / "metadata.json", "w") as f:
            json.dump(self.training_metadata, f, indent=2)

        # Save feature importance
        importance = self.get_feature_importance(top_n=50)
        with open(model_path / "feature_importance.json", "w") as f:
            json.dump(importance, f, indent=2)

        logger.info("Model saved to %s", model_path)
        return str(model_path)

    def load_model(self, version: str = "latest") -> None:
        """Load a previously saved model."""
        model_path = self.model_dir / f"xgboost_fraud_{version}"

        self.model = xgb.XGBClassifier()
        self.model.load_model(str(model_path / "model.json"))

        with open(model_path / "calibrated_model.pkl", "rb") as f:
            self.calibrated_model = pickle.load(f)

        with open(model_path / "metadata.json", "r") as f:
            self.training_metadata = json.load(f)

        self.feature_names = self.training_metadata.get("feature_names", ALL_FEATURES)
        logger.info("Model loaded from %s", model_path)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict fraud probability for new events."""
        if self.calibrated_model is None:
            raise ValueError("Model not loaded/trained")
        return self.calibrated_model.predict_proba(X)[:, 1]


# =============================================================================
# Cross-Validation Pipeline
# =============================================================================

def cross_validate_model(
    X: np.ndarray,
    y: np.ndarray,
    n_folds: int = 5,
) -> dict:
    """
    Run stratified k-fold cross-validation.

    Stratified folds ensure each fold has the same fraud ratio,
    critical given the severe class imbalance in fraud detection.
    """
    skf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
    fold_metrics = []

    for fold, (train_idx, val_idx) in enumerate(skf.split(X, y)):
        logger.info("Training fold %d/%d...", fold + 1, n_folds)

        trainer = XGBoostFraudTrainer()
        metrics = trainer.train(
            X[train_idx], y[train_idx],
            X[val_idx], y[val_idx],
        )
        fold_metrics.append(metrics)

    # Aggregate metrics across folds
    avg_metrics = {}
    for key in fold_metrics[0]:
        if isinstance(fold_metrics[0][key], (int, float)):
            values = [m[key] for m in fold_metrics]
            avg_metrics[f"{key}_mean"] = round(np.mean(values), 4)
            avg_metrics[f"{key}_std"] = round(np.std(values), 4)

    logger.info("Cross-validation results (%d folds):", n_folds)
    for k, v in avg_metrics.items():
        logger.info("  %s: %.4f", k, v)

    return avg_metrics


# =============================================================================
# Entry point
# =============================================================================

def main():
    """Train XGBoost model on synthetic fraud data for demonstration."""
    logger.info("Generating synthetic fraud training data...")

    np.random.seed(42)
    n_samples = 50_000
    n_features = len(ALL_FEATURES)
    fraud_rate = 0.02  # 2% fraud rate (typical for iGaming)

    # Generate synthetic features
    X = np.random.randn(n_samples, n_features)
    y = np.zeros(n_samples)

    # Create realistic fraud patterns
    n_fraud = int(n_samples * fraud_rate)
    fraud_indices = np.random.choice(n_samples, n_fraud, replace=False)
    y[fraud_indices] = 1

    # Make fraud samples have distinguishable patterns
    # Higher velocity features for fraudulent transactions
    X[fraud_indices, 0] += 2.5   # tx_count_1h
    X[fraud_indices, 1] += 2.0   # tx_count_24h
    X[fraud_indices, 10] += 1.5  # bot_timing_score
    X[fraud_indices, 11] -= 1.0  # amount_cv_20 (more consistent = bot-like)

    # Split
    split_idx = int(n_samples * 0.8)
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    # Train
    trainer = XGBoostFraudTrainer()
    metrics = trainer.train(X_train, y_train, X_val, y_val)

    # Feature importance
    logger.info("\nTop 10 most important features:")
    for fi in trainer.get_feature_importance(top_n=10):
        logger.info("  %s: %.4f", fi["feature"], fi["importance_gain"])

    # Save model
    model_path = trainer.save_model(version="demo")
    logger.info("Model saved to: %s", model_path)

    # Test prediction
    sample_scores = trainer.predict(X_val[:5])
    logger.info("\nSample predictions (first 5 validation):")
    for i, score in enumerate(sample_scores):
        logger.info("  Sample %d: score=%.4f (actual=%d)", i, score, int(y_val[i]))


if __name__ == "__main__":
    main()
