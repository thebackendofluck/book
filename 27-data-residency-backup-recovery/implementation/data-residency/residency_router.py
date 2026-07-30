#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Multi-Jurisdiction Data Residency Router
=========================================
Routes data writes/reads to jurisdiction-compliant storage regions.
Enforces region-lock policies and logs all routing decisions for audit.

Jurisdictions: UK (UKGC), Malta (MGA), Germany (GGL), Ontario (AGCO).

Usage:
    python residency_router.py --demo
    python residency_router.py --test-routing UK player_pii eu-west-2
    python residency_router.py --show-rules
"""

import json
import logging
import argparse
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("residency-router")


# ---------------------------------------------------------------------------
# Data types and routing decisions
# ---------------------------------------------------------------------------
class DataType(str, Enum):
    PLAYER_PII = "player_pii"
    FINANCIAL = "financial"
    GAMING_ACTIVITY = "gaming_activity"
    KYC = "kyc"
    MARKETING = "marketing"
    ANALYTICS = "analytics"
    SYSTEM_LOGS = "system_logs"


class RoutingDecision(str, Enum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    REQUIRES_APPROVAL = "requires_approval"
    REDIRECT = "redirect"


@dataclass
class DataRequest:
    request_id: str
    jurisdiction: str
    data_type: DataType
    target_region: str
    source_region: str = ""
    player_id: Optional[str] = None
    payload_size_bytes: int = 0
    operation: str = "write"  # write / read / replicate


@dataclass
class RoutingResult:
    request_id: str
    decision: RoutingDecision
    actual_region: str
    jurisdiction: str
    data_type: str
    reason: str
    encryption_required: bool = True
    audit_logged: bool = True
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Jurisdiction region definitions
# ---------------------------------------------------------------------------
@dataclass
class RegionPolicy:
    jurisdiction: str
    primary_regions: list[str]
    backup_regions: list[str]
    dr_regions: list[str]  # disaster recovery
    cross_border_allowed: bool
    cross_border_conditions: list[str]
    encryption_algorithm: str
    data_type_overrides: dict = field(default_factory=dict)


REGION_POLICIES: dict[str, RegionPolicy] = {
    "UK": RegionPolicy(
        jurisdiction="UK",
        primary_regions=["eu-west-2"],  # London
        backup_regions=["eu-west-2", "uk-dc-london-1"],
        dr_regions=["eu-west-1"],  # Ireland -- allowed with UK IDTA
        cross_border_allowed=True,
        cross_border_conditions=[
            "UK International Data Transfer Agreement (IDTA) in place",
            "Adequate safeguards verified",
            "Data Protection Impact Assessment completed",
        ],
        encryption_algorithm="AES-256-GCM",
        data_type_overrides={
            # KYC must stay in UK regions only
            DataType.KYC: {
                "allowed_regions": ["eu-west-2", "uk-dc-london-1"],
                "cross_border": False,
            },
        },
    ),
    "MT": RegionPolicy(
        jurisdiction="MT",
        primary_regions=["eu-central-1", "eu-south-1"],  # Frankfurt, Milan
        backup_regions=["eu-central-1", "eu-west-1", "eu-south-1"],
        dr_regions=["eu-north-1"],  # Stockholm
        cross_border_allowed=False,
        cross_border_conditions=[
            "Standard Contractual Clauses (SCCs) required",
            "Must stay within EU/EEA",
        ],
        encryption_algorithm="AES-256-GCM",
    ),
    "DE": RegionPolicy(
        jurisdiction="DE",
        primary_regions=["eu-central-1"],  # Frankfurt
        backup_regions=["eu-central-1", "de-dc-frankfurt-1"],
        dr_regions=["eu-central-1"],  # Strict: DR must also be in Germany/EU
        cross_border_allowed=False,
        cross_border_conditions=[
            "GDPR Article 49 derogations only",
            "Bundesbeauftragter approval required",
            "DPIA mandatory before any transfer",
        ],
        encryption_algorithm="AES-256-GCM",
        data_type_overrides={
            # Germany requires all player data in German/EU DCs
            DataType.PLAYER_PII: {
                "allowed_regions": ["eu-central-1", "de-dc-frankfurt-1"],
                "cross_border": False,
            },
            DataType.GAMING_ACTIVITY: {
                "allowed_regions": ["eu-central-1", "de-dc-frankfurt-1"],
                "cross_border": False,
            },
        },
    ),
    "ON": RegionPolicy(
        jurisdiction="ON",
        primary_regions=["ca-central-1"],  # Montreal
        backup_regions=["ca-central-1", "ca-dc-toronto-1"],
        dr_regions=["ca-central-1", "us-east-1"],  # US DR allowed
        cross_border_allowed=True,
        cross_border_conditions=[
            "PIPEDA compliance maintained",
            "Comparable privacy protections",
            "AGCO notified of cross-border arrangements",
        ],
        encryption_algorithm="AES-256-GCM",
        data_type_overrides={
            # Financial data prefers Canadian regions
            DataType.FINANCIAL: {
                "preferred_regions": ["ca-central-1", "ca-dc-toronto-1"],
                "cross_border": True,  # allowed but not preferred
            },
        },
    ),
}


# ---------------------------------------------------------------------------
# Audit logger
# ---------------------------------------------------------------------------
class AuditLogger:
    """
    Logs all routing decisions for compliance. In production, this writes
    to an append-only audit store (e.g., immutable S3 bucket or dedicated
    audit database).
    """

    def __init__(self):
        self._log: list[dict] = []

    def log_routing(self, result: RoutingResult, request: DataRequest):
        entry = {
            "event_type": "data_routing_decision",
            "request_id": result.request_id,
            "timestamp": result.timestamp,
            "jurisdiction": result.jurisdiction,
            "data_type": result.data_type,
            "decision": result.decision.value,
            "requested_region": request.target_region,
            "actual_region": result.actual_region,
            "reason": result.reason,
            "player_id_hash": self._hash_player_id(request.player_id),
            "operation": request.operation,
            "payload_size_bytes": request.payload_size_bytes,
        }
        self._log.append(entry)
        if result.decision == RoutingDecision.BLOCKED:
            logger.warning(
                "BLOCKED routing: %s -> %s for %s (%s)",
                request.source_region or "unknown",
                request.target_region,
                request.jurisdiction,
                result.reason,
            )
        else:
            logger.info(
                "Routing %s: %s -> %s (%s)",
                result.decision.value,
                request.source_region or "origin",
                result.actual_region,
                request.jurisdiction,
            )
        return entry

    def get_audit_log(self) -> list[dict]:
        return list(self._log)

    @staticmethod
    def _hash_player_id(player_id: Optional[str]) -> Optional[str]:
        if player_id is None:
            return None
        import hashlib
        return hashlib.sha256(player_id.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Residency router
# ---------------------------------------------------------------------------
class ResidencyRouter:
    """
    Core routing engine. Evaluates data requests against jurisdiction
    policies and returns routing decisions.
    """

    def __init__(self):
        self.policies = REGION_POLICIES
        self.audit = AuditLogger()

    def route(self, request: DataRequest) -> RoutingResult:
        """Route a data request according to jurisdiction policies."""
        if request.jurisdiction not in self.policies:
            return RoutingResult(
                request_id=request.request_id,
                decision=RoutingDecision.BLOCKED,
                actual_region="",
                jurisdiction=request.jurisdiction,
                data_type=request.data_type.value,
                reason=f"Unsupported jurisdiction: {request.jurisdiction}",
            )

        policy = self.policies[request.jurisdiction]

        # Check data-type-specific overrides
        override = policy.data_type_overrides.get(request.data_type)
        if override:
            allowed = override.get(
                "allowed_regions",
                override.get("preferred_regions", policy.primary_regions),
            )
            cross_border = override.get("cross_border", policy.cross_border_allowed)
        else:
            allowed = (
                policy.primary_regions
                + policy.backup_regions
                + policy.dr_regions
            )
            # Deduplicate while preserving order
            seen = set()
            unique_allowed = []
            for r in allowed:
                if r not in seen:
                    seen.add(r)
                    unique_allowed.append(r)
            allowed = unique_allowed
            cross_border = policy.cross_border_allowed

        # Decision logic
        target = request.target_region

        if target in allowed:
            result = RoutingResult(
                request_id=request.request_id,
                decision=RoutingDecision.ALLOWED,
                actual_region=target,
                jurisdiction=request.jurisdiction,
                data_type=request.data_type.value,
                reason=f"Region {target} is approved for {request.jurisdiction}",
                encryption_required=True,
            )
        elif cross_border:
            # Cross-border allowed but target not in primary list
            result = RoutingResult(
                request_id=request.request_id,
                decision=RoutingDecision.REQUIRES_APPROVAL,
                actual_region=target,
                jurisdiction=request.jurisdiction,
                data_type=request.data_type.value,
                reason=(
                    f"Region {target} not in approved list. "
                    f"Cross-border transfer requires: "
                    f"{'; '.join(policy.cross_border_conditions)}"
                ),
                encryption_required=True,
            )
        else:
            # Redirect to primary region
            redirect_to = policy.primary_regions[0]
            result = RoutingResult(
                request_id=request.request_id,
                decision=RoutingDecision.REDIRECT,
                actual_region=redirect_to,
                jurisdiction=request.jurisdiction,
                data_type=request.data_type.value,
                reason=(
                    f"Region {target} NOT allowed for {request.jurisdiction}. "
                    f"Cross-border transfers prohibited. "
                    f"Redirecting to {redirect_to}."
                ),
                encryption_required=True,
            )

        self.audit.log_routing(result, request)
        return result

    def batch_route(
        self, requests: list[DataRequest]
    ) -> list[RoutingResult]:
        """Route multiple requests, returning results in order."""
        return [self.route(req) for req in requests]

    def validate_replication_target(
        self, jurisdiction: str, source_region: str, target_region: str
    ) -> RoutingResult:
        """
        Validate whether replication from source to target is compliant.
        Used for backup and DR replication validation.
        """
        request = DataRequest(
            request_id=str(uuid.uuid4()),
            jurisdiction=jurisdiction,
            data_type=DataType.FINANCIAL,  # use strictest classification
            target_region=target_region,
            source_region=source_region,
            operation="replicate",
        )
        return self.route(request)

    def get_compliant_regions(
        self, jurisdiction: str, data_type: DataType
    ) -> list[str]:
        """Return list of compliant regions for a jurisdiction/data-type combo."""
        if jurisdiction not in self.policies:
            return []
        policy = self.policies[jurisdiction]
        override = policy.data_type_overrides.get(data_type)
        if override:
            return override.get(
                "allowed_regions",
                override.get("preferred_regions", policy.primary_regions),
            )
        regions = set(
            policy.primary_regions
            + policy.backup_regions
            + policy.dr_regions
        )
        return sorted(regions)


# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------
def run_demo():
    """Run demonstration routing scenarios."""
    router = ResidencyRouter()

    scenarios = [
        # UK: allowed region
        DataRequest(
            request_id=str(uuid.uuid4()),
            jurisdiction="UK",
            data_type=DataType.PLAYER_PII,
            target_region="eu-west-2",
            player_id="uk-player-001",
            operation="write",
        ),
        # UK: KYC to Ireland -- should be blocked (KYC override)
        DataRequest(
            request_id=str(uuid.uuid4()),
            jurisdiction="UK",
            data_type=DataType.KYC,
            target_region="eu-west-1",
            player_id="uk-player-002",
            operation="write",
        ),
        # Malta: attempt to route to US -- should redirect
        DataRequest(
            request_id=str(uuid.uuid4()),
            jurisdiction="MT",
            data_type=DataType.FINANCIAL,
            target_region="us-east-1",
            player_id="mt-player-001",
            operation="write",
        ),
        # Germany: player data to Frankfurt -- allowed
        DataRequest(
            request_id=str(uuid.uuid4()),
            jurisdiction="DE",
            data_type=DataType.PLAYER_PII,
            target_region="eu-central-1",
            player_id="de-player-001",
            operation="write",
        ),
        # Germany: player data to Ireland -- should redirect
        DataRequest(
            request_id=str(uuid.uuid4()),
            jurisdiction="DE",
            data_type=DataType.PLAYER_PII,
            target_region="eu-west-1",
            player_id="de-player-002",
            operation="write",
        ),
        # Ontario: financial data to US -- requires approval
        DataRequest(
            request_id=str(uuid.uuid4()),
            jurisdiction="ON",
            data_type=DataType.FINANCIAL,
            target_region="us-east-1",
            player_id="on-player-001",
            operation="replicate",
        ),
        # Ontario: analytics to Canadian region -- allowed
        DataRequest(
            request_id=str(uuid.uuid4()),
            jurisdiction="ON",
            data_type=DataType.ANALYTICS,
            target_region="ca-central-1",
            operation="write",
        ),
    ]

    print("=" * 80)
    print("DATA RESIDENCY ROUTING DEMONSTRATION")
    print("=" * 80)

    for req in scenarios:
        result = router.route(req)
        print(f"\n--- Scenario: {req.jurisdiction} / {req.data_type.value} "
              f"-> {req.target_region} ---")
        print(f"  Decision:  {result.decision.value}")
        print(f"  Region:    {result.actual_region}")
        print(f"  Reason:    {result.reason}")
        print(f"  Encrypted: {result.encryption_required}")

    # Print audit log
    print("\n" + "=" * 80)
    print("AUDIT LOG (all routing decisions)")
    print("=" * 80)
    for entry in router.audit.get_audit_log():
        print(json.dumps(entry, indent=2))

    # Show compliant regions per jurisdiction
    print("\n" + "=" * 80)
    print("COMPLIANT REGIONS BY JURISDICTION")
    print("=" * 80)
    for jur in REGION_POLICIES:
        for dt in [DataType.PLAYER_PII, DataType.FINANCIAL, DataType.KYC]:
            regions = router.get_compliant_regions(jur, dt)
            print(f"  {jur:4s} / {dt.value:20s}: {regions}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Multi-Jurisdiction Data Residency Router"
    )
    parser.add_argument("--demo", action="store_true", help="Run demo scenarios")
    parser.add_argument(
        "--test-routing",
        nargs=3,
        metavar=("JURISDICTION", "DATA_TYPE", "REGION"),
        help="Test a single routing decision",
    )
    parser.add_argument(
        "--show-rules", action="store_true", help="Show all jurisdiction rules"
    )

    args = parser.parse_args()

    if args.demo:
        run_demo()
    elif args.test_routing:
        jur, dt_str, region = args.test_routing
        try:
            dt = DataType(dt_str)
        except ValueError:
            print(f"Invalid data type. Choose from: {[d.value for d in DataType]}")
            return
        router = ResidencyRouter()
        req = DataRequest(
            request_id=str(uuid.uuid4()),
            jurisdiction=jur.upper(),
            data_type=dt,
            target_region=region,
            operation="write",
        )
        result = router.route(req)
        print(json.dumps(asdict(result), indent=2))
    elif args.show_rules:
        for jur, policy in REGION_POLICIES.items():
            print(f"\n{'='*60}")
            print(f"Jurisdiction: {jur}")
            print(f"  Primary regions:      {policy.primary_regions}")
            print(f"  Backup regions:       {policy.backup_regions}")
            print(f"  DR regions:           {policy.dr_regions}")
            print(f"  Cross-border:         {policy.cross_border_allowed}")
            print(f"  Conditions:           {policy.cross_border_conditions}")
            print(f"  Encryption:           {policy.encryption_algorithm}")
            if policy.data_type_overrides:
                print(f"  Data type overrides:")
                for dt, override in policy.data_type_overrides.items():
                    print(f"    {dt.value}: {override}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
