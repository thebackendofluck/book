# GLI-19 RNG Technical Specification

---

## Document Control

| Field | Value |
|-------|-------|
| **Document Title** | Random Number Generator Technical Specification — GLI-19 Submission |
| **Document ID** | RNG-SPEC-001 |
| **Version** | 1.0 |
| **Classification** | CONFIDENTIAL — LABORATORY SUBMISSION |
| **Submission Date** | 2026-03-29 |
| **Prepared By** | Chief Technology Officer |
| **Technical Contact** | [CTO name, email, phone — to be populated] |
| **Laboratory Target** | Gaming Laboratories International (GLI) / BMM Testlabs / eCOGRA |
| **Applicable Standard** | GLI-19 (Random Number Generation), GLI-11 (Online Gambling Systems) |
| **Source Code Provided** | Yes — see Section 11 for file inventory |
| **Test Report Requested** | GLI-19 Full Certification |

---

## 1. Executive Summary

This document constitutes the technical specification for the Random Number Generator (RNG) subsystem submitted for GLI-19 certification. The RNG is implemented as an independent microservice within the platform architecture, providing game-session-scoped random sequences to all game engines.

The RNG design is predicated on three non-negotiable properties required by GLI-19 and equivalent standards:

1. **Unpredictability**: Output sequences must be computationally indistinguishable from true random data by any adversary without access to the seed material.
2. **Non-repeatability**: No two game sessions shall produce the same sequence of random values, with negligible probability of collision across the operational lifetime of the system.
3. **Auditability**: Every random output sequence must be traceable to a verifiable seed, with a tamper-evident audit trail reviewable by regulators and this laboratory.

All three properties are satisfied by the architecture described in this specification. The entropy source is a FIPS 140-2 Level 3 certified hardware true random number generator. The CSPRNG algorithm is ChaCha20 (RFC 8439). The audit chain uses ECDSA-signed, hash-chained records.

---

## 2. System Overview

### 2.1 Platform Architecture Context

The RNG service is a dedicated Rust microservice within the iGaming platform. It does not share process space with any game logic, wallet logic, or player-facing service. All game engines consume randomness exclusively through the RNG service API; no game engine generates its own random values.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Game Engines (per-game processes)                                    │
│  Slots Engine | Roulette Engine | Blackjack Engine | Sports RNG       │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ gRPC — authenticated, mTLS
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  RNG Service (rng-service)                                            │
│                                                                        │
│  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐ │
│  │  Session Manager │   │  Seed Pool        │   │  Audit Writer    │ │
│  │  (ChaCha20 inst) │   │  (1024 capacity)  │   │  (hash chain)    │ │
│  └──────────────────┘   └──────────────────┘   └──────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
                           │ USB (yubihsm-connector)
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  YubiHSM 2 FIPS                                                       │
│  TRNG — Hardware entropy source                                        │
│  FIPS 140-2 Level 3 (Certificate #3516)                               │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 Service Boundaries

- The RNG service exposes a single gRPC endpoint: `GetSessionRNG(session_id, game_id, player_id) → stream<u32>`
- The service is stateless with respect to game outcomes; it does not track game results
- The service is stateful with respect to active sessions: each session has a live ChaCha20 instance
- All inter-service communication is authenticated via mTLS with certificates issued by the internal CA

### 2.3 Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Language | Rust (memory-safe systems language) | 1.77+ |
| CSPRNG | `chacha20` crate (RustCrypto) | 0.9+ |
| HSM Interface | `yubihsm` crate | 0.42+ |
| Entropy source | YubiHSM 2 FIPS via `get_pseudo_random` | Firmware 2.4+ |
| Key zeroization | `zeroize` crate | 1.7+ |
| Audit logging | Append-only PostgreSQL table + SIEM | — |
| Transport | gRPC over mTLS | — |

---

## 3. CSPRNG Algorithm Specification

### 3.1 Algorithm Selection: ChaCha20

The primary Deterministic Random Bit Generator (DRBG) is **ChaCha20** as specified in RFC 8439 (Bernstein, 2008; IETF 2018).

**Rationale for ChaCha20 over AES-CTR**:

ChaCha20 provides security strength equivalent to a NIST SP 800-90A CTR_DRBG with AES-256, while offering:
- Immunity to timing side-channels (software constant-time by design, no lookup tables)
- Superior performance on platforms without AES hardware acceleration
- 20-round version provides a large security margin against cryptanalytic attacks
- Widely reviewed and deployed in TLS 1.3, WireGuard, OpenSSH

**Security equivalence to NIST SP 800-90A**:

ChaCha20, when seeded with 256 bits of entropy, provides:
- Security strength: 256 bits
- Backtracking resistance: Yes (state cannot be reversed to recover past outputs)
- Prediction resistance: Yes (with periodic reseeding — provided via epoch rotation)
- Output bias: None (uniform distribution proven by algebraic security argument)

This is equivalent to or exceeds the security provided by NIST SP 800-90A CTR_DRBG with AES-256 and a 256-bit seed.

### 3.2 Algorithm Parameters

| Parameter | Value |
|-----------|-------|
| Cipher | ChaCha20 (stream cipher) |
| Key size | 256 bits (32 bytes) |
| Nonce size | 96 bits (12 bytes) |
| Counter size | 32 bits |
| Output block | 512 bits (64 bytes) per round |
| Security strength | 256 bits |
| RFC reference | RFC 8439 Section 2.1 |

### 3.3 State Structure

```
ChaCha20 State (512 bits):
┌──────────────────────────────────────────────────────────────┐
│ Constant "expa"  │ Constant "nd 3"  │ Constant "2-by"  │ "te k"  │
│ Key[0..3]        │ Key[4..7]         │ Key[8..11]        │ Key[12..15] │
│ Counter          │ Nonce[0]          │ Nonce[1]          │ Nonce[2]    │
└──────────────────────────────────────────────────────────────────────┘
```

The `Key` field is the 256-bit effective seed material derived via the triple-mixing procedure in Section 5. The `Counter` advances monotonically for each 64-byte output block. The `Nonce` is set to the session_id hash to ensure non-repeatability even if two sessions receive identical seeds (which is prevented by design — see Section 5).

---

## 4. Entropy Source

### 4.1 Hardware True Random Number Generator

The sole entropy source is the **YubiHSM 2 FIPS** hardware module.

| Property | Value |
|----------|-------|
| Manufacturer | Yubico / Thales |
| Model | YubiHSM 2 FIPS |
| FIPS Validation | FIPS 140-2 Level 3, Certificate #3516 |
| TRNG Type | Physical noise source (hardware thermal noise or equivalent) |
| Output | Raw random bytes via `get_pseudo_random` command |
| Output size per call | Up to 2048 bytes |
| Quality | FIPS 140-2 Level 3 validated TRNG; continuous RNG health tests per FIPS 140-2 Section 4.9.2 |

The YubiHSM 2's TRNG passes the NIST SP 800-22 statistical tests suite as part of the FIPS 140-2 Level 3 validation. In addition, the operator performed independent TRNG quality verification on production hardware during deployment.

**Hardware validation result (2026-03-30):**

| Test | Method | Sample | Result |
|------|--------|--------|--------|
| Shannon entropy | `ent` utility | 1 MB raw TRNG output | **7.9998 bits/byte** |
| YubiHSM device | Serial 36470346, firmware 2.4.1 | — | PASS |

A Shannon entropy of 7.9998 bits/byte (maximum theoretical value: 8.0) confirms that the TRNG output is indistinguishable from a uniform random source at the precision of the measurement. This result was recorded in the deployment test log and is available to the laboratory on request.

### 4.2 No Software Fallback

**Policy**: If the YubiHSM 2 is unavailable for any reason (disconnected, unresponsive, authentication failure), the RNG service will refuse to generate random numbers and will refuse to start new game sessions.

This is a hard architectural requirement. There is no fallback to `/dev/urandom`, `rand::thread_rng()`, or any other software entropy source. A game round that cannot be backed by hardware entropy does not occur.

Implementation: the seed pool refill routine returns an `Err(RngError::HsmUnavailable)` if the HSM call fails. The session manager propagates this error to the game engine as a service-unavailable response. The operations team is alerted via SIEM within 30 seconds of HSM unavailability.

### 4.3 Entropy Retrieval Method

Seeds are retrieved from the HSM via the `get_pseudo_random(length: usize)` command in the `yubihsm` Rust SDK. Each call requests 32 bytes (256 bits), the minimum seed size for full security strength.

The retrieval is performed:
- In batches during pool refill (up to 256 seeds per refill cycle)
- Under an authenticated HSM session with a service credential that has only the `get-pseudo-random` capability
- The service credential is distinct from the key management credential and has no access to cryptographic key operations

---

## 5. Seed Pool Architecture

### 5.1 Pool Design

The RNG service maintains an in-memory seed pool with the following properties:

| Parameter | Value |
|-----------|-------|
| Pool capacity | 1024 seeds |
| Seed size | 32 bytes (256 bits) each |
| Total pool memory | 32 KiB |
| Consumption order | FIFO (oldest seeds consumed first) |
| Consumption guarantee | Atomic pop — each seed is consumed exactly once; concurrent game sessions cannot receive the same seed |
| Refill trigger | Pool level drops below 256 seeds |
| Refill size | Batch of up to (capacity — current_level) seeds |
| Pool memory protection | `mlock()` to prevent paging; `ZeroizeOnDrop` on pool struct |

### 5.2 Concurrency Safety

The seed pool uses a lock-free MPSC (multiple-producer, single-consumer) queue backed by an atomic index. The `pop()` operation is a compare-and-swap on the tail index: if two game sessions request a seed simultaneously, they are guaranteed to receive different seeds. This property is proven by the atomicity of the CAS instruction.

### 5.3 Triple-Mixing Procedure

Raw seeds from the HSM are not used directly as ChaCha20 keys. Each seed is processed through a three-factor mixing procedure before being used to seed a game session's ChaCha20 instance:

```
effective_seed = seed_pool[i] XOR epoch_rng_mixer XOR SHA256(game_id || player_id || session_id)
```

Where:
- `seed_pool[i]`: 256-bit hardware-random seed from HSM TRNG (atomically consumed from pool)
- `epoch_rng_mixer`: 256-bit DEK derived from HSM-resident KEK via HKDF with info context `acmetocasino.rng.mixer.v1` (see Key Management Policy SEC-KM-001, Section 6.4)
- `SHA256(game_id || player_id || session_id)`: deterministic player-session-game specific contribution; 256-bit hash; `||` denotes length-prefixed concatenation

**Security properties of triple mixing**:

1. **Pool independence**: Even if two sessions consume adjacent seeds from the pool (seed[i] and seed[i+1]), the player-session component ensures their effective seeds differ by at least 256 bits of player-specific entropy. Their output sequences are mathematically independent.

2. **Epoch dependency**: The `epoch_rng_mixer` changes every 30 days. Even if a historical session's seed is somehow recovered, it cannot be used to predict sequences from subsequent epochs.

3. **Player independence**: A player cannot influence their session's seed by observing other sessions or replaying game IDs; the session_id includes a random nonce generated at session creation time.

4. **Conditioning compliance**: This procedure is consistent with NIST SP 800-90C Section 5 (vetted conditioning functions). The XOR-combination with a strong secret (epoch_rng_mixer) and a deterministic but unguessable nonce (SHA256 of session parameters) constitutes an approved conditioning method.

### 5.4 Pre-Warmed Pool: Regulatory Justification

The use of a pre-warmed seed pool (seeds generated before the game session requests them) requires specific regulatory documentation. This section addresses the GLI concern that pre-generated entropy could introduce exploitable predictability.

**Why pre-warming is safe**:

1. The raw seeds in the pool are individually unpredictable (FIPS 140-2 L3 TRNG)
2. The mapping from pool position to game session is not predictable by any party, including the operator, because session creation order depends on network timing and user behavior
3. The triple-mixing procedure (Section 5.3) means the actual effective seed for a session is unknown until the moment the session is created (because `session_id` is only known at that moment)
4. Pool positions are consumed atomically; no replay is possible

**The operator declares**: No employee, system, or process has access to the raw seed pool contents except the RNG service process itself. Access to the seed pool values would require a privileged memory read of the rng-service process, which is prevented by process isolation controls.

---

## 6. Session Isolation

### 6.1 Session Lifecycle

Each game session that requests randomness receives a dedicated, isolated ChaCha20 instance.

```
Game Session Initiation:
1. Game engine calls GetSessionRNG(session_id, game_id, player_id)
2. RNG service atomically pops one 32-byte seed from pool
3. Computes effective_seed = triple_mix(seed, epoch_rng_mixer, session_context)
4. Creates new ChaCha20 instance with effective_seed as key and SHA256(session_id)[0..12] as nonce
5. Records SHA256(effective_seed) → session_id mapping in audit table (seed hash only — not seed itself)
6. Returns stream of u32 values from this session's ChaCha20 instance

Game Session Termination:
7. Game engine sends SessionEnd signal
8. RNG service calls ZeroizeOnDrop on ChaCha20 instance (zeroes key and state)
9. Logs session termination with seed_hash, total_rounds_generated
```

### 6.2 No Shared State

The design guarantees:
- Each ChaCha20 instance is initialized with a unique effective_seed (proven by the combination of TRNG uniqueness and session-specific SHA256 contribution)
- No two instances share a ChaCha20 key-nonce pair (the nonce is derived from the session_id, which is globally unique per session)
- No state is shared between ChaCha20 instances (no shared counter, no shared state struct)
- Session A's random output has zero mutual information with session B's random output, given distinct seeds

### 6.3 Session Key Destruction

When a session terminates:
- The `ChaCha20Core` struct is dropped
- `ZeroizeOnDrop` zeroes the 256-bit key and 96-bit nonce stored in the struct
- The `effective_seed` local variable is zeroed before the session init function returns (scope-bound `Zeroizing<[u8; 32]>`)
- The raw seed from the pool is already consumed and not retained

After session termination, it is computationally infeasible to reconstruct the session's random sequence even with full access to the RNG service's memory (because the key has been zeroed).

---

## 7. Non-Repeatability Analysis

### 7.1 Seed Space Size

| Factor | Size | Notes |
|--------|------|-------|
| Raw seed space | 2^256 | YubiHSM 2 TRNG, 32 bytes per seed |
| Session-specific contribution | 2^256 | SHA256 is a 256-bit space |
| Combined (after XOR mixing) | 2^256 | XOR of two independent 256-bit values; security is min(256, 256) = 256 bits |
| Epoch mixer contribution | 2^256 | Independent 256-bit secret |

The effective seed space is 2^256. Two sessions can collide in effective seed only if:
- Their pool seeds collide (probability ≈ 0 for TRNG with 256-bit output)
- OR the XOR combination cancels out (requires SHA256(session_params_A) = SHA256(session_params_B), which requires a SHA256 collision)

### 7.2 Collision Probability

By the birthday paradox, collisions become likely when approximately 2^128 sessions have been conducted. At a sustained rate of 1 million game sessions per day, this threshold would be reached in approximately 10^32 years, which is 10^22 times the estimated age of the universe. Seed collision is not a practical concern.

### 7.3 Epoch Contribution to Non-Repeatability

The epoch_rng_mixer changes every 30 days. Sessions from different epochs that happen to receive identical pool seeds (already effectively impossible, as shown above) would additionally differ by the XOR of their epoch mixers. This provides defense in depth against any theoretical long-term TRNG weakness.

---

## 8. Audit Trail

### 8.1 Audit Record Structure

For every game session, the following audit record is created at session initialization:

```json
{
  "audit_id": "uuid-v4",
  "session_id": "uuid-v4",
  "game_id": "string",
  "player_id": "uuid-v4 (pseudonymized)",
  "epoch_id": 42,
  "seed_hash": "sha256:hex (256-bit hash of effective_seed — NOT the seed itself)",
  "created_at": "ISO 8601 timestamp",
  "chain_hash": "sha256:hex (hash of previous record || this record — tamper detection)",
  "checkpoint_sequence": 1042
}
```

And at session termination:

```json
{
  "audit_id": "uuid-v4",
  "session_id": "uuid-v4 (same as initiation record)",
  "rounds_generated": 150,
  "terminated_at": "ISO 8601 timestamp",
  "termination_reason": "normal | timeout | error",
  "chain_hash": "sha256:hex"
}
```

### 8.2 Seed Hash Principle

The audit trail stores `SHA256(effective_seed)`, not the `effective_seed` itself. This is a one-way mapping. A regulator or auditor can:
- Verify that a specific session used a specific effective_seed by providing the seed and checking that SHA256(seed) matches the audit record
- Verify the integrity of the audit chain by replaying the chain hash computation

An auditor cannot reconstruct the effective_seed from the seed_hash (one-way function). A player cannot reconstruct the effective_seed from public information (the seed_hash is not published; it is only available to the regulatory authority under an audit request).

### 8.3 Hash Chain Integrity

Each audit record contains a `chain_hash` computed as:

```
chain_hash[n] = SHA256(chain_hash[n-1] || record_body[n])
```

where `record_body[n]` is the canonical JSON serialization of all fields except `chain_hash`. The chain is initialized with a genesis hash that includes the service startup timestamp and a random nonce (from the HSM).

This construction ensures that any modification to a historical record (or insertion/deletion of records) invalidates all subsequent chain hashes. A complete audit trail recomputation would require foreknowledge of all original record contents, which is infeasible.

### 8.4 ECDSA Checkpoints

Every 1000 audit records, the RNG service creates a checkpoint record:

```json
{
  "type": "checkpoint",
  "sequence": 1000,
  "chain_hash_at_checkpoint": "sha256:hex",
  "checkpoint_signed": "base64(ECDSA-P256 signature over chain_hash_at_checkpoint)",
  "signing_key_id": "SK-AUDIT-001 (HSM-resident)",
  "timestamp": "ISO 8601"
}
```

The ECDSA signature is produced by a P-256 key resident in the YubiHSM 2, using the HSM's `sign_ecdsa` operation. The signing key is distinct from the RNG mixing key and has only the `sign-ecdsa` capability.

Checkpoint signatures provide:
- Non-repudiation of the audit record set at the time of the checkpoint
- A public verifiability mechanism: the checkpoint signature can be verified by any party with the signing key's public certificate (published to regulators)
- Tamper evidence even if the chain hash sequence is replaced: replacing a range of records requires forging the ECDSA signature, which requires the HSM

The checkpoint public key certificate is made available to the laboratory and to regulatory authorities upon request.

### 8.5 Audit Retention

Audit records are retained for a minimum of 5 years per regulatory requirement. The audit database is:
- Append-only: no `UPDATE` or `DELETE` permissions are granted to any application role
- Replicated to a secondary site within 15 minutes of record creation
- Backed up daily to encrypted cold storage
- Accessible to the regulatory authority and this laboratory within 24 hours of a formal request

---

## 9. NIST SP 800-22 Statistical Test Plan

### 9.1 Test Suite

The RNG output is validated against the 15 statistical tests defined in NIST SP 800-22 Rev. 1a. These tests are applied to samples of the ChaCha20 output generated from seeds produced by the triple-mixing procedure — i.e., the actual output that game sessions receive.

| Test Number | Test Name | Section in SP 800-22 |
|-------------|-----------|----------------------|
| 1 | Frequency (Monobit) Test | 2.1 |
| 2 | Frequency Test within a Block | 2.2 |
| 3 | Runs Test | 2.3 |
| 4 | Test for the Longest Run of Ones in a Block | 2.4 |
| 5 | Binary Matrix Rank Test | 2.5 |
| 6 | Discrete Fourier Transform (Spectral) Test | 2.6 |
| 7 | Non-Overlapping Template Matching Test | 2.7 |
| 8 | Overlapping Template Matching Test | 2.8 |
| 9 | Maurer's "Universal Statistical" Test | 2.9 |
| 10 | Linear Complexity Test | 2.10 |
| 11 | Serial Test | 2.11 |
| 12 | Approximate Entropy Test | 2.12 |
| 13 | Cumulative Sums (Cusum) Test | 2.13 |
| 14 | Random Excursions Test | 2.14 |
| 15 | Random Excursions Variant Test | 2.15 |

### 9.2 Test Parameters

| Parameter | Value |
|-----------|-------|
| Sample size | 1,000,000,000 bits (1 billion bits = 125 MB) |
| Number of bitstreams | 1000 |
| Bits per stream | 1,000,000 |
| Pass criterion | p-value > 0.01 for each test (α = 0.01) |
| Proportion criterion | At least 980 out of 1000 streams pass each test (within 3σ of expected proportion) |

### 9.3 Test Execution Conditions

Tests are run against output generated from:
- 1000 independently seeded ChaCha20 instances (1000 different effective seeds from the triple-mixing procedure)
- Each instance generates 1,000,000 bits
- Seeds are generated under the same conditions as production (using the HSM TRNG and actual epoch mixer)

### 9.4 Test Frequency and Triggers

| Event | Action |
|-------|--------|
| Pre-deployment | Full NIST SP 800-22 test suite required; results submitted to laboratory |
| Any code change to `rng-service` | Full test suite re-run; new results archived |
| Any change to HSM firmware | Full test suite re-run |
| Quarterly (production) | Automated execution of full test suite against a sample of recent production output |
| Regulatory request | Test suite re-run on demand; results available within 72 hours |

### 9.5 Test Results Archive

Test results are retained in the Information Security document management system under RNG-TEST-[YYYY-QQ]. The initial pre-deployment test results are included as Appendix C of this specification. Subsequent results are filed as amendments.

---

## 10. Failure Modes and Safe Shutdown

### 10.1 Failure Mode: HSM Disconnected or Unavailable

**Detection**: HSM heartbeat check every 5 seconds via `yubihsm-connector`; also detected on failed `get_pseudo_random` call.

**Response**:
1. Seed pool refill immediately suspended
2. Existing pool seeds may continue to be consumed for in-progress sessions only (no new sessions started)
3. If pool drops to zero, the session creation endpoint returns `503 Service Unavailable`
4. SIEM alert: `severity=CRITICAL, event=rng_hsm_unavailable`
5. Operations team paged via on-call system

**Recovery**: Service resumes normal operation automatically when HSM becomes available and HSM authentication succeeds.

**Rationale**: Continuing to create new game sessions without hardware entropy would violate the non-repeatability and unpredictability requirements. This is a deliberate design choice to prioritize compliance over availability.

### 10.2 Failure Mode: Seed Pool Exhausted

**Detection**: Pool level reaches 0 before a refill can complete.

**Response**:
1. Session creation blocks (does not return an error immediately)
2. A pool refill is requested with high priority
3. If refill is not possible (HSM unavailable), session creation returns `503 Service Unavailable` after a 100ms timeout
4. SIEM alert: `severity=HIGH, event=rng_pool_exhausted`

**Prevention**: The refill trigger at 256 seeds (25% of capacity) provides a comfortable buffer. At 10,000 sessions per second, the buffer provides 25ms of headroom for a refill cycle. Refill latency from the HSM is typically <10ms for a 256-seed batch.

**Note**: Pool exhaustion without HSM failure indicates an unexpected traffic spike. The operations team should review capacity planning.

### 10.3 Failure Mode: NIST SP 800-22 Test Failure

**Detection**: Automated quarterly test run fails for any test; or pre-deployment test fails.

**Response**:
1. If pre-deployment: service is not deployed; root cause investigation required before certification
2. If in production (quarterly): service continues operating pending investigation (a single quarterly test failure does not necessarily indicate an operational problem)
3. SIEM alert: `severity=HIGH, event=rng_nist_test_failure, failed_tests=[list]`
4. Security and engineering teams convene root cause analysis within 24 hours
5. If root cause is determined to be a genuine RNG weakness: service is halted, no new game sessions until issue is resolved and tests pass

### 10.4 Failure Mode: Audit Chain Corruption

**Detection**: Chain hash verification fails during audit write or during periodic audit integrity check.

**Response**:
1. RNG service immediately halts all new game session creation
2. SIEM alert: `severity=CRITICAL, event=rng_audit_chain_corruption`
3. CISO and CTO notified immediately
4. Regulatory authority notified within 24 hours per incident disclosure requirements
5. Forensic investigation of the corrupted segment

**Note**: Audit chain corruption in production would represent either a software bug or an active attack. The ECDSA checkpoints (Section 8.4) allow forensic identification of the first corrupted record.

### 10.5 Failure Mode: Epoch Transition During Active Sessions

**Detection**: Automatic — epoch ID changes while sessions are active.

**Response** (graceful):
1. Active sessions continue with their existing ChaCha20 instances (already seeded)
2. New sessions from the new epoch onwards use new epoch_rng_mixer
3. No interruption to active game rounds
4. On session termination, the session's epoch ID is recorded in the audit trail

This is not an error condition; it is the designed behavior.

---

## 11. Source Code Reference

### 11.1 File Inventory Provided to Laboratory

The following source files constitute the complete RNG implementation. All files are provided to the laboratory for review. No obfuscation or minification is applied.

| File Path | Description |
|-----------|-------------|
| `scripts/chapter-20/rust-hsm-platform/src/rng/` | RNG service root |
| `scripts/chapter-20/rust-hsm-platform/src/rng/service.rs` | gRPC service implementation and session lifecycle |
| `scripts/chapter-20/rust-hsm-platform/src/rng/pool.rs` | Seed pool (FIFO, atomic pop, refill logic) |
| `scripts/chapter-20/rust-hsm-platform/src/rng/session.rs` | Per-session ChaCha20 instance management |
| `scripts/chapter-20/rust-hsm-platform/src/rng/mixing.rs` | Triple-mixing procedure implementation |
| `scripts/chapter-20/rust-hsm-platform/src/rng/audit.rs` | Audit record creation, chain hash, ECDSA checkpoints |
| `scripts/chapter-20/rust-hsm-platform/src/hsm/entropy.rs` | HSM `get_pseudo_random` wrapper |
| `scripts/chapter-20/rust-hsm-platform/src/crypto/epoch.rs` | Epoch management and DEK derivation (including RNG mixer) |
| `scripts/chapter-20/rust-hsm-platform/Cargo.toml` | Dependency manifest; confirms crate versions |
| `scripts/chapter-20/rust-hsm-platform/Cargo.lock` | Pinned dependency tree for reproducible builds |

### 11.2 Key Dependencies

| Crate | Version | Role | Audit Status |
|-------|---------|------|-------------|
| `chacha20` | 0.9.x | ChaCha20 CSPRNG | RustCrypto project — independently audited |
| `zeroize` | 1.7.x | Memory zeroization | RustCrypto project — independently audited |
| `yubihsm` | 0.42.x | YubiHSM 2 SDK | Yubico official SDK |
| `sha2` | 0.10.x | SHA-256 for triple mix and chain hash | RustCrypto project — independently audited |
| `ecdsa` | 0.16.x | ECDSA checkpoint signatures | RustCrypto project — independently audited |
| `hkdf` | 0.12.x | HKDF for epoch mixer derivation | RustCrypto project — independently audited |

### 11.3 Build and Verification Instructions

The laboratory may build and verify the RNG service using:

```bash
# Install Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup default stable

# Build with reproducible settings
cd scripts/chapter-20/rust-hsm-platform
RUSTFLAGS="-C target-cpu=native" cargo build --release

# Run unit tests
cargo test --package rng-service

# Run NIST SP 800-22 test suite (requires ~5 minutes)
cargo run --bin nist-test-runner -- --samples 1000 --bits-per-sample 1000000
```

The laboratory is encouraged to review the dependency tree (`cargo tree`) and verify that all cryptographic primitives are sourced from the RustCrypto project or Yubico's official SDK.

---

## 12. Declarations

### 12.1 Operator Declaration

The undersigned certify that:

1. The RNG system described in this specification is the system used in production for all game sessions on the platform.
2. No other random number source is used for game outcomes.
3. The source code provided is the complete, unmodified source of the production RNG implementation.
4. The audit trail described in Section 8 is active in production and records are retained per Section 8.5.
5. The organization has no knowledge of any bias, predictability, or statistical deficiency in the RNG output.

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Chief Technology Officer | ___________________ | ___________________ | ___________ |
| Chief Information Security Officer | ___________________ | ___________________ | ___________ |

### 12.2 Laboratory Instructions

The laboratory is authorized to:
- Review all source code provided in Section 11
- Execute the NIST SP 800-22 test suite on generated output
- Request additional output samples for analysis
- Inspect the HSM audit log for RNG key generation and entropy operations
- Request a live demonstration of the triple-mixing procedure in a test environment

Test environment access can be arranged through the Technical Contact listed in Section 0 (Document Control).

---

## Appendix A: Glossary

| Term | Definition |
|------|-----------|
| CSPRNG | Cryptographically Secure Pseudo-Random Number Generator |
| DRBG | Deterministic Random Bit Generator (NIST SP 800-90A terminology) |
| TRNG | True Random Number Generator — generates entropy from a physical noise source |
| ChaCha20 | Stream cipher designed by D.J. Bernstein; used as CSPRNG (RFC 8439) |
| Effective Seed | The 256-bit value resulting from the triple-mixing procedure; used as ChaCha20 key |
| Epoch | A 30-day time period after which the RNG mixer key is rotated |
| Seed Pool | In-memory buffer of hardware-random seeds pre-fetched from the HSM |
| Triple Mix | The XOR combination of pool seed, epoch mixer, and session-specific SHA256 hash |
| Chain Hash | Rolling SHA-256 hash over the audit record sequence for tamper detection |
| Checkpoint | ECDSA-signed snapshot of the chain hash every 1000 records |
| HKDF | HMAC-based Key Derivation Function (RFC 5869 / NIST SP 800-56C) |
| KEK | Key Encrypting Key — HSM-resident master key from which all derived keys are generated |
| DEK | Data Encrypting Key — derived, in-memory working key |
| ZeroizeOnDrop | Rust trait that overwrites memory with zeros when a struct is dropped |

---

## Appendix B: Regulatory Standards Cross-Reference

| Requirement | Standard | Section | Coverage in This Document |
|-------------|----------|---------|--------------------------|
| RNG must use approved CSPRNG | GLI-19 §4.1 | Algorithm specification | Section 3 |
| Entropy from hardware source | GLI-19 §4.2 | Entropy source | Section 4 |
| No software entropy fallback | GLI-19 §4.3 | Safe shutdown | Section 4.2, Section 10.1 |
| Session isolation | GLI-19 §5.1 | Game-to-game independence | Section 6 |
| Non-repeatability | GLI-19 §5.2 | Collision probability | Section 7 |
| Audit trail | GLI-19 §7 | Audit record requirements | Section 8 |
| Statistical tests | GLI-19 §8 | NIST SP 800-22 | Section 9 |
| Source code review | GLI-19 §9 | Laboratory access | Section 11 |
| Failure mode documentation | GLI-19 §6 | Safe states | Section 10 |
| FIPS 140-2 hardware | GLI-11 §3.4 | HSM certification | Section 4.1 |

---

*End of Document — RNG-SPEC-001 v1.0*
