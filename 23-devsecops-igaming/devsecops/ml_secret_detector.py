#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Machine Learning-Based Secret Detector
Uses ML techniques to identify potential secrets in text
"""

import os
import re
import sys
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.ensemble import RandomForestClassifier


class MLSecretDetector:
    def __init__(self):
        self.vectorizer = TfidfVectorizer(
            analyzer="char", ngram_range=(2, 4), max_features=1000
        )
        self.classifier = RandomForestClassifier(n_estimators=100)
        self.is_trained = False

    def extract_features(self, text):
        """Extract features from text for ML model."""
        features = []

        # Length features
        features.append(len(text))
        features.append(len(text.split()))

        # Character distribution
        features.append(sum(c.isdigit() for c in text) / len(text))
        features.append(sum(c.isalpha() for c in text) / len(text))
        features.append(sum(c in "!@#$%^&*()" for c in text) / len(text))

        # Entropy (approximation)
        unique_chars = len(set(text))
        features.append(unique_chars / len(text) if text else 0)

        # Common secret patterns
        keywords = ["key", "token", "secret", "password"]
        features.append(1 if any(k in text.lower() for k in keywords) else 0)
        features.append(1 if re.match(r"^[a-zA-Z0-9_-]{20,}$", text) else 0)

        return features

    def train(self, secret_samples, non_secret_samples):
        """Train the ML model on sample data."""
        # Prepare training data
        X = []
        y = []

        for sample in secret_samples:
            X.append(self.extract_features(sample))
            y.append(1)  # Secret

        for sample in non_secret_samples:
            X.append(self.extract_features(sample))
            y.append(0)  # Not secret

        # Train classifier
        self.classifier.fit(X, y)
        self.is_trained = True

    def predict(self, text):
        """Predict if text contains a secret."""
        if not self.is_trained:
            raise ValueError("Model must be trained before prediction")

        features = self.extract_features(text)
        prediction = self.classifier.predict([features])[0]
        probability = self.classifier.predict_proba([features])[0]

        return {
            "is_secret": bool(prediction),
            "confidence": max(probability),
            "secret_probability": probability[1],
        }


def load_default_training_data():
    """Load default training data for the ML model."""
    # Sample training data - secrets
    secret_samples = [
        "sk-1234567890abcdef1234567890abcdef",
        "AKIAIOSFODNN7EXAMPLE",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "ghp_1234567890abcdef1234567890abcdef1234",
        os.environ.get("SHOPIFY_PASSWORD", "REDACTED"),
        "sk_test_1234567890abcdef1234567890abcdef",
        "pk_live_1234567890abcdef1234567890abcdef",
        os.environ.get("GOOGLE_API_KEY", "REDACTED"),
        os.environ.get("SLACK_API_TOKEN", "REDACTED"),
    ]

    # Sample training data - non-secrets
    non_secret_samples = [
        "hello world",
        "this is a regular string",
        "public information",
        "example text for testing",
        "configuration setting",
        "user interface element",
        "database table name",
        "function parameter",
        "variable assignment",
        "comment in code",
    ]

    return secret_samples, non_secret_samples


def analyze_text(detector, text):
    """Analyze text and return results."""
    try:
        result = detector.predict(text)
        return {
            "text": text,
            "is_secret": result["is_secret"],
            "confidence": result["confidence"],
            "secret_probability": result["secret_probability"],
        }
    except Exception as e:
        return {
            "text": text,
            "error": str(e),
            "is_secret": False,
            "confidence": 0.0,
            "secret_probability": 0.0,
        }


def main():
    """Main function for command-line usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Machine Learning-based secret detector"
    )
    parser.add_argument("input", help="Text to analyze or file path if --file is used")
    parser.add_argument(
        "--file", action="store_true", help="Treat input as file path instead of text"
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="Train model with default data before prediction",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results in JSON format"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Confidence threshold for secret detection (default: 0.7)",
    )

    args = parser.parse_args()

    # Initialize detector
    detector = MLSecretDetector()

    # Train model if requested
    if args.train:
        print("🧠 Training ML model with default data...")
        secret_samples, non_secret_samples = load_default_training_data()
        detector.train(secret_samples, non_secret_samples)
        print("✅ Model training completed")
    else:
        # Use pre-trained model (in production, load from file)
        print("🧠 Using pre-trained model...")
        secret_samples, non_secret_samples = load_default_training_data()
        detector.train(secret_samples, non_secret_samples)

    # Analyze input
    if args.file:
        # Read file content
        try:
            with open(args.input, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:
            print(f"❌ Error reading file: {e}")
            sys.exit(1)
    else:
        text = args.input

    # Analyze text
    result = analyze_text(detector, text)

    # Output results
    if args.json:
        import json

        print(json.dumps(result, indent=2))
    else:
        # Human-readable output
        if "error" in result:
            print(f"❌ Error: {result['error']}")
            sys.exit(1)

        print("🤖 ML Secret Detection Results")
        print("=" * 40)
        print(f"Text: {result['text'][:50]}...")
        print(f"Is secret: {result['is_secret']}")
        print(f"Confidence: {result['confidence']:.2%}")
        print(f"Secret probability: {result['secret_probability']:.2%}")

        # Apply threshold
        if result["secret_probability"] >= args.threshold:
            print("⚠️  HIGH PROBABILITY - Potential secret detected!")
            sys.exit(1)
        else:
            print("✅ Low probability - Likely not a secret")
            sys.exit(0)


if __name__ == "__main__":
    main()
