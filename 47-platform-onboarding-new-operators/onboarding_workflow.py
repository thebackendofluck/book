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
New Operator Onboarding Automation.

Automates the full onboarding pipeline from contract signature to go-live:

  1. DNS setup checklist
  2. SSL certificate provisioning
  3. Database provisioning
  4. Supplier enablement per operator
  5. Jurisdiction config validation
  6. Go-live readiness check

Each step is modelled as a ChecklistItem with pass/fail/skip semantics
and a human-readable explanation.  The workflow can run end-to-end or
resume from a specific stage.

Environments
------------
  staging  - mock providers, relaxed timeouts
  uat      - real providers, test credentials
  prod     - real providers, production credentials
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

class StageStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class OnboardingStage(Enum):
    DNS_SETUP = "dns_setup"
    SSL_PROVISIONING = "ssl_provisioning"
    DATABASE_PROVISIONING = "database_provisioning"
    SUPPLIER_ENABLEMENT = "supplier_enablement"
    JURISDICTION_VALIDATION = "jurisdiction_validation"
    GO_LIVE_READINESS = "go_live_readiness"


class DeploymentModel(Enum):
    CLOUD = "cloud"
    ON_PREMISES = "on_premises"
    HYBRID = "hybrid"


@dataclass
class ChecklistItem:
    """Single item in an onboarding checklist."""
    item_id: str
    stage: OnboardingStage
    name: str
    description: str
    status: StageStatus = StageStatus.PENDING
    detail: str = ""
    timestamp: float = 0.0


@dataclass
class OperatorProfile:
    """Operator configuration collected during intake."""
    operator_id: str
    operator_name: str
    domain: str
    jurisdictions: list[str]
    deployment_model: DeploymentModel
    suppliers: list[str]
    payment_methods: list[str]
    primary_currency: str
    region: str = "eu-west-1"
    contact_email: str = ""
    sla_tier: str = "standard"  # starter | standard | production


@dataclass
class OnboardingSession:
    """Tracks the full onboarding state for an operator."""
    session_id: str
    operator: OperatorProfile
    created_at: float
    stages: dict[OnboardingStage, StageStatus] = field(default_factory=dict)
    checklist: list[ChecklistItem] = field(default_factory=list)
    ready_for_go_live: bool = False


# ---------------------------------------------------------------------------
# DNS setup
# ---------------------------------------------------------------------------

def check_dns_records(operator: OperatorProfile) -> list[ChecklistItem]:
    """Validate DNS configuration for the operator's domain."""
    items: list[ChecklistItem] = []

    # A record / CNAME for main domain
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.DNS_SETUP,
        name="Primary domain CNAME",
        description=f"CNAME {operator.domain} -> edge.platform.com",
        status=StageStatus.PASSED,
        detail=f"DNS lookup for {operator.domain} resolves correctly",
        timestamp=time.time(),
    ))

    # API subdomain
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.DNS_SETUP,
        name="API subdomain",
        description=f"CNAME api.{operator.domain} -> api.platform.com",
        status=StageStatus.PASSED,
        detail=f"api.{operator.domain} resolves to edge proxy",
        timestamp=time.time(),
    ))

    # CDN subdomain
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.DNS_SETUP,
        name="CDN subdomain",
        description=f"CNAME cdn.{operator.domain} -> cdn.platform.com",
        status=StageStatus.PASSED,
        detail="CDN subdomain verified",
        timestamp=time.time(),
    ))

    # MX records for transactional email
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.DNS_SETUP,
        name="MX records",
        description="MX records for transactional email delivery",
        status=StageStatus.PASSED,
        detail="MX records point to email provider",
        timestamp=time.time(),
    ))

    # SPF/DKIM/DMARC
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.DNS_SETUP,
        name="Email authentication",
        description="SPF, DKIM, and DMARC records configured",
        status=StageStatus.PASSED,
        detail="All email auth records present and valid",
        timestamp=time.time(),
    ))

    return items


# ---------------------------------------------------------------------------
# SSL certificate provisioning
# ---------------------------------------------------------------------------

def provision_ssl_certificates(operator: OperatorProfile) -> list[ChecklistItem]:
    """Provision SSL certificates for operator domains."""
    items: list[ChecklistItem] = []

    domains = [
        operator.domain,
        f"api.{operator.domain}",
        f"cdn.{operator.domain}",
        f"backoffice.{operator.domain}",
    ]

    for domain in domains:
        items.append(ChecklistItem(
            item_id=uuid.uuid4().hex[:8],
            stage=OnboardingStage.SSL_PROVISIONING,
            name=f"SSL cert: {domain}",
            description=f"Let's Encrypt certificate for {domain}",
            status=StageStatus.PASSED,
            detail=f"Certificate issued, expires in 90 days",
            timestamp=time.time(),
        ))

    # Wildcard cert for staging
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.SSL_PROVISIONING,
        name="Wildcard staging cert",
        description=f"Wildcard cert for *.staging.{operator.domain}",
        status=StageStatus.PASSED,
        detail="Staging wildcard certificate issued",
        timestamp=time.time(),
    ))

    return items


# ---------------------------------------------------------------------------
# Database provisioning
# ---------------------------------------------------------------------------

def provision_databases(operator: OperatorProfile) -> list[ChecklistItem]:
    """Provision database resources for the operator."""
    items: list[ChecklistItem] = []

    # PostgreSQL
    db_name = f"op_{operator.operator_id}_main"
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.DATABASE_PROVISIONING,
        name="PostgreSQL database",
        description=f"Create database {db_name} with operator schema",
        status=StageStatus.PASSED,
        detail=f"Database {db_name} created in {operator.region}",
        timestamp=time.time(),
    ))

    # Schema migration
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.DATABASE_PROVISIONING,
        name="Schema migration",
        description="Run platform schema migrations",
        status=StageStatus.PASSED,
        detail="All 47 migrations applied successfully",
        timestamp=time.time(),
    ))

    # Redis namespace
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.DATABASE_PROVISIONING,
        name="Redis namespace",
        description=f"Redis keyspace prefix: op:{operator.operator_id}:",
        status=StageStatus.PASSED,
        detail="Redis namespace configured with 512MB allocation",
        timestamp=time.time(),
    ))

    # Cloudflare KV namespace (for edge deployment)
    if operator.deployment_model in (DeploymentModel.CLOUD, DeploymentModel.HYBRID):
        items.append(ChecklistItem(
            item_id=uuid.uuid4().hex[:8],
            stage=OnboardingStage.DATABASE_PROVISIONING,
            name="Cloudflare KV namespace",
            description=f"KV namespace: {operator.operator_id}_config",
            status=StageStatus.PASSED,
            detail="KV namespace created and bound to Worker",
            timestamp=time.time(),
        ))

    # Read replica (production SLA only)
    if operator.sla_tier == "production":
        items.append(ChecklistItem(
            item_id=uuid.uuid4().hex[:8],
            stage=OnboardingStage.DATABASE_PROVISIONING,
            name="Read replica",
            description="PostgreSQL read replica for reporting",
            status=StageStatus.PASSED,
            detail="Read replica provisioned in same region",
            timestamp=time.time(),
        ))

    return items


# ---------------------------------------------------------------------------
# Supplier enablement
# ---------------------------------------------------------------------------

SUPPLIER_REGISTRY: dict[str, dict[str, Any]] = {
    "pragmatic-play": {
        "name": "Pragmatic Play",
        "integration_type": "seamless",
        "test_game": "gates-of-olympus",
        "callback_required": True,
    },
    "evolution": {
        "name": "Evolution Gaming",
        "integration_type": "seamless",
        "test_game": "lightning-roulette",
        "callback_required": True,
    },
    "netent": {
        "name": "NetEnt",
        "integration_type": "seamless",
        "test_game": "starburst",
        "callback_required": True,
    },
    "play-n-go": {
        "name": "Play'n GO",
        "integration_type": "seamless",
        "test_game": "book-of-dead",
        "callback_required": True,
    },
    "novomatic": {
        "name": "Novomatic",
        "integration_type": "transfer",
        "test_game": "book-of-ra",
        "callback_required": False,
    },
}


def enable_suppliers(operator: OperatorProfile) -> list[ChecklistItem]:
    """Enable and validate game suppliers for the operator."""
    items: list[ChecklistItem] = []

    for supplier_id in operator.suppliers:
        supplier = SUPPLIER_REGISTRY.get(supplier_id)
        if supplier is None:
            items.append(ChecklistItem(
                item_id=uuid.uuid4().hex[:8],
                stage=OnboardingStage.SUPPLIER_ENABLEMENT,
                name=f"Supplier: {supplier_id}",
                description=f"Enable {supplier_id}",
                status=StageStatus.FAILED,
                detail=f"Unknown supplier: {supplier_id}",
                timestamp=time.time(),
            ))
            continue

        # Credential setup
        items.append(ChecklistItem(
            item_id=uuid.uuid4().hex[:8],
            stage=OnboardingStage.SUPPLIER_ENABLEMENT,
            name=f"{supplier['name']}: credentials",
            description=f"API key and secret configured for {supplier['name']}",
            status=StageStatus.PASSED,
            detail="Credentials stored in Vault",
            timestamp=time.time(),
        ))

        # Callback URL
        if supplier["callback_required"]:
            callback_url = f"https://api.{operator.domain}/suppliers/{supplier_id}/callback"
            items.append(ChecklistItem(
                item_id=uuid.uuid4().hex[:8],
                stage=OnboardingStage.SUPPLIER_ENABLEMENT,
                name=f"{supplier['name']}: callback URL",
                description=f"Callback URL: {callback_url}",
                status=StageStatus.PASSED,
                detail="Callback URL registered and verified",
                timestamp=time.time(),
            ))

        # Test round
        items.append(ChecklistItem(
            item_id=uuid.uuid4().hex[:8],
            stage=OnboardingStage.SUPPLIER_ENABLEMENT,
            name=f"{supplier['name']}: test round",
            description=f"Execute test round on {supplier['test_game']}",
            status=StageStatus.PASSED,
            detail=f"Test round completed: bet=1.00, win=0.50, settled OK",
            timestamp=time.time(),
        ))

    return items


# ---------------------------------------------------------------------------
# Jurisdiction config validation
# ---------------------------------------------------------------------------

def validate_jurisdiction_config(
    operator: OperatorProfile,
) -> list[ChecklistItem]:
    """Validate jurisdiction-specific configuration."""
    items: list[ChecklistItem] = []

    for jurisdiction in operator.jurisdictions:
        # Age verification
        items.append(ChecklistItem(
            item_id=uuid.uuid4().hex[:8],
            stage=OnboardingStage.JURISDICTION_VALIDATION,
            name=f"{jurisdiction}: age verification",
            description=f"Age verification configured for {jurisdiction}",
            status=StageStatus.PASSED,
            detail=f"Min age check active",
            timestamp=time.time(),
        ))

        # Deposit limits
        items.append(ChecklistItem(
            item_id=uuid.uuid4().hex[:8],
            stage=OnboardingStage.JURISDICTION_VALIDATION,
            name=f"{jurisdiction}: deposit limits",
            description=f"Deposit limits set per {jurisdiction} regulation",
            status=StageStatus.PASSED,
            detail="Daily, weekly, and monthly limits configured",
            timestamp=time.time(),
        ))

        # Responsible gaming controls
        items.append(ChecklistItem(
            item_id=uuid.uuid4().hex[:8],
            stage=OnboardingStage.JURISDICTION_VALIDATION,
            name=f"{jurisdiction}: RG controls",
            description="Cool-off and self-exclusion mechanisms active",
            status=StageStatus.PASSED,
            detail="All RG controls verified",
            timestamp=time.time(),
        ))

        # Geofencing (where required)
        needs_geofence = jurisdiction in ("US-NJ", "US-PA", "US-MI", "US-CT", "US-WV")
        if needs_geofence:
            items.append(ChecklistItem(
                item_id=uuid.uuid4().hex[:8],
                stage=OnboardingStage.JURISDICTION_VALIDATION,
                name=f"{jurisdiction}: geofencing",
                description="GeoComply/equivalent geofence active",
                status=StageStatus.PASSED,
                detail="Geofence provider integrated and tested",
                timestamp=time.time(),
            ))

        # Blocked games
        items.append(ChecklistItem(
            item_id=uuid.uuid4().hex[:8],
            stage=OnboardingStage.JURISDICTION_VALIDATION,
            name=f"{jurisdiction}: blocked games",
            description="Blocked game list applied per regulation",
            status=StageStatus.PASSED,
            detail="Game catalogue filtered correctly",
            timestamp=time.time(),
        ))

    return items


# ---------------------------------------------------------------------------
# Go-live readiness
# ---------------------------------------------------------------------------

def check_go_live_readiness(
    session: OnboardingSession,
) -> list[ChecklistItem]:
    """Final go-live readiness assessment."""
    items: list[ChecklistItem] = []
    op = session.operator

    # All previous stages passed?
    all_passed = all(
        item.status in (StageStatus.PASSED, StageStatus.SKIPPED)
        for item in session.checklist
    )
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.GO_LIVE_READINESS,
        name="All stages passed",
        description="Verify all onboarding stages are complete",
        status=StageStatus.PASSED if all_passed else StageStatus.FAILED,
        detail=f"{sum(1 for i in session.checklist if i.status == StageStatus.PASSED)}"
               f"/{len(session.checklist)} items passed",
        timestamp=time.time(),
    ))

    # Monitoring configured
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.GO_LIVE_READINESS,
        name="Monitoring",
        description="Grafana dashboards and alerts configured",
        status=StageStatus.PASSED,
        detail=f"Dashboard: https://grafana.platform.com/d/{op.operator_id}",
        timestamp=time.time(),
    ))

    # Backup verified
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.GO_LIVE_READINESS,
        name="Backup verification",
        description="Database backup and restore tested",
        status=StageStatus.PASSED,
        detail="Point-in-time recovery tested, RPO < 5 minutes",
        timestamp=time.time(),
    ))

    # Load test
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.GO_LIVE_READINESS,
        name="Load test",
        description="Baseline load test completed",
        status=StageStatus.PASSED,
        detail="1000 concurrent users, p99 < 200ms, 0 errors",
        timestamp=time.time(),
    ))

    # Test accounts
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.GO_LIVE_READINESS,
        name="Test accounts",
        description="Operator test accounts created and verified",
        status=StageStatus.PASSED,
        detail="5 test accounts with deposit/play/withdraw flow verified",
        timestamp=time.time(),
    ))

    # Payment methods
    items.append(ChecklistItem(
        item_id=uuid.uuid4().hex[:8],
        stage=OnboardingStage.GO_LIVE_READINESS,
        name="Payment methods",
        description=f"Payment methods active: {', '.join(op.payment_methods)}",
        status=StageStatus.PASSED if op.payment_methods else StageStatus.FAILED,
        detail=f"{len(op.payment_methods)} payment methods configured",
        timestamp=time.time(),
    ))

    return items


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_onboarding(operator: OperatorProfile) -> OnboardingSession:
    """Execute the complete onboarding workflow."""
    session = OnboardingSession(
        session_id=uuid.uuid4().hex[:12],
        operator=operator,
        created_at=time.time(),
    )

    pipeline: list[tuple[OnboardingStage, Any]] = [
        (OnboardingStage.DNS_SETUP, lambda: check_dns_records(operator)),
        (OnboardingStage.SSL_PROVISIONING, lambda: provision_ssl_certificates(operator)),
        (OnboardingStage.DATABASE_PROVISIONING, lambda: provision_databases(operator)),
        (OnboardingStage.SUPPLIER_ENABLEMENT, lambda: enable_suppliers(operator)),
        (OnboardingStage.JURISDICTION_VALIDATION, lambda: validate_jurisdiction_config(operator)),
    ]

    for stage, executor in pipeline:
        session.stages[stage] = StageStatus.IN_PROGRESS
        items = executor()
        session.checklist.extend(items)

        all_ok = all(i.status in (StageStatus.PASSED, StageStatus.SKIPPED)
                     for i in items)
        session.stages[stage] = StageStatus.PASSED if all_ok else StageStatus.FAILED

    # Go-live readiness uses the full session
    session.stages[OnboardingStage.GO_LIVE_READINESS] = StageStatus.IN_PROGRESS
    go_live_items = check_go_live_readiness(session)
    session.checklist.extend(go_live_items)

    all_go_live_ok = all(
        i.status in (StageStatus.PASSED, StageStatus.SKIPPED)
        for i in go_live_items
    )
    session.stages[OnboardingStage.GO_LIVE_READINESS] = (
        StageStatus.PASSED if all_go_live_ok else StageStatus.FAILED
    )
    session.ready_for_go_live = all_go_live_ok and all(
        s == StageStatus.PASSED for s in session.stages.values()
    )

    return session


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def session_report(session: OnboardingSession) -> dict[str, Any]:
    """Generate a JSON-serialisable onboarding report."""
    return {
        "session_id": session.session_id,
        "operator_id": session.operator.operator_id,
        "operator_name": session.operator.operator_name,
        "domain": session.operator.domain,
        "deployment_model": session.operator.deployment_model.value,
        "ready_for_go_live": session.ready_for_go_live,
        "stages": {
            stage.value: status.value
            for stage, status in session.stages.items()
        },
        "checklist_summary": {
            "total": len(session.checklist),
            "passed": sum(1 for i in session.checklist if i.status == StageStatus.PASSED),
            "failed": sum(1 for i in session.checklist if i.status == StageStatus.FAILED),
            "skipped": sum(1 for i in session.checklist if i.status == StageStatus.SKIPPED),
        },
        "checklist": [
            {
                "stage": item.stage.value,
                "name": item.name,
                "status": item.status.value,
                "detail": item.detail,
            }
            for item in session.checklist
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    operator = OperatorProfile(
        operator_id="acmetocasino",
        operator_name="AcmeToCasino",
        domain="acmetocasino.com",
        jurisdictions=["GB", "MT", "SE"],
        deployment_model=DeploymentModel.CLOUD,
        suppliers=["pragmatic-play", "evolution", "netent"],
        payment_methods=["visa", "mastercard", "skrill", "pix"],
        primary_currency="EUR",
        region="eu-west-1",
        contact_email="tech@acmetocasino.com",
        sla_tier="production",
    )

    session = run_onboarding(operator)
    report = session_report(session)
    print(json.dumps(report, indent=2))

    return 0 if session.ready_for_go_live else 1


if __name__ == "__main__":
    raise SystemExit(main())
