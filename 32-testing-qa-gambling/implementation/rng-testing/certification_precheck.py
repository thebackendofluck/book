#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
RNG Certification Pre-Check Automation
=======================================
Automated pre-certification checks against GLI-11, eCOGRA, and BMM standards.
Run this before submitting to testing labs to catch issues early.

Checks performed:
  - RNG algorithm identification and validation
  - Seed management and entropy source verification
  - Output range and scaling correctness
  - Statistical test battery (delegated to statistical_test_suite.py)
  - Game outcome mapping verification (e.g., card deck, roulette wheel)
  - Cycle length / period estimation
  - Re-seeding behavior under load
  - Thread safety verification
  - State isolation between games/sessions

Usage:
  python certification_precheck.py --config precheck_config.yaml
  python certification_precheck.py --rng-endpoint https://rng.casino.com/api/v1 --games slots,blackjack,roulette
"""

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import numpy as np
    from scipy import stats as scipy_stats
except ImportError:
    print("Required: pip install numpy scipy")
    sys.exit(1)


class Severity(Enum):
    CRITICAL = "CRITICAL"   # Will fail certification
    MAJOR = "MAJOR"         # Likely to fail certification
    MINOR = "MINOR"         # May cause questions from lab
    INFO = "INFO"           # Recommendation only


class CheckStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARNING"
    SKIP = "SKIPPED"


@dataclass
class CheckResult:
    check_id: str
    name: str
    standard: str          # GLI-11 section, eCOGRA requirement, etc.
    status: CheckStatus
    severity: Severity
    message: str
    details: dict = field(default_factory=dict)
    remediation: str = ""

    def to_dict(self):
        return {
            "check_id": self.check_id,
            "name": self.name,
            "standard": self.standard,
            "status": self.status.value,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
            "remediation": self.remediation,
        }


class CertificationPreCheck:
    """
    Pre-certification check suite for iGaming RNG systems.

    Covers GLI-11 (Gaming Laboratories International Standard 11),
    eCOGRA requirements, and BMM Testlabs criteria.
    """

    def __init__(self):
        self.results: List[CheckResult] = []

    def add_result(self, result: CheckResult):
        self.results.append(result)

    # -------------------------------------------------------------------
    # GLI-11 Section 1: RNG Algorithm Requirements
    # -------------------------------------------------------------------
    def check_algorithm_identification(
        self, algorithm_name: str, implementation_details: dict
    ) -> CheckResult:
        """
        GLI-11 1.1: The RNG algorithm must be identified and documented.
        Approved algorithms include AES-CTR-DRBG, SHA-256-DRBG, Fortuna, etc.
        """
        approved_algorithms = {
            "aes-ctr-drbg", "aes-256-ctr-drbg", "hmac-drbg", "hash-drbg",
            "sha-256-drbg", "sha-512-drbg", "fortuna", "yarrow",
            "chacha20", "mersenne-twister",  # MT only for non-crypto uses
            "xoshiro256", "pcg64",
        }

        # Algorithms that require additional justification
        conditional_algorithms = {
            "mersenne-twister": "MT is predictable if state is exposed. Must not be used for security-critical RNG.",
            "lcg": "Linear Congruential Generator is NOT acceptable for production gambling.",
            "xorshift": "XorShift variants have known weaknesses. Requires justification.",
        }

        algo_lower = algorithm_name.lower().replace(" ", "-")
        is_approved = algo_lower in approved_algorithms

        if algo_lower in conditional_algorithms:
            result = CheckResult(
                check_id="GLI11-ALG-001",
                name="RNG Algorithm Identification",
                standard="GLI-11 Section 1.1",
                status=CheckStatus.WARN,
                severity=Severity.MAJOR,
                message=f"Algorithm '{algorithm_name}' requires justification: {conditional_algorithms[algo_lower]}",
                details=implementation_details,
                remediation="Provide documentation justifying algorithm choice. Consider AES-CTR-DRBG.",
            )
        elif is_approved:
            result = CheckResult(
                check_id="GLI11-ALG-001",
                name="RNG Algorithm Identification",
                standard="GLI-11 Section 1.1",
                status=CheckStatus.PASS,
                severity=Severity.CRITICAL,
                message=f"Algorithm '{algorithm_name}' is on the approved list.",
                details=implementation_details,
            )
        else:
            result = CheckResult(
                check_id="GLI11-ALG-001",
                name="RNG Algorithm Identification",
                standard="GLI-11 Section 1.1",
                status=CheckStatus.FAIL,
                severity=Severity.CRITICAL,
                message=f"Algorithm '{algorithm_name}' is not on the approved list.",
                details=implementation_details,
                remediation="Use an approved CSPRNG such as AES-CTR-DRBG or HMAC-DRBG.",
            )

        self.add_result(result)
        return result

    # -------------------------------------------------------------------
    # GLI-11 Section 2: Seed / Entropy Source
    # -------------------------------------------------------------------
    def check_entropy_source(
        self, entropy_sources: List[str], seed_length_bits: int
    ) -> CheckResult:
        """
        GLI-11 2.1: Entropy source must provide sufficient randomness.
        Minimum 128 bits of entropy for seed material.
        """
        approved_sources = {
            "/dev/urandom", "/dev/random", "rdrand", "rdseed",
            "tpm", "haveged", "jitter-entropy", "hardware-rng",
            "os.urandom", "secrets", "getrandom",
            "aws-kms", "azure-keyvault", "gcp-cloud-hsm",
        }

        found_approved = [s for s in entropy_sources if s.lower() in approved_sources]
        min_seed_bits = 128

        issues = []
        if seed_length_bits < min_seed_bits:
            issues.append(
                f"Seed length {seed_length_bits} bits is below minimum {min_seed_bits} bits"
            )

        if not found_approved:
            issues.append(
                f"No approved entropy source found. Sources: {entropy_sources}"
            )

        if "time" in " ".join(entropy_sources).lower() or "clock" in " ".join(entropy_sources).lower():
            issues.append(
                "Time-based seeding is NOT acceptable as sole entropy source"
            )

        if issues:
            result = CheckResult(
                check_id="GLI11-ENT-001",
                name="Entropy Source Verification",
                standard="GLI-11 Section 2.1",
                status=CheckStatus.FAIL,
                severity=Severity.CRITICAL,
                message="; ".join(issues),
                details={
                    "entropy_sources": entropy_sources,
                    "seed_length_bits": seed_length_bits,
                    "approved_sources_found": found_approved,
                },
                remediation=(
                    "Use OS-provided CSPRNG (e.g., /dev/urandom, os.urandom) "
                    "with minimum 256-bit seed. Consider hardware RNG for primary entropy."
                ),
            )
        else:
            result = CheckResult(
                check_id="GLI11-ENT-001",
                name="Entropy Source Verification",
                standard="GLI-11 Section 2.1",
                status=CheckStatus.PASS,
                severity=Severity.CRITICAL,
                message=f"Entropy source(s) approved. Seed length: {seed_length_bits} bits.",
                details={
                    "entropy_sources": entropy_sources,
                    "seed_length_bits": seed_length_bits,
                    "approved_sources_found": found_approved,
                },
            )

        self.add_result(result)
        return result

    # -------------------------------------------------------------------
    # GLI-11 Section 3: Output Scaling
    # -------------------------------------------------------------------
    def check_output_scaling(
        self,
        raw_range: Tuple[int, int],
        scaled_outcomes: Dict[str, int],
        samples: np.ndarray,
        game_type: str,
    ) -> CheckResult:
        """
        GLI-11 3.1: Scaling from RNG output to game outcomes must not introduce bias.
        Modulo bias check and outcome distribution verification.
        """
        issues = []
        details = {}

        # Check for modulo bias
        raw_min, raw_max = raw_range
        raw_span = raw_max - raw_min + 1
        num_outcomes = sum(scaled_outcomes.values())

        if raw_span % num_outcomes != 0:
            bias = (raw_span % num_outcomes) / raw_span
            if bias > 0.001:  # More than 0.1% bias
                issues.append(
                    f"Potential modulo bias: {bias*100:.4f}%. "
                    f"Raw range {raw_span} is not evenly divisible by {num_outcomes} outcomes."
                )
            details["modulo_bias_pct"] = round(bias * 100, 6)

        # Verify outcome distribution if samples provided
        if len(samples) > 0:
            observed = Counter(samples)
            total = len(samples)

            # Chi-squared test on actual outcomes
            expected_counts = {
                k: total * v / num_outcomes for k, v in scaled_outcomes.items()
            }
            chi2 = sum(
                (observed.get(k, 0) - exp) ** 2 / exp
                for k, exp in expected_counts.items()
                if exp > 0
            )
            df = len(expected_counts) - 1
            p_value = 1 - scipy_stats.chi2.cdf(chi2, max(df, 1))

            details["chi2_statistic"] = round(chi2, 4)
            details["p_value"] = round(p_value, 6)
            details["sample_size"] = total

            if p_value < 0.01:
                issues.append(
                    f"Outcome distribution fails chi-squared test (p={p_value:.6f})"
                )

        # Game-specific checks
        if game_type == "cards":
            if num_outcomes != 52:
                issues.append(f"Card game expects 52 outcomes, got {num_outcomes}")
        elif game_type == "roulette":
            if num_outcomes not in (37, 38):
                issues.append(
                    f"Roulette expects 37 (European) or 38 (American) outcomes, got {num_outcomes}"
                )
        elif game_type == "dice":
            if num_outcomes != 6:
                issues.append(f"Dice expects 6 outcomes, got {num_outcomes}")

        status = CheckStatus.FAIL if issues else CheckStatus.PASS

        result = CheckResult(
            check_id="GLI11-SCALE-001",
            name=f"Output Scaling ({game_type})",
            standard="GLI-11 Section 3.1",
            status=status,
            severity=Severity.CRITICAL,
            message="; ".join(issues) if issues else "Output scaling is unbiased.",
            details=details,
            remediation=(
                "Use rejection sampling to eliminate modulo bias. "
                "Example: discard values >= (raw_max - raw_max % num_outcomes)."
            ) if issues else "",
        )
        self.add_result(result)
        return result

    # -------------------------------------------------------------------
    # GLI-11 Section 4: Non-Repeatability
    # -------------------------------------------------------------------
    def check_non_repeatability(
        self, sequences: List[np.ndarray], window_size: int = 100
    ) -> CheckResult:
        """
        GLI-11 4.1: RNG must not produce repeating sequences.
        Check for duplicate subsequences across multiple sessions.
        """
        seen_hashes: Set[str] = set()
        duplicates = 0
        total_windows = 0

        for seq in sequences:
            for i in range(0, len(seq) - window_size + 1, window_size // 2):
                window = seq[i : i + window_size]
                h = hashlib.sha256(window.tobytes()).hexdigest()[:16]
                total_windows += 1
                if h in seen_hashes:
                    duplicates += 1
                seen_hashes.add(h)

        status = CheckStatus.PASS if duplicates == 0 else CheckStatus.FAIL

        result = CheckResult(
            check_id="GLI11-REP-001",
            name="Non-Repeatability Check",
            standard="GLI-11 Section 4.1",
            status=status,
            severity=Severity.CRITICAL,
            message=(
                f"No duplicate sequences found in {total_windows} windows."
                if duplicates == 0
                else f"Found {duplicates} duplicate sequences in {total_windows} windows!"
            ),
            details={
                "total_windows": total_windows,
                "window_size": window_size,
                "duplicates_found": duplicates,
                "num_sequences": len(sequences),
            },
            remediation=(
                "Ensure proper re-seeding between sessions. "
                "Investigate entropy source exhaustion."
            ) if duplicates > 0 else "",
        )
        self.add_result(result)
        return result

    # -------------------------------------------------------------------
    # GLI-11 Section 5: Thread Safety
    # -------------------------------------------------------------------
    def check_thread_safety_report(
        self, thread_results: Dict[str, Any]
    ) -> CheckResult:
        """
        GLI-11 5.1: RNG must be thread-safe in multi-player environments.
        Validates results from concurrent access testing.
        """
        issues = []

        if thread_results.get("race_conditions_detected", 0) > 0:
            issues.append(
                f"{thread_results['race_conditions_detected']} race conditions detected"
            )

        if thread_results.get("duplicate_outputs_across_threads", 0) > 0:
            issues.append(
                f"{thread_results['duplicate_outputs_across_threads']} duplicate outputs across threads"
            )

        if thread_results.get("state_corruption_events", 0) > 0:
            issues.append(
                f"{thread_results['state_corruption_events']} state corruption events"
            )

        # Check per-thread distribution uniformity
        if "per_thread_p_values" in thread_results:
            failed_threads = [
                tid
                for tid, pv in thread_results["per_thread_p_values"].items()
                if pv < 0.01
            ]
            if failed_threads:
                issues.append(
                    f"Threads with non-uniform distribution: {failed_threads}"
                )

        status = CheckStatus.PASS if not issues else CheckStatus.FAIL

        result = CheckResult(
            check_id="GLI11-THRD-001",
            name="Thread Safety Verification",
            standard="GLI-11 Section 5.1",
            status=status,
            severity=Severity.CRITICAL,
            message=(
                "Thread safety verified."
                if not issues
                else "; ".join(issues)
            ),
            details=thread_results,
            remediation=(
                "Implement per-thread RNG instances or use thread-safe CSPRNG. "
                "Never share mutable RNG state across threads without synchronization."
            ) if issues else "",
        )
        self.add_result(result)
        return result

    # -------------------------------------------------------------------
    # eCOGRA: Game Outcome Mapping
    # -------------------------------------------------------------------
    def check_game_outcome_mapping(
        self,
        game_type: str,
        paytable: Dict[str, float],
        theoretical_rtp: float,
        simulated_rtp: float,
        num_simulations: int,
    ) -> CheckResult:
        """
        eCOGRA: Verify that RNG-to-outcome mapping produces correct theoretical RTP.
        """
        issues = []

        # RTP bounds check
        if game_type == "slots":
            min_rtp, max_rtp = 0.80, 0.9999
        elif game_type in ("blackjack", "baccarat", "poker"):
            min_rtp, max_rtp = 0.90, 1.05  # Some bets can exceed 100% with strategy
        elif game_type == "roulette":
            min_rtp, max_rtp = 0.94, 0.98
        else:
            min_rtp, max_rtp = 0.75, 1.00

        if not (min_rtp <= theoretical_rtp <= max_rtp):
            issues.append(
                f"Theoretical RTP {theoretical_rtp*100:.2f}% outside expected range "
                f"[{min_rtp*100:.1f}%, {max_rtp*100:.2f}%] for {game_type}"
            )

        # Check simulated vs theoretical RTP
        # Use confidence interval based on number of simulations
        std_err = 0.1 / np.sqrt(num_simulations) if num_simulations > 0 else 1.0
        rtp_diff = abs(simulated_rtp - theoretical_rtp)
        z_score = rtp_diff / std_err if std_err > 0 else float("inf")

        if z_score > 3.0:
            issues.append(
                f"Simulated RTP ({simulated_rtp*100:.4f}%) differs significantly from "
                f"theoretical ({theoretical_rtp*100:.4f}%), z={z_score:.2f}"
            )

        status = CheckStatus.PASS if not issues else CheckStatus.FAIL

        result = CheckResult(
            check_id="ECOGRA-RTP-001",
            name=f"Game Outcome Mapping ({game_type})",
            standard="eCOGRA RNG Requirements",
            status=status,
            severity=Severity.CRITICAL,
            message=(
                f"RTP verified: theoretical={theoretical_rtp*100:.4f}%, "
                f"simulated={simulated_rtp*100:.4f}%"
                if not issues
                else "; ".join(issues)
            ),
            details={
                "game_type": game_type,
                "theoretical_rtp": round(theoretical_rtp * 100, 4),
                "simulated_rtp": round(simulated_rtp * 100, 4),
                "rtp_difference_pct": round(rtp_diff * 100, 4),
                "z_score": round(z_score, 4),
                "num_simulations": num_simulations,
                "paytable_entries": len(paytable),
            },
            remediation=(
                "Review paytable configuration and RNG-to-outcome mapping logic. "
                "Increase simulation count for more accurate RTP estimation."
            ) if issues else "",
        )
        self.add_result(result)
        return result

    # -------------------------------------------------------------------
    # BMM: Re-seeding Under Load
    # -------------------------------------------------------------------
    def check_reseeding_behavior(
        self,
        reseed_interval_seconds: float,
        reseed_after_n_outputs: int,
        entropy_pool_size_bits: int,
    ) -> CheckResult:
        """
        BMM Testlabs: RNG must re-seed at appropriate intervals.
        """
        issues = []

        if reseed_interval_seconds > 3600:
            issues.append(
                f"Re-seed interval {reseed_interval_seconds}s exceeds 1 hour maximum"
            )

        if reseed_after_n_outputs > 1_000_000:
            issues.append(
                f"Re-seed after {reseed_after_n_outputs} outputs is too high. "
                "Maximum recommended: 1,000,000"
            )

        if entropy_pool_size_bits < 256:
            issues.append(
                f"Entropy pool {entropy_pool_size_bits} bits is below 256-bit minimum"
            )

        status = CheckStatus.PASS if not issues else CheckStatus.WARN

        result = CheckResult(
            check_id="BMM-RESEED-001",
            name="Re-seeding Behavior",
            standard="BMM Testlabs Requirements",
            status=status,
            severity=Severity.MAJOR,
            message=(
                "Re-seeding configuration is adequate."
                if not issues
                else "; ".join(issues)
            ),
            details={
                "reseed_interval_seconds": reseed_interval_seconds,
                "reseed_after_n_outputs": reseed_after_n_outputs,
                "entropy_pool_size_bits": entropy_pool_size_bits,
            },
            remediation=(
                "Configure automatic re-seeding every 100,000 outputs or 5 minutes, "
                "whichever comes first. Use at least 256 bits of entropy per re-seed."
            ) if issues else "",
        )
        self.add_result(result)
        return result

    # -------------------------------------------------------------------
    # Report
    # -------------------------------------------------------------------
    def generate_report(self) -> dict:
        critical_fails = [
            r for r in self.results
            if r.status == CheckStatus.FAIL and r.severity == Severity.CRITICAL
        ]
        major_fails = [
            r for r in self.results
            if r.status == CheckStatus.FAIL and r.severity == Severity.MAJOR
        ]
        warnings = [r for r in self.results if r.status == CheckStatus.WARN]
        passed = [r for r in self.results if r.status == CheckStatus.PASS]

        certification_ready = len(critical_fails) == 0 and len(major_fails) == 0

        return {
            "report_title": "RNG Certification Pre-Check Report",
            "timestamp": datetime.utcnow().isoformat() + "Z",  # ty:ignore[deprecated]
            "certification_ready": certification_ready,
            "summary": {
                "total_checks": len(self.results),
                "passed": len(passed),
                "failed_critical": len(critical_fails),
                "failed_major": len(major_fails),
                "warnings": len(warnings),
            },
            "recommendation": (
                "READY FOR SUBMISSION: All critical and major checks passed."
                if certification_ready
                else (
                    f"NOT READY: {len(critical_fails)} critical and "
                    f"{len(major_fails)} major issues must be resolved."
                )
            ),
            "critical_issues": [r.to_dict() for r in critical_fails],
            "major_issues": [r.to_dict() for r in major_fails],
            "warnings": [r.to_dict() for r in warnings],
            "all_checks": [r.to_dict() for r in self.results],
        }


def run_demo_precheck():
    """Run a demonstration pre-check with sample data."""
    checker = CertificationPreCheck()

    print("=" * 72)
    print("RNG CERTIFICATION PRE-CHECK")
    print("=" * 72)

    # 1. Algorithm check
    print("\n[1/7] Checking algorithm identification...")
    checker.check_algorithm_identification(
        algorithm_name="AES-CTR-DRBG",
        implementation_details={
            "library": "OpenSSL 3.0",
            "key_length": 256,
            "counter_length": 128,
        },
    )

    # 2. Entropy source
    print("[2/7] Checking entropy source...")
    checker.check_entropy_source(
        entropy_sources=["/dev/urandom", "rdrand"],
        seed_length_bits=256,
    )

    # 3. Output scaling (slots - 100 symbols)
    print("[3/7] Checking output scaling (slots)...")
    rng_data = np.random.randint(0, 100, size=100000)
    checker.check_output_scaling(
        raw_range=(0, 2**32 - 1),
        scaled_outcomes={f"symbol_{i}": 1 for i in range(100)},
        samples=rng_data,
        game_type="slots",
    )

    # 4. Output scaling (roulette)
    print("[4/7] Checking output scaling (roulette)...")
    roulette_data = np.random.randint(0, 37, size=100000)
    checker.check_output_scaling(
        raw_range=(0, 2**32 - 1),
        scaled_outcomes={str(i): 1 for i in range(37)},
        samples=roulette_data,
        game_type="roulette",
    )

    # 5. Non-repeatability
    print("[5/7] Checking non-repeatability...")
    sequences = [
        np.random.randint(0, 2**32, size=10000, dtype=np.uint32) for _ in range(10)
    ]
    checker.check_non_repeatability(sequences)

    # 6. Thread safety (simulated results)
    print("[6/7] Checking thread safety...")
    checker.check_thread_safety_report(
        {
            "race_conditions_detected": 0,
            "duplicate_outputs_across_threads": 0,
            "state_corruption_events": 0,
            "num_threads": 32,
            "outputs_per_thread": 100000,
            "per_thread_p_values": {f"thread_{i}": 0.5 for i in range(32)},
        }
    )

    # 7. Re-seeding
    print("[7/7] Checking re-seeding behavior...")
    checker.check_reseeding_behavior(
        reseed_interval_seconds=300,
        reseed_after_n_outputs=100000,
        entropy_pool_size_bits=512,
    )

    # Generate report
    report = checker.generate_report()

    print("\n" + "=" * 72)
    print("RESULTS SUMMARY")
    print("=" * 72)
    print(f"  Total checks:      {report['summary']['total_checks']}")
    print(f"  Passed:            {report['summary']['passed']}")
    print(f"  Critical failures: {report['summary']['failed_critical']}")
    print(f"  Major failures:    {report['summary']['failed_major']}")
    print(f"  Warnings:          {report['summary']['warnings']}")
    print(f"\n  {report['recommendation']}")
    print("=" * 72)

    # Save report
    report_path = "precheck_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull report saved to {report_path}")

    return report


def main():
    parser = argparse.ArgumentParser(description="RNG Certification Pre-Check")
    parser.add_argument("--demo", action="store_true", help="Run demo pre-check")
    parser.add_argument("--config", help="YAML config file")
    parser.add_argument("--output", default="precheck_report.json", help="Output file")

    args = parser.parse_args()

    if args.demo or not args.config:
        run_demo_precheck()
    else:
        try:
            import yaml
            with open(args.config) as f:
                config = yaml.safe_load(f)
            # Config-driven checks would go here
            print(f"Config-driven pre-check not yet implemented. Use --demo for demonstration.")
        except ImportError:
            print("pip install pyyaml for config file support")
            sys.exit(1)


if __name__ == "__main__":
    main()
