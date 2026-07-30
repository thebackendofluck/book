# Companion code for "The Backend of Luck" - Chapter 42, War Stories.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 34: War Stories
War Story 1: The RNG That Wasn't Random - RNG Implementation Examples

This file contains both the problematic (buggy) RNG seeding code that caused
a real casino incident, and the corrected secure implementation that replaced it.
Preserved exactly as-is for educational purposes - the buggy code demonstrates
common mistakes; the fixed code shows the correct approach.

DO NOT use the buggy seeding functions in production.
"""

import os
import time
import random
import secrets
import hashlib


# ---------------------------------------------------------------------------
# THE PROBLEMATIC CODE (preserved for educational reference)
# ---------------------------------------------------------------------------

def seed_rng():
    """
    BUGGY seeding code that caused a real casino RNG incident.
    DO NOT USE IN PRODUCTION.

    Problems:
    - Only using seconds (not nanoseconds) - changes only once per second
    - Reading urandom but not mixing it properly (only 4 bytes)
    - Simple addition instead of proper entropy mixing
    - Multiple game sessions in the same second get identical seeds
    """
    # BUG: Only using seconds, not nanoseconds
    timestamp_seed = int(time.time())  # Only changes every second!

    # BUG: Reading urandom but not using it properly
    entropy_bytes = os.urandom(4)  # Only 4 bytes, not mixed properly
    entropy_seed = int.from_bytes(entropy_bytes, byteorder='big')

    # BUG: Simple addition instead of proper mixing
    final_seed = timestamp_seed + entropy_seed

    random.seed(final_seed)
    return final_seed


# ---------------------------------------------------------------------------
# THE FIXED CODE
# ---------------------------------------------------------------------------

def secure_rng_seed():
    """Generate cryptographically secure random seed using hardware entropy"""
    hardware_entropy = secrets.token_bytes(32)
    timestamp_entropy = hashlib.sha256(str(time.time_ns()).encode()).digest()
    combined = hashlib.sha256(hardware_entropy + timestamp_entropy).digest()
    return combined


def generate_secure_outcome(min_val: int, max_val: int) -> int:
    """Generate a cryptographically secure random number in range"""
    return secrets.randbelow(max_val - min_val + 1) + min_val


def test_rng_fairness(samples=100000):
    """Continuous RNG fairness testing"""
    results = [generate_secure_outcome(1, 100) for _ in range(samples)]

    # Chi-square test for uniformity
    observed = [results.count(i) for i in range(1, 101)]
    expected = [samples/100] * 100

    chi_square = sum((o - e) ** 2 / e for o, e in zip(observed, expected))

    # Alert if chi-square statistic is too extreme
    if chi_square > 150.0:  # 99.9% confidence threshold
        alert_rng_anomaly(chi_square)

    return chi_square < 150.0


def alert_rng_anomaly(chi_square: float):
    """Placeholder for RNG anomaly alerting"""
    # In production, this would send alerts to monitoring systems
    print(f"RNG ANOMALY DETECTED: chi-square={chi_square:.2f} exceeds threshold of 150.0")
