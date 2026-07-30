# Companion code for "The Backend of Luck" - Chapter 30, FinOps Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
FinOps Framework for iGaming Operations
=======================================

This package provides comprehensive FinOps (Financial Operations) tools
for managing cloud costs in iGaming platforms.

Classes:
    - FinOpsCostAllocationSystem: Cost allocation and chargeback management
    - MultiCloudCostOptimizer: Multi-cloud cost optimization
    - ReservedInstancesOptimizer: Reserved instances and savings plans strategy
    - KubernetesCostManager: Kubernetes cost management
    - DatabaseCostOptimizer: Database cost optimization
    - FinOpsCultureFramework: FinOps culture and governance
    - FinOpsROICalculator: ROI calculation and business cases

Usage:
    from finops import FinOpsCostAllocationSystem, MultiCloudCostOptimizer

    # Initialize cost allocation system
    cost_system = FinOpsCostAllocationSystem(config)
    results = await cost_system.implement_cost_allocation_system()

    # Optimize multi-cloud costs
    optimizer = MultiCloudCostOptimizer(cloud_config)
    savings = await optimizer.optimize_multi_cloud_costs()

Author: iGaming Technical Book
License: MIT
Version: 1.0.0
"""

from .cost_allocation import FinOpsCostAllocationSystem  # ty:ignore[unresolved-import]
from .multi_cloud_optimizer import MultiCloudCostOptimizer  # ty:ignore[unresolved-import]
from .reserved_instances import ReservedInstancesOptimizer  # ty:ignore[unresolved-import]
from .kubernetes_costs import KubernetesCostManager  # ty:ignore[unresolved-import]
from .database_optimizer import DatabaseCostOptimizer  # ty:ignore[unresolved-import]
from .culture_framework import FinOpsCultureFramework  # ty:ignore[unresolved-import]
from .roi_calculator import FinOpsROICalculator  # ty:ignore[unresolved-import]

__all__ = [
    'FinOpsCostAllocationSystem',
    'MultiCloudCostOptimizer',
    'ReservedInstancesOptimizer',
    'KubernetesCostManager',
    'DatabaseCostOptimizer',
    'FinOpsCultureFramework',
    'FinOpsROICalculator'
]

__version__ = '1.0.0'
