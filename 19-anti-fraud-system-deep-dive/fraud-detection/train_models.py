#!/usr/bin/env python3
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
ML Model Training for Fraud Detection

Trains baseline models -- Isolation Forest (unsupervised anomaly detection),
Random Forest, and XGBoost (supervised classification) -- on engineered
features.  All experiments are tracked with MLflow.

Usage:
    python train_models.py --data training_data.parquet --output models/initial

Reference implementation for Chapter 41: Anti-Fraud System Deep Dive.
"""

import argparse
import json
import pickle
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import mlflow  # ty:ignore[unresolved-import]
import mlflow.sklearn  # ty:ignore[unresolved-import]
import mlflow.xgboost  # ty:ignore[unresolved-import]
import numpy as np
import pandas as pd
import structlog
import xgboost as xgb  # ty:ignore[unresolved-import]
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler

logger = structlog.get_logger(__name__)


class FraudModelTrainer:
    """Orchestrates training, evaluation, and serialisation of fraud models."""

    def __init__(self, experiment_name: str = "fraud_detection_initial"):
        self.experiment_name = experiment_name
        self.models: Dict[str, Any] = {}
        self.scalers: Dict[str, StandardScaler] = {}
        self.encoders: Dict[str, LabelEncoder] = {}
        self.feature_columns: list = []
        mlflow.set_experiment(experiment_name)

    # ------------------------------------------------------------------
    # Data loading & preprocessing
    # ------------------------------------------------------------------

    def load_training_data(self, data_path: str) -> Tuple[pd.DataFrame, pd.Series]:
        logger.info("Loading training data", path=data_path)

        if data_path.endswith(".parquet"):
            df = pd.read_parquet(data_path)
        elif data_path.endswith(".csv"):
            df = pd.read_csv(data_path)
        else:
            raise ValueError(f"Unsupported format: {data_path}")

        # Auto-detect target column
        for col in ("is_fraud", "fraud_label", "target", "label"):
            if col in df.columns:
                X = df.drop(columns=[col])
                y = df[col]
                self.feature_columns = X.columns.tolist()
                logger.info("Data loaded", shape=df.shape, target=col)
                return X, y

        raise ValueError("Target column not found (tried: is_fraud, fraud_label, target, label)")

    def preprocess_data(
        self, X: pd.DataFrame, y: pd.Series, is_training: bool = True
    ) -> Tuple[pd.DataFrame, pd.Series]:
        X = X.fillna(X.mean(numeric_only=True)).fillna(0)

        # Encode categoricals
        cat_cols = X.select_dtypes(include=["object", "category"]).columns
        for col in cat_cols:
            if is_training:
                enc = LabelEncoder()
                X[col] = enc.fit_transform(X[col].astype(str))
                self.encoders[col] = enc
            elif col in self.encoders:
                enc = self.encoders[col]
                known = set(enc.classes_)
                X[col] = X[col].astype(str).apply(lambda v: v if v in known else "unknown")
                enc.classes_ = np.array(list(enc.classes_) + ["unknown"])
                X[col] = enc.transform(X[col])

        # Scale numerics
        num_cols = X.select_dtypes(include=[np.number]).columns
        if is_training:
            scaler = StandardScaler()
            X[num_cols] = scaler.fit_transform(X[num_cols])
            self.scalers["features"] = scaler
        elif "features" in self.scalers:
            X[num_cols] = self.scalers["features"].transform(X[num_cols])

        return X, y

    # ------------------------------------------------------------------
    # Model training
    # ------------------------------------------------------------------

    def train_isolation_forest(
        self, X: pd.DataFrame, contamination: float = 0.1
    ) -> IsolationForest:
        logger.info("Training Isolation Forest", contamination=contamination)

        with mlflow.start_run(run_name="isolation_forest"):
            model = IsolationForest(
                contamination=contamination, random_state=42, n_estimators=100
            )
            model.fit(X)

            scores = model.score_samples(X)
            anomaly_rate = (model.predict(X) == -1).mean()

            mlflow.log_params({"contamination": contamination, "n_estimators": 100})
            mlflow.log_metrics({"anomaly_rate": anomaly_rate, "mean_score": scores.mean()})
            mlflow.sklearn.log_model(model, "model")

        self.models["isolation_forest"] = model
        logger.info("Isolation Forest trained", anomaly_rate=anomaly_rate)
        return model

    def train_random_forest(
        self, X: pd.DataFrame, y: pd.Series
    ) -> RandomForestClassifier:
        logger.info("Training Random Forest")

        with mlflow.start_run(run_name="random_forest"):
            model = RandomForestClassifier(
                n_estimators=100, max_depth=10, class_weight="balanced", random_state=42
            )
            cv = cross_val_score(model, X, y, cv=5, scoring="roc_auc")
            model.fit(X, y)

            y_proba = model.predict_proba(X)[:, 1]
            auc = roc_auc_score(y, y_proba)

            mlflow.log_params({"n_estimators": 100, "max_depth": 10})
            mlflow.log_metrics({"cv_auc_mean": cv.mean(), "cv_auc_std": cv.std(), "train_auc": auc})

            for feat, imp in zip(X.columns, model.feature_importances_):
                mlflow.log_metric(f"fi_{feat}", imp)

            mlflow.sklearn.log_model(model, "model")

        self.models["random_forest"] = model
        logger.info("Random Forest trained", train_auc=auc)
        return model

    def train_xgboost(self, X: pd.DataFrame, y: pd.Series) -> xgb.XGBClassifier:
        logger.info("Training XGBoost")

        with mlflow.start_run(run_name="xgboost"):
            spw = len(y[y == 0]) / max(len(y[y == 1]), 1)
            model = xgb.XGBClassifier(
                objective="binary:logistic",
                n_estimators=100,
                max_depth=6,
                learning_rate=0.1,
                scale_pos_weight=spw,
                random_state=42,
            )
            cv = cross_val_score(model, X, y, cv=5, scoring="roc_auc")
            model.fit(X, y)

            y_proba = model.predict_proba(X)[:, 1]
            auc = roc_auc_score(y, y_proba)

            mlflow.log_params(
                {"objective": "binary:logistic", "n_estimators": 100,
                 "max_depth": 6, "lr": 0.1, "spw": spw}
            )
            mlflow.log_metrics({"cv_auc_mean": cv.mean(), "cv_auc_std": cv.std(), "train_auc": auc})

            for feat, imp in model.get_booster().get_score(importance_type="gain").items():
                mlflow.log_metric(f"fi_{feat}", imp)

            mlflow.xgboost.log_model(model, "model")

        self.models["xgboost"] = model
        logger.info("XGBoost trained", train_auc=auc)
        return model

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate_models(
        self, X_test: pd.DataFrame, y_test: pd.Series
    ) -> Dict[str, Any]:
        results = {}
        for name, model in self.models.items():
            X_proc, _ = self.preprocess_data(X_test.copy(), y_test, is_training=False)

            if hasattr(model, "predict_proba"):
                y_proba = model.predict_proba(X_proc)[:, 1]
                y_pred = (y_proba > 0.5).astype(int)
            else:
                preds = model.predict(X_proc)
                y_pred = (preds == -1).astype(int)
                y_proba = y_pred.astype(float)

            auc = roc_auc_score(y_test, y_proba)
            results[name] = {
                "auc": auc,
                "classification_report": classification_report(y_test, y_pred, output_dict=True),
                "confusion_matrix": confusion_matrix(y_test, y_pred).tolist(),
            }
            logger.info(f"{name} evaluation", auc=auc)

        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_models(self, output_dir: str):
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        for name, model in self.models.items():
            with open(out / f"{name}.pkl", "wb") as f:
                pickle.dump(model, f)

        with open(out / "preprocessing.pkl", "wb") as f:
            pickle.dump(
                {"scalers": self.scalers, "encoders": self.encoders,
                 "feature_columns": self.feature_columns}, f
            )

        with open(out / "metadata.json", "w") as f:
            json.dump(
                {"experiment": self.experiment_name,
                 "feature_columns": self.feature_columns,
                 "models": list(self.models.keys()),
                 "created_at": pd.Timestamp.now().isoformat()}, f, indent=2
            )

        logger.info("Models saved", output_dir=output_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Train fraud detection models")
    parser.add_argument("--data", required=True, help="Path to training data")
    parser.add_argument("--output", default="models/initial", help="Output directory")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--contamination", type=float, default=0.1)
    parser.add_argument("--experiment", default="fraud_detection_initial")
    args = parser.parse_args()

    trainer = FraudModelTrainer(args.experiment)

    try:
        X, y = trainer.load_training_data(args.data)
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=args.test_size, random_state=42, stratify=y
        )

        X_train, y_train = trainer.preprocess_data(X_train, y_train, is_training=True)

        trainer.train_isolation_forest(X_train, args.contamination)
        trainer.train_random_forest(X_train, y_train)
        trainer.train_xgboost(X_train, y_train)

        results = trainer.evaluate_models(X_test, y_test)

        (Path(args.output) / "evaluation_results.json").write_text(
            json.dumps(results, indent=2, default=str)
        )

        trainer.save_models(args.output)

        print("\n" + "=" * 50)
        print("MODEL TRAINING COMPLETED")
        print("=" * 50)
        for name, r in results.items():
            print(f"  {name}: AUC = {r['auc']:.4f}")

    except Exception as e:
        logger.error("Training failed", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
