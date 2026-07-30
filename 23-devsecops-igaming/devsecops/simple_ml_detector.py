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
Simple Machine Learning-Based Secret Detector
Uses rule-based and statistical methods to identify potential secrets
"""

import math
import os
import re
import sys
from collections import Counter


class SimpleMLSecretDetector:
    def __init__(self):
        self.secret_patterns = [
            # API keys
            r"sk-[a-zA-Z0-9]{32,}",
            r"pk_[a-zA-Z0-9]{32,}",
            r"ghp_[a-zA-Z0-9]{36}",
            r"shppa_[a-zA-Z0-9]{32,}",
            # AWS keys
            r"AKIA[0-9A-Z]{16}",
            r'aws(.{0,20})?[\'"][0-9a-zA-Z\/+]{40}[\'"]',
            # JWT tokens
            r"eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*",
            # Generic high-entropy strings
            r"[a-zA-Z0-9]{32,}",
            r"[0-9a-f]{64}",  # SHA256
            r"[0-9a-f]{40}",  # SHA1
            r"[0-9A-Za-z]{42}",  # Common secret length
        ]

        self.non_secret_patterns = [
            r"^\d+$",  # Only numbers
            r"^[a-f0-9]+$",  # Only hex (might be hash)
            r"^0x[0-9a-f]+$",  # Hex number
            r"^true|false$",  # Boolean
            r"^null$",  # Null
            r"^undefined$",  # Undefined
        ]

        self.secret_keywords = [
            "key",
            "token",
            "secret",
            "password",
            "auth",
            "credential",
            "private",
            "access",
            "api",
            "jwt",
            "bearer",
            "oauth",
        ]

    def calculate_entropy(self, string):
        """Calculate Shannon entropy of a string."""
        if not string:
            return 0

        counter = Counter(string)
        length = len(string)
        entropy = 0

        for count in counter.values():
            probability = count / length
            entropy -= probability * math.log2(probability)

        return entropy

    def extract_features(self, text):
        """Extract features from text for analysis."""
        features = {}

        # Basic features
        features["length"] = len(text)
        features["has_special_chars"] = bool(
            re.search(r"[!@#$%^&*()_+=\[\]{}|;:,.<>?]", text)
        )
        features["has_mixed_case"] = bool(
            re.search(r"[a-z]", text) and re.search(r"[A-Z]", text)
        )
        features["has_digits"] = bool(re.search(r"\d", text))
        features["entropy"] = self.calculate_entropy(text)

        # Character distribution
        if text:
            digit_count = sum(c.isdigit() for c in text)
            alpha_count = sum(c.isalpha() for c in text)
            features["digit_ratio"] = digit_count / len(text)
            features["alpha_ratio"] = alpha_count / len(text)
            features["special_ratio"] = sum(c in "!@#$%^&*()" for c in text) / len(text)
        else:
            features["digit_ratio"] = 0
            features["alpha_ratio"] = 0
            features["special_ratio"] = 0

        # Pattern matching
        features["matches_secret_pattern"] = any(
            re.match(pattern, text) for pattern in self.secret_patterns
        )
        features["matches_non_secret_pattern"] = any(
            re.match(pattern, text) for pattern in self.non_secret_patterns
        )

        # Context analysis
        features["contains_secret_keywords"] = any(
            keyword in text.lower() for keyword in self.secret_keywords
        )

        return features

    def predict(self, text):
        """Predict if text contains a secret."""
        features = self.extract_features(text)

        # Scoring system
        score = 0
        reasons = []

        # Length scoring
        if features["length"] >= 32:
            score += 2
            reasons.append("Long string (>=32 chars)")
        elif features["length"] >= 20:
            score += 1
            reasons.append("Medium string (>=20 chars)")

        # Entropy scoring
        if features["entropy"] >= 4.5:
            score += 3
            reasons.append("High entropy (>=4.5)")
        elif features["entropy"] >= 4.0:
            score += 2
            reasons.append("Medium-high entropy (>=4.0)")
        elif features["entropy"] >= 3.5:
            score += 1
            reasons.append("Medium entropy (>=3.5)")

        # Character variety scoring
        if features["has_mixed_case"]:
            score += 1
            reasons.append("Mixed case letters")

        if features["has_digits"] and features["has_special_chars"]:
            score += 2
            reasons.append("Contains digits and special chars")
        elif features["has_digits"] or features["has_special_chars"]:
            score += 1
            reasons.append("Contains digits or special chars")

        # Pattern matching
        if features["matches_secret_pattern"]:
            score += 3
            reasons.append("Matches known secret pattern")

        if features["contains_secret_keywords"]:
            score += 2
            reasons.append("Contains secret-related keywords")

        # Penalties
        if features["matches_non_secret_pattern"]:
            score -= 2
            reasons.append("Matches non-secret pattern")

        # Final classification
        is_secret = score >= 5
        confidence = min(score / 10, 1.0)

        return {
            "is_secret": is_secret,
            "confidence": confidence,
            "score": score,
            "reasons": reasons,
            "features": features,
        }


def load_default_training_data():
    """Load default training data."""
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
            "score": result["score"],
            "reasons": result["reasons"],
        }
    except Exception as e:
        return {
            "text": text,
            "error": str(e),
            "is_secret": False,
            "confidence": 0.0,
            "score": 0,
            "reasons": [],
        }


def main():
    """Main function for command-line usage."""
    import argparse

    parser = argparse.ArgumentParser(description="Simple ML-based secret detector")
    parser.add_argument("input", help="Text to analyze or file path if --file is used")
    parser.add_argument(
        "--file", action="store_true", help="Treat input as file path instead of text"
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="Show training examples and model performance",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results in JSON format"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="Confidence threshold for secret detection (default: 0.6)",
    )

    args = parser.parse_args()

    # Initialize detector
    detector = SimpleMLSecretDetector()

    if args.train:
        # Show training performance
        print("🧠 Training model with default data...")
        secret_samples, non_secret_samples = load_default_training_data()

        # Test on training data
        correct = 0
        total = 0

        print("\n📊 Testing on secret samples:")
        for sample in secret_samples:
            result = detector.predict(sample)
            is_correct = result["is_secret"]
            correct += is_correct
            total += 1
            status = "✅" if is_correct else "❌"
            print(f"  {status} {sample[:30]}... (score: {result['score']})")

        print("\n📊 Testing on non-secret samples:")
        for sample in non_secret_samples:
            result = detector.predict(sample)
            is_correct = not result["is_secret"]
            correct += is_correct
            total += 1
            status = "✅" if is_correct else "❌"
            print(f"  {status} {sample[:30]}... (score: {result['score']})")

        accuracy = correct / total if total > 0 else 0
        print(f"\n🎯 Model accuracy: {accuracy:.1%}")
        return

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

        print("🤖 Simple ML Secret Detection Results")
        print("=" * 40)
        print(f"Text: {result['text'][:50]}...")
        print(f"Is secret: {result['is_secret']}")
        print(f"Confidence: {result['confidence']:.1%}")
        print(f"Score: {result['score']}")

        if result["reasons"]:
            print("Reasons:")
            for reason in result["reasons"]:
                print(f"  • {reason}")

        # Apply threshold
        if result["confidence"] >= args.threshold:
            print("⚠️  HIGH PROBABILITY - Potential secret detected!")
            sys.exit(1)
        else:
            print("✅ Low probability - Likely not a secret")
            sys.exit(0)


if __name__ == "__main__":
    main()
