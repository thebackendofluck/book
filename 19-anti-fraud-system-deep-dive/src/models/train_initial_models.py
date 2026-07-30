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
Initial ML Model Training for Fraud Detection

This script trains baseline machine learning models for fraud detection
using engineered features from the feature engineering pipeline.
"""

import argparse
import json
import pickle
import sys
from pathlib import Path
from typing import Dict, Any, Tuple, List
import structlog

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, IsolationForest
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, roc_auc_score, confusion_matrix
from sklearn.preprocessing import StandardScaler, LabelEncoder
import xgboost as xgb  # ty:ignore[unresolved-import]
import mlflow  # ty:ignore[unresolved-import]
import mlflow.sklearn  # ty:ignore[unresolved-import]
import mlflow.xgboost  # ty:ignore[unresolved-import]

from ..data_ingestion.config import settings  # ty:ignore[unresolved-import]

logger = structlog.get_logger(__name__)


class FraudModelTrainer:
    """Train initial fraud detection models"""

    def __init__(self, experiment_name: str = "fraud_detection_initial"):
        self.experiment_name = experiment_name
        self.models = {}
        self.scalers = {}
        self.encoders = {}
        self.feature_columns = []

        # Set up MLflow
        mlflow.set_experiment(experiment_name)

    def load_training_data(self, data_path: str) -> Tuple[pd.DataFrame, pd.Series]:
        """Load and prepare training data"""

        logger.info("Loading training data", path=data_path)

        if data_path.endswith('.parquet'):
            df = pd.read_parquet(data_path)
        elif data_path.endswith('.csv'):
            df = pd.read_csv(data_path)
        else:
            raise ValueError(f"Unsupported file format: {data_path}")

        logger.info("Data loaded", shape=df.shape, columns=len(df.columns))

        # Assume the target column is named 'is_fraud' or 'fraud_label'
        target_columns = ['is_fraud', 'fraud_label', 'target', 'label']
        target_col = None

        for col in target_columns:
            if col in df.columns:
                target_col = col
                break

        if target_col is None:
            raise ValueError(f"Could not find target column. Tried: {target_columns}")

        # Separate features and target
        X = df.drop(columns=[target_col])
        y = df[target_col]

        # Store feature columns for later use
        self.feature_columns = X.columns.tolist()

        logger.info("Data prepared", features=len(self.feature_columns), target_col=target_col)
        return X, y

    def preprocess_data(self, X: pd.DataFrame, y: pd.Series, is_training: bool = True) -> Tuple[pd.DataFrame, pd.Series]:
        """Preprocess data for training"""

        logger.info("Preprocessing data", is_training=is_training)

        # Handle missing values
        X = X.fillna(X.mean(numeric_only=True))
        X = X.fillna(0)  # Fill any remaining NaN with 0

        # Encode categorical variables
        categorical_cols = X.select_dtypes(include=['object', 'category']).columns

        for col in categorical_cols:
            if is_training:
                encoder = LabelEncoder()
                X[col] = encoder.fit_transform(X[col].astype(str))
                self.encoders[col] = encoder
            else:
                # Use stored encoder for prediction
                if col in self.encoders:
                    encoder = self.encoders[col]
                    # Handle unknown categories
                    X[col] = X[col].astype(str)
                    known_categories = set(encoder.classes_)
                    X[col] = X[col].apply(lambda x: x if x in known_categories else 'unknown')
                    encoder_classes = list(encoder.classes_) + ['unknown']
                    encoder.classes_ = np.array(encoder_classes)
                    X[col] = encoder.transform(X[col])

        # Scale numerical features
        numerical_cols = X.select_dtypes(include=[np.number]).columns

        if is_training:
            scaler = StandardScaler()
            X[numerical_cols] = scaler.fit_transform(X[numerical_cols])
            self.scalers['features'] = scaler
        else:
            if 'features' in self.scalers:
                X[numerical_cols] = self.scalers['features'].transform(X[numerical_cols])

        logger.info("Data preprocessed", shape=X.shape)
        return X, y

    def train_isolation_forest(self, X: pd.DataFrame, contamination: float = 0.1) -> IsolationForest:
        """Train Isolation Forest for unsupervised anomaly detection"""

        logger.info("Training Isolation Forest", contamination=contamination)

        with mlflow.start_run(run_name="isolation_forest"):
            model = IsolationForest(
                contamination=contamination,
                random_state=42,
                n_estimators=100
            )

            model.fit(X)

            # Log parameters and model
            mlflow.log_param("contamination", contamination)
            mlflow.log_param("n_estimators", 100)
            mlflow.log_param("random_state", 42)
            mlflow.sklearn.log_model(model, "model")

            # Calculate anomaly scores on training data
            scores = model.score_samples(X)
            anomaly_predictions = model.predict(X)

            # Log metrics
            anomaly_rate = (anomaly_predictions == -1).mean()
            mlflow.log_metric("anomaly_rate", anomaly_rate)
            mlflow.log_metric("mean_anomaly_score", scores.mean())

        self.models['isolation_forest'] = model
        logger.info("Isolation Forest trained", anomaly_rate=anomaly_rate)
        return model

    def train_random_forest(self, X: pd.DataFrame, y: pd.Series) -> RandomForestClassifier:
        """Train Random Forest classifier"""

        logger.info("Training Random Forest")

        with mlflow.start_run(run_name="random_forest"):
            model = RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                class_weight='balanced',
                random_state=42
            )

            # Cross-validation
            cv_scores = cross_val_score(model, X, y, cv=5, scoring='roc_auc')
            logger.info("CV Scores", mean=cv_scores.mean(), std=cv_scores.std())

            # Train final model
            model.fit(X, y)

            # Predictions and metrics
            y_pred = model.predict(X)
            y_pred_proba = model.predict_proba(X)[:, 1]

            # Log parameters and metrics
            mlflow.log_param("n_estimators", 100)
            mlflow.log_param("max_depth", 10)
            mlflow.log_param("class_weight", "balanced")

            mlflow.log_metric("cv_auc_mean", cv_scores.mean())
            mlflow.log_metric("cv_auc_std", cv_scores.std())
            mlflow.log_metric("train_auc", roc_auc_score(y, y_pred_proba))

            # Feature importance
            feature_importance = dict(zip(X.columns, model.feature_importances_))
            for feature, importance in feature_importance.items():
                mlflow.log_metric(f"feature_importance_{feature}", importance)

            mlflow.sklearn.log_model(model, "model")

        self.models['random_forest'] = model
        logger.info("Random Forest trained", train_auc=roc_auc_score(y, y_pred_proba))
        return model

    def train_xgboost(self, X: pd.DataFrame, y: pd.Series) -> xgb.XGBClassifier:
        """Train XGBoost classifier"""

        logger.info("Training XGBoost")

        with mlflow.start_run(run_name="xgboost"):
            # Calculate scale_pos_weight for imbalanced classes
            scale_pos_weight = len(y[y == 0]) / len(y[y == 1]) if len(y[y == 1]) > 0 else 1

            model = xgb.XGBClassifier(
                objective='binary:logistic',
                n_estimators=100,
                max_depth=6,
                learning_rate=0.1,
                scale_pos_weight=scale_pos_weight,
                random_state=42
            )

            # Cross-validation
            cv_scores = cross_val_score(model, X, y, cv=5, scoring='roc_auc')
            logger.info("CV Scores", mean=cv_scores.mean(), std=cv_scores.std())

            # Train final model
            model.fit(X, y)

            # Predictions and metrics
            y_pred = model.predict(X)
            y_pred_proba = model.predict_proba(X)[:, 1]

            # Log parameters and metrics
            mlflow.log_param("objective", "binary:logistic")
            mlflow.log_param("n_estimators", 100)
            mlflow.log_param("max_depth", 6)
            mlflow.log_param("learning_rate", 0.1)
            mlflow.log_param("scale_pos_weight", scale_pos_weight)

            mlflow.log_metric("cv_auc_mean", cv_scores.mean())
            mlflow.log_metric("cv_auc_std", cv_scores.std())
            mlflow.log_metric("train_auc", roc_auc_score(y, y_pred_proba))

            # Feature importance
            feature_importance = model.get_booster().get_score(importance_type='gain')
            for feature, importance in feature_importance.items():
                mlflow.log_metric(f"feature_importance_{feature}", importance)

            mlflow.xgboost.log_model(model, "model")

        self.models['xgboost'] = model
        logger.info("XGBoost trained", train_auc=roc_auc_score(y, y_pred_proba))
        return model

    def evaluate_models(self, X_test: pd.DataFrame, y_test: pd.Series) -> Dict[str, Any]:
        """Evaluate all trained models on test data"""

        logger.info("Evaluating models on test data")

        results = {}

        for model_name, model in self.models.items():
            logger.info(f"Evaluating {model_name}")

            # Preprocess test data
            X_test_processed, _ = self.preprocess_data(X_test, y_test, is_training=False)

            # Predictions
            if hasattr(model, 'predict_proba'):
                y_pred_proba = model.predict_proba(X_test_processed)[:, 1]
                y_pred = (y_pred_proba > 0.5).astype(int)
            else:
                # For unsupervised models like Isolation Forest
                y_pred = model.predict(X_test_processed)
                y_pred = (y_pred == -1).astype(int)  # Convert to 0/1
                y_pred_proba = y_pred.astype(float)

            # Calculate metrics
            auc_score = roc_auc_score(y_test, y_pred_proba)
            report = classification_report(y_test, y_pred, output_dict=True)
            conf_matrix = confusion_matrix(y_test, y_pred)

            results[model_name] = {
                'auc': auc_score,
                'classification_report': report,
                'confusion_matrix': conf_matrix.tolist(),
                'predictions': y_pred.tolist(),
                'probabilities': y_pred_proba.tolist()
            }

            logger.info(f"{model_name} evaluation", auc=auc_score)

        return results

    def save_models(self, output_dir: str):
        """Save trained models and preprocessing objects"""

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        logger.info("Saving models", output_dir=output_dir)

        # Save models
        for model_name, model in self.models.items():
            model_path = output_path / f"{model_name}.pkl"
            with open(model_path, 'wb') as f:
                pickle.dump(model, f)
            logger.info(f"Saved {model_name}", path=model_path)

        # Save preprocessing objects
        preprocessing = {
            'scalers': self.scalers,
            'encoders': self.encoders,
            'feature_columns': self.feature_columns
        }

        preprocessing_path = output_path / "preprocessing.pkl"
        with open(preprocessing_path, 'wb') as f:
            pickle.dump(preprocessing, f)

        logger.info("Preprocessing objects saved", path=preprocessing_path)

        # Save model metadata
        metadata = {
            'experiment_name': self.experiment_name,
            'feature_columns': self.feature_columns,
            'models': list(self.models.keys()),
            'created_at': pd.Timestamp.now().isoformat()
        }

        metadata_path = output_path / "metadata.json"
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        logger.info("Metadata saved", path=metadata_path)

    def load_models(self, model_dir: str):
        """Load trained models and preprocessing objects"""

        model_path = Path(model_dir)

        # Load preprocessing objects
        preprocessing_path = model_path / "preprocessing.pkl"
        with open(preprocessing_path, 'rb') as f:
            preprocessing = pickle.load(f)

        self.scalers = preprocessing['scalers']
        self.encoders = preprocessing['encoders']
        self.feature_columns = preprocessing['feature_columns']

        # Load models
        for model_file in model_path.glob("*.pkl"):
            if model_file.name != "preprocessing.pkl":
                model_name = model_file.stem
                with open(model_file, 'rb') as f:
                    self.models[model_name] = pickle.load(f)

        logger.info("Models loaded", count=len(self.models))


def main():
    """Main training function"""

    parser = argparse.ArgumentParser(description="Train initial fraud detection models")
    parser.add_argument("--data", required=True, help="Path to training data file")
    parser.add_argument("--output", default="models/initial", help="Output directory for models")
    parser.add_argument("--test-size", type=float, default=0.2, help="Test set size")
    parser.add_argument("--contamination", type=float, default=0.1, help="Contamination rate for Isolation Forest")
    parser.add_argument("--experiment", default="fraud_detection_initial", help="MLflow experiment name")

    args = parser.parse_args()

    # Initialize trainer
    trainer = FraudModelTrainer(args.experiment)

    try:
        # Load and split data
        X, y = trainer.load_training_data(args.data)
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=args.test_size, random_state=42, stratify=y
        )

        logger.info("Data split", train_shape=X_train.shape, test_shape=X_test.shape)

        # Preprocess training data
        X_train_processed, y_train_processed = trainer.preprocess_data(X_train, y_train, is_training=True)

        # Train models
        logger.info("Starting model training")

        trainer.train_isolation_forest(X_train_processed, args.contamination)
        trainer.train_random_forest(X_train_processed, y_train_processed)
        trainer.train_xgboost(X_train_processed, y_train_processed)

        # Evaluate models
        evaluation_results = trainer.evaluate_models(X_test, y_test)

        # Save results
        results_path = Path(args.output) / "evaluation_results.json"
        with open(results_path, 'w') as f:
            json.dump(evaluation_results, f, indent=2, default=str)

        logger.info("Evaluation results saved", path=results_path)

        # Save models
        trainer.save_models(args.output)

        # Print summary
        print("\n" + "="*50)
        print("MODEL TRAINING COMPLETED")
        print("="*50)
        print(f"Training data: {args.data}")
        print(f"Models saved to: {args.output}")
        print(f"Feature count: {len(trainer.feature_columns)}")
        print("\nModel Performance (AUC):")
        for model_name, results in evaluation_results.items():
            print(".4f")

        print("\nNext steps:")
        print("1. Review model performance in MLflow")
        print("2. Deploy models using the model serving service")
        print("3. Set up monitoring and alerting")

    except Exception as e:
        logger.error("Training failed", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()