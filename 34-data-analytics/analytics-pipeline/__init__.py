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
Real-Time Analytics Pipeline for iGaming

This package provides real-time streaming analytics using Apache Flink:

- Player activity monitoring
- Game performance tracking
- Revenue metrics streaming
- Risk monitoring and alerting

Components:
- RealTimeAnalyticsPipeline: Main pipeline orchestrator
- StreamProcessors: Player, game, revenue, and risk processors
- OutputSinks: Redis, Kafka, and alert sinks

Usage:
    from analytics_pipeline import RealTimeAnalyticsPipeline

    pipeline = RealTimeAnalyticsPipeline(
        kafka_brokers=["localhost:9092"],
        redis_url="redis://localhost:6379"
    )
    await pipeline.start()

Dependencies:
    pip install apache-flink redis
"""

from .realtime_analytics import (  # ty:ignore[unresolved-import]
    RealTimeAnalyticsPipeline,
    StreamProcessor,
    PlayerActivityProcessor,
    GamePerformanceProcessor,
    RevenueProcessor,
    RiskMonitoringProcessor,
)

__all__ = [
    "RealTimeAnalyticsPipeline",
    "StreamProcessor",
    "PlayerActivityProcessor",
    "GamePerformanceProcessor",
    "RevenueProcessor",
    "RiskMonitoringProcessor",
]
