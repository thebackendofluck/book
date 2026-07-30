#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Performance Monitor Framework for iGaming Operations
=====================================================

A comprehensive framework for monitoring and optimizing performance
in iGaming platforms across APIs, databases, frontend, and gaming systems.

This module provides enterprise-grade performance monitoring with:
- API performance benchmarking and monitoring
- Database performance optimization
- Frontend Core Web Vitals tracking
- Gaming-specific performance metrics
- Monitoring and alerting frameworks
- Business impact analysis

Usage:
    from performance_monitor import (
        APIPerformanceMonitor,
        DatabasePerformanceMonitor,
        FrontendPerformanceMonitor,
        GamingPerformanceMonitor,
        MonitoringAlertingFramework,
        PerformanceOptimizationFramework,
        PerformanceBusinessImpactAnalyzer
    )

Example:
    import asyncio

    async def main():
        monitor = APIPerformanceMonitor({
            "organization": "igaming_corp",
            "environment": "production"
        })
        results = await monitor.monitor_api_performance()
        print(f"Performance Score: {results['performance_score']:.0%}")

    asyncio.run(main())
"""

from .api_performance import APIPerformanceMonitor  # ty:ignore[unresolved-import]
from .database_performance import DatabasePerformanceMonitor  # ty:ignore[unresolved-import]
from .frontend_performance import FrontendPerformanceMonitor  # ty:ignore[unresolved-import]
from .gaming_performance import GamingPerformanceMonitor  # ty:ignore[unresolved-import]
from .monitoring_alerting import MonitoringAlertingFramework  # ty:ignore[unresolved-import]
from .optimization_framework import PerformanceOptimizationFramework  # ty:ignore[unresolved-import]
from .business_impact import PerformanceBusinessImpactAnalyzer  # ty:ignore[unresolved-import]

__all__ = [
    "APIPerformanceMonitor",
    "DatabasePerformanceMonitor",
    "FrontendPerformanceMonitor",
    "GamingPerformanceMonitor",
    "MonitoringAlertingFramework",
    "PerformanceOptimizationFramework",
    "PerformanceBusinessImpactAnalyzer"
]

__version__ = "1.0.0"
__author__ = "iGaming Technical Book"
