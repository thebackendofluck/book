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
Data Lineage Tracking Module for iGaming Data Lake

This module provides comprehensive data lineage capabilities including:
- End-to-end data flow tracking
- Column-level lineage
- Impact analysis (what will be affected by changes)
- Root cause analysis (where did data come from)
- Transformation documentation

Lineage Types:
1. Dataset-level: Which datasets feed into which
2. Column-level: How each column is derived
3. Job-level: Which ETL jobs process data
4. Time-based: Historical lineage snapshots

Architecture:
    Source Systems -> ETL Jobs -> Data Lake -> Analytics
         |              |            |            |
         +-------- Lineage Events -------+--------+
                         |
                   Lineage Store
                         |
                   Lineage API

Usage:
    tracker = LineageTracker(config)
    await tracker.record_transformation(job_info, inputs, outputs)
    lineage = await tracker.get_upstream_lineage(dataset_id)

Dependencies:
    pip install boto3 networkx
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

import boto3  # ty:ignore[unresolved-import]
from botocore.config import Config  # ty:ignore[unresolved-import]

try:
    import networkx as nx  # ty:ignore[unresolved-import]
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================


class LineageNodeType(Enum):
    """Types of nodes in lineage graph."""

    SOURCE_SYSTEM = "source_system"      # External data source
    DATABASE = "database"                 # Database/table
    DATASET = "dataset"                   # S3 dataset
    COLUMN = "column"                     # Individual column
    ETL_JOB = "etl_job"                  # Glue/Spark job
    STREAM = "stream"                     # Kinesis stream
    REPORT = "report"                     # BI report/dashboard


class TransformationType(Enum):
    """Types of data transformations."""

    DIRECT_COPY = "direct_copy"          # No transformation
    AGGREGATION = "aggregation"          # SUM, COUNT, AVG, etc.
    FILTER = "filter"                    # WHERE clause
    JOIN = "join"                        # Table join
    DERIVATION = "derivation"            # Calculated column
    MASKING = "masking"                  # PII masking
    ENCRYPTION = "encryption"            # Data encryption
    TYPE_CAST = "type_cast"              # Data type conversion
    NORMALIZATION = "normalization"      # Data normalization
    DEDUPLICATION = "deduplication"      # Remove duplicates


class LineageEventType(Enum):
    """Types of lineage events."""

    DATASET_CREATED = "dataset_created"
    DATASET_UPDATED = "dataset_updated"
    DATASET_DELETED = "dataset_deleted"
    SCHEMA_CHANGED = "schema_changed"
    JOB_EXECUTED = "job_executed"
    LINEAGE_LINKED = "lineage_linked"


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class LineageNode:
    """Node in the lineage graph."""

    node_id: str
    node_type: LineageNodeType
    name: str
    qualified_name: str  # Full path/identifier
    description: str = ""

    # Metadata
    owner: str = ""
    domain: str = ""
    classification: str = ""

    # Location
    location: str = ""  # S3 path, database connection, etc.
    platform: str = ""  # aws, on_premise, etc.

    # Schema (for datasets)
    columns: list[str] = field(default_factory=list)

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class LineageEdge:
    """Edge connecting two nodes in lineage graph."""

    edge_id: str
    source_id: str
    target_id: str
    transformation_type: TransformationType

    # Transformation details
    transformation_sql: str = ""
    transformation_description: str = ""

    # Column mapping (source_col -> target_col)
    column_mappings: dict[str, str] = field(default_factory=dict)

    # Job information
    job_id: str = ""
    job_name: str = ""

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_execution: Optional[datetime] = None
    execution_count: int = 0


@dataclass
class ColumnLineage:
    """Detailed column-level lineage."""

    target_column: str
    target_dataset: str
    source_columns: list[dict[str, str]]  # [{dataset, column}]
    transformation: TransformationType
    transformation_logic: str = ""
    is_pii: bool = False
    is_derived: bool = False


@dataclass
class LineageEvent:
    """Event in lineage history."""

    event_id: str
    event_type: LineageEventType
    timestamp: datetime
    node_id: str
    job_id: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)
    user: str = ""


@dataclass
class ImpactAnalysis:
    """Result of impact analysis."""

    source_node_id: str
    impacted_datasets: list[str]
    impacted_reports: list[str]
    impacted_jobs: list[str]
    total_downstream_nodes: int
    critical_paths: list[list[str]]  # Paths to critical systems


@dataclass
class RootCauseAnalysis:
    """Result of root cause analysis."""

    target_column: str
    target_dataset: str
    source_systems: list[str]
    transformation_chain: list[dict[str, Any]]
    total_upstream_nodes: int
    data_quality_checkpoints: list[str]


# =============================================================================
# LINEAGE TRACKER
# =============================================================================


class LineageTracker:
    """
    Data lineage tracking and analysis.

    Maintains a graph of data flow through the data lake
    and provides impact/root cause analysis capabilities.
    """

    def __init__(
        self,
        table_name: str = "data-lineage",
        region: str = "us-east-1",
    ):
        self.table_name = table_name
        self.region = region
        self.logger = logging.getLogger(__name__)

        boto_config = Config(retries={"max_attempts": 3, "mode": "adaptive"})

        self.dynamodb = boto3.resource("dynamodb", region_name=region, config=boto_config)
        self.table = self.dynamodb.Table(table_name)

        # In-memory graph for analysis (in production, use Neptune or similar)
        self.graph: Optional[Any] = None
        if HAS_NETWORKX:
            self.graph = nx.DiGraph()

    async def register_node(self, node: LineageNode) -> str:
        """
        Register a new node in the lineage graph.

        Args:
            node: Lineage node to register

        Returns:
            Node ID
        """
        if not node.node_id:
            node.node_id = str(uuid4())

        self.table.put_item(
            Item={
                "PK": f"NODE#{node.node_id}",
                "SK": "METADATA",
                "node_id": node.node_id,
                "node_type": node.node_type.value,
                "name": node.name,
                "qualified_name": node.qualified_name,
                "description": node.description,
                "owner": node.owner,
                "domain": node.domain,
                "location": node.location,
                "columns": node.columns,
                "created_at": node.created_at.isoformat(),
                "updated_at": node.updated_at.isoformat(),
            }
        )

        # Add to graph
        if self.graph is not None:
            self.graph.add_node(
                node.node_id,
                type=node.node_type.value,
                name=node.name,
                domain=node.domain,
            )

        self.logger.info(f"Registered lineage node: {node.name} ({node.node_id})")
        return node.node_id

    async def record_transformation(
        self,
        job_id: str,
        job_name: str,
        source_nodes: list[str],
        target_node: str,
        transformation_type: TransformationType,
        column_mappings: Optional[dict[str, str]] = None,
        transformation_sql: str = "",
    ) -> list[str]:
        """
        Record a data transformation (edge in lineage graph).

        Args:
            job_id: ETL job identifier
            job_name: Job name
            source_nodes: List of source node IDs
            target_node: Target node ID
            transformation_type: Type of transformation
            column_mappings: Source to target column mappings
            transformation_sql: SQL or code for transformation

        Returns:
            List of edge IDs
        """
        edge_ids = []
        now = datetime.now(timezone.utc)

        for source_id in source_nodes:
            edge_id = str(uuid4())

            edge = LineageEdge(
                edge_id=edge_id,
                source_id=source_id,
                target_id=target_node,
                transformation_type=transformation_type,
                transformation_sql=transformation_sql,
                column_mappings=column_mappings or {},
                job_id=job_id,
                job_name=job_name,
                created_at=now,
                last_execution=now,
                execution_count=1,
            )

            # Store edge
            self.table.put_item(
                Item={
                    "PK": f"EDGE#{source_id}",
                    "SK": f"TARGET#{target_node}",
                    "edge_id": edge_id,
                    "source_id": source_id,
                    "target_id": target_node,
                    "transformation_type": transformation_type.value,
                    "transformation_sql": transformation_sql,
                    "column_mappings": column_mappings or {},
                    "job_id": job_id,
                    "job_name": job_name,
                    "created_at": now.isoformat(),
                    "last_execution": now.isoformat(),
                    "execution_count": 1,
                }
            )

            # Store reverse lookup
            self.table.put_item(
                Item={
                    "PK": f"UPSTREAM#{target_node}",
                    "SK": f"SOURCE#{source_id}",
                    "edge_id": edge_id,
                }
            )

            # Add to graph
            if self.graph is not None:
                self.graph.add_edge(
                    source_id,
                    target_node,
                    edge_id=edge_id,
                    transformation=transformation_type.value,
                    job=job_name,
                )

            edge_ids.append(edge_id)

        self.logger.info(
            f"Recorded transformation: {len(source_nodes)} sources -> {target_node}"
        )
        return edge_ids

    async def get_upstream_lineage(
        self,
        node_id: str,
        depth: int = 10,
    ) -> dict[str, Any]:
        """
        Get upstream lineage (all sources that feed into this node).

        Args:
            node_id: Starting node ID
            depth: Maximum depth to traverse

        Returns:
            Upstream lineage tree
        """
        visited = set()
        lineage = {
            "node_id": node_id,
            "upstream": [],
            "depth": 0,
        }

        await self._traverse_upstream(node_id, lineage, visited, 0, depth)

        return lineage

    async def _traverse_upstream(
        self,
        node_id: str,
        lineage: dict[str, Any],
        visited: set[str],
        current_depth: int,
        max_depth: int,
    ) -> None:
        """Recursively traverse upstream lineage."""
        if current_depth >= max_depth or node_id in visited:
            return

        visited.add(node_id)

        # Get upstream edges
        response = self.table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"UPSTREAM#{node_id}"},
        )

        for item in response.get("Items", []):
            source_id = item["SK"].replace("SOURCE#", "")

            upstream_node = {
                "node_id": source_id,
                "edge_id": item.get("edge_id"),
                "depth": current_depth + 1,
                "upstream": [],
            }

            lineage["upstream"].append(upstream_node)

            await self._traverse_upstream(
                source_id, upstream_node, visited, current_depth + 1, max_depth
            )

    async def get_downstream_lineage(
        self,
        node_id: str,
        depth: int = 10,
    ) -> dict[str, Any]:
        """
        Get downstream lineage (all nodes that use this node).

        Args:
            node_id: Starting node ID
            depth: Maximum depth to traverse

        Returns:
            Downstream lineage tree
        """
        visited = set()
        lineage = {
            "node_id": node_id,
            "downstream": [],
            "depth": 0,
        }

        await self._traverse_downstream(node_id, lineage, visited, 0, depth)

        return lineage

    async def _traverse_downstream(
        self,
        node_id: str,
        lineage: dict[str, Any],
        visited: set[str],
        current_depth: int,
        max_depth: int,
    ) -> None:
        """Recursively traverse downstream lineage."""
        if current_depth >= max_depth or node_id in visited:
            return

        visited.add(node_id)

        # Get downstream edges
        response = self.table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"EDGE#{node_id}"},
        )

        for item in response.get("Items", []):
            target_id = item["SK"].replace("TARGET#", "")

            downstream_node = {
                "node_id": target_id,
                "edge_id": item.get("edge_id"),
                "transformation": item.get("transformation_type"),
                "depth": current_depth + 1,
                "downstream": [],
            }

            lineage["downstream"].append(downstream_node)

            await self._traverse_downstream(
                target_id, downstream_node, visited, current_depth + 1, max_depth
            )

    async def analyze_impact(self, node_id: str) -> ImpactAnalysis:
        """
        Analyze impact of changes to a node.

        What would be affected if this dataset changes?
        """
        downstream = await self.get_downstream_lineage(node_id, depth=20)

        impacted_datasets = []
        impacted_reports = []
        impacted_jobs = set()

        def collect_impacts(node: dict[str, Any]) -> None:
            node_id = node.get("node_id", "")
            # In production, look up node type from catalog
            impacted_datasets.append(node_id)

            for child in node.get("downstream", []):
                collect_impacts(child)

        for child in downstream.get("downstream", []):
            collect_impacts(child)

        return ImpactAnalysis(
            source_node_id=node_id,
            impacted_datasets=impacted_datasets,
            impacted_reports=impacted_reports,
            impacted_jobs=list(impacted_jobs),
            total_downstream_nodes=len(impacted_datasets),
            critical_paths=[],  # Would compute critical paths in production
        )

    async def analyze_root_cause(
        self,
        target_dataset: str,
        target_column: str,
    ) -> RootCauseAnalysis:
        """
        Analyze root cause for a column's data.

        Where does this column's data originate from?
        """
        upstream = await self.get_upstream_lineage(target_dataset, depth=20)

        source_systems = []
        transformation_chain = []

        def collect_sources(node: dict[str, Any], chain: list[str]) -> None:
            node_id = node.get("node_id", "")
            current_chain = chain + [node_id]

            if not node.get("upstream"):
                # This is a source system
                source_systems.append(node_id)
                transformation_chain.append({
                    "path": current_chain,
                    "transformations": [],  # Would collect transformations
                })
            else:
                for parent in node.get("upstream", []):
                    collect_sources(parent, current_chain)

        for parent in upstream.get("upstream", []):
            collect_sources(parent, [target_dataset])

        return RootCauseAnalysis(
            target_column=target_column,
            target_dataset=target_dataset,
            source_systems=source_systems,
            transformation_chain=transformation_chain,
            total_upstream_nodes=len(source_systems),
            data_quality_checkpoints=[],
        )

    async def get_column_lineage(
        self,
        dataset_id: str,
        column_name: str,
    ) -> list[ColumnLineage]:
        """
        Get detailed column-level lineage.

        Shows exactly how a column is derived from source columns.
        """
        # In production, would query stored column mappings
        # This is a placeholder
        return [
            ColumnLineage(
                target_column=column_name,
                target_dataset=dataset_id,
                source_columns=[{"dataset": "source_dataset", "column": "source_column"}],
                transformation=TransformationType.DIRECT_COPY,
                transformation_logic="",
            )
        ]

    async def record_event(self, event: LineageEvent) -> str:
        """Record a lineage event for audit trail."""
        if not event.event_id:
            event.event_id = str(uuid4())

        self.table.put_item(
            Item={
                "PK": f"EVENT#{event.node_id}",
                "SK": f"TIME#{event.timestamp.isoformat()}",
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "timestamp": event.timestamp.isoformat(),
                "node_id": event.node_id,
                "job_id": event.job_id,
                "details": event.details,
                "user": event.user,
            }
        )

        return event.event_id

    def visualize_lineage(self, node_id: str) -> str:
        """
        Generate ASCII visualization of lineage.

        Returns simple text representation.
        """
        if self.graph is None or not HAS_NETWORKX:
            return "NetworkX not available for visualization"

        # Get subgraph around node
        upstream = list(nx.ancestors(self.graph, node_id)) if node_id in self.graph else []
        downstream = list(nx.descendants(self.graph, node_id)) if node_id in self.graph else []

        lines = []
        lines.append("=" * 60)
        lines.append(f"LINEAGE FOR: {node_id}")
        lines.append("=" * 60)

        lines.append("\nUPSTREAM SOURCES:")
        for source in upstream[:10]:  # Limit to 10
            lines.append(f"  <- {source}")

        lines.append(f"\n  [{node_id}]")

        lines.append("\nDOWNSTREAM TARGETS:")
        for target in downstream[:10]:
            lines.append(f"  -> {target}")

        lines.append("=" * 60)

        return "\n".join(lines)


# =============================================================================
# MAIN
# =============================================================================


async def main() -> None:
    """Example usage of LineageTracker."""
    logging.basicConfig(level=logging.INFO)

    print("\n" + "=" * 70)
    print("DATA LINEAGE TRACKING EXAMPLE")
    print("=" * 70)

    # Example lineage graph for iGaming data lake
    example_lineage = """
    Source Systems:
    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
    │  On-Premise  │    │   Payment    │    │    Game      │
    │   Database   │    │   Gateway    │    │   Servers    │
    └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
           │                   │                   │
           v                   v                   v
    ┌────────────────────────────────────────────────────────┐
    │                    Kinesis Streams                      │
    └────────────────────────────────────────────────────────┘
                               │
                               v
    ┌────────────────────────────────────────────────────────┐
    │                    S3 Bronze Layer                      │
    │   (Raw: transactions/, events/, player_updates/)        │
    └────────────────────────────────────────────────────────┘
                               │
                         Glue ETL Job
                               │
                               v
    ┌────────────────────────────────────────────────────────┐
    │                    S3 Silver Layer                      │
    │   (Cleaned: transactions_clean/, events_clean/)         │
    └────────────────────────────────────────────────────────┘
                               │
                         Glue ETL Job
                               │
                               v
    ┌────────────────────────────────────────────────────────┐
    │                     S3 Gold Layer                       │
    │   (Aggregated: player_summary/, game_metrics/)          │
    └────────────────────────────────────────────────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           │                   │                   │
           v                   v                   v
    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
    │    Athena    │    │   Redshift   │    │  QuickSight  │
    │   (Ad-hoc)   │    │  (Analytics) │    │    (BI)      │
    └──────────────┘    └──────────────┘    └──────────────┘
    """

    print(example_lineage)

    print("\n" + "-" * 70)
    print("COLUMN LINEAGE EXAMPLE")
    print("-" * 70)

    column_lineage = """
    Target: gold.player_summary.total_ggr

    Source Chain:
    1. on_premise.bets.amount (DECIMAL)
       └─> bronze.transactions.bet_amount (DIRECT_COPY)
           └─> silver.transactions_clean.bet_amount (TYPE_CAST, VALIDATION)
               └─> gold.player_summary.total_bets (AGGREGATION: SUM)

    2. on_premise.wins.amount (DECIMAL)
       └─> bronze.transactions.win_amount (DIRECT_COPY)
           └─> silver.transactions_clean.win_amount (TYPE_CAST, VALIDATION)
               └─> gold.player_summary.total_wins (AGGREGATION: SUM)

    3. DERIVATION: total_ggr = total_bets - total_wins
    """

    print(column_lineage)

    print("\n" + "-" * 70)
    print("IMPACT ANALYSIS EXAMPLE")
    print("-" * 70)

    impact = """
    If bronze.transactions schema changes:

    IMPACTED DATASETS:
    ├─ silver.transactions_clean (DIRECT)
    │  ├─ gold.player_summary (DERIVED)
    │  │  ├─ athena.player_reports (CONSUMED)
    │  │  └─ quicksight.executive_dashboard (CONSUMED)
    │  ├─ gold.revenue_daily (DERIVED)
    │  │  └─ quicksight.revenue_dashboard (CONSUMED)
    │  └─ gold.risk_scoring (DERIVED)
    │     └─ ml.fraud_detection_model (CONSUMED)
    └─ silver.transaction_audit (DIRECT)
       └─ compliance.regulatory_reports (CONSUMED)

    TOTAL IMPACTED: 9 downstream nodes
    CRITICAL: Yes (regulatory reports affected)
    """

    print(impact)

    print("=" * 70)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
