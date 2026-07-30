#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
pqc-benchmark.py — PQC performance benchmark for iGaming workloads
Chapter 24g: Post-Quantum Cryptography for iGaming

Benchmarks classical and post-quantum cryptographic operations and
projects their impact on real iGaming connection patterns.

Requirements:
    pip install cryptography
    pip install pyoqs      # Optional: Open Quantum Safe Python bindings
                           # Install: https://github.com/open-quantum-safe/liboqs-python

Usage:
    python pqc-benchmark.py [--iterations N] [--json] [--output FILE]

Options:
    --iterations N   Number of iterations per algorithm (default: 1000)
    --json           Output results as JSON
    --output FILE    Write results to file (default: stdout)
    --help           Show help
"""

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Optional imports — degrade gracefully if not installed
# ---------------------------------------------------------------------------
try:
    from cryptography.hazmat.primitives.asymmetric import rsa, ec, padding
    from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key as gen_rsa
    from cryptography.hazmat.primitives.asymmetric.ec import (
        generate_private_key as gen_ec, SECP256R1, SECP384R1, ECDSA
    )
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.backends import default_backend
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False
    print("[WARN] 'cryptography' package not installed. Install with: pip install cryptography", file=sys.stderr)

try:
    import oqs
    PYOQS_AVAILABLE = True
except ImportError:
    PYOQS_AVAILABLE = False
    print("[WARN] 'pyoqs' not installed — PQC benchmarks will be skipped.", file=sys.stderr)
    print("       Install: pip install pyoqs  (requires liboqs system library)", file=sys.stderr)
    print("       Docs: https://github.com/open-quantum-safe/liboqs-python", file=sys.stderr)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class BenchmarkResult:
    algorithm: str
    operation: str          # keygen | sign | verify | encaps | decaps
    iterations: int
    total_ms: float
    mean_ms: float
    median_ms: float
    p99_ms: float
    stddev_ms: float
    ops_per_second: float
    key_size_bytes: Optional[int] = None
    signature_size_bytes: Optional[int] = None
    ciphertext_size_bytes: Optional[int] = None
    error: Optional[str] = None


@dataclass
class iGamingScenario:
    name: str
    description: str
    connections_per_second: int
    handshakes_per_connection: float  # fraction; 1.0 = full handshake every time
    classical_ms: float
    pqc_ms: float
    overhead_ms: float
    classical_overhead_percent: float
    impact_assessment: str


# ---------------------------------------------------------------------------
# Timing utility
# ---------------------------------------------------------------------------
def measure(fn, iterations: int) -> tuple[list[float], Any]:
    """Run fn() for `iterations` times; return (timings_in_ms, last_result)."""
    timings = []
    result = None
    for _ in range(iterations):
        t0 = time.perf_counter()
        result = fn()
        t1 = time.perf_counter()
        timings.append((t1 - t0) * 1000)
    return timings, result


def stats(timings: list[float], iterations: int) -> dict:
    sorted_t = sorted(timings)
    n = len(sorted_t)
    mean = sum(sorted_t) / n
    variance = sum((x - mean) ** 2 for x in sorted_t) / n
    p99_idx = int(math.ceil(0.99 * n)) - 1
    return {
        "total_ms": sum(sorted_t),
        "mean_ms": mean,
        "median_ms": sorted_t[n // 2],
        "p99_ms": sorted_t[max(0, p99_idx)],
        "stddev_ms": math.sqrt(variance),
        "ops_per_second": 1000.0 / mean if mean > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Classical benchmarks (RSA, ECDSA)
# ---------------------------------------------------------------------------
def benchmark_rsa2048(iterations: int) -> list[BenchmarkResult]:
    if not CRYPTOGRAPHY_AVAILABLE:
        return []

    results = []
    backend = default_backend()
    MESSAGE = b"igaming-transaction-payload-" * 4  # 112 bytes

    # --- Key generation ---
    keygen_times, _ = measure(
        lambda: gen_rsa(public_exponent=65537, key_size=2048, backend=backend),
        iterations=min(iterations, 100)  # keygen is slow; cap at 100
    )
    key_stats = stats(keygen_times, min(iterations, 100))
    results.append(BenchmarkResult(
        algorithm="RSA-2048",
        operation="keygen",
        iterations=min(iterations, 100),
        key_size_bytes=2048 // 8,
        **key_stats
    ))

    # Reuse one key for sign/verify benchmarks
    private_key = gen_rsa(public_exponent=65537, key_size=2048, backend=backend)
    public_key = private_key.public_key()

    # --- Sign ---
    sign_fn = lambda: private_key.sign(MESSAGE, padding.PKCS1v15(), hashes.SHA256())
    sign_times, signature = measure(sign_fn, iterations)
    sig_stats = stats(sign_times, iterations)
    results.append(BenchmarkResult(
        algorithm="RSA-2048",
        operation="sign",
        iterations=iterations,
        signature_size_bytes=len(signature),
        **sig_stats
    ))

    # --- Verify ---
    verify_fn = lambda: public_key.verify(signature, MESSAGE, padding.PKCS1v15(), hashes.SHA256())
    verify_times, _ = measure(verify_fn, iterations)
    results.append(BenchmarkResult(
        algorithm="RSA-2048",
        operation="verify",
        iterations=iterations,
        signature_size_bytes=len(signature),
        **stats(verify_times, iterations)
    ))

    return results


def benchmark_ecdsa_p256(iterations: int) -> list[BenchmarkResult]:
    if not CRYPTOGRAPHY_AVAILABLE:
        return []

    results = []
    backend = default_backend()
    MESSAGE = b"igaming-transaction-payload-" * 4

    # --- Key generation ---
    keygen_times, _ = measure(
        lambda: gen_ec(curve=SECP256R1(), backend=backend),
        iterations=min(iterations, 500)
    )
    key_stats = stats(keygen_times, min(iterations, 500))
    results.append(BenchmarkResult(
        algorithm="ECDSA-P256",
        operation="keygen",
        iterations=min(iterations, 500),
        key_size_bytes=32,
        **key_stats
    ))

    private_key = gen_ec(curve=SECP256R1(), backend=backend)
    public_key = private_key.public_key()

    # --- Sign ---
    sign_fn = lambda: private_key.sign(MESSAGE, ECDSA(hashes.SHA256()))
    sign_times, signature = measure(sign_fn, iterations)
    sig_stats = stats(sign_times, iterations)
    results.append(BenchmarkResult(
        algorithm="ECDSA-P256",
        operation="sign",
        iterations=iterations,
        signature_size_bytes=len(signature),
        **sig_stats
    ))

    # --- Verify ---
    verify_fn = lambda: public_key.verify(signature, MESSAGE, ECDSA(hashes.SHA256()))
    verify_times, _ = measure(verify_fn, iterations)
    results.append(BenchmarkResult(
        algorithm="ECDSA-P256",
        operation="verify",
        iterations=iterations,
        signature_size_bytes=len(signature),
        **stats(verify_times, iterations)
    ))

    return results


# ---------------------------------------------------------------------------
# PQC benchmarks (ML-KEM-768 / Kyber768, ML-DSA-65 / Dilithium3)
# ---------------------------------------------------------------------------
def benchmark_ml_kem_768(iterations: int) -> list[BenchmarkResult]:
    """Benchmark ML-KEM-768 (NIST FIPS 203, formerly Kyber-768) KEM operations."""
    if not PYOQS_AVAILABLE:
        return _pqc_not_available_result("ML-KEM-768", ["keygen", "encaps", "decaps"])

    results = []
    # liboqs may expose this as "Kyber768" or "ML-KEM-768" depending on version
    kem_name = "ML-KEM-768" if "ML-KEM-768" in oqs.get_enabled_kem_mechanisms() else "Kyber768"

    with oqs.KeyEncapsulation(kem_name) as kem:
        # --- Key generation ---
        keygen_fn = lambda: kem.generate_keypair()
        keygen_times, public_key = measure(keygen_fn, min(iterations, 500))
        results.append(BenchmarkResult(
            algorithm="ML-KEM-768",
            operation="keygen",
            iterations=min(iterations, 500),
            key_size_bytes=len(public_key),
            **stats(keygen_times, min(iterations, 500))
        ))

        # --- Encapsulate ---
        encaps_fn = lambda: kem.encap_secret(public_key)
        encaps_times, (ciphertext, shared_secret_enc) = measure(encaps_fn, iterations)
        results.append(BenchmarkResult(
            algorithm="ML-KEM-768",
            operation="encaps",
            iterations=iterations,
            ciphertext_size_bytes=len(ciphertext),
            **stats(encaps_times, iterations)
        ))

        # --- Decapsulate ---
        decaps_fn = lambda: kem.decap_secret(ciphertext)
        decaps_times, _ = measure(decaps_fn, iterations)
        results.append(BenchmarkResult(
            algorithm="ML-KEM-768",
            operation="decaps",
            iterations=iterations,
            ciphertext_size_bytes=len(ciphertext),
            **stats(decaps_times, iterations)
        ))

    return results


def benchmark_ml_dsa_65(iterations: int) -> list[BenchmarkResult]:
    """Benchmark ML-DSA-65 (NIST FIPS 204, formerly Dilithium3) signature operations."""
    if not PYOQS_AVAILABLE:
        return _pqc_not_available_result("ML-DSA-65", ["keygen", "sign", "verify"])

    results = []
    MESSAGE = b"igaming-transaction-payload-" * 4
    sig_name = "ML-DSA-65" if "ML-DSA-65" in oqs.get_enabled_sig_mechanisms() else "Dilithium3"

    with oqs.Signature(sig_name) as sig:
        # --- Key generation ---
        keygen_fn = lambda: sig.generate_keypair()
        keygen_times, public_key = measure(keygen_fn, min(iterations, 500))
        results.append(BenchmarkResult(
            algorithm="ML-DSA-65",
            operation="keygen",
            iterations=min(iterations, 500),
            key_size_bytes=len(public_key),
            **stats(keygen_times, min(iterations, 500))
        ))

        # --- Sign ---
        sign_fn = lambda: sig.sign(MESSAGE)
        sign_times, signature = measure(sign_fn, iterations)
        results.append(BenchmarkResult(
            algorithm="ML-DSA-65",
            operation="sign",
            iterations=iterations,
            signature_size_bytes=len(signature),
            **stats(sign_times, iterations)
        ))

        # --- Verify ---
        verify_fn = lambda: sig.verify(MESSAGE, signature, public_key)
        verify_times, _ = measure(verify_fn, iterations)
        results.append(BenchmarkResult(
            algorithm="ML-DSA-65",
            operation="verify",
            iterations=iterations,
            signature_size_bytes=len(signature),
            **stats(verify_times, iterations)
        ))

    return results


def _pqc_not_available_result(algo: str, ops: list[str]) -> list[BenchmarkResult]:
    """Return placeholder results when pyoqs is not installed."""
    return [
        BenchmarkResult(
            algorithm=algo,
            operation=op,
            iterations=0,
            total_ms=0, mean_ms=0, median_ms=0, p99_ms=0,
            stddev_ms=0, ops_per_second=0,
            error="pyoqs not installed — install with: pip install pyoqs"
        )
        for op in ops
    ]


# ---------------------------------------------------------------------------
# iGaming scenario projections
# ---------------------------------------------------------------------------
def project_igaming_scenarios(results: list[BenchmarkResult]) -> list[iGamingScenario]:
    """
    Model the impact of PQC handshake overhead on typical iGaming workloads.

    Assumptions:
      - Classical TLS 1.3 with ECDHE-P256: ~1.5 ms for key exchange
      - PQC hybrid (X25519 + ML-KEM-768): ~3.5 ms for key exchange
      - Additional overhead: ~2 ms per new connection
    """

    # Look up mean keygen time for ML-KEM-768; use estimate if not available
    mlkem_keygen_ms = next(
        (r.mean_ms for r in results if r.algorithm == "ML-KEM-768" and r.operation == "keygen"),
        2.0  # estimated fallback
    )
    ecdsa_keygen_ms = next(
        (r.mean_ms for r in results if r.algorithm == "ECDSA-P256" and r.operation == "keygen"),
        0.5  # estimated fallback
    )

    # PQC overhead per new TLS handshake (conservative estimate)
    pqc_overhead_per_handshake_ms = mlkem_keygen_ms + ecdsa_keygen_ms
    classical_handshake_ms = ecdsa_keygen_ms * 2 + 1.0  # keygen + sign + base
    pqc_handshake_ms = classical_handshake_ms + pqc_overhead_per_handshake_ms

    scenarios = [
        {
            "name": "Player Login",
            "description": "New player session establishment (full handshake)",
            "cps": 500,
            "handshake_ratio": 1.0,
        },
        {
            "name": "Payment API Call",
            "description": "Payment processor webhook / API call (new connection per request)",
            "cps": 200,
            "handshake_ratio": 1.0,
        },
        {
            "name": "WebSocket Connect",
            "description": "Live casino / sports betting WebSocket upgrade (full handshake, then persistent)",
            "cps": 1000,
            "handshake_ratio": 1.0,
        },
        {
            "name": "REST API (Keep-Alive)",
            "description": "Game engine API calls on persistent connections (session reuse)",
            "cps": 10000,
            "handshake_ratio": 0.05,  # 5% are new connections
        },
        {
            "name": "CDN Cache Hit",
            "description": "Game asset delivery via CDN (TLS session resumed)",
            "cps": 50000,
            "handshake_ratio": 0.01,  # 1% cold start
        },
        {
            "name": "Blockchain Txn Signing",
            "description": "On-chain iGaming transaction batch signing (crypto-only, no TLS)",
            "cps": 100,
            "handshake_ratio": 1.0,  # every transaction requires fresh signing
        },
    ]

    output = []
    for s in scenarios:
        effective_cps = float(s["cps"]) * float(s["handshake_ratio"])
        classical_total = classical_handshake_ms * effective_cps / 1000.0  # ms of crypto per second
        pqc_total = pqc_handshake_ms * effective_cps / 1000.0

        overhead = pqc_total - classical_total
        pct = (overhead / classical_total * 100) if classical_total > 0 else 0

        if pct < 5:
            assessment = "Negligible impact — no action needed"
        elif pct < 20:
            assessment = "Minor impact — monitor latency metrics post-migration"
        elif pct < 50:
            assessment = "Moderate impact — plan capacity buffer (1.5x), tune session caching"
        else:
            assessment = "Significant impact — hardware acceleration or connection pooling recommended"

        output.append(iGamingScenario(
            name=str(s["name"]),
            description=str(s["description"]),
            connections_per_second=int(s["cps"]),
            handshakes_per_connection=float(s["handshake_ratio"]),
            classical_ms=round(classical_total, 3),
            pqc_ms=round(pqc_total, 3),
            overhead_ms=round(overhead, 3),
            classical_overhead_percent=round(pct, 1),
            impact_assessment=assessment,
        ))

    return output


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------
def print_table(results: list[BenchmarkResult], scenarios: list[iGamingScenario]) -> None:
    """Print human-readable comparison tables."""

    print("\n" + "=" * 80)
    print("  PQC Benchmark Results")
    print("=" * 80)

    # Group by algorithm
    algos = {}
    for r in results:
        algos.setdefault(r.algorithm, []).append(r)

    header = f"{'Algorithm':<16} {'Operation':<10} {'Mean (ms)':>10} {'P99 (ms)':>10} {'ops/s':>10} {'Key/Sig (B)':>12}"
    print(f"\n{header}")
    print("-" * 80)

    for algo, algo_results in algos.items():
        for r in algo_results:
            if r.error:
                print(f"  {r.algorithm:<14} {r.operation:<10} {'N/A ('+r.error[:30]+')':>50}")
                continue
            extra = r.key_size_bytes or r.signature_size_bytes or r.ciphertext_size_bytes or 0
            print(
                f"  {r.algorithm:<14} {r.operation:<10} "
                f"{r.mean_ms:>10.3f} {r.p99_ms:>10.3f} "
                f"{r.ops_per_second:>10.0f} {extra:>12}"
            )
        print()

    print("\n" + "=" * 80)
    print("  iGaming Scenario Impact Analysis")
    print("=" * 80)

    s_header = f"{'Scenario':<30} {'CPS':>6} {'Ratio':>6} {'Class.ms':>9} {'PQC ms':>9} {'Ovhd%':>7}"
    print(f"\n{s_header}")
    print("-" * 80)

    for s in scenarios:
        print(
            f"  {s.name:<28} {s.connections_per_second:>6} "
            f"{s.handshakes_per_connection:>6.2f} "
            f"{s.classical_ms:>9.3f} {s.pqc_ms:>9.3f} "
            f"{s.classical_overhead_percent:>6.1f}%"
        )

    print()
    print("  Assessment:")
    for s in scenarios:
        print(f"    [{s.name}] {s.impact_assessment}")

    print("\n" + "=" * 80)
    print("  Key Size Comparison (bytes)")
    print("=" * 80)
    print(f"\n  {'Algorithm':<20} {'Public Key':>12} {'Private Key':>12} {'Signature/CT':>14}")
    print("  " + "-" * 62)
    # Reference values (NIST FIPS 203/204 + classical)
    key_sizes = [
        ("RSA-2048",    256,   1192,  256),
        ("ECDSA-P256",   64,     32,   71),
        ("ML-KEM-768", 1184,   2400, 1088),   # CT size for KEM
        ("ML-DSA-65",  1952,   4032, 3293),
    ]
    for name, pk, sk, sig in key_sizes:
        print(f"  {name:<20} {pk:>12} {sk:>12} {sig:>14}")

    print("\n  Note: PQC sizes are much larger — plan for bigger TLS records and")
    print("        increased network overhead during handshake (~2-4 KB extra per connection).\n")


def output_json(results: list[BenchmarkResult], scenarios: list[iGamingScenario]) -> str:
    return json.dumps(
        {
            "benchmark_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "python_version": sys.version,
            "cryptography_available": CRYPTOGRAPHY_AVAILABLE,
            "pyoqs_available": PYOQS_AVAILABLE,
            "results": [asdict(r) for r in results],
            "scenarios": [asdict(s) for s in scenarios],
        },
        indent=2
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Benchmark PQC vs classical crypto for iGaming workloads",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--iterations", type=int, default=1000,
                   help="Iterations per benchmark (default: 1000)")
    p.add_argument("--json", action="store_true",
                   help="Output JSON instead of human-readable tables")
    p.add_argument("--output", type=str, default=None,
                   help="Write output to FILE (default: stdout)")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    print(f"[INFO] Starting PQC benchmark (iterations={args.iterations})", file=sys.stderr)
    print(f"[INFO] cryptography={CRYPTOGRAPHY_AVAILABLE}  pyoqs={PYOQS_AVAILABLE}", file=sys.stderr)

    all_results: list[BenchmarkResult] = []

    # Classical benchmarks
    if CRYPTOGRAPHY_AVAILABLE:
        print("[INFO] Benchmarking RSA-2048...", file=sys.stderr)
        all_results.extend(benchmark_rsa2048(args.iterations))

        print("[INFO] Benchmarking ECDSA-P256...", file=sys.stderr)
        all_results.extend(benchmark_ecdsa_p256(args.iterations))
    else:
        print("[WARN] Skipping classical benchmarks — install 'cryptography'", file=sys.stderr)

    # PQC benchmarks
    if PYOQS_AVAILABLE:
        print("[INFO] Benchmarking ML-KEM-768...", file=sys.stderr)
        all_results.extend(benchmark_ml_kem_768(args.iterations))

        print("[INFO] Benchmarking ML-DSA-65...", file=sys.stderr)
        all_results.extend(benchmark_ml_dsa_65(args.iterations))
    else:
        # Add placeholder results so scenario projections have something to work with
        all_results.extend(_pqc_not_available_result("ML-KEM-768", ["keygen", "encaps", "decaps"]))
        all_results.extend(_pqc_not_available_result("ML-DSA-65", ["keygen", "sign", "verify"]))

    # Scenario projections
    print("[INFO] Projecting iGaming scenario impacts...", file=sys.stderr)
    scenarios = project_igaming_scenarios(all_results)

    # Produce output
    if args.json:
        output = output_json(all_results, scenarios)
    else:
        # Capture table output for optional file writing
        import io
        old_stdout = sys.stdout
        sys.stdout = buf = io.StringIO()
        print_table(all_results, scenarios)
        sys.stdout = old_stdout
        output = buf.getvalue()
        if not args.output:
            print(output)

    if args.output:
        with open(args.output, "w") as fh:
            fh.write(output)
        print(f"[INFO] Results written to {args.output}", file=sys.stderr)
    elif args.json:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
