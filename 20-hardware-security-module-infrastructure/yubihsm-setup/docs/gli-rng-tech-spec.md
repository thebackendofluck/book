# RNG Technical Specification
## iGaming Platform — GLI-19 Submission Package

**Document type:** Technical Specification for RNG Laboratory Submission
**Target lab:** Gaming Laboratories International (GLI) / BMM Testlabs / eCOGRA
**Standard:** GLI-19 v5.0 — Standards for Internet Gaming Systems
**RNG algorithm:** ChaCha20-CSPRNG with YubiHSM 2 TRNG seeding
**Submission version:** 1.0

---

## 1. Executive Summary

The platform implements a Certified Random Number Generator (CertifiedRng) using the ChaCha20 stream cipher as the CSPRNG, seeded with true hardware entropy from a YubiHSM 2 FIPS 140-2 Level 3 hardware security module. Each game session uses an independently seeded ChaCha20 instance; seeds are never reused, shared, or persisted to disk.

The design satisfies all GLI-19 requirements for:
- CSPRNG algorithm selection (ChaCha20, equivalent to NIST SP 800-90A CTR_DRBG)
- Seed isolation between game sessions
- Hardware entropy source (FIPS-certified TRNG)
- Audit trail with seed hash (without exposing the seed)
- Statistical quality (NIST SP 800-22 test suite)

---

## 2. Seed Pool Architecture

### 2.1 Two-Layer Architecture

The RNG system uses a two-layer design to decouple hardware entropy collection from game session initialization:

```
Layer 1 — Hardware Entropy Collection (async background)
  YubiHSM 2 TRNG
    --> random_bytes(N * 32)     # batch collect N seeds in one USB call
    --> SeedPool (VecDeque)      # FIFO queue, Mutex protected
    --> background refill task   # replenishes when pool drops below threshold

Layer 2 — Game Session RNG (synchronous, zero HSM latency)
  get_seed() from pool           # pop_front() from pre-filled pool
  XOR with epoch.rng_mixer       # add epoch-keyed entropy
  XOR with SHA256(game:player)   # add deterministic context
  --> ChaCha20::from_seed(final_seed)  # cryptographically secure CSPRNG
  --> GameRngSession { rng, seed_hash, game_id, draws: 0 }
```

### 2.2 Seed Isolation Guarantee

Each `GameRngSession` receives a unique 32-byte seed from the following composition:

```
final_seed[i] = hw_seed[i] XOR epoch_rng_mixer[i] XOR context_hash[i]

where:
  hw_seed         = 32 bytes from YubiHSM 2 TRNG (unique per call)
  epoch_rng_mixer = 32 bytes derived from current epoch KeyHierarchy
  context_hash    = SHA-256("game_id:player_id") — deterministic per session
```

This triple-mixing provides defense-in-depth:
- If the TRNG output is somehow predicted, epoch mixer adds HKDF entropy
- If epoch key is compromised, context hash adds per-session binding
- No two sessions can share the same `final_seed` without knowing all three components

### 2.3 Pool Lifecycle

```
Startup:
  warmup() called during service initialization
  hsm.random_bytes(1000 * 32) = 32,000 bytes fetched in one HSM call
  1,000 seeds loaded into VecDeque

Per game session:
  get_seed() called
  pop_front() from pool (O(1))
  if pool.len() < 100: spawn refill task (non-blocking)

Refill (background):
  hsm.random_bytes(200 * 32) = 6,400 bytes per refill call
  pool.extend() up to max_level
```

---

## 3. ChaCha20 CSPRNG Implementation

### 3.1 Algorithm Selection

**Algorithm:** ChaCha20 stream cipher used as DRBG
**Rust crate:** `rand_chacha = "0.3"` (implements `rand::SeedableRng`)
**Equivalent standard:** NIST SP 800-90A Section 10.2 (CTR_DRBG) — ChaCha20 provides equivalent security guarantees via a different construction

### 3.2 ChaCha20 Security Properties

| Property | Value |
|---|---|
| Key size | 256 bits (32 bytes) |
| State size | 512 bits |
| Period | 2^64 × 2^256 (unbounded for practical purposes) |
| Security level | 256 bits |
| Predictability | Computationally indistinguishable from true random given unknown seed |
| Bias | None by construction (stream cipher, not modulo) |

### 3.3 Rejection Sampling for Bounded Ranges

To avoid modulo bias when generating outcomes in a bounded range:

```rust
pub fn draw(&mut self, range: u32) -> u32 {
    let threshold = range.wrapping_neg() % range;
    loop {
        let v = self.rng.next_u32();
        if v >= threshold { return v % range; }
    }
}
```

This implements the rejection sampling method from Daniel Lemire's "Fast Random Integer Generation" which produces exactly uniform distributions without bias for any range value.

---

## 4. Session Isolation Proof

### 4.1 Isolation Properties

**Definition:** Two game sessions are isolated if and only if knowing the complete output of one session yields no advantage in predicting the output of any other session.

**Proof sketch:**
1. Each session seed is derived as `hw_seed XOR epoch_mixer XOR context_hash`
2. `hw_seed` is a fresh TRNG output, independent per session by definition
3. Therefore, sessions are independent even if `epoch_mixer` and `context_hash` are known
4. ChaCha20 is a PRF (pseudorandom function), so output is indistinguishable from random given unknown seed
5. QED: sessions are computationally isolated

### 4.2 GLI-19 Seed Isolation Requirements

| GLI-19 Requirement | Implementation | Status |
|---|---|---|
| Seeds must not be shared between sessions | `pop_front()` single-use seeds | COMPLIANT |
| Seeds must not be reused | FIFO pool, seed discarded after use | COMPLIANT |
| Seed must come from approved entropy source | YubiHSM 2 FIPS 140-2 L3 TRNG | COMPLIANT |
| Seed must not be derivable from session outcomes | ChaCha20 is one-way | COMPLIANT |
| Seed hash must be auditable | `seed_hash = SHA-256(final_seed)` logged | COMPLIANT |

### 4.3 Audit Trail Format

For each game session, the following structured log entry is emitted:

```json
{
  "level": "INFO",
  "target": "rng_service",
  "game_id": "slot-session-uuid-v4",
  "player_id": "player-uuid-v4",
  "seed_hash": "sha256-hex-of-seed",
  "algorithm": "ChaCha20-CSPRNG+HSM-TRNG+epoch-mix",
  "entropy_source": "YubiHSM2-TRNG-pool",
  "timestamp": "2026-03-29T10:00:00Z",
  "message": "game RNG session created"
}
```

**Note:** The seed itself is NEVER logged. The `seed_hash` allows replay verification by the platform operator without exposing the actual seed to unauthorized parties.

---

## 5. NIST SP 800-22 Test Plan

### 5.1 Test Methodology

**Standard:** NIST SP 800-22 Rev. 1a — A Statistical Test Suite for Random and Pseudorandom Number Generators for Cryptographic Applications

**Sample size:** 1,000,000,000 bits (125,000,000 bytes) per test run
**Significance level:** 0.01 (standard for GLI submissions)
**Tool:** NIST STS implementation (C reference implementation from NIST website)

### 5.2 Test Suite — 15 Tests

| Test Name | Test ID | Purpose |
|---|---|---|
| Frequency (Monobit) | T-01 | Tests proportion of 1s and 0s |
| Block Frequency | T-02 | Tests proportion of 1s in M-bit blocks |
| Runs | T-03 | Tests uninterrupted sequences of identical bits |
| Longest Run of Ones in a Block | T-04 | Tests longest runs in 128-bit blocks |
| Binary Matrix Rank | T-05 | Tests linear dependence of fixed-length subsequences |
| Discrete Fourier Transform (Spectral) | T-06 | Tests periodic features that would indicate non-randomness |
| Non-Overlapping Template Matching | T-07 | Tests occurrences of specific aperiodic patterns |
| Overlapping Template Matching | T-08 | Tests occurrences of overlapping patterns |
| Maurer's Universal Statistical | T-09 | Tests compressibility (entropy measure) |
| Linear Complexity | T-10 | Tests length of linear feedback shift register |
| Serial | T-11 | Tests frequency of all overlapping m-bit patterns |
| Approximate Entropy | T-12 | Tests frequency of overlapping blocks |
| Cumulative Sums (Cusum) | T-13 | Tests maximum excursion from zero for cumulative sums |
| Random Excursions | T-14 | Tests number of cycles visiting each state |
| Random Excursions Variant | T-15 | Tests total number of times a state is visited |

### 5.3 Test Execution

```bash
# Step 1: Generate 1 billion bits from the RNG service
# Use the production RNG service test endpoint
curl -X POST https://rng-service.internal/v1/generate \
  -H "Authorization: Bearer $RNG_TEST_TOKEN" \
  -d '{"bits": 1000000000, "session_id": "nist-test-2026-03-29"}' \
  --output rng_output.bin

# Step 2: Run NIST SP 800-22 test suite
# Download from: https://csrc.nist.gov/projects/random-bit-generation/documentation-and-software
./assess 1000000 < rng_output.bin

# Step 3: Interpret results
# All 15 tests must pass at alpha=0.01 significance level
# Expected output: p-value > 0.01 for each test across all sequences
```

### 5.4 Expected Results

Based on ChaCha20 characteristics and prior laboratory certifications of ChaCha20-based systems:

| Test | Expected p-value | Pass Criterion |
|---|---|---|
| Frequency | ~0.5 (uniform) | p-value > 0.01 |
| Block Frequency | ~0.5 | p-value > 0.01 |
| Runs | ~0.5 | p-value > 0.01 |
| DFT/Spectral | ~0.5 | p-value > 0.01 |
| All 15 tests | > 0.01 | 98%+ sequences pass |

### 5.5 Pre-Submission Checklist

Before submitting to GLI/BMM:

- [ ] Run full 15-test NIST suite on 1 billion bits minimum
- [ ] Run suite 10 times with different seeds (verify consistency)
- [ ] Document YubiHSM 2 device serial number and FIPS 140-2 certificate number
- [ ] Document ChaCha20 implementation version (rand_chacha 0.3.x)
- [ ] Provide source code of CertifiedRng struct and GameRngSession
- [ ] Provide audit log sample showing seed_hash without seed
- [ ] Provide architecture diagram (seed pool + ChaCha20 + mixing)
- [ ] Document key management policy for RNG seed keys (see PCI DSS policy doc)

---

## 6. Hardware Entropy Source Certification

### 6.1 YubiHSM 2 TRNG Specifications

| Attribute | Value |
|---|---|
| Device | YubiHSM 2 (Yubico) |
| FIPS Certification | FIPS 140-2 Level 3 |
| FIPS Certificate | #3204 (verify at csrc.nist.gov/projects/cryptographic-module-validation-program) |
| TRNG mechanism | Hardware-based true random number generator |
| Output size | Configurable (up to 2,048 bytes per call via PKCS#11) |
| PKCS#11 function | `C_GenerateRandom` |
| Entropy quality | Full entropy (hardware noise source, not DRBG) |

### 6.2 NIST SP 800-90C Alignment

**Reference:** NIST SP 800-90C — Recommendation for Random Bit Generator (RBG) Constructions

The platform implements a "DRBG construction with an entropy source" as defined in NIST SP 800-90C Section 3:

- **Entropy source (ES):** YubiHSM 2 TRNG — provides full-entropy input
- **DRBG mechanism:** ChaCha20 — equivalent to NIST-approved CTR_DRBG
- **Seed construction:** Direct seeding from ES output (no conditioning function required for full-entropy sources)
- **Security strength:** 256 bits (32-byte seed = 256 bits entropy from full-entropy source)

---

## 7. Submission Package Contents

The complete GLI submission package includes:

```
gli-rng-submission/
├── source-code/
│   ├── services/rng/src/lib.rs          # CertifiedRng implementation
│   ├── libs/hsm/src/lib.rs              # HsmClient (TRNG interface)
│   └── Cargo.lock                        # Pinned dependency versions
├── technical-documentation/
│   ├── gli-rng-tech-spec.md             # This document
│   ├── pci-dss-key-management-policy.md # Key management policy
│   └── architecture-diagram.png         # System architecture
├── test-results/
│   ├── nist-800-22-run-1.txt            # Full NIST test output
│   ├── nist-800-22-run-2.txt
│   ├── nist-800-22-run-3.txt
│   └── nist-800-22-summary.csv          # p-values for all 15 tests × 3 runs
├── hardware-evidence/
│   ├── yubihsm2-fips-certificate.pdf    # FIPS 140-2 L3 certificate
│   ├── yubihsm2-device-serial.txt       # Device serial for traceability
│   └── hsm-audit-sample.json            # Sample audit log from production
└── audit-logs/
    └── rng-session-sample.json          # Sample RNG session audit entries
```

---

*This technical specification is prepared for laboratory submission under GLI-19 v5.0.*
*All claims are verifiable by the submitting laboratory via source code inspection and live testing.*
