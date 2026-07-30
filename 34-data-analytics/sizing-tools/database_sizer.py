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
Database Performance Sizing Calculator for iGaming

This module provides database sizing calculations including:
- CPU core requirements
- Memory sizing
- Storage capacity
- IOPS requirements
- Read replica recommendations
- Cost estimation

Features:
- Workload-based calculations
- High availability considerations
- Multi-region support
- AWS RDS cost estimation
- Optimization recommendations

Usage:
    sizer = DatabasePerformanceSizer()

    requirements = DatabaseSizingRequirements(
        concurrent_users=100000,
        peak_daily_transactions=50000000,
        data_retention_days=2555,
        read_write_ratio=10,
        high_availability=True
    )

    recommendation = sizer.calculate_sizing(requirements)
    print(f"CPU Cores: {recommendation.cpu_cores}")
    print(f"Memory: {recommendation.memory_gb} GB")
    print(f"Storage: {recommendation.primary_storage_tb:.1f} TB")

Dependencies:
    No external dependencies required
"""

from dataclasses import dataclass
from typing import Any
import math


@dataclass
class DatabaseSizingRequirements:
    """Requirements for database sizing calculation."""

    concurrent_users: int
    peak_daily_transactions: int
    data_retention_days: int
    read_write_ratio: float  # Reads per write
    avg_query_complexity: float = 5.0  # 1-10 scale
    high_availability: bool = True
    multi_region: bool = False
    avg_transaction_size_kb: float = 1.0
    avg_response_size_kb: float = 10.0


@dataclass
class DatabaseSizingRecommendation:
    """Database sizing recommendation."""

    primary_storage_tb: float
    read_replicas: int
    cpu_cores: int
    memory_gb: int
    iops_required: int
    network_mbps: int
    estimated_cost_monthly: float

    # Breakdown details
    cores_for_writes: int = 0
    cores_for_reads: int = 0
    storage_for_data_tb: float = 0.0
    storage_for_indexes_tb: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "primary_storage_tb": round(self.primary_storage_tb, 2),
            "read_replicas": self.read_replicas,
            "cpu_cores": self.cpu_cores,
            "memory_gb": self.memory_gb,
            "iops_required": self.iops_required,
            "network_mbps": self.network_mbps,
            "estimated_cost_monthly": round(self.estimated_cost_monthly, 2),
            "breakdown": {
                "cores_for_writes": self.cores_for_writes,
                "cores_for_reads": self.cores_for_reads,
                "storage_for_data_tb": round(self.storage_for_data_tb, 2),
                "storage_for_indexes_tb": round(self.storage_for_indexes_tb, 2),
            },
        }


class DatabasePerformanceSizer:
    """
    Database performance sizing calculator for iGaming platforms.

    Calculates optimal database configuration based on workload
    requirements, providing recommendations for CPU, memory,
    storage, and read replicas.
    """

    def __init__(self) -> None:
        # Baseline performance metrics (per core)
        self.baseline_tps_per_core = 2000  # Transactions per second
        self.baseline_reads_per_core = 10000  # Read operations per second
        self.baseline_memory_per_core = 8  # GB RAM per core
        self.baseline_iops_per_core = 5000  # IOPS per core

        # Cost parameters (AWS RDS r6g pricing as baseline)
        self.cost_per_core_hour = 0.50
        self.cost_per_gb_memory_hour = 0.10
        self.cost_per_gb_storage_month = 0.115
        self.cost_per_iops_month = 0.065  # For io1/io2 storage
        self.hours_per_month = 730

    def calculate_sizing(
        self,
        requirements: DatabaseSizingRequirements,
    ) -> DatabaseSizingRecommendation:
        """
        Calculate database sizing based on requirements.

        Args:
            requirements: Workload requirements

        Returns:
            DatabaseSizingRecommendation with optimal configuration
        """
        # Calculate transaction load
        peak_tps = requirements.peak_daily_transactions / 86400  # Per second
        sustained_tps = peak_tps * 0.7  # 70% of peak sustained

        # Calculate read/write operations
        total_ops_per_second = sustained_tps * (1 + requirements.read_write_ratio)
        read_ops_per_second = total_ops_per_second * (
            requirements.read_write_ratio / (1 + requirements.read_write_ratio)
        )

        # Adjust for query complexity
        complexity_multiplier = 1 + (requirements.avg_query_complexity - 1) * 0.5
        adjusted_reads = read_ops_per_second * complexity_multiplier

        # Calculate CPU cores needed
        cores_for_writes = math.ceil(sustained_tps / self.baseline_tps_per_core)
        cores_for_reads = math.ceil(adjusted_reads / self.baseline_reads_per_core)
        total_cores = max(cores_for_writes, cores_for_reads)

        # Add overhead for HA and background tasks
        if requirements.high_availability:
            total_cores = math.ceil(total_cores * 1.5)

        if requirements.multi_region:
            total_cores = math.ceil(total_cores * 1.2)

        # Minimum cores
        total_cores = max(total_cores, 4)

        # Calculate memory requirements
        memory_gb = total_cores * self.baseline_memory_per_core

        # Add buffer for caching and connections
        memory_gb = math.ceil(memory_gb * 1.3)

        # Add memory for concurrent connections
        connection_memory = requirements.concurrent_users * 0.005  # 5MB per connection
        memory_gb += math.ceil(connection_memory)

        # Calculate storage requirements
        daily_data_gb = (
            requirements.peak_daily_transactions
            * requirements.avg_transaction_size_kb
            / (1024 * 1024)
        )
        data_storage_tb = (daily_data_gb * requirements.data_retention_days) / 1024

        # Add indexes and overhead (typically 2-3x)
        index_overhead = 1.5
        total_storage_tb = data_storage_tb * (1 + index_overhead)

        # Calculate IOPS requirements
        iops_required = total_cores * self.baseline_iops_per_core

        # Calculate network requirements
        network_mbps = (
            total_ops_per_second
            * requirements.avg_response_size_kb
            * 8
            / 1024
        )

        # Calculate read replicas
        read_replicas = 0
        if requirements.read_write_ratio > 2:
            additional_read_capacity = adjusted_reads - self.baseline_reads_per_core
            if additional_read_capacity > 0:
                read_replicas = math.ceil(
                    additional_read_capacity / self.baseline_reads_per_core
                )

        # Multi-region requires at least one replica
        if requirements.multi_region and read_replicas < 1:
            read_replicas = 1

        # Cost estimation
        estimated_cost = self._calculate_cost_estimate(
            total_cores,
            memory_gb,
            total_storage_tb,
            read_replicas,
            iops_required,
        )

        return DatabaseSizingRecommendation(
            primary_storage_tb=total_storage_tb,
            read_replicas=read_replicas,
            cpu_cores=total_cores,
            memory_gb=memory_gb,
            iops_required=iops_required,
            network_mbps=int(network_mbps),
            estimated_cost_monthly=estimated_cost,
            cores_for_writes=cores_for_writes,
            cores_for_reads=cores_for_reads,
            storage_for_data_tb=data_storage_tb,
            storage_for_indexes_tb=total_storage_tb - data_storage_tb,
        )

    def _calculate_cost_estimate(
        self,
        cores: int,
        memory_gb: int,
        storage_tb: float,
        replicas: int,
        iops: int,
    ) -> float:
        """Calculate estimated monthly cost (AWS RDS)."""
        # Instance cost
        instance_cost = cores * self.cost_per_core_hour * self.hours_per_month

        # Memory cost
        memory_cost = memory_gb * self.cost_per_gb_memory_hour * self.hours_per_month

        # Storage cost
        storage_gb = storage_tb * 1024
        storage_cost = storage_gb * self.cost_per_gb_storage_month

        # IOPS cost (for provisioned IOPS storage)
        iops_cost = iops * self.cost_per_iops_month

        # Read replica costs (70% of primary)
        replica_cost = replicas * (instance_cost + memory_cost) * 0.7

        total_cost = instance_cost + memory_cost + storage_cost + iops_cost + replica_cost

        return round(total_cost, 2)

    def get_optimization_recommendations(
        self,
        requirements: DatabaseSizingRequirements,
    ) -> list[str]:
        """
        Get optimization recommendations based on requirements.

        Args:
            requirements: Workload requirements

        Returns:
            List of optimization recommendations
        """
        recommendations = []

        if requirements.read_write_ratio > 5:
            recommendations.append(
                "Consider read replicas to offload read traffic from primary"
            )

        if requirements.avg_query_complexity > 7:
            recommendations.append(
                "Implement query optimization and consider denormalization"
            )
            recommendations.append(
                "Use materialized views for complex aggregations"
            )

        if requirements.peak_daily_transactions > 10_000_000:
            recommendations.append(
                "Consider database sharding for horizontal scaling"
            )
            recommendations.append(
                "Implement connection pooling (PgBouncer, ProxySQL)"
            )

        if requirements.data_retention_days > 365:
            recommendations.append(
                "Implement data archiving to reduce active dataset"
            )
            recommendations.append(
                "Use table partitioning for time-series data"
            )

        if requirements.multi_region:
            recommendations.append(
                "Use global database for cross-region replication"
            )
            recommendations.append(
                "Implement read replicas in each region"
            )

        if requirements.concurrent_users > 50_000:
            recommendations.append(
                "Use connection pooler (PgBouncer) for connection management"
            )
            recommendations.append(
                "Consider Amazon RDS Proxy for serverless scaling"
            )

        if requirements.high_availability:
            recommendations.append(
                "Enable Multi-AZ deployment for automatic failover"
            )
            recommendations.append(
                "Configure automated backups with point-in-time recovery"
            )

        return recommendations

    def get_aws_instance_recommendation(
        self,
        recommendation: DatabaseSizingRecommendation,
    ) -> dict[str, Any]:
        """
        Get AWS RDS instance type recommendation.

        Args:
            recommendation: Sizing recommendation

        Returns:
            Dictionary with AWS instance recommendations
        """
        cores = recommendation.cpu_cores
        memory = recommendation.memory_gb

        # Map to RDS instance types
        if cores <= 2 and memory <= 16:
            instance_class = "db.r6g.large"
            instance_memory = 16
            instance_vcpus = 2
        elif cores <= 4 and memory <= 32:
            instance_class = "db.r6g.xlarge"
            instance_memory = 32
            instance_vcpus = 4
        elif cores <= 8 and memory <= 64:
            instance_class = "db.r6g.2xlarge"
            instance_memory = 64
            instance_vcpus = 8
        elif cores <= 16 and memory <= 128:
            instance_class = "db.r6g.4xlarge"
            instance_memory = 128
            instance_vcpus = 16
        elif cores <= 32 and memory <= 256:
            instance_class = "db.r6g.8xlarge"
            instance_memory = 256
            instance_vcpus = 32
        elif cores <= 48 and memory <= 384:
            instance_class = "db.r6g.12xlarge"
            instance_memory = 384
            instance_vcpus = 48
        else:
            instance_class = "db.r6g.16xlarge"
            instance_memory = 512
            instance_vcpus = 64

        # Storage type recommendation
        if recommendation.iops_required > 64000:
            storage_type = "io2 Block Express"
            max_iops = 256000
        elif recommendation.iops_required > 16000:
            storage_type = "io2"
            max_iops = 64000
        elif recommendation.iops_required > 3000:
            storage_type = "io1"
            max_iops = 64000
        else:
            storage_type = "gp3"
            max_iops = 16000

        return {
            "instance_class": instance_class,
            "vcpus": instance_vcpus,
            "memory_gb": instance_memory,
            "storage_type": storage_type,
            "max_iops": max_iops,
            "multi_az": True,
            "engine": "postgres",
            "engine_version": "15.4",
        }

    def print_sizing_report(
        self,
        requirements: DatabaseSizingRequirements,
        recommendation: DatabaseSizingRecommendation,
    ) -> None:
        """Print formatted sizing report."""
        print("\n" + "=" * 70)
        print("  DATABASE SIZING REPORT")
        print("=" * 70)

        print("\n📊 REQUIREMENTS")
        print("-" * 70)
        print(f"  Concurrent Users:        {requirements.concurrent_users:>15,}")
        print(f"  Peak Daily Transactions: {requirements.peak_daily_transactions:>15,}")
        print(f"  Data Retention:          {requirements.data_retention_days:>15} days")
        print(f"  Read/Write Ratio:        {requirements.read_write_ratio:>15}:1")
        print(f"  Query Complexity:        {requirements.avg_query_complexity:>15}/10")
        print(f"  High Availability:       {str(requirements.high_availability):>15}")
        print(f"  Multi-Region:            {str(requirements.multi_region):>15}")

        print("\n⚡ RECOMMENDATIONS")
        print("-" * 70)
        print(f"  CPU Cores:               {recommendation.cpu_cores:>15}")
        print(f"  Memory:                  {recommendation.memory_gb:>15} GB")
        print(f"  Primary Storage:         {recommendation.primary_storage_tb:>15.1f} TB")
        print(f"  Read Replicas:           {recommendation.read_replicas:>15}")
        print(f"  IOPS Required:           {recommendation.iops_required:>15,}")
        print(f"  Network:                 {recommendation.network_mbps:>15} Mbps")

        print("\n💰 COST ESTIMATE")
        print("-" * 70)
        print(f"  Monthly Cost:            ${recommendation.estimated_cost_monthly:>14,.2f}")
        print(f"  Annual Cost:             ${recommendation.estimated_cost_monthly * 12:>14,.2f}")

        # AWS instance recommendation
        aws_rec = self.get_aws_instance_recommendation(recommendation)
        print("\n☁️ AWS RDS RECOMMENDATION")
        print("-" * 70)
        print(f"  Instance Class:          {aws_rec['instance_class']:>15}")
        print(f"  vCPUs:                   {aws_rec['vcpus']:>15}")
        print(f"  Memory:                  {aws_rec['memory_gb']:>15} GB")
        print(f"  Storage Type:            {aws_rec['storage_type']:>15}")
        print(f"  Engine:                  {aws_rec['engine']:>15} {aws_rec['engine_version']}")

        # Optimization recommendations
        optimizations = self.get_optimization_recommendations(requirements)
        if optimizations:
            print("\n💡 OPTIMIZATION RECOMMENDATIONS")
            print("-" * 70)
            for rec in optimizations:
                print(f"  • {rec}")

        print("\n" + "=" * 70)


def main() -> None:
    """Example usage of Database Performance Sizer."""
    sizer = DatabasePerformanceSizer()

    # Large casino requirements
    requirements = DatabaseSizingRequirements(
        concurrent_users=100000,
        peak_daily_transactions=50_000_000,  # 50M transactions/day
        data_retention_days=2555,  # 7 years
        read_write_ratio=10,  # 10 reads per write
        avg_query_complexity=6,
        high_availability=True,
        multi_region=True,
    )

    recommendation = sizer.calculate_sizing(requirements)
    sizer.print_sizing_report(requirements, recommendation)


if __name__ == "__main__":
    main()
