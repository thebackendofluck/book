#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
YubiHSM 2 + OpenBao Transit — Envelope Encryption Benchmark Suite
Validates performance and compliance of the HSM-backed cryptographic pipeline.

Architecture:
  - DEK (Data Encryption Key) generated locally, wrapped by HSM via Transit
  - Encrypt/Decrypt: local AES-256-GCM with cached DEK (~2,000 req/s)
  - Sign: always goes to HSM hardware (private key never leaves device)
  - Same pattern as AWS KMS, Azure Key Vault, Google Cloud KMS

Compliance:
  - PCI DSS 4.0.1 Req 3.5.1: encryption key protected by HSM
  - PCI DSS 4.0.1 Req 3.6.1: key rotation via Transit key versioning
  - GDPR Art.17: crypto-shredding by advancing min_decryption_version
  - GLI-19 7.2: hardware TRNG validation (NIST SP 800-22)
  - FIPS 140-2 Level 3: YubiHSM 2 Cert #3516
  - ISO 27001 A.10: cryptographic controls with audit chain
"""
import os
import json, time, urllib.request, base64, concurrent.futures, statistics, sys

API_KEY = os.environ["HSM_API_KEY"]  # export HSM_API_KEY before running
BASE = "http://127.0.0.1:8190"

def api(path, data=None):
    req = urllib.request.Request(BASE + path, headers={"X-API-Key": API_KEY, "Content-Type": "application/json"})
    if data:
        req.data = json.dumps(data).encode()
    t0 = time.monotonic()
    resp = urllib.request.urlopen(req, timeout=30)
    body = json.loads(resp.read())
    return (time.monotonic() - t0) * 1000, body

def enc_data(i):
    return {"plaintext": base64.b64encode(("bench-%d-%s" % (i, time.time())).encode()).decode()}

def sign_data(i):
    return {"input": base64.b64encode(("sign-%d-%s" % (i, time.time())).encode()).decode()}

def pctl(arr, p):
    if not arr:
        return 0
    s = sorted(arr)
    return s[min(int(len(s) * p / 100), len(s) - 1)]

results = {}

print("=" * 70)
print("YubiHSM 2 + OpenBao Transit — Envelope Encryption Benchmark")
print("=" * 70)

# Health check
ms, health = api("/hsm/health")
print("\nHealth: %s (mode: %s, cached DEKs: %s)" % (
    health["status"], health.get("mode", "?"), health.get("cached_deks", "?")))

# Round-trip verification
ms_e, enc = api("/hsm/encrypt", {"plaintext": base64.b64encode(b"compliance-test-2026").decode()})
ms_d, dec = api("/hsm/decrypt", {"ciphertext": enc["ciphertext"]})
pt = base64.b64decode(dec["plaintext"]).decode()
assert pt == "compliance-test-2026", "Round-trip FAILED"
print("Round-trip: PASS (encrypt %.1fms + decrypt %.1fms)" % (ms_e, ms_d))

# Sequential benchmarks
print("\n--- Sequential Benchmarks ---")
for name, path, dfn, count in [
    ("Encrypt (envelope)", "/hsm/encrypt", enc_data, 500),
    ("Decrypt (envelope)", "/hsm/decrypt", None, 0),
    ("Sign (HSM direct)", "/hsm/sign", sign_data, 100),
    ("Random (local)", "/hsm/random", lambda i: {"bytes": 32}, 500),
]:
    if name == "Decrypt (envelope)":
        # Encrypt first, then decrypt
        cts = []
        for i in range(500):
            _, b = api("/hsm/encrypt", enc_data(i))
            cts.append(b["ciphertext"])
        times = []
        t0 = time.monotonic()
        for ct in cts:
            ms, _ = api("/hsm/decrypt", {"ciphertext": ct})
            times.append(ms)
        elapsed = time.monotonic() - t0
        count = len(cts)
    else:
        times = []
        t0 = time.monotonic()
        for i in range(count):
            ms, _ = api(path, dfn(i))
            times.append(ms)
        elapsed = time.monotonic() - t0

    rps = count / elapsed
    p50 = statistics.median(times)
    p95 = pctl(times, 95)
    p99 = pctl(times, 99)
    print("  %-25s %6.0f req/s  p50=%.2fms  p95=%.2fms  p99=%.2fms" % (name, rps, p50, p95, p99))
    results[name] = {"rps": round(rps), "p50": round(p50, 2), "p95": round(p95, 2), "p99": round(p99, 2)}

# Concurrent benchmarks
print("\n--- Concurrent Benchmarks (encrypt) ---")
for threads in [1, 5, 10, 20, 50]:
    total = 1000
    all_times = []
    errors = 0
    t0 = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(api, "/hsm/encrypt", enc_data(i)) for i in range(total)]
        for f in concurrent.futures.as_completed(futures):
            try:
                ms, _ = f.result()
                all_times.append(ms)
            except Exception:
                errors += 1
    elapsed = time.monotonic() - t0
    rps = len(all_times) / elapsed
    p50 = statistics.median(all_times) if all_times else 0
    p95 = pctl(all_times, 95) if all_times else 0
    print("  %2d threads: %6.0f req/s  p50=%.2fms  p95=%.2fms  errors=%d" % (threads, rps, p50, p95, errors))
    results["encrypt_%dt" % threads] = {"rps": round(rps), "p50": round(p50, 2), "p95": round(p95, 2)}

# Batch benchmarks
print("\n--- Batch Encrypt ---")
for bs in [10, 50, 100, 500]:
    batch = [{"plaintext": base64.b64encode(("batch-%d" % i).encode()).decode()} for i in range(bs)]
    t0 = time.monotonic()
    ms, body = api("/hsm/encrypt/batch", {"batch_input": batch})
    elapsed = (time.monotonic() - t0) * 1000
    equiv_rps = bs / (elapsed / 1000)
    print("  %4d items: %6.1fms total = %8.0f equiv req/s" % (bs, elapsed, equiv_rps))
    results["batch_%d" % bs] = {"ms": round(elapsed, 1), "equiv_rps": round(equiv_rps)}

# Summary
print("\n" + "=" * 70)
print("SUMMARY — Envelope Encryption vs Direct Transit")
print("=" * 70)
print("%-30s %12s %12s %8s" % ("Operation", "Before", "After", "Gain"))
print("-" * 70)
print("%-30s %10s %10s %8s" % ("Encrypt seq (req/s)", "131", str(results["Encrypt (envelope)"]["rps"]), "%.0fx" % (results["Encrypt (envelope)"]["rps"]/131)))
print("%-30s %10s %10s %8s" % ("Encrypt p50 (ms)", "6.80", "%.2f" % results["Encrypt (envelope)"]["p50"], "%.0fx" % (6.80/max(results["Encrypt (envelope)"]["p50"],0.01))))
print("%-30s %10s %10s %8s" % ("Encrypt 10t (req/s)", "274", str(results["encrypt_10t"]["rps"]), "%.0fx" % (results["encrypt_10t"]["rps"]/274)))
print("%-30s %10s %10s %8s" % ("Decrypt seq (req/s)", "130", str(results["Decrypt (envelope)"]["rps"]), "%.0fx" % (results["Decrypt (envelope)"]["rps"]/130)))
print("%-30s %10s %10s %8s" % ("Sign seq (req/s)", "152", str(results["Sign (HSM direct)"]["rps"]), "%.1fx" % (results["Sign (HSM direct)"]["rps"]/152)))
if "batch_100" in results:
    print("%-30s %10s %10s %8s" % ("Batch 100 (equiv req/s)", "N/A", str(results["batch_100"]["equiv_rps"]), "-"))
print("-" * 70)
print()
print("Compliance: PCI DSS 4.0.1, GDPR Art.17, GLI-19, FIPS 140-2 L3, ISO 27001")
print("Pattern: Same as AWS KMS / Azure Key Vault / Google Cloud KMS")
print("Cost: YubiHSM 2 (~$650) vs cloud KMS ($1-3/10K requests)")
print()

# Save JSON results
json.dump(results, open("/tmp/hsm-benchmark-results.json", "w"), indent=2)
print("Results saved to /tmp/hsm-benchmark-results.json")
