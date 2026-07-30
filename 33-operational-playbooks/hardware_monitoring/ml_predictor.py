#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Hardware Failure Prediction ML Model - Chapter 23: Operational Playbooks

Trains and evaluates machine learning models (Random Forest, Gradient Boosting,
Logistic Regression) to predict hardware failures within 30 days using equipment
age and performance metrics. Includes feature importance analysis, cross-validation,
ROC-AUC scoring, and batch prediction capabilities.

Usage:
    python ml_predictor.py --train hardware_training_data.csv --save-model
    python ml_predictor.py --load-model --predict '{"age_days":900,"cpu_usage":45,...}'
    python ml_predictor.py --load-model --batch-predict current_metrics.csv

Part of the iGaming Platform Engineering book.
"""

import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
import joblib
import matplotlib.pyplot as plt  # ty:ignore[unresolved-import]
import seaborn as sns  # ty:ignore[unresolved-import]
from datetime import datetime, timezone
import warnings
warnings.filterwarnings('ignore')


class HardwareFailurePredictor:
    def __init__(self, model_path: str = 'hardware_failure_model.pkl'):
        self.model_path = model_path
        self.model = None
        self.scaler = StandardScaler()
        self.label_encoders = {}
        self.feature_columns = [
            'age_days', 'cpu_usage', 'memory_usage', 'disk_usage',
            'network_errors', 'temperature', 'wear_level', 'error_count',
            'reallocated_sectors'
        ]
        self.target_column = 'failed_in_30_days'

    def load_and_preprocess_data(self, data_file: str) -> tuple:
        """Load and preprocess training data"""
        print(f"Loading data from {data_file}...")

        # Load data
        df = pd.read_csv(data_file)

        # Basic data cleaning
        df = df.dropna(subset=[self.target_column])

        # Handle missing values
        for col in self.feature_columns:
            if col in df.columns:
                df[col] = df[col].fillna(df[col].median())

        # Encode categorical variables
        categorical_cols = ['component_type', 'component_id']
        for col in categorical_cols:
            if col in df.columns:
                if col not in self.label_encoders:
                    self.label_encoders[col] = LabelEncoder()
                df[f'{col}_encoded'] = self.label_encoders[col].fit_transform(df[col].astype(str))

        # Select features
        feature_cols = self.feature_columns + [f'{col}_encoded' for col in categorical_cols if f'{col}_encoded' in df.columns]
        X = df[feature_cols]
        y = df[self.target_column]

        print(f"Loaded {len(df)} samples with {len(feature_cols)} features")
        print(f"Class distribution: {y.value_counts().to_dict()}")

        return X, y

    def train_model(self, X, y, model_type: str = 'random_forest'):
        """Train the predictive model"""
        print(f"Training {model_type} model...")

        if model_type == 'random_forest':
            self.model = RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                min_samples_split=10,
                min_samples_leaf=5,
                random_state=42,
                class_weight='balanced'
            )
        elif model_type == 'gradient_boosting':
            self.model = GradientBoostingClassifier(
                n_estimators=100,
                max_depth=6,
                learning_rate=0.1,
                random_state=42
            )
        elif model_type == 'logistic_regression':
            self.model = Pipeline([
                ('imputer', SimpleImputer(strategy='median')),
                ('scaler', StandardScaler()),
                ('classifier', LogisticRegression(random_state=42, class_weight='balanced'))
            ])
        else:
            raise ValueError(f"Unknown model type: {model_type}")

        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        # Train model
        self.model.fit(X_train, y_train)

        # Evaluate model
        y_pred = self.model.predict(X_test)
        y_pred_proba = self.model.predict_proba(X_test)[:, 1]

        print("\nModel Performance:")
        print(classification_report(y_test, y_pred))

        print(f"ROC-AUC Score: {roc_auc_score(y_test, y_pred_proba):.3f}")

        # Cross-validation
        cv_scores = cross_val_score(self.model, X, y, cv=5, scoring='roc_auc')
        print(f"Cross-validation ROC-AUC: {cv_scores.mean():.3f} (+/- {cv_scores.std() * 2:.3f})")

        # Feature importance (for tree-based models)
        if hasattr(self.model, 'feature_importances_'):
            feature_importance = pd.DataFrame({
                'feature': X.columns,
                'importance': self.model.feature_importances_
            }).sort_values('importance', ascending=False)

            print("\nTop 10 Feature Importances:")
            print(feature_importance.head(10))

            # Plot feature importance
            plt.figure(figsize=(10, 6))
            sns.barplot(x='importance', y='feature', data=feature_importance.head(10))
            plt.title('Top 10 Feature Importances for Hardware Failure Prediction')
            plt.tight_layout()
            plt.savefig('feature_importance.png', dpi=300, bbox_inches='tight')
            plt.close()

        return X_test, y_test, y_pred, y_pred_proba

    def predict_failure_probability(self, component_data: dict) -> dict:
        """Predict failure probability for a component"""
        if self.model is None:
            raise ValueError("Model not trained or loaded")

        # Prepare input data
        input_df = pd.DataFrame([component_data])

        # Encode categorical variables
        for col in ['component_type', 'component_id']:
            if col in input_df.columns and col in self.label_encoders:
                input_df[f'{col}_encoded'] = self.label_encoders[col].transform(input_df[col].astype(str))

        # Select features
        feature_cols = self.feature_columns + [f'{col}_encoded' for col in ['component_type', 'component_id'] if f'{col}_encoded' in input_df.columns]
        X_input = input_df[feature_cols]

        # Make prediction
        prediction_proba = self.model.predict_proba(X_input)[0]
        prediction = self.model.predict(X_input)[0]

        # Get risk level
        risk_level = self._get_risk_level(prediction_proba[1])

        return {
            'failure_probability': float(prediction_proba[1]),
            'prediction': int(prediction),
            'risk_level': risk_level,
            'confidence': float(max(prediction_proba))
        }

    def _get_risk_level(self, probability: float) -> str:
        """Convert probability to risk level"""
        if probability >= 0.8:
            return 'critical'
        elif probability >= 0.6:
            return 'high'
        elif probability >= 0.3:
            return 'medium'
        elif probability >= 0.1:
            return 'low'
        else:
            return 'minimal'

    def save_model(self):
        """Save trained model to disk"""
        if self.model is None:
            raise ValueError("No model to save")

        model_data = {
            'model': self.model,
            'scaler': self.scaler,
            'label_encoders': self.label_encoders,
            'feature_columns': self.feature_columns,
            'target_column': self.target_column,
            'trained_at': datetime.now().isoformat()
        }

        joblib.dump(model_data, self.model_path)
        print(f"Model saved to {self.model_path}")

    def load_model(self):
        """Load trained model from disk"""
        try:
            model_data = joblib.load(self.model_path)
            self.model = model_data['model']
            self.scaler = model_data.get('scaler', StandardScaler())
            self.label_encoders = model_data.get('label_encoders', {})
            self.feature_columns = model_data.get('feature_columns', self.feature_columns)
            self.target_column = model_data.get('target_column', self.target_column)

            trained_at = model_data.get('trained_at', 'unknown')
            print(f"Model loaded from {self.model_path} (trained at {trained_at})")
            return True
        except FileNotFoundError:
            print(f"Model file {self.model_path} not found")
            return False

    def batch_predict(self, data_file: str, output_file: str | None = None):
        """Make predictions on batch data"""
        if self.model is None:
            raise ValueError("Model not loaded")

        # Load data
        df = pd.read_csv(data_file)

        # Prepare data
        for col in ['component_type', 'component_id']:
            if col in df.columns and col in self.label_encoders:
                df[f'{col}_encoded'] = self.label_encoders[col].transform(df[col].astype(str))

        feature_cols = self.feature_columns + [f'{col}_encoded' for col in ['component_type', 'component_id'] if f'{col}_encoded' in df.columns]
        X = df[feature_cols]

        # Make predictions
        predictions = self.model.predict(X)
        probabilities = self.model.predict_proba(X)[:, 1]

        # Add results to dataframe
        df['failure_probability'] = probabilities
        df['failure_prediction'] = predictions
        df['risk_level'] = [self._get_risk_level(p) for p in probabilities]

        # Save results
        if output_file:
            df.to_csv(output_file, index=False)
            print(f"Predictions saved to {output_file}")
        else:
            output_file = f"predictions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            df.to_csv(output_file, index=False)
            print(f"Predictions saved to {output_file}")

        return df


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Hardware Failure Prediction ML Model')
    parser.add_argument('--train', type=str, help='Train model with data file')
    parser.add_argument('--model-type', type=str, default='random_forest',
                       choices=['random_forest', 'gradient_boosting', 'logistic_regression'],
                       help='Model type to train')
    parser.add_argument('--predict', type=str, help='Make prediction on single component (JSON string)')
    parser.add_argument('--batch-predict', type=str, help='Make predictions on CSV file')
    parser.add_argument('--load-model', action='store_true', help='Load existing model')
    parser.add_argument('--save-model', action='store_true', help='Save model after training')

    args = parser.parse_args()

    predictor = HardwareFailurePredictor()

    if args.load_model:
        predictor.load_model()

    if args.train:
        X, y = predictor.load_and_preprocess_data(args.train)
        predictor.train_model(X, y, args.model_type)

        if args.save_model:
            predictor.save_model()

    elif args.predict:
        import json
        component_data = json.loads(args.predict)
        result = predictor.predict_failure_probability(component_data)
        print(json.dumps(result, indent=2))

    elif args.batch_predict:
        predictor.batch_predict(args.batch_predict)

    else:
        print("No action specified. Use --help for options.")


if __name__ == '__main__':
    main()
