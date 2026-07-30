# FinOps Framework for iGaming Operations

A comprehensive Python framework for Financial Operations (FinOps) in iGaming cloud infrastructure, providing cost allocation, optimization, and governance capabilities.

## Overview

This framework implements enterprise-grade FinOps practices for iGaming platforms, enabling organizations to:

- **Allocate Costs**: Implement comprehensive cost allocation and chargeback systems
- **Optimize Multi-Cloud**: Reduce costs across AWS, GCP, and Azure
- **Manage Reservations**: Optimize reserved instances and savings plans
- **Control Kubernetes Costs**: Right-size clusters and optimize workloads
- **Reduce Database Costs**: Optimize database infrastructure spending
- **Build Culture**: Establish FinOps governance and training programs
- **Calculate ROI**: Build business cases for optimization initiatives

## Framework Components

| Module | Class | Description |
|--------|-------|-------------|
| `cost_allocation.py` | `FinOpsCostAllocationSystem` | Cost allocation, tagging, and chargeback |
| `multi_cloud_optimizer.py` | `MultiCloudCostOptimizer` | Multi-cloud cost optimization |
| `reserved_instances.py` | `ReservedInstancesOptimizer` | Reserved instances and savings plans |
| `kubernetes_costs.py` | `KubernetesCostManager` | Kubernetes cost management |
| `database_optimizer.py` | `DatabaseCostOptimizer` | Database cost optimization |
| `culture_framework.py` | `FinOpsCultureFramework` | FinOps culture and governance |
| `roi_calculator.py` | `FinOpsROICalculator` | ROI calculation and business cases |

## Installation

The core FinOps framework has no external dependencies and uses only Python standard library. For cloud provider integrations, additional packages are required.

### Using pip

```bash
# Navigate to the finops directory
cd scripts/chapter-30/finops

# Core functionality works out of the box with Python 3.9+
# No dependencies required for core functionality

# For full functionality with cloud integrations:
pip install boto3 google-cloud-billing azure-mgmt-costmanagement
```

### Using uv (Recommended)

[uv](https://github.com/astral-sh/uv) is an extremely fast Python package installer and resolver, written in Rust by Astral.

```bash
# Install uv (if not already installed)
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Navigate to the finops directory
cd scripts/chapter-30/finops

# Install cloud integration dependencies with uv
uv pip install boto3 google-cloud-billing azure-mgmt-costmanagement
```

### Using uvx for One-off Execution

`uvx` allows you to run Python scripts with dependencies without installing them permanently:

```bash
# Run the ROI calculator with cloud dependencies
uvx --with boto3 --with google-cloud-billing python -c "
import asyncio
from finops import FinOpsROICalculator
result = asyncio.run(FinOpsROICalculator({}).calculate_finops_roi())
print(f'Annual ROI: {result[\"roi_analysis\"][\"annual_roi_percentage\"]:.0f}%')
"
```

### Creating a Virtual Environment with uv

```bash
# Create a new virtual environment
uv venv

# Activate it
source .venv/bin/activate  # Linux/macOS
# or
.venv\Scripts\activate     # Windows

# Install dependencies
uv pip install boto3 google-cloud-billing azure-mgmt-costmanagement
```

## Quick Start

### Cost Allocation System

```python
import asyncio
from finops import FinOpsCostAllocationSystem

async def main():
    config = {
        "organization": "igaming_corp",
        "cost_centers": ["casino", "sports", "platform", "shared"],
        "providers": ["aws", "gcp", "azure"]
    }

    allocator = FinOpsCostAllocationSystem(config)
    results = await allocator.implement_cost_allocation_system()

    print(f"System Maturity Score: {results['system_maturity_score']:.0%}")
    print(f"Tagging Compliance: {results['tagging_strategy']['tagging_compliance_rate']:.0%}")

asyncio.run(main())
```

### Multi-Cloud Cost Optimization

```python
import asyncio
from finops import MultiCloudCostOptimizer

async def main():
    config = {
        "providers": ["aws", "gcp", "azure"],
        "regions": ["us-east-1", "eu-west-1", "ap-southeast-1"],
        "optimization_targets": ["compute", "storage", "database"]
    }

    optimizer = MultiCloudCostOptimizer(config)
    savings = await optimizer.optimize_multi_cloud_costs()

    print(f"Annual Savings Potential: €{savings['overall_savings_potential']['annual_savings']:,}")
    print(f"ROI: {savings['overall_savings_potential']['roi_percentage']}%")

asyncio.run(main())
```

### Reserved Instances Strategy

```python
import asyncio
from finops import ReservedInstancesOptimizer

async def main():
    config = {
        "cloud_provider": "aws",
        "analysis_period_days": 90,
        "target_coverage": 0.75
    }

    optimizer = ReservedInstancesOptimizer(config)
    recommendations = await optimizer.optimize_reserved_instances()

    print(f"Total Annual Savings: €{recommendations['reservation_recommendations']['total_annual_savings']:,}")
    print(f"ROI: {recommendations['reservation_recommendations']['roi_percentage']}%")

asyncio.run(main())
```

### Kubernetes Cost Management

```python
import asyncio
from finops import KubernetesCostManager

async def main():
    config = {
        "cluster_name": "igaming-prod",
        "cloud_provider": "aws",
        "namespaces": ["casino", "sports", "platform"]
    }

    manager = KubernetesCostManager(config)
    optimization = await manager.optimize_kubernetes_costs()

    print(f"Monthly Savings: €{optimization['total_cost_savings']['monthly_savings']:,}")
    print(f"Efficiency Improvement: {optimization['total_cost_savings']['efficiency_improvement']:.0%}")

asyncio.run(main())
```

### Database Cost Optimization

```python
import asyncio
from finops import DatabaseCostOptimizer

async def main():
    config = {
        "database_type": "aurora_mysql",
        "cloud_provider": "aws",
        "environment": "production"
    }

    optimizer = DatabaseCostOptimizer(config)
    savings = await optimizer.optimize_database_costs()

    print(f"Monthly Savings: €{savings['total_database_savings']['monthly_savings']:,}")
    print(f"Annual Savings: €{savings['total_database_savings']['annual_savings']:,}")

asyncio.run(main())
```

### ROI Calculator

```python
import asyncio
from finops import FinOpsROICalculator

async def main():
    config = {
        "investment_horizon_years": 3,
        "discount_rate": 0.08,
        "risk_tolerance": "moderate"
    }

    calculator = FinOpsROICalculator(config)
    roi = await calculator.calculate_finops_roi()

    print(f"Annual ROI: {roi['roi_analysis']['annual_roi_percentage']:.0f}%")
    print(f"Payback Period: {roi['roi_analysis']['payback_period_months']:.1f} months")
    print(f"3-Year NPV: €{roi['roi_analysis']['npv_3_year']:,.0f}")
    print(f"Business Case: {roi['business_case_strength']}")

asyncio.run(main())
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      FinOps Framework                            │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐ │
│  │ Cost Allocation  │  │  Multi-Cloud     │  │   Reserved    │ │
│  │     System       │  │   Optimizer      │  │   Instances   │ │
│  └────────┬─────────┘  └────────┬─────────┘  └───────┬───────┘ │
│           │                      │                    │         │
│  ┌────────┴─────────┐  ┌────────┴─────────┐  ┌───────┴───────┐ │
│  │   Kubernetes     │  │    Database      │  │    Culture    │ │
│  │     Costs        │  │   Optimizer      │  │   Framework   │ │
│  └────────┬─────────┘  └────────┬─────────┘  └───────┬───────┘ │
│           │                      │                    │         │
│           └──────────────────────┼────────────────────┘         │
│                                  │                              │
│                         ┌────────┴─────────┐                    │
│                         │  ROI Calculator  │                    │
│                         └──────────────────┘                    │
└─────────────────────────────────────────────────────────────────┘
```

## Key Features

### Cost Allocation
- Multi-cloud cost data collection (AWS, GCP, Azure)
- Comprehensive resource tagging taxonomy
- Automated chargeback calculation
- Executive and operational dashboards

### Multi-Cloud Optimization
- Cross-provider cost comparison
- Workload placement optimization
- Data transfer cost reduction
- Vendor negotiation strategies

### Reserved Instances
- Usage pattern analysis
- Reservation recommendations
- Savings plan comparison
- Purchase timing optimization

### Kubernetes Costs
- Cluster right-sizing
- Auto-scaling configuration
- Spot instance strategies
- Storage and network optimization

### Database Optimization
- Instance right-sizing
- Aurora Serverless evaluation
- Storage optimization
- Query performance tuning

### Culture & Governance
- FinOps Center of Excellence setup
- Decision-making frameworks
- Training programs
- Accountability structures

### ROI Analysis
- Cost savings quantification
- Productivity gain assessment
- Risk reduction valuation
- NPV and payback calculations

## Integration with iGaming Operations

This framework is designed specifically for iGaming platforms with considerations for:

- **Peak Traffic Patterns**: Gaming events, weekends, and promotions
- **Multi-Jurisdiction Operations**: Cost allocation across regulated markets
- **Compliance Requirements**: PCI-DSS, gambling regulations
- **Real-time Systems**: Low-latency infrastructure cost optimization

## Typical Savings

Based on implementations across iGaming platforms:

| Optimization Area | Typical Savings | Implementation Time |
|-------------------|-----------------|---------------------|
| Reserved Instances | 25-45% | 1-2 months |
| Kubernetes Optimization | 30-40% | 2-3 months |
| Database Right-sizing | 35-50% | 1-2 months |
| Multi-Cloud Strategy | 15-30% | 3-6 months |
| **Total Portfolio** | **30-50%** | **6-12 months** |

## Related Resources

- [Chapter 30: FinOps Deep Dive](../../20-finops-deep-dive.md) - Complete FinOps guide
- [License Scanner](../license-scanner/) - Software license compliance scanning
- [FinOps Foundation](https://www.finops.org/) - Industry framework and certification

## License

Apache License 2.0 - see the LICENSE file at the repository root.

## Code Quality Verification

This code has been verified using the following tools:

### Type Checking with ty (Astral)

All Python modules have been checked with [ty](https://github.com/astral-sh/ty) type checker:

```bash
ty check cost_allocation.py      # All checks passed!
ty check multi_cloud_optimizer.py # All checks passed!
ty check reserved_instances.py    # All checks passed!
ty check kubernetes_costs.py      # All checks passed!
ty check database_optimizer.py    # All checks passed!
ty check culture_framework.py     # All checks passed!
ty check roi_calculator.py        # All checks passed!
```

**Verification Date:** December 2025
**ty Version:** 0.0.1-alpha.32

### Verification Summary

| Module | Type Checking | Status |
|--------|--------------|--------|
| `cost_allocation.py` | ty | Passed |
| `multi_cloud_optimizer.py` | ty | Passed |
| `reserved_instances.py` | ty | Passed |
| `kubernetes_costs.py` | ty | Passed |
| `database_optimizer.py` | ty | Passed |
| `culture_framework.py` | ty | Passed |
| `roi_calculator.py` | ty | Passed |

**Note:** The `__init__.py` file shows import resolution warnings when checked from outside the package directory. This is expected behavior and the imports work correctly when the package is installed or run from the proper Python path.
