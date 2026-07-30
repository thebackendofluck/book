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
Post-Quantum Cryptography Migration Planner for iGaming Platforms
==================================================================

Inventories current cryptographic usage across a gambling platform and
maps each algorithm to its NIST Post-Quantum Cryptography (PQC)
replacement. Generates a prioritized migration plan.

Covers:
- Cryptographic inventory across all platform components
- Risk assessment per algorithm against quantum threat timeline
- NIST PQC replacement mapping (CRYSTALS-Kyber, CRYSTALS-Dilithium, SPHINCS+)
- Migration priority scoring (harvest-now-decrypt-later risk)
- Hybrid deployment strategy (classical + PQC during transition)
- Compliance impact for gambling regulators
- Budget and timeline estimation

NIST PQC Standards (FIPS 203, 204, 205 - August 2024):
- ML-KEM (CRYSTALS-Kyber): Key encapsulation (replaces RSA/ECDH key exchange)
- ML-DSA (CRYSTALS-Dilithium): Digital signatures (replaces RSA/ECDSA signatures)
- SLH-DSA (SPHINCS+): Hash-based signatures (stateless, conservative fallback)
- FN-DSA (FALCON): Compact signatures (expected 2025 standardization)

Feasibility Assessment:
- Inventory is a configuration scan - no crypto expertise needed to run
- Replacement mappings are based on NIST publications
- Migration plan is a structured project plan
- No external dependencies for core tool

Dependencies: None
"""

import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

class CryptoUsage(Enum):
    KEY_EXCHANGE = "key_exchange"
    DIGITAL_SIGNATURE = "digital_signature"
    ENCRYPTION = "encryption"
    HASHING = "hashing"
    MAC = "message_authentication"
    KEY_DERIVATION = "key_derivation"
    RANDOM_GENERATION = "random_generation"


class QuantumRisk(Enum):
    NONE = "none"               # quantum-safe already (e.g., AES-256, SHA-3)
    LOW = "low"                 # large key sizes, long migration window
    MEDIUM = "medium"           # needs migration within 5-10 years
    HIGH = "high"               # harvest-now-decrypt-later threat
    CRITICAL = "critical"       # protecting long-lived secrets


class MigrationPriority(Enum):
    P0_IMMEDIATE = "P0_immediate"      # start now (HNDL risk)
    P1_SHORT_TERM = "P1_short_term"    # within 1-2 years
    P2_MEDIUM_TERM = "P2_medium_term"  # within 3-5 years
    P3_LONG_TERM = "P3_long_term"      # 5+ years or already safe


class PQCReplacement(Enum):
    ML_KEM = "ML-KEM (CRYSTALS-Kyber)"
    ML_DSA = "ML-DSA (CRYSTALS-Dilithium)"
    SLH_DSA = "SLH-DSA (SPHINCS+)"
    FN_DSA = "FN-DSA (FALCON)"
    HYBRID_KEM = "Hybrid: X25519 + ML-KEM-768"
    HYBRID_SIG = "Hybrid: Ed25519 + ML-DSA-65"
    NO_CHANGE = "No change needed (quantum-safe)"
    INCREASE_KEY = "Increase key size (e.g., AES-128 -> AES-256)"


@dataclass
class CryptoAsset:
    """A single cryptographic usage found in the platform."""
    asset_id: str
    component: str           # e.g., "payment-gateway", "player-auth"
    algorithm: str           # e.g., "RSA-2048", "AES-256-GCM"
    usage: CryptoUsage
    protocol: str = ""       # e.g., "TLS 1.3", "JWS", "PGP"
    key_size_bits: int = 0
    data_sensitivity: str = ""  # "player_pii", "financial", "session"
    data_retention_years: int = 0
    quantum_risk: QuantumRisk = QuantumRisk.MEDIUM
    pqc_replacement: PQCReplacement = PQCReplacement.NO_CHANGE
    migration_priority: MigrationPriority = MigrationPriority.P2_MEDIUM_TERM
    notes: str = ""


@dataclass
class MigrationTask:
    """A concrete migration task in the plan."""
    task_id: str
    title: str
    component: str
    current_algorithm: str
    target_algorithm: str
    priority: MigrationPriority
    effort_weeks: int
    prerequisites: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    estimated_cost: float = 0.0


@dataclass
class MigrationPlan:
    """Complete PQC migration plan."""
    organization: str
    assessment_date: str
    total_crypto_assets: int = 0
    quantum_safe_count: int = 0
    needs_migration_count: int = 0
    risk_summary: dict = field(default_factory=dict)
    assets: list[CryptoAsset] = field(default_factory=list)
    migration_tasks: list[MigrationTask] = field(default_factory=list)
    timeline: dict = field(default_factory=dict)
    total_estimated_cost: float = 0.0
    compliance_impact: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Quantum risk database
# ---------------------------------------------------------------------------

# Algorithm -> (quantum_risk, pqc_replacement, notes)
ALGORITHM_RISK_MAP: dict[str, tuple[QuantumRisk, PQCReplacement, str]] = {
    # Asymmetric - vulnerable
    "RSA-2048": (QuantumRisk.HIGH, PQCReplacement.ML_KEM, "Broken by Shor's algorithm; 2048-bit provides no quantum resistance"),
    "RSA-3072": (QuantumRisk.HIGH, PQCReplacement.ML_KEM, "Broken by Shor's algorithm regardless of key size"),
    "RSA-4096": (QuantumRisk.HIGH, PQCReplacement.ML_KEM, "Broken by Shor's algorithm regardless of key size"),
    "ECDSA-P256": (QuantumRisk.HIGH, PQCReplacement.ML_DSA, "Elliptic curve broken by Shor's algorithm"),
    "ECDSA-P384": (QuantumRisk.HIGH, PQCReplacement.ML_DSA, "Elliptic curve broken by Shor's algorithm"),
    "ECDH-P256": (QuantumRisk.HIGH, PQCReplacement.ML_KEM, "Key exchange broken by Shor's algorithm"),
    "ECDH-X25519": (QuantumRisk.HIGH, PQCReplacement.HYBRID_KEM, "Hybrid recommended for transition period"),
    "Ed25519": (QuantumRisk.HIGH, PQCReplacement.HYBRID_SIG, "Hybrid recommended for transition period"),
    "DSA-2048": (QuantumRisk.HIGH, PQCReplacement.ML_DSA, "Broken by Shor's algorithm"),
    "DH-2048": (QuantumRisk.HIGH, PQCReplacement.ML_KEM, "Broken by Shor's algorithm"),

    # Symmetric - quantum resistant with sufficient key size
    "AES-256-GCM": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "256-bit provides 128-bit quantum security (Grover's)"),
    "AES-256-CBC": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "256-bit quantum safe; consider GCM for AEAD"),
    "AES-128-GCM": (QuantumRisk.LOW, PQCReplacement.INCREASE_KEY, "128-bit provides 64-bit quantum security; upgrade to 256"),
    "AES-128-CBC": (QuantumRisk.LOW, PQCReplacement.INCREASE_KEY, "Upgrade to AES-256; also consider GCM mode"),
    "ChaCha20-Poly1305": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "256-bit key; quantum safe"),

    # Hashing - quantum resistant with sufficient output size
    "SHA-256": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "Grover's reduces to 128-bit; sufficient for most uses"),
    "SHA-384": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "Quantum safe"),
    "SHA-512": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "Quantum safe"),
    "SHA-3-256": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "Quantum safe"),
    "BLAKE2b": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "Quantum safe"),
    "SHA-1": (QuantumRisk.MEDIUM, PQCReplacement.NO_CHANGE, "Already broken classically; replace with SHA-256+"),
    "MD5": (QuantumRisk.MEDIUM, PQCReplacement.NO_CHANGE, "Already broken classically; replace immediately"),

    # Key derivation
    "PBKDF2-SHA256": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "Quantum safe for password hashing"),
    "Argon2id": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "Quantum safe; recommended for password hashing"),
    "bcrypt": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "Quantum safe for password hashing"),
    "HKDF-SHA256": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "Quantum safe"),

    # MAC
    "HMAC-SHA256": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "Quantum safe"),
    "HMAC-SHA512": (QuantumRisk.NONE, PQCReplacement.NO_CHANGE, "Quantum safe"),
}


# ---------------------------------------------------------------------------
# Migration planner
# ---------------------------------------------------------------------------

class PostQuantumMigrationPlanner:
    """
    Scans platform cryptographic usage, assesses quantum risk,
    and generates a prioritized migration plan.

    Workflow:
    1. Inventory all crypto usage (manual input or automated scan)
    2. Assess quantum risk per algorithm
    3. Factor in data sensitivity and retention
    4. Generate migration tasks with priorities
    5. Estimate budget and timeline
    """

    def __init__(self):
        self.assets: list[CryptoAsset] = []
        self._asset_counter = 0
        self._task_counter = 0

    def add_crypto_asset(
        self,
        component: str,
        algorithm: str,
        usage: CryptoUsage,
        protocol: str = "",
        key_size_bits: int = 0,
        data_sensitivity: str = "",
        data_retention_years: int = 0,
        notes: str = "",
    ) -> CryptoAsset:
        """Register a cryptographic usage found in the platform."""
        self._asset_counter += 1

        # Look up risk and replacement
        risk_info = ALGORITHM_RISK_MAP.get(algorithm, (QuantumRisk.MEDIUM, PQCReplacement.NO_CHANGE, "Unknown algorithm"))
        quantum_risk, pqc_replacement, risk_notes = risk_info

        # Adjust risk based on data sensitivity and retention
        if data_sensitivity in ("financial", "player_pii") and data_retention_years > 5:
            if quantum_risk == QuantumRisk.HIGH:
                quantum_risk = QuantumRisk.CRITICAL  # harvest-now-decrypt-later

        # Determine migration priority
        priority = self._determine_priority(quantum_risk, data_sensitivity, data_retention_years)

        asset = CryptoAsset(
            asset_id=f"CRYPTO-{self._asset_counter:04d}",
            component=component,
            algorithm=algorithm,
            usage=usage,
            protocol=protocol,
            key_size_bits=key_size_bits,
            data_sensitivity=data_sensitivity,
            data_retention_years=data_retention_years,
            quantum_risk=quantum_risk,
            pqc_replacement=pqc_replacement,
            migration_priority=priority,
            notes=notes or risk_notes,
        )
        self.assets.append(asset)
        return asset

    def generate_migration_plan(self, organization: str) -> MigrationPlan:
        """Generate the complete migration plan from inventoried assets."""

        # Risk summary
        risk_counts: dict[str, int] = {}
        for r in QuantumRisk:
            risk_counts[r.value] = sum(1 for a in self.assets if a.quantum_risk == r)

        needs_migration = [a for a in self.assets if a.quantum_risk not in (QuantumRisk.NONE, QuantumRisk.LOW)]
        quantum_safe = [a for a in self.assets if a.quantum_risk == QuantumRisk.NONE]

        # Generate migration tasks
        tasks = self._generate_tasks(needs_migration)

        # Timeline
        timeline = {
            "phase_1_assessment": "Months 1-3: Complete cryptographic inventory and risk assessment",
            "phase_2_pilot": "Months 4-6: Pilot hybrid deployments on non-critical systems",
            "phase_3_critical": "Months 7-12: Migrate CRITICAL and HIGH risk assets",
            "phase_4_remaining": "Months 13-24: Migrate MEDIUM risk assets",
            "phase_5_decommission": "Months 25-36: Remove classical-only crypto, full PQC",
        }

        # Cost estimate
        total_cost = sum(t.estimated_cost for t in tasks)

        # Compliance impact
        compliance = [
            "UK Gambling Commission: LCCP requires 'appropriate' encryption - PQC timeline not yet mandated but expected",
            "MGA Technical Standards: Require 'industry standard' cryptography - PQC will become the standard",
            "PCI DSS v4.0: Expects migration planning for quantum threats by 2025",
            "GDPR Article 32: 'State of the art' encryption requirement will evolve to include PQC",
            "ISO 27001:2022: Cryptographic controls must address emerging threats",
            "GLI-33: Crypto requirements for RNG and gaming systems will update for PQC",
        ]

        # Recommendations
        recommendations = [
            "Start with hybrid deployments (classical + PQC) to maintain backward compatibility",
            "Prioritize TLS/mTLS migration - most gambling platform traffic uses TLS",
            "Migrate payment channel encryption first (harvest-now-decrypt-later risk)",
            "Coordinate with payment processors on their PQC timelines",
            "Update key management infrastructure to support larger PQC key sizes",
            "Budget for performance testing - PQC algorithms have different performance profiles",
            "Engage with gambling regulators proactively to understand their PQC expectations",
            "Consider SLH-DSA (SPHINCS+) for long-lived signatures (certificates, audit logs)",
        ]

        return MigrationPlan(
            organization=organization,
            assessment_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            total_crypto_assets=len(self.assets),
            quantum_safe_count=len(quantum_safe),
            needs_migration_count=len(needs_migration),
            risk_summary=risk_counts,
            assets=self.assets,
            migration_tasks=tasks,
            timeline=timeline,
            total_estimated_cost=total_cost,
            compliance_impact=compliance,
            recommendations=recommendations,
        )

    def _determine_priority(
        self, risk: QuantumRisk, sensitivity: str, retention_years: int,
    ) -> MigrationPriority:
        if risk == QuantumRisk.CRITICAL:
            return MigrationPriority.P0_IMMEDIATE
        if risk == QuantumRisk.HIGH:
            if sensitivity in ("financial", "player_pii"):
                return MigrationPriority.P0_IMMEDIATE
            return MigrationPriority.P1_SHORT_TERM
        if risk == QuantumRisk.MEDIUM:
            return MigrationPriority.P2_MEDIUM_TERM
        if risk == QuantumRisk.LOW:
            return MigrationPriority.P2_MEDIUM_TERM
        return MigrationPriority.P3_LONG_TERM

    def _generate_tasks(self, assets: list[CryptoAsset]) -> list[MigrationTask]:
        """Generate concrete migration tasks grouped by component."""
        # Group assets by component
        by_component: dict[str, list[CryptoAsset]] = {}
        for a in assets:
            by_component.setdefault(a.component, []).append(a)

        tasks = []
        for component, comp_assets in by_component.items():
            # Determine highest priority among component's assets
            priority_order = [
                MigrationPriority.P0_IMMEDIATE,
                MigrationPriority.P1_SHORT_TERM,
                MigrationPriority.P2_MEDIUM_TERM,
                MigrationPriority.P3_LONG_TERM,
            ]
            highest_priority = MigrationPriority.P3_LONG_TERM
            for a in comp_assets:
                if priority_order.index(a.migration_priority) < priority_order.index(highest_priority):
                    highest_priority = a.migration_priority

            # Create task per unique algorithm replacement
            seen = set()
            for a in comp_assets:
                migration_key = f"{a.algorithm}->{a.pqc_replacement.value}"
                if migration_key in seen:
                    continue
                seen.add(migration_key)

                self._task_counter += 1
                effort = self._estimate_effort(a)
                cost = effort * 12000  # ~$12K per engineer-week

                tasks.append(MigrationTask(
                    task_id=f"PQC-{self._task_counter:04d}",
                    title=f"Migrate {component}: {a.algorithm} -> {a.pqc_replacement.value}",
                    component=component,
                    current_algorithm=a.algorithm,
                    target_algorithm=a.pqc_replacement.value,
                    priority=highest_priority,
                    effort_weeks=effort,
                    prerequisites=self._get_prerequisites(a),
                    risks=[
                        "Performance regression from larger PQC key sizes",
                        "Compatibility with third-party integrations",
                        f"Key management update for {a.pqc_replacement.value}",
                    ],
                    acceptance_criteria=[
                        f"All {component} connections use {a.pqc_replacement.value}",
                        "No regression in latency p99 beyond 20%",
                        "Backward compatibility maintained via hybrid mode",
                        "Security audit confirms correct implementation",
                    ],
                    estimated_cost=cost,
                ))

        # Sort by priority
        tasks.sort(key=lambda t: priority_order.index(t.priority))
        return tasks

    def _estimate_effort(self, asset: CryptoAsset) -> int:
        """Estimate effort in engineer-weeks."""
        base = 2
        if asset.usage == CryptoUsage.KEY_EXCHANGE:
            base = 4  # TLS/protocol changes are complex
        elif asset.usage == CryptoUsage.DIGITAL_SIGNATURE:
            base = 3
        elif asset.usage == CryptoUsage.ENCRYPTION:
            base = 3

        if asset.protocol in ("TLS 1.3", "TLS 1.2"):
            base += 2  # needs infra-wide rollout

        return base

    def _get_prerequisites(self, asset: CryptoAsset) -> list[str]:
        prereqs = []
        if asset.pqc_replacement in (PQCReplacement.ML_KEM, PQCReplacement.HYBRID_KEM):
            prereqs.append("Update TLS library to version supporting ML-KEM (e.g., OpenSSL 3.5+)")
            prereqs.append("Verify load balancer and CDN support for PQC key exchange")
        if asset.pqc_replacement in (PQCReplacement.ML_DSA, PQCReplacement.HYBRID_SIG):
            prereqs.append("Update signing libraries to support ML-DSA")
            prereqs.append("Update certificate infrastructure for PQC certificates")
        if asset.pqc_replacement == PQCReplacement.SLH_DSA:
            prereqs.append("Evaluate SLH-DSA signature sizes (~8KB-41KB) for your use case")
        prereqs.append("Performance benchmark in staging environment")
        return prereqs


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo():
    """Simulate PQC migration planning for a gambling platform."""

    planner = PostQuantumMigrationPlanner()

    print("\n" + "=" * 70)
    print("  Post-Quantum Cryptography Migration Planner")
    print("=" * 70)

    # Inventory the platform's cryptographic usage
    inventory = [
        # TLS / Network
        ("api-gateway", "ECDH-X25519", CryptoUsage.KEY_EXCHANGE, "TLS 1.3", 256, "session", 0),
        ("api-gateway", "ECDSA-P256", CryptoUsage.DIGITAL_SIGNATURE, "TLS 1.3", 256, "session", 0),
        ("api-gateway", "AES-256-GCM", CryptoUsage.ENCRYPTION, "TLS 1.3", 256, "session", 0),

        # Payment processing
        ("payment-gateway", "RSA-2048", CryptoUsage.KEY_EXCHANGE, "TLS 1.2", 2048, "financial", 7),
        ("payment-gateway", "RSA-2048", CryptoUsage.DIGITAL_SIGNATURE, "PGP", 2048, "financial", 7),
        ("payment-gateway", "AES-256-GCM", CryptoUsage.ENCRYPTION, "at-rest", 256, "financial", 7),

        # Player authentication
        ("player-auth", "Ed25519", CryptoUsage.DIGITAL_SIGNATURE, "JWT", 256, "player_pii", 5),
        ("player-auth", "Argon2id", CryptoUsage.KEY_DERIVATION, "password-hash", 256, "player_pii", 5),
        ("player-auth", "HMAC-SHA256", CryptoUsage.MAC, "JWT", 256, "session", 0),

        # Inter-service communication
        ("service-mesh", "ECDSA-P256", CryptoUsage.DIGITAL_SIGNATURE, "mTLS", 256, "internal", 0),
        ("service-mesh", "ECDH-P256", CryptoUsage.KEY_EXCHANGE, "mTLS", 256, "internal", 0),
        ("service-mesh", "AES-128-GCM", CryptoUsage.ENCRYPTION, "mTLS", 128, "internal", 0),

        # Data at rest
        ("database", "AES-256-GCM", CryptoUsage.ENCRYPTION, "TDE", 256, "player_pii", 10),
        ("database", "SHA-256", CryptoUsage.HASHING, "integrity", 256, "player_pii", 10),

        # RNG service
        ("rng-service", "AES-256-GCM", CryptoUsage.ENCRYPTION, "DRBG", 256, "gaming", 0),
        ("rng-service", "SHA-512", CryptoUsage.HASHING, "seed-generation", 512, "gaming", 0),
        ("rng-service", "ECDSA-P384", CryptoUsage.DIGITAL_SIGNATURE, "audit-sign", 384, "gaming", 10),

        # Regulatory reporting
        ("compliance", "RSA-4096", CryptoUsage.DIGITAL_SIGNATURE, "XML-DSIG", 4096, "regulatory", 15),
        ("compliance", "AES-256-CBC", CryptoUsage.ENCRYPTION, "archive", 256, "regulatory", 15),

        # KYC document storage
        ("kyc-storage", "RSA-2048", CryptoUsage.ENCRYPTION, "envelope", 2048, "player_pii", 7),
        ("kyc-storage", "SHA-256", CryptoUsage.HASHING, "document-hash", 256, "player_pii", 7),
    ]

    print("\n  Inventorying cryptographic assets...\n")
    for component, algo, usage, protocol, key_size, sensitivity, retention in inventory:
        planner.add_crypto_asset(
            component=component,
            algorithm=algo,
            usage=usage,
            protocol=protocol,
            key_size_bits=key_size,
            data_sensitivity=sensitivity,
            data_retention_years=retention,
        )

    # Generate migration plan
    plan = planner.generate_migration_plan("Acme Casino Group")

    print(f"  Total crypto assets inventoried: {plan.total_crypto_assets}")
    print(f"  Already quantum-safe: {plan.quantum_safe_count}")
    print(f"  Needs migration: {plan.needs_migration_count}")

    print(f"\n  Risk Distribution:")
    for risk, count in plan.risk_summary.items():
        bar = "#" * (count * 2) if count else ""
        print(f"    {risk:10s}: {count:2d} {bar}")

    print(f"\n  Crypto Asset Inventory (vulnerable assets):")
    for a in plan.assets:
        if a.quantum_risk in (QuantumRisk.HIGH, QuantumRisk.CRITICAL):
            risk_tag = "CRIT" if a.quantum_risk == QuantumRisk.CRITICAL else "HIGH"
            print(f"    [{risk_tag}] {a.component:20s} {a.algorithm:15s} ({a.usage.value})")
            print(f"           Replace with: {a.pqc_replacement.value}")
            print(f"           Data: {a.data_sensitivity}, retention: {a.data_retention_years}y")

    print(f"\n  Migration Tasks ({len(plan.migration_tasks)} total):")
    for t in plan.migration_tasks[:10]:
        print(f"    [{t.priority.value:15s}] {t.task_id}: {t.title}")
        print(f"           Effort: {t.effort_weeks} weeks | Cost: ${t.estimated_cost:,.0f}")

    print(f"\n  Migration Timeline:")
    for phase, desc in plan.timeline.items():
        print(f"    {desc}")

    print(f"\n  Total Estimated Cost: ${plan.total_estimated_cost:,.0f}")

    print(f"\n  Compliance Impact:")
    for c in plan.compliance_impact[:4]:
        print(f"    - {c}")

    print(f"\n  Top Recommendations:")
    for r in plan.recommendations[:4]:
        print(f"    - {r}")

    print(f"\n  Next steps:")
    print("    1. Validate inventory with infrastructure team")
    print("    2. Begin hybrid TLS deployment pilot (X25519 + ML-KEM)")
    print("    3. Engage payment processors on PQC timeline")
    print("    4. Update security policy to include PQC migration mandate\n")


if __name__ == "__main__":
    demo()
