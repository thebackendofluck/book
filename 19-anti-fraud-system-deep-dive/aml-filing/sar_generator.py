# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
sar_generator.py — Multi-jurisdiction Suspicious Activity Report generator.

Jurisdiction:       United States, United Kingdom, European Union, Brazil
Regulators:
  - US:  FinCEN (Financial Crimes Enforcement Network)
         https://www.fincen.gov/resources/filing-information/
         suspicious-activity-report
  - UK:  National Crime Agency (NCA) — DAML / Consent SAR
         https://www.nationalcrimeagency.gov.uk/what-we-do/
         crime-threats/money-laundering-and-terrorist-financing
  - EU:  Per-jurisdiction Financial Intelligence Units (FIUs)
         Egmont Group member FIUs; goAML platform
         https://www.fatf-gafi.org/
  - BR:  COAF (Conselho de Controle de Atividades Financeiras)
         https://www.gov.br/coaf/

Regulation refs:
  - US: Bank Secrecy Act (BSA) 31 U.S.C. § 5318(g); FinCEN 31 CFR 1021.320
    https://www.ecfr.gov/current/title-31/subtitle-B/chapter-X/part-1021
  - UK: Proceeds of Crime Act 2002 (POCA) §§ 330-331 (DAML consent SARs)
    https://www.legislation.gov.uk/ukpga/2002/29/part/7
  - EU: 6AMLD (Directive 2018/843/EU); AMLA Regulation (EU) 2024/1624
    https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L0843
  - BR: Lei 9.613/1998 (Lei de Lavagem de Dinheiro) as amended by Lei 12.683/2012
    https://www.planalto.gov.br/ccivil_03/leis/l9613.htm
Penalties:
  - US: FinCEN civil penalties up to $1,000,000/day; DOJ criminal prosecution
  - UK: Unlimited fines; up to 14 years imprisonment (POCA s.340)
  - EU: Administrative fines up to 10% of annual turnover (AMLA 2024)
  - BR: COAF fines up to R$20 million; BACEN sanctions

Filing deadlines:
  - US FinCEN SAR:  30 days from date of detection; 60 days if no suspect
  - UK NCA SAR:     Must file before acting — "consent" model (DAML)
  - EU FIU:         Without delay; most FIUs use goAML portal
  - BR COAF:        48 hours for transactions ≥ R$50,000 (gambling)

Book chapter:  Chapter 19 — Anti-Fraud & Compliance Systems
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Jurisdiction(str, Enum):
    US = "US"
    UK = "UK"
    EU = "EU"
    BR = "BR"


class SarType(str, Enum):
    FINCEN_SAR = "fincen_sar"          # US FinCEN BSA form 111
    NCA_DAML = "nca_daml"              # UK Defence Against Money Laundering
    EU_FIU = "eu_fiu"                  # EU goAML / per-jurisdiction
    COAF = "coaf"                      # Brazil COAF


class ActivityType(str, Enum):
    STRUCTURING = "structuring"
    RAPID_MOVEMENT = "rapid_movement"
    UNUSUAL_DEPOSIT_PATTERN = "unusual_deposit_pattern"
    THIRD_PARTY_FUNDING = "third_party_funding"
    PEP_ACTIVITY = "pep_activity"
    SANCTIONS_PROXIMITY = "sanctions_proximity"
    SMURFING = "smurfing"
    LAYERING = "layering"
    PLACEMENT = "placement"
    UNKNOWN_SOURCE_OF_FUNDS = "unknown_source_of_funds"


class SarStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    FILED = "filed"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SuspiciousActivity:
    """Description of the suspicious behaviour that triggered the SAR."""
    activity_type: ActivityType
    description: str
    amount: Decimal
    currency: str
    transaction_ids: list[str]
    first_observed_at: datetime
    last_observed_at: datetime
    evidence_hashes: list[str] = field(default_factory=list)  # SHA-256 of evidence docs


@dataclass
class SubjectIdentity:
    """Subject (player/account) identity block for the SAR."""
    player_id: str
    first_name: str
    last_name: str
    date_of_birth: str
    nationality: str
    id_document_type: str
    id_document_number_hash: str     # never store raw ID numbers
    address: str
    is_pep: bool = False
    is_sanctioned: bool = False
    risk_rating: str = "high"


@dataclass
class SarReport:
    """
    Unified SAR record that serialises to the format required by each
    jurisdiction's FIU filing system.
    """
    sar_id: str
    jurisdiction: Jurisdiction
    sar_type: SarType
    subject: SubjectIdentity
    activity: SuspiciousActivity
    filing_deadline: datetime
    created_at: datetime
    status: SarStatus = SarStatus.DRAFT
    filed_at: Optional[datetime] = None
    fiu_reference: Optional[str] = None
    mlro_id: Optional[str] = None             # Money Laundering Reporting Officer
    narrative: str = ""
    continuation_report: bool = False         # true if follow-up to prior SAR
    prior_sar_id: Optional[str] = None
    audit_trail: list[dict[str, Any]] = field(default_factory=list)

    def record_event(self, event: str, detail: dict[str, Any] | None = None) -> None:
        self.audit_trail.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "detail": detail or {},
        })


# ---------------------------------------------------------------------------
# Jurisdiction-specific serialisers
# ---------------------------------------------------------------------------

def _build_fincen_payload(report: SarReport) -> dict[str, Any]:
    """
    Serialise to FinCEN BSA SAR form 111 (FinCEN XML schema v2.2).

    FinCEN filing: https://bsaefiling.fincen.treas.gov/
    Filing window: 30 days from detection, 60 if no identified suspect.
    """
    return {
        "form_type": "FINCEN_SAR_111",
        "filing_institution": {
            "institution_type": "online_gambling_operator",
            "ein": "<<EIN_FROM_CONFIG>>",
            "name": "<<OPERATOR_LEGAL_NAME>>",
            "address": "<<OPERATOR_ADDRESS>>",
        },
        "subject": {
            "first_name": report.subject.first_name,
            "last_name": report.subject.last_name,
            "dob": report.subject.date_of_birth,
            "id_type": report.subject.id_document_type,
            "id_hash": report.subject.id_document_number_hash,
            "address": report.subject.address,
        },
        "suspicious_activity": {
            "type": report.activity.activity_type.value,
            "amount_usd": str(report.activity.amount),
            "begin_date": report.activity.first_observed_at.isoformat(),
            "end_date": report.activity.last_observed_at.isoformat(),
            "description": report.narrative or report.activity.description,
        },
        "transaction_ids": report.activity.transaction_ids,
        "filing_date": datetime.now(timezone.utc).isoformat(),
        "sar_id": report.sar_id,
    }


def _build_nca_daml_payload(report: SarReport) -> dict[str, Any]:
    """
    Serialise to NCA DAML (Defence Against Money Laundering) SAR.

    Filed via NCA SAR Online: https://www.ukfiu.co.uk/
    DAML consent must be obtained BEFORE the suspected transaction proceeds.
    Consent period: 7 working days, extendable to 31 days (moratorium).
    """
    return {
        "form_type": "NCA_DAML_SAR",
        "report_type": "daml_consent" if report.activity.activity_type in (
            ActivityType.PLACEMENT, ActivityType.LAYERING
        ) else "standard_disclosure",
        "reporter": {
            "organisation": "<<OPERATOR_NAME>>",
            "mlro_name": "<<MLRO_NAME>>",
            "contact_number": "<<MLRO_PHONE>>",
        },
        "subject": {
            "first_name": report.subject.first_name,
            "last_name": report.subject.last_name,
            "dob": report.subject.date_of_birth,
            "nationality": report.subject.nationality,
        },
        "property_value_gbp": str(report.activity.amount),
        "suspicion_details": report.narrative or report.activity.description,
        "date_of_suspicion": report.activity.first_observed_at.isoformat(),
        "poca_section": "330",    # 330 = employed in regulated sector
        "sar_ref": report.sar_id,
    }


def _build_eu_fiu_payload(report: SarReport) -> dict[str, Any]:
    """
    Serialise to EU goAML XML format for submission to the local FIU.

    Each EU member state has its own FIU portal; the goAML schema is standard.
    Reference: https://goaml.unodc.org/
    """
    return {
        "schema": "goAML_v4",
        "report_code": "STR",          # Suspicious Transaction Report
        "reporting_entity": {
            "type": "gambling_operator",
            "licence_number": "<<MGA_OR_LOCAL_LICENCE>>",
        },
        "reporting_person": {
            "name": report.subject.first_name + " " + report.subject.last_name,
            "dob": report.subject.date_of_birth,
            "nationality": report.subject.nationality,
            "id_type": report.subject.id_document_type,
        },
        "transactions": [
            {"tx_id": txid} for txid in report.activity.transaction_ids
        ],
        "amount": str(report.activity.amount),
        "currency": report.activity.currency,
        "suspicion_type": report.activity.activity_type.value,
        "narrative": report.narrative or report.activity.description,
        "report_date": datetime.now(timezone.utc).isoformat(),
    }


def _build_coaf_payload(report: SarReport) -> dict[str, Any]:
    """
    Serialise to COAF (Brazil) suspicious transaction communication.

    Filed via SISCOAF: https://www.gov.br/coaf/pt-br/siscoaf
    48-hour deadline for gambling-related transactions ≥ R$50,000.
    """
    return {
        "tipo_relatorio": "CIF",        # Comunicação de Irregularidades Financeiras
        "numero_controle": report.sar_id,
        "cnpj_comunicante": "<<OPERATOR_CNPJ>>",
        "pessoa": {
            "nome": report.subject.first_name + " " + report.subject.last_name,
            "data_nascimento": report.subject.date_of_birth,
            "cpf_hash": report.subject.id_document_number_hash,
            "nacionalidade": report.subject.nationality,
        },
        "operacao": {
            "valor_brl": str(report.activity.amount),
            "tipo": report.activity.activity_type.value,
            "data_operacao": report.activity.first_observed_at.isoformat(),
            "descricao": report.narrative or report.activity.description,
        },
        "transacoes": report.activity.transaction_ids,
        "data_comunicacao": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# SAR generator
# ---------------------------------------------------------------------------

_FILING_DEADLINES: dict[Jurisdiction, int] = {
    Jurisdiction.US: 30,    # days
    Jurisdiction.UK: 0,     # DAML: before the act (immediate)
    Jurisdiction.EU: 1,     # without delay — treat as 1 day
    Jurisdiction.BR: 2,     # 48 hours
}

_BUILDERS = {
    SarType.FINCEN_SAR: _build_fincen_payload,
    SarType.NCA_DAML:   _build_nca_daml_payload,
    SarType.EU_FIU:     _build_eu_fiu_payload,
    SarType.COAF:       _build_coaf_payload,
}

_JURISDICTION_SAR_TYPE: dict[Jurisdiction, SarType] = {
    Jurisdiction.US: SarType.FINCEN_SAR,
    Jurisdiction.UK: SarType.NCA_DAML,
    Jurisdiction.EU: SarType.EU_FIU,
    Jurisdiction.BR: SarType.COAF,
}


class SarGenerator:
    """
    Creates SAR records and serialises them to the appropriate
    jurisdiction-specific filing format.

    Workflow:
      1. create_sar() — draft record
      2. add_narrative() — MLRO documents the suspicion
      3. approve() — MLRO signs off
      4. serialise() — produces the filing payload
      5. mark_filed() — records confirmation number from the FIU
    """

    def create_sar(
        self,
        jurisdiction: Jurisdiction,
        subject: SubjectIdentity,
        activity: SuspiciousActivity,
        mlro_id: str,
    ) -> SarReport:
        sar_id = f"SAR-{jurisdiction.value}-{uuid.uuid4().hex[:10].upper()}"
        now = datetime.now(timezone.utc)
        deadline_days = _FILING_DEADLINES[jurisdiction]

        report = SarReport(
            sar_id=sar_id,
            jurisdiction=jurisdiction,
            sar_type=_JURISDICTION_SAR_TYPE[jurisdiction],
            subject=subject,
            activity=activity,
            filing_deadline=now + timedelta(days=deadline_days),
            created_at=now,
            mlro_id=mlro_id,
        )
        report.record_event("sar_created", {
            "jurisdiction": jurisdiction.value,
            "activity_type": activity.activity_type.value,
            "amount": str(activity.amount),
        })
        log.info("sar: report created",
                 sar_id=sar_id,
                 jurisdiction=jurisdiction.value,
                 deadline=report.filing_deadline.isoformat())
        return report

    def add_narrative(self, report: SarReport, narrative: str) -> SarReport:
        """MLRO adds the formal suspicion narrative."""
        report.narrative = narrative
        report.status = SarStatus.PENDING_REVIEW
        report.record_event("narrative_added",
                            {"length": len(narrative)})
        return report

    def approve(self, report: SarReport, approver_id: str) -> SarReport:
        """Senior MLRO or Compliance Officer approves the report for filing."""
        report.status = SarStatus.APPROVED
        report.record_event("approved", {"approver_id": approver_id})
        log.info("sar: approved for filing",
                 sar_id=report.sar_id, approver=approver_id)
        return report

    def serialise(self, report: SarReport) -> dict[str, Any]:
        """Produce the jurisdiction-specific filing payload."""
        builder = _BUILDERS.get(report.sar_type)
        if not builder:
            raise ValueError(f"No builder for SAR type: {report.sar_type}")
        payload = builder(report)
        report.record_event("serialised",
                            {"target_format": report.sar_type.value})
        return payload

    def mark_filed(
        self, report: SarReport, fiu_reference: str
    ) -> SarReport:
        """Record the FIU confirmation reference after successful submission."""
        report.status = SarStatus.FILED
        report.filed_at = datetime.now(timezone.utc)
        report.fiu_reference = fiu_reference
        report.record_event("filed", {
            "fiu_reference": fiu_reference,
            "filed_at": report.filed_at.isoformat(),
        })
        log.info("sar: filed successfully",
                 sar_id=report.sar_id,
                 fiu_reference=fiu_reference,
                 jurisdiction=report.jurisdiction.value)
        return report

    def check_filing_deadline(self, report: SarReport) -> bool:
        """Return True if the filing deadline has passed without filing."""
        if report.status == SarStatus.FILED:
            return False
        overdue = datetime.now(timezone.utc) > report.filing_deadline
        if overdue:
            log.error("sar: FILING DEADLINE EXCEEDED",
                      sar_id=report.sar_id,
                      jurisdiction=report.jurisdiction.value,
                      deadline=report.filing_deadline.isoformat())
        return overdue


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    gen = SarGenerator()

    subject = SubjectIdentity(
        player_id="player-us-0042",
        first_name="John",
        last_name="Smith",
        date_of_birth="1978-11-05",
        nationality="US",
        id_document_type="passport",
        id_document_number_hash=hashlib.sha256(b"123456789").hexdigest(),
        address="123 Main St, Las Vegas, NV 89101",
        risk_rating="high",
    )

    activity = SuspiciousActivity(
        activity_type=ActivityType.STRUCTURING,
        description=(
            "Player made 9 consecutive deposits of $999 over 3 days, "
            "consistent with structuring to avoid CTR threshold."
        ),
        amount=Decimal("8991.00"),
        currency="USD",
        transaction_ids=["tx-001", "tx-002", "tx-003", "tx-004",
                         "tx-005", "tx-006", "tx-007", "tx-008", "tx-009"],
        first_observed_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
        last_observed_at=datetime(2026, 4, 3, 18, 0, tzinfo=timezone.utc),
    )

    report = gen.create_sar(Jurisdiction.US, subject, activity, mlro_id="mlro-001")
    gen.add_narrative(report, activity.description)
    gen.approve(report, "compliance-director-001")
    payload = gen.serialise(report)

    print(f"SAR {report.sar_id} ready to file")
    print(f"Filing deadline: {report.filing_deadline.isoformat()}")
    print(f"Payload keys: {list(payload.keys())}")


if __name__ == "__main__":
    _demo()
