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
demo-crypto-shredding.py — End-to-end crypto-shredding demonstration.

Crypto-shredding: encrypt data with a per-record key, then destroy the key.
The encrypted data remains in place (satisfying backup/archival systems) but
becomes permanently unrecoverable — equivalent to deletion.

Workflow:
    1. Generate AES-256 key for a player's PII
    2. Encrypt all PII fields with that key (AES-256-GCM)
    3. Store encrypted PII + key reference (key stored separately in OpenBao)
    4. On GDPR Art.17 deletion request: destroy ONLY the key
    5. Encrypted data becomes permanently unrecoverable
    6. Transaction records (non-PII) remain intact for AML compliance

Usage:
    python3 demo-crypto-shredding.py [options]

Options:
    --pg-host HOST      PostgreSQL host (default: localhost)
    --pg-port PORT      PostgreSQL port (default: 5432)
    --pg-user USER      PostgreSQL user (default: postgres)
    --pg-password PASS  PostgreSQL password
    --test-mode         Run in test mode (prints PASS/FAIL, cleans up)
    --keep-db           Do not drop test database after run

Compliance: GDPR Art.17; PCI DSS v4.0.1 Req.3.5.1; FATF Recommendation 11
"""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import sys
from dataclasses import dataclass, field
from typing import Optional

try:
    import psycopg2
    import psycopg2.extensions
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    print("ERROR: cryptography not installed. Run: pip install cryptography")
    sys.exit(1)


# ---------------------------------------------------------------------------
# In-memory key store (simulates OpenBao Transit in demo)
# In production: keys live in OpenBao; never in application memory or DB
# ---------------------------------------------------------------------------
class InMemoryKeyStore:
    """Simulate OpenBao Transit Engine for demonstration purposes."""

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}
        self._audit_log: list[str] = []

    def create_key(self, key_id: str) -> bytes:
        """Generate and store a new 256-bit AES key."""
        key = secrets.token_bytes(32)
        self._keys[key_id] = key
        self._audit_log.append(f"CREATE key_id={key_id}")
        return key

    def get_key(self, key_id: str) -> Optional[bytes]:
        """Retrieve key if it exists."""
        return self._keys.get(key_id)

    def destroy_key(self, key_id: str) -> bool:
        """
        Permanently destroy a key.
        This is the crypto-shredding operation: after this call,
        any data encrypted with this key is permanently unrecoverable.
        """
        if key_id not in self._keys:
            return False
        # Overwrite key bytes before deletion (defence in depth)
        key_bytes = self._keys[key_id]
        # Zero out the key material
        zeroed = bytearray(len(key_bytes))
        self._keys[key_id] = bytes(zeroed)
        del self._keys[key_id]
        self._audit_log.append(f"DESTROY key_id={key_id} — data permanently unrecoverable")
        return True

    def key_exists(self, key_id: str) -> bool:
        return key_id in self._keys

    def audit_log(self) -> list[str]:
        return list(self._audit_log)


# ---------------------------------------------------------------------------
# PII encryption utilities
# ---------------------------------------------------------------------------
def encrypt_pii(plaintext: str, key: bytes, associated_data: bytes = b"") -> str:
    """
    Encrypt a PII field using AES-256-GCM.

    Returns base64-encoded (nonce || ciphertext) suitable for storage
    in a TEXT database column.

    AES-256-GCM provides both confidentiality and authenticity.
    The 96-bit nonce is randomly generated per encryption call.
    """
    nonce = secrets.token_bytes(12)  # 96-bit nonce for GCM
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data)
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode("ascii")


def decrypt_pii(
    encoded: str, key: bytes, associated_data: bytes = b""
) -> Optional[str]:
    """
    Decrypt a PII field. Returns None if key is wrong or data is corrupt.
    This is expected to return None after crypto-shredding.
    """
    try:
        combined = base64.b64decode(encoded.encode("ascii"))
        nonce = combined[:12]
        ciphertext = combined[12:]
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data)
        return plaintext.decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cs_demo_players (
    id              BIGSERIAL PRIMARY KEY,
    player_uuid     TEXT NOT NULL,
    email_enc       TEXT,           -- AES-256-GCM encrypted
    phone_enc       TEXT,           -- AES-256-GCM encrypted
    full_name_enc   TEXT,           -- AES-256-GCM encrypted
    dek_id          TEXT NOT NULL,  -- Key reference in OpenBao (NOT the key itself)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,
    gdpr_erasure_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS cs_demo_transactions (
    id          BIGSERIAL PRIMARY KEY,
    player_id   BIGINT REFERENCES cs_demo_players(id),
    player_uuid TEXT NOT NULL,
    amount      NUMERIC(12,2) NOT NULL,
    game_ref    TEXT NOT NULL,
    txn_date    TIMESTAMPTZ DEFAULT NOW()
    -- Note: no PII here; player_uuid links to pseudonymised record
    -- Amounts and game refs are retained for AML compliance
);
"""


# ---------------------------------------------------------------------------
# Demo runner
# ---------------------------------------------------------------------------
@dataclass
class CryptoShredDemo:
    conn: psycopg2.extensions.connection
    key_store: InMemoryKeyStore = field(default_factory=InMemoryKeyStore)
    test_mode: bool = False
    results: list[tuple[str, bool]] = field(default_factory=list)

    def _record(self, label: str, passed: bool) -> None:
        self.results.append((label, passed))
        status = "PASS" if passed else "FAIL"
        print(f"  {status}  {label}")

    def setup_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        self.conn.commit()
        print("  INFO  Schema created")

    def create_player(self) -> tuple[int, str]:
        """Create a player with encrypted PII."""
        player_uuid = secrets.token_hex(16)
        dek_id = f"pii-key-{player_uuid}"
        key = self.key_store.create_key(dek_id)

        # AAD: player_uuid prevents ciphertexts being swapped between players
        aad = player_uuid.encode("utf-8")

        email_enc = encrypt_pii("alice.smith@example.com", key, aad)
        phone_enc = encrypt_pii("+44 7700 900123", key, aad)
        name_enc = encrypt_pii("Alice Smith", key, aad)

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cs_demo_players
                    (player_uuid, email_enc, phone_enc, full_name_enc, dek_id)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (player_uuid, email_enc, phone_enc, name_enc, dek_id),
            )
            row = cur.fetchone()
            player_id: int = row[0]
        self.conn.commit()
        print(f"  INFO  Created player ID={player_id}, UUID={player_uuid}")
        return player_id, player_uuid

    def create_transactions(self, player_id: int, player_uuid: str) -> int:
        """Insert transaction records — these contain NO PII."""
        txns = [
            (player_id, player_uuid, "50.00", "book-of-dead-001"),
            (player_id, player_uuid, "200.00", "roulette-002"),
            (player_id, player_uuid, "75.00", "blackjack-003"),
            (player_id, player_uuid, "1500.00", "jackpot-slots-004"),
        ]
        with self.conn.cursor() as cur:
            for txn in txns:
                cur.execute(
                    """
                    INSERT INTO cs_demo_transactions
                        (player_id, player_uuid, amount, game_ref)
                    VALUES (%s, %s, %s, %s)
                    """,
                    txn,
                )
        self.conn.commit()
        print(f"  INFO  Created {len(txns)} transaction records (non-PII)")
        return len(txns)

    def verify_decryptable(self, player_id: int) -> bool:
        """Verify PII can be decrypted when key is available."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT player_uuid, email_enc, dek_id FROM cs_demo_players WHERE id = %s",
                (player_id,),
            )
            row = cur.fetchone()
            if not row:
                return False

            key = self.key_store.get_key(row["dek_id"])
            if not key:
                return False

            aad = row["player_uuid"].encode("utf-8")
            decrypted = decrypt_pii(row["email_enc"], key, aad)
            return decrypted is not None and "@" in decrypted

    def crypto_shred(self, player_id: int) -> None:
        """
        Perform crypto-shredding:
        1. Look up the DEK ID for this player
        2. Destroy the key in the key store (OpenBao in production)
        3. Mark player record as GDPR-erased
        4. Encrypted ciphertext remains in DB but is permanently unrecoverable
        """
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT dek_id FROM cs_demo_players WHERE id = %s",
                (player_id,),
            )
            row = cur.fetchone()
            if not row:
                return
            dek_id = row[0]

        # The critical operation: destroy the key
        destroyed = self.key_store.destroy_key(dek_id)
        print(f"  INFO  Key {dek_id} destroyed: {destroyed}")

        # Mark the record (audit trail)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE cs_demo_players
                SET gdpr_erasure_at = NOW(),
                    deleted_at      = NOW()
                WHERE id = %s
                """,
                (player_id,),
            )
        self.conn.commit()
        print(
            f"  INFO  Player {player_id} marked as GDPR-erased. "
            "Ciphertext remains but is permanently unrecoverable."
        )

    def verify_unrecoverable(self, player_id: int) -> bool:
        """Verify that PII is unrecoverable after key destruction."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT player_uuid, email_enc, dek_id FROM cs_demo_players WHERE id = %s",
                (player_id,),
            )
            row = cur.fetchone()
            if not row:
                return True  # Record deleted entirely is also "unrecoverable"

            # Confirm key is gone
            if self.key_store.key_exists(row["dek_id"]):
                print("  FAIL  Key still exists after crypto-shred!")
                return False

            # Attempt decryption with destroyed key
            aad = row["player_uuid"].encode("utf-8")
            # Key is gone — we can't even attempt proper decryption
            # Try with a random wrong key to confirm ciphertext is still there
            wrong_key = secrets.token_bytes(32)
            result = decrypt_pii(row["email_enc"], wrong_key, aad)
            # Result must be None (decryption fails) — the ciphertext exists but is unreadable
            return result is None

    def verify_transactions_intact(self, player_id: int) -> int:
        """Verify transaction records remain after crypto-shredding."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM cs_demo_transactions WHERE player_id = %s",
                (player_id,),
            )
            row = cur.fetchone()
            return row[0] if row else 0

    def cleanup(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS cs_demo_transactions CASCADE")
            cur.execute("DROP TABLE IF EXISTS cs_demo_players CASCADE")
        self.conn.commit()
        print("  INFO  Test tables cleaned up")

    def run(self) -> bool:
        """Run the full crypto-shredding demonstration."""
        print("\n=== Crypto-Shredding Demonstration ===")
        print("Compliance: GDPR Art.17; PCI DSS v4.0.1 Req.3.5.1; FATF Rec.11\n")

        self.setup_schema()

        # Step 1: Create player with encrypted PII
        print("\n--- Step 1: Create player with encrypted PII ---")
        player_id, player_uuid = self.create_player()
        txn_count = self.create_transactions(player_id, player_uuid)

        # Step 2: Verify PII is decryptable (key exists)
        print("\n--- Step 2: Verify PII is readable (key available) ---")
        can_decrypt = self.verify_decryptable(player_id)
        self._record("PII decryptable before crypto-shred (key exists)", can_decrypt)

        # Step 3: Crypto-shred — destroy the key
        print("\n--- Step 3: Crypto-shredding (GDPR Art.17 erasure request) ---")
        self.crypto_shred(player_id)

        # Step 4: Verify PII is unrecoverable
        print("\n--- Step 4: Verify PII is permanently unrecoverable ---")
        unrecoverable = self.verify_unrecoverable(player_id)
        self._record(
            "PII unrecoverable after key destruction (AES-256-GCM)", unrecoverable
        )

        # Step 5: Verify transactions remain
        print("\n--- Step 5: Verify AML records remain intact ---")
        remaining_txns = self.verify_transactions_intact(player_id)
        self._record(
            f"Transactions retained for AML compliance ({remaining_txns}/{txn_count})",
            remaining_txns == txn_count,
        )

        # Step 6: Audit trail
        print("\n--- Step 6: Key store audit log ---")
        for entry in self.key_store.audit_log():
            print(f"  AUDIT  {entry}")

        # Print summary
        print("\n=== Summary ===")
        passed = sum(1 for _, ok in self.results if ok)
        total = len(self.results)
        for label, ok in self.results:
            status = "PASS" if ok else "FAIL"
            print(f"  {status}  {label}")
        print(f"\n  Total: {passed}/{total} passed")

        all_passed = all(ok for _, ok in self.results)
        if all_passed:
            print("\n  PASS  Crypto-shredding demonstration completed successfully.")
        else:
            print("\n  FAIL  One or more crypto-shredding checks failed.")

        return all_passed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crypto-shredding demonstration for iGaming platforms"
    )
    parser.add_argument("--pg-host", default="localhost")
    parser.add_argument("--pg-port", type=int, default=5432)
    parser.add_argument("--pg-user", default="postgres")
    parser.add_argument("--pg-password", default=os.environ.get("PG_PASSWORD", ""))
    parser.add_argument("--pg-db", default="postgres")
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--keep-db", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Try sslmode=require first (production); fall back to prefer for dev/demo
    for sslmode in ("require", "prefer"):
        try:
            conn = psycopg2.connect(
                host=args.pg_host,
                port=args.pg_port,
                user=args.pg_user,
                password=args.pg_password,
                dbname=args.pg_db,
                sslmode=sslmode,
            )
            if sslmode != "require":
                print(f"  WARN  Connected without SSL (sslmode={sslmode}) — demo only")
                print("        In production, sslmode=require must be enforced.")
            break
        except psycopg2.OperationalError as exc:
            if sslmode == "prefer":
                print(f"ERROR: Cannot connect to PostgreSQL: {exc}")
                print("Hint: Set PG_PASSWORD env var or use --pg-password")
                sys.exit(1)

    demo = CryptoShredDemo(conn=conn, test_mode=args.test_mode)

    try:
        success = demo.run()
    finally:
        if not args.keep_db:
            demo.cleanup()
        conn.close()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
