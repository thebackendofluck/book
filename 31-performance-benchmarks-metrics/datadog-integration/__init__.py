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
Datadog Integration Module
==========================

Comprehensive Datadog integration for iGaming platform monitoring.
Provides APM, metrics, logging, and alerting configuration.
"""

from .datadog_monitor import (  # ty:ignore[unresolved-import]
    DatadogIGamingIntegration,
    DatadogMetric,
    DatadogMonitor,
    MetricType,
    AlertPriority
)

__all__ = [
    "DatadogIGamingIntegration",
    "DatadogMetric",
    "DatadogMonitor",
    "MetricType",
    "AlertPriority"
]

__version__ = "1.0.0"
