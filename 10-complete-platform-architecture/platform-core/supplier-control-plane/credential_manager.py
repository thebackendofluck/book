# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
credential_manager.py
---------------------
Credential Manager for the Supplier Integration Control Plane.

Credentials are issued per-brand, per-jurisdiction. A single supplier
integration with three brands operating in two jurisdictions each would
have up to six independent credential sets.

Rotation
--------
rotate_credentials() generates a new api_key and api_secret for the
given (supplier, brand) combination across all jurisdictions. In
production this would:

  1. Call the supplier's key-rotation API endpoint.
  2. Store the new credentials in AWS Secrets Manager / Vault.
  3. Update the in-memory registry.
  4. Schedule revocation of the old credentials after a grace period.

The stub implementation here generates a deterministic fake key for
testability and records the rotation timestamp.

Secret storage
--------------
Production deployments must NEVER store raw credentials in application
memory beyond what is needed for a single request. The SecretBackend
protocol (see below) abstracts the storage layer so the rest of the
control plane stays backend-agnostic.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import string
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from models import Credentials, SupplierRecord, SupplierStatus
from registry import SupplierRegistry, registry as default_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Secret backend protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SecretBackend(Protocol):
    """
    Protocol for a secret storage backend.

    Implementations might wrap AWS Secrets Manager, HashiCorp Vault,
    Azure Key Vault, or a local .env file for development.
    """

    def read(self, path: str) -> Optional[dict[str, str]]:
        """Return the secret at path, or None if not found."""
        ...

    def write(self, path: str, value: dict[str, str]) -> None:
        """Persist a secret at path."""
        ...

    def delete(self, path: str) -> None:
        """Delete a secret at path."""
        ...


class InMemorySecretBackend:
    """
    Development-only in-memory secret backend.

    Not suitable for production — secrets are lost on process restart
    and are visible in heap dumps.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}

    def read(self, path: str) -> Optional[dict[str, str]]:
        return self._store.get(path)

    def write(self, path: str, value: dict[str, str]) -> None:
        self._store[path] = value

    def delete(self, path: str) -> None:
        self._store.pop(path, None)


# ---------------------------------------------------------------------------
# Credential manager
# ---------------------------------------------------------------------------


class CredentialManager:
    """
    Retrieves and rotates per-brand, per-jurisdiction credentials.

    Parameters
    ----------
    registry: SupplierRegistry — source of SupplierRecord objects.
    backend:  SecretBackend — where raw secrets are persisted.
    """

    _KEY_LENGTH = 40        # characters
    _SECRET_LENGTH = 64     # characters
    _ALPHABET = string.ascii_letters + string.digits

    def __init__(
        self,
        registry: SupplierRegistry = default_registry,
        backend: Optional[SecretBackend] = None,
    ) -> None:
        self._registry = registry
        self._backend: SecretBackend = backend or InMemorySecretBackend()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _secret_path(supplier_id: str, brand_id: str, jurisdiction: str) -> str:
        return f"suppliers/{supplier_id}/brands/{brand_id}/jurisdictions/{jurisdiction}"

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def get_credentials(
        self,
        supplier_id: str,
        brand_id: str,
        jurisdiction: str,
    ) -> Credentials:
        """
        Return credentials for a (supplier, brand, jurisdiction) tuple.

        Lookup order:
        1. In-memory SupplierRecord (populated at startup from the secret backend)
        2. Secret backend (for credentials added after startup)

        Raises ValueError if no credentials are found.
        """
        # Try registry first (fast path)
        record = self._registry.get_supplier(supplier_id)
        creds = record.get_credentials(brand_id, jurisdiction)
        if creds is not None:
            return creds

        # Fall back to secret backend
        path = self._secret_path(supplier_id, brand_id, jurisdiction)
        raw = self._backend.read(path)
        if raw is None:
            raise ValueError(
                f"No credentials found for supplier={supplier_id!r} "
                f"brand={brand_id!r} jurisdiction={jurisdiction!r}. "
                "Ensure credentials are provisioned before going live."
            )

        return Credentials(
            supplier_id=supplier_id,
            brand_id=brand_id,
            jurisdiction=jurisdiction,
            api_key=raw["api_key"],
            api_secret=raw["api_secret"],
            operator_id=raw.get("operator_id", ""),
            extra={k: v for k, v in raw.items()
                   if k not in ("api_key", "api_secret", "operator_id")},
        )

    def add_credentials(self, creds: Credentials) -> Credentials:
        """
        Store new credentials for a (supplier, brand, jurisdiction) tuple.

        This persists to the secret backend and also updates the in-memory
        SupplierRecord so subsequent get_credentials() calls don't need a
        backend round-trip.
        """
        record = self._registry.get_supplier(creds.supplier_id)

        # Write to backend
        path = self._secret_path(creds.supplier_id, creds.brand_id, creds.jurisdiction)
        self._backend.write(path, {
            "api_key": creds.api_key,
            "api_secret": creds.api_secret,
            "operator_id": creds.operator_id,
            **creds.extra,
        })

        # Update in-memory record
        brand_creds = record.credentials_per_brand.setdefault(creds.brand_id, [])
        # Replace existing creds for same jurisdiction, or append
        for i, existing in enumerate(brand_creds):
            if existing.jurisdiction == creds.jurisdiction:
                brand_creds[i] = creds
                return creds
        brand_creds.append(creds)

        logger.info(
            "Credentials added: supplier=%s brand=%s jurisdiction=%s",
            creds.supplier_id,
            creds.brand_id,
            creds.jurisdiction,
        )
        return creds

    def rotate_credentials(
        self,
        supplier_id: str,
        brand_id: str,
        jurisdiction: Optional[str] = None,
    ) -> list[Credentials]:
        """
        Rotate credentials for a (supplier, brand) combination.

        If jurisdiction is specified, only that credential set is rotated.
        Otherwise, all jurisdictions for the brand are rotated.

        Returns the list of newly-generated Credentials objects.
        """
        record = self._registry.get_supplier(supplier_id)
        brand_creds = record.credentials_per_brand.get(brand_id, [])

        if not brand_creds:
            raise ValueError(
                f"No credentials found for supplier={supplier_id!r} brand={brand_id!r}."
            )

        targets = [
            c for c in brand_creds
            if jurisdiction is None or c.jurisdiction == jurisdiction
        ]
        if not targets:
            raise ValueError(
                f"No credentials found for supplier={supplier_id!r} "
                f"brand={brand_id!r} jurisdiction={jurisdiction!r}."
            )

        rotated: list[Credentials] = []
        now = datetime.now(timezone.utc)

        for old_creds in targets:
            new_key = self._generate_key()
            new_secret = self._generate_secret()

            new_creds = Credentials(
                supplier_id=supplier_id,
                brand_id=brand_id,
                jurisdiction=old_creds.jurisdiction,
                api_key=new_key,
                api_secret=new_secret,
                operator_id=old_creds.operator_id,
                extra=dict(old_creds.extra),
                rotated_at=now,
            )

            self.add_credentials(new_creds)
            rotated.append(new_creds)

            logger.info(
                "Rotated credentials: supplier=%s brand=%s jurisdiction=%s key=...%s",
                supplier_id,
                brand_id,
                old_creds.jurisdiction,
                new_key[-4:],
            )

        return rotated

    def list_brands_for_supplier(self, supplier_id: str) -> list[str]:
        """Return all brand IDs that have credentials for this supplier."""
        record = self._registry.get_supplier(supplier_id)
        return list(record.credentials_per_brand.keys())

    def has_credentials(
        self,
        supplier_id: str,
        brand_id: str,
        jurisdiction: str,
    ) -> bool:
        """Return True if credentials exist for the given triple."""
        try:
            self.get_credentials(supplier_id, brand_id, jurisdiction)
            return True
        except (ValueError, KeyError):
            return False

    # ------------------------------------------------------------------
    # Key generation
    # ------------------------------------------------------------------

    def _generate_key(self) -> str:
        return "".join(
            secrets.choice(self._ALPHABET) for _ in range(self._KEY_LENGTH)
        )

    def _generate_secret(self) -> str:
        return secrets.token_hex(self._SECRET_LENGTH // 2)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

credential_manager = CredentialManager()
