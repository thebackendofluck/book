# hsm-keymgr

Minimal CLI for YubiHSM2 + AEGIS-128L key management.

## Quick start (dev mode — no hardware required)

```bash
cd hsm-keymgr

# Generate a 32-byte hex master key for dev use
export DEV_MASTER_KEY=$(openssl rand -hex 32)

# 1. Generate a data key and wrap it
cargo run --no-default-features -- generate --no-hsm --label mykey --output wrapped.bin

# 2. Unwrap to get the raw key bytes
cargo run --no-default-features -- unwrap --no-hsm --wrapped wrapped.bin --output key.bin

# 3. Encrypt a file
cargo run --no-default-features -- encrypt --key key.bin --input plaintext.txt --output cipher.bin

# 4. Decrypt
cargo run --no-default-features -- decrypt --key key.bin --input cipher.bin --output recovered.txt
```

## Running tests (dev mode only)

Tests use `env::set_var` for master key isolation, so run single-threaded:

```bash
cargo test --no-default-features -- --test-threads=1
```

## HSM mode (requires physical YubiHSM2)

Build with default features (HSM enabled):

```bash
export HSM_AUTH_KEY_ID=1
# Set HSM_PASSWORD from your secret manager / hardware token; never commit a literal default.
export HSM_PASSWORD="$(read -s -p 'YubiHSM password: ' p && echo "$p")"
export HSM_WRAP_KEY_ID=2

cargo build   # default features include "hsm"
./target/debug/hsm-keymgr generate --label mykey --output wrapped.bin
```

Do NOT run HSM mode against a production device without reviewing the key policy.
The `hsm.rs` backend is a starting point — production deployments should keep data
keys inside the HSM and use HSM-side encrypt/decrypt rather than exporting raw key bytes.

## Wire formats

### Wrapped key blob (`.bin` from `generate`/`unwrap`)

| Offset | Length | Field |
|--------|--------|-------|
| 0 | 1 | version (0x01) |
| 1 | 1 | algorithm (0x01=dev/AES-GCM, 0x02=HSM) |
| 2 | 4 | payload length (u32 LE) |
| 6 | N | payload: `nonce(12) \|\| AES-GCM-ciphertext(32+16)` |

### AEGIS ciphertext file (from `encrypt`)

| Offset | Length | Field |
|--------|--------|-------|
| 0 | 16 | AEGIS-128L nonce |
| 16 | 16 | AEGIS-128L authentication tag |
| 32 | N | ciphertext |

## Security notes

- All key types implement `ZeroizeOnDrop` — key bytes are wiped from memory on drop.
- `DEV_MASTER_KEY` is for development only. Never use it in production.
- The AEGIS-128L key is derived from the first 16 bytes of the 32-byte data key.
- Key material is never logged.
- The wrapped blob format includes a version byte for future algorithm agility.
