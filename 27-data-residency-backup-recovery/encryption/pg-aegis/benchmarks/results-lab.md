# pg_aegis Benchmark — measured on ops-host, PostgreSQL 16

**Date:** 2026-04-04
**Host:** ops-host, AMD Ryzen Threadripper PRO 3995WX, 64C/128T, Ubuntu 24.04
**CPU features:** AES-NI, AVX2, PCLMULQDQ, SHA-NI, BMI2
**PostgreSQL:** 16.13 (system apt package, default config)
**Toolchain:** pgrx 0.12.9, rustc 1.94.1 (release build, LTO=fat)

## End-to-end PG benchmark (100 000 rows)

Workload: synthetic PII payloads, `avg plaintext = 144 bytes` (range 32–256 B),
inserted into an UNLOGGED table, then decrypted on read.

| Metric | pgcrypto `pgp_sym_encrypt` (AES-256-CBC) | pg_aegis `aegis_encrypt` (AEGIS-128L) | Speedup |
|---|---:|---:|---:|
| INSERT 100 k rows (encrypt) | **15.32 s** | **3.53 s** | **4.34x** |
| SELECT 100 k rows (decrypt) | **4.99 s** | **3.13 s** | **1.60x** |
| Total table size | 27 MB | **23 MB** | -15% |
| Avg ciphertext size | 211 B (+67 B overhead, 47 %) | **181 B** (+37 B overhead, 26 %) | smaller |

The INSERT path is where `pgp_sym_encrypt`'s per-call S2K key derivation
dominates; AEGIS-128L skips that entirely because the data key is
pre-unwrapped once per statement via SPI. On bulk re-encryption
workloads (e.g. rotating a column) pg_aegis is **4.3x faster**.

The SELECT/decrypt gap is smaller because both paths pay for SPI
round-trips and bytea construction; the cipher itself is a minor
fraction of the per-row cost at 144 B payloads.

## Raw AEAD throughput (out-of-PG, same host)

Single-threaded, `RUSTFLAGS=-C target-cpu=native`:

| Payload | AEGIS-128L | ChaCha20-Poly1305 | AES-256-GCM | AEGIS vs AES-GCM |
|---:|---:|---:|---:|---:|
|    32 B |    489 MB/s |  103 MB/s |   588 MB/s | 0.83x |
|    64 B |    920 MB/s |  160 MB/s |   745 MB/s | 1.24x |
|   128 B |  1,271 MB/s |  244 MB/s |   887 MB/s | 1.43x |
|   256 B |  2,268 MB/s |  477 MB/s | 1,110 MB/s | 2.04x |
|   512 B |  4,005 MB/s |  697 MB/s | 1,190 MB/s | 3.37x |
| 1,024 B |  6,205 MB/s |  885 MB/s | 1,216 MB/s | 5.10x |
| 4,096 B |  9,952 MB/s | 1,229 MB/s | 1,325 MB/s | 7.51x |
|16,384 B | 11,968 MB/s | 1,308 MB/s | 1,275 MB/s | **9.39x** |

## Interpretation

* **For typical single PII fields (32–256 bytes):** raw AEGIS is only
  1–2x faster than AES-GCM. Per-call constructor cost dominates. But
  against `pgp_sym_encrypt` (which does S2K key derivation per call),
  pg_aegis still wins **4x** on INSERT because it skips the KDF.
* **For larger payloads (≥ 1 KB):** AEGIS-128L is **5x – 9x** faster
  than AES-GCM raw. Consider this for bulk re-encryption, session
  blobs, encrypted JSON documents, or audit-log batches.
* **ChaCha20-Poly1305** is the slowest on this CPU (AES-NI kills it).
  Keep it only for platforms without AES hardware acceleration.
* **Storage:** pg_aegis wire format is fixed 37-byte overhead
  (1 alg + 4 version + 16 nonce + 16 tag) — no OpenPGP framing, no
  armor, no per-row S2K salt. Saves ~15 % on short-field tables.

## Reproducing

```bash
# Raw AEAD
cd pg-aegis/benchmarks/standalone
RUSTFLAGS='-C target-cpu=native' cargo run --release

# End-to-end in PG
cd pg-aegis/benchmarks
psql -U postgres -v ON_ERROR_STOP=1 -f benchmark.sql
```
