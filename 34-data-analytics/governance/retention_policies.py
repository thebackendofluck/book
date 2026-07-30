# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Data Retention and Lifecycle Management for iGaming Data Lake

This module provides data governance capabilities including:
- Retention policy management
- Data lifecycle automation
- Compliance reporting
- PII handling and anonymization
- Audit logging

Regulatory Requirements (iGaming):
- Transaction records: 7 years (UK, Malta, Gibraltar)
- Player verification docs: 5 years after account closure
- Game logs: 5 years minimum
- Financial records: 7 years
- Marketing consent: Duration of consent + 2 years

Usage:
    manager = RetentionManager(config)
    await manager.apply_retention_policies()
    report = await manager.generate_compliance_report()

Dependencies:
    pip install boto3 pandas pyarrow
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional

import boto3  # ty:ignore[unresolved-import]
from botocore.config import Config  # ty:ignore[unresolved-import]


# =============================================================================
# CONFIGURATION
# =============================================================================


class DataClassification(Enum):
    """Data classification levels."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"  # PII, financial


class RetentionTier(Enum):
    """Storage tiers for data lifecycle."""

    HOT = "hot"          # S3 Standard - frequent access
    WARM = "warm"        # S3 Standard-IA - infrequent access
    COLD = "cold"        # S3 Glacier Instant Retrieval
    ARCHIVE = "archive"  # S3 Glacier Deep Archive
    DELETE = "delete"    # Mark for deletion


@dataclass
class RetentionPolicy:
    """Retention policy definition."""

    name: str
    description: str
    classification: DataClassification
    retention_days: int
    regulatory_reference: str

    # Lifecycle transitions (days)
    hot_days: int = 30
    warm_days: int = 90
    cold_days: int = 365
    archive_days: int = 730

    # Compliance flags
    requires_audit_log: bool = True
    requires_encryption: bool = True
    allows_anonymization: bool = False
    deletion_requires_approval: bool = False


@dataclass
class DataAsset:
    """Data asset metadata."""

    path: str
    classification: DataClassification
    policy_name: str
    created_at: datetime
    last_accessed: Optional[datetime] = None
    size_bytes: int = 0
    record_count: int = 0
    contains_pii: bool = False
    jurisdiction: str = "GLOBAL"


@dataclass
class ComplianceReport:
    """Compliance report for auditing."""

    report_date: datetime
    policies_applied: int
    assets_compliant: int
    assets_non_compliant: int
    data_deleted_gb: float
    data_archived_gb: float
    pii_records_anonymized: int
    issues: list[dict[str, Any]] = field(default_factory=list)


# =============================================================================
# RETENTION POLICIES
# =============================================================================

# iGaming-specific retention policies
IGAMING_RETENTION_POLICIES = {
    "transactions": RetentionPolicy(
        name="transactions",
        description="Financial transaction records",
        classification=DataClassification.RESTRICTED,
        retention_days=2555,  # 7 years
        regulatory_reference="UK Gambling Commission LCCP 15.2.1, MGA Requirements",
        hot_days=90,
        warm_days=365,
        cold_days=1095,  # 3 years
        archive_days=1825,  # 5 years
        requires_audit_log=True,
        requires_encryption=True,
        deletion_requires_approval=True,
    ),
    "player_profiles": RetentionPolicy(
        name="player_profiles",
        description="Player personal information and KYC documents",
        classification=DataClassification.RESTRICTED,
        retention_days=1825,  # 5 years after account closure
        regulatory_reference="GDPR Art. 17, AML Directive 5",
        hot_days=365,
        warm_days=730,
        cold_days=1095,
        archive_days=1460,
        requires_audit_log=True,
        requires_encryption=True,
        allows_anonymization=True,
        deletion_requires_approval=True,
    ),
    "game_logs": RetentionPolicy(
        name="game_logs",
        description="Game round and betting history",
        classification=DataClassification.CONFIDENTIAL,
        retention_days=1825,  # 5 years
        regulatory_reference="UK Gambling Commission LCCP 15.2.1",
        hot_days=30,
        warm_days=90,
        cold_days=365,
        archive_days=730,
        requires_audit_log=True,
        requires_encryption=True,
    ),
    "events": RetentionPolicy(
        name="events",
        description="Player activity and behavioral events",
        classification=DataClassification.INTERNAL,
        retention_days=730,  # 2 years
        regulatory_reference="Internal analytics policy",
        hot_days=30,
        warm_days=90,
        cold_days=365,
        archive_days=545,
        requires_audit_log=False,
        allows_anonymization=True,
    ),
    "marketing_consent": RetentionPolicy(
        name="marketing_consent",
        description="Marketing consent records",
        classification=DataClassification.CONFIDENTIAL,
        retention_days=1095,  # Consent duration + 3 years
        regulatory_reference="GDPR Art. 7, PECR",
        hot_days=365,
        warm_days=730,
        cold_days=1095,
        archive_days=1095,
        requires_audit_log=True,
        requires_encryption=True,
        deletion_requires_approval=True,
    ),
    "audit_logs": RetentionPolicy(
        name="audit_logs",
        description="System and access audit logs",
        classification=DataClassification.RESTRICTED,
        retention_days=2555,  # 7 years
        regulatory_reference="SOX, PCI-DSS 10.7",
        hot_days=90,
        warm_days=365,
        cold_days=730,
        archive_days=1825,
        requires_audit_log=True,
        requires_encryption=True,
        deletion_requires_approval=True,
    ),
}


# =============================================================================
# RETENTION MANAGER
# =============================================================================


class RetentionManager:
    """
    Data retention and lifecycle manager.

    Manages data lifecycle based on retention policies,
    ensuring regulatory compliance and cost optimization.
    """

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        dry_run: bool = False,
    ):
        self.bucket = bucket
        self.region = region
        self.dry_run = dry_run
        self.logger = logging.getLogger(__name__)

        boto_config = Config(
            retries={"max_attempts": 3, "mode": "adaptive"},
        )

        self.s3_client = boto3.client("s3", region_name=region, config=boto_config)
        self.glue_client = boto3.client("glue", region_name=region, config=boto_config)

        self.policies = IGAMING_RETENTION_POLICIES

    def get_policy(self, policy_name: str) -> Optional[RetentionPolicy]:
        """Get retention policy by name."""
        return self.policies.get(policy_name)

    def determine_tier(self, policy: RetentionPolicy, age_days: int) -> RetentionTier:
        """
        Determine storage tier based on data age and policy.

        Args:
            policy: Retention policy
            age_days: Age of data in days

        Returns:
            Appropriate storage tier
        """
        if age_days > policy.retention_days:
            return RetentionTier.DELETE
        elif age_days > policy.archive_days:
            return RetentionTier.ARCHIVE
        elif age_days > policy.cold_days:
            return RetentionTier.COLD
        elif age_days > policy.warm_days:
            return RetentionTier.WARM
        else:
            return RetentionTier.HOT

    def _tier_to_storage_class(self, tier: RetentionTier) -> Optional[str]:
        """Convert tier to S3 storage class."""
        mapping = {
            RetentionTier.HOT: "STANDARD",
            RetentionTier.WARM: "STANDARD_IA",
            RetentionTier.COLD: "GLACIER_IR",  # Glacier Instant Retrieval
            RetentionTier.ARCHIVE: "DEEP_ARCHIVE",
            RetentionTier.DELETE: None,
        }
        return mapping.get(tier)

    async def analyze_data_assets(self, prefix: str = "") -> list[DataAsset]:
        """
        Analyze data assets in the bucket.

        Args:
            prefix: S3 prefix to analyze

        Returns:
            List of DataAsset objects
        """
        assets = []
        paginator = self.s3_client.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]

                # Determine policy from path
                policy_name = self._infer_policy_from_path(key)
                policy = self.get_policy(policy_name)

                if not policy:
                    continue

                asset = DataAsset(
                    path=f"s3://{self.bucket}/{key}",
                    classification=policy.classification,
                    policy_name=policy_name,
                    created_at=obj["LastModified"],
                    size_bytes=obj["Size"],
                    contains_pii=policy.classification == DataClassification.RESTRICTED,
                )
                assets.append(asset)

        self.logger.info(f"Found {len(assets)} data assets")
        return assets

    def _infer_policy_from_path(self, path: str) -> str:
        """Infer retention policy from S3 path."""
        path_lower = path.lower()

        if "transaction" in path_lower:
            return "transactions"
        elif "player" in path_lower or "profile" in path_lower:
            return "player_profiles"
        elif "game" in path_lower or "round" in path_lower:
            return "game_logs"
        elif "event" in path_lower:
            return "events"
        elif "consent" in path_lower or "marketing" in path_lower:
            return "marketing_consent"
        elif "audit" in path_lower or "log" in path_lower:
            return "audit_logs"
        else:
            return "events"  # Default policy

    async def apply_retention_policies(self) -> ComplianceReport:
        """
        Apply retention policies to all data assets.

        Returns:
            ComplianceReport with results
        """
        report = ComplianceReport(
            report_date=datetime.now(timezone.utc),
            policies_applied=0,
            assets_compliant=0,
            assets_non_compliant=0,
            data_deleted_gb=0,
            data_archived_gb=0,
            pii_records_anonymized=0,
        )

        assets = await self.analyze_data_assets()
        now = datetime.now(timezone.utc)

        for asset in assets:
            policy = self.get_policy(asset.policy_name)
            if not policy:
                continue

            age_days = (now - asset.created_at.replace(tzinfo=timezone.utc)).days
            target_tier = self.determine_tier(policy, age_days)

            # Get current storage class
            current_class = await self._get_storage_class(asset.path)
            target_class = self._tier_to_storage_class(target_tier)

            if target_tier == RetentionTier.DELETE:
                # Handle deletion
                if policy.deletion_requires_approval:
                    report.issues.append({
                        "type": "deletion_pending_approval",
                        "asset": asset.path,
                        "policy": policy.name,
                        "age_days": age_days,
                    })
                    report.assets_non_compliant += 1
                else:
                    await self._delete_asset(asset, policy)
                    report.data_deleted_gb += asset.size_bytes / (1024 ** 3)
                    report.policies_applied += 1
                    report.assets_compliant += 1

            elif target_class and target_class != current_class:
                # Transition to new storage class
                await self._transition_asset(asset, target_class)

                if target_tier in [RetentionTier.COLD, RetentionTier.ARCHIVE]:
                    report.data_archived_gb += asset.size_bytes / (1024 ** 3)

                report.policies_applied += 1
                report.assets_compliant += 1

            else:
                report.assets_compliant += 1

        return report

    async def _get_storage_class(self, s3_path: str) -> str:
        """Get current storage class for an object."""
        # Parse s3://bucket/key
        parts = s3_path.replace("s3://", "").split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""

        try:
            response = self.s3_client.head_object(Bucket=bucket, Key=key)
            return response.get("StorageClass", "STANDARD")
        except Exception:
            return "STANDARD"

    async def _transition_asset(self, asset: DataAsset, storage_class: str) -> None:
        """Transition asset to new storage class."""
        # Parse s3://bucket/key
        parts = asset.path.replace("s3://", "").split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""

        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would transition {key} to {storage_class}")
            return

        try:
            self.s3_client.copy_object(
                Bucket=bucket,
                Key=key,
                CopySource={"Bucket": bucket, "Key": key},
                StorageClass=storage_class,
                MetadataDirective="COPY",
            )
            self.logger.info(f"Transitioned {key} to {storage_class}")

            # Log audit event
            await self._log_audit_event(
                action="STORAGE_TRANSITION",
                asset=asset.path,
                details={"new_storage_class": storage_class},
            )

        except Exception as e:
            self.logger.error(f"Failed to transition {key}: {e}")

    async def _delete_asset(self, asset: DataAsset, policy: RetentionPolicy) -> None:
        """Delete asset according to policy."""
        # Parse s3://bucket/key
        parts = asset.path.replace("s3://", "").split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""

        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would delete {key}")
            return

        try:
            # Archive to glacier before deletion for compliance
            if policy.requires_audit_log:
                await self._archive_before_delete(asset)

            self.s3_client.delete_object(Bucket=bucket, Key=key)
            self.logger.info(f"Deleted {key}")

            # Log audit event
            await self._log_audit_event(
                action="DATA_DELETION",
                asset=asset.path,
                details={"policy": policy.name, "retention_days": policy.retention_days},
            )

        except Exception as e:
            self.logger.error(f"Failed to delete {key}: {e}")

    async def _archive_before_delete(self, asset: DataAsset) -> None:
        """Archive asset metadata before deletion."""
        # Store deletion record in audit bucket
        audit_record = {
            "asset_path": asset.path,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "classification": asset.classification.value,
            "policy": asset.policy_name,
            "size_bytes": asset.size_bytes,
            "created_at": asset.created_at.isoformat(),
        }

        audit_key = f"audit/deletions/{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/{asset.policy_name}_{datetime.now(timezone.utc).timestamp()}.json"

        if not self.dry_run:
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=audit_key,
                Body=json.dumps(audit_record),
                StorageClass="GLACIER_IR",
            )

    async def _log_audit_event(
        self,
        action: str,
        asset: str,
        details: dict[str, Any],
    ) -> None:
        """Log audit event."""
        audit_event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "asset": asset,
            "details": details,
        }

        self.logger.info(f"AUDIT: {json.dumps(audit_event)}")

    async def generate_compliance_report(self) -> dict[str, Any]:
        """
        Generate comprehensive compliance report.

        Returns:
            Dictionary with compliance status
        """
        assets = await self.analyze_data_assets()
        now = datetime.now(timezone.utc)

        by_classification: dict[str, dict[str, Any]] = {}
        by_policy: dict[str, dict[str, Any]] = {}
        compliance_issues: list[dict[str, Any]] = []

        # Group by classification
        for classification in DataClassification:
            filtered = [a for a in assets if a.classification == classification]
            by_classification[classification.value] = {
                "count": len(filtered),
                "size_gb": sum(a.size_bytes for a in filtered) / (1024 ** 3),
            }

        # Group by policy
        for policy_name, policy in self.policies.items():
            filtered = [a for a in assets if a.policy_name == policy_name]
            if not filtered:
                continue

            oldest = min(a.created_at for a in filtered)
            newest = max(a.created_at for a in filtered)

            # Check for overdue deletions
            overdue = [
                a for a in filtered
                if (now - a.created_at.replace(tzinfo=timezone.utc)).days > policy.retention_days
            ]

            by_policy[policy_name] = {
                "count": len(filtered),
                "size_gb": sum(a.size_bytes for a in filtered) / (1024 ** 3),
                "oldest_data": oldest.isoformat(),
                "newest_data": newest.isoformat(),
                "retention_days": policy.retention_days,
                "overdue_count": len(overdue),
            }

            if overdue:
                compliance_issues.append({
                    "policy": policy_name,
                    "issue": "overdue_retention",
                    "count": len(overdue),
                    "action_required": "Delete or archive",
                })

        return {
            "generated_at": now.isoformat(),
            "bucket": self.bucket,
            "total_assets": len(assets),
            "total_size_gb": sum(a.size_bytes for a in assets) / (1024 ** 3),
            "by_classification": by_classification,
            "by_policy": by_policy,
            "compliance_issues": compliance_issues,
            "retention_summary": [],
        }

    def print_policies(self) -> None:
        """Print all retention policies."""
        print("\n" + "=" * 80)
        print("iGAMING DATA RETENTION POLICIES")
        print("=" * 80)

        for name, policy in self.policies.items():
            print(f"\n{name.upper()}")
            print("-" * 40)
            print(f"  Description: {policy.description}")
            print(f"  Classification: {policy.classification.value}")
            print(f"  Retention: {policy.retention_days} days ({policy.retention_days // 365} years)")
            print(f"  Regulatory: {policy.regulatory_reference}")
            print(f"  Lifecycle:")
            print(f"    - Hot (Standard): 0-{policy.hot_days} days")
            print(f"    - Warm (Standard-IA): {policy.hot_days}-{policy.warm_days} days")
            print(f"    - Cold (Glacier IR): {policy.warm_days}-{policy.cold_days} days")
            print(f"    - Archive (Deep Archive): {policy.cold_days}-{policy.archive_days} days")
            print(f"    - Delete: After {policy.retention_days} days")
            print(f"  Requires Encryption: {policy.requires_encryption}")
            print(f"  Requires Audit Log: {policy.requires_audit_log}")
            print(f"  Deletion Approval: {policy.deletion_requires_approval}")

        print("\n" + "=" * 80)


# =============================================================================
# COST CALCULATOR
# =============================================================================


class StorageCostCalculator:
    """Calculate data lake storage costs."""

    # AWS S3 pricing (us-east-1, as of 2024)
    PRICING = {
        "STANDARD": 0.023,           # per GB/month
        "STANDARD_IA": 0.0125,       # per GB/month
        "GLACIER_IR": 0.004,         # per GB/month
        "DEEP_ARCHIVE": 0.00099,     # per GB/month
        "RETRIEVAL_STANDARD": 0.0,   # per GB
        "RETRIEVAL_IA": 0.01,        # per GB
        "RETRIEVAL_GLACIER": 0.03,   # per GB
        "RETRIEVAL_ARCHIVE": 0.02,   # per GB (bulk)
    }

    def calculate_monthly_cost(
        self,
        data_gb: float,
        distribution: dict[str, int | float],
    ) -> dict[str, float]:
        """
        Calculate monthly storage cost.

        Args:
            data_gb: Total data in GB
            distribution: Percentage in each tier

        Returns:
            Cost breakdown by tier
        """
        costs = {}
        total = 0.0

        for tier, percentage in distribution.items():
            tier_gb = data_gb * (percentage / 100)
            tier_cost = tier_gb * self.PRICING.get(tier, 0)
            costs[tier] = round(tier_cost, 2)
            total += tier_cost

        costs["total"] = round(total, 2)
        return costs

    def estimate_7_year_cost(
        self,
        initial_data_gb: float,
        monthly_growth_rate: float = 0.05,  # 5% monthly growth
    ) -> dict[str, Any]:
        """
        Estimate 7-year storage costs with lifecycle management.

        Args:
            initial_data_gb: Initial data size
            monthly_growth_rate: Expected monthly growth rate

        Returns:
            Cost projection
        """
        months = 7 * 12  # 84 months
        costs_by_year = {}
        total_cost = 0.0
        current_data = initial_data_gb

        for month in range(1, months + 1):
            year = (month - 1) // 12 + 1

            # Determine data distribution based on age
            # More data moves to cold/archive over time
            if month <= 3:
                distribution = {"STANDARD": 80, "STANDARD_IA": 20}
            elif month <= 12:
                distribution = {"STANDARD": 40, "STANDARD_IA": 40, "GLACIER_IR": 20}
            elif month <= 36:
                distribution = {"STANDARD": 20, "STANDARD_IA": 30, "GLACIER_IR": 30, "DEEP_ARCHIVE": 20}
            else:
                distribution = {"STANDARD": 10, "STANDARD_IA": 20, "GLACIER_IR": 30, "DEEP_ARCHIVE": 40}

            month_cost = self.calculate_monthly_cost(current_data, distribution)["total"]  # ty:ignore[invalid-argument-type]
            total_cost += month_cost

            if year not in costs_by_year:
                costs_by_year[year] = 0
            costs_by_year[year] += month_cost

            current_data *= (1 + monthly_growth_rate)

        return {
            "initial_data_gb": initial_data_gb,
            "final_data_gb": round(current_data, 2),
            "growth_rate": monthly_growth_rate,
            "total_7_year_cost": round(total_cost, 2),
            "costs_by_year": {k: round(v, 2) for k, v in costs_by_year.items()},
            "average_monthly_cost": round(total_cost / months, 2),
        }


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================


async def main() -> None:
    """Example usage of RetentionManager."""
    logging.basicConfig(level=logging.INFO)

    # Print policies
    manager = RetentionManager(
        bucket="igaming-datalake-bronze",
        region="us-east-1",
        dry_run=True,
    )

    manager.print_policies()

    # Cost estimation
    calculator = StorageCostCalculator()

    print("\n" + "=" * 80)
    print("STORAGE COST ESTIMATION")
    print("=" * 80)

    # Estimate for 10TB initial data
    estimate = calculator.estimate_7_year_cost(
        initial_data_gb=10000,  # 10TB
        monthly_growth_rate=0.03,  # 3% monthly growth
    )

    print(f"\nInitial Data: {estimate['initial_data_gb']:,.0f} GB")
    print(f"Final Data (7 years): {estimate['final_data_gb']:,.0f} GB")
    print(f"Monthly Growth Rate: {estimate['growth_rate'] * 100}%")
    print(f"\nTotal 7-Year Cost: ${estimate['total_7_year_cost']:,.2f}")
    print(f"Average Monthly Cost: ${estimate['average_monthly_cost']:,.2f}")
    print("\nCosts by Year:")
    for year, cost in estimate["costs_by_year"].items():
        print(f"  Year {year}: ${cost:,.2f}")

    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
