# License Scanner Framework for iGaming Operations

A comprehensive software license scanning and compliance framework integrating with Aqua Security Trivy for CI/CD pipelines.

## Overview

This framework provides enterprise-grade license scanning capabilities for iGaming platforms, enabling organizations to:

- **Scan Dependencies**: Analyze license compliance across all dependencies
- **Enforce Policies**: Implement customizable license policies
- **Generate SBOMs**: Create Software Bill of Materials in SPDX and CycloneDX formats
- **Integrate CI/CD**: Automate compliance checks in GitOps pipelines
- **Report Compliance**: Generate detailed compliance reports

## Directory Structure

```
license-scanner/
├── scanner.py              # Main license scanner CLI and core logic
├── trivy_integration.py    # Aqua Security Trivy wrapper
├── license_policies.py     # License policy engine and risk classification
├── sbom_generator.py       # SBOM generation (SPDX, CycloneDX)
├── compliance_reporter.py  # Report generation (HTML, JSON, SARIF)
├── ci_cd_integration.sh    # CI/CD pipeline shell script
├── github-actions.yml      # GitHub Actions workflow example
├── gitlab-ci.yml           # GitLab CI configuration example
├── requirements.txt        # Python dependencies
├── config/
│   ├── allowed_licenses.json   # Allowed license whitelist
│   ├── denied_licenses.json    # Denied license blacklist
│   └── policy_rules.yaml       # Policy rule definitions
└── README.md               # This file
```

## Installation

### Prerequisites

- Python 3.9+
- Trivy (for license scanning)

### Install Trivy

```bash
# macOS
brew install trivy

# Linux
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin

# Verify installation
trivy --version
```

### Install Python Dependencies with pip

```bash
# Navigate to the license-scanner directory
cd scripts/chapter-30/license-scanner

# Install dependencies
pip install -r requirements.txt
```

### Install with uv (Recommended)

[uv](https://github.com/astral-sh/uv) is an extremely fast Python package installer and resolver, written in Rust by Astral.

```bash
# Install uv (if not already installed)
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Navigate to the license-scanner directory
cd scripts/chapter-30/license-scanner

# Install dependencies with uv (10-100x faster than pip)
uv pip install -r requirements.txt
```

### Using uvx for One-off Execution

`uvx` allows you to run Python scripts with dependencies without installing them permanently:

```bash
# Run the scanner directly without installing
uvx --with PyYAML --with click --with rich python scanner.py --help

# Run a license scan
uvx --with PyYAML --with click --with rich --with requests python scanner.py scan --target /path/to/repo
```

### Creating a Virtual Environment with uv

```bash
# Create a new virtual environment
uv venv

# Activate it
source .venv/bin/activate  # Linux/macOS
# or
.venv\Scripts\activate     # Windows

# Install all dependencies
uv pip install -r requirements.txt
```

## Quick Start

### Command Line Usage

```bash
# Scan a repository
python scanner.py scan --target /path/to/repo --format html

# Generate SBOM
python scanner.py sbom --target /path/to/repo --format spdx-json

# Check against policy
python scanner.py check --target /path/to/repo --policy config/policy_rules.yaml
```

### Python API Usage

```python
from trivy_integration import TrivyScanner
from license_policies import PolicyEngine, RiskLevel
from sbom_generator import SBOMGenerator, SBOMFormat

# Initialize scanner
scanner = TrivyScanner()

# Scan repository
result = await scanner.scan_repository("/path/to/repo")

# Check policy compliance
policy = PolicyEngine()
violations = policy.check_compliance(result.licenses)

# Generate SBOM
generator = SBOMGenerator()
sbom = generator.generate(result.licenses, SBOMFormat.SPDX_JSON)
```

### CI/CD Integration

```bash
# Run in CI/CD pipeline
./ci_cd_integration.sh --target /app --format spdx-json --fail-on-error true
```

## License Risk Classification

| Risk Level | Licenses | Action |
|------------|----------|--------|
| Low | MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC | Allowed |
| Medium | LGPL-2.1, LGPL-3.0, MPL-2.0, EPL-2.0 | Review Required |
| High | GPL-2.0, GPL-3.0 | Approval Required |
| Critical | AGPL-3.0, SSPL-1.0, BSL-1.1 | Blocked |

## Configuration

### Policy Rules (config/policy_rules.yaml)

```yaml
settings:
  max_risk_level: medium
  require_osi_approved: true
  allow_copyleft: false
  fail_on_violation: true
  min_compliance_score: 95
```

### Allowed Licenses (config/allowed_licenses.json)

Pre-configured list of licenses approved for iGaming production use.

### Denied Licenses (config/denied_licenses.json)

Pre-configured list of licenses that are blocked for compliance reasons.

## Output Formats

- **HTML**: Interactive compliance report
- **JSON**: Machine-readable scan results
- **SARIF**: IDE integration format
- **SPDX**: Standard SBOM format
- **CycloneDX**: Alternative SBOM format

## Related Resources

- [Chapter 30: FinOps Deep Dive](../../20-finops-deep-dive.md) - Complete guide
- [FinOps Framework](../finops/) - Cost optimization tools
- [Aqua Security Trivy](https://trivy.dev/) - Scanner documentation

## License

Apache License 2.0 - see the LICENSE file at the repository root.

## Code Quality Verification

This code has been verified using the following tools:

### Type Checking with ty (Astral)

All Python modules have been checked with [ty](https://github.com/astral-sh/ty) type checker:

```bash
ty check trivy_integration.py    # All checks passed!
ty check license_policies.py     # All checks passed!
ty check sbom_generator.py       # All checks passed!
ty check compliance_reporter.py  # All checks passed!
```

### Shell Script Linting with ShellCheck

The bash script has been verified with [ShellCheck](https://www.shellcheck.net/):

```bash
shellcheck ci_cd_integration.sh  # No issues found!
```

**Verification Date:** December 2025
**ty Version:** 0.0.1-alpha.32
**ShellCheck Version:** 0.10.0

### Verification Summary

| Module | Tool | Status |
|--------|------|--------|
| `trivy_integration.py` | ty | Passed |
| `license_policies.py` | ty | Passed |
| `sbom_generator.py` | ty | Passed |
| `compliance_reporter.py` | ty | Passed |
| `ci_cd_integration.sh` | shellcheck | Passed |

### Issues Fixed During Verification

1. **sbom_generator.py**: Fixed deprecated `datetime.utcnow()` usage - now uses timezone-aware `datetime.now(timezone.utc)`
2. **sbom_generator.py**: Added explicit type annotations for dictionary variables
3. **ci_cd_integration.sh**: Fixed SC2034 (unused variables) by exporting `POLICY_FILE`
4. **ci_cd_integration.sh**: Fixed SC2155 (declare and assign separately) for local variables

**Note:** The `scanner.py` file shows import resolution warnings when checked from outside the package directory. This is expected behavior and the imports work correctly when the scripts are run from the license-scanner directory.
