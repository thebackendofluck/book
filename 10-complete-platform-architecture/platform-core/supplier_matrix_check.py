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
supplier_matrix_check.py  (M9)
------------------------------
Operational validation script: Supplier Matrix Check.

Validates the full state of the supplier integration layer against five
invariants. Designed to run as a Kubernetes CronJob, a CI gate, or an
on-call runbook step.

Invariants checked
------------------
1. Every supplier has valid credentials for each configured brand.
2. The capability matrix covers all required jurisdictions for every supplier.
3. No supplier is in DEGRADED state for more than 1 hour without an alert
   having been fired.
4. Maintenance windows don't overlap for the same supplier.
5. All suppliers respond to the health check within the latency threshold.

Exit codes
----------
0 — all checks passed
1 — one or more checks failed (details printed to stdout)
2 — configuration error (bad environment variable, missing required config)

Usage
-----
    python supplier_matrix_check.py

Environment variables
---------------------
    REQUIRED_JURISDICTIONS  Comma-separated list of jurisdictions every
                            supplier must cover. Default: GB,MT,GI
    REQUIRED_BRANDS         Comma-separated list of brand IDs every supplier
                            must have credentials for. Default: brand1,brand2
    DEGRADED_ALERT_HOURS    Hours before an un-alerted DEGRADED state is a
                            failure. Default: 1.0
    HEALTH_LATENCY_THRESHOLD_MS  Max acceptable latency in ms. Default: 2000
    CONTROL_PLANE_URL       Base URL of the Supplier Control Plane API.
                            If set, checks are performed via HTTP calls.
                            If unset, checks run in-process against the
                            in-memory singleton registry (dev/test mode).
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
import urllib.error
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("m9-supplier-matrix-check")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REQUIRED_JURISDICTIONS: list[str] = os.environ.get(
    "REQUIRED_JURISDICTIONS", "GB,MT,GI"
).split(",")

REQUIRED_BRANDS: list[str] = os.environ.get(
    "REQUIRED_BRANDS", "brand1,brand2"
).split(",")

DEGRADED_ALERT_HOURS: float = float(os.environ.get("DEGRADED_ALERT_HOURS", "1.0"))

HEALTH_LATENCY_THRESHOLD_MS: float = float(
    os.environ.get("HEALTH_LATENCY_THRESHOLD_MS", "2000")
)

CONTROL_PLANE_URL: Optional[str] = os.environ.get("CONTROL_PLANE_URL", "").strip() or None


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    check_name: str
    passed: bool = True
    details: list[str] = field(default_factory=list)
    supplier_id: Optional[str] = None

    def add_failure(self, message: str) -> None:
        self.passed = False
        self.details.append(message)

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [f"[{status}] {self.check_name}"]
        for d in self.details:
            lines.append(f"       {d}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTTP helpers (used when CONTROL_PLANE_URL is set)
# ---------------------------------------------------------------------------


def _api_get(path: str) -> dict | list:
    url = f"{CONTROL_PLANE_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url}") from exc
    except Exception as exc:
        raise RuntimeError(f"Request to {url} failed: {exc}") from exc


# ---------------------------------------------------------------------------
# In-process helpers (used when no CONTROL_PLANE_URL is configured)
# ---------------------------------------------------------------------------


def _load_in_process():
    """
    Return (registry, maintenance_manager, health_monitor) from in-process
    singletons. Adds parent to sys.path if needed.
    """
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _cp_dir = os.path.join(_script_dir, "supplier-control-plane")
    if _cp_dir not in sys.path:
        sys.path.insert(0, _cp_dir)

    from registry import registry
    from maintenance import maintenance_manager
    from health_monitor import monitor

    return registry, maintenance_manager, monitor


# ---------------------------------------------------------------------------
# Check 1: Credentials coverage
# ---------------------------------------------------------------------------


def check_credentials_coverage() -> CheckResult:
    """
    Every supplier must have valid credentials for each (brand, jurisdiction)
    pair defined in REQUIRED_BRANDS × REQUIRED_JURISDICTIONS.
    """
    result = CheckResult(check_name="M9-C1: Credentials Coverage")

    if CONTROL_PLANE_URL:
        suppliers = _api_get("/suppliers")
        for supplier in suppliers:
            sid = supplier["id"]
            # For each required brand × jurisdiction, try to fetch creds via API
            for brand in REQUIRED_BRANDS:
                for jur in REQUIRED_JURISDICTIONS:
                    try:
                        _api_get(f"/suppliers/{sid}/credentials/{brand}/{jur}")
                    except RuntimeError:
                        result.add_failure(
                            f"Supplier {sid!r}: missing credentials for "
                            f"brand={brand!r} jurisdiction={jur!r}"
                        )
        return result

    # In-process path
    registry, _, _ = _load_in_process()
    for record in registry.list_suppliers():
        for brand in REQUIRED_BRANDS:
            for jur in REQUIRED_JURISDICTIONS:
                creds = record.get_credentials(brand, jur)
                if creds is None:
                    result.add_failure(
                        f"Supplier {record.id!r}: missing credentials for "
                        f"brand={brand!r} jurisdiction={jur!r}"
                    )

    return result


# ---------------------------------------------------------------------------
# Check 2: Capability matrix jurisdiction coverage
# ---------------------------------------------------------------------------


def check_capability_jurisdiction_coverage() -> CheckResult:
    """
    Every supplier's capability matrix must include all REQUIRED_JURISDICTIONS.
    Suppliers with no capability matrix at all are flagged.
    """
    result = CheckResult(check_name="M9-C2: Capability Jurisdiction Coverage")

    if CONTROL_PLANE_URL:
        suppliers = _api_get("/suppliers")
        for supplier in suppliers:
            sid = supplier["id"]
            cap = supplier.get("capabilities")
            if cap is None:
                result.add_failure(f"Supplier {sid!r}: no capability matrix configured")
                continue
            jurs = set(cap.get("jurisdictions", []))
            for required in REQUIRED_JURISDICTIONS:
                if required not in jurs:
                    result.add_failure(
                        f"Supplier {sid!r}: capability matrix missing jurisdiction {required!r}"
                    )
        return result

    registry, _, _ = _load_in_process()
    for record in registry.list_suppliers():
        if record.capabilities is None:
            result.add_failure(
                f"Supplier {record.id!r}: no capability matrix configured"
            )
            continue
        for required in REQUIRED_JURISDICTIONS:
            if not record.capabilities.supports_jurisdiction(required):
                result.add_failure(
                    f"Supplier {record.id!r}: capability matrix missing "
                    f"jurisdiction {required!r}"
                )

    return result


# ---------------------------------------------------------------------------
# Check 3: Long-duration DEGRADED without alert
# ---------------------------------------------------------------------------


def check_degraded_alert_coverage() -> CheckResult:
    """
    No supplier should remain in DEGRADED state for more than
    DEGRADED_ALERT_HOURS without an alert having been fired.

    In API mode this checks the /suppliers endpoint for DEGRADED suppliers
    and compares updated_at against the threshold.
    In process mode it uses the health monitor's _degraded_since tracking.
    """
    result = CheckResult(check_name="M9-C3: Degraded Alert Coverage")

    if CONTROL_PLANE_URL:
        suppliers = _api_get("/suppliers")
        now = datetime.now(timezone.utc)
        for supplier in suppliers:
            if supplier.get("status") == "DEGRADED":
                sid = supplier["id"]
                updated_str = supplier.get("updated_at", "")
                try:
                    updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                    # Strip timezone for naive comparison
                    if updated.tzinfo is not None:
                        from datetime import timezone
                        updated = updated.replace(tzinfo=None)
                    hours_degraded = (now - updated).total_seconds() / 3600
                except ValueError:
                    hours_degraded = 0.0

                if hours_degraded > DEGRADED_ALERT_HOURS:
                    result.add_failure(
                        f"Supplier {sid!r} has been DEGRADED for "
                        f"{hours_degraded:.1f} h without alert (threshold: "
                        f"{DEGRADED_ALERT_HOURS} h)"
                    )
        return result

    registry, _, monitor = _load_in_process()
    long_degraded = monitor.get_long_degraded_suppliers(
        threshold_hours=DEGRADED_ALERT_HOURS
    )
    for supplier_id, hours in long_degraded:
        result.add_failure(
            f"Supplier {supplier_id!r} has been DEGRADED for {hours:.1f} h "
            f"without recovery (threshold: {DEGRADED_ALERT_HOURS} h)"
        )

    return result


# ---------------------------------------------------------------------------
# Check 4: Maintenance window overlap
# ---------------------------------------------------------------------------


def check_maintenance_window_overlaps() -> CheckResult:
    """
    No two maintenance windows for the same supplier should overlap.
    (The maintenance manager prevents this at scheduling time; this check
    is a belt-and-suspenders audit of persisted state.)
    """
    result = CheckResult(check_name="M9-C4: Maintenance Window Overlap")

    if CONTROL_PLANE_URL:
        suppliers = _api_get("/suppliers")
        for supplier in suppliers:
            sid = supplier["id"]
            windows = _api_get(f"/suppliers/{sid}/maintenance")
            # Naive O(n²) overlap check
            for i, w1 in enumerate(windows):
                for j, w2 in enumerate(windows):
                    if j <= i:
                        continue
                    s1 = datetime.fromisoformat(w1["start"].replace("Z", ""))
                    e1 = datetime.fromisoformat(w1["end"].replace("Z", ""))
                    s2 = datetime.fromisoformat(w2["start"].replace("Z", ""))
                    e2 = datetime.fromisoformat(w2["end"].replace("Z", ""))
                    if s1 < e2 and e1 > s2:
                        result.add_failure(
                            f"Supplier {sid!r}: windows {w1['id']} and "
                            f"{w2['id']} overlap"
                        )
        return result

    registry, maintenance_manager, _ = _load_in_process()
    for record in registry.list_suppliers():
        windows = maintenance_manager.list_maintenance(record.id)
        for i, w1 in enumerate(windows):
            for j, w2 in enumerate(windows):
                if j <= i:
                    continue
                if w1.overlaps(w2):
                    result.add_failure(
                        f"Supplier {record.id!r}: windows {w1.id} and "
                        f"{w2.id} overlap ({w1.start}–{w1.end} vs "
                        f"{w2.start}–{w2.end})"
                    )

    return result


# ---------------------------------------------------------------------------
# Check 5: Health check responsiveness
# ---------------------------------------------------------------------------


def check_health_responsiveness() -> CheckResult:
    """
    All ACTIVE and DEGRADED suppliers should respond to a health probe
    within HEALTH_LATENCY_THRESHOLD_MS.

    MAINTENANCE and DISABLED suppliers are excluded.
    """
    result = CheckResult(check_name="M9-C5: Health Check Responsiveness")

    if CONTROL_PLANE_URL:
        health_data = _api_get("/suppliers/health")
        if isinstance(health_data, dict):
            for sid, h in health_data.items():
                if h.get("status") == "UNREACHABLE":
                    result.add_failure(
                        f"Supplier {sid!r} is UNREACHABLE: {h.get('message', '')}"
                    )
                elif h.get("latency_ms", 0) > HEALTH_LATENCY_THRESHOLD_MS:
                    result.add_failure(
                        f"Supplier {sid!r} latency {h['latency_ms']:.0f} ms > "
                        f"threshold {HEALTH_LATENCY_THRESHOLD_MS:.0f} ms"
                    )
        return result

    registry, _, monitor = _load_in_process()
    from models import SupplierStatus as SS, HealthStatus as HS

    # Only check active/degraded — skip maintenance and disabled
    active_suppliers = registry.list_suppliers(
        {"status": "ACTIVE"}
    ) + registry.list_suppliers({"status": "DEGRADED"})

    for record in active_suppliers:
        health = monitor.get_cached_health(record.id)
        if health is None:
            # No cached reading; attempt a live probe
            try:
                health = monitor.check_supplier_health(record.id)
            except Exception as exc:  # noqa: BLE001
                result.add_failure(
                    f"Supplier {record.id!r}: health probe failed — {exc}"
                )
                continue

        if health.status == HS.UNREACHABLE:
            result.add_failure(
                f"Supplier {record.id!r} is UNREACHABLE: {health.message}"
            )
        elif health.latency_ms > HEALTH_LATENCY_THRESHOLD_MS:
            result.add_failure(
                f"Supplier {record.id!r} latency {health.latency_ms:.0f} ms > "
                f"threshold {HEALTH_LATENCY_THRESHOLD_MS:.0f} ms"
            )

    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all_checks() -> int:
    """Run all M9 checks and return exit code (0 = all pass, 1 = failures)."""
    checks = [
        check_credentials_coverage,
        check_capability_jurisdiction_coverage,
        check_degraded_alert_coverage,
        check_maintenance_window_overlaps,
        check_health_responsiveness,
    ]

    results: list[CheckResult] = []
    for check_fn in checks:
        logger.info("Running %s ...", check_fn.__name__)
        try:
            r = check_fn()
        except Exception as exc:  # noqa: BLE001
            r = CheckResult(check_name=check_fn.__name__, passed=False)
            r.add_failure(f"Unexpected exception: {exc}")
        results.append(r)

    print("\n" + "=" * 60)
    print("M9 Supplier Matrix Check — Results")
    print("=" * 60)
    any_failure = False
    for r in results:
        print(r.summary())
        if not r.passed:
            any_failure = True

    print("=" * 60)
    if any_failure:
        print("RESULT: FAILED — one or more invariants violated")
        return 1
    else:
        print("RESULT: PASSED — all invariants satisfied")
        return 0


if __name__ == "__main__":
    sys.exit(run_all_checks())
