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
AI Decision Logger for iGaming Platform.

Provides immutable audit trail logging for every AI-driven decision
in the casino platform (AML scoring, fraud detection, responsible
gaming triggers, KYC verification, etc.).

Satisfies EU AI Act Article 12 (record-keeping), GDPR Article 22
(automated decision-making documentation), and jurisdiction-specific
retention requirements (MGA 10yr, UKGC 7yr, SGA 7yr, NJ DGE 10yr).

Usage:
    logger = DecisionLogger("/var/lib/ai-governance/decisions.db")

    decision = AIDecision(
        model_name="aml_scoring_v3.2",
        model_version="3.2.1",
        player_id="P-29481",
        input_features={"amount": 5000, "frequency_24h": 12, ...},
        output_score=0.82,
        threshold=0.70,
        decision="FLAG_FOR_REVIEW",
        explanation=Explanation(
            method="shap",
            top_features=[
                FeatureContribution("deposit_frequency_24h", 0.23, "High deposit frequency"),
                FeatureContribution("amount_vs_avg", 0.18, "Amount 5x above average"),
            ],
            base_score=0.12,
        ),
        jurisdiction="MGA",
    )

    decision_id = logger.log(decision)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class FeatureContribution:
    """A single feature's contribution to a model prediction."""

    feature_name: str
    impact: float
    human_readable: str


@dataclass
class Explanation:
    """Model explanation for a single decision."""

    method: str  # "shap", "lime", "rule_based"
    top_features: list[FeatureContribution]
    base_score: float = 0.0
    confidence_interval: tuple[float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "top_features": [
                {"feature": f.feature_name, "impact": f.impact, "description": f.human_readable}
                for f in self.top_features
            ],
            "base_score": self.base_score,
            "confidence_interval": list(self.confidence_interval) if self.confidence_interval else None,
        }

    def player_facing_text(self) -> str:
        """Generate a plain-language explanation for the player."""
        if not self.top_features:
            return "This decision was made by our automated security system."

        reasons = [f.human_readable for f in self.top_features[:3]]
        reasons_text = "; ".join(reasons)
        return (
            f"This decision was made because: {reasons_text}. "
            "You have the right to request a human review of this decision."
        )


@dataclass
class HumanOverride:
    """Record of a human analyst overriding an AI decision."""

    analyst_id: str
    override_action: str
    reason_code: str
    reason_text: str
    timestamp: str = ""
    second_reviewer_id: str | None = None

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class AIDecision:
    """A single AI-driven decision in the casino platform."""

    model_name: str
    model_version: str
    player_id: str
    input_features: dict[str, Any]
    output_score: float
    threshold: float
    decision: str  # "APPROVE", "FLAG_FOR_REVIEW", "BLOCK", "ESCALATE"
    explanation: Explanation
    jurisdiction: str
    decision_id: str = ""
    timestamp: str = ""
    impact_level: str = "MEDIUM"  # "LOW", "MEDIUM", "HIGH"
    human_override: HumanOverride | None = None
    player_notified: bool = False
    notification_text: str = ""

    def __post_init__(self) -> None:
        if not self.decision_id:
            self.decision_id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id      TEXT PRIMARY KEY,
    model_name       TEXT NOT NULL,
    model_version    TEXT NOT NULL,
    player_id        TEXT NOT NULL,
    input_features   TEXT NOT NULL,
    output_score     REAL NOT NULL,
    threshold        REAL NOT NULL,
    decision         TEXT NOT NULL,
    explanation      TEXT NOT NULL,
    jurisdiction     TEXT NOT NULL,
    impact_level     TEXT NOT NULL DEFAULT 'MEDIUM',
    timestamp        TEXT NOT NULL,
    integrity_hash   TEXT NOT NULL,
    player_notified  INTEGER NOT NULL DEFAULT 0,
    notification_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS human_overrides (
    override_id      TEXT PRIMARY KEY,
    decision_id      TEXT NOT NULL,
    analyst_id       TEXT NOT NULL,
    override_action  TEXT NOT NULL,
    reason_code      TEXT NOT NULL,
    reason_text      TEXT NOT NULL DEFAULT '',
    second_reviewer_id TEXT,
    timestamp        TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_model ON decisions(model_name);
CREATE INDEX IF NOT EXISTS idx_decisions_player ON decisions(player_id);
CREATE INDEX IF NOT EXISTS idx_decisions_time ON decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_jurisdiction ON decisions(jurisdiction);
CREATE INDEX IF NOT EXISTS idx_decisions_decision ON decisions(decision);
CREATE INDEX IF NOT EXISTS idx_overrides_decision ON human_overrides(decision_id);
"""


class DecisionLogger:
    """Immutable audit trail for AI-driven decisions.

    Every decision is stored with an integrity hash to detect tampering.
    Supports querying by model, player, time range, and jurisdiction.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def _compute_hash(self, decision: AIDecision) -> str:
        """Compute integrity hash for tamper detection."""
        payload = json.dumps({
            "decision_id": decision.decision_id,
            "model_name": decision.model_name,
            "model_version": decision.model_version,
            "player_id": decision.player_id,
            "output_score": decision.output_score,
            "decision": decision.decision,
            "timestamp": decision.timestamp,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def log(self, decision: AIDecision) -> str:
        """Log an AI decision with immutable integrity hash.

        Returns the decision_id.
        """
        integrity_hash = self._compute_hash(decision)

        # Redact PII from stored features (keep feature names, hash values)
        redacted_features = self._redact_pii(decision.input_features)

        self._conn.execute(
            "INSERT INTO decisions "
            "(decision_id, model_name, model_version, player_id, input_features, "
            " output_score, threshold, decision, explanation, jurisdiction, "
            " impact_level, timestamp, integrity_hash, player_notified, notification_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision.decision_id, decision.model_name, decision.model_version,
                decision.player_id, json.dumps(redacted_features),
                decision.output_score, decision.threshold, decision.decision,
                json.dumps(decision.explanation.to_dict()), decision.jurisdiction,
                decision.impact_level, decision.timestamp, integrity_hash,
                int(decision.player_notified), decision.notification_text,
            ),
        )
        self._conn.commit()
        return decision.decision_id

    def log_override(self, decision_id: str, override: HumanOverride) -> str:
        """Log a human override of an AI decision."""
        override_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO human_overrides "
            "(override_id, decision_id, analyst_id, override_action, "
            " reason_code, reason_text, second_reviewer_id, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                override_id, decision_id, override.analyst_id,
                override.override_action, override.reason_code,
                override.reason_text, override.second_reviewer_id,
                override.timestamp,
            ),
        )
        self._conn.commit()
        return override_id

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        """Retrieve a decision by ID, including any overrides."""
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Decision '{decision_id}' not found.")

        result = dict(row)
        result["input_features"] = json.loads(result["input_features"])
        result["explanation"] = json.loads(result["explanation"])

        # Attach overrides
        overrides = self._conn.execute(
            "SELECT * FROM human_overrides WHERE decision_id = ? ORDER BY timestamp",
            (decision_id,),
        ).fetchall()
        result["human_overrides"] = [dict(o) for o in overrides]

        # Verify integrity
        result["integrity_valid"] = self._verify_integrity(result)

        return result

    def query_decisions(
        self,
        model_name: str | None = None,
        player_id: str | None = None,
        jurisdiction: str | None = None,
        decision: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query decisions with filters."""
        clauses: list[str] = []
        params: list[Any] = []

        if model_name:
            clauses.append("model_name = ?")
            params.append(model_name)
        if player_id:
            clauses.append("player_id = ?")
            params.append(player_id)
        if jurisdiction:
            clauses.append("jurisdiction = ?")
            params.append(jurisdiction)
        if decision:
            clauses.append("decision = ?")
            params.append(decision)
        if start_time:
            clauses.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            clauses.append("timestamp <= ?")
            params.append(end_time)

        where = " AND ".join(clauses) if clauses else "1=1"
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM decisions WHERE {where} ORDER BY timestamp DESC LIMIT ?",
            params,
        ).fetchall()

        results = []
        for row in rows:
            r = dict(row)
            r["input_features"] = json.loads(r["input_features"])
            r["explanation"] = json.loads(r["explanation"])
            results.append(r)
        return results

    def get_override_rate(
        self,
        model_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        """Calculate the human override rate for a model."""
        time_clause = ""
        params: list[Any] = [model_name]
        if start_time:
            time_clause += " AND d.timestamp >= ?"
            params.append(start_time)
        if end_time:
            time_clause += " AND d.timestamp <= ?"
            params.append(end_time)

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM decisions d WHERE d.model_name = ?{time_clause}",
            params,
        ).fetchone()[0]

        overridden = self._conn.execute(
            f"SELECT COUNT(DISTINCT d.decision_id) FROM decisions d "
            f"JOIN human_overrides h ON d.decision_id = h.decision_id "
            f"WHERE d.model_name = ?{time_clause}",
            params,
        ).fetchone()[0]

        rate = overridden / total if total > 0 else 0.0

        return {
            "model_name": model_name,
            "total_decisions": total,
            "overridden": overridden,
            "override_rate": round(rate, 4),
        }

    def _redact_pii(self, features: dict[str, Any]) -> dict[str, Any]:
        """Redact personally identifiable information from features.

        Keeps feature names and non-PII values. Hashes values for PII fields.
        """
        pii_fields = {
            "email", "name", "first_name", "last_name", "phone",
            "address", "ip_address", "card_number", "iban",
            "date_of_birth", "ssn", "national_id",
        }
        redacted = {}
        for key, value in features.items():
            if key.lower() in pii_fields:
                redacted[key] = f"[REDACTED:{hashlib.sha256(str(value).encode()).hexdigest()[:12]}]"
            else:
                redacted[key] = value
        return redacted

    def _verify_integrity(self, record: dict[str, Any]) -> bool:
        """Verify the integrity hash of a stored decision."""
        payload = json.dumps({
            "decision_id": record["decision_id"],
            "model_name": record["model_name"],
            "model_version": record["model_version"],
            "player_id": record["player_id"],
            "output_score": record["output_score"],
            "decision": record["decision"],
            "timestamp": record["timestamp"],
        }, sort_keys=True)
        expected = hashlib.sha256(payload.encode()).hexdigest()
        return expected == record["integrity_hash"]

    def get_statistics(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        """Get aggregate statistics on AI decisions."""
        time_clause = ""
        params: list[Any] = []
        if start_time:
            time_clause += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            time_clause += " AND timestamp <= ?"
            params.append(end_time)

        where = f"1=1{time_clause}"

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM decisions WHERE {where}", params
        ).fetchone()[0]

        by_decision = self._conn.execute(
            f"SELECT decision, COUNT(*) as cnt FROM decisions WHERE {where} "
            f"GROUP BY decision ORDER BY cnt DESC", params
        ).fetchall()

        by_model = self._conn.execute(
            f"SELECT model_name, COUNT(*) as cnt FROM decisions WHERE {where} "
            f"GROUP BY model_name ORDER BY cnt DESC", params
        ).fetchall()

        by_impact = self._conn.execute(
            f"SELECT impact_level, COUNT(*) as cnt FROM decisions WHERE {where} "
            f"GROUP BY impact_level ORDER BY cnt DESC", params
        ).fetchall()

        return {
            "total_decisions": total,
            "by_decision": {row[0]: row[1] for row in by_decision},
            "by_model": {row[0]: row[1] for row in by_model},
            "by_impact": {row[0]: row[1] for row in by_impact},
        }

    def close(self) -> None:
        self._conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Decision Logger CLI")
    parser.add_argument("--db-path", default="decisions.db")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize the decision log database")

    stats_p = sub.add_parser("stats", help="Show decision statistics")
    stats_p.add_argument("--start", help="Start time (ISO 8601)")
    stats_p.add_argument("--end", help="End time (ISO 8601)")

    query_p = sub.add_parser("query", help="Query decisions")
    query_p.add_argument("--model", help="Filter by model name")
    query_p.add_argument("--player", help="Filter by player ID")
    query_p.add_argument("--limit", type=int, default=20)

    get_p = sub.add_parser("get", help="Get a specific decision")
    get_p.add_argument("decision_id", help="Decision ID")

    args = parser.parse_args()
    logger = DecisionLogger(args.db_path)

    if args.command == "init":
        print(f"Decision log initialized at {args.db_path}")

    elif args.command == "stats":
        stats = logger.get_statistics(args.start, args.end)
        print(f"Total decisions: {stats['total_decisions']}")
        print("\nBy outcome:")
        for k, v in stats["by_decision"].items():
            print(f"  {k}: {v}")
        print("\nBy model:")
        for k, v in stats["by_model"].items():
            print(f"  {k}: {v}")

    elif args.command == "query":
        decisions = logger.query_decisions(
            model_name=args.model,
            player_id=args.player,
            limit=args.limit,
        )
        for d in decisions:
            print(f"  {d['decision_id'][:8]}  {d['model_name']:25s}  "
                  f"{d['decision']:15s}  score={d['output_score']:.2f}  "
                  f"{d['timestamp']}")

    elif args.command == "get":
        d = logger.get_decision(args.decision_id)
        print(json.dumps(d, indent=2))

    logger.close()


# ---------------------------------------------------------------------------
# Public compatibility layer
# ---------------------------------------------------------------------------
#
# The original `DecisionLogger` API (above) is built around the `AIDecision`
# dataclass — the full record carrying integrity hash, explanation, override
# history, etc. The governance chapter tests (and a few downstream callers)
# use a leaner, higher-level API that stores one row per decision with a
# SHA-256 input hash for PII redaction, plus query helpers grouped by
# player / model / date range and CSV/JSON export.
#
# Rather than fork the storage into a second table, we expose the shim as
# a thin facade on top of the same sqlite connection, adding a single new
# table (`decision_events`) so both APIs can coexist without stepping on
# each other's rows.

# Decision types accepted by `AIDecisionLogger.log_decision`. This is
# intentionally a superset of the `AIDecision.decision` field above: the
# governance tests exercise a taxonomy of *events* (AML flagged, bonus
# offered, KYC verified, ...) rather than the tri-state outcome.
VALID_DECISION_TYPES: frozenset[str] = frozenset(
    {
        "aml_flag",
        "rg_trigger",
        "kyc_verify",
        "bonus_offer",
        "fraud_alert",
        # Outcome strings from the AIDecision.decision contract, kept so
        # callers that mix the two APIs don't get spurious validation
        # errors.
        "APPROVE",
        "FLAG_FOR_REVIEW",
        "BLOCK",
        "ESCALATE",
    }
)


_EVENTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decision_events (
    decision_id      TEXT PRIMARY KEY,
    model_name       TEXT NOT NULL,
    model_version    TEXT NOT NULL,
    input_hash       TEXT NOT NULL,
    output           TEXT NOT NULL,
    affected_player_id TEXT NOT NULL,
    decision_type    TEXT NOT NULL,
    timestamp        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_player ON decision_events(affected_player_id);
CREATE INDEX IF NOT EXISTS idx_events_model ON decision_events(model_name);
CREATE INDEX IF NOT EXISTS idx_events_time ON decision_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON decision_events(decision_type);
"""


def _hash_input_features(features: dict[str, Any]) -> str:
    """SHA-256 hash of a deterministic JSON encoding of the features.

    Returns a 64-character hex digest so callers never see raw PII in the
    audit trail — only the hash is retained, satisfying GDPR Art. 17
    (right to erasure) while preserving the ability to re-verify inputs
    when replaying a decision in front of a regulator.
    """
    payload = json.dumps(features, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class AIDecisionLogger(DecisionLogger):
    """Event-oriented decision log used by EU AI Act governance tooling.

    Inherits the full `DecisionLogger` storage so the integrity-hashed
    `AIDecision` pipeline keeps working, and adds a parallel
    `decision_events` table for the lighter-weight API expected by the
    governance chapter tests (log_decision / query_by_* / export_* /
    enforce_retention).
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        super().__init__(db_path=db_path)
        self._conn.executescript(_EVENTS_SCHEMA_SQL)
        self._conn.commit()

    def log_decision(
        self,
        *,
        model_name: str,
        model_version: str,
        input_features: dict[str, Any],
        output: str,
        affected_player_id: str,
        decision_type: str,
    ) -> str:
        """Log a single AI-driven decision event.

        PII in `input_features` is never persisted raw — only the SHA-256
        hash of the JSON-encoded feature set is stored. Raises
        ValueError if `decision_type` is not in VALID_DECISION_TYPES.
        """
        if decision_type not in VALID_DECISION_TYPES:
            raise ValueError(
                f"decision_type must be one of {sorted(VALID_DECISION_TYPES)}, "
                f"got {decision_type!r}"
            )

        decision_id = str(uuid.uuid4())
        input_hash = _hash_input_features(input_features)
        timestamp = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "INSERT INTO decision_events "
            "(decision_id, model_name, model_version, input_hash, output, "
            " affected_player_id, decision_type, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id, model_name, model_version, input_hash, output,
                affected_player_id, decision_type, timestamp,
            ),
        )
        self._conn.commit()
        return decision_id

    def _events_where(
        self,
        clauses: list[str],
        params: list[Any],
    ) -> list[dict[str, Any]]:
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM decision_events WHERE {where} ORDER BY timestamp DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def query_by_player(self, player_id: str) -> list[dict[str, Any]]:
        return self._events_where(
            ["affected_player_id = ?"], [player_id],
        )

    def query_by_model(self, model_name: str) -> list[dict[str, Any]]:
        return self._events_where(["model_name = ?"], [model_name])

    def query_by_date_range(
        self,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        return self._events_where(
            ["timestamp >= ?", "timestamp <= ?"],
            [start, end + "T23:59:59Z"],
        )

    def export_csv(self, decisions: list[dict[str, Any]]) -> str:
        """Serialize a list of decision events to CSV."""
        import csv
        import io

        if not decisions:
            return ""

        columns = [
            "decision_id", "model_name", "model_version", "input_hash",
            "output", "affected_player_id", "decision_type", "timestamp",
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in decisions:
            writer.writerow({k: row.get(k, "") for k in columns})
        return buf.getvalue()

    def export_json(self, decisions: list[dict[str, Any]]) -> str:
        """Serialize a list of decision events to JSON."""
        return json.dumps(decisions, indent=2, sort_keys=True, default=str)

    def enforce_retention(self, max_age_days: int) -> int:
        """Delete decision events older than `max_age_days`.

        Returns the number of rows removed. `max_age_days=0` deletes
        everything up to and including the current instant — useful for
        GDPR Art. 17 erasure requests and for exercising retention
        policies under test.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        cutoff_iso = cutoff.isoformat()

        cur = self._conn.execute(
            "DELETE FROM decision_events WHERE timestamp <= ?",
            (cutoff_iso,),
        )
        self._conn.commit()
        return cur.rowcount or 0


if __name__ == "__main__":
    main()
