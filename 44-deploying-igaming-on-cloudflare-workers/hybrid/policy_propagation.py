#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Policy Propagation Service.

Pushes configuration changes from the core layer to the edge layer
with verification and rollback:

  1. Cloudflare KV bulk write
  2. Verification read-back (checksum comparison)
  3. Automatic rollback on failure

The propagation model is push-based: core is the source of truth,
edge is a cached projection.  Every propagation is atomic per batch --
either all keys in the batch succeed, or all are rolled back.

Environments
------------
  staging  - in-memory KV, instant propagation
  uat      - mock Cloudflare API with simulated latency
  prod     - real Cloudflare API calls
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class PropagationStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class PropagationEntry:
    key: str
    value: dict[str, Any]
    checksum: str
    previous_value: dict[str, Any] | None = None
    previous_checksum: str | None = None


@dataclass
class PropagationBatch:
    batch_id: str
    entries: list[PropagationEntry]
    status: PropagationStatus = PropagationStatus.PENDING
    created_at: float = 0.0
    completed_at: float = 0.0
    write_latency_ms: float = 0.0
    verify_latency_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class PropagationReport:
    batch_id: str
    status: str
    entries_count: int
    write_latency_ms: float
    verify_latency_ms: float
    total_latency_ms: float
    all_verified: bool
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "status": self.status,
            "entries_count": self.entries_count,
            "write_latency_ms": round(self.write_latency_ms, 2),
            "verify_latency_ms": round(self.verify_latency_ms, 2),
            "total_latency_ms": round(self.total_latency_ms, 2),
            "all_verified": self.all_verified,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _checksum(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Cloudflare KV adapter
# ---------------------------------------------------------------------------

class CloudflareKVAdapter:
    """
    Simulated Cloudflare KV API.

    In production, this would use the Cloudflare REST API:
      PUT /client/v4/accounts/{id}/storage/kv/namespaces/{ns}/bulk
      GET /client/v4/accounts/{id}/storage/kv/namespaces/{ns}/values/{key}
    """

    def __init__(
        self,
        simulated_latency_ms: float = 5.0,
        failure_keys: set[str] | None = None,
    ) -> None:
        self._store: dict[str, str] = {}
        self._latency_ms = simulated_latency_ms
        self._failure_keys = failure_keys or set()

    def bulk_write(self, entries: dict[str, str]) -> float:
        """Write multiple KV pairs. Returns latency in ms."""
        start = time.monotonic()
        for key in entries:
            if key in self._failure_keys:
                raise RuntimeError(f"Simulated write failure for key: {key}")
        for key, value in entries.items():
            self._store[key] = value
        elapsed = (time.monotonic() - start) * 1000 + self._latency_ms
        return elapsed

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def read_back_checksum(self, key: str) -> str | None:
        raw = self.get(key)
        if raw is None:
            return None
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Core config source
# ---------------------------------------------------------------------------

class CoreConfigSource:
    """Simulated core configuration source (Redis or PostgreSQL)."""

    def __init__(self) -> None:
        self._configs: dict[str, dict[str, Any]] = {}

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._configs[key] = value

    def get(self, key: str) -> dict[str, Any] | None:
        return self._configs.get(key)

    def get_all(self) -> dict[str, dict[str, Any]]:
        return dict(self._configs)


# ---------------------------------------------------------------------------
# Propagation engine
# ---------------------------------------------------------------------------

class PolicyPropagationEngine:
    """
    Orchestrates configuration propagation from core to edge.

    Guarantees:
      - Atomic batch semantics (all-or-nothing)
      - Checksum verification after write
      - Automatic rollback on verification failure
    """

    def __init__(
        self,
        kv: CloudflareKVAdapter,
        core: CoreConfigSource,
    ) -> None:
        self._kv = kv
        self._core = core
        self._history: list[PropagationBatch] = []

    def propagate(
        self,
        keys: list[str] | None = None,
    ) -> PropagationBatch:
        """Propagate config keys from core to edge.

        If keys is None, propagates all keys in core.
        """
        if keys is None:
            source = self._core.get_all()
        else:
            source = {}
            for k in keys:
                val = self._core.get(k)
                if val is not None:
                    source[k] = val

        batch = PropagationBatch(
            batch_id=uuid.uuid4().hex[:12],
            entries=[],
            status=PropagationStatus.IN_PROGRESS,
            created_at=time.time(),
        )

        # Build entries with previous state for rollback
        for key, value in source.items():
            cs = _checksum(value)
            # Capture current edge state for rollback
            current_raw = self._kv.get(key)
            prev_value = json.loads(current_raw) if current_raw else None
            prev_cs = _checksum(prev_value) if prev_value else None

            batch.entries.append(PropagationEntry(
                key=key,
                value=value,
                checksum=cs,
                previous_value=prev_value,
                previous_checksum=prev_cs,
            ))

        if not batch.entries:
            batch.status = PropagationStatus.VERIFIED
            batch.completed_at = time.time()
            self._history.append(batch)
            return batch

        # Bulk write
        kv_payload = {
            e.key: json.dumps(e.value, sort_keys=True, separators=(",", ":"))
            for e in batch.entries
        }
        try:
            batch.write_latency_ms = self._kv.bulk_write(kv_payload)
        except Exception as exc:
            batch.status = PropagationStatus.FAILED
            batch.errors.append(f"Write failed: {exc}")
            batch.completed_at = time.time()
            self._history.append(batch)
            return batch

        # Verify
        verify_start = time.monotonic()
        all_verified = True
        for entry in batch.entries:
            raw = self._kv.get(entry.key)
            if raw is None:
                all_verified = False
                batch.errors.append(f"Key '{entry.key}' not found after write")
                continue
            actual_cs = hashlib.sha256(raw.encode()).hexdigest()[:16]
            if actual_cs != entry.checksum:
                all_verified = False
                batch.errors.append(
                    f"Key '{entry.key}' checksum mismatch: "
                    f"expected={entry.checksum} actual={actual_cs}"
                )
        batch.verify_latency_ms = (time.monotonic() - verify_start) * 1000

        if all_verified:
            batch.status = PropagationStatus.VERIFIED
        else:
            # Rollback
            batch.status = PropagationStatus.ROLLED_BACK
            self._rollback(batch)

        batch.completed_at = time.time()
        self._history.append(batch)
        return batch

    def propagate_single(self, key: str) -> PropagationBatch:
        """Propagate a single config key."""
        return self.propagate(keys=[key])

    def _rollback(self, batch: PropagationBatch) -> None:
        """Restore previous edge state for all entries in batch."""
        for entry in batch.entries:
            if entry.previous_value is not None:
                payload = json.dumps(
                    entry.previous_value, sort_keys=True, separators=(",", ":")
                )
                try:
                    self._kv.bulk_write({entry.key: payload})
                except Exception:
                    batch.errors.append(
                        f"Rollback also failed for key '{entry.key}'"
                    )
            else:
                self._kv.delete(entry.key)

    def get_history(self) -> list[PropagationBatch]:
        return list(self._history)

    def generate_report(self, batch: PropagationBatch) -> PropagationReport:
        return PropagationReport(
            batch_id=batch.batch_id,
            status=batch.status.value,
            entries_count=len(batch.entries),
            write_latency_ms=batch.write_latency_ms,
            verify_latency_ms=batch.verify_latency_ms,
            total_latency_ms=batch.write_latency_ms + batch.verify_latency_ms,
            all_verified=batch.status == PropagationStatus.VERIFIED,
            errors=batch.errors,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Policy Propagation Demo")
    parser.add_argument("--env", choices=["staging", "uat", "prod"],
                        default="staging")
    args = parser.parse_args()

    kv = CloudflareKVAdapter(simulated_latency_ms=5.0)
    core = CoreConfigSource()
    engine = PolicyPropagationEngine(kv, core)

    # Seed core configs
    core.set("feature:aviator", {"enabled": True, "rollout_pct": 100})
    core.set("feature:crash", {"enabled": False, "rollout_pct": 0})
    core.set("jurisdiction:GB", {"min_age": 18, "deposit_limit_daily_gbp": 1000})
    core.set("jurisdiction:DE", {"min_age": 18, "deposit_limit_monthly_eur": 1000,
                                  "autoplay_prohibited": True})
    core.set("supplier:pragmatic", {"enabled": True, "rtp_target": 96.5})
    core.set("supplier:evolution", {"enabled": True, "rtp_target": 97.3})

    # Propagate all
    print("=== Full Propagation ===")
    batch = engine.propagate()
    report = engine.generate_report(batch)
    print(json.dumps(report.to_dict(), indent=2))

    # Update and re-propagate single key
    print("\n=== Single Key Update ===")
    core.set("feature:aviator", {"enabled": False, "rollout_pct": 0})
    batch2 = engine.propagate_single("feature:aviator")
    report2 = engine.generate_report(batch2)
    print(json.dumps(report2.to_dict(), indent=2))

    # Show history
    print(f"\n=== Propagation History: {len(engine.get_history())} batches ===")
    for b in engine.get_history():
        print(f"  {b.batch_id}: {b.status.value} ({len(b.entries)} entries)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
