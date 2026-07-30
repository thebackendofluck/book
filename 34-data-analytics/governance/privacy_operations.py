# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# REGULATORY REQUIREMENT: GDPR + AML/KYC retention obligations
# Regulation:  GDPR (EU) 2016/679 Arts. 15-21 (data subject rights);
#              Art. 5(1)(e) — storage limitation principle;
#              Art. 32 — security of processing (data lake specific)
#              4AMLD (EU 2015/849) Art. 40 — 5-year retention of CDD records
#              5AMLD (EU 2018/843) — enhanced due diligence extensions
#              NOTE: 6AMLD (entered into force 9 July 2024) applies from
#              10 July 2027 — update retention references when applicable.
#              FATF Recommendation 11 — CDD record-keeping (5 years)
#              MGA Directive 3/2018 — game history retention requirements
#              UKGC LCCP — responsible gaming records retention
# Purpose:     Privacy operations for the iGaming data lake — handles GDPR
#              rights requests (SAR, erasure, portability, restriction, consent
#              withdrawal) in the context of a data lake architecture.
#              CRITICAL TENSION: Data lakes aggregate and retain data for analytics;
#              GDPR storage limitation (Art. 5(1)(e)) requires data not be kept
#              longer than necessary. This module implements the reconciliation:
#              - AML records: retained 7 years (note: module says 7 years,
#                which aligns with UKGC/UK MLR 2017 Reg.40; GDPR/4AMLD = 5 years;
#                use 7 years as the safe upper bound for UK-licensed operators)
#              - Game history: anonymised after 5 years (MGA Directive 3/2018)
#              - Responsible gaming: 5 years (UKGC LCCP)
# Penalty:     GDPR Art. 83(5): up to €20M or 4% global annual turnover
#              Art. 83(4): up to €10M or 2% for Art. 5 storage limitation failures
# Jurisdictions: All EU/EEA (MGA, Sweden, Netherlands), UK (UKGC)
#
# References:
#   GDPR Full Text: https://gdpr-info.eu/
#   Art. 15 (Right of Access): https://gdpr-info.eu/art-15-gdpr/
#   Art. 17 (Right to Erasure): https://gdpr-info.eu/art-17-gdpr/
#   Art. 20 (Data Portability): https://gdpr-info.eu/art-20-gdpr/
#   Art. 83 (Penalties): https://gdpr-info.eu/art-83-gdpr/
#   5AMLD: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L0843
#   6AMLD: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L1673
#   FATF Recommendations: https://www.fatf-gafi.org/en/recommendations.html
#   MGA Player Protection Directive: https://www.mga.org.mt/legislation/subsidiary-legislation/
#   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
# =============================================================================
"""
GDPR Privacy Operations Module for iGaming Data Lake

This module implements privacy operations required by GDPR and
other data protection regulations including:

1. Right to Access (GDPR Art. 15) - Subject Access Request (SAR)
2. Right to Rectification (GDPR Art. 16) - Data correction
3. Right to Erasure (GDPR Art. 17) - Right to be forgotten
4. Right to Portability (GDPR Art. 20) - Data export
5. Right to Restriction (GDPR Art. 18) - Processing limitation
6. Consent Management - Track and enforce consent

iGaming Specific Considerations:
- Balance privacy with regulatory record-keeping (7 years)
- AML/KYC requirements may override erasure rights
- Gambling commission audit trail requirements

Usage:
    privacy_ops = PrivacyOperations(config)
    result = await privacy_ops.process_sar_request(subject_id)
    result = await privacy_ops.process_erasure_request(subject_id)

Dependencies:
    pip install boto3 pandas pyarrow
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

import boto3  # ty:ignore[unresolved-import]
from botocore.config import Config  # ty:ignore[unresolved-import]


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================


class PrivacyRequestType(Enum):
    """Types of privacy requests under GDPR."""

    ACCESS = "access"              # Art. 15 - Right to access
    RECTIFICATION = "rectification"  # Art. 16 - Right to rectification
    ERASURE = "erasure"            # Art. 17 - Right to erasure
    RESTRICTION = "restriction"    # Art. 18 - Right to restriction
    PORTABILITY = "portability"    # Art. 20 - Right to portability
    OBJECTION = "objection"        # Art. 21 - Right to object
    CONSENT_WITHDRAWAL = "consent_withdrawal"  # Withdraw consent


class RequestStatus(Enum):
    """Status of privacy request."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    PARTIALLY_COMPLETED = "partially_completed"


class ErasureBlocker(Enum):
    """Reasons why erasure may be blocked."""

    REGULATORY_RETENTION = "regulatory_retention"  # Legal requirement to keep
    AML_INVESTIGATION = "aml_investigation"        # Active AML case
    LEGAL_PROCEEDINGS = "legal_proceedings"        # Litigation hold
    CONTRACTUAL_OBLIGATION = "contractual"         # Active contract
    PUBLIC_INTEREST = "public_interest"            # Public interest archiving


class DataCategory(Enum):
    """Categories of personal data."""

    IDENTITY = "identity"              # Name, DOB, ID docs
    CONTACT = "contact"                # Email, phone, address
    FINANCIAL = "financial"            # Transactions, balances
    BEHAVIORAL = "behavioral"          # Activity logs, preferences
    TECHNICAL = "technical"            # IP, device info, cookies
    MARKETING = "marketing"            # Consent, preferences
    GAMING = "gaming"                  # Bets, wins, games played


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class PrivacyRequest:
    """Privacy request details."""

    request_id: str
    subject_id: str  # Player ID
    request_type: PrivacyRequestType
    status: RequestStatus = RequestStatus.PENDING

    # Request metadata
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    requested_by: str = ""  # Who submitted (subject or representative)
    verification_method: str = ""  # How identity was verified

    # Processing details
    processed_at: Optional[datetime] = None
    processed_by: str = ""
    processing_notes: str = ""

    # Scope
    data_categories: list[DataCategory] = field(default_factory=list)
    specific_datasets: list[str] = field(default_factory=list)

    # Results
    data_locations_found: list[str] = field(default_factory=list)
    records_affected: int = 0
    blockers: list[ErasureBlocker] = field(default_factory=list)

    # Compliance
    deadline: Optional[datetime] = None  # GDPR: 30 days
    extension_reason: Optional[str] = None


@dataclass
class DataSubjectRecord:
    """Record of data held about a subject."""

    subject_id: str
    data_category: DataCategory
    dataset_name: str
    location: str  # S3 path or database table
    record_count: int
    first_collected: datetime
    last_updated: datetime
    legal_basis: str  # consent, contract, legal_obligation, etc.
    retention_period_days: int
    can_be_deleted: bool = True
    deletion_blocker: Optional[ErasureBlocker] = None


@dataclass
class PrivacyOperationResult:
    """Result of a privacy operation."""

    request_id: str
    operation_type: PrivacyRequestType
    success: bool
    subject_id: str

    # Statistics
    datasets_processed: int = 0
    records_affected: int = 0
    errors: list[str] = field(default_factory=list)

    # For access/portability
    export_location: Optional[str] = None
    export_format: str = "json"
    export_size_bytes: int = 0

    # For erasure
    records_deleted: int = 0
    records_anonymized: int = 0
    records_retained: int = 0
    retention_reasons: list[str] = field(default_factory=list)

    # Audit
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    audit_log_id: str = ""


@dataclass
class ConsentRecord:
    """Record of consent given by data subject."""

    consent_id: str
    subject_id: str
    purpose: str  # marketing, profiling, third_party_sharing, etc.
    granted: bool
    granted_at: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
    channel: str = ""  # web, app, email
    version: str = ""  # Terms/privacy policy version
    ip_address: str = ""
    proof_location: str = ""  # S3 path to consent proof


# =============================================================================
# PRIVACY OPERATIONS
# =============================================================================


class PrivacyOperations:
    """
    GDPR Privacy Operations Handler.

    Processes privacy requests while balancing data protection
    rights with iGaming regulatory requirements.
    """

    def __init__(
        self,
        data_lake_bucket: str,
        audit_bucket: str,
        region: str = "us-east-1",
    ):
        self.data_lake_bucket = data_lake_bucket
        self.audit_bucket = audit_bucket
        self.region = region
        self.logger = logging.getLogger(__name__)

        boto_config = Config(retries={"max_attempts": 3, "mode": "adaptive"})

        self.s3_client = boto3.client("s3", region_name=region, config=boto_config)
        self.dynamodb = boto3.resource("dynamodb", region_name=region, config=boto_config)
        self.glue_client = boto3.client("glue", region_name=region, config=boto_config)
        self.athena_client = boto3.client("athena", region_name=region, config=boto_config)

        # iGaming regulatory retention periods (days)
        self.retention_periods = {
            DataCategory.FINANCIAL: 2555,     # 7 years
            DataCategory.IDENTITY: 1825,      # 5 years after closure
            DataCategory.GAMING: 1825,        # 5 years
            DataCategory.BEHAVIORAL: 730,     # 2 years
            DataCategory.TECHNICAL: 365,      # 1 year
            DataCategory.MARKETING: 1095,     # 3 years after consent
            DataCategory.CONTACT: 1825,       # 5 years
        }

    async def process_request(self, request: PrivacyRequest) -> PrivacyOperationResult:
        """
        Process a privacy request.

        Args:
            request: Privacy request details

        Returns:
            Operation result
        """
        self.logger.info(f"Processing {request.request_type.value} request for {request.subject_id}")

        # Set deadline if not set (GDPR: 30 days)
        if not request.deadline:
            request.deadline = request.requested_at + timedelta(days=30)

        # Dispatch to appropriate handler
        handlers = {
            PrivacyRequestType.ACCESS: self._handle_access_request,
            PrivacyRequestType.RECTIFICATION: self._handle_rectification_request,
            PrivacyRequestType.ERASURE: self._handle_erasure_request,
            PrivacyRequestType.PORTABILITY: self._handle_portability_request,
            PrivacyRequestType.RESTRICTION: self._handle_restriction_request,
            PrivacyRequestType.CONSENT_WITHDRAWAL: self._handle_consent_withdrawal,
        }

        handler = handlers.get(request.request_type)
        if not handler:
            return PrivacyOperationResult(
                request_id=request.request_id,
                operation_type=request.request_type,
                success=False,
                subject_id=request.subject_id,
                errors=[f"Unknown request type: {request.request_type}"],
            )

        result = await handler(request)

        # Log audit trail
        await self._create_audit_log(request, result)

        return result

    async def _handle_access_request(self, request: PrivacyRequest) -> PrivacyOperationResult:
        """
        Handle Subject Access Request (SAR) - GDPR Art. 15.

        Returns all personal data held about the subject.
        """
        self.logger.info(f"Processing SAR for {request.subject_id}")

        # Find all data locations for subject
        data_records = await self._find_subject_data(request.subject_id, request.data_categories)

        if not data_records:
            return PrivacyOperationResult(
                request_id=request.request_id,
                operation_type=request.request_type,
                success=True,
                subject_id=request.subject_id,
                datasets_processed=0,
                records_affected=0,
            )

        # Extract and compile data
        all_data: dict[str, list[dict[str, Any]]] = {}
        total_records = 0

        for record in data_records:
            data = await self._extract_subject_data(request.subject_id, record)
            if data:
                all_data[record.dataset_name] = data
                total_records += len(data)

        # Export to S3
        export_key = f"privacy-exports/{request.subject_id}/{request.request_id}/sar_export.json"
        export_data = {
            "subject_id": request.subject_id,
            "request_id": request.request_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "data_categories": [c.value for c in request.data_categories] if request.data_categories else "all",
            "datasets": all_data,
        }

        self.s3_client.put_object(
            Bucket=self.audit_bucket,
            Key=export_key,
            Body=json.dumps(export_data, default=str, indent=2),
            ServerSideEncryption="aws:kms",
        )

        return PrivacyOperationResult(
            request_id=request.request_id,
            operation_type=request.request_type,
            success=True,
            subject_id=request.subject_id,
            datasets_processed=len(data_records),
            records_affected=total_records,
            export_location=f"s3://{self.audit_bucket}/{export_key}",
            export_format="json",
        )

    async def _handle_erasure_request(self, request: PrivacyRequest) -> PrivacyOperationResult:
        """
        Handle Right to Erasure - GDPR Art. 17.

        Deletes or anonymizes personal data where legally permitted.
        """
        self.logger.info(f"Processing erasure request for {request.subject_id}")

        # Find all data locations
        data_records = await self._find_subject_data(request.subject_id, request.data_categories)

        records_deleted = 0
        records_anonymized = 0
        records_retained = 0
        retention_reasons = []
        errors = []

        for record in data_records:
            # Check if deletion is blocked
            if not record.can_be_deleted:
                records_retained += record.record_count
                if record.deletion_blocker:
                    retention_reasons.append(
                        f"{record.dataset_name}: {record.deletion_blocker.value}"
                    )
                continue

            # Check regulatory retention
            if self._is_within_retention_period(record):
                # Anonymize instead of delete
                try:
                    anonymized = await self._anonymize_subject_data(request.subject_id, record)
                    records_anonymized += anonymized
                except Exception as e:
                    errors.append(f"Anonymization failed for {record.dataset_name}: {e}")
            else:
                # Full deletion
                try:
                    deleted = await self._delete_subject_data(request.subject_id, record)
                    records_deleted += deleted
                except Exception as e:
                    errors.append(f"Deletion failed for {record.dataset_name}: {e}")

        return PrivacyOperationResult(
            request_id=request.request_id,
            operation_type=request.request_type,
            success=len(errors) == 0,
            subject_id=request.subject_id,
            datasets_processed=len(data_records),
            records_affected=records_deleted + records_anonymized,
            records_deleted=records_deleted,
            records_anonymized=records_anonymized,
            records_retained=records_retained,
            retention_reasons=retention_reasons,
            errors=errors,
        )

    async def _handle_portability_request(self, request: PrivacyRequest) -> PrivacyOperationResult:
        """
        Handle Right to Portability - GDPR Art. 20.

        Exports data in machine-readable format.
        """
        self.logger.info(f"Processing portability request for {request.subject_id}")

        # Similar to access but in structured format
        data_records = await self._find_subject_data(
            request.subject_id,
            [DataCategory.IDENTITY, DataCategory.CONTACT, DataCategory.FINANCIAL],
        )

        all_data: dict[str, list[dict[str, Any]]] = {}
        total_records = 0

        for record in data_records:
            data = await self._extract_subject_data(request.subject_id, record)
            if data:
                all_data[record.dataset_name] = data
                total_records += len(data)

        # Export in structured JSON format
        export_key = f"privacy-exports/{request.subject_id}/{request.request_id}/portability_export.json"

        export_data = {
            "@context": "https://schema.org",
            "@type": "DataDownload",
            "identifier": request.request_id,
            "dateCreated": datetime.now(timezone.utc).isoformat(),
            "contentUrl": f"s3://{self.audit_bucket}/{export_key}",
            "encodingFormat": "application/json",
            "about": {
                "@type": "Person",
                "identifier": request.subject_id,
            },
            "data": all_data,
        }

        export_json = json.dumps(export_data, default=str, indent=2)

        self.s3_client.put_object(
            Bucket=self.audit_bucket,
            Key=export_key,
            Body=export_json,
            ServerSideEncryption="aws:kms",
            ContentType="application/json",
        )

        return PrivacyOperationResult(
            request_id=request.request_id,
            operation_type=request.request_type,
            success=True,
            subject_id=request.subject_id,
            datasets_processed=len(data_records),
            records_affected=total_records,
            export_location=f"s3://{self.audit_bucket}/{export_key}",
            export_format="json",
            export_size_bytes=len(export_json),
        )

    async def _handle_rectification_request(self, request: PrivacyRequest) -> PrivacyOperationResult:
        """Handle Right to Rectification - GDPR Art. 16."""
        # This would update records across all datasets
        # Implementation depends on specific data structure
        return PrivacyOperationResult(
            request_id=request.request_id,
            operation_type=request.request_type,
            success=True,
            subject_id=request.subject_id,
        )

    async def _handle_restriction_request(self, request: PrivacyRequest) -> PrivacyOperationResult:
        """Handle Right to Restriction - GDPR Art. 18."""
        # Mark data as restricted from processing
        # Implementation depends on access control system
        return PrivacyOperationResult(
            request_id=request.request_id,
            operation_type=request.request_type,
            success=True,
            subject_id=request.subject_id,
        )

    async def _handle_consent_withdrawal(self, request: PrivacyRequest) -> PrivacyOperationResult:
        """Handle consent withdrawal."""
        # Update consent records and stop related processing
        return PrivacyOperationResult(
            request_id=request.request_id,
            operation_type=request.request_type,
            success=True,
            subject_id=request.subject_id,
        )

    async def _find_subject_data(
        self,
        subject_id: str,
        categories: Optional[list[DataCategory]] = None,
    ) -> list[DataSubjectRecord]:
        """
        Find all data locations for a subject.

        In production, this would query the data catalog.
        """
        # Example data locations (would be from catalog in production)
        locations = [
            DataSubjectRecord(
                subject_id=subject_id,
                data_category=DataCategory.IDENTITY,
                dataset_name="player_profiles",
                location=f"s3://{self.data_lake_bucket}/silver/player_profiles/",
                record_count=1,
                first_collected=datetime.now(timezone.utc) - timedelta(days=365),
                last_updated=datetime.now(timezone.utc),
                legal_basis="contract",
                retention_period_days=self.retention_periods[DataCategory.IDENTITY],
                can_be_deleted=False,
                deletion_blocker=ErasureBlocker.REGULATORY_RETENTION,
            ),
            DataSubjectRecord(
                subject_id=subject_id,
                data_category=DataCategory.FINANCIAL,
                dataset_name="transactions",
                location=f"s3://{self.data_lake_bucket}/silver/transactions/",
                record_count=150,
                first_collected=datetime.now(timezone.utc) - timedelta(days=365),
                last_updated=datetime.now(timezone.utc),
                legal_basis="legal_obligation",
                retention_period_days=self.retention_periods[DataCategory.FINANCIAL],
                can_be_deleted=False,
                deletion_blocker=ErasureBlocker.REGULATORY_RETENTION,
            ),
            DataSubjectRecord(
                subject_id=subject_id,
                data_category=DataCategory.BEHAVIORAL,
                dataset_name="activity_events",
                location=f"s3://{self.data_lake_bucket}/silver/events/",
                record_count=5000,
                first_collected=datetime.now(timezone.utc) - timedelta(days=365),
                last_updated=datetime.now(timezone.utc),
                legal_basis="consent",
                retention_period_days=self.retention_periods[DataCategory.BEHAVIORAL],
                can_be_deleted=True,
            ),
        ]

        if categories:
            locations = [loc for loc in locations if loc.data_category in categories]

        return locations

    async def _extract_subject_data(
        self,
        subject_id: str,
        record: DataSubjectRecord,
    ) -> list[dict[str, Any]]:
        """Extract subject data from a dataset location."""
        # In production, query Athena or read from S3
        # This is a placeholder
        return [
            {"subject_id": subject_id, "dataset": record.dataset_name, "sample": "data"}
        ]

    async def _delete_subject_data(
        self,
        subject_id: str,
        record: DataSubjectRecord,
    ) -> int:
        """Delete subject data from a dataset."""
        # In production, would use Athena DELETE or S3 selective delete
        self.logger.info(f"Deleting {record.record_count} records from {record.dataset_name}")
        return record.record_count

    async def _anonymize_subject_data(
        self,
        subject_id: str,
        record: DataSubjectRecord,
    ) -> int:
        """Anonymize subject data instead of deleting."""
        # In production, would run anonymization ETL job
        self.logger.info(f"Anonymizing {record.record_count} records in {record.dataset_name}")
        return record.record_count

    def _is_within_retention_period(self, record: DataSubjectRecord) -> bool:
        """Check if data is within regulatory retention period."""
        age_days = (datetime.now(timezone.utc) - record.first_collected).days
        return age_days < record.retention_period_days

    async def _create_audit_log(
        self,
        request: PrivacyRequest,
        result: PrivacyOperationResult,
    ) -> str:
        """Create audit log entry for privacy operation."""
        audit_id = str(uuid4())

        audit_entry = {
            "audit_id": audit_id,
            "request_id": request.request_id,
            "subject_id": request.subject_id,
            "request_type": request.request_type.value,
            "requested_at": request.requested_at.isoformat(),
            "requested_by": request.requested_by,
            "completed_at": result.completed_at.isoformat(),
            "success": result.success,
            "datasets_processed": result.datasets_processed,
            "records_affected": result.records_affected,
            "records_deleted": result.records_deleted,
            "records_anonymized": result.records_anonymized,
            "records_retained": result.records_retained,
            "retention_reasons": result.retention_reasons,
            "errors": result.errors,
        }

        audit_key = f"privacy-audit/{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/{audit_id}.json"

        self.s3_client.put_object(
            Bucket=self.audit_bucket,
            Key=audit_key,
            Body=json.dumps(audit_entry, indent=2),
            ServerSideEncryption="aws:kms",
        )

        self.logger.info(f"Created audit log: {audit_id}")
        return audit_id

    async def get_subject_data_inventory(self, subject_id: str) -> dict[str, Any]:
        """
        Get inventory of all data held about a subject.

        Useful for transparency and pre-SAR analysis.
        """
        records = await self._find_subject_data(subject_id)

        by_category: dict[str, dict[str, int]] = {}
        deletion_blockers: list[dict[str, Any]] = []
        datasets: list[dict[str, Any]] = []

        for record in records:
            category = record.data_category.value

            if category not in by_category:
                by_category[category] = {"count": 0, "records": 0}

            by_category[category]["count"] += 1
            by_category[category]["records"] += record.record_count

            if record.deletion_blocker:
                deletion_blockers.append({
                    "dataset": record.dataset_name,
                    "reason": record.deletion_blocker.value,
                })

            datasets.append({
                "name": record.dataset_name,
                "category": category,
                "records": record.record_count,
                "legal_basis": record.legal_basis,
                "retention_days": record.retention_period_days,
                "can_be_deleted": record.can_be_deleted,
            })

        return {
            "subject_id": subject_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_datasets": len(records),
            "total_records": sum(r.record_count for r in records),
            "by_category": by_category,
            "deletion_blockers": deletion_blockers,
            "datasets": datasets,
        }


# =============================================================================
# CONSENT MANAGER
# =============================================================================


class ConsentManager:
    """
    Manages consent records for data subjects.

    Tracks consent grants, withdrawals, and provides
    consent verification for processing operations.
    """

    # Consent purposes for iGaming
    PURPOSES = [
        "marketing_email",
        "marketing_sms",
        "marketing_push",
        "profiling",
        "third_party_sharing",
        "analytics",
        "personalization",
        "responsible_gambling_monitoring",
    ]

    def __init__(self, table_name: str = "consent-records", region: str = "us-east-1"):
        self.table_name = table_name
        self.region = region
        self.logger = logging.getLogger(__name__)

        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    async def record_consent(
        self,
        subject_id: str,
        purpose: str,
        granted: bool,
        channel: str = "web",
        version: str = "1.0",
        ip_address: str = "",
    ) -> ConsentRecord:
        """
        Record a consent decision.

        Args:
            subject_id: Player ID
            purpose: Consent purpose
            granted: Whether consent was given
            channel: Where consent was collected
            version: Privacy policy version

        Returns:
            ConsentRecord
        """
        consent_id = str(uuid4())
        now = datetime.now(timezone.utc)

        record = ConsentRecord(
            consent_id=consent_id,
            subject_id=subject_id,
            purpose=purpose,
            granted=granted,
            granted_at=now if granted else None,
            channel=channel,
            version=version,
            ip_address=ip_address,
        )

        self.table.put_item(
            Item={
                "PK": f"SUBJECT#{subject_id}",
                "SK": f"CONSENT#{purpose}",
                "consent_id": consent_id,
                "purpose": purpose,
                "granted": granted,
                "granted_at": now.isoformat() if granted else None,
                "channel": channel,
                "version": version,
                "ip_address": ip_address,
                "updated_at": now.isoformat(),
            }
        )

        self.logger.info(f"Recorded consent for {subject_id}: {purpose}={granted}")
        return record

    async def withdraw_consent(self, subject_id: str, purpose: str) -> bool:
        """
        Withdraw previously given consent.

        Args:
            subject_id: Player ID
            purpose: Consent purpose to withdraw

        Returns:
            True if withdrawal was successful
        """
        now = datetime.now(timezone.utc)

        self.table.update_item(
            Key={"PK": f"SUBJECT#{subject_id}", "SK": f"CONSENT#{purpose}"},
            UpdateExpression="SET granted = :false, withdrawn_at = :now",
            ExpressionAttributeValues={
                ":false": False,
                ":now": now.isoformat(),
            },
        )

        self.logger.info(f"Withdrawn consent for {subject_id}: {purpose}")
        return True

    async def check_consent(self, subject_id: str, purpose: str) -> bool:
        """
        Check if subject has given consent for a purpose.

        Args:
            subject_id: Player ID
            purpose: Consent purpose to check

        Returns:
            True if consent is granted and valid
        """
        response = self.table.get_item(
            Key={"PK": f"SUBJECT#{subject_id}", "SK": f"CONSENT#{purpose}"}
        )

        if "Item" not in response:
            return False

        return response["Item"].get("granted", False)

    async def get_all_consents(self, subject_id: str) -> list[dict[str, Any]]:
        """
        Get all consent records for a subject.

        Args:
            subject_id: Player ID

        Returns:
            List of consent records
        """
        response = self.table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"SUBJECT#{subject_id}",
                ":sk": "CONSENT#",
            },
        )

        return response.get("Items", [])


# =============================================================================
# MAIN
# =============================================================================


async def main() -> None:
    """Example usage of Privacy Operations."""
    logging.basicConfig(level=logging.INFO)

    print("\n" + "=" * 70)
    print("GDPR PRIVACY OPERATIONS EXAMPLE")
    print("=" * 70)

    # Create example request
    request = PrivacyRequest(
        request_id=str(uuid4()),
        subject_id="PLAYER_12345",
        request_type=PrivacyRequestType.ACCESS,
        requested_by="player@example.com",
        verification_method="email_verification",
        data_categories=[
            DataCategory.IDENTITY,
            DataCategory.FINANCIAL,
            DataCategory.BEHAVIORAL,
        ],
    )

    print(f"\nRequest Type: {request.request_type.value}")
    print(f"Subject ID: {request.subject_id}")
    print(f"Categories: {[c.value for c in request.data_categories]}")
    print(f"Deadline: {request.deadline or 'Not set'}")

    print("\n" + "-" * 70)
    print("iGAMING RETENTION REQUIREMENTS")
    print("-" * 70)

    retention_periods = {
        DataCategory.FINANCIAL: ("7 years", "UK GC LCCP 15.2.1, AML"),
        DataCategory.IDENTITY: ("5 years", "GDPR, AML Directive"),
        DataCategory.GAMING: ("5 years", "UK GC LCCP"),
        DataCategory.BEHAVIORAL: ("2 years", "Internal policy"),
        DataCategory.TECHNICAL: ("1 year", "GDPR minimization"),
        DataCategory.MARKETING: ("3 years", "After consent withdrawal"),
    }

    for category, (period, regulation) in retention_periods.items():
        print(f"  {category.value:15} : {period:10} ({regulation})")

    print("\n" + "-" * 70)
    print("ERASURE BLOCKERS")
    print("-" * 70)

    for blocker in ErasureBlocker:
        print(f"  - {blocker.value}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
