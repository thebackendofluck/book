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
Entropy Analyzer for Secret Detection
Analyzes text for high-entropy strings that might be secrets.

Part of the iGaming DevSecOps pipeline. High-entropy strings in source
code often indicate leaked API keys, tokens, or credentials -- a critical
risk for platforms handling financial transactions and player data.
"""

import math
import re
import sys
from collections import Counter


def calculate_entropy(string):
    """Calculate Shannon entropy of a string."""
    if not string:
        return 0

    # Count character frequencies
    counter = Counter(string)
    length = len(string)

    # Calculate entropy
    entropy = 0
    for count in counter.values():
        probability = count / length
        entropy -= probability * math.log2(probability)

    return entropy


def detect_high_entropy_strings(text, min_entropy=4.0, min_length=20):
    """Detect high-entropy strings that might be secrets."""
    # Pattern for potential secrets (alphanumeric with special chars)
    pattern = rf"[a-zA-Z0-9\-_+/]{{{min_length},}}"

    potential_secrets = []
    for match in re.finditer(pattern, text):
        string = match.group()
        entropy = calculate_entropy(string)

        if entropy >= min_entropy:
            potential_secrets.append(
                {
                    "string": string,
                    "entropy": entropy,
                    "position": match.span(),
                    "length": len(string),
                }
            )

    return potential_secrets


def analyze_file(file_path, min_entropy=4.0, min_length=20):
    """Analyze a file for high-entropy strings."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        secrets = detect_high_entropy_strings(content, min_entropy, min_length)

        return {"file": file_path, "secrets_found": len(secrets), "secrets": secrets}
    except Exception as e:
        return {"file": file_path, "error": str(e), "secrets_found": 0, "secrets": []}


def main():
    """Main function for command-line usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyze text for high-entropy strings that might be secrets"
    )
    parser.add_argument("input", help="Input file path or text to analyze")
    parser.add_argument(
        "--min-entropy",
        type=float,
        default=4.0,
        help="Minimum entropy threshold (default: 4.0)",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=20,
        help="Minimum string length to analyze (default: 20)",
    )
    parser.add_argument(
        "--file", action="store_true", help="Treat input as file path instead of text"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results in JSON format"
    )

    args = parser.parse_args()

    if args.file:
        result = analyze_file(args.input, args.min_entropy, args.min_length)
    else:
        secrets = detect_high_entropy_strings(
            args.input, args.min_entropy, args.min_length
        )
        result = {"input": "text", "secrets_found": len(secrets), "secrets": secrets}

    if args.json:
        import json

        print(json.dumps(result, indent=2))
    else:
        if "error" in result:
            print(f"Error: {result['error']}")
            sys.exit(1)

        print("Entropy Analysis Results")
        print("=" * 50)
        print(f"Input: {result.get('input', result.get('file', 'unknown'))}")
        print(f"Secrets found: {result['secrets_found']}")
        print(f"Min entropy: {args.min_entropy}")
        print(f"Min length: {args.min_length}")

        if result["secrets"]:
            print("\nPotential Secrets:")
            for i, secret in enumerate(result["secrets"], 1):  # ty:ignore[invalid-argument-type]
                truncated = (
                    secret["string"][:30] + "..."
                    if len(secret["string"]) > 30
                    else secret["string"]
                )
                print(f"  {i}. {truncated}")
                print(f"     Entropy: {secret['entropy']:.2f}")
                print(f"     Length: {secret['length']}")
                print(f"     Position: {secret['position']}")
                print()
        else:
            print("No high-entropy strings detected")

    sys.exit(0 if result["secrets_found"] == 0 else 1)


if __name__ == "__main__":
    main()
