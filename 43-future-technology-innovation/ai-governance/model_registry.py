#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
AI Model Registry for iGaming Platform Governance.

Provides centralized tracking of all AI/ML models deployed across the casino
platform, including risk classification per EU AI Act (Regulation 2024/1689),
version management, performance metrics, bias audit results, and full audit
trails.

Usage:
    registry = ModelRegistry("/var/lib/ai-governance/models.db")
    model_id = registry.register_model(
        name="aml_transaction_scoring",
        version="3.2.1",
        risk_tier=RiskTier.HIGH,
        owner="risk-compliance-team",
        ...
    )

Storage: SQLite (swap for PostgreSQL in production via connection string).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class RiskTier(str, Enum):
    """EU AI Act risk classification tiers."""

    PROHIBITED = "PROHIBITED"
    HIGH = "HIGH"
    LIMITED = "LIMITED"
    MINIMAL = "MINIMAL"


class ModelStatus(str, Enum):
    """Lifecycle status of a registered model."""

    DRAFT = "DRAFT"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    DEPLOYED = "DEPLOYED"
    MONITORING = "MONITORING"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


# Convenience frozensets exported for tests and callers that want to
# validate string inputs without constructing the Enum members.
VALID_RISK_TIERS: frozenset[str] = frozenset(tier.value for tier in RiskTier)
VALID_STATUSES: frozenset[str] = frozenset(status.value for status in ModelStatus)


@dataclass
class BiasAuditResult:
    """Result of a fairness/bias audit on a model."""

    audit_id: str
    model_id: str
    audit_date: str
    auditor: str
    demographic_parity_diff: float
    equalized_odds_diff: float
    predictive_parity_diff: Optional[float] = None
    pass_fail: str = "PENDING"
    notes: str = ""
    protected_attributes_tested: list[str] = field(default_factory=list)


@dataclass
class AuditEvent:
    """Immutable audit trail entry."""

    event_id: str
    model_id: str
    timestamp: str
    event_type: str
    actor: str
    details: dict[str, Any]


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS models (
    model_id         TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    version          TEXT NOT NULL,
    risk_tier        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'DRAFT',
    owner            TEXT NOT NULL,
    purpose          TEXT NOT NULL DEFAULT '',
    architecture     TEXT NOT NULL DEFAULT '',
    training_data_description TEXT NOT NULL DEFAULT '',
    performance_metrics TEXT NOT NULL DEFAULT '{}',
    jurisdictions    TEXT NOT NULL DEFAULT '[]',
    human_oversight_configured INTEGER NOT NULL DEFAULT 0,
    conformity_assessment_date TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    deployed_at      TEXT,
    retired_at       TEXT,
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS bias_audits (
    audit_id         TEXT PRIMARY KEY,
    model_id         TEXT NOT NULL,
    audit_date       TEXT NOT NULL,
    auditor          TEXT NOT NULL,
    demographic_parity_diff REAL,
    equalized_odds_diff     REAL,
    predictive_parity_diff  REAL,
    pass_fail        TEXT NOT NULL DEFAULT 'PENDING',
    notes            TEXT NOT NULL DEFAULT '',
    protected_attributes TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (model_id) REFERENCES models(model_id)
);

CREATE TABLE IF NOT EXISTS audit_trail (
    event_id   TEXT PRIMARY KEY,
    model_id   TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor      TEXT NOT NULL,
    details    TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (model_id) REFERENCES models(model_id)
);

CREATE INDEX IF NOT EXISTS idx_models_name ON models(name);
CREATE INDEX IF NOT EXISTS idx_models_risk ON models(risk_tier);
CREATE INDEX IF NOT EXISTS idx_models_status ON models(status);
CREATE INDEX IF NOT EXISTS idx_audit_model ON audit_trail(model_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_trail(timestamp);
CREATE INDEX IF NOT EXISTS idx_bias_model ON bias_audits(model_id);
"""


class ModelRegistry:
    """Central registry for all AI/ML models in the casino platform.

    Tracks model metadata, risk classification, performance metrics,
    bias audit results, and provides a full audit trail for every
    state change.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _log_event(
        self,
        model_id: str,
        event_type: str,
        actor: str,
        details: dict[str, Any],
    ) -> str:
        event_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO audit_trail (event_id, model_id, timestamp, event_type, actor, details) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, model_id, self._now(), event_type, actor, json.dumps(details)),
        )
        return event_id

    # ------------------------------------------------------------------
    # Model CRUD
    # ------------------------------------------------------------------

    def register_model(
        self,
        name: str,
        version: str,
        risk_tier: RiskTier | str,
        owner: str,
        purpose: str = "",
        architecture: str = "",
        training_data_description: str = "",
        performance_metrics: dict[str, float] | None = None,
        jurisdictions: list[str] | None = None,
    ) -> str:
        """Register a new AI model in the governance registry.

        Returns the generated model_id (UUID).
        Raises ValueError if a model with the same name+version exists.
        """
        if isinstance(risk_tier, str):
            risk_tier = RiskTier(risk_tier)

        if risk_tier == RiskTier.PROHIBITED:
            raise ValueError(
                f"Cannot register model '{name}' with PROHIBITED risk tier. "
                "Prohibited AI practices must not be deployed (Article 5)."
            )

        model_id = str(uuid.uuid4())
        now = self._now()
        metrics_json = json.dumps(performance_metrics or {})
        jurisdictions_json = json.dumps(jurisdictions or [])

        try:
            self._conn.execute(
                "INSERT INTO models "
                "(model_id, name, version, risk_tier, status, owner, purpose, "
                " architecture, training_data_description, performance_metrics, "
                " jurisdictions, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    model_id, name, version, risk_tier.value, ModelStatus.DRAFT.value,
                    owner, purpose, architecture, training_data_description,
                    metrics_json, jurisdictions_json, now, now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Model '{name}' version '{version}' already registered."
            ) from exc

        self._log_event(model_id, "REGISTERED", owner, {
            "name": name, "version": version, "risk_tier": risk_tier.value,
        })
        self._conn.commit()
        return model_id

    def get_model(self, model_id: str) -> dict[str, Any]:
        """Retrieve full model metadata by ID."""
        row = self._conn.execute(
            "SELECT * FROM models WHERE model_id = ?", (model_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Model '{model_id}' not found.")
        result = dict(row)
        result["performance_metrics"] = json.loads(result["performance_metrics"])
        result["jurisdictions"] = json.loads(result["jurisdictions"])
        return result

    def find_models(
        self,
        name: str | None = None,
        risk_tier: RiskTier | str | None = None,
        status: ModelStatus | str | None = None,
    ) -> list[dict[str, Any]]:
        """Search models by name, risk tier, or status."""
        clauses: list[str] = []
        params: list[str] = []
        if name:
            clauses.append("name = ?")
            params.append(name)
        if risk_tier:
            tier = risk_tier.value if isinstance(risk_tier, RiskTier) else risk_tier
            clauses.append("risk_tier = ?")
            params.append(tier)
        if status:
            st = status.value if isinstance(status, ModelStatus) else status
            clauses.append("status = ?")
            params.append(st)

        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM models WHERE {where} ORDER BY updated_at DESC", params
        ).fetchall()

        results = []
        for row in rows:
            r = dict(row)
            r["performance_metrics"] = json.loads(r["performance_metrics"])
            r["jurisdictions"] = json.loads(r["jurisdictions"])
            results.append(r)
        return results

    def update_status(
        self,
        model_id: str,
        new_status: ModelStatus | str,
        actor: str = "system",
        reason: str = "",
    ) -> None:
        """Transition a model to a new lifecycle status."""
        if isinstance(new_status, str):
            new_status = ModelStatus(new_status)

        model = self.get_model(model_id)
        old_status = model["status"]
        now = self._now()

        updates: dict[str, Any] = {"status": new_status.value, "updated_at": now}
        if new_status == ModelStatus.DEPLOYED:
            updates["deployed_at"] = now
        elif new_status == ModelStatus.RETIRED:
            updates["retired_at"] = now

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [model_id]
        self._conn.execute(
            f"UPDATE models SET {set_clause} WHERE model_id = ?", values
        )

        self._log_event(model_id, "STATUS_CHANGE", actor, {
            "old_status": old_status,
            "new_status": new_status.value,
            "reason": reason,
        })
        self._conn.commit()

    def update_performance_metrics(
        self,
        model_id: str,
        metrics: dict[str, float],
        actor: str = "system",
    ) -> None:
        """Update performance metrics for a model."""
        now = self._now()
        self._conn.execute(
            "UPDATE models SET performance_metrics = ?, updated_at = ? WHERE model_id = ?",
            (json.dumps(metrics), now, model_id),
        )
        self._log_event(model_id, "METRICS_UPDATED", actor, {"metrics": metrics})
        self._conn.commit()

    def set_human_oversight(
        self, model_id: str, configured: bool, actor: str = "system"
    ) -> None:
        """Mark whether human oversight is configured for this model."""
        now = self._now()
        self._conn.execute(
            "UPDATE models SET human_oversight_configured = ?, updated_at = ? WHERE model_id = ?",
            (int(configured), now, model_id),
        )
        self._log_event(model_id, "OVERSIGHT_CONFIG", actor, {"configured": configured})
        self._conn.commit()

    def set_conformity_assessment_date(
        self, model_id: str, date: str, actor: str = "system"
    ) -> None:
        """Record when the last conformity assessment was performed."""
        now = self._now()
        self._conn.execute(
            "UPDATE models SET conformity_assessment_date = ?, updated_at = ? WHERE model_id = ?",
            (date, now, model_id),
        )
        self._log_event(model_id, "CONFORMITY_ASSESSED", actor, {"date": date})
        self._conn.commit()

    # ------------------------------------------------------------------
    # Bias Audits
    # ------------------------------------------------------------------

    def record_bias_audit(
        self,
        model_id: str,
        auditor: str,
        demographic_parity_diff: float,
        equalized_odds_diff: float,
        pass_fail: str = "PASS",
        predictive_parity_diff: float | None = None,
        notes: str = "",
        protected_attributes_tested: list[str] | None = None,
    ) -> str:
        """Record a bias/fairness audit result for a model."""
        audit_id = str(uuid.uuid4())
        now = self._now()
        attrs_json = json.dumps(protected_attributes_tested or [])

        self._conn.execute(
            "INSERT INTO bias_audits "
            "(audit_id, model_id, audit_date, auditor, demographic_parity_diff, "
            " equalized_odds_diff, predictive_parity_diff, pass_fail, notes, "
            " protected_attributes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit_id, model_id, now, auditor, demographic_parity_diff,
                equalized_odds_diff, predictive_parity_diff, pass_fail,
                notes, attrs_json,
            ),
        )

        self._log_event(model_id, "BIAS_AUDIT", auditor, {
            "audit_id": audit_id,
            "demographic_parity_diff": demographic_parity_diff,
            "equalized_odds_diff": equalized_odds_diff,
            "predictive_parity_diff": predictive_parity_diff,
            "pass_fail": pass_fail,
        })
        self._conn.commit()
        return audit_id

    def get_bias_audits(self, model_id: str) -> list[dict[str, Any]]:
        """Get all bias audit results for a model, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM bias_audits WHERE model_id = ? ORDER BY audit_date DESC",
            (model_id,),
        ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["protected_attributes"] = json.loads(r["protected_attributes"])
            results.append(r)
        return results

    def get_latest_bias_audit(self, model_id: str) -> dict[str, Any] | None:
        """Get the most recent bias audit for a model."""
        audits = self.get_bias_audits(model_id)
        return audits[0] if audits else None

    # ------------------------------------------------------------------
    # Audit Trail
    # ------------------------------------------------------------------

    def get_audit_trail(
        self, model_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get the full audit trail for a model."""
        rows = self._conn.execute(
            "SELECT * FROM audit_trail WHERE model_id = ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (model_id, limit),
        ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["details"] = json.loads(r["details"])
            results.append(r)
        return results

    # ------------------------------------------------------------------
    # Deployment gate
    # ------------------------------------------------------------------

    def check_deployment_readiness(self, model_id: str) -> dict[str, Any]:
        """Check whether a model meets all requirements for deployment.

        Returns a dict with 'ready' (bool), 'checks' (list of pass/fail),
        and 'blocking_issues' (list of strings).
        """
        model = self.get_model(model_id)
        checks: list[dict[str, Any]] = []
        blocking: list[str] = []

        # Check 1: Not prohibited
        if model["risk_tier"] == RiskTier.PROHIBITED.value:
            checks.append({"check": "risk_tier", "status": "FAIL", "detail": "Prohibited AI practice"})
            blocking.append("Model classified as PROHIBITED -- cannot be deployed")
        else:
            checks.append({"check": "risk_tier", "status": "PASS", "detail": model["risk_tier"]})

        # Check 2: Status must be APPROVED
        if model["status"] not in (ModelStatus.APPROVED.value, ModelStatus.DEPLOYED.value):
            checks.append({"check": "status", "status": "FAIL", "detail": f"Current: {model['status']}"})
            blocking.append(f"Model status is {model['status']}, must be APPROVED")
        else:
            checks.append({"check": "status", "status": "PASS", "detail": model["status"]})

        # Check 3: Performance metrics exist
        if not model["performance_metrics"]:
            checks.append({"check": "performance_metrics", "status": "FAIL", "detail": "No metrics"})
            blocking.append("No performance metrics recorded")
        else:
            checks.append({"check": "performance_metrics", "status": "PASS", "detail": "Present"})

        # High-risk specific checks
        if model["risk_tier"] == RiskTier.HIGH.value:
            # Bias audit required
            latest_audit = self.get_latest_bias_audit(model_id)
            if latest_audit is None:
                checks.append({"check": "bias_audit", "status": "FAIL", "detail": "No audit"})
                blocking.append("High-risk model requires bias audit before deployment")
            elif latest_audit["pass_fail"] != "PASS":
                checks.append({"check": "bias_audit", "status": "FAIL", "detail": latest_audit["pass_fail"]})
                blocking.append("Latest bias audit did not pass")
            else:
                checks.append({"check": "bias_audit", "status": "PASS", "detail": "Latest audit passed"})

            # Human oversight required
            if not model["human_oversight_configured"]:
                checks.append({"check": "human_oversight", "status": "FAIL", "detail": "Not configured"})
                blocking.append("Human oversight not configured for high-risk model")
            else:
                checks.append({"check": "human_oversight", "status": "PASS", "detail": "Configured"})

            # Conformity assessment required
            if not model["conformity_assessment_date"]:
                checks.append({"check": "conformity_assessment", "status": "FAIL", "detail": "Not assessed"})
                blocking.append("Conformity assessment not performed")
            else:
                checks.append({"check": "conformity_assessment", "status": "PASS", "detail": model["conformity_assessment_date"]})

        return {
            "model_id": model_id,
            "ready": len(blocking) == 0,
            "checks": checks,
            "blocking_issues": blocking,
        }

    def close(self) -> None:
        self._conn.close()


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="AI Model Registry CLI")
    parser.add_argument("--db-path", default="models.db", help="SQLite database path")
    sub = parser.add_subparsers(dest="command")

    # init
    sub.add_parser("init", help="Initialize the registry database")

    # list
    list_p = sub.add_parser("list", help="List registered models")
    list_p.add_argument("--risk-tier", choices=[t.value for t in RiskTier])
    list_p.add_argument("--status", choices=[s.value for s in ModelStatus])

    # check
    check_p = sub.add_parser("check", help="Check deployment readiness")
    check_p.add_argument("model_id", help="Model ID to check")

    args = parser.parse_args()
    registry = ModelRegistry(args.db_path)

    if args.command == "init":
        print(f"Registry initialized at {args.db_path}")

    elif args.command == "list":
        models = registry.find_models(
            risk_tier=args.risk_tier,
            status=args.status,
        )
        for m in models:
            print(f"  {m['model_id'][:8]}  {m['name']:30s}  v{m['version']:8s}  "
                  f"{m['risk_tier']:10s}  {m['status']}")
        if not models:
            print("  No models found.")

    elif args.command == "check":
        result = registry.check_deployment_readiness(args.model_id)
        for c in result["checks"]:
            status = "PASS" if c["status"] == "PASS" else "FAIL"
            print(f"  [{status}] {c['check']}: {c['detail']}")
        if result["ready"]:
            print("\n  Model is READY for deployment.")
        else:
            print("\n  Model is NOT ready. Blocking issues:")
            for issue in result["blocking_issues"]:
                print(f"    - {issue}")

    registry.close()


if __name__ == "__main__":
    main()




# ---------------------------------------------------------------------------
# Status vocabulary translation
# ---------------------------------------------------------------------------
#
# The storage layer uses the enum values from `ModelStatus` (DRAFT,
# UNDER_REVIEW, APPROVED, DEPLOYED, MONITORING, RETIRED). The governance
# test suite -- and the public compat API in general -- uses the shorter
# lowercase vocabulary from the EU AI Act guidance (draft, review,
# approved, deployed, retired). These two helpers translate in each
# direction; anything not in the table passes through lowercased so
# unexpected values don't silently disappear.

_STATUS_DB_TO_PUBLIC: dict[str, str] = {
    "DRAFT": "draft",
    "UNDER_REVIEW": "review",
    "APPROVED": "approved",
    "DEPLOYED": "deployed",
    "MONITORING": "monitoring",
    "RETIRED": "retired",
    "SUSPENDED": "suspended",
}

_STATUS_PUBLIC_TO_DB: dict[str, str] = {v: k for k, v in _STATUS_DB_TO_PUBLIC.items()}


def _status_db_to_public(value: str) -> str:
    return _STATUS_DB_TO_PUBLIC.get(value, str(value).lower())


def _status_public_to_db(value: str) -> str:
    return _STATUS_PUBLIC_TO_DB.get(value.lower(), value.upper())


# Wrapper class for test compatibility
@dataclass
class ModelRecord:
    """Model record returned by register() for test compatibility."""
    model_id: str
    name: str
    version: str
    risk_tier: str
    status: str
    owner: str = ""
    purpose: str = ""
    architecture: str = ""
    training_data_description: str = ""
    performance_metrics: dict[str, Any] = field(default_factory=dict)
    human_oversight_configured: bool = False
    conformity_assessment_date: str = ""
    jurisdictions: list[str] = field(default_factory=list)
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelRecord":
        """Create a ModelRecord from a dictionary.

        Normalises `risk_tier` and `status` to lowercase so callers see a
        stable, test-friendly vocabulary regardless of whether the
        underlying storage uses enum-style upper case.
        """
        return cls(
            model_id=data.get("model_id", ""),
            name=data.get("name", ""),
            version=data.get("version", ""),
            risk_tier=str(data.get("risk_tier", "")).lower(),
            status=_status_db_to_public(data.get("status", "")),
            owner=data.get("owner", ""),
            purpose=data.get("purpose", ""),
            architecture=data.get("architecture", ""),
            training_data_description=data.get("training_data_description", ""),
            performance_metrics=data.get("performance_metrics", {}),
            human_oversight_configured=data.get("human_oversight_configured", False),
            conformity_assessment_date=data.get("conformity_assessment_date", ""),
            jurisdictions=data.get("jurisdictions", []),
        )


# Extend ModelRegistry with test-compatible wrapper methods
def _register(
    self,
    name: str,
    risk_tier: str,
    purpose: str,
    owner: str = "test",
    version: str = "1.0",
    training_date: str = "",  # Accepted but not stored directly
    dataset_description: str = "",  # Maps to training_data_description
    bias_audit_results: dict[str, Any] | None = None,  # Accepted but not stored directly
    **kwargs: Any,
) -> ModelRecord:
    """Register a model (wrapper for test compatibility)."""
    # Validate risk tier
    if risk_tier.lower() == "prohibited":
        raise ValueError("Prohibited risk tier cannot be registered")
    if risk_tier.upper() not in VALID_RISK_TIERS:
        raise ValueError(f"Invalid risk_tier: {risk_tier}")
    
    # Map test parameter names to actual parameter names
    training_data_desc = dataset_description or kwargs.pop('training_data_description', '')
    
    # Call the actual register_model method with only supported params
    result = self.register_model(
        name=name,
        version=version,
        risk_tier=risk_tier.upper(),
        owner=owner,
        purpose=purpose,
        architecture=kwargs.pop('architecture', ''),
        training_data_description=training_data_desc,
        performance_metrics=kwargs.pop('performance_metrics', None),
        jurisdictions=kwargs.pop('jurisdictions', None),
    )
    
    # Fetch and return as ModelRecord
    model_data = self.get_model(result)
    return ModelRecord.from_dict(model_data)


def _get(self, model_id: str) -> ModelRecord:
    """Get a model by ID (wrapper for test compatibility)."""
    model_data = self.get_model(model_id)
    return ModelRecord.from_dict(model_data)


def _list_models(self, status: str | None = None) -> list[ModelRecord]:
    """List models with optional status filter (test compatibility)."""
    # Map test status names to actual status names
    actual_status = None
    if status:
        status_map = {
            "draft": "DRAFT",
            "review": "UNDER_REVIEW",
            "approved": "APPROVED",
            "deployed": "DEPLOYED",
            "monitoring": "MONITORING",
            "retired": "RETIRED",
        }
        actual_status = status_map.get(status.lower(), status.upper())
    
    all_models = self.find_models(status=actual_status)
    return [ModelRecord.from_dict(m) for m in all_models]


def _transition_status(
    self,
    model_id: str,
    new_status: str,
    actor: str = "test",
    reason: str = "",
) -> ModelRecord:
    """Transition model status (wrapper for test compatibility)."""
    actual_status = _status_public_to_db(new_status)

    # Check for valid transitions against the current stored value.
    model = self.get_model(model_id)
    current_status = model.get("status", "DRAFT")

    valid_transitions = {
        "DRAFT": ["UNDER_REVIEW"],
        "UNDER_REVIEW": ["APPROVED", "DRAFT"],
        "APPROVED": ["DEPLOYED", "DRAFT"],
        "DEPLOYED": ["MONITORING", "SUSPENDED", "RETIRED"],
        "MONITORING": ["SUSPENDED", "RETIRED"],
    }

    if actual_status not in valid_transitions.get(current_status, []):
        raise ValueError(
            f"Cannot transition from {current_status} to {actual_status}"
        )

    # High-risk models require a documented reason when moving to
    # APPROVED. This is the human-oversight handoff envisioned by
    # Article 14 -- the approver is attesting they reviewed the bias
    # audit + performance metrics before pushing the model toward a
    # deployment gate.
    if model.get("risk_tier") == "HIGH" and actual_status == "APPROVED" and not reason:
        raise ValueError(
            "High-risk models require a documented reason for approval"
        )

    self.update_status(model_id, actual_status, actor=actor)
    updated = self.get_model(model_id)
    return ModelRecord.from_dict(updated)


# Preserve the original native get_audit_trail so the public wrapper
# can delegate into it.
_original_get_audit_trail = ModelRegistry.get_audit_trail


def _get_audit_trail(
    self,
    model_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Audit trail in chronological order with test-friendly keys.

    The storage layer orders events DESC (newest first) and stores the
    event type under `event_type`. Callers that use the compat API
    expect chronological ascending order with an `action` key keyed to
    a lowercase EU AI Act vocabulary (`register`, `status_change`,
    `bias_audit`, ...), so we translate both here.
    """
    events = _original_get_audit_trail(self, model_id, limit=limit)
    # Storage returns DESC; the governance API expects chronological
    # order so audit trails read naturally top-to-bottom.
    events = list(reversed(events))

    event_type_map = {
        "REGISTERED": "register",
        "STATUS_CHANGED": "status_change",
        "BIAS_AUDIT_RECORDED": "bias_audit",
        "DEPLOYMENT_APPROVED": "deployment_approved",
    }

    for e in events:
        raw = e.get("event_type", "")
        e["action"] = event_type_map.get(raw, raw.lower())
    return events


# Monkey-patch the methods onto ModelRegistry
ModelRegistry.register = _register
ModelRegistry.get = _get
ModelRegistry.list_models = _list_models
ModelRegistry.transition_status = _transition_status
ModelRegistry.get_audit_trail = _get_audit_trail
