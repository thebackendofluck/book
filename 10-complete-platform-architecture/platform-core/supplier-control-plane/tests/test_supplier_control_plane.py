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
tests/test_supplier_control_plane.py
-------------------------------------
Test suite for the Supplier Integration Control Plane.

Covers:
  - SupplierRegistry CRUD and filtering
  - HealthMonitor probe logic and degradation detection
  - MaintenanceManager scheduling, overlap detection, tick()
  - CredentialManager get/add/rotate operations
  - FastAPI endpoints via TestClient

Test isolation: each test builds fresh instances of registry, monitor,
maintenance manager, and credential manager rather than relying on module
singletons. This prevents cross-test state leakage.
"""

from __future__ import annotations

import sys
import os
import pytest
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Path setup — tests run from tests/ or from supplier-control-plane/
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(__file__)
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from models import (
    CallbackPolicy,
    Credentials,
    HealthStatus,
    MaintenanceWindow,
    SupplierCapabilityMatrix,
    SupplierHealth,
    SupplierRecord,
    SupplierStatus,
    SupplierType,
    WalletModel,
)
from registry import (
    DuplicateSupplierError,
    SupplierNotFoundError,
    SupplierRegistry,
)
from health_monitor import HealthMonitor, LATENCY_THRESHOLD_MS, ERROR_RATE_THRESHOLD
from maintenance import (
    MaintenanceManager,
    MaintenanceWindowNotFoundError,
    OverlappingMaintenanceError,
)
from credential_manager import CredentialManager, InMemorySecretBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_registry() -> SupplierRegistry:
    return SupplierRegistry()


def _make_record(
    supplier_id: str = "evolution",
    status: SupplierStatus = SupplierStatus.ACTIVE,
    add_capabilities: bool = True,
) -> SupplierRecord:
    cap = None
    if add_capabilities:
        cap = SupplierCapabilityMatrix(
            supplier_id=supplier_id,
            games={"blackjack", "roulette"},
            currencies={"EUR", "GBP", "USD"},
            jurisdictions={"GB", "MT"},
            wallet_model=WalletModel.SEAMLESS,
            rtp_certified=True,
        )
    return SupplierRecord(
        id=supplier_id,
        name=f"{supplier_id.title()} Gaming",
        type=SupplierType.CASINO,
        status=status,
        capabilities=cap,
    )


def _make_healthy_probe(latency_ms: float = 100.0, error_rate: float = 0.0):
    def _probe(supplier_id: str):
        return latency_ms, error_rate, "OK"
    return _probe


def _make_degraded_probe(latency_ms: float = 3000.0, error_rate: float = 0.10):
    def _probe(supplier_id: str):
        return latency_ms, error_rate, "slow"
    return _probe


def _make_unreachable_probe():
    def _probe(supplier_id: str):
        return 0.0, 1.0, "Connection refused"
    return _probe


# ===========================================================================
# 1. Models
# ===========================================================================


class TestModels:
    def test_supplier_record_is_available_when_active(self):
        record = _make_record(status=SupplierStatus.ACTIVE)
        assert record.is_available() is True

    def test_supplier_record_not_available_when_disabled(self):
        record = _make_record(status=SupplierStatus.DISABLED)
        assert record.is_available() is False

    def test_credentials_masked_key(self):
        creds = Credentials(
            supplier_id="evo",
            brand_id="brand1",
            jurisdiction="GB",
            api_key="ABCDEFGHIJ1234",
            api_secret="secret",
            operator_id="op1",
        )
        assert creds.masked_key().endswith("1234")
        assert creds.masked_key().startswith("**")

    def test_maintenance_window_is_active(self):
        now = datetime.now(timezone.utc)
        window = MaintenanceWindow(
            supplier_id="evo",
            start=now - timedelta(minutes=5),
            end=now + timedelta(minutes=30),
            reason="test",
        )
        assert window.is_active() is True

    def test_maintenance_window_not_active_future(self):
        now = datetime.now(timezone.utc)
        window = MaintenanceWindow(
            supplier_id="evo",
            start=now + timedelta(hours=2),
            end=now + timedelta(hours=4),
            reason="test",
        )
        assert window.is_active() is False

    def test_maintenance_window_overlaps(self):
        now = datetime.now(timezone.utc)
        w1 = MaintenanceWindow(
            supplier_id="evo",
            start=now,
            end=now + timedelta(hours=2),
            reason="a",
        )
        w2 = MaintenanceWindow(
            supplier_id="evo",
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=3),
            reason="b",
        )
        assert w1.overlaps(w2) is True

    def test_maintenance_window_no_overlap(self):
        now = datetime.now(timezone.utc)
        w1 = MaintenanceWindow(
            supplier_id="evo",
            start=now,
            end=now + timedelta(hours=1),
            reason="a",
        )
        w2 = MaintenanceWindow(
            supplier_id="evo",
            start=now + timedelta(hours=2),
            end=now + timedelta(hours=3),
            reason="b",
        )
        assert w1.overlaps(w2) is False

    def test_supplier_health_is_degraded_high_error_rate(self):
        h = SupplierHealth(
            supplier_id="evo",
            last_check=datetime.now(timezone.utc),
            latency_ms=100.0,
            error_rate=0.10,
        )
        assert h.is_degraded() is True

    def test_supplier_health_is_degraded_high_latency(self):
        h = SupplierHealth(
            supplier_id="evo",
            last_check=datetime.now(timezone.utc),
            latency_ms=3000.0,
            error_rate=0.0,
        )
        assert h.is_degraded() is True

    def test_callback_policy_effective_timeout(self):
        policy = CallbackPolicy(supplier_id="evo", timeout_ms=3500)
        assert policy.effective_timeout_s() == pytest.approx(3.5)


# ===========================================================================
# 2. Registry
# ===========================================================================


class TestSupplierRegistry:
    def test_register_and_get_supplier(self):
        reg = _make_registry()
        record = _make_record()
        reg.register_supplier(record)
        retrieved = reg.get_supplier("evolution")
        assert retrieved.id == "evolution"

    def test_duplicate_registration_raises(self):
        reg = _make_registry()
        reg.register_supplier(_make_record())
        with pytest.raises(DuplicateSupplierError):
            reg.register_supplier(_make_record())

    def test_replace_supplier_upserts(self):
        reg = _make_registry()
        reg.replace_supplier(_make_record())
        updated = _make_record()
        updated.name = "Evolution v2"
        reg.replace_supplier(updated)
        assert reg.get_supplier("evolution").name == "Evolution v2"

    def test_get_nonexistent_supplier_raises(self):
        reg = _make_registry()
        with pytest.raises(SupplierNotFoundError):
            reg.get_supplier("ghost")

    def test_update_status(self):
        reg = _make_registry()
        reg.register_supplier(_make_record())
        reg.update_status("evolution", SupplierStatus.MAINTENANCE)
        assert reg.get_supplier("evolution").status == SupplierStatus.MAINTENANCE

    def test_list_suppliers_empty(self):
        reg = _make_registry()
        assert reg.list_suppliers() == []

    def test_list_suppliers_filter_by_type(self):
        reg = _make_registry()
        reg.register_supplier(_make_record("evo"))
        sports = _make_record("kambi")
        sports.type = SupplierType.SPORTS_BOOK
        reg.register_supplier(sports)
        results = reg.list_suppliers({"type": "CASINO"})
        assert all(r.type == SupplierType.CASINO for r in results)
        assert len(results) == 1

    def test_list_suppliers_filter_by_status(self):
        reg = _make_registry()
        reg.register_supplier(_make_record("evo", SupplierStatus.ACTIVE))
        rec2 = _make_record("pragmatic", SupplierStatus.DISABLED)
        reg.register_supplier(rec2)
        active = reg.list_suppliers({"status": "ACTIVE"})
        assert len(active) == 1 and active[0].id == "evo"

    def test_get_capability_matrix(self):
        reg = _make_registry()
        reg.register_supplier(_make_record())
        matrix = reg.get_capability_matrix("evolution")
        assert "EUR" in matrix.currencies

    def test_get_capability_matrix_no_capabilities_raises(self):
        reg = _make_registry()
        reg.register_supplier(_make_record(add_capabilities=False))
        with pytest.raises(ValueError):
            reg.get_capability_matrix("evolution")

    def test_len_and_contains(self):
        reg = _make_registry()
        assert len(reg) == 0
        reg.register_supplier(_make_record())
        assert len(reg) == 1
        assert "evolution" in reg


# ===========================================================================
# 3. Health Monitor
# ===========================================================================


class TestHealthMonitor:
    def _setup(self, probe_fn=None, status=SupplierStatus.ACTIVE):
        reg = _make_registry()
        reg.register_supplier(_make_record(status=status))
        mon = HealthMonitor(registry=reg, probe_fn=probe_fn or _make_healthy_probe())
        return reg, mon

    def test_healthy_supplier_returns_healthy_status(self):
        _, mon = self._setup(_make_healthy_probe())
        health = mon.check_supplier_health("evolution")
        assert health.status == HealthStatus.HEALTHY

    def test_high_latency_triggers_degraded(self):
        _, mon = self._setup(_make_degraded_probe(latency_ms=3000.0, error_rate=0.0))
        health = mon.check_supplier_health("evolution")
        assert health.status == HealthStatus.DEGRADED

    def test_high_error_rate_triggers_degraded(self):
        _, mon = self._setup(_make_degraded_probe(latency_ms=100.0, error_rate=0.10))
        health = mon.check_supplier_health("evolution")
        assert health.status == HealthStatus.DEGRADED

    def test_unreachable_supplier_status(self):
        _, mon = self._setup(_make_unreachable_probe())
        health = mon.check_supplier_health("evolution")
        assert health.status == HealthStatus.UNREACHABLE

    def test_check_all_suppliers(self):
        reg = _make_registry()
        reg.register_supplier(_make_record("evo"))
        reg.register_supplier(_make_record("pragmatic"))
        mon = HealthMonitor(registry=reg, probe_fn=_make_healthy_probe())
        results = mon.check_all_suppliers()
        assert set(results.keys()) == {"evo", "pragmatic"}

    def test_disabled_supplier_skipped_in_check_all(self):
        reg = _make_registry()
        reg.register_supplier(_make_record("evo", SupplierStatus.ACTIVE))
        reg.register_supplier(_make_record("pragmatic", SupplierStatus.DISABLED))
        mon = HealthMonitor(registry=reg, probe_fn=_make_healthy_probe())
        results = mon.check_all_suppliers()
        assert "pragmatic" not in results

    def test_detect_degraded_suppliers(self):
        reg = _make_registry()
        reg.register_supplier(_make_record("evo"))
        reg.register_supplier(_make_record("pragmatic"))
        # evo healthy, pragmatic degraded
        call_count = {"n": 0}
        def mixed_probe(supplier_id: str):
            if supplier_id == "pragmatic":
                return 3000.0, 0.10, "slow"
            return 100.0, 0.0, "OK"
        mon = HealthMonitor(registry=reg, probe_fn=mixed_probe)
        mon.check_all_suppliers()
        degraded = mon.detect_degraded_suppliers()
        assert any(h.supplier_id == "pragmatic" for h in degraded)

    def test_degraded_supplier_status_updated_in_registry(self):
        reg, mon = self._setup(_make_degraded_probe())
        mon.check_supplier_health("evolution")
        assert reg.get_supplier("evolution").status == SupplierStatus.DEGRADED

    def test_recovery_restores_active_status(self):
        reg, mon = self._setup(_make_degraded_probe())
        mon.check_supplier_health("evolution")
        assert reg.get_supplier("evolution").status == SupplierStatus.DEGRADED
        # now recover
        mon._probe_fn = _make_healthy_probe()
        mon.check_supplier_health("evolution")
        assert reg.get_supplier("evolution").status == SupplierStatus.ACTIVE


# ===========================================================================
# 4. Maintenance Manager
# ===========================================================================


class TestMaintenanceManager:
    def _setup(self):
        reg = _make_registry()
        reg.register_supplier(_make_record())
        mgr = MaintenanceManager(registry=reg)
        return reg, mgr

    def _future_window(self, supplier_id="evolution", offset_hours=2, duration_hours=2):
        now = datetime.now(timezone.utc)
        return MaintenanceWindow(
            supplier_id=supplier_id,
            start=now + timedelta(hours=offset_hours),
            end=now + timedelta(hours=offset_hours + duration_hours),
            reason="planned",
        )

    def _active_window(self, supplier_id="evolution"):
        now = datetime.now(timezone.utc)
        return MaintenanceWindow(
            supplier_id=supplier_id,
            start=now - timedelta(minutes=5),
            end=now + timedelta(hours=2),
            reason="active",
        )

    def test_schedule_future_window(self):
        _, mgr = self._setup()
        w = self._future_window()
        scheduled = mgr.schedule_maintenance("evolution", w)
        assert scheduled.id is not None

    def test_active_window_flips_status_to_maintenance(self):
        reg, mgr = self._setup()
        mgr.schedule_maintenance("evolution", self._active_window())
        assert reg.get_supplier("evolution").status == SupplierStatus.MAINTENANCE

    def test_is_in_maintenance_true(self):
        _, mgr = self._setup()
        mgr.schedule_maintenance("evolution", self._active_window())
        assert mgr.is_in_maintenance("evolution") is True

    def test_is_in_maintenance_false_no_window(self):
        _, mgr = self._setup()
        assert mgr.is_in_maintenance("evolution") is False

    def test_overlapping_windows_raise(self):
        _, mgr = self._setup()
        now = datetime.now(timezone.utc)
        w1 = MaintenanceWindow(
            supplier_id="evolution",
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=3),
            reason="a",
        )
        w2 = MaintenanceWindow(
            supplier_id="evolution",
            start=now + timedelta(hours=2),
            end=now + timedelta(hours=4),
            reason="b",
        )
        mgr.schedule_maintenance("evolution", w1)
        with pytest.raises(OverlappingMaintenanceError):
            mgr.schedule_maintenance("evolution", w2)

    def test_cancel_active_window_restores_active(self):
        reg, mgr = self._setup()
        w = self._active_window()
        mgr.schedule_maintenance("evolution", w)
        assert reg.get_supplier("evolution").status == SupplierStatus.MAINTENANCE
        mgr.cancel_maintenance(w.id)
        assert reg.get_supplier("evolution").status == SupplierStatus.ACTIVE

    def test_cancel_nonexistent_window_raises(self):
        _, mgr = self._setup()
        with pytest.raises(MaintenanceWindowNotFoundError):
            mgr.cancel_maintenance("no-such-id")

    def test_get_active_maintenance(self):
        _, mgr = self._setup()
        mgr.schedule_maintenance("evolution", self._active_window())
        active = mgr.get_active_maintenance()
        assert len(active) == 1

    def test_list_maintenance_includes_all(self):
        _, mgr = self._setup()
        mgr.schedule_maintenance("evolution", self._future_window(offset_hours=10))
        mgr.schedule_maintenance("evolution", self._future_window(offset_hours=20))
        all_windows = mgr.list_maintenance("evolution")
        assert len(all_windows) == 2

    def test_invalid_window_start_after_end_raises(self):
        _, mgr = self._setup()
        now = datetime.now(timezone.utc)
        bad_window = MaintenanceWindow(
            supplier_id="evolution",
            start=now + timedelta(hours=3),
            end=now + timedelta(hours=1),
            reason="bad",
        )
        with pytest.raises(ValueError):
            mgr.schedule_maintenance("evolution", bad_window)


# ===========================================================================
# 5. Credential Manager
# ===========================================================================


class TestCredentialManager:
    def _setup(self):
        reg = _make_registry()
        reg.register_supplier(_make_record())
        backend = InMemorySecretBackend()
        mgr = CredentialManager(registry=reg, backend=backend)
        return reg, mgr

    def _add_creds(self, mgr: CredentialManager, supplier_id="evolution",
                   brand_id="brand1", jurisdiction="GB"):
        creds = Credentials(
            supplier_id=supplier_id,
            brand_id=brand_id,
            jurisdiction=jurisdiction,
            api_key="TESTKEY1234567890",
            api_secret="TESTSECRET",
            operator_id="OP1",
        )
        return mgr.add_credentials(creds)

    def test_add_and_get_credentials(self):
        _, mgr = self._setup()
        self._add_creds(mgr)
        creds = mgr.get_credentials("evolution", "brand1", "GB")
        assert creds.api_key == "TESTKEY1234567890"

    def test_get_nonexistent_credentials_raises(self):
        _, mgr = self._setup()
        with pytest.raises(ValueError):
            mgr.get_credentials("evolution", "brand1", "GB")

    def test_rotate_credentials_generates_new_key(self):
        _, mgr = self._setup()
        self._add_creds(mgr)
        original_key = mgr.get_credentials("evolution", "brand1", "GB").api_key
        rotated = mgr.rotate_credentials("evolution", "brand1", "GB")
        assert len(rotated) == 1
        assert rotated[0].api_key != original_key

    def test_rotate_records_rotation_timestamp(self):
        _, mgr = self._setup()
        self._add_creds(mgr)
        rotated = mgr.rotate_credentials("evolution", "brand1", "GB")
        assert rotated[0].rotated_at is not None

    def test_has_credentials_true_after_add(self):
        _, mgr = self._setup()
        self._add_creds(mgr)
        assert mgr.has_credentials("evolution", "brand1", "GB") is True

    def test_has_credentials_false_before_add(self):
        _, mgr = self._setup()
        assert mgr.has_credentials("evolution", "brand1", "GB") is False

    def test_list_brands_for_supplier(self):
        _, mgr = self._setup()
        self._add_creds(mgr, brand_id="brand1")
        self._add_creds(mgr, brand_id="brand2")
        brands = mgr.list_brands_for_supplier("evolution")
        assert set(brands) == {"brand1", "brand2"}

    def test_rotate_all_jurisdictions_for_brand(self):
        _, mgr = self._setup()
        self._add_creds(mgr, jurisdiction="GB")
        self._add_creds(mgr, jurisdiction="MT")
        rotated = mgr.rotate_credentials("evolution", "brand1")
        assert len(rotated) == 2

    def test_credentials_masked_key_hides_secret(self):
        _, mgr = self._setup()
        creds = self._add_creds(mgr)
        assert creds.masked_key()[-4:] == creds.api_key[-4:]
        assert "****" in creds.masked_key() or creds.masked_key().startswith("*")
