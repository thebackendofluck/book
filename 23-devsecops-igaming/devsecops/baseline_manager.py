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
Baseline Manager for Detect-Secrets
Manages detect-secrets baseline configuration and validation
"""

import json
import subprocess
import sys
from pathlib import Path


class SecretBaselineManager:
    def __init__(self, baseline_file=".secrets.baseline"):
        self.baseline_file = Path(baseline_file)

    def create_baseline(self):
        """Create initial baseline for repository."""
        try:
            cmd = ["detect-secrets", "scan", "--baseline", str(self.baseline_file)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(f"✅ Baseline created: {self.baseline_file}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to create baseline: {e}")
            return False
        except FileNotFoundError:
            print(
                "❌ detect-secrets not found. "
                "Please install it first: pip install detect-secrets"
            )
            return False

    def update_baseline(self):
        """Update existing baseline with new findings."""
        if not self.baseline_file.exists():
            print("⚠️  Baseline file not found. Creating new baseline...")
            return self.create_baseline()

        try:
            cmd = ["detect-secrets", "scan", "--baseline", str(self.baseline_file)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(f"✅ Baseline updated: {self.baseline_file}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to update baseline: {e}")
            return False

    def audit_baseline(self):
        """Interactive audit of baseline findings."""
        if not self.baseline_file.exists():
            print("❌ Baseline file not found. Please create it first.")
            return False

        try:
            cmd = ["detect-secrets", "audit", str(self.baseline_file)]
            subprocess.run(cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to audit baseline: {e}")
            return False

    def validate_baseline(self):
        """Validate baseline integrity."""
        try:
            with open(self.baseline_file) as f:
                baseline = json.load(f)

            # Check required fields
            required_fields = ["version", "plugins_used", "results"]
            for field in required_fields:
                if field not in baseline:
                    raise ValueError(f"Missing required field: {field}")

            # Validate version
            if baseline["version"] not in ["0.1", "0.2", "1.0"]:
                print(f"⚠️  Unknown baseline version: {baseline['version']}")

            # Check results structure
            if not isinstance(baseline["results"], dict):
                raise ValueError("Results field must be a dictionary")

            print("✅ Baseline validation successful")
            print(f"   Version: {baseline['version']}")
            print(f"   Plugins: {len(baseline['plugins_used'])}")
            print(f"   Results: {len(baseline['results'])}")
            return True

        except FileNotFoundError:
            print(f"❌ Baseline file not found: {self.baseline_file}")
            return False
        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON in baseline file: {e}")
            return False
        except Exception as e:
            print(f"❌ Baseline validation failed: {e}")
            return False

    def show_statistics(self):
        """Show baseline statistics."""
        if not self.baseline_file.exists():
            print("❌ Baseline file not found.")
            return False

        try:
            with open(self.baseline_file) as f:
                baseline = json.load(f)

            print("📊 Baseline Statistics")
            print("=" * 30)
            print(f"File: {self.baseline_file}")
            print(f"Version: {baseline.get('version', 'unknown')}")
            print(f"Generated: {baseline.get('generated_at', 'unknown')}")
            print(f"Total findings: {len(baseline.get('results', {}))}")

            if baseline.get("results"):
                # Group by file type
                file_types = {}
                for file_path, findings in baseline["results"].items():
                    ext = Path(file_path).suffix or "no_extension"
                    file_types[ext] = file_types.get(ext, 0) + len(findings)

                print("\nFindings by file type:")
                for ext, count in sorted(
                    file_types.items(), key=lambda x: x[1], reverse=True
                ):
                    print(f"  {ext}: {count}")

            return True

        except Exception as e:
            print(f"❌ Failed to show statistics: {e}")
            return False


def main():
    """Main function for command-line usage."""
    import argparse

    parser = argparse.ArgumentParser(description="Manage detect-secrets baseline")
    parser.add_argument(
        "action",
        choices=["create", "update", "audit", "validate", "stats"],
        help="Action to perform",
    )
    parser.add_argument(
        "--baseline",
        default=".secrets.baseline",
        help="Baseline file path (default: .secrets.baseline)",
    )

    args = parser.parse_args()

    manager = SecretBaselineManager(args.baseline)

    actions = {
        "create": manager.create_baseline,
        "update": manager.update_baseline,
        "audit": manager.audit_baseline,
        "validate": manager.validate_baseline,
        "stats": manager.show_statistics,
    }

    success = actions[args.action]()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
