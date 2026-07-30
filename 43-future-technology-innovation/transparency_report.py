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
transparency_report.py -- Compliance report generation for EU AI Act.

Generates two report formats for different audiences:

  REGULATOR report:
    Full technical detail — model cards, fairness metrics, override analysis,
    decision volume statistics, audit trail summary. Hand this to the MGA,
    UKGC, or any national competent authority when they audit your AI systems.

  PLAYER report:
    Plain-language summary — what AI systems affect the player, what decisions
    they make, how to challenge a decision, and how to request human review.
    Published in the privacy center and linked from any page where AI affects
    the player experience.

CLI usage::

    python transparency_report.py \\
        --registry-db /var/lib/ai-governance/models.db \\
        --decision-db /var/lib/ai-governance/decisions.db \\
        --report-type regulator \\
        --jurisdiction MGA \\
        --period 2026-Q1 \\
        --output-dir /var/lib/ai-governance/reports/

Chapter 43b: AI Governance for iGaming Platforms under the EU AI Act
Script reference: new-platform/scripts/ai-governance/transparency_report.py
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from model_registry import ModelRegistry, RiskTier


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RegulatorReport:
    """
    Full technical EU AI Act compliance report for regulatory submission.

    Covers all high-risk AI systems deployed in a specified jurisdiction
    and time period.
    """
    report_id: str
    jurisdiction: str
    period: str                         # e.g., '2026-Q1'
    generated_at: str
    generator: str

    # Operational summary
    total_deployed_models: int = 0
    high_risk_models: int = 0
    limited_risk_models: int = 0
    total_decisions: int = 0
    auto_executed_decisions: int = 0
    human_reviewed_decisions: int = 0
    overridden_decisions: int = 0

    model_cards: list[dict[str, Any]] = field(default_factory=list)
    fairness_summaries: list[dict[str, Any]] = field(default_factory=list)
    override_analysis: list[dict[str, Any]] = field(default_factory=list)
    incidents: list[dict[str, Any]] = field(default_factory=list)

    @property
    def override_rate(self) -> float:
        reviewed = self.human_reviewed_decisions
        return self.overridden_decisions / reviewed if reviewed > 0 else 0.0

    @property
    def human_review_rate(self) -> float:
        total = self.total_decisions
        return self.human_reviewed_decisions / total if total > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "report_type": "REGULATOR",
            "jurisdiction": self.jurisdiction,
            "period": self.period,
            "generated_at": self.generated_at,
            "generator": self.generator,
            "summary": {
                "total_deployed_models": self.total_deployed_models,
                "high_risk_models": self.high_risk_models,
                "limited_risk_models": self.limited_risk_models,
                "total_decisions": self.total_decisions,
                "auto_executed": self.auto_executed_decisions,
                "human_reviewed": self.human_reviewed_decisions,
                "overridden": self.overridden_decisions,
                "human_review_rate_pct": round(self.human_review_rate * 100, 2),
                "override_rate_pct": round(self.override_rate * 100, 2),
            },
            "model_cards": self.model_cards,
            "fairness_summaries": self.fairness_summaries,
            "override_analysis": self.override_analysis,
            "incidents": self.incidents,
        }


@dataclass
class PlayerReport:
    """
    Plain-language AI transparency report for players.

    Explains what AI systems operate on the platform, what they decide,
    and how a player can challenge or request human review of a decision.
    """
    report_id: str
    jurisdiction: str
    generated_at: str

    operator_name: str = "iGaming Operator"
    contact_email: str = "compliance@operator.example"
    contact_phone: Optional[str] = None
    regulator_name: str = ""
    regulator_url: str = ""

    ai_systems: list[dict[str, str]] = field(default_factory=list)
    player_rights: list[str] = field(default_factory=list)
    how_to_challenge: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "report_type": "PLAYER",
            "jurisdiction": self.jurisdiction,
            "generated_at": self.generated_at,
            "operator": {
                "name": self.operator_name,
                "contact_email": self.contact_email,
                "contact_phone": self.contact_phone,
            },
            "regulator": {
                "name": self.regulator_name,
                "url": self.regulator_url,
            },
            "ai_systems": self.ai_systems,
            "your_rights": self.player_rights,
            "how_to_challenge_a_decision": self.how_to_challenge,
        }

    def to_plain_text(self) -> str:
        """Generate a human-readable plain-text version for the privacy center."""
        lines = [
            f"AI TRANSPARENCY NOTICE — {self.operator_name}",
            f"Jurisdiction: {self.jurisdiction}",
            f"Last updated: {self.generated_at[:10]}",
            "",
            "We use automated systems (AI/ML) to help run our platform safely "
            "and fairly. This notice explains what those systems do and your rights.",
            "",
            "AI SYSTEMS WE USE:",
        ]

        for system in self.ai_systems:
            lines.append(f"\n  {system['name']}")
            lines.append(f"  What it does: {system['purpose']}")
            lines.append(f"  Decisions: {system['decisions']}")
            lines.append(f"  Human review: {system['human_review']}")

        lines.extend([
            "",
            "YOUR RIGHTS:",
        ])
        for i, right in enumerate(self.player_rights, 1):
            lines.append(f"  {i}. {right}")

        lines.extend([
            "",
            "HOW TO CHALLENGE A DECISION:",
        ])
        for i, step in enumerate(self.how_to_challenge, 1):
            lines.append(f"  {i}. {step}")

        lines.extend([
            "",
            f"Questions? Contact: {self.contact_email}",
        ])
        if self.regulator_name:
            lines.append(
                f"Regulator: {self.regulator_name} — {self.regulator_url}"
            )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TransparencyReportGenerator
# ---------------------------------------------------------------------------

# Static player-rights and how-to-challenge text per jurisdiction.
# In production this would be loaded from a CMS or regulatory content library.
_JURISDICTION_META: dict[str, dict[str, Any]] = {
    "MGA": {
        "regulator_name": "Malta Gaming Authority",
        "regulator_url": "https://www.mga.org.mt",
        "player_rights": [
            "Know when an automated system has influenced a decision about your account.",
            "Request human review of any automated decision that restricts your access "
            "or account.",
            "Receive a plain-language explanation of why a decision was made.",
            "Contest a decision and provide your own information for reconsideration.",
            "Lodge a complaint with the Malta Gaming Authority if you are not satisfied "
            "with the outcome.",
        ],
        "how_to_challenge": [
            "Contact customer support via live chat or email and state that you wish to "
            "challenge an automated decision.",
            "A human reviewer will assess your case within 4 hours for account "
            "restrictions and within 24 hours for other decisions.",
            "You will receive a written explanation of the original decision and the "
            "outcome of the human review.",
            "If you remain unsatisfied, you may escalate to the Malta Gaming Authority "
            "at https://www.mga.org.mt/player-support/",
        ],
    },
    "UKGC": {
        "regulator_name": "UK Gambling Commission",
        "regulator_url": "https://www.gamblingcommission.gov.uk",
        "player_rights": [
            "Be informed when an automated system has made or influenced a decision "
            "about your account.",
            "Request meaningful information about the logic used in automated decisions.",
            "Request human intervention for any decision made solely by automated means.",
            "Express your point of view and contest the decision.",
            "Lodge a complaint with the UK Gambling Commission if unresolved.",
        ],
        "how_to_challenge": [
            "Contact our customer support team and reference the specific decision you "
            "wish to challenge.",
            "A senior member of our team — not an automated system — will review your "
            "case within 24 hours.",
            "You will receive a written explanation and the outcome of the review.",
            "If you are not satisfied, you may escalate to the UK Gambling Commission "
            "or use an approved Alternative Dispute Resolution provider.",
        ],
    },
    "DEFAULT": {
        "regulator_name": "",
        "regulator_url": "",
        "player_rights": [
            "Know when an automated system influences a decision about your account.",
            "Request human review of any automated decision.",
            "Receive an explanation of why a decision was made.",
            "Contest a decision through our complaints process.",
        ],
        "how_to_challenge": [
            "Contact customer support to request a review of any automated decision.",
            "A human reviewer will assess your case within 24 hours.",
            "You will receive a written explanation and the outcome of the review.",
        ],
    },
}

_AI_SYSTEM_DESCRIPTIONS: list[dict[str, str]] = [
    {
        "name": "Transaction Monitoring (AML)",
        "risk_tier": "HIGH",
        "purpose": "Detects patterns associated with money laundering and financial crime "
                   "in deposit and withdrawal transactions.",
        "decisions": "May flag transactions for manual review or temporarily hold "
                     "withdrawals pending investigation.",
        "human_review": "All decisions to block or hold funds are reviewed by a human "
                        "analyst before any action takes effect.",
    },
    {
        "name": "Fraud Detection",
        "risk_tier": "HIGH",
        "purpose": "Identifies account activity consistent with fraud, multi-accounting, "
                   "or bonus abuse.",
        "decisions": "May restrict account access, void bonuses, or flag accounts for "
                     "investigation.",
        "human_review": "Significant restrictions are subject to human review. "
                        "You can request review of any restriction.",
    },
    {
        "name": "Responsible Gaming System",
        "risk_tier": "HIGH",
        "purpose": "Identifies patterns that may indicate problem gambling behaviour to "
                   "enable timely interventions.",
        "decisions": "May trigger a check-in from a responsible gaming specialist, "
                     "suggest or apply cooling-off periods.",
        "human_review": "A trained responsible gaming specialist reviews all triggered "
                        "interventions within 24 hours.",
    },
    {
        "name": "Identity Verification (KYC)",
        "risk_tier": "HIGH",
        "purpose": "Verifies player identity documents and matches identity information "
                   "against sanctions and PEP databases.",
        "decisions": "May approve, reject, or escalate identity verification requests.",
        "human_review": "Rejections and escalations are reviewed by a compliance officer "
                        "before any account restriction takes effect.",
    },
    {
        "name": "Game Recommendations",
        "risk_tier": "LIMITED",
        "purpose": "Personalises game suggestions based on your play history and preferences.",
        "decisions": "Influences which games are shown on your home screen. "
                     "No account restrictions are made by this system.",
        "human_review": "No individual human review — aggregate performance is monitored "
                        "monthly. You can disable personalisation in account settings.",
    },
    {
        "name": "Customer Support Routing",
        "risk_tier": "LIMITED",
        "purpose": "Routes support enquiries to the most suitable team based on the "
                   "content of your message.",
        "decisions": "Determines which support team receives your enquiry. "
                     "You can always request a specific team.",
        "human_review": "Routing performance is monitored weekly. All enquiries are "
                        "handled by human agents.",
    },
]


class TransparencyReportGenerator:
    """
    Generate EU AI Act transparency reports for regulators and players.

    Reads model metadata from the ModelRegistry and (optionally) decision
    volume statistics from the DecisionLogger database to produce structured
    compliance reports.

    Usage::

        gen = TransparencyReportGenerator(
            registry_db="models.db",
            decision_db="decisions.db",
        )

        # Regulator report for MGA audit
        reg_report = gen.generate_regulator_report(
            jurisdiction="MGA",
            period="2026-Q1",
        )
        gen.save_report(reg_report.to_dict(), "/reports/mga_2026q1_regulator.json")

        # Player-facing report
        player_report = gen.generate_player_report(jurisdiction="MGA")
        gen.save_report(player_report.to_dict(), "/reports/mga_player_notice.json")
        print(player_report.to_plain_text())
    """

    def __init__(
        self,
        registry_db: str = ":memory:",
        decision_db: Optional[str] = None,
        generator_id: str = "transparency-report-v1.0",
    ) -> None:
        self.registry = ModelRegistry(registry_db)
        self.decision_db = decision_db
        self.generator_id = generator_id
        self._decision_conn: Optional[sqlite3.Connection] = None
        if decision_db and os.path.exists(decision_db):
            self._decision_conn = sqlite3.connect(decision_db, check_same_thread=False)
            self._decision_conn.row_factory = sqlite3.Row

    def generate_regulator_report(
        self,
        jurisdiction: str,
        period: str,
        period_start_iso: Optional[str] = None,
        period_end_iso: Optional[str] = None,
    ) -> RegulatorReport:
        """
        Generate a full technical compliance report for a regulator.

        Args:
            jurisdiction:     Regulatory jurisdiction (MGA, UKGC, NJ DGE, etc.)
            period:           Human-readable period label (e.g., '2026-Q1')
            period_start_iso: ISO timestamp for decision statistics start
            period_end_iso:   ISO timestamp for decision statistics end
                              (defaults to now if period_start_iso is set)

        Returns:
            RegulatorReport with all model cards, fairness summaries, and statistics.
        """
        now = datetime.now(tz=timezone.utc).isoformat()
        report = RegulatorReport(
            report_id=str(uuid.uuid4()),
            jurisdiction=jurisdiction,
            period=period,
            generated_at=now,
            generator=self.generator_id,
        )

        # Gather all deployed models
        deployed_models = self._get_deployed_models()
        report.total_deployed_models = len(deployed_models)

        for model in deployed_models:
            risk_tier = model.get("risk_tier", "MINIMAL")
            if risk_tier == RiskTier.HIGH.value:
                report.high_risk_models += 1
            elif risk_tier == RiskTier.LIMITED.value:
                report.limited_risk_models += 1

            # Build model card
            model_card = self._build_model_card(model)
            report.model_cards.append(model_card)

            # Bias audit summary from latest audit
            if model.get("latest_bias_audit"):
                report.fairness_summaries.append(
                    self._build_fairness_summary(model)
                )

        # Decision statistics from DecisionLogger DB
        if self._decision_conn and period_start_iso:
            end_iso = period_end_iso or now
            stats = self._get_decision_stats(jurisdiction, period_start_iso, end_iso)
            report.total_decisions = stats.get("total", 0)
            report.auto_executed_decisions = stats.get("auto_executed", 0)
            report.human_reviewed_decisions = stats.get("human_reviewed", 0)
            report.overridden_decisions = stats.get("overridden", 0)
            report.override_analysis = stats.get("by_model", [])

        return report

    def generate_player_report(
        self,
        jurisdiction: str,
        operator_name: str = "iGaming Operator",
        contact_email: str = "compliance@operator.example",
        contact_phone: Optional[str] = None,
    ) -> PlayerReport:
        """
        Generate a plain-language AI transparency notice for players.

        Args:
            jurisdiction:   Regulatory jurisdiction (used for jurisdiction-specific rights)
            operator_name:  Trading name of the operator
            contact_email:  Compliance/support contact email
            contact_phone:  Optional phone contact

        Returns:
            PlayerReport suitable for publication in the privacy center.
        """
        now = datetime.now(tz=timezone.utc).isoformat()
        meta = _JURISDICTION_META.get(jurisdiction, _JURISDICTION_META["DEFAULT"])

        report = PlayerReport(
            report_id=str(uuid.uuid4()),
            jurisdiction=jurisdiction,
            generated_at=now,
            operator_name=operator_name,
            contact_email=contact_email,
            contact_phone=contact_phone,
            regulator_name=meta["regulator_name"],
            regulator_url=meta["regulator_url"],
            ai_systems=list(_AI_SYSTEM_DESCRIPTIONS),
            player_rights=meta["player_rights"],
            how_to_challenge=meta["how_to_challenge"],
        )

        return report

    @staticmethod
    def save_report(report_dict: dict[str, Any], output_path: str) -> None:
        """
        Persist a report to a JSON file.

        Creates parent directories if they don't exist.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, indent=2, ensure_ascii=False)
        print(f"Report saved to {output_path}")

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _get_deployed_models(self) -> list[dict[str, Any]]:
        """Retrieve all currently deployed models from the registry."""
        # ModelRegistry doesn't expose a list_all, so we query SQLite directly
        conn = self.registry._conn
        rows = conn.execute(
            "SELECT id FROM models WHERE status IN ('DEPLOYED', 'MONITORING')"
        ).fetchall()
        models = []
        for row in rows:
            try:
                model = self.registry.get_model(row["id"])
                models.append(model)
            except KeyError:
                pass
        return models

    def _build_model_card(self, model: dict[str, Any]) -> dict[str, Any]:
        """Build a structured model card from registry metadata."""
        return {
            "model_id": model["id"],
            "name": model["name"],
            "version": model["version"],
            "risk_tier": model["risk_tier"],
            "status": model["status"],
            "owner": model["owner"],
            "purpose": model["purpose"],
            "architecture": model.get("architecture", "Not documented"),
            "training_data": model.get("training_data_description", "Not documented"),
            "performance_metrics": model.get("performance_metrics", {}),
            "jurisdictions": model.get("jurisdictions", []),
            "human_oversight_configured": bool(model.get("human_oversight_configured")),
            "conformity_assessment_date": model.get("conformity_assessment_date"),
            "deployed_at": model.get("deployed_at"),
            "latest_bias_audit": model.get("latest_bias_audit"),
        }

    def _build_fairness_summary(self, model: dict[str, Any]) -> dict[str, Any]:
        """Build a fairness metrics summary from the latest bias audit."""
        audit = model["latest_bias_audit"]
        if not audit:
            return {"model_name": model["name"], "status": "No audit recorded"}

        return {
            "model_name": model["name"],
            "model_version": model["version"],
            "audit_date": audit.get("created_at", "")[:10],
            "auditor": audit.get("auditor"),
            "demographic_parity_diff": audit.get("demographic_parity_diff"),
            "equalized_odds_diff": audit.get("equalized_odds_diff"),
            "predictive_parity_diff": audit.get("predictive_parity_diff"),
            "pass_fail": audit.get("pass_fail"),
            "notes": audit.get("notes"),
        }

    def _get_decision_stats(
        self,
        jurisdiction: str,
        start_iso: str,
        end_iso: str,
    ) -> dict[str, Any]:
        """Query decision statistics from the DecisionLogger database."""
        if not self._decision_conn:
            return {}

        conn = self._decision_conn
        total_row = conn.execute(
            """SELECT COUNT(*) as n FROM decisions
               WHERE jurisdiction = ? AND decided_at BETWEEN ? AND ?""",
            (jurisdiction, start_iso, end_iso),
        ).fetchone()

        auto_row = conn.execute(
            """SELECT COUNT(*) as n FROM decisions
               WHERE jurisdiction = ? AND decided_at BETWEEN ? AND ?
               AND review_status = 'AUTO_EXECUTED'""",
            (jurisdiction, start_iso, end_iso),
        ).fetchone()

        reviewed_row = conn.execute(
            """SELECT COUNT(*) as n FROM decisions
               WHERE jurisdiction = ? AND decided_at BETWEEN ? AND ?
               AND review_status IN ('APPROVED', 'OVERRIDDEN', 'DISMISSED')""",
            (jurisdiction, start_iso, end_iso),
        ).fetchone()

        overridden_row = conn.execute(
            """SELECT COUNT(*) as n FROM decisions
               WHERE jurisdiction = ? AND decided_at BETWEEN ? AND ?
               AND review_status = 'OVERRIDDEN'""",
            (jurisdiction, start_iso, end_iso),
        ).fetchone()

        by_model_rows = conn.execute(
            """SELECT model_name,
                      COUNT(*) as total,
                      SUM(CASE WHEN review_status = 'OVERRIDDEN' THEN 1 ELSE 0 END) as overridden
               FROM decisions
               WHERE jurisdiction = ? AND decided_at BETWEEN ? AND ?
               AND review_status != 'PENDING'
               GROUP BY model_name""",
            (jurisdiction, start_iso, end_iso),
        ).fetchall()

        return {
            "total": total_row["n"] if total_row else 0,
            "auto_executed": auto_row["n"] if auto_row else 0,
            "human_reviewed": reviewed_row["n"] if reviewed_row else 0,
            "overridden": overridden_row["n"] if overridden_row else 0,
            "by_model": [
                {
                    "model_name": r["model_name"],
                    "total_decisions": r["total"],
                    "overridden": r["overridden"],
                    "override_rate_pct": round(
                        r["overridden"] / r["total"] * 100, 2
                    ) if r["total"] > 0 else 0.0,
                }
                for r in by_model_rows
            ],
        }

    def close(self) -> None:
        """Release all database resources."""
        self.registry.close()
        if self._decision_conn:
            self._decision_conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate EU AI Act transparency reports"
    )
    parser.add_argument("--registry-db", required=True)
    parser.add_argument("--decision-db")
    parser.add_argument(
        "--report-type",
        choices=["regulator", "player", "both"],
        default="both",
    )
    parser.add_argument("--jurisdiction", default="MGA")
    parser.add_argument("--period", default="2026-Q1")
    parser.add_argument("--period-start")
    parser.add_argument("--period-end")
    parser.add_argument("--operator-name", default="iGaming Operator")
    parser.add_argument("--contact-email", default="compliance@operator.example")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    gen = TransparencyReportGenerator(
        registry_db=args.registry_db,
        decision_db=args.decision_db,
    )

    safe_jurisdiction = args.jurisdiction.lower().replace(" ", "_")

    if args.report_type in ("regulator", "both"):
        reg_report = gen.generate_regulator_report(
            jurisdiction=args.jurisdiction,
            period=args.period,
            period_start_iso=args.period_start,
            period_end_iso=args.period_end,
        )
        out_path = os.path.join(
            args.output_dir,
            f"{safe_jurisdiction}_{args.period}_regulator.json",
        )
        TransparencyReportGenerator.save_report(reg_report.to_dict(), out_path)
        print(f"Regulator report: {reg_report.total_deployed_models} deployed models, "
              f"{reg_report.total_decisions:,} decisions")

    if args.report_type in ("player", "both"):
        player_report = gen.generate_player_report(
            jurisdiction=args.jurisdiction,
            operator_name=args.operator_name,
            contact_email=args.contact_email,
        )
        json_path = os.path.join(
            args.output_dir,
            f"{safe_jurisdiction}_player_notice.json",
        )
        txt_path = os.path.join(
            args.output_dir,
            f"{safe_jurisdiction}_player_notice.txt",
        )
        TransparencyReportGenerator.save_report(player_report.to_dict(), json_path)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(player_report.to_plain_text())
        print(f"Player report saved to {txt_path}")

    gen.close()


if __name__ == "__main__":
    _main()
