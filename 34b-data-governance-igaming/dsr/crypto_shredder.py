# Companion code for "The Backend of Luck" - Chapter 34b, Data Governance for iGaming Platforms.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Crypto Shredder — Vault DEK Destruction for Backup-Safe Erasure
================================================================
Connects to HashiCorp Vault transit engine, destroys the player-specific
Data Encryption Key (DEK), verifies destruction, and logs to the audit trail.

This pattern implements "cryptographic erasure" (also called "crypto-shredding"):
all player data encrypted under the DEK becomes permanently unreadable even if
backup snapshots or offline copies exist, satisfying GDPR Art. 17 erasure without
requiring coordinated deletion from every backup tier.

Regulatory context:
  - GDPR Art. 17: Right to erasure — cryptographic erasure accepted by EDPB
    (Guidelines 05/2020 on consent, Section 7.3 / ENISA Pseudonymisation Techniques)
  - GDPR Art. 32: Security of processing — AES-256 transit encryption at rest
  - LGPD Art. 16: Termination of processing — destruction of encryption key satisfies
    the obligation when data cannot be accessed post-destruction
  - PCI-DSS Req. 3.5: Protect keys used to secure cardholder data

Architecture notes:
  - Each player has one DEK stored as a named key in Vault transit engine:
    Path: transit/keys/player-{player_id}
  - Ciphertext stored in DB/S3 is NEVER decryptable without the DEK
  - Key destruction is irreversible — no key versions are retained
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from unittest.mock import MagicMock

try:
    import hvac  # type: ignore[import-untyped]
    HVAC_AVAILABLE = True
except ImportError:
    hvac = MagicMock()  # type: ignore[assignment]
    HVAC_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("crypto_shredder")


class ShredStatus(str, Enum):
    SUCCESS          = "SUCCESS"
    ALREADY_ABSENT   = "ALREADY_ABSENT"   # Key was never created or already destroyed
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    ERROR            = "ERROR"


@dataclass
class VaultConfig:
    addr: str
    token: str
    mount_point: str = "transit"
    namespace: Optional[str] = None     # Vault Enterprise namespaces


@dataclass
class ShredResult:
    player_id: str
    vault_key_path: str
    status: ShredStatus
    shredded_at: str
    audit_event_id: str
    error: Optional[str] = None
    verified_absent: bool = False


class VaultTransitClient:
    """
    Thin wrapper around hvac for transit key lifecycle operations.

    Production usage:
        client = VaultTransitClient(VaultConfig(addr="https://vault.internal:8200", token="..."))
        result = client.destroy_key("player-uuid-1234")
    """

    def __init__(self, config: VaultConfig) -> None:
        self.config = config
        self._client: Any
        if HVAC_AVAILABLE:
            self._client = hvac.Client(
                url=config.addr,
                token=config.token,
                namespace=config.namespace,
            )
        else:
            logger.warning("hvac not installed — running in stub mode")
            self._client = None

    def _key_name(self, player_id: str) -> str:
        """Vault transit key name for a player. Prefix avoids key name collisions."""
        return f"player-{player_id}"

    def key_exists(self, player_id: str) -> bool:
        """Return True if the transit key exists in Vault."""
        if self._client is None:
            return True  # Stub: pretend key exists
        try:
            self._client.secrets.transit.read_key(
                name=self._key_name(player_id),
                mount_point=self.config.mount_point,
            )
            return True
        except Exception:
            return False

    def allow_deletion(self, player_id: str) -> None:
        """
        Set deletion_allowed=true on the key.
        Vault requires this flag before a key can be deleted — acts as a
        two-step safety interlock (analogous to S3 MFA Delete).
        """
        if self._client is None:
            return
        self._client.secrets.transit.update_key_configuration(
            name=self._key_name(player_id),
            mount_point=self.config.mount_point,
            deletion_allowed=True,
        )

    def delete_key(self, player_id: str) -> None:
        """Destroy the transit key. All versions are irrevocably deleted."""
        if self._client is None:
            logger.info("[STUB] Deleting Vault key for player %s", player_id)
            return
        self._client.secrets.transit.delete_key(
            name=self._key_name(player_id),
            mount_point=self.config.mount_point,
        )

    def verify_absent(self, player_id: str) -> bool:
        """Confirm the key no longer exists. Returns True if confirmed absent."""
        return not self.key_exists(player_id)


def _write_audit_log(result: ShredResult) -> None:
    """
    Persist the shred result to the audit trail.

    Production implementation inserts into the immutable audit_events table
    (append-only, WORM-compliant for GDPR Art. 5(2) accountability principle).
    """
    logger.info(
        "AUDIT: player=%s key=%s status=%s at=%s verified=%s",
        result.player_id,
        result.vault_key_path,
        result.status.value,
        result.shredded_at,
        result.verified_absent,
    )


def shred_player_key(
    player_id: str,
    vault_config: VaultConfig,
    dry_run: bool = False,
) -> ShredResult:
    """
    Destroy the Vault DEK for a player and verify the destruction.

    Steps:
      1. Check key existence (idempotent — no error if already absent)
      2. Set deletion_allowed flag (Vault safety interlock)
      3. Delete the key (all versions destroyed)
      4. Verify the key is absent (read back)
      5. Log to audit trail

    Parameters
    ----------
    player_id:    Platform player identifier
    vault_config: Vault connection parameters
    dry_run:      If True, log intent without performing actual destruction

    Returns
    -------
    ShredResult with status and verification flag
    """
    import uuid as _uuid
    audit_id = str(_uuid.uuid4())
    client = VaultTransitClient(vault_config)
    key_path = f"{vault_config.mount_point}/keys/player-{player_id}"
    now = datetime.now(timezone.utc).isoformat()

    logger.info("Initiating crypto-shred for player %s (key: %s)", player_id, key_path)

    # Step 1 — Check existence
    if not client.key_exists(player_id):
        logger.info("Key for player %s already absent — idempotent success", player_id)
        result = ShredResult(
            player_id=player_id, vault_key_path=key_path,
            status=ShredStatus.ALREADY_ABSENT,
            shredded_at=now, audit_event_id=audit_id, verified_absent=True,
        )
        _write_audit_log(result)
        return result

    if dry_run:
        logger.info("[DRY-RUN] Would destroy Vault key %s", key_path)
        result = ShredResult(
            player_id=player_id, vault_key_path=key_path,
            status=ShredStatus.SUCCESS,
            shredded_at=now, audit_event_id=audit_id, verified_absent=False,
            error="dry-run — key not actually destroyed",
        )
        _write_audit_log(result)
        return result

    # Steps 2–3 — Enable deletion and destroy
    try:
        client.allow_deletion(player_id)
        client.delete_key(player_id)
        logger.info("Vault key destroyed for player %s", player_id)
    except Exception as exc:
        logger.error("Failed to destroy Vault key for player %s: %s", player_id, exc)
        result = ShredResult(
            player_id=player_id, vault_key_path=key_path,
            status=ShredStatus.ERROR,
            shredded_at=now, audit_event_id=audit_id, error=str(exc),
        )
        _write_audit_log(result)
        return result

    # Step 4 — Verification
    verified = client.verify_absent(player_id)
    if not verified:
        logger.error("Verification FAILED — key still present after deletion for player %s", player_id)
        result = ShredResult(
            player_id=player_id, vault_key_path=key_path,
            status=ShredStatus.VERIFICATION_FAILED,
            shredded_at=now, audit_event_id=audit_id, verified_absent=False,
        )
        _write_audit_log(result)
        return result

    logger.info("Verification SUCCESS — key absent for player %s", player_id)
    result = ShredResult(
        player_id=player_id, vault_key_path=key_path,
        status=ShredStatus.SUCCESS,
        shredded_at=now, audit_event_id=audit_id, verified_absent=True,
    )
    _write_audit_log(result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Destroy a player's Vault DEK (crypto-shredding) for GDPR Art. 17 compliance."
    )
    parser.add_argument("--player-id",   required=True,                    help="Player UUID")
    parser.add_argument("--vault-addr",  default="http://127.0.0.1:8200",  help="Vault address")
    parser.add_argument("--vault-token", default="root",                   help="Vault token (or use VAULT_TOKEN env)")
    parser.add_argument("--mount",       default="transit",                help="Transit mount point")
    parser.add_argument("--dry-run",     action="store_true",              help="Simulate without destroying the key")
    parser.add_argument("--output",      default="-",                      help="Output JSON (default: stdout)")
    args = parser.parse_args()

    import os
    token = os.environ.get("VAULT_TOKEN", args.vault_token)
    config = VaultConfig(addr=args.vault_addr, token=token, mount_point=args.mount)

    shred_result = shred_player_key(args.player_id, config, dry_run=args.dry_run)
    payload = json.dumps(asdict(shred_result), indent=2, default=str)

    if args.output == "-":
        sys.stdout.write(payload + "\n")
    else:
        with open(args.output, "w") as fh:
            fh.write(payload + "\n")
        logger.info("Result written to %s", args.output)
