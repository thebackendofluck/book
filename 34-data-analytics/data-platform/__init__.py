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
Enterprise Data Platform for iGaming

This package provides a comprehensive data platform implementation
for iGaming platforms, including:

- Event ingestion and streaming via Kafka
- Real-time analytics storage with ClickHouse
- Operational data management with PostgreSQL
- Caching layer with Redis
- Player analytics and ML feature engineering
- Business intelligence dashboards

Components:
- EnterpriseDataPlatform: Main data platform class
- DataEvent: Event data model
- DataPipelineConfig: Configuration settings
- PlayerAnalytics: Player behavior analysis
- BusinessIntelligence: BI metrics and dashboards

Usage:
    from data_platform import EnterpriseDataPlatform, DataPipelineConfig

    config = DataPipelineConfig(
        kafka_brokers=["localhost:9092"],
        clickhouse_host="localhost",
        postgres_url="postgresql://localhost/casino",
        redis_url="redis://localhost:6379"
    )

    platform = EnterpriseDataPlatform(config)
    await platform.initialize()

Dependencies:
    pip install aiokafka asyncpg redis clickhouse-driver
    # or
    uv pip install aiokafka asyncpg redis clickhouse-driver
"""

from .data_platform import (  # ty:ignore[unresolved-import]
    EnterpriseDataPlatform,
    DataEvent,
    DataPipelineConfig,
)
from .player_analytics import PlayerAnalytics  # ty:ignore[unresolved-import]
from .business_intelligence import BusinessIntelligenceEngine  # ty:ignore[unresolved-import]

__all__ = [
    "EnterpriseDataPlatform",
    "DataEvent",
    "DataPipelineConfig",
    "PlayerAnalytics",
    "BusinessIntelligenceEngine",
]
