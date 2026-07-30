# crypto-engine

High-throughput AEAD crypto library for iGaming workloads — game events,
Kafka messages, logs, and backups.

Written in Rust. Exposed to Python and Go via a C ABI.

## Algorithms

| Algorithm          | alg_id | Key  | Nonce | Tag | When to use                              |
|--------------------|--------|------|-------|-----|------------------------------------------|
| AEGIS-128L         | 0x01   | 16 B | 16 B  | 16 B| Default. Fastest on AES-NI / ARMv8-AES.  |
| AEGIS-256          | 0x02   | 32 B | 32 B  | 16 B| Highest safety margin, same family.      |
| AES-256-GCM        | 0x03   | 32 B | 12 B  | 16 B| pgcrypto interop, NIST-blessed.          |
| ChaCha20-Poly1305  | 0x04   | 32 B | 12 B  | 16 B| Software fallback for non-AES-NI CPUs.   |

All four provide **IND-CCA2** security and a **128-bit authentication tag**.

### When to pick what

- **AEGIS-128L** — pick this for everything running on modern x86_64 (AES-NI)
  or ARMv8 (Apple Silicon, Graviton) hardware. Not yet on the NIST-approved
  list but standardized in RFC 9380 / CAESAR.
- **AES-256-GCM** — pick when you must interop with PostgreSQL `pgcrypto`,
  AWS KMS envelopes, or FIPS-bound auditors.
- **ChaCha20-Poly1305** — pick on CPUs without AES hardware (rare in 2026;
  embedded, old VMs). No timing side channels without AES-NI.
- **AEGIS-256** — pick when you want the AEGIS speed with a 256-bit key.

Use `CryptoEngine::new_auto` to let the library pick based on CPU features.

## Wire format

```
[1 byte alg_id][N bytes nonce][K bytes ciphertext][16 bytes tag]
```

Nonce length (`N`) depends on `alg_id`:

| alg_id | nonce len |
|--------|-----------|
| 0x01   | 16        |
| 0x02   | 32        |
| 0x03   | 12        |
| 0x04   | 12        |

The ciphertext length (`K`) equals the plaintext length (all four ciphers
are length-preserving streams). A receiver decodes the payload entirely
from `alg_id`:

```python
alg = payload[0]
nonce_len = {1: 16, 2: 32, 3: 12, 4: 12}[alg]
nonce = payload[1 : 1 + nonce_len]
ct    = payload[1 + nonce_len : -16]
tag   = payload[-16:]
```

## Build

```bash
cd crypto-engine
cargo build --release
# → target/release/libcrypto_engine.dylib (macOS)
# → target/release/libcrypto_engine.so    (Linux)
cargo test --release
cargo run --release --bin bench
```

The release profile uses `lto=true`, `codegen-units=1`, and `opt-level=3`.

## Rust usage

```rust
use crypto_engine::{CryptoEngine, NonceGenerator};

let key = crypto_engine::random_key_16();
let engine = CryptoEngine::new_aegis128l(&key)?;

// Random nonce (fine for < 2^48 messages):
let payload = engine.encrypt(b"game event", b"user=42;topic=bets")?;
let plain = engine.decrypt(&payload, b"user=42;topic=bets")?;

// Counter-based nonce (preferred for high throughput):
let nonces = NonceGenerator::new(16, producer_id);
let payload = engine.encrypt_with_nonce(b"event", b"aad", &nonces.next())?;
```

## Python usage

```python
from crypto_engine import CryptoEngine
import os

engine = CryptoEngine.aegis128l(os.urandom(16))
payload = engine.encrypt(b"sensitive data", aad=b"context")
plain = engine.decrypt(payload, aad=b"context")
```

Point the loader at the shared library:

```bash
export CRYPTO_ENGINE_LIB=/path/to/libcrypto_engine.so
python3 -m unittest test_crypto_engine
```

## Go usage

```go
import cryptoengine "github.com/you/crypto-engine-go"

e, _ := cryptoengine.NewAegis128L(key16)
defer e.Close()
payload, _ := e.Encrypt([]byte("event"), []byte("ctx"))
plain, _ := e.Decrypt(payload, []byte("ctx"))
```

Build with:

```bash
export CGO_LDFLAGS="-L/abs/path/to/crypto-engine/target/release -lcrypto_engine"
export LD_LIBRARY_PATH="/abs/path/to/crypto-engine/target/release"
go build ./...
go test ./...
```

## Examples

### Kafka event encryption pipeline

Reads JSON events from stdin, emits base64 wire payloads:

```bash
cat events.jsonl | \
  CRYPTO_KEY_HEX=0123456789abcdef0123456789abcdef PRODUCER_ID=7 \
  ./target/release/examples/kafka_event_encrypt > encrypted.b64
```

Measured: **459,949 events/sec** on Apple M-series (single thread, ~75-byte
JSON events), far above the 100K/sec target.

### Log stream encryption

Reads raw bytes, batches into 64 KB blocks, emits length-prefixed payloads:

```bash
tail -F /var/log/app.log | \
  CRYPTO_KEY_HEX=... STREAM_ID=3 \
  ./target/release/examples/log_stream_encrypt > logs.enc
```

## Benchmarks

Measured on Apple M-series (ARMv8 AES), single core, `cargo run --release --bin bench`:

| Algorithm          | 64 B     | 1 KB     | 16 KB    | 1 MB     |
|--------------------|----------|----------|----------|----------|
| **AEGIS-128L**     | 1.17 GB/s| 5.82 GB/s| 8.52 GB/s| 8.85 GB/s|
| AEGIS-256          | 1.21 GB/s| 4.54 GB/s| 6.24 GB/s| 5.73 GB/s|
| AES-256-GCM        | 0.06 GB/s| 0.10 GB/s| 0.14 GB/s| 0.15 GB/s|
| ChaCha20-Poly1305  | 0.26 GB/s| 0.40 GB/s| 0.42 GB/s| 0.43 GB/s|

**AEGIS-128L is ~60× faster than aes-gcm (Rust AES-256-GCM)** at 1 MB messages.

On production x86_64 (AES-NI + VAES), expect AEGIS-128L ~12 GB/s per core
and AES-256-GCM ~1.4 GB/s per core (see `deploy-ops-host.sh` to rerun on
iGaming benchmark VMs).

## YubiHSM integration pattern

Don't put long-lived keys in application memory. Use an HSM to wrap data
keys:

```text
┌──────────────────────┐           ┌───────────────────┐
│   YubiHSM 2          │           │ app process       │
│  (root AES-256-GCM)  │◄────────►│ (AEGIS-128L)      │
│                      │  wraps    │                   │
│  KEK: master_key_v3  │  data_key │ data_key (16 B)   │
└──────────────────────┘           └───────────────────┘
```

1. App starts → calls YubiHSM `aes-gcm unwrap` on the stored blob to recover
   the 16-byte data key for AEGIS-128L.
2. The data key lives in memory wrapped by our [`Key16`] type, which
   zeroises on drop.
3. All hot-path encryption uses AEGIS-128L with that data key.
4. Rotate the data key every 24h or after 2^48 messages, whichever comes
   first. Store each wrapped key blob versioned — include the version in
   the AAD so a receiver knows which key to unwrap.

The wrapped blob format:

```
[4 bytes key_version][AES-GCM wrap of 16-byte AEGIS data key]
```

## Security properties

- **IND-CCA2** (indistinguishability under adaptive chosen-ciphertext attack)
- **INT-CTXT** (ciphertext integrity — forgery is infeasible)
- 128-bit authentication tag → ~2^-128 forgery probability
- **Nonce reuse is catastrophic** for all four ciphers. Always use the
  `NonceGenerator` in high-throughput paths.

## Testing

```bash
cd crypto-engine
cargo test --release           # 13 unit tests + 2 doctests

cd ../python
python3 -m unittest test_crypto_engine -v   # 5 tests

cd ../go
export CGO_LDFLAGS="-L../crypto-engine/target/release -lcrypto_engine"
export DYLD_LIBRARY_PATH="../crypto-engine/target/release"  # macOS
export LD_LIBRARY_PATH="../crypto-engine/target/release"    # Linux
go test -v ./...              # 6 tests (3 tests, AllAlgorithms has 4 subtests)
```

## File layout

```
aegis-rust/
├── crypto-engine/            # Rust library (rlib + cdylib)
│   ├── Cargo.toml
│   ├── src/
│   │   ├── lib.rs
│   │   ├── error.rs
│   │   ├── key.rs            # Zeroize-on-drop key wrappers
│   │   ├── nonce.rs          # Counter-based nonce generator
│   │   ├── ffi.rs            # C ABI (only unsafe module)
│   │   └── bin/bench.rs
│   └── examples/
│       ├── kafka_event_encrypt/
│       └── log_stream_encrypt/
├── python/
│   ├── crypto_engine.py      # ctypes wrapper
│   └── test_crypto_engine.py
├── go/
│   ├── crypto_engine.go      # CGO wrapper
│   └── crypto_engine_test.go
├── deploy-ops-host.sh         # deploy + benchmark on Linux VM
└── README.md
```
