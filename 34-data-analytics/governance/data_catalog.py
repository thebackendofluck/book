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
Data Catalog and Metadata Management for iGaming Data Lake

This module provides comprehensive metadata management including:
- Data asset registration and discovery
- Schema versioning and evolution
- Data quality metrics tracking
- Business glossary and data dictionary
- Sensitive data classification
- Usage statistics and popularity

Architecture:
    AWS Glue Catalog <-> Data Catalog API <-> Metadata Store (DynamoDB)
                                          <-> Search Index (OpenSearch)

Usage:
    catalog = DataCatalog(config)
    await catalog.register_dataset(dataset_metadata)
    results = await catalog.search("player transactions")

Dependencies:
    pip install boto3 pydantic opensearch-py
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

import boto3  # ty:ignore[unresolved-import]
from botocore.config import Config  # ty:ignore[unresolved-import]


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================


class DataClassification(Enum):
    """Data sensitivity classification levels."""

    PUBLIC = "public"              # Non-sensitive, can be shared
    INTERNAL = "internal"          # Business internal use
    CONFIDENTIAL = "confidential"  # Restricted access
    RESTRICTED = "restricted"      # PII, financial, highly sensitive
    SECRET = "secret"              # Encryption keys, credentials


class DataQualityDimension(Enum):
    """Data quality measurement dimensions."""

    COMPLETENESS = "completeness"    # % non-null values
    UNIQUENESS = "uniqueness"        # % unique values
    VALIDITY = "validity"            # % values passing validation
    ACCURACY = "accuracy"            # % values matching source
    CONSISTENCY = "consistency"      # % values consistent across systems
    TIMELINESS = "timeliness"        # Data freshness


class PIIType(Enum):
    """Types of Personally Identifiable Information."""

    DIRECT_IDENTIFIER = "direct_identifier"      # Name, email, SSN
    QUASI_IDENTIFIER = "quasi_identifier"        # DOB, zip code, gender
    SENSITIVE_ATTRIBUTE = "sensitive_attribute"  # Health, financial
    NON_SENSITIVE = "non_sensitive"              # Aggregated, anonymous


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class ColumnMetadata:
    """Metadata for a single column."""

    name: str
    data_type: str
    description: str
    is_nullable: bool = True
    is_primary_key: bool = False
    is_partition_key: bool = False

    # Classification
    classification: DataClassification = DataClassification.INTERNAL
    pii_type: PIIType = PIIType.NON_SENSITIVE
    contains_pii: bool = False

    # Validation
    validation_rules: list[str] = field(default_factory=list)
    allowed_values: Optional[list[str]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    regex_pattern: Optional[str] = None

    # Statistics
    null_percentage: float = 0.0
    unique_percentage: float = 0.0
    sample_values: list[str] = field(default_factory=list)

    # Lineage
    source_column: Optional[str] = None
    transformation: Optional[str] = None


@dataclass
class DataQualityMetrics:
    """Data quality metrics for a dataset."""

    dataset_id: str
    measured_at: datetime
    row_count: int
    column_count: int

    # Quality scores (0-100)
    completeness_score: float = 0.0
    uniqueness_score: float = 0.0
    validity_score: float = 0.0
    consistency_score: float = 0.0
    overall_score: float = 0.0

    # Detailed metrics
    null_counts: dict[str, int] = field(default_factory=dict)
    duplicate_count: int = 0
    validation_failures: dict[str, int] = field(default_factory=dict)
    anomaly_count: int = 0

    # Issues
    issues: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DatasetMetadata:
    """Complete metadata for a dataset."""

    # Identification
    dataset_id: str
    name: str
    description: str
    version: str = "1.0.0"

    # Location
    location: str = ""  # S3 path or table reference
    format: str = "parquet"  # parquet, json, csv, delta
    database: str = ""
    schema_name: str = ""

    # Classification
    classification: DataClassification = DataClassification.INTERNAL
    contains_pii: bool = False
    pii_columns: list[str] = field(default_factory=list)

    # Schema
    columns: list[ColumnMetadata] = field(default_factory=list)
    partition_keys: list[str] = field(default_factory=list)

    # Ownership
    owner: str = ""
    steward: str = ""
    team: str = ""
    contact_email: str = ""

    # Business context
    domain: str = ""  # e.g., "player", "game", "transaction"
    subdomain: str = ""
    business_glossary_terms: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # Lifecycle
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    refresh_frequency: str = "daily"  # real-time, hourly, daily, weekly
    retention_policy: str = ""

    # Quality
    quality_metrics: Optional[DataQualityMetrics] = None

    # Lineage
    upstream_datasets: list[str] = field(default_factory=list)
    downstream_datasets: list[str] = field(default_factory=list)
    transformations: list[str] = field(default_factory=list)

    # Usage
    access_count: int = 0
    query_count: int = 0
    last_accessed: Optional[datetime] = None


@dataclass
class BusinessTerm:
    """Business glossary term definition."""

    term_id: str
    name: str
    definition: str
    domain: str
    synonyms: list[str] = field(default_factory=list)
    related_terms: list[str] = field(default_factory=list)
    related_datasets: list[str] = field(default_factory=list)
    owner: str = ""
    approved: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# DATA CATALOG
# =============================================================================


class DataCatalog:
    """
    Enterprise data catalog for metadata management.

    Integrates with AWS Glue Catalog and provides additional
    capabilities for data governance.
    """

    def __init__(
        self,
        table_name: str = "igaming-data-catalog",
        region: str = "us-east-1",
    ):
        self.table_name = table_name
        self.region = region
        self.logger = logging.getLogger(__name__)

        boto_config = Config(retries={"max_attempts": 3, "mode": "adaptive"})

        self.dynamodb = boto3.resource("dynamodb", region_name=region, config=boto_config)
        self.glue_client = boto3.client("glue", region_name=region, config=boto_config)

        self.table = self.dynamodb.Table(table_name)

    async def register_dataset(self, metadata: DatasetMetadata) -> str:
        """
        Register a new dataset in the catalog.

        Args:
            metadata: Dataset metadata

        Returns:
            Dataset ID
        """
        # Generate ID if not provided
        if not metadata.dataset_id:
            metadata.dataset_id = str(uuid4())

        # Calculate classification based on columns
        if any(col.contains_pii for col in metadata.columns):
            metadata.contains_pii = True
            metadata.pii_columns = [col.name for col in metadata.columns if col.contains_pii]

            # Upgrade classification if PII detected
            if metadata.classification.value in ["public", "internal"]:
                metadata.classification = DataClassification.CONFIDENTIAL

        # Store in DynamoDB
        item = {
            "PK": f"DATASET#{metadata.dataset_id}",
            "SK": f"VERSION#{metadata.version}",
            "dataset_id": metadata.dataset_id,
            "name": metadata.name,
            "description": metadata.description,
            "version": metadata.version,
            "location": metadata.location,
            "format": metadata.format,
            "classification": metadata.classification.value,
            "contains_pii": metadata.contains_pii,
            "pii_columns": metadata.pii_columns,
            "columns": [self._column_to_dict(col) for col in metadata.columns],
            "owner": metadata.owner,
            "domain": metadata.domain,
            "tags": metadata.tags,
            "created_at": metadata.created_at.isoformat(),
            "updated_at": metadata.updated_at.isoformat(),
            "upstream_datasets": metadata.upstream_datasets,
            "downstream_datasets": metadata.downstream_datasets,
        }

        self.table.put_item(Item=item)
        self.logger.info(f"Registered dataset: {metadata.name} ({metadata.dataset_id})")

        # Sync with Glue Catalog if database specified
        if metadata.database:
            await self._sync_to_glue(metadata)

        return metadata.dataset_id

    async def get_dataset(self, dataset_id: str, version: Optional[str] = None) -> Optional[DatasetMetadata]:
        """
        Retrieve dataset metadata.

        Args:
            dataset_id: Dataset identifier
            version: Specific version (latest if None)

        Returns:
            Dataset metadata or None
        """
        if version:
            response = self.table.get_item(
                Key={"PK": f"DATASET#{dataset_id}", "SK": f"VERSION#{version}"}
            )
        else:
            # Get latest version
            response = self.table.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": f"DATASET#{dataset_id}"},
                ScanIndexForward=False,
                Limit=1,
            )
            if not response.get("Items"):
                return None
            return self._dict_to_dataset(response["Items"][0])

        if "Item" not in response:
            return None

        return self._dict_to_dataset(response["Item"])

    async def search_datasets(
        self,
        query: str,
        domain: Optional[str] = None,
        classification: Optional[DataClassification] = None,
        contains_pii: Optional[bool] = None,
        tags: Optional[list[str]] = None,
        limit: int = 50,
    ) -> list[DatasetMetadata]:
        """
        Search for datasets.

        Args:
            query: Search query
            domain: Filter by domain
            classification: Filter by classification
            contains_pii: Filter by PII flag
            tags: Filter by tags
            limit: Maximum results

        Returns:
            List of matching datasets
        """
        # Build filter expression
        filter_parts = []
        expr_values: dict[str, Any] = {}

        if domain:
            filter_parts.append("domain = :domain")
            expr_values[":domain"] = domain

        if classification:
            filter_parts.append("classification = :classification")
            expr_values[":classification"] = classification.value

        if contains_pii is not None:
            filter_parts.append("contains_pii = :contains_pii")
            expr_values[":contains_pii"] = contains_pii

        # Scan with filters (in production, use OpenSearch for full-text)
        scan_kwargs: dict[str, Any] = {"Limit": limit}

        if filter_parts:
            scan_kwargs["FilterExpression"] = " AND ".join(filter_parts)
            scan_kwargs["ExpressionAttributeValues"] = expr_values

        response = self.table.scan(**scan_kwargs)

        results = []
        for item in response.get("Items", []):
            # Simple text matching (replace with OpenSearch in production)
            if query.lower() in item.get("name", "").lower() or query.lower() in item.get("description", "").lower():
                results.append(self._dict_to_dataset(item))

        return results

    async def update_quality_metrics(
        self,
        dataset_id: str,
        metrics: DataQualityMetrics,
    ) -> None:
        """
        Update data quality metrics for a dataset.

        Args:
            dataset_id: Dataset identifier
            metrics: Quality metrics
        """
        self.table.update_item(
            Key={"PK": f"DATASET#{dataset_id}", "SK": "QUALITY#latest"},
            UpdateExpression="""
                SET measured_at = :measured_at,
                    row_count = :row_count,
                    completeness_score = :completeness,
                    uniqueness_score = :uniqueness,
                    validity_score = :validity,
                    overall_score = :overall,
                    issues = :issues
            """,
            ExpressionAttributeValues={
                ":measured_at": metrics.measured_at.isoformat(),
                ":row_count": metrics.row_count,
                ":completeness": int(metrics.completeness_score),
                ":uniqueness": int(metrics.uniqueness_score),
                ":validity": int(metrics.validity_score),
                ":overall": int(metrics.overall_score),
                ":issues": metrics.issues,
            },
        )

        self.logger.info(f"Updated quality metrics for {dataset_id}: {metrics.overall_score:.1f}%")

    async def register_business_term(self, term: BusinessTerm) -> str:
        """
        Register a business glossary term.

        Args:
            term: Business term definition

        Returns:
            Term ID
        """
        if not term.term_id:
            term.term_id = str(uuid4())

        item = {
            "PK": f"TERM#{term.term_id}",
            "SK": f"DOMAIN#{term.domain}",
            "term_id": term.term_id,
            "name": term.name,
            "definition": term.definition,
            "domain": term.domain,
            "synonyms": term.synonyms,
            "related_terms": term.related_terms,
            "related_datasets": term.related_datasets,
            "owner": term.owner,
            "approved": term.approved,
            "created_at": term.created_at.isoformat(),
        }

        self.table.put_item(Item=item)
        self.logger.info(f"Registered business term: {term.name}")

        return term.term_id

    async def get_pii_inventory(self) -> dict[str, Any]:
        """
        Get inventory of all PII-containing datasets.

        Returns:
            PII inventory report
        """
        response = self.table.scan(
            FilterExpression="contains_pii = :true",
            ExpressionAttributeValues={":true": True},
        )

        by_classification: dict[str, int] = {}
        by_domain: dict[str, int] = {}
        pii_columns: list[dict[str, Any]] = []
        datasets: list[dict[str, Any]] = []

        for item in response.get("Items", []):
            classification = item.get("classification", "unknown")
            domain = item.get("domain", "unknown")

            by_classification[classification] = by_classification.get(classification, 0) + 1
            by_domain[domain] = by_domain.get(domain, 0) + 1

            for col in item.get("pii_columns", []):
                pii_columns.append({
                    "dataset": item.get("name"),
                    "column": col,
                    "classification": classification,
                })

            datasets.append({
                "dataset_id": item.get("dataset_id"),
                "name": item.get("name"),
                "pii_columns": item.get("pii_columns"),
                "classification": classification,
            })

        return {
            "total_datasets": len(response.get("Items", [])),
            "by_classification": by_classification,
            "by_domain": by_domain,
            "pii_columns": pii_columns,
            "datasets": datasets,
        }

    async def _sync_to_glue(self, metadata: DatasetMetadata) -> None:
        """Sync dataset metadata to AWS Glue Catalog."""
        try:
            # Convert columns to Glue format
            glue_columns = [
                {
                    "Name": col.name,
                    "Type": self._to_glue_type(col.data_type),
                    "Comment": col.description,
                }
                for col in metadata.columns
                if not col.is_partition_key
            ]

            partition_keys = [
                {
                    "Name": col.name,
                    "Type": self._to_glue_type(col.data_type),
                    "Comment": col.description,
                }
                for col in metadata.columns
                if col.is_partition_key
            ]

            # Create or update table
            table_input = {
                "Name": metadata.name,
                "Description": metadata.description,
                "Owner": metadata.owner,
                "StorageDescriptor": {
                    "Columns": glue_columns,
                    "Location": metadata.location,
                    "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                    "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                    "SerdeInfo": {
                        "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
                    },
                },
                "PartitionKeys": partition_keys,
                "Parameters": {
                    "classification": metadata.classification.value,
                    "contains_pii": str(metadata.contains_pii).lower(),
                    "domain": metadata.domain,
                    "catalog_id": metadata.dataset_id,
                },
            }

            try:
                self.glue_client.create_table(
                    DatabaseName=metadata.database,
                    TableInput=table_input,
                )
            except self.glue_client.exceptions.AlreadyExistsException:
                self.glue_client.update_table(
                    DatabaseName=metadata.database,
                    TableInput=table_input,
                )

            self.logger.info(f"Synced to Glue Catalog: {metadata.database}.{metadata.name}")

        except Exception as e:
            self.logger.error(f"Failed to sync to Glue: {e}")

    def _column_to_dict(self, col: ColumnMetadata) -> dict[str, Any]:
        """Convert ColumnMetadata to dictionary."""
        return {
            "name": col.name,
            "data_type": col.data_type,
            "description": col.description,
            "is_nullable": col.is_nullable,
            "is_primary_key": col.is_primary_key,
            "is_partition_key": col.is_partition_key,
            "classification": col.classification.value,
            "pii_type": col.pii_type.value,
            "contains_pii": col.contains_pii,
            "validation_rules": col.validation_rules,
        }

    def _dict_to_dataset(self, item: dict[str, Any]) -> DatasetMetadata:
        """Convert dictionary to DatasetMetadata."""
        columns = []
        for col_dict in item.get("columns", []):
            columns.append(
                ColumnMetadata(
                    name=col_dict["name"],
                    data_type=col_dict["data_type"],
                    description=col_dict.get("description", ""),
                    is_nullable=col_dict.get("is_nullable", True),
                    is_primary_key=col_dict.get("is_primary_key", False),
                    classification=DataClassification(col_dict.get("classification", "internal")),
                    pii_type=PIIType(col_dict.get("pii_type", "non_sensitive")),
                    contains_pii=col_dict.get("contains_pii", False),
                )
            )

        return DatasetMetadata(
            dataset_id=item.get("dataset_id", ""),
            name=item.get("name", ""),
            description=item.get("description", ""),
            version=item.get("version", "1.0.0"),
            location=item.get("location", ""),
            classification=DataClassification(item.get("classification", "internal")),
            contains_pii=item.get("contains_pii", False),
            pii_columns=item.get("pii_columns", []),
            columns=columns,
            owner=item.get("owner", ""),
            domain=item.get("domain", ""),
            tags=item.get("tags", []),
            upstream_datasets=item.get("upstream_datasets", []),
            downstream_datasets=item.get("downstream_datasets", []),
        )

    def _to_glue_type(self, data_type: str) -> str:
        """Convert data type to Glue/Hive type."""
        type_mapping = {
            "string": "string",
            "str": "string",
            "int": "int",
            "integer": "int",
            "bigint": "bigint",
            "float": "float",
            "double": "double",
            "decimal": "decimal(18,8)",
            "boolean": "boolean",
            "bool": "boolean",
            "timestamp": "timestamp",
            "date": "date",
            "binary": "binary",
            "array": "array<string>",
            "map": "map<string,string>",
        }
        return type_mapping.get(data_type.lower(), "string")


# =============================================================================
# SENSITIVE DATA SCANNER
# =============================================================================


class SensitiveDataScanner:
    """
    Scans data to identify and classify sensitive information.

    Uses pattern matching and ML to detect PII in datasets.
    """

    # Common PII patterns
    PII_PATTERNS = {
        "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "phone": r"(\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
        "ssn": r"\d{3}-\d{2}-\d{4}",
        "credit_card": r"\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}",
        "ip_address": r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
        "date_of_birth": r"\d{4}[-/]\d{2}[-/]\d{2}",
    }

    # Column name indicators
    PII_COLUMN_INDICATORS = {
        PIIType.DIRECT_IDENTIFIER: [
            "name", "email", "phone", "ssn", "passport", "license",
            "first_name", "last_name", "full_name", "address",
        ],
        PIIType.QUASI_IDENTIFIER: [
            "dob", "birth_date", "age", "gender", "zip", "postal",
            "city", "state", "country", "nationality",
        ],
        PIIType.SENSITIVE_ATTRIBUTE: [
            "salary", "income", "balance", "credit_score",
            "health", "medical", "diagnosis", "treatment",
        ],
    }

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def classify_column(self, column_name: str, sample_values: list[str]) -> tuple[PIIType, float]:
        """
        Classify a column's PII type.

        Args:
            column_name: Column name
            sample_values: Sample values from column

        Returns:
            Tuple of (PIIType, confidence score)
        """
        import re

        column_lower = column_name.lower()
        confidence = 0.0
        detected_type = PIIType.NON_SENSITIVE

        # Check column name indicators
        for pii_type, indicators in self.PII_COLUMN_INDICATORS.items():
            for indicator in indicators:
                if indicator in column_lower:
                    detected_type = pii_type
                    confidence = 0.7
                    break
            if confidence > 0:
                break

        # Check value patterns
        for value in sample_values[:100]:  # Sample first 100 values
            if not value:
                continue

            for pattern_name, pattern in self.PII_PATTERNS.items():
                if re.match(pattern, str(value)):
                    detected_type = PIIType.DIRECT_IDENTIFIER
                    confidence = max(confidence, 0.9)
                    break

        return detected_type, confidence

    async def scan_dataset(
        self,
        dataset: DatasetMetadata,
        sample_data: list[dict[str, Any]],
    ) -> list[ColumnMetadata]:
        """
        Scan dataset for sensitive data.

        Args:
            dataset: Dataset metadata
            sample_data: Sample rows from dataset

        Returns:
            Updated column metadata with classifications
        """
        updated_columns = []

        for column in dataset.columns:
            # Extract sample values for this column
            sample_values = [
                str(row.get(column.name, ""))
                for row in sample_data
                if row.get(column.name) is not None
            ]

            # Classify
            pii_type, confidence = self.classify_column(column.name, sample_values)

            # Update column metadata
            column.pii_type = pii_type
            column.contains_pii = pii_type != PIIType.NON_SENSITIVE

            if column.contains_pii:
                self.logger.info(
                    f"Detected {pii_type.value} in column {column.name} "
                    f"(confidence: {confidence:.0%})"
                )

                # Upgrade classification
                if pii_type == PIIType.DIRECT_IDENTIFIER:
                    column.classification = DataClassification.RESTRICTED
                elif pii_type == PIIType.SENSITIVE_ATTRIBUTE:
                    column.classification = DataClassification.CONFIDENTIAL

            updated_columns.append(column)

        return updated_columns


# =============================================================================
# MAIN
# =============================================================================


async def main() -> None:
    """Example usage of Data Catalog."""
    logging.basicConfig(level=logging.INFO)

    # Create sample dataset metadata
    columns = [
        ColumnMetadata(
            name="player_id",
            data_type="string",
            description="Unique player identifier",
            is_primary_key=True,
        ),
        ColumnMetadata(
            name="email",
            data_type="string",
            description="Player email address",
            contains_pii=True,
            pii_type=PIIType.DIRECT_IDENTIFIER,
            classification=DataClassification.RESTRICTED,
        ),
        ColumnMetadata(
            name="phone",
            data_type="string",
            description="Player phone number",
            contains_pii=True,
            pii_type=PIIType.DIRECT_IDENTIFIER,
            classification=DataClassification.RESTRICTED,
        ),
        ColumnMetadata(
            name="registration_date",
            data_type="timestamp",
            description="When player registered",
        ),
        ColumnMetadata(
            name="total_deposits",
            data_type="decimal",
            description="Total deposits amount",
            contains_pii=True,
            pii_type=PIIType.SENSITIVE_ATTRIBUTE,
            classification=DataClassification.CONFIDENTIAL,
        ),
    ]

    dataset = DatasetMetadata(
        dataset_id="",
        name="player_profiles",
        description="Player profile information including PII",
        location="s3://igaming-datalake-silver/player_profiles/",
        database="igaming_silver",
        classification=DataClassification.RESTRICTED,
        columns=columns,
        owner="data-platform-team",
        domain="player",
        tags=["pii", "gdpr", "player"],
    )

    print("\n" + "=" * 70)
    print("DATA CATALOG EXAMPLE")
    print("=" * 70)

    print(f"\nDataset: {dataset.name}")
    print(f"Classification: {dataset.classification.value}")
    print(f"Contains PII: {dataset.contains_pii}")
    print(f"\nColumns:")

    for col in dataset.columns:
        pii_flag = " [PII]" if col.contains_pii else ""
        print(f"  - {col.name}: {col.data_type}{pii_flag}")
        if col.contains_pii:
            print(f"      PII Type: {col.pii_type.value}")
            print(f"      Classification: {col.classification.value}")

    print("=" * 70)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
