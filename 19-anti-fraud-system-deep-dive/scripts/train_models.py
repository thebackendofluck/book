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
Model Training Script

This script provides a convenient interface for training fraud detection models
using the training pipeline.
"""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from models.train_initial_models import FraudModelTrainer  # ty:ignore[unresolved-import]


def main():
    """Main training script"""

    parser = argparse.ArgumentParser(
        description="Train fraud detection models",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Train models with default settings
  python scripts/train_models.py --data data/training_features.parquet

  # Train with custom output directory
  python scripts/train_models.py --data data/training.csv \
    --output models/production

  # Train with custom contamination rate for anomaly detection
  python scripts/train_models.py --data data/training.parquet \
    --contamination 0.05

  # Train with custom test size
  python scripts/train_models.py --data data/training.csv --test-size 0.3
        """
    )

    parser.add_argument(
        "--data",
        required=True,
        help="Path to training data file (CSV or Parquet)"
    )

    parser.add_argument(
        "--output",
        default="models/initial",
        help="Output directory for trained models (default: models/initial)"
    )

    parser.add_argument(
        "--test-size",
        type=float,
        default=0.2,
        help="Fraction of data to use for testing (default: 0.2)"
    )

    parser.add_argument(
        "--contamination",
        type=float,
        default=0.1,
        help="Contamination rate for Isolation Forest (default: 0.1)"
    )

    parser.add_argument(
        "--experiment",
        default="fraud_detection_initial",
        help="MLflow experiment name (default: fraud_detection_initial)"
    )

    parser.add_argument(
        "--skip-evaluation",
        action="store_true",
        help="Skip model evaluation on test set"
    )

    args = parser.parse_args()

    # Validate inputs
    data_path = Path(args.data)
    if not data_path.exists():
        print(f"Error: Training data file not found: {data_path}")
        sys.exit(1)

    # Create output directory
    output_path = Path(args.output)
    output_path.mkdir(parents=True, exist_ok=True)

    print("🚀 Starting Fraud Detection Model Training")
    print(f"📊 Data file: {data_path}")
    print(f"📁 Output directory: {output_path}")
    print(f"🧪 Test size: {args.test_size}")
    print(f"🔍 Contamination rate: {args.contamination}")
    print(f"🧪 MLflow experiment: {args.experiment}")
    print("-" * 50)

    try:
        # Initialize trainer
        trainer = FraudModelTrainer(args.experiment)

        # Load and prepare data
        print("📥 Loading training data...")
        X, y = trainer.load_training_data(str(data_path))

        # Split data
        from sklearn.model_selection import train_test_split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=args.test_size, random_state=42, stratify=y
        )

        print(f"✅ Data loaded: {len(X_train)} training, "
              f"{len(X_test)} test samples")
        print(f"🎯 Target distribution: {y_train.value_counts().to_dict()}")

        # Preprocess training data
        print("🔧 Preprocessing data...")
        X_train_processed, y_train_processed = trainer.preprocess_data(
            X_train, y_train, is_training=True)

        # Train models
        print("🤖 Training models...")
        print("  • Training Isolation Forest...")
        trainer.train_isolation_forest(X_train_processed, args.contamination)

        print("  • Training Random Forest...")
        trainer.train_random_forest(X_train_processed, y_train_processed)

        print("  • Training XGBoost...")
        trainer.train_xgboost(X_train_processed, y_train_processed)

        # Evaluate models
        if not args.skip_evaluation:
            print("📊 Evaluating models...")
            evaluation_results = trainer.evaluate_models(X_test, y_test)

            print("\n📈 Model Performance Summary:")
            print("-" * 40)
            for model_name, results in evaluation_results.items():
                auc = results['auc']
                print(f"  {model_name}: AUC = {auc:.4f}")
        else:
            print("⏭️  Skipping evaluation as requested")

        # Save models
        print("💾 Saving models...")
        trainer.save_models(str(output_path))

        print("✅ Training completed successfully!")
        print(f"📁 Models saved to: {output_path}")
        print("\n📋 Saved files:")
        for file_path in output_path.glob("*"):
            print(f"  • {file_path.name}")

        print("\n🚀 Next steps:")
        print("1. Review model performance in MLflow UI")
        print("2. Deploy models using the model serving service")
        print("3. Set up monitoring and alerting")
        print("4. Run integration tests")

    except Exception as e:
        print(f"❌ Training failed: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()