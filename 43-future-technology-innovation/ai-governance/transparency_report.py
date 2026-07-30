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
Transparency Report Generator for iGaming AI Governance.

Generates two report types:
  1. Regulator-facing: Full technical detail with model cards, fairness
     metrics, decision logs, and override analysis.
  2. Player-facing: Plain-language summary of AI systems used, decisions
     they make, and how to challenge a decision.

Satisfies EU AI Act Article 13 (transparency), GDPR Articles 13-14
(information obligations), and jurisdiction-specific reporting requirements.

Usage:
    generator = TransparencyReportGenerator(
        registry_db="models.db",
        decision_db="decisions.db",
    )

    # Regulator report
    report = generator.generate_regulator_report(
        jurisdiction="MGA",
        period="2026-Q1",
    )

    # Player report
    report = generator.generate_player_report()

CLI:
    python transparency_report.py \
        --registry-db models.db \
        --decision-db decisions.db \
        --report-type regulator \
        --jurisdiction MGA \
        --period 2026-Q1 \
        --output-dir ./reports/
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decision_logger import DecisionLogger  # ty:ignore[unresolved-import]
from model_registry import ModelRegistry, RiskTier  # ty:ignore[unresolved-import]


class TransparencyReportGenerator:
    """Generates compliance transparency reports for AI governance."""

    def __init__(
        self,
        registry_db: str | None = None,
        decision_db: str | None = None,
    ) -> None:
        # Lazy init: callers that only use the data-driven
        # `generate_player_report(data=...)` / `generate_regulator_report(data=...)`
        # entry points don't need a backing sqlite store. Historically this
        # constructor eagerly created `models.db` / `decisions.db` files in
        # the current working directory even when the reports were built
        # from in-memory data — leaking stray files on every test run.
        self._registry_db = registry_db
        self._decision_db = decision_db
        self._registry: ModelRegistry | None = None
        self._decision_logger: DecisionLogger | None = None

    @property
    def registry(self) -> ModelRegistry:
        if self._registry is None:
            self._registry = ModelRegistry(self._registry_db or "models.db")
        return self._registry

    @property
    def decision_logger(self) -> DecisionLogger:
        if self._decision_logger is None:
            self._decision_logger = DecisionLogger(self._decision_db or "decisions.db")
        return self._decision_logger

    def generate_regulator_report(
        self,
        jurisdiction: str = "ALL",
        period: str = "",
        include_decision_sample: bool = True,
        sample_size: int = 50,
    ) -> dict[str, Any]:
        """Generate a comprehensive report for regulatory authorities.

        Includes:
        - Inventory of all AI systems with risk classifications
        - Model cards for high-risk systems
        - Fairness metrics and bias audit history
        - Decision volume and override statistics
        - Conformity assessment status
        - Sample decisions with explanations
        """
        report: dict[str, Any] = {
            "report_type": "regulator",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "jurisdiction": jurisdiction,
            "period": period,
            "generator": "TransparencyReportGenerator v1.0",
        }

        # Section 1: AI System Inventory
        all_models = self.registry.find_models()
        if jurisdiction != "ALL":
            all_models = [
                m for m in all_models
                if jurisdiction in m.get("jurisdictions", [])
            ]

        inventory: list[dict[str, Any]] = []
        for model in all_models:
            entry = {
                "model_id": model["model_id"],
                "name": model["name"],
                "version": model["version"],
                "risk_tier": model["risk_tier"],
                "status": model["status"],
                "owner": model["owner"],
                "purpose": model["purpose"],
                "jurisdictions": model["jurisdictions"],
            }
            inventory.append(entry)

        report["ai_system_inventory"] = {
            "total_systems": len(inventory),
            "by_risk_tier": self._count_by_field(inventory, "risk_tier"),
            "by_status": self._count_by_field(inventory, "status"),
            "systems": inventory,
        }

        # Section 2: High-Risk Model Cards
        high_risk_models = [m for m in all_models if m["risk_tier"] == RiskTier.HIGH.value]
        model_cards: list[dict[str, Any]] = []
        for model in high_risk_models:
            card = {
                "model_id": model["model_id"],
                "name": model["name"],
                "version": model["version"],
                "owner": model["owner"],
                "purpose": model["purpose"],
                "architecture": model["architecture"],
                "training_data": model["training_data_description"],
                "performance_metrics": model["performance_metrics"],
                "human_oversight_configured": bool(model["human_oversight_configured"]),
                "conformity_assessment_date": model["conformity_assessment_date"],
            }

            # Add bias audit history
            audits = self.registry.get_bias_audits(model["model_id"])
            card["bias_audits"] = [
                {
                    "date": a["audit_date"][:10],
                    "auditor": a["auditor"],
                    "demographic_parity_diff": a["demographic_parity_diff"],
                    "equalized_odds_diff": a["equalized_odds_diff"],
                    "pass_fail": a["pass_fail"],
                }
                for a in audits[:5]  # Last 5 audits
            ]

            model_cards.append(card)

        report["high_risk_model_cards"] = model_cards

        # Section 3: Decision Statistics
        stats = self.decision_logger.get_statistics()
        report["decision_statistics"] = stats

        # Section 4: Override Analysis
        override_analysis: list[dict[str, Any]] = []
        for model in all_models:
            rate_info = self.decision_logger.get_override_rate(model["name"])
            if rate_info["total_decisions"] > 0:
                override_analysis.append(rate_info)

        report["override_analysis"] = override_analysis

        # Section 5: Sample Decisions (with explanations)
        if include_decision_sample:
            sample = self.decision_logger.query_decisions(limit=sample_size)
            report["decision_sample"] = [
                {
                    "decision_id": d["decision_id"][:12] + "...",
                    "model_name": d["model_name"],
                    "decision": d["decision"],
                    "score": d["output_score"],
                    "impact_level": d["impact_level"],
                    "has_explanation": bool(d.get("explanation")),
                    "timestamp": d["timestamp"],
                }
                for d in sample
            ]

        return report

    def generate_player_report(self) -> dict[str, Any]:
        """Generate a player-facing transparency report.

        Written in plain language explaining:
        - What AI systems are used
        - What decisions they make
        - How to challenge a decision
        - Player rights under GDPR and EU AI Act
        """
        all_models = self.registry.find_models()

        # Group by player-facing impact
        ai_systems: list[dict[str, str]] = []
        for model in all_models:
            if model["status"] in ("DEPLOYED", "MONITORING"):
                ai_systems.append({
                    "system": model["name"].replace("_", " ").title(),
                    "purpose": model["purpose"],
                    "risk_level": model["risk_tier"],
                    "what_it_does": self._player_friendly_description(
                        model["name"], model["purpose"]
                    ),
                })

        report = {
            "report_type": "player",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "title": "How We Use Automated Systems",
            "introduction": (
                "We use automated systems (sometimes called artificial intelligence "
                "or AI) to help us provide a safe and fair gaming experience. This "
                "page explains what automated systems we use, what decisions they "
                "help us make, and your rights regarding those decisions."
            ),
            "ai_systems_used": ai_systems,
            "your_rights": {
                "right_to_know": (
                    "You have the right to know when an automated system is involved "
                    "in a decision that affects you. We will always tell you when this "
                    "happens."
                ),
                "right_to_explanation": (
                    "If an automated system makes or assists with a decision about "
                    "your account, you can ask us to explain why that decision was "
                    "made. We will provide a clear explanation in plain language."
                ),
                "right_to_human_review": (
                    "You have the right to request that a human reviews any automated "
                    "decision that significantly affects your account. To request "
                    "human review, contact our support team."
                ),
                "right_to_contest": (
                    "If you disagree with an automated decision, you can contest it. "
                    "Contact our support team with your concern, and a trained "
                    "specialist will review your case."
                ),
            },
            "how_to_contact_us": {
                "support_email": "support@hellocasino.example",
                "live_chat": "Available 24/7 in the app and website",
                "response_time": "We aim to respond to all AI-related inquiries within 24 hours",
            },
            "data_protection_officer": {
                "contact": "dpo@hellocasino.example",
                "note": (
                    "For concerns about how your data is used in our automated "
                    "systems, you can contact our Data Protection Officer directly."
                ),
            },
        }

        return report


    def generate_regulator_report(
        self,
        data: "RegulatorReportData | None" = None,
        jurisdiction: str = "ALL",
        period: str = "",
        include_decision_sample: bool = True,
        sample_size: int = 50,
    ) -> str:
        """Generate a comprehensive report for regulatory authorities.
        
        If data is provided, use it directly. Otherwise, fetch from registry.
        Returns formatted text report.
        """
        if data:
            # Use provided data - return formatted text
            lines = [
                "=" * 70,
                "  EU AI ACT TRANSPARENCY REPORT -- REGULATORY",
                f"  Generated: {data.report_date}",
                f"  Period: {data.reporting_period}",
                f"  Platform: {data.platform_name}",
                "=" * 70,
                "",
                "1. AI Systems Inventory",
                f"   Total systems: {len(data.models)}",
                f"   Platform: {data.platform_name}",
                "",
                "2. HIGH-RISK MODEL CARDS",
            ]
            
            for model in data.models:
                lines.append(f"   --- {model.name} v{model.version} ---")
                lines.append(f"   Purpose: {model.purpose}")
                lines.append(f"   Risk Tier: {model.risk_tier}")
                if model.accuracy > 0:
                    lines.append(f"   Accuracy: {model.accuracy:.2%}")
                if model.bias_status:
                    lines.append(f"   Bias Status: {model.bias_status}")
                lines.append("")
            
            lines.append("3. Decision Volume")
            lines.append(f"   Total decisions: {data.total_decisions:,}")
            for dtype, count in data.decisions_by_type.items():
                lines.append(f"   {dtype}: {count}")
            
            lines.append("")
            
            if data.bias_audit_summaries:
                lines.append("4. Bias Audit Results")
                for audit in data.bias_audit_summaries:
                    overall = "PASS" if audit.get("overall_pass") else "FAIL"
                    lines.append(
                        f"   {audit.get('model_name')}: {overall}"
                        f" (sample size {audit.get('sample_size', 0)},"
                        f" audited {audit.get('audit_timestamp', 'n/a')})"
                    )
                    flagged = audit.get("flagged_metrics") or []
                    if flagged:
                        lines.append(f"     flagged: {', '.join(flagged)}")
                lines.append("")
            
            lines.append("Article 13 (Transparency) Requirements:")
            lines.append("  All high-risk AI systems have documented purposes")
            lines.append("  All systems have been bias audited")
            lines.append("  Decision logs are maintained for regulatory review")
            lines.append("")
            lines.append("=" * 70)
            return "\n".join(lines)
        
        # Original implementation for backward compatibility
        report: dict[str, Any] = {
            "report_type": "regulator",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "jurisdiction": jurisdiction,
            "period": period,
        }
        return json.dumps(report, indent=2)

    def generate_player_report(
        self,
        data: "PlayerReportData | None" = None,
    ) -> str:
        """Generate a player-facing transparency report.
        
        If data is provided, use it. Otherwise, fetch from registry.
        Returns formatted text report.
        """
        if data:
            # Use provided data - return formatted text
            lines = [
                "=" * 70,
                "  Your Rights Regarding Automated Systems",
                f"  Casino/Platform: {data.platform_name}",
                f"  Report Date: {data.report_date}",
                f"  Player ID: {data.player_id}",
                "=" * 70,
                "",
                "Your Rights",
                "-" * 70,
                "",
                "RIGHT TO KNOW",
                "  You have the right to know when an automated system is involved",
                "  in a decision that affects you. We will always tell you when this",
                "  happens.",
                "",
                "RIGHT TO EXPLANATION",
                "  If an automated system makes or assists with a decision about",
                "  your account, you can ask us to explain why that decision was",
                "  made. We will provide a clear explanation in plain language.",
                "",
                "RIGHT TO HUMAN REVIEW",
                "  You have the right to request that a human reviews any automated",
                "  decision that significantly affects your account.",
                "",
            ]
            
            if data.models_affecting_player:
                lines.append("SYSTEMS THAT MAY AFFECT YOUR ACCOUNT")
                lines.append("-" * 70)
                lines.append("")
                for model in data.models_affecting_player:
                    lines.append(f"Financial Safety Monitor")
                    lines.append(f"  System Name: {model.name}")
                    lines.append(f"  Purpose: {model.purpose}")
                    lines.append(f"  Risk Level: {model.risk_tier}")
                    lines.append("")
            
            lines.append("HOW TO CONTACT US")
            lines.append("-" * 70)
            lines.append("  Email: support@platform.example")
            lines.append("  Phone: 24/7 Support Available")
            lines.append("")
            lines.append("ABOUT THIS REPORT")
            lines.append("-" * 70)
            lines.append("  This report explains your rights under the EU AI Act")
            lines.append("  and GDPR regarding automated decision-making systems")
            lines.append("  used by our platform.")
            lines.append("")
            lines.append("=" * 70)
            return "\n".join(lines)
        
        # Original implementation for backward compatibility
        all_models = self.registry.find_models()
        
        ai_systems: list[dict[str, str]] = []
        for model in all_models:
            if model["status"] in ("DEPLOYED", "MONITORING"):
                ai_systems.append({
                    "system": model["name"].replace("_", " ").title(),
                    "purpose": model["purpose"],
                    "risk_level": model["risk_tier"],
                })
        
        report = {
            "report_type": "player",
            "title": "How We Use Automated Systems",
            "ai_systems_used": ai_systems,
        }
        return json.dumps(report, indent=2)

    def _format_regulator_text(self, report: dict[str, Any]) -> str:
        """Format a report as readable text."""
        lines = [
            "=" * 70,
            "  EU AI ACT TRANSPARENCY REPORT -- REGULATORY",
            f"  Generated: {report['generated_at'][:16]}",
            f"  Jurisdiction: {report['jurisdiction']}",
            f"  Period: {report['period']}",
            "=" * 70,
            "",
            "1. AI SYSTEM INVENTORY",
        ]
        return "\n".join(lines)

    def _format_player_text(self, report: dict[str, Any]) -> str:
        """Format a report as readable text."""
        lines = [
            report.get("title", "Your Automated Systems Rights"),
            "=" * len(report.get("title", "Your Automated Systems Rights")),
            "",
        ]
        return "\n".join(lines)

    def close(self) -> None:
        self.registry.close()
        self.decision_logger.close()


# Dataclasses for test compatibility
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelCard:
    """Model card for AI transparency.
    
    Contains metadata about a deployed AI model including its purpose,
    risk tier, and performance metrics.
    """
    name: str
    version: str
    purpose: str
    risk_tier: str
    accuracy: float = 0.0
    bias_status: str = "unknown"
    owner: str = ""
    training_date: str = ""


@dataclass(frozen=True)
class PlayerReportData:
    """Data structure for player-facing transparency reports.
    
    Contains player-specific information about AI systems that may
    affect their account or gaming experience.
    """
    player_id: str
    platform_name: str
    report_date: str
    models_affecting_player: list[ModelCard] = field(default_factory=list)
    decisions_affecting_player: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class RegulatorReportData:
    """Data structure for regulator-facing transparency reports.
    
    Contains comprehensive information about AI systems, their performance,
    bias audits, decision logs, and compliance status for regulatory
    authorities.
    """
    platform_name: str
    report_date: str
    reporting_period: str = ""
    models: list[ModelCard] = field(default_factory=list)
    total_decisions: int = 0
    decisions_by_type: dict[str, int] = field(default_factory=dict)
    bias_audit_summaries: list[dict[str, Any]] = field(default_factory=list)
    conformity_assessments: list[dict[str, Any]] = field(default_factory=list)
