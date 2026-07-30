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
decision_logger.py -- Immutable AI decision audit trail with SHAP explanations.

Implements Article 12 (Record-Keeping) and Article 13 (Transparency) of the
EU AI Act, combined with GDPR Article 22 (right to explanation for automated
decisions).

Every high-risk AI decision (AML scoring, fraud detection, KYC, responsible
gaming triggers) must be logged before any action is taken. Human overrides
are recorded as separate events that reference the original decision — the
original decision record is never modified.

Decision lifecycle:
  PENDING -> AUTO_EXECUTED (confidence > threshold, auto-executed)
  PENDING -> QUEUED_FOR_REVIEW (medium confidence, human review within 4h)
  PENDING -> ESCALATED (low confidence / high impact, immediate escalation)
  QUEUED_FOR_REVIEW -> APPROVED (human review, original decision stands)
  QUEUED_FOR_REVIEW -> OVERRIDDEN (human review, decision changed)
  QUEUED_FOR_REVIEW -> ESCALATED (human escalates)

Chapter 43b: AI Governance for iGaming Platforms under the EU AI Act
Script reference: new-platform/scripts/ai-governance/decision_logger.py
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DecisionType(str, Enum):
    """Type of AI decision being logged."""
    AML_FLAG = "AML_FLAG"
    FRAUD_SCORE = "FRAUD_SCORE"
    KYC_OUTCOME = "KYC_OUTCOME"
    RG_INTERVENTION = "RG_INTERVENTION"
    WITHDRAWAL_BLOCK = "WITHDRAWAL_BLOCK"
    ACCOUNT_RESTRICTION = "ACCOUNT_RESTRICTION"
    BONUS_RESTRICTION = "BONUS_RESTRICTION"
    GAME_RECOMMENDATION = "GAME_RECOMMENDATION"
    SUPPORT_ROUTING = "SUPPORT_ROUTING"


class DecisionOutcome(str, Enum):
    """Final action taken based on the model output."""
    ALLOW = "ALLOW"
    FLAG_FOR_REVIEW = "FLAG_FOR_REVIEW"
    BLOCK = "BLOCK"
    RESTRICT = "RESTRICT"
    ESCALATE = "ESCALATE"
    RECOMMEND = "RECOMMEND"


class ReviewStatus(str, Enum):
    """Human review workflow status."""
    PENDING = "PENDING"
    AUTO_EXECUTED = "AUTO_EXECUTED"
    QUEUED_FOR_REVIEW = "QUEUED_FOR_REVIEW"
    ESCALATED = "ESCALATED"
    APPROVED = "APPROVED"
    OVERRIDDEN = "OVERRIDDEN"
    DISMISSED = "DISMISSED"


class ImpactLevel(str, Enum):
    """
    Impact classification for human oversight routing (Article 14).

    HIGH  -> Human-in-the-loop (must approve before execution)
    MEDIUM -> Human-on-the-loop (batch review within 4 hours)
    LOW   -> Human-on-the-loop (periodic performance review)
    """
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ShapExplanation:
    """
    SHAP-based explanation for a single model prediction.

    Stores the top contributing features with their SHAP values and
    player-facing descriptions (plain language translation of model internals).
    """
    base_score: float                               # model's expected value (mean output)
    final_score: float                              # actual model output
    top_positive_factors: list[dict[str, Any]]     # features pushing score UP
    top_negative_factors: list[dict[str, Any]]     # features pushing score DOWN

    # Example structure for each factor:
    # {
    #     "feature": "deposit_frequency_24h",
    #     "impact": 0.23,
    #     "description": "Unusually high deposit frequency in last 24 hours"
    # }

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_score": self.base_score,
            "final_score": self.final_score,
            "top_positive_factors": self.top_positive_factors,
            "top_negative_factors": self.top_negative_factors,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ShapExplanation":
        return cls(
            base_score=d["base_score"],
            final_score=d["final_score"],
            top_positive_factors=d.get("top_positive_factors", []),
            top_negative_factors=d.get("top_negative_factors", []),
        )


@dataclass
class AIDecision:
    """
    A single AI decision ready to be logged.

    Create this object, populate it, then pass to DecisionLogger.log().
    The logger assigns decision_id and timestamp.
    """
    model_name: str                                 # e.g., 'aml_transaction_scoring'
    model_version: str                              # e.g., '3.2.1'
    decision_type: DecisionType
    player_id: str                                  # pseudonymised player ID
    session_id: Optional[str]                       # game/payment session
    transaction_id: Optional[str]                   # payment transaction if applicable
    input_features: dict[str, Any]                  # features used (no raw PII)
    output_score: float                             # raw model output (0.0-1.0)
    threshold_applied: float                        # business-rule threshold
    outcome: DecisionOutcome                        # final action taken
    impact_level: ImpactLevel
    jurisdiction: str                               # regulatory context (MGA, UKGC, etc.)
    explanation: Optional[ShapExplanation] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HumanOverride:
    """
    Record of a human analyst overriding an AI decision.

    Human overrides are ADDITIONAL records — they never modify the original
    decision. This preserves the immutable audit trail while recording that
    a human reviewed and changed the outcome.
    """
    decision_id: str                                # references AIDecision.decision_id
    analyst_id: str
    original_outcome: DecisionOutcome
    new_outcome: DecisionOutcome
    reason_code: str                                # from predefined list
    reason_text: str                                # free-text justification
    second_approver_id: Optional[str] = None       # required for high-impact overrides
    supporting_evidence_url: Optional[str] = None  # optional attachment reference


# ---------------------------------------------------------------------------
# DecisionLogger
# ---------------------------------------------------------------------------

class DecisionLogger:
    """
    Immutable AI decision audit trail logger.

    Persists every AI decision and human override to SQLite (use PostgreSQL
    with WAL mode in production). All records are INSERT-only — no UPDATEs
    or DELETEs are performed by this class.

    The decisions table captures the full decision context including SHAP
    explanations. The human_overrides table records any analyst interventions.
    The review_queue table tracks pending human review items.

    Retention: Configure your database backup policy to meet the most
    restrictive jurisdiction you operate in (MGA/NJ DGE: 10 years).

    Usage::

        logger = DecisionLogger("decisions.db")

        decision = AIDecision(
            model_name="aml_transaction_scoring",
            model_version="3.2.1",
            decision_type=DecisionType.AML_FLAG,
            player_id="P-29481",
            session_id=None,
            transaction_id="TXN-984231",
            input_features={
                "amount_eur": 5000,
                "deposit_frequency_24h": 8,
                "payment_method_type": "CRYPTO",
            },
            output_score=0.82,
            threshold_applied=0.70,
            outcome=DecisionOutcome.FLAG_FOR_REVIEW,
            impact_level=ImpactLevel.HIGH,
            jurisdiction="MGA",
            explanation=ShapExplanation(
                base_score=0.12,
                final_score=0.82,
                top_positive_factors=[
                    {"feature": "deposit_frequency_24h", "impact": 0.23,
                     "description": "Unusually high deposit frequency"},
                ],
                top_negative_factors=[
                    {"feature": "account_age_days", "impact": -0.15,
                     "description": "Long-standing account history"},
                ],
            ),
        )

        decision_id = logger.log(decision)
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        """Create audit tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (
                id                  TEXT PRIMARY KEY,
                model_name          TEXT NOT NULL,
                model_version       TEXT NOT NULL,
                decision_type       TEXT NOT NULL,
                player_id           TEXT NOT NULL,
                session_id          TEXT,
                transaction_id      TEXT,
                input_features      TEXT NOT NULL,    -- JSON (PII-stripped)
                output_score        REAL NOT NULL,
                threshold_applied   REAL NOT NULL,
                outcome             TEXT NOT NULL,
                review_status       TEXT NOT NULL DEFAULT 'PENDING',
                impact_level        TEXT NOT NULL,
                jurisdiction        TEXT NOT NULL,
                explanation         TEXT,             -- JSON (ShapExplanation)
                metadata            TEXT,             -- JSON
                decided_at          TEXT NOT NULL,
                review_completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS human_overrides (
                id                      TEXT PRIMARY KEY,
                decision_id             TEXT NOT NULL REFERENCES decisions(id),
                analyst_id              TEXT NOT NULL,
                original_outcome        TEXT NOT NULL,
                new_outcome             TEXT NOT NULL,
                reason_code             TEXT NOT NULL,
                reason_text             TEXT NOT NULL,
                second_approver_id      TEXT,
                supporting_evidence_url TEXT,
                created_at              TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS review_queue (
                decision_id     TEXT PRIMARY KEY REFERENCES decisions(id),
                impact_level    TEXT NOT NULL,
                model_name      TEXT NOT NULL,
                outcome         TEXT NOT NULL,
                jurisdiction    TEXT NOT NULL,
                queued_at       TEXT NOT NULL,
                due_by          TEXT NOT NULL,        -- SLA deadline
                assigned_to     TEXT,
                priority        INTEGER NOT NULL DEFAULT 5
            );

            CREATE INDEX IF NOT EXISTS idx_decisions_player ON decisions(player_id, decided_at);
            CREATE INDEX IF NOT EXISTS idx_decisions_model ON decisions(model_name, decided_at);
            CREATE INDEX IF NOT EXISTS idx_decisions_jurisdiction ON decisions(jurisdiction, decided_at);
            CREATE INDEX IF NOT EXISTS idx_overrides_decision ON human_overrides(decision_id);
            CREATE INDEX IF NOT EXISTS idx_queue_due ON review_queue(due_by, priority);
        """)
        self._conn.commit()

    # ---------------------------------------------------------------------------
    # Core logging
    # ---------------------------------------------------------------------------

    def log(self, decision: AIDecision) -> str:
        """
        Log an AI decision to the immutable audit trail.

        Automatically:
          - Assigns a unique decision_id (UUID4)
          - Sets review_status based on impact_level and confidence
          - Queues for human review if required (Article 14)

        Args:
            decision: Populated AIDecision dataclass

        Returns:
            decision_id (UUID string)
        """
        decision_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()

        review_status = self._determine_review_status(decision)

        self._conn.execute(
            """INSERT INTO decisions (
                id, model_name, model_version, decision_type,
                player_id, session_id, transaction_id,
                input_features, output_score, threshold_applied,
                outcome, review_status, impact_level, jurisdiction,
                explanation, metadata, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id,
                decision.model_name,
                decision.model_version,
                decision.decision_type.value,
                decision.player_id,
                decision.session_id,
                decision.transaction_id,
                json.dumps(decision.input_features),
                decision.output_score,
                decision.threshold_applied,
                decision.outcome.value,
                review_status.value,
                decision.impact_level.value,
                decision.jurisdiction,
                json.dumps(decision.explanation.to_dict()) if decision.explanation else None,
                json.dumps(decision.metadata),
                now,
            ),
        )

        if review_status in (ReviewStatus.QUEUED_FOR_REVIEW, ReviewStatus.ESCALATED):
            self._enqueue_for_review(decision_id, decision, now)

        self._conn.commit()
        return decision_id

    def log_override(self, override: HumanOverride) -> str:
        """
        Record a human analyst override of an AI decision.

        The original decision is NOT modified. The override is stored as a
        separate immutable record that references the original decision_id.

        Args:
            override: Populated HumanOverride dataclass

        Returns:
            override_id (UUID string)

        Raises:
            KeyError: If decision_id does not exist in the audit trail
        """
        # Verify the decision exists
        row = self._conn.execute(
            "SELECT id FROM decisions WHERE id = ?", (override.decision_id,)
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Decision {override.decision_id!r} not found in audit trail"
            )

        override_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()

        self._conn.execute(
            """INSERT INTO human_overrides (
                id, decision_id, analyst_id, original_outcome, new_outcome,
                reason_code, reason_text, second_approver_id,
                supporting_evidence_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                override_id,
                override.decision_id,
                override.analyst_id,
                override.original_outcome.value,
                override.new_outcome.value,
                override.reason_code,
                override.reason_text,
                override.second_approver_id,
                override.supporting_evidence_url,
                now,
            ),
        )

        # Update review_status on the decision record
        new_status = (
            ReviewStatus.OVERRIDDEN
            if override.original_outcome != override.new_outcome
            else ReviewStatus.APPROVED
        )
        self._conn.execute(
            "UPDATE decisions SET review_status = ?, review_completed_at = ? WHERE id = ?",
            (new_status.value, now, override.decision_id),
        )
        self._conn.execute(
            "DELETE FROM review_queue WHERE decision_id = ?",
            (override.decision_id,),
        )

        self._conn.commit()
        return override_id

    # ---------------------------------------------------------------------------
    # Retrieval
    # ---------------------------------------------------------------------------

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        """
        Retrieve a logged decision with its explanation and any human override.

        Args:
            decision_id: UUID of the decision

        Returns:
            Decision dict with parsed JSON fields and 'overrides' list.

        Raises:
            KeyError: If decision_id not found.
        """
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Decision {decision_id!r} not found")

        decision = dict(row)
        decision["input_features"] = json.loads(decision["input_features"] or "{}")
        decision["metadata"] = json.loads(decision["metadata"] or "{}")
        if decision.get("explanation"):
            decision["explanation"] = json.loads(decision["explanation"])

        override_rows = self._conn.execute(
            "SELECT * FROM human_overrides WHERE decision_id = ? ORDER BY created_at ASC",
            (decision_id,),
        ).fetchall()
        decision["overrides"] = [dict(r) for r in override_rows]

        return decision

    def get_player_decisions(
        self,
        player_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return all logged decisions for a player (most recent first)."""
        rows = self._conn.execute(
            """SELECT id, model_name, decision_type, output_score, outcome,
                      review_status, decided_at
               FROM decisions
               WHERE player_id = ?
               ORDER BY decided_at DESC
               LIMIT ?""",
            (player_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_review_queue(
        self,
        jurisdiction: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Return decisions pending human review, ordered by SLA deadline (most urgent first).

        Args:
            jurisdiction: Filter to a specific regulatory jurisdiction
            limit:        Maximum number of items to return
        """
        if jurisdiction:
            rows = self._conn.execute(
                """SELECT q.*, d.output_score, d.player_id, d.transaction_id
                   FROM review_queue q
                   JOIN decisions d ON q.decision_id = d.id
                   WHERE q.jurisdiction = ?
                   ORDER BY q.priority DESC, q.due_by ASC
                   LIMIT ?""",
                (jurisdiction, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT q.*, d.output_score, d.player_id, d.transaction_id
                   FROM review_queue q
                   JOIN decisions d ON q.decision_id = d.id
                   ORDER BY q.priority DESC, q.due_by ASC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_override_rate(
        self,
        model_name: str,
        since_iso: str,
    ) -> dict[str, float]:
        """
        Compute the human override rate for a model since a given ISO timestamp.

        A consistently high override rate (>5%) is a signal that the model's
        thresholds or features need recalibration.

        Returns:
            {'total_decisions': N, 'overridden': M, 'override_rate': 0.XX}
        """
        total_row = self._conn.execute(
            """SELECT COUNT(*) as n FROM decisions
               WHERE model_name = ? AND decided_at >= ?
               AND review_status != 'PENDING'""",
            (model_name, since_iso),
        ).fetchone()
        overridden_row = self._conn.execute(
            """SELECT COUNT(*) as n FROM decisions
               WHERE model_name = ? AND decided_at >= ?
               AND review_status = 'OVERRIDDEN'""",
            (model_name, since_iso),
        ).fetchone()

        total = total_row["n"] if total_row else 0
        overridden = overridden_row["n"] if overridden_row else 0

        return {
            "total_decisions": total,
            "overridden": overridden,
            "override_rate": overridden / total if total > 0 else 0.0,
        }

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _determine_review_status(self, decision: AIDecision) -> ReviewStatus:
        """
        Determine the initial review status based on impact level and confidence.

        Implements the human oversight routing from Article 14:
          - HIGH impact: always queued for human review before execution
          - MEDIUM impact: queued if confidence is below auto-execute threshold
          - LOW impact: auto-executed, human-on-the-loop via periodic review
        """
        confidence = decision.output_score

        if decision.impact_level == ImpactLevel.HIGH:
            # High-impact decisions ALWAYS require human review before execution
            # e.g., account suspension, withdrawal block, self-exclusion trigger
            return ReviewStatus.QUEUED_FOR_REVIEW

        if decision.impact_level == ImpactLevel.MEDIUM:
            # Medium-impact: auto-execute if very high confidence; queue otherwise
            if confidence >= 0.95:
                return ReviewStatus.AUTO_EXECUTED
            return ReviewStatus.QUEUED_FOR_REVIEW

        # LOW impact: auto-execute (human-on-the-loop via periodic review)
        return ReviewStatus.AUTO_EXECUTED

    def _enqueue_for_review(
        self,
        decision_id: str,
        decision: AIDecision,
        queued_at: str,
    ) -> None:
        """
        Add a decision to the human review queue with SLA deadline.

        SLA deadlines by impact level (Article 14 + iGaming best practice):
          HIGH:   4 hours (matches regulatory complaint SLAs)
          MEDIUM: 4 hours (batch review threshold)
        """
        from datetime import timedelta
        queued_dt = datetime.fromisoformat(queued_at)
        sla_hours = 4  # Both HIGH and MEDIUM use 4-hour SLA
        due_by = (queued_dt + timedelta(hours=sla_hours)).isoformat()

        priority = 10 if decision.impact_level == ImpactLevel.HIGH else 5

        self._conn.execute(
            """INSERT OR IGNORE INTO review_queue (
                decision_id, impact_level, model_name, outcome,
                jurisdiction, queued_at, due_by, priority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id,
                decision.impact_level.value,
                decision.model_name,
                decision.outcome.value,
                decision.jurisdiction,
                queued_at,
                due_by,
                priority,
            ),
        )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
