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
model_registry.py -- AI Model Registry for EU AI Act compliance.

Single source of truth for all AI/ML models deployed in the iGaming platform.
Tracks the full lifecycle from training to retirement with immutable audit trails.

Model lifecycle states:
  DRAFT -> IN_REVIEW -> APPROVED -> DEPLOYED -> MONITORING
  MONITORING -> SUSPENDED (drift) or RETIRED
  SUSPENDED -> IN_REVIEW (retrained)

Chapter 43b: AI Governance for iGaming Platforms under the EU AI Act
Script reference: new-platform/scripts/ai-governance/model_registry.py
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class RiskTier(str, Enum):
    """EU AI Act risk classification tiers."""
    PROHIBITED = "PROHIBITED"
    HIGH = "HIGH"
    LIMITED = "LIMITED"
    MINIMAL = "MINIMAL"


class ModelStatus(str, Enum):
    """Model lifecycle states."""
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    DEPLOYED = "DEPLOYED"
    MONITORING = "MONITORING"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class ModelRegistry:
    """
    AI Model Registry with SQLite storage (swap for PostgreSQL in production).

    Provides:
      - Model registration with EU AI Act risk tier classification
      - Status lifecycle management with audit trail
      - Bias audit recording
      - Deployment gate validation (blocks PROHIBITED and unapproved models)
      - Full audit trail retrieval

    Usage::

        registry = ModelRegistry("models.db")
        model_id = registry.register_model(
            name="aml_transaction_scoring",
            version="3.2.1",
            risk_tier=RiskTier.HIGH,
            owner="risk-compliance-team",
            purpose="Real-time AML transaction risk scoring",
            ...
        )
        registry.update_status(model_id, ModelStatus.DEPLOYED, deployed_by="jsmith")
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        """
        Args:
            db_path: Path to SQLite database file. Use ':memory:' for testing.
        """
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        """Create registry tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS models (
                id                          TEXT PRIMARY KEY,
                name                        TEXT NOT NULL,
                version                     TEXT NOT NULL,
                risk_tier                   TEXT NOT NULL,
                status                      TEXT NOT NULL DEFAULT 'DRAFT',
                owner                       TEXT NOT NULL,
                purpose                     TEXT NOT NULL,
                architecture                TEXT,
                training_data_description   TEXT,
                performance_metrics         TEXT,   -- JSON
                jurisdictions               TEXT,   -- JSON array
                human_oversight_configured  INTEGER NOT NULL DEFAULT 0,
                conformity_assessment_date  TEXT,
                deployed_at                 TEXT,
                retired_at                  TEXT,
                created_at                  TEXT NOT NULL,
                updated_at                  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id          TEXT PRIMARY KEY,
                model_id    TEXT NOT NULL REFERENCES models(id),
                event_type  TEXT NOT NULL,  -- 'REGISTERED', 'STATUS_CHANGE', 'BIAS_AUDIT', ...
                from_status TEXT,
                to_status   TEXT,
                actor       TEXT,
                details     TEXT,           -- JSON
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bias_audits (
                id                          TEXT PRIMARY KEY,
                model_id                    TEXT NOT NULL REFERENCES models(id),
                auditor                     TEXT NOT NULL,
                demographic_parity_diff     REAL,
                equalized_odds_diff         REAL,
                predictive_parity_diff      REAL,
                pass_fail                   TEXT NOT NULL,
                notes                       TEXT,
                raw_metrics                 TEXT,   -- JSON
                created_at                  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_models_name ON models(name);
            CREATE INDEX IF NOT EXISTS idx_audit_model_id ON audit_log(model_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_bias_model_id ON bias_audits(model_id, created_at);
        """)
        self._conn.commit()

    # ---------------------------------------------------------------------------
    # Model CRUD
    # ---------------------------------------------------------------------------

    def register_model(
        self,
        name: str,
        version: str,
        risk_tier: RiskTier,
        owner: str,
        purpose: str,
        architecture: Optional[str] = None,
        training_data_description: Optional[str] = None,
        performance_metrics: Optional[dict[str, float]] = None,
        jurisdictions: Optional[list[str]] = None,
    ) -> str:
        """
        Register a new AI model in the registry.

        Args:
            name:           Model name (e.g., 'aml_transaction_scoring')
            version:        Semantic version (e.g., '3.2.1')
            risk_tier:      EU AI Act risk classification
            owner:          Team/individual responsible for the model
            purpose:        Plain-language description of what the model does
            architecture:   Model architecture (e.g., 'XGBoost ensemble + LSTM')
            training_data_description: Description of training dataset
            performance_metrics: {'precision': 0.94, 'recall': 0.89, ...}
            jurisdictions:  List of jurisdictions where deployed (['MGA', 'UKGC'])

        Returns:
            model_id (UUID string)

        Raises:
            ValueError: If risk_tier is PROHIBITED (cannot register prohibited systems)
        """
        if risk_tier == RiskTier.PROHIBITED:
            raise ValueError(
                f"Cannot register model {name!r}: PROHIBITED AI systems cannot be deployed "
                f"under EU AI Act Article 5. This use case must be redesigned or abandoned."
            )

        model_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()

        self._conn.execute(
            """INSERT INTO models (
                id, name, version, risk_tier, status, owner, purpose,
                architecture, training_data_description, performance_metrics,
                jurisdictions, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                model_id, name, version, risk_tier.value, ModelStatus.DRAFT.value,
                owner, purpose, architecture, training_data_description,
                json.dumps(performance_metrics or {}),
                json.dumps(jurisdictions or []),
                now, now,
            ),
        )

        self._log_event(
            model_id=model_id,
            event_type="REGISTERED",
            from_status=None,
            to_status=ModelStatus.DRAFT.value,
            actor=owner,
            details={"version": version, "risk_tier": risk_tier.value},
        )

        self._conn.commit()
        return model_id

    def get_model(self, model_id: str) -> dict[str, Any]:
        """Retrieve a model by ID with latest bias audit status."""
        row = self._conn.execute(
            "SELECT * FROM models WHERE id = ?", (model_id,)
        ).fetchone()

        if row is None:
            raise KeyError(f"Model {model_id!r} not found in registry")

        model = dict(row)
        model["performance_metrics"] = json.loads(model["performance_metrics"] or "{}")
        model["jurisdictions"] = json.loads(model["jurisdictions"] or "[]")

        # Attach latest bias audit
        audit_row = self._conn.execute(
            """SELECT * FROM bias_audits WHERE model_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (model_id,),
        ).fetchone()
        model["latest_bias_audit"] = dict(audit_row) if audit_row else None

        return model

    def update_status(
        self,
        model_id: str,
        new_status: ModelStatus | str,
        deployed_by: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """
        Transition a model to a new lifecycle status.

        Pre-deployment validation:
          - DEPLOYED: model must be APPROVED, risk_tier must not be PROHIBITED,
                      latest bias audit must PASS, HIGH-risk models must have
                      human oversight configured and conformity assessment < 365 days

        Args:
            model_id:    Model ID to update
            new_status:  Target lifecycle status
            deployed_by: Actor performing the transition
            reason:      Reason for status change (required for SUSPENDED, RETIRED)
        """
        if isinstance(new_status, str):
            new_status = ModelStatus(new_status)

        model = self.get_model(model_id)
        from_status = model["status"]

        # Deployment gate validation
        if new_status == ModelStatus.DEPLOYED:
            self._validate_deployment_gate(model)

        now = datetime.now(tz=timezone.utc).isoformat()
        update_fields: dict[str, Any] = {
            "status": new_status.value,
            "updated_at": now,
        }
        if new_status == ModelStatus.DEPLOYED:
            update_fields["deployed_at"] = now
        if new_status == ModelStatus.RETIRED:
            update_fields["retired_at"] = now

        set_clause = ", ".join(f"{k} = ?" for k in update_fields)
        self._conn.execute(
            f"UPDATE models SET {set_clause} WHERE id = ?",
            (*update_fields.values(), model_id),
        )

        self._log_event(
            model_id=model_id,
            event_type="STATUS_CHANGE",
            from_status=from_status,
            to_status=new_status.value,
            actor=deployed_by,
            details={"reason": reason},
        )
        self._conn.commit()

    def _validate_deployment_gate(self, model: dict[str, Any]) -> None:
        """
        Validate that a model meets all requirements for production deployment.
        Raises ValueError if any requirement is not met.
        """
        if model["status"] != ModelStatus.APPROVED.value:
            raise ValueError(
                f"Model must be APPROVED before deployment. Current status: {model['status']}"
            )

        if model["risk_tier"] == RiskTier.PROHIBITED.value:
            raise ValueError("Cannot deploy PROHIBITED AI system (EU AI Act Article 5)")

        audit = model.get("latest_bias_audit")
        if audit is None or audit.get("pass_fail") != "PASS":
            raise ValueError(
                "Cannot deploy model without a passing bias audit. "
                "Run BiasAuditor and record results before deployment."
            )

        if model["risk_tier"] == RiskTier.HIGH.value:
            if not model.get("human_oversight_configured"):
                raise ValueError(
                    "HIGH-RISK model requires human oversight configuration (EU AI Act Article 14)"
                )

            conformity_date_str = model.get("conformity_assessment_date")
            if conformity_date_str is None:
                raise ValueError(
                    "HIGH-RISK model requires conformity assessment (EU AI Act Article 9)"
                )

            conformity_date = datetime.fromisoformat(conformity_date_str)
            days_since = (datetime.now(tz=timezone.utc) - conformity_date).days
            if days_since >= 365:
                raise ValueError(
                    f"Conformity assessment is {days_since} days old (max 365 for HIGH-RISK models)"
                )

    # ---------------------------------------------------------------------------
    # Bias audits
    # ---------------------------------------------------------------------------

    def record_bias_audit(
        self,
        model_id: str,
        auditor: str,
        demographic_parity_diff: float,
        equalized_odds_diff: float,
        pass_fail: str,
        predictive_parity_diff: Optional[float] = None,
        notes: Optional[str] = None,
        raw_metrics: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Record a bias audit result for a model.

        Args:
            model_id:                   Model to audit
            auditor:                    Auditor name/ID
            demographic_parity_diff:    Max demographic parity difference across groups
            equalized_odds_diff:        Max equalized odds difference across groups
            pass_fail:                  'PASS' or 'FAIL'
            predictive_parity_diff:     Max predictive parity difference (optional)
            notes:                      Auditor notes
            raw_metrics:                Full metrics dict for archival

        Returns:
            audit_id (UUID string)
        """
        audit_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()

        self._conn.execute(
            """INSERT INTO bias_audits (
                id, model_id, auditor, demographic_parity_diff,
                equalized_odds_diff, predictive_parity_diff,
                pass_fail, notes, raw_metrics, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                audit_id, model_id, auditor,
                demographic_parity_diff, equalized_odds_diff,
                predictive_parity_diff, pass_fail, notes,
                json.dumps(raw_metrics or {}), now,
            ),
        )

        self._log_event(
            model_id=model_id,
            event_type="BIAS_AUDIT",
            from_status=None,
            to_status=None,
            actor=auditor,
            details={
                "pass_fail": pass_fail,
                "demographic_parity_diff": demographic_parity_diff,
                "equalized_odds_diff": equalized_odds_diff,
            },
        )

        self._conn.commit()
        return audit_id

    # ---------------------------------------------------------------------------
    # Audit trail
    # ---------------------------------------------------------------------------

    def get_audit_trail(self, model_id: str) -> list[dict[str, Any]]:
        """Return all audit log entries for a model, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM audit_log WHERE model_id = ? ORDER BY created_at ASC",
            (model_id,),
        ).fetchall()
        result = []
        for row in rows:
            entry = dict(row)
            if entry.get("details"):
                entry["details"] = json.loads(entry["details"])
            result.append(entry)
        return result

    def _log_event(
        self,
        model_id: str,
        event_type: str,
        from_status: Optional[str],
        to_status: Optional[str],
        actor: Optional[str],
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Insert an immutable audit log entry."""
        self._conn.execute(
            """INSERT INTO audit_log (id, model_id, event_type, from_status, to_status, actor, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()), model_id, event_type,
                from_status, to_status, actor,
                json.dumps(details or {}),
                datetime.now(tz=timezone.utc).isoformat(),
            ),
        )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
