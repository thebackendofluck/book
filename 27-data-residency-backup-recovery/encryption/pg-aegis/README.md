# pg_aegis

PostgreSQL extension providing **AEGIS-128L** and **ChaCha20-Poly1305**
AEAD column encryption, as a high-throughput replacement for
pgcrypto's `pgp_sym_encrypt` / AES-256-CBC.

Built with [pgrx](https://github.com/pgcentralfoundation/pgrx) 0.12 on
PostgreSQL 14 – 17.

## Why

| Cipher | Throughput (Rust / AMD EPYC) | Notes |
|---|---|---|
| AEGIS-128L | ~12 GB/s | SIMD-optimized, IETF CFRG draft 2024 |
| ChaCha20-Poly1305 | ~1.3 GB/s | Software-only, constant-time, well-reviewed |
| AES-256-GCM | ~1.4 GB/s | AES-NI required |
| pgcrypto AES-256-CBC (pgp_sym) | much slower | armored, key derivation per call |

For bulk column encrypt/decrypt in a hot path (gaming session state,
audit logs, PII), AEGIS-128L is the fastest credible option.

## Wire format

bytea column layout:

```
[1 byte alg tag][4 bytes key_version BE][16 bytes nonce][ciphertext][16 bytes tag]
```

Alg tags: `0x01` AEGIS-128L, `0x02` ChaCha20-Poly1305. For ChaCha the
nonce field's last 4 bytes are zero (12-byte nonce padded to 16 for
wire uniformity).

## SQL reference

```sql
-- Encryption
SELECT aegis_encrypt('sensitive data', 'player_pii_key');              -- bytea
SELECT aegis_encrypt_aad(plaintext, 'key', 'players:email:42');        -- bound to row
SELECT chacha_encrypt('sensitive data', 'player_pii_key');             -- bytea

-- Decryption
SELECT aegis_decrypt(col, 'player_pii_key');                           -- text
SELECT aegis_decrypt_aad(col, 'player_pii_key', 'players:email:42');   -- text

-- Key management
SELECT aegis_generate_key('player_pii_key');
SELECT aegis_rotate_key('player_pii_key');   -- returns new version int

SELECT aegis_version();
```

Keys live in `pg_aegis_keys`. The `encrypted_key` column holds a
ChaCha20-Poly1305-wrapped data key; the wrapping master key is read
from the GUC `pg_aegis.master_key_b64`. In production, swap the
`get_master_key()` / `wrap_key` path for a YubiHSM wrap/unwrap
(hook point marked in `src/lib.rs`).

## Install

```bash
# Prereqs (Debian/Ubuntu, PG 16)
sudo apt install -y postgresql-16 postgresql-server-dev-16 build-essential pkg-config libssl-dev
rustup install stable
cargo install --locked cargo-pgrx --version 0.12.9
cargo pgrx init --pg16=$(which pg_config)

# Build + install
cd pg-aegis
cargo pgrx install --release --features pg16 --sudo
```

In `postgresql.conf`:
```
shared_preload_libraries = 'pg_aegis'
pg_aegis.master_key_b64  = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='  # 32 random bytes, base64
```

Restart, then:
```sql
CREATE EXTENSION pg_aegis;
SELECT aegis_generate_key('player_pii_key');
```

## Test

```bash
cargo pgrx test pg16
```

Tests cover: round-trip, tamper detection (GCM-style), AAD binding
(wrong AAD must fail).

## Migrate from pgcrypto

```sql
\i sql/migrate_from_pgcrypto.sql
SELECT aegis_generate_key('player_pii_key');
CALL migrate_pgcrypto_to_aegis_proc(
    'players', 'email_enc',
    'old-pgp-passphrase',
    'player_pii_key',
    5000
);
```

The procedure re-encrypts in 5k-row batches with `FOR UPDATE SKIP
LOCKED`, commits between batches, and binds AAD to `table:column:id`.
Rows whose first byte is already `0x01` (pg_aegis wire format) are
skipped, so the migration is idempotent and resumable.

## Benchmarks

`benchmarks/benchmark.sql` times 100 k-row insert + decrypt with both
pgcrypto and pg_aegis on the same payloads, plus reports storage
overhead (pgcrypto OpenPGP armor ~30 % vs pg_aegis fixed 37 bytes).

## Security

* **Keys are never logged.** The `SecretKey` wrapper zeroizes on drop.
* **AAD binding.** Use `aegis_encrypt_aad` with `table:column:id` to
  prevent ciphertext-swap attacks between rows.
* **Master key.** GUC `pg_aegis.master_key_b64` is intended for dev.
  In production replace with a YubiHSM `wrap`/`unwrap` call (YubiHSM
  does not natively support AEGIS, only AES, so key-wrapping stays on
  AES-CCM — the data keys themselves drive AEGIS).
* **Audit.** `pg_aegis_audit` table is created. Wrap `aegis_decrypt`
  in an application-side view / SECURITY DEFINER function that INSERTs
  on every call.
* **Key rotation.** `aegis_rotate_key` bumps the version and rewraps
  the data key. For existing ciphertexts to remain decryptable after
  rotation, keep historical key versions (extension left as TODO: a
  `pg_aegis_keys_history` table keyed by `(name, version)`).

## Algorithm choice

AEGIS-128L is a CFRG draft (RFC 9380 related) but is not yet a full
IETF RFC. ChaCha20-Poly1305 is RFC 8439 and universally deployed. Use
ChaCha where compliance/interop dominates; use AEGIS where throughput
dominates and crypto agility is acceptable.

## Project layout

```
pg-aegis/
  Cargo.toml
  pg_aegis.control
  install.sh
  src/lib.rs              # pgrx SQL functions + AEAD + key management
  sql/
    pg_aegis--0.1.0.sql   # schema bootstrap (keys + audit tables)
    migrate_from_pgcrypto.sql
  benchmarks/benchmark.sql
  tests/                  # (integration fixtures live in src/lib.rs#tests)
```
