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
registry.py
-----------
Supplier Registry for the Supplier Integration Control Plane.

The registry is the authoritative source of truth for all configured
supplier integrations. It stores SupplierRecords in memory (with an
optional persistence hook) and exposes CRUD operations used by the
control plane API.

Thread safety
-------------
The registry uses a threading.Lock around all mutations so it is safe
to use from concurrent FastAPI request handlers without an async lock.
Read operations do not acquire the lock because Python's GIL protects
simple dict reads; this trade-off is acceptable for the expected read/
write ratio.

Filtering
---------
list_suppliers() accepts a flat filters dict. Recognised filter keys:

    type        — SupplierType value
    status      — SupplierStatus value
    jurisdiction — jurisdiction slug; matched against capability matrix
    currency     — ISO-4217 code; matched against capability matrix
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from models import (
    Credentials,
    SupplierCapabilityMatrix,
    SupplierRecord,
    SupplierStatus,
    SupplierType,
)

logger = logging.getLogger(__name__)


class SupplierNotFoundError(KeyError):
    """Raised when a supplier ID is not present in the registry."""


class DuplicateSupplierError(ValueError):
    """Raised when trying to register an ID that already exists."""


class SupplierRegistry:
    """
    Thread-safe in-memory registry of all configured supplier integrations.

    Usage::

        registry = SupplierRegistry()
        record = registry.register_supplier(SupplierRecord(id="evolution", ...))
        record = registry.get_supplier("evolution")
        registry.update_status("evolution", SupplierStatus.MAINTENANCE)
    """

    def __init__(self) -> None:
        self._store: dict[str, SupplierRecord] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def register_supplier(self, record: SupplierRecord) -> SupplierRecord:
        """
        Add a new supplier to the registry.

        Raises DuplicateSupplierError if the ID is already registered.
        Use update_status() or replace_supplier() for modifications.
        """
        with self._lock:
            if record.id in self._store:
                raise DuplicateSupplierError(
                    f"Supplier {record.id!r} is already registered. "
                    "Use replace_supplier() to overwrite."
                )
            self._store[record.id] = record
            logger.info(
                "Registered supplier id=%s name=%r type=%s status=%s",
                record.id,
                record.name,
                record.type.value,
                record.status.value,
            )
            return record

    def replace_supplier(self, record: SupplierRecord) -> SupplierRecord:
        """Upsert — register or replace an existing supplier record."""
        with self._lock:
            existed = record.id in self._store
            self._store[record.id] = record
            action = "Replaced" if existed else "Registered"
            logger.info("%s supplier id=%s", action, record.id)
            return record

    def update_status(self, supplier_id: str, status: SupplierStatus) -> SupplierRecord:
        """
        Change the operational status of a registered supplier.

        Valid transitions
        -----------------
        ACTIVE     → MAINTENANCE, DEGRADED, DISABLED
        MAINTENANCE → ACTIVE, DISABLED
        DEGRADED   → ACTIVE, DISABLED, MAINTENANCE
        DISABLED   → ACTIVE

        All transitions are allowed here; enforcement of stricter state
        machines belongs in the service layer.

        Raises SupplierNotFoundError if the supplier is not registered.
        """
        with self._lock:
            record = self._store.get(supplier_id)
            if record is None:
                raise SupplierNotFoundError(supplier_id)
            from datetime import datetime, timezone
            record.status = status
            record.updated_at = datetime.now(timezone.utc)
            logger.info("Supplier %s status → %s", supplier_id, status.value)
            return record

    def deregister_supplier(self, supplier_id: str) -> None:
        """Remove a supplier from the registry (used in tests and decommissions)."""
        with self._lock:
            self._store.pop(supplier_id, None)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_supplier(self, supplier_id: str) -> SupplierRecord:
        """
        Return the SupplierRecord for the given ID.

        Raises SupplierNotFoundError if not registered.
        """
        record = self._store.get(supplier_id)
        if record is None:
            raise SupplierNotFoundError(supplier_id)
        return record

    def list_suppliers(self, filters: Optional[dict[str, Any]] = None) -> list[SupplierRecord]:
        """
        Return all registered suppliers, optionally filtered.

        Supported filter keys
        ---------------------
        type         — SupplierType or its string value
        status       — SupplierStatus or its string value
        jurisdiction — Jurisdiction slug matched against capability matrix
        currency     — ISO-4217 code matched against capability matrix
        """
        results = list(self._store.values())

        if not filters:
            return results

        if "type" in filters:
            wanted = filters["type"]
            if isinstance(wanted, str):
                wanted = SupplierType(wanted)
            results = [r for r in results if r.type == wanted]

        if "status" in filters:
            wanted = filters["status"]
            if isinstance(wanted, str):
                wanted = SupplierStatus(wanted)
            results = [r for r in results if r.status == wanted]

        if "jurisdiction" in filters:
            jur = filters["jurisdiction"]
            results = [
                r for r in results
                if r.capabilities and r.capabilities.supports_jurisdiction(jur)
            ]

        if "currency" in filters:
            cur = filters["currency"]
            results = [
                r for r in results
                if r.capabilities and r.capabilities.supports_currency(cur)
            ]

        return results

    def get_capability_matrix(self, supplier_id: str) -> SupplierCapabilityMatrix:
        """
        Return the SupplierCapabilityMatrix for the given supplier.

        Raises SupplierNotFoundError if supplier is unknown.
        Raises ValueError if the supplier has no capability matrix configured.
        """
        record = self.get_supplier(supplier_id)
        if record.capabilities is None:
            raise ValueError(
                f"Supplier {supplier_id!r} has no capability matrix configured."
            )
        return record.capabilities

    # ------------------------------------------------------------------
    # Credential helpers (delegated to SupplierRecord)
    # ------------------------------------------------------------------

    def get_credentials(
        self,
        supplier_id: str,
        brand_id: str,
        jurisdiction: str,
    ) -> Optional[Credentials]:
        """
        Convenience accessor: return credentials for brand + jurisdiction.

        Returns None if no matching credential is found.
        """
        record = self.get_supplier(supplier_id)
        return record.get_credentials(brand_id, jurisdiction)

    # ------------------------------------------------------------------
    # Container protocol
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, supplier_id: str) -> bool:
        return supplier_id in self._store


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

registry = SupplierRegistry()
