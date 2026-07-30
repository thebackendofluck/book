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
Linux Performance Tools Module
==============================

Linux performance analysis tools and kernel comparison utilities
for iGaming infrastructure optimization.
"""

from .performance_analyzer import (  # ty:ignore[unresolved-import]
    LinuxPerformanceAnalyzer,
    LinuxToolCommands,
    PerformanceMetricType,
    PerformanceSnapshot
)
from .kernel_comparison import (  # ty:ignore[unresolved-import]
    KernelPerformanceComparison,
    KernelVersion,
    KernelFeature,
    get_kernel_performance_benchmarks
)

__all__ = [
    "LinuxPerformanceAnalyzer",
    "LinuxToolCommands",
    "PerformanceMetricType",
    "PerformanceSnapshot",
    "KernelPerformanceComparison",
    "KernelVersion",
    "KernelFeature",
    "get_kernel_performance_benchmarks"
]

__version__ = "1.0.0"
