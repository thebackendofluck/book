<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 30: FinOps Deep Dive

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 30 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

This directory contains all the code examples and frameworks referenced in Chapter 30 of the iGaming Technical Book.

## Directory Structure

```
scripts/chapter-30/
├── README.md                           # This file
├── license-scanner/                    # Software License Scanning Framework
│   ├── scanner.py                      # Main license scanner module
│   ├── trivy_integration.py            # Aqua Security Trivy integration
│   ├── sbom_generator.py               # SBOM generation utilities
│   ├── license_policies.py             # License policy definitions
│   ├── compliance_reporter.py          # Compliance reporting module
│   ├── ci_cd_integration.sh            # CI/CD pipeline integration script
│   ├── gitlab-ci.yml                   # GitLab CI/CD example
│   ├── github-actions.yml              # GitHub Actions example
│   ├── config/
│   │   ├── allowed_licenses.json       # Allowed license whitelist
│   │   ├── denied_licenses.json        # Denied license blacklist
│   │   └── policy_rules.yaml           # Policy rule definitions
│   └── requirements.txt                # Python dependencies
└── finops/                             # FinOps Implementation Framework
    ├── __init__.py                     # Package initialization and exports
    ├── cost_allocation.py              # Cost allocation and chargeback system
    ├── multi_cloud_optimizer.py        # Multi-cloud cost optimization
    ├── reserved_instances.py           # Reserved instances optimizer
    ├── kubernetes_costs.py             # Kubernetes cost management
    ├── database_optimizer.py           # Database cost optimization
    ├── culture_framework.py            # FinOps culture and governance
    ├── roi_calculator.py               # ROI calculation framework
    └── README.md                       # Framework documentation
```

## License Scanning Framework

The license scanning framework integrates with **Aqua Security Trivy** to provide comprehensive software license scanning for CI/CD pipelines.

### Features

- **Multi-format SBOM generation** (SPDX, CycloneDX)
- **License policy enforcement** with customizable rules
- **GitOps integration** for automated compliance
- **Vulnerability and license combined scanning**
- **Compliance reporting** with audit trails

### Quick Start with pip

```bash
# Install dependencies
pip install -r license-scanner/requirements.txt

# Install Trivy (if not already installed)
# macOS
brew install trivy

# Linux
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin

# Run license scan
python license-scanner/scanner.py --repo /path/to/repo --output sbom.json
```

### Quick Start with uv (Recommended)

[uv](https://github.com/astral-sh/uv) is an extremely fast Python package installer (10-100x faster than pip), written in Rust by Astral.

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies with uv
uv pip install -r license-scanner/requirements.txt

# Or run directly with uvx (no installation needed)
uvx --with PyYAML --with click --with rich python license-scanner/scanner.py --help
```

### CI/CD Integration

See `license-scanner/ci_cd_integration.sh` for pipeline integration examples.

## FinOps Framework

The FinOps framework provides comprehensive cost management for iGaming cloud infrastructure.

### Components

1. **Cost Allocation System** - Multi-cloud cost tracking and chargeback
2. **Multi-Cloud Optimizer** - Cross-provider cost optimization
3. **Reserved Instances Manager** - Commitment optimization
4. **Kubernetes Cost Manager** - Container cost allocation
5. **Database Optimizer** - Database cost reduction strategies
6. **ROI Calculator** - Business case analysis

### Quick Start with pip

```bash
# Navigate to finops directory
cd scripts/chapter-30/finops

# No external dependencies required for core functionality
# For cloud integrations, install:
pip install boto3 google-cloud-billing azure-mgmt-costmanagement
```

### Quick Start with uv (Recommended)

```bash
# Install cloud integrations with uv (much faster)
uv pip install boto3 google-cloud-billing azure-mgmt-costmanagement

# Or use uvx for one-off execution
uvx --with boto3 python -c "from finops import FinOpsROICalculator; print('OK')"
```

### Usage Example

```python
import asyncio
from finops import (
    FinOpsCostAllocationSystem,
    MultiCloudCostOptimizer,
    ReservedInstancesOptimizer,
    KubernetesCostManager,
    DatabaseCostOptimizer,
    FinOpsCultureFramework,
    FinOpsROICalculator
)

async def run_finops_analysis():
    # Cost Allocation
    allocator = FinOpsCostAllocationSystem({
        "organization": "igaming_corp",
        "providers": ["aws", "gcp", "azure"]
    })
    allocation_results = await allocator.implement_cost_allocation_system()

    # ROI Calculation
    calculator = FinOpsROICalculator({
        "investment_horizon_years": 3,
        "discount_rate": 0.08
    })
    roi_results = await calculator.calculate_finops_roi()

    print(f"System Maturity: {allocation_results['system_maturity_score']:.0%}")
    print(f"Annual ROI: {roi_results['roi_analysis']['annual_roi_percentage']:.0f}%")

asyncio.run(run_finops_analysis())
```

## Requirements

- Python 3.9+
- Trivy 0.50+ (for license scanning)
- Cloud provider CLI tools (aws-cli, gcloud, az)
- Docker (optional, for containerized scanning)

## License

Apache 2.0 - See the book for full licensing details.

## Related Documentation

- [Chapter 30: FinOps Deep Dive](../../20-finops-deep-dive.md)
- [Aqua Security Trivy Documentation](https://trivy.dev/)
- [FinOps Foundation](https://www.finops.org/)

## Code Quality Verification

All code in this repository has been verified using industry-standard tools:

### Type Checking with ty (Astral)

All Python modules have been checked with [ty](https://github.com/astral-sh/ty) type checker:

| Directory | Files Checked | Status |
|-----------|---------------|--------|
| `finops/` | 7 modules | All passed |
| `license-scanner/` | 4 modules | All passed |

### Shell Script Linting with ShellCheck

| Script | Tool | Status |
|--------|------|--------|
| `license-scanner/ci_cd_integration.sh` | ShellCheck | Passed |

**Verification Date:** December 2025
**Tools Used:**
- ty 0.0.1-alpha.32 (Astral type checker)
- ShellCheck 0.10.0

### Running Verification

```bash
# Type check FinOps modules
for f in finops/*.py; do ty check "$f"; done

# Type check License Scanner modules
for f in license-scanner/*.py; do ty check "$f"; done

# Lint shell scripts
shellcheck license-scanner/ci_cd_integration.sh
```

For detailed verification information, see the README.md in each subdirectory.
