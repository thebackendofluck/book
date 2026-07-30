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
tests/test_degradation_controller.py
-------------------------------------
Test suite for the Degradation Controller.

Covers:
  - Circuit breaker state transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
  - Failure threshold triggering
  - Recovery timeout and half-open probing
  - Supplier availability checks
  - Fallback routing
  - Degradation actions and restoration
  - Manual breaker reset
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_HERE = os.path.dirname(__file__)
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from degradation_controller import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    DegradationAction,
    DegradationController,
)
from models import (
    HealthStatus,
    SupplierCapabilityMatrix,
    SupplierHealth,
    SupplierRecord,
    SupplierStatus,
    SupplierType,
    WalletModel,
)
from registry import SupplierRegistry
from health_monitor import HealthMonitor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_record(
    supplier_id: str = "evolution",
    status: SupplierStatus = SupplierStatus.ACTIVE,
) -> SupplierRecord:
    return SupplierRecord(
        id=supplier_id,
        name=f"{supplier_id.title()} Gaming",
        type=SupplierType.CASINO,
        status=status,
        capabilities=SupplierCapabilityMatrix(
            supplier_id=supplier_id,
            games={"blackjack"},
            currencies={"EUR"},
            jurisdictions={"GB"},
            wallet_model=WalletModel.SEAMLESS,
        ),
    )


def _make_controller(
    failure_threshold: int = 3,
    recovery_timeout: int = 1,
    success_threshold: int = 2,
):
    reg = SupplierRegistry()
    reg.register_supplier(_make_record("evolution"))
    reg.register_supplier(_make_record("pragmatic"))

    def healthy_probe(supplier_id):
        return 100.0, 0.0, "OK"

    mon = HealthMonitor(registry=reg, probe_fn=healthy_probe)
    config = CircuitBreakerConfig(
        failure_threshold=failure_threshold,
        recovery_timeout_seconds=recovery_timeout,
        success_threshold=success_threshold,
    )
    ctrl = DegradationController(
        registry=reg, monitor=mon, default_config=config,
    )
    return reg, mon, ctrl


# ===========================================================================
# 1. Circuit Breaker Unit Tests
# ===========================================================================


class TestCircuitBreaker:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(supplier_id="test")
        assert cb.state == CircuitState.CLOSED

    def test_allows_request_when_closed(self):
        cb = CircuitBreaker(supplier_id="test")
        assert cb.should_allow_request() is True

    def test_opens_after_failure_threshold(self):
        cb = CircuitBreaker(
            supplier_id="test",
            config=CircuitBreakerConfig(failure_threshold=3),
        )
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_blocks_requests_when_open(self):
        cb = CircuitBreaker(
            supplier_id="test",
            config=CircuitBreakerConfig(
                failure_threshold=2,
                recovery_timeout_seconds=9999,
            ),
        )
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.should_allow_request() is False

    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(
            supplier_id="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_seconds=0,  # instant timeout for testing
            ),
        )
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        # With 0 timeout, should immediately transition
        assert cb.should_allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_closes_after_success_in_half_open(self):
        cb = CircuitBreaker(
            supplier_id="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_seconds=0,
                success_threshold=1,
            ),
        )
        cb.record_failure()
        cb.should_allow_request()  # transitions to HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_default_half_open_probe_closes_after_one_success(self):
        cb = CircuitBreaker(
            supplier_id="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_seconds=0,
            ),
        )
        cb.record_failure()
        cb.should_allow_request()  # transitions to HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_reopens_on_failure_in_half_open(self):
        cb = CircuitBreaker(
            supplier_id="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_seconds=0,
            ),
        )
        cb.record_failure()
        cb.should_allow_request()  # transitions to HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_failure_count_when_closed(self):
        cb = CircuitBreaker(
            supplier_id="test",
            config=CircuitBreakerConfig(failure_threshold=3),
        )
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2
        cb.record_success()
        assert cb.failure_count == 0

    def test_total_trips_incremented(self):
        cb = CircuitBreaker(
            supplier_id="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_seconds=0,
                success_threshold=1,
            ),
        )
        cb.record_failure()
        assert cb.total_trips == 1
        cb.should_allow_request()
        cb.record_success()
        cb.record_failure()
        assert cb.total_trips == 2

    def test_reset(self):
        cb = CircuitBreaker(
            supplier_id="test",
            config=CircuitBreakerConfig(failure_threshold=1),
        )
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_to_dict(self):
        cb = CircuitBreaker(supplier_id="test")
        d = cb.to_dict()
        assert d["supplier_id"] == "test"
        assert d["state"] == "CLOSED"
        assert "failure_count" in d


# ===========================================================================
# 2. Degradation Controller — Availability
# ===========================================================================


class TestAvailability:
    def test_active_supplier_is_available(self):
        _, _, ctrl = _make_controller()
        assert ctrl.is_available("evolution") is True

    def test_disabled_supplier_not_available(self):
        reg, _, ctrl = _make_controller()
        reg.update_status("evolution", SupplierStatus.DISABLED)
        assert ctrl.is_available("evolution") is False

    def test_maintenance_supplier_not_available(self):
        reg, _, ctrl = _make_controller()
        reg.update_status("evolution", SupplierStatus.MAINTENANCE)
        assert ctrl.is_available("evolution") is False

    def test_unknown_supplier_not_available(self):
        _, _, ctrl = _make_controller()
        assert ctrl.is_available("nonexistent") is False

    def test_supplier_unavailable_after_circuit_opens(self):
        _, _, ctrl = _make_controller(failure_threshold=3, recovery_timeout=9999)
        for _ in range(3):
            ctrl.record_failure("evolution")
        assert ctrl.is_available("evolution") is False


# ===========================================================================
# 3. Fallback Routing
# ===========================================================================


class TestFallbackRouting:
    def test_returns_primary_when_available(self):
        _, _, ctrl = _make_controller()
        assert ctrl.get_available_supplier("evolution") == "evolution"

    def test_returns_fallback_when_primary_down(self):
        _, _, ctrl = _make_controller(failure_threshold=1, recovery_timeout=9999)
        ctrl.set_fallback("evolution", "pragmatic")
        ctrl.record_failure("evolution")
        result = ctrl.get_available_supplier("evolution")
        assert result == "pragmatic"

    def test_returns_none_when_both_down(self):
        _, _, ctrl = _make_controller(failure_threshold=1, recovery_timeout=9999)
        ctrl.set_fallback("evolution", "pragmatic")
        ctrl.record_failure("evolution")
        ctrl.record_failure("pragmatic")
        assert ctrl.get_available_supplier("evolution") is None

    def test_get_and_remove_fallback(self):
        _, _, ctrl = _make_controller()
        ctrl.set_fallback("evolution", "pragmatic")
        assert ctrl.get_fallback("evolution") == "pragmatic"
        ctrl.remove_fallback("evolution")
        assert ctrl.get_fallback("evolution") is None


# ===========================================================================
# 4. Degradation and Recovery
# ===========================================================================


class TestDegradationRecovery:
    def test_circuit_open_hides_supplier(self):
        _, _, ctrl = _make_controller(failure_threshold=2)
        ctrl.record_failure("evolution")
        ctrl.record_failure("evolution")
        assert ctrl.is_hidden("evolution") is True

    def test_recovery_unhides_supplier(self):
        _, _, ctrl = _make_controller(
            failure_threshold=1, recovery_timeout=0, success_threshold=1,
        )
        ctrl.record_failure("evolution")
        assert ctrl.is_hidden("evolution") is True

        # Trigger half-open transition and success
        breaker = ctrl.get_breaker("evolution")
        breaker.should_allow_request()  # -> HALF_OPEN
        ctrl.record_success("evolution")
        assert ctrl.is_hidden("evolution") is False

    def test_degradation_updates_registry_status(self):
        reg, _, ctrl = _make_controller(failure_threshold=2)
        ctrl.record_failure("evolution")
        ctrl.record_failure("evolution")
        assert reg.get_supplier("evolution").status == SupplierStatus.DEGRADED

    def test_recovery_restores_registry_status(self):
        reg, _, ctrl = _make_controller(
            failure_threshold=1, recovery_timeout=0, success_threshold=1,
        )
        ctrl.record_failure("evolution")
        assert reg.get_supplier("evolution").status == SupplierStatus.DEGRADED

        breaker = ctrl.get_breaker("evolution")
        breaker.should_allow_request()
        ctrl.record_success("evolution")
        assert reg.get_supplier("evolution").status == SupplierStatus.ACTIVE

    def test_degradation_log_recorded(self):
        _, _, ctrl = _make_controller(failure_threshold=1)
        ctrl.record_failure("evolution")
        log = ctrl.get_degradation_log(supplier_id="evolution")
        assert len(log) >= 1
        assert log[-1].action_type == "hide_games"

    def test_manual_reset(self):
        _, _, ctrl = _make_controller(failure_threshold=1, recovery_timeout=9999)
        ctrl.record_failure("evolution")
        assert ctrl.is_available("evolution") is False

        ctrl.reset_breaker("evolution")
        assert ctrl.is_available("evolution") is True
        assert ctrl.is_hidden("evolution") is False


# ===========================================================================
# 5. Breaker State Introspection
# ===========================================================================


class TestBreakerIntrospection:
    def test_get_all_breaker_states(self):
        _, _, ctrl = _make_controller()
        ctrl.record_success("evolution")
        ctrl.record_failure("pragmatic")
        states = ctrl.get_all_breaker_states()
        assert "evolution" in states
        assert "pragmatic" in states
        assert states["evolution"]["state"] == "CLOSED"

    def test_get_hidden_suppliers(self):
        _, _, ctrl = _make_controller(failure_threshold=1)
        ctrl.record_failure("evolution")
        hidden = ctrl.get_hidden_suppliers()
        assert "evolution" in hidden

    def test_set_custom_breaker_config(self):
        _, _, ctrl = _make_controller()
        custom_config = CircuitBreakerConfig(
            failure_threshold=10,
            recovery_timeout_seconds=60,
        )
        ctrl.set_breaker_config("evolution", custom_config)
        breaker = ctrl.get_breaker("evolution")
        assert breaker.config.failure_threshold == 10
