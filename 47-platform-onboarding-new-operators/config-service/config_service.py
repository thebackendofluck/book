#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 47, Platform Onboarding.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Runtime Configuration Governance Service.

Manages the full lifecycle of platform configuration for a multi-tenant
iGaming deployment:

  - Feature flags (game enable/disable, market open/close)
  - Jurisdiction rules (age limits, deposit limits, blocked games per country)
  - Supplier config (RTP targets, bet limits, enabled/disabled)
  - Config versioning and rollback
  - Propagation to edge (Cloudflare KV) and core (Redis) with consistency checks
  - Audit trail of all config changes

Design notes
------------
The service is deliberately stateless.  All state lives in PostgreSQL (the
version-controlled config store), Redis (core runtime cache), and Cloudflare
KV (edge runtime cache).  The service itself is a thin governance layer that
enforces validation, versioning, and propagation semantics.

Environments
------------
  staging  - relaxed validation; propagation to edge is optional
  uat      - full validation; propagation is mandatory but non-blocking
  prod     - full validation; propagation is mandatory and blocking
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

class Environment(Enum):
    STAGING = "staging"
    UAT = "uat"
    PROD = "prod"


class ConfigScope(Enum):
    FEATURE_FLAG = "feature_flag"
    JURISDICTION = "jurisdiction"
    SUPPLIER = "supplier"
    PLATFORM = "platform"


class PropagationTarget(Enum):
    REDIS = "redis"
    CLOUDFLARE_KV = "cloudflare_kv"


class AuditAction(Enum):
    CREATE = "create"
    UPDATE = "update"
    ROLLBACK = "rollback"
    PROPAGATE = "propagate"
    PROPAGATE_FAIL = "propagate_fail"


@dataclass
class ConfigVersion:
    """Immutable snapshot of a configuration value."""

    version_id: str
    scope: ConfigScope
    key: str
    value: dict[str, Any]
    checksum: str
    created_at: float
    created_by: str
    environment: Environment
    previous_version_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditEntry:
    """Append-only record of every configuration change."""

    entry_id: str
    timestamp: float
    action: AuditAction
    scope: ConfigScope
    key: str
    version_id: str
    actor: str
    environment: Environment
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class PropagationResult:
    target: PropagationTarget
    success: bool
    latency_ms: float
    error: str | None = None
    verified: bool = False


# ---------------------------------------------------------------------------
# Jurisdiction rules
# ---------------------------------------------------------------------------

JURISDICTION_DEFAULTS: dict[str, dict[str, Any]] = {
    "GB": {
        "min_age": 18,
        "deposit_limit_daily_gbp": 1000,
        "deposit_limit_monthly_gbp": 5000,
        "blocked_games": [],
        "rg_cool_off_hours": [24, 48, 168],
        "self_exclusion_min_months": 6,
        "kyc_required_before_deposit": True,
        "affordability_check_threshold_gbp": 100,
    },
    "MT": {
        "min_age": 18,
        "deposit_limit_daily_eur": 2000,
        "deposit_limit_monthly_eur": 10000,
        "blocked_games": [],
        "rg_cool_off_hours": [24, 72],
        "self_exclusion_min_months": 6,
        "kyc_required_before_deposit": False,
    },
    "SE": {
        "min_age": 18,
        "deposit_limit_weekly_sek": 5000,
        "blocked_games": ["table-poker-cash"],
        "mandatory_play_break_minutes": 60,
        "rg_cool_off_hours": [24, 72, 720],
        "self_exclusion_via_spelpaus": True,
    },
    "US-NJ": {
        "min_age": 21,
        "deposit_limit_daily_usd": 5000,
        "geofence_required": True,
        "blocked_games": [],
        "data_residency": "us-east-1",
        "gli_certification_required": True,
    },
    "BR": {
        "min_age": 18,
        "deposit_limit_daily_brl": 5000,
        "blocked_games": [],
        "cpf_verification_required": True,
        "pix_mandatory": True,
        "sigap_reporting": True,
    },
    "DE": {
        "min_age": 18,
        "deposit_limit_monthly_eur": 1000,
        "blocked_games": ["live-roulette", "live-blackjack"],
        "slot_spin_interval_seconds": 5,
        "autoplay_prohibited": True,
        "panic_button_required": True,
    },
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ConfigValidationError(Exception):
    """Raised when a configuration value fails validation."""


def _compute_checksum(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def validate_feature_flag(key: str, value: dict[str, Any]) -> None:
    """Validate a feature flag configuration."""
    if "enabled" not in value:
        raise ConfigValidationError(
            f"Feature flag '{key}' must have an 'enabled' boolean field"
        )
    if not isinstance(value["enabled"], bool):
        raise ConfigValidationError(
            f"Feature flag '{key}'.enabled must be a boolean"
        )
    if "rollout_pct" in value:
        pct = value["rollout_pct"]
        if not isinstance(pct, (int, float)) or not (0 <= pct <= 100):
            raise ConfigValidationError(
                f"Feature flag '{key}'.rollout_pct must be 0-100"
            )


def validate_jurisdiction_rules(key: str, value: dict[str, Any]) -> None:
    """Validate jurisdiction-specific configuration."""
    if "min_age" not in value:
        raise ConfigValidationError(
            f"Jurisdiction '{key}' must define min_age"
        )
    min_age = value["min_age"]
    if not isinstance(min_age, int) or min_age < 18:
        raise ConfigValidationError(
            f"Jurisdiction '{key}'.min_age must be >= 18"
        )
    if "blocked_games" in value and not isinstance(value["blocked_games"], list):
        raise ConfigValidationError(
            f"Jurisdiction '{key}'.blocked_games must be a list"
        )


def validate_supplier_config(key: str, value: dict[str, Any]) -> None:
    """Validate supplier configuration."""
    required = {"supplier_id", "enabled"}
    missing = required - set(value.keys())
    if missing:
        raise ConfigValidationError(
            f"Supplier config '{key}' missing required fields: {missing}"
        )
    if "rtp_target" in value:
        rtp = value["rtp_target"]
        if not isinstance(rtp, (int, float)) or not (80 <= rtp <= 99.9):
            raise ConfigValidationError(
                f"Supplier '{key}'.rtp_target must be between 80 and 99.9"
            )
    if "bet_limits" in value:
        limits = value["bet_limits"]
        if "min" in limits and "max" in limits:
            if limits["min"] >= limits["max"]:
                raise ConfigValidationError(
                    f"Supplier '{key}' bet_limits.min must be < bet_limits.max"
                )


_VALIDATORS = {
    ConfigScope.FEATURE_FLAG: validate_feature_flag,
    ConfigScope.JURISDICTION: validate_jurisdiction_rules,
    ConfigScope.SUPPLIER: validate_supplier_config,
}


# ---------------------------------------------------------------------------
# Storage adapters (pluggable backends)
# ---------------------------------------------------------------------------

class PostgresConfigStore:
    """
    Simulated PostgreSQL store for config versions.

    In production this executes parameterised queries against a real
    PostgreSQL instance.  For the book's test suite, it operates
    entirely in-memory.
    """

    def __init__(self) -> None:
        self._versions: dict[str, list[ConfigVersion]] = {}
        self._audit: list[AuditEntry] = []

    def save_version(self, version: ConfigVersion) -> None:
        self._versions.setdefault(version.key, []).append(version)

    def get_current(self, key: str) -> ConfigVersion | None:
        history = self._versions.get(key, [])
        return history[-1] if history else None

    def get_version(self, key: str, version_id: str) -> ConfigVersion | None:
        for v in self._versions.get(key, []):
            if v.version_id == version_id:
                return v
        return None

    def get_history(self, key: str) -> list[ConfigVersion]:
        return list(self._versions.get(key, []))

    def append_audit(self, entry: AuditEntry) -> None:
        self._audit.append(entry)

    def get_audit_trail(
        self, key: str | None = None, limit: int = 100
    ) -> list[AuditEntry]:
        entries = self._audit
        if key:
            entries = [e for e in entries if e.key == key]
        return entries[-limit:]


class RedisCache:
    """
    Simulated Redis cache for core runtime config.

    In production this would use redis-py with connection pooling.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set_config(self, key: str, value: dict[str, Any]) -> float:
        start = time.monotonic()
        self._store[f"config:{key}"] = json.dumps(value, sort_keys=True)
        elapsed_ms = (time.monotonic() - start) * 1000
        return elapsed_ms

    def get_config(self, key: str) -> dict[str, Any] | None:
        raw = self._store.get(f"config:{key}")
        return json.loads(raw) if raw else None

    def delete_config(self, key: str) -> None:
        self._store.pop(f"config:{key}", None)

    def get_checksum(self, key: str) -> str | None:
        val = self.get_config(key)
        return _compute_checksum(val) if val else None


class CloudflareKVCache:
    """
    Simulated Cloudflare KV for edge config distribution.

    In production this uses the Cloudflare API for bulk writes
    and per-key reads.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._write_latency_ms: float = 2.0  # simulated edge latency

    def bulk_write(self, entries: dict[str, dict[str, Any]]) -> float:
        start = time.monotonic()
        for k, v in entries.items():
            self._store[f"config:{k}"] = json.dumps(v, sort_keys=True)
        elapsed_ms = (time.monotonic() - start) * 1000 + self._write_latency_ms
        return elapsed_ms

    def read(self, key: str) -> dict[str, Any] | None:
        raw = self._store.get(f"config:{key}")
        return json.loads(raw) if raw else None

    def read_back_verify(self, key: str, expected_checksum: str) -> bool:
        val = self.read(key)
        if val is None:
            return False
        return _compute_checksum(val) == expected_checksum

    def delete(self, key: str) -> None:
        self._store.pop(f"config:{key}", None)


# ---------------------------------------------------------------------------
# Config service
# ---------------------------------------------------------------------------

class ConfigService:
    """
    Central configuration governance service.

    Orchestrates validation, versioning, propagation, and audit for
    all configuration scopes.
    """

    def __init__(
        self,
        store: PostgresConfigStore,
        redis: RedisCache,
        kv: CloudflareKVCache,
        environment: Environment = Environment.STAGING,
    ) -> None:
        self._store = store
        self._redis = redis
        self._kv = kv
        self._env = environment

    # -- public API --------------------------------------------------------

    def set_config(
        self,
        scope: ConfigScope,
        key: str,
        value: dict[str, Any],
        actor: str,
        metadata: dict[str, Any] | None = None,
    ) -> ConfigVersion:
        """Create or update a configuration key.

        1. Validate the value against scope-specific rules.
        2. Create an immutable version snapshot.
        3. Persist to PostgreSQL.
        4. Propagate to Redis and Cloudflare KV.
        5. Write audit entry.
        """
        # validate
        validator = _VALIDATORS.get(scope)
        if validator:
            validator(key, value)

        # version
        current = self._store.get_current(key)
        version = ConfigVersion(
            version_id=uuid.uuid4().hex[:12],
            scope=scope,
            key=key,
            value=copy.deepcopy(value),
            checksum=_compute_checksum(value),
            created_at=time.time(),
            created_by=actor,
            environment=self._env,
            previous_version_id=current.version_id if current else None,
            metadata=metadata or {},
        )

        # persist
        self._store.save_version(version)

        # propagate
        self._propagate(version)

        # audit
        self._audit(AuditAction.CREATE if current is None else AuditAction.UPDATE,
                     version, actor)

        return version

    def get_config(self, key: str) -> dict[str, Any] | None:
        """Return current config value for a key."""
        version = self._store.get_current(key)
        return copy.deepcopy(version.value) if version else None

    def get_version(self, key: str, version_id: str) -> ConfigVersion | None:
        return self._store.get_version(key, version_id)

    def get_history(self, key: str) -> list[ConfigVersion]:
        return self._store.get_history(key)

    def rollback(self, key: str, target_version_id: str, actor: str) -> ConfigVersion:
        """Roll back a config key to a previous version.

        Creates a new version whose value matches the target version,
        then propagates and audits.
        """
        target = self._store.get_version(key, target_version_id)
        if target is None:
            raise ConfigValidationError(
                f"Version '{target_version_id}' not found for key '{key}'"
            )

        current = self._store.get_current(key)
        rollback_version = ConfigVersion(
            version_id=uuid.uuid4().hex[:12],
            scope=target.scope,
            key=key,
            value=copy.deepcopy(target.value),
            checksum=target.checksum,
            created_at=time.time(),
            created_by=actor,
            environment=self._env,
            previous_version_id=current.version_id if current else None,
            metadata={"rollback_to": target_version_id},
        )
        self._store.save_version(rollback_version)
        self._propagate(rollback_version)
        self._audit(AuditAction.ROLLBACK, rollback_version, actor,
                     detail={"rolled_back_to": target_version_id})
        return rollback_version

    def check_consistency(self, key: str) -> dict[str, Any]:
        """Verify edge and core caches are consistent with the source of truth."""
        version = self._store.get_current(key)
        if version is None:
            return {"key": key, "exists": False}

        expected = version.checksum
        redis_checksum = self._redis.get_checksum(key)
        kv_ok = self._kv.read_back_verify(key, expected)

        return {
            "key": key,
            "exists": True,
            "version_id": version.version_id,
            "expected_checksum": expected,
            "redis_consistent": redis_checksum == expected,
            "kv_consistent": kv_ok,
            "all_consistent": redis_checksum == expected and kv_ok,
        }

    def get_audit_trail(
        self, key: str | None = None, limit: int = 100
    ) -> list[AuditEntry]:
        return self._store.get_audit_trail(key, limit)

    def get_jurisdiction_defaults(self, country_code: str) -> dict[str, Any] | None:
        """Return built-in jurisdiction defaults for a country code."""
        return copy.deepcopy(JURISDICTION_DEFAULTS.get(country_code))

    def apply_jurisdiction_defaults(
        self, country_code: str, actor: str
    ) -> ConfigVersion | None:
        """Load jurisdiction defaults and persist as initial config."""
        defaults = self.get_jurisdiction_defaults(country_code)
        if defaults is None:
            return None
        key = f"jurisdiction:{country_code}"
        return self.set_config(ConfigScope.JURISDICTION, key, defaults, actor)

    # -- feature flag helpers ----------------------------------------------

    def is_feature_enabled(self, flag_key: str) -> bool:
        """Quick check whether a feature flag is enabled."""
        val = self.get_config(flag_key)
        if val is None:
            return False
        return bool(val.get("enabled", False))

    def set_feature_flag(
        self,
        flag_key: str,
        enabled: bool,
        actor: str,
        rollout_pct: float = 100.0,
    ) -> ConfigVersion:
        value: dict[str, Any] = {"enabled": enabled, "rollout_pct": rollout_pct}
        return self.set_config(ConfigScope.FEATURE_FLAG, flag_key, value, actor)

    # -- supplier helpers --------------------------------------------------

    def enable_supplier(
        self,
        supplier_id: str,
        config: dict[str, Any],
        actor: str,
    ) -> ConfigVersion:
        """Enable a game supplier with the given configuration."""
        config["supplier_id"] = supplier_id
        config.setdefault("enabled", True)
        key = f"supplier:{supplier_id}"
        return self.set_config(ConfigScope.SUPPLIER, key, config, actor)

    def disable_supplier(self, supplier_id: str, actor: str) -> ConfigVersion:
        """Disable a supplier while preserving its configuration."""
        key = f"supplier:{supplier_id}"
        current = self.get_config(key)
        if current is None:
            raise ConfigValidationError(f"Supplier '{supplier_id}' not found")
        current["enabled"] = False
        return self.set_config(ConfigScope.SUPPLIER, key, current, actor)

    # -- propagation -------------------------------------------------------

    def _propagate(self, version: ConfigVersion) -> list[PropagationResult]:
        results: list[PropagationResult] = []

        # Redis (core)
        try:
            latency = self._redis.set_config(version.key, version.value)
            results.append(PropagationResult(
                target=PropagationTarget.REDIS,
                success=True,
                latency_ms=latency,
                verified=self._redis.get_checksum(version.key) == version.checksum,
            ))
        except Exception as exc:
            results.append(PropagationResult(
                target=PropagationTarget.REDIS,
                success=False,
                latency_ms=0,
                error=str(exc),
            ))
            self._audit(AuditAction.PROPAGATE_FAIL, version, "system",
                         detail={"target": "redis", "error": str(exc)})

        # Cloudflare KV (edge)
        try:
            latency = self._kv.bulk_write({version.key: version.value})
            verified = self._kv.read_back_verify(version.key, version.checksum)
            results.append(PropagationResult(
                target=PropagationTarget.CLOUDFLARE_KV,
                success=True,
                latency_ms=latency,
                verified=verified,
            ))
        except Exception as exc:
            results.append(PropagationResult(
                target=PropagationTarget.CLOUDFLARE_KV,
                success=False,
                latency_ms=0,
                error=str(exc),
            ))
            self._audit(AuditAction.PROPAGATE_FAIL, version, "system",
                         detail={"target": "cloudflare_kv", "error": str(exc)})

            # In prod, KV failure triggers rollback of Redis to keep consistency
            if self._env == Environment.PROD:
                prev = version.previous_version_id
                if prev:
                    prev_version = self._store.get_version(version.key, prev)
                    if prev_version:
                        self._redis.set_config(version.key, prev_version.value)

        self._audit(AuditAction.PROPAGATE, version, "system",
                     detail={"results": [
                         {"target": r.target.value, "ok": r.success, "ms": r.latency_ms}
                         for r in results
                     ]})
        return results

    def _audit(
        self,
        action: AuditAction,
        version: ConfigVersion,
        actor: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        entry = AuditEntry(
            entry_id=uuid.uuid4().hex[:12],
            timestamp=time.time(),
            action=action,
            scope=version.scope,
            key=version.key,
            version_id=version.version_id,
            actor=actor,
            environment=self._env,
            detail=detail or {},
        )
        self._store.append_audit(entry)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Demonstrate the config service lifecycle."""
    import argparse

    parser = argparse.ArgumentParser(description="Config Service Demo")
    parser.add_argument("--env", choices=["staging", "uat", "prod"],
                        default="staging")
    args = parser.parse_args()

    env = Environment(args.env)
    store = PostgresConfigStore()
    redis = RedisCache()
    kv = CloudflareKVCache()
    svc = ConfigService(store, redis, kv, environment=env)

    # 1. Feature flags
    print("=== Feature Flags ===")
    svc.set_feature_flag("game:aviator", enabled=True, actor="ops-team",
                         rollout_pct=50.0)
    svc.set_feature_flag("market:brazil", enabled=True, actor="ops-team")
    svc.set_feature_flag("market:germany", enabled=False, actor="compliance")
    print(f"  aviator enabled: {svc.is_feature_enabled('game:aviator')}")
    print(f"  brazil enabled:  {svc.is_feature_enabled('market:brazil')}")
    print(f"  germany enabled: {svc.is_feature_enabled('market:germany')}")

    # 2. Jurisdiction rules
    print("\n=== Jurisdiction Rules ===")
    for code in ["GB", "BR", "DE", "US-NJ"]:
        v = svc.apply_jurisdiction_defaults(code, actor="compliance")
        if v:
            print(f"  {code}: version={v.version_id} checksum={v.checksum}")

    # 3. Supplier config
    print("\n=== Supplier Config ===")
    svc.enable_supplier("pragmatic-play", {
        "rtp_target": 96.5,
        "bet_limits": {"min": 0.20, "max": 100.00, "currency": "EUR"},
        "callback_url": "https://api.example.com/pragmatic/callback",
    }, actor="integration-team")

    svc.enable_supplier("evolution", {
        "rtp_target": 97.3,
        "bet_limits": {"min": 1.00, "max": 10000.00, "currency": "EUR"},
        "live_tables": True,
    }, actor="integration-team")

    # 4. Consistency check
    print("\n=== Consistency Checks ===")
    for key in ["game:aviator", "jurisdiction:GB", "supplier:pragmatic-play"]:
        result = svc.check_consistency(key)
        status = "OK" if result.get("all_consistent") else "DRIFT"
        print(f"  {key}: {status}")

    # 5. Rollback demo
    print("\n=== Rollback Demo ===")
    history = svc.get_history("game:aviator")
    if len(history) >= 1:
        original_id = history[0].version_id
        svc.set_feature_flag("game:aviator", enabled=False, actor="ops-team")
        print(f"  disabled aviator: {svc.is_feature_enabled('game:aviator')}")
        svc.rollback("game:aviator", original_id, actor="ops-team")
        print(f"  rolled back:      {svc.is_feature_enabled('game:aviator')}")

    # 6. Audit trail
    print("\n=== Audit Trail (last 10) ===")
    for entry in svc.get_audit_trail(limit=10):
        print(f"  [{entry.action.value}] {entry.key} v={entry.version_id}"
              f" by={entry.actor} env={entry.environment.value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
