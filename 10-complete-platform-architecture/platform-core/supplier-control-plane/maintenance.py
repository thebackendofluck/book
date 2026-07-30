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
maintenance.py
--------------
Maintenance Window Manager for the Supplier Integration Control Plane.

Manages the lifecycle of scheduled and emergency maintenance windows:

  - schedule_maintenance(): Create a new window and flip supplier to MAINTENANCE
  - cancel_maintenance():   Remove a window and restore ACTIVE if no other
                            active windows remain for that supplier
  - is_in_maintenance():    Fast boolean check for routing decisions
  - get_active_maintenance(): All windows that are currently active
  - list_maintenance():     All windows for a supplier (past, present, future)

Overlap detection
-----------------
The manager prevents scheduling two overlapping windows for the same
supplier. This avoids ambiguous state when the windows end at different
times (which window's expiry should restore ACTIVE?).

An OverlappingMaintenanceError is raised if the new window overlaps any
existing window for the same supplier.

Persistence
-----------
This implementation is in-memory. In production, windows are persisted
to a relational database and read back on startup. The interface is
intentionally thin so it can be wrapped with a DB-backed implementation
without changing callers.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from models import MaintenanceWindow, SupplierStatus
from registry import SupplierRegistry, registry as default_registry

logger = logging.getLogger(__name__)


class OverlappingMaintenanceError(ValueError):
    """Raised when a new window overlaps an existing window for the same supplier."""


class MaintenanceWindowNotFoundError(KeyError):
    """Raised when a window ID is not found."""


class MaintenanceManager:
    """
    Manages scheduled maintenance windows for all suppliers.

    Parameters
    ----------
    registry: The SupplierRegistry — used to flip supplier status.
    """

    def __init__(self, registry: SupplierRegistry = default_registry) -> None:
        self._registry = registry
        # supplier_id → list of MaintenanceWindow
        self._windows: dict[str, list[MaintenanceWindow]] = {}
        # window_id → supplier_id (for fast lookup on cancel)
        self._index: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def schedule_maintenance(
        self,
        supplier_id: str,
        window: MaintenanceWindow,
    ) -> MaintenanceWindow:
        """
        Register a new maintenance window for a supplier.

        Steps:
        1. Confirm the supplier exists in the registry.
        2. Validate start < end.
        3. Check for overlapping windows (same supplier).
        4. Persist the window.
        5. If the window is currently active, flip supplier status to MAINTENANCE.

        Returns the window with its generated ID.
        Raises OverlappingMaintenanceError on conflict.
        """
        # Validate supplier exists
        self._registry.get_supplier(supplier_id)

        if window.start >= window.end:
            raise ValueError(
                f"Maintenance window start ({window.start}) must be before end ({window.end})."
            )

        with self._lock:
            existing = self._windows.get(supplier_id, [])

            # Check for overlaps with non-expired windows
            for existing_window in existing:
                if window.overlaps(existing_window):
                    raise OverlappingMaintenanceError(
                        f"New window {window.start}–{window.end} overlaps with "
                        f"existing window {existing_window.id} "
                        f"({existing_window.start}–{existing_window.end}) "
                        f"for supplier {supplier_id!r}."
                    )

            self._windows.setdefault(supplier_id, []).append(window)
            self._index[window.id] = supplier_id

        # Flip status if the window starts now or in the past (already active)
        if window.is_active():
            self._registry.update_status(supplier_id, SupplierStatus.MAINTENANCE)
            logger.info(
                "Supplier %s placed into MAINTENANCE (window %s)",
                supplier_id,
                window.id,
            )
        else:
            logger.info(
                "Scheduled future maintenance for supplier %s: %s → %s (window %s)",
                supplier_id,
                window.start.isoformat(),
                window.end.isoformat(),
                window.id,
            )

        return window

    def cancel_maintenance(self, window_id: str) -> None:
        """
        Cancel a maintenance window by ID.

        If the cancelled window was the only active window for the supplier,
        the supplier status is restored to ACTIVE.
        """
        with self._lock:
            supplier_id = self._index.get(window_id)
            if supplier_id is None:
                raise MaintenanceWindowNotFoundError(window_id)

            self._windows[supplier_id] = [
                w for w in self._windows.get(supplier_id, [])
                if w.id != window_id
            ]
            del self._index[window_id]

        # Restore ACTIVE if no more active windows exist
        remaining_active = [
            w for w in self._windows.get(supplier_id, [])
            if w.is_active()
        ]
        if not remaining_active:
            record = self._registry.get_supplier(supplier_id)
            if record.status == SupplierStatus.MAINTENANCE:
                self._registry.update_status(supplier_id, SupplierStatus.ACTIVE)
                logger.info(
                    "Supplier %s restored to ACTIVE after cancellation of window %s",
                    supplier_id,
                    window_id,
                )

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def is_in_maintenance(self, supplier_id: str, at: Optional[datetime] = None) -> bool:
        """
        Return True if the supplier has any active maintenance window at the
        given time (default: now).
        """
        windows = self._windows.get(supplier_id, [])
        now = at or datetime.now(timezone.utc)
        return any(w.is_active(at=now) for w in windows)

    def get_active_maintenance(self, at: Optional[datetime] = None) -> list[MaintenanceWindow]:
        """
        Return all maintenance windows that are currently active across
        all suppliers.
        """
        now = at or datetime.now(timezone.utc)
        active: list[MaintenanceWindow] = []
        for windows in self._windows.values():
            for w in windows:
                if w.is_active(at=now):
                    active.append(w)
        return active

    def list_maintenance(self, supplier_id: str) -> list[MaintenanceWindow]:
        """
        Return all maintenance windows for a supplier (past, present, future).
        """
        return list(self._windows.get(supplier_id, []))

    def get_window(self, window_id: str) -> MaintenanceWindow:
        """Return a specific window by ID."""
        supplier_id = self._index.get(window_id)
        if supplier_id is None:
            raise MaintenanceWindowNotFoundError(window_id)
        for w in self._windows.get(supplier_id, []):
            if w.id == window_id:
                return w
        raise MaintenanceWindowNotFoundError(window_id)

    def tick(self, at: Optional[datetime] = None) -> None:
        """
        Called periodically (e.g. every minute by a scheduler) to:
        - Activate windows whose start time has passed (set supplier MAINTENANCE)
        - Expire windows whose end time has passed (restore supplier ACTIVE)
        """
        now = at or datetime.now(timezone.utc)
        for supplier_id, windows in self._windows.items():
            has_active = any(w.is_active(at=now) for w in windows)
            record = self._registry.get_supplier(supplier_id)

            if has_active and record.status == SupplierStatus.ACTIVE:
                self._registry.update_status(supplier_id, SupplierStatus.MAINTENANCE)
                logger.info("Supplier %s entered MAINTENANCE window", supplier_id)
            elif not has_active and record.status == SupplierStatus.MAINTENANCE:
                self._registry.update_status(supplier_id, SupplierStatus.ACTIVE)
                logger.info("Supplier %s exited MAINTENANCE window → ACTIVE", supplier_id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

maintenance_manager = MaintenanceManager()
