# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Cost Optimization Module

This module provides comprehensive cost optimization strategies for the fraud detection system,
including resource optimization, usage-based pricing, and automated cost management.
"""

__version__ = "1.0.0"
__author__ = "Cost Optimization Team"

from .cost_optimization_engine import (  # ty:ignore[unresolved-import]
    CostOptimizationEngine,
    CostOptimizationRule,
    CostAnalysis,
    ResourceUsage,
    cost_optimization_engine,
    initialize_cost_optimization
)

__all__ = [
    "CostOptimizationEngine",
    "CostOptimizationRule",
    "CostAnalysis",
    "ResourceUsage",
    "cost_optimization_engine",
    "initialize_cost_optimization"
]