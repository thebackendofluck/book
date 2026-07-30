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
Database and Infrastructure Sizing Tools for iGaming

This package provides sizing calculators for iGaming infrastructure:

- DatabasePerformanceSizer: CPU, memory, storage, IOPS calculations
- CostEstimator: AWS/GCP/Azure cost estimation
- CapacityPlanner: Growth and capacity planning

Usage:
    from sizing_tools import DatabasePerformanceSizer, DatabaseSizingRequirements

    requirements = DatabaseSizingRequirements(
        concurrent_users=100000,
        peak_daily_transactions=50000000,
        data_retention_days=2555,
        read_write_ratio=10
    )

    sizer = DatabasePerformanceSizer()
    recommendation = sizer.calculate_sizing(requirements)

Dependencies:
    No external dependencies required
"""

from .database_sizer import (  # ty:ignore[unresolved-import]
    DatabasePerformanceSizer,
    DatabaseSizingRequirements,
    DatabaseSizingRecommendation,
)

__all__ = [
    "DatabasePerformanceSizer",
    "DatabaseSizingRequirements",
    "DatabaseSizingRecommendation",
]
