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
demo-pseudonymisation.py — GDPR Art.17 pseudonymisation for iGaming.

Pseudonymisation workflow:
    1. Player requests data deletion (GDPR Art.17 right to erasure)
    2. Check AML hold period (5 years minimum per FATF Recommendation 11)
    3. Replace PII with HMAC-SHA-256 hashes using an ephemeral salt
    4. Destroy the salt — hashes become irreversible
    5. Keep transaction skeleton (amounts, dates, game refs) for AML compliance
    6. Self-exclusion flags are NEVER deleted (regulatory exemption)

Why pseudonymisation instead of deletion?
    - Transaction records must be retained 5 years (AML / FATF)
    - Player linked to those records must be identifiable for AML investigation
    - Pseudonym preserves the link without identifying the natural person
    - GDPR Recital 26: properly pseudonymised data need not be deleted
    - GDPR Art.17(3)(b): erasure does not apply where processing is legally required

Usage:
    python3 demo-pseudonymisation.py [options]

Options:
    --pg-host HOST     PostgreSQL host (default: localhost)
    --pg-port PORT     PostgreSQL port (default: 5432)
    --pg-user USER     PostgreSQL user (default: postgres)
    --pg-password PASS PostgreSQL password
    --keep-db          Do not clean up test tables after run

Compliance: GDPR Art.17, Art.4(5), Recital 26, Recital 65;
            FATF Recommendation 11; UK POCA 2002; MLA Directive 2015/849
"""

from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_module
import os
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import psycopg2
    import psycopg2.extensions
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ps_demo_players (
    id                  BIGSERIAL PRIMARY KEY,
    player_uuid         TEXT NOT NULL UNIQUE,
    email               TEXT,
    phone               TEXT,
    full_name           TEXT,
    date_of_birth       DATE,
    address             TEXT,
    kyc_document_number TEXT,
    self_excluded       BOOLEAN DEFAULT FALSE,
    self_excluded_at    TIMESTAMPTZ,
    exclusion_source    TEXT,
    aml_hold_until      TIMESTAMPTZ,   -- earliest permitted deletion date
    pseudonymised_at    TIMESTAMPTZ,
    pseudonym           TEXT,          -- public pseudonym (stable across records)
    gdpr_request_ref    TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ps_demo_transactions (
    id              BIGSERIAL PRIMARY KEY,
    player_id       BIGINT REFERENCES ps_demo_players(id),
    player_uuid     TEXT NOT NULL,
    pseudonym       TEXT,              -- copied at erasure time for AML linkage
    amount          NUMERIC(12,2),
    currency        TEXT DEFAULT 'EUR',
    game_ref        TEXT,
    aml_flagged     BOOLEAN DEFAULT FALSE,
    txn_date        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ps_demo_gdpr_requests (
    id              BIGSERIAL PRIMARY KEY,
    player_id       BIGINT REFERENCES ps_demo_players(id),
    request_ref     TEXT UNIQUE,
    request_type    TEXT DEFAULT 'erasure',  -- erasure | rectification | access
    requested_at    TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    outcome         TEXT,   -- 'pseudonymised' | 'rejected_aml_hold' | 'rejected_self_exclusion'
    legal_basis     TEXT    -- statutory justification for any rejection/partial erasure
);
"""

AML_RETENTION_YEARS = 5  # FATF Recommendation 11; EU 4th/5th AMLD
MIN_ACCOUNT_AGE_DAYS = 0  # Some operators add a minimum e.g. 30 days


# ---------------------------------------------------------------------------
# Pseudonymisation logic
# ---------------------------------------------------------------------------
def generate_pseudonym(player_id: int, player_uuid: str) -> tuple[str, None]:
    """
    Generate a pseudonym using HMAC-SHA-256 with an ephemeral salt.

    Returns (pseudonym, None) — the salt is NEVER returned or stored.
    The pseudonym is deterministic within this call but non-reversible
    because the salt is immediately destroyed after this function returns.

    In production, the same salt MUST NOT be reused across different erasure
    requests (it would allow linking pre/post-erasure records).
    """
    # Ephemeral salt — exists only in this function call's stack frame
    salt = secrets.token_bytes(32)

    # HMAC-SHA-256: salt is the key, player identity is the message
    identity = f"{player_id}:{player_uuid}".encode("utf-8")
    raw_hmac = hmac_module.new(salt, identity, hashlib.sha256).digest()

    # Format as a stable, database-safe pseudonym
    hex_hash = raw_hmac.hex()[:32]
    pseudonym = f"PSEUDO_{hex_hash}"

    # Destroy salt — all references overwritten before return
    # Python does not guarantee immediate memory reclamation, but we clear
    # the local reference; for production use ZeroizeOnDrop in Rust.
    import ctypes  # noqa: PLC0415
    id_ = id(salt)
    try:
        ctypes.memset(id_, 0, len(salt))
    except Exception:  # noqa: BLE001
        pass
    del salt

    return pseudonym, None  # second value reserved for future audit token


# ---------------------------------------------------------------------------
# Deletion eligibility check
# ---------------------------------------------------------------------------
@dataclass
class ErasureEligibility:
    eligible: bool
    outcome: str
    legal_basis: str
    aml_hold_until: Optional[datetime] = None

    @property
    def partial_eligible(self) -> bool:
        """PII can be pseudonymised even if record must be retained for AML."""
        return self.outcome in ("pseudonymised", "partial_pseudonymised")


def check_erasure_eligibility(
    player: psycopg2.extras.DictRow,
    now: Optional[datetime] = None,
) -> ErasureEligibility:
    """
    Determine whether a player's PII can be erased (pseudonymised).

    Rules (in order of precedence):
    1. Self-exclusion flag: NEVER erased (regulatory requirement)
       - Source: UKGC LCCP SR Code 3.5.3; MGA Player Protection Directive Art.4
    2. AML hold: transaction skeleton retained, PII can be pseudonymised
       - Source: FATF Recommendation 11; EU 5th AMLD Art.40
    3. Eligible: full pseudonymisation of all PII fields
    """
    now = now or datetime.now(tz=timezone.utc)

    # Rule 1: Self-exclusion — PII pseudonymised but exclusion FLAG preserved
    if player["self_excluded"]:
        return ErasureEligibility(
            eligible=True,
            outcome="partial_pseudonymised",
            legal_basis=(
                "Self-exclusion flag preserved under UKGC LCCP SR Code 3.5.3 "
                "and MGA Player Protection Directive Art.4(3). "
                "PII fields pseudonymised; exclusion flag and source retained."
            ),
        )

    # Rule 2: AML hold — check if retention period has expired
    aml_hold_until = player["aml_hold_until"]
    if aml_hold_until and isinstance(aml_hold_until, datetime):
        if now < aml_hold_until.replace(tzinfo=timezone.utc) if aml_hold_until.tzinfo is None else now < aml_hold_until:
            return ErasureEligibility(
                eligible=True,
                outcome="partial_pseudonymised",
                legal_basis=(
                    f"AML records retained until {aml_hold_until.date()} "
                    "per FATF Recommendation 11 (5-year minimum) and "
                    "EU 5th Anti-Money Laundering Directive Art.40. "
                    "GDPR Art.17(3)(b) exemption applies. "
                    "PII pseudonymised; transaction skeleton retained."
                ),
                aml_hold_until=aml_hold_until,
            )

    # Rule 3: Fully eligible
    return ErasureEligibility(
        eligible=True,
        outcome="pseudonymised",
        legal_basis=(
            "GDPR Art.17(1): erasure request granted. "
            "No AML hold active. No self-exclusion flag. "
            "All PII fields pseudonymised."
        ),
    )


# ---------------------------------------------------------------------------
# Pseudonymisation executor
# ---------------------------------------------------------------------------
@dataclass
class PseudonymisationDemo:
    conn: psycopg2.extensions.connection
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

    def create_test_player(
        self,
        *,
        self_excluded: bool = False,
        aml_age_years: int = 6,
    ) -> tuple[int, str]:
        """Create a player with test PII and configurable AML hold period."""
        player_uuid = secrets.token_hex(16)
        first_txn_date = datetime.now(tz=timezone.utc) - timedelta(days=aml_age_years * 365)
        aml_hold_until = first_txn_date + timedelta(days=AML_RETENTION_YEARS * 365)

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ps_demo_players (
                    player_uuid, email, phone, full_name,
                    date_of_birth, address, kyc_document_number,
                    self_excluded, self_excluded_at, exclusion_source,
                    aml_hold_until
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) RETURNING id
                """,
                (
                    player_uuid,
                    "john.smith@realplayer.com",
                    "+44 7700 900456",
                    "John Smith",
                    "1985-06-15",
                    "42 High Street, London, W1A 1AA",
                    "GB-PASS-123456789",
                    self_excluded,
                    datetime.now(tz=timezone.utc) if self_excluded else None,
                    "player_request_gamstop" if self_excluded else None,
                    aml_hold_until,
                ),
            )
            row = cur.fetchone()
            player_id: int = row[0]

        # Insert some transactions
        txns = [
            (player_id, player_uuid, "150.00", "book-of-dead-001", False),
            (player_id, player_uuid, "5000.00", "roulette-vip-002", True),  # AML flagged
            (player_id, player_uuid, "50.00", "slots-003", False),
        ]
        with self.conn.cursor() as cur:
            for txn in txns:
                cur.execute(
                    """
                    INSERT INTO ps_demo_transactions
                        (player_id, player_uuid, amount, game_ref, aml_flagged)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    txn,
                )
        self.conn.commit()

        aml_status = "EXPIRED" if datetime.now(tz=timezone.utc) > aml_hold_until else "ACTIVE"
        excl_status = "SELF-EXCLUDED" if self_excluded else "normal"
        print(
            f"  INFO  Created player ID={player_id}, UUID={player_uuid[:8]}... "
            f"[{excl_status}] [AML hold: {aml_status} until {aml_hold_until.date()}]"
        )
        return player_id, player_uuid

    def process_erasure_request(self, player_id: int) -> ErasureEligibility:
        """Process a GDPR Art.17 erasure request."""
        request_ref = f"GDPR-{secrets.token_hex(8).upper()}"

        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM ps_demo_players WHERE id = %s", (player_id,)
            )
            player = cur.fetchone()

        if not player:
            return ErasureEligibility(
                eligible=False,
                outcome="not_found",
                legal_basis="Player not found",
            )

        eligibility = check_erasure_eligibility(player)

        # Log the GDPR request
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ps_demo_gdpr_requests
                    (player_id, request_ref, outcome, legal_basis)
                VALUES (%s, %s, %s, %s)
                """,
                (player_id, request_ref, eligibility.outcome, eligibility.legal_basis),
            )

        if eligibility.eligible:
            pseudonym, _ = generate_pseudonym(player_id, player["player_uuid"])

            pii_fields = "email, phone, full_name, date_of_birth, address, kyc_document_number"
            with self.conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE ps_demo_players
                    SET email               = %s || '@pseudonymised.invalid',
                        phone               = %s,
                        full_name           = %s,
                        date_of_birth       = NULL,
                        address             = %s,
                        kyc_document_number = %s,
                        pseudonym           = %s,
                        pseudonymised_at    = NOW(),
                        gdpr_request_ref    = %s
                    WHERE id = %s
                    """,
                    (
                        pseudonym, pseudonym, pseudonym, pseudonym, pseudonym,
                        pseudonym, request_ref, player_id,
                    ),
                )
                # Copy pseudonym to transactions for AML linkage
                cur.execute(
                    """
                    UPDATE ps_demo_transactions
                    SET pseudonym = %s
                    WHERE player_id = %s
                    """,
                    (pseudonym, player_id),
                )
                # Mark request as completed
                cur.execute(
                    """
                    UPDATE ps_demo_gdpr_requests
                    SET completed_at = NOW()
                    WHERE request_ref = %s
                    """,
                    (request_ref,),
                )
            self.conn.commit()
            print(f"  INFO  Erasure request {request_ref}: {eligibility.outcome}")
            print(f"  INFO  Legal basis: {eligibility.legal_basis[:80]}...")

        return eligibility

    def verify_pii_gone(self, player_id: int) -> dict[str, bool]:
        """Verify that all PII fields have been replaced with pseudonyms."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM ps_demo_players WHERE id = %s", (player_id,)
            )
            player = cur.fetchone()

        if not player:
            return {}

        checks: dict[str, bool] = {}
        import re  # noqa: PLC0415
        # Patterns that indicate REAL PII is present (not a pseudonym)
        pii_patterns = {
            "email": r"john\.smith@realplayer\.com",
            "phone": r"\+44\s?7700",
            "full_name": r"John Smith",
            "address": r"London|High Street",
            "kyc_document_number": r"GB-PASS",
        }

        for field_name, pattern in pii_patterns.items():
            value = player[field_name] or ""
            is_plaintext = bool(re.search(pattern, str(value), re.IGNORECASE))
            checks[field_name] = not is_plaintext  # True = real PII removed

        return checks

    def verify_transactions_intact(self, player_id: int) -> dict[str, bool]:
        """Verify transaction records and AML data survive pseudonymisation."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT count(*), sum(amount) FROM ps_demo_transactions WHERE player_id = %s",
                (player_id,),
            )
            row = cur.fetchone()
            txn_count = row[0]
            txn_total = row[1]

            # Check AML-flagged transaction is retained
            cur.execute(
                "SELECT count(*) FROM ps_demo_transactions WHERE player_id = %s AND aml_flagged = TRUE",
                (player_id,),
            )
            aml_row = cur.fetchone()
            aml_count = aml_row[0]

        return {
            "transactions_exist": txn_count > 0,
            "amounts_readable": txn_total is not None and txn_total > 0,
            "aml_records_retained": aml_count > 0,
        }

    def verify_self_exclusion_preserved(self, player_id: int) -> bool:
        """Verify that self-exclusion flags survive GDPR erasure."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT self_excluded, exclusion_source FROM ps_demo_players WHERE id = %s",
                (player_id,),
            )
            player = cur.fetchone()
        if not player:
            return False
        return bool(player["self_excluded"]) and bool(player["exclusion_source"])

    def run_scenario(
        self,
        *,
        label: str,
        self_excluded: bool = False,
        aml_age_years: int = 6,
    ) -> None:
        """Run a complete pseudonymisation scenario."""
        print(f"\n--- Scenario: {label} ---")

        player_id, _ = self.create_test_player(
            self_excluded=self_excluded,
            aml_age_years=aml_age_years,
        )

        # Before-state verification
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT email FROM ps_demo_players WHERE id = %s", (player_id,))
            row = cur.fetchone()
            has_pii_before = row and "john.smith" in (row["email"] or "")
        self._record(f"[{label}] PII present before erasure request", bool(has_pii_before))

        # Process erasure request
        eligibility = self.process_erasure_request(player_id)

        # After-state verification
        pii_checks = self.verify_pii_gone(player_id)
        all_pii_removed = all(pii_checks.values())
        self._record(
            f"[{label}] All PII fields pseudonymised: {pii_checks}",
            all_pii_removed,
        )

        # Transaction integrity
        txn_checks = self.verify_transactions_intact(player_id)
        self._record(
            f"[{label}] Transaction records intact for AML",
            txn_checks.get("transactions_exist", False),
        )
        self._record(
            f"[{label}] Transaction amounts readable (non-PII)",
            txn_checks.get("amounts_readable", False),
        )
        self._record(
            f"[{label}] AML-flagged transactions retained",
            txn_checks.get("aml_records_retained", False),
        )

        # Self-exclusion check
        if self_excluded:
            preserved = self.verify_self_exclusion_preserved(player_id)
            self._record(
                f"[{label}] Self-exclusion flag preserved (regulatory requirement)",
                preserved,
            )

        # Confirm eligibility outcome
        expected_outcome = "partial_pseudonymised" if self_excluded else (
            "partial_pseudonymised" if aml_age_years < AML_RETENTION_YEARS else "pseudonymised"
        )
        outcome_ok = eligibility.outcome == expected_outcome
        self._record(
            f"[{label}] Correct erasure outcome: expected={expected_outcome} got={eligibility.outcome}",
            outcome_ok,
        )

    def cleanup(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS ps_demo_gdpr_requests CASCADE")
            cur.execute("DROP TABLE IF EXISTS ps_demo_transactions CASCADE")
            cur.execute("DROP TABLE IF EXISTS ps_demo_players CASCADE")
        self.conn.commit()
        print("\n  INFO  Test tables cleaned up")

    def run(self) -> bool:
        print("\n=== GDPR Art.17 Pseudonymisation Demonstration ===")
        print("Compliance: GDPR Art.17/Art.4(5); FATF Rec.11; UKGC LCCP SR Code 3.5.3\n")

        self.setup_schema()

        # Scenario 1: Player with expired AML hold — full pseudonymisation eligible
        self.run_scenario(
            label="AML hold EXPIRED (6 years old account)",
            self_excluded=False,
            aml_age_years=6,
        )

        # Scenario 2: Player within AML hold period — partial pseudonymisation
        self.run_scenario(
            label="AML hold ACTIVE (2 years old account)",
            self_excluded=False,
            aml_age_years=2,
        )

        # Scenario 3: Self-excluded player — exclusion flag preserved
        self.run_scenario(
            label="Self-excluded player",
            self_excluded=True,
            aml_age_years=6,
        )

        # Print overall summary
        print("\n=== Summary ===")
        passed = sum(1 for _, ok in self.results if ok)
        total = len(self.results)
        for label, ok in self.results:
            status = "PASS" if ok else "FAIL"
            print(f"  {status}  {label}")
        print(f"\n  Total: {passed}/{total} passed")

        all_passed = all(ok for _, ok in self.results)
        if all_passed:
            print("\n  PASS  All pseudonymisation scenarios passed.")
        else:
            print("\n  FAIL  One or more pseudonymisation checks failed.")
        return all_passed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GDPR Art.17 pseudonymisation demonstration for iGaming"
    )
    parser.add_argument("--pg-host", default="localhost")
    parser.add_argument("--pg-port", type=int, default=5432)
    parser.add_argument("--pg-user", default="postgres")
    parser.add_argument("--pg-password", default=os.environ.get("PG_PASSWORD", ""))
    parser.add_argument("--pg-db", default="postgres")
    parser.add_argument("--keep-db", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

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
            break
        except psycopg2.OperationalError as exc:
            if sslmode == "prefer":
                print(f"ERROR: Cannot connect to PostgreSQL: {exc}")
                print("Hint: Set PG_PASSWORD env var or use --pg-password")
                sys.exit(1)

    demo = PseudonymisationDemo(conn=conn)
    try:
        success = demo.run()
    finally:
        if not args.keep_db:
            demo.cleanup()
        conn.close()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
