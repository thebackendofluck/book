<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-03.jpg" alt="Volume 3" width="150" /></a>

# Chapter 23: DevSecOps for iGaming

**📙 Part of Volume 3 — Security Engineering and Runtime Defense** · €84.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZCRSTMH) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 23 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

Comprehensive DevSecOps security pipeline and tools for iGaming platforms, implementing enterprise-grade security scanning, secret detection, and compliance validation.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DevSecOps Pipeline                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │  Developer  │───▶│  Pre-commit │───▶│    CI/CD    │───▶│  Production │  │
│  │   Commit    │    │    Hooks    │    │   Pipeline  │    │   Deploy    │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│         │                  │                  │                  │          │
│         ▼                  ▼                  ▼                  ▼          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Security Scanning Layers                          │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────┐   │   │
│  │  │  SAST   │ │   SCA   │ │Container│ │   IaC   │ │Secret Detect│   │   │
│  │  │CodeQL   │ │  Snyk   │ │  Trivy  │ │Checkov  │ │Gitleaks/ML  │   │   │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
chapter-23/
├── devsecops/                    # Python modules for DevSecOps
│   ├── __init__.py               # Module exports (3,220 lines total)
│   ├── baseline_manager.py       # Detect-secrets baseline management
│   ├── entropy_analyzer.py       # Shannon entropy-based secret detection
│   ├── simple_ml_detector.py     # ML-based secret detection (standalone)
│   ├── ml_secret_detector.py     # Advanced ML detection (requires sklearn)
│   ├── test_secret_detection.py  # Test suite
│   ├── supply_chain_security.py  # Third-party provider security (695 lines)
│   ├── vulnerability_management.py # Enterprise vulnerability management (769 lines)
│   └── security_champion.py      # Security champion program (475 lines)
├── pipelines/                    # CI/CD pipeline configurations
│   ├── github-actions-security.yml    # GitHub Actions workflow
│   ├── azure-pipeline-security.yml    # Azure DevOps pipeline
│   └── security-pipeline-template.yml # Reusable template
├── config/                       # Tool configurations
│   ├── .checkov.yml              # Infrastructure security scanning
│   ├── .tflint.hcl               # Terraform linting rules
│   ├── .pre-commit-config.yaml   # Pre-commit hooks (15+ checks)
│   ├── .yamllint                 # YAML linting rules
│   ├── pyproject.toml            # Python project configuration
│   └── sonar-project.properties  # SonarQube configuration
├── .github/                      # GitHub templates
│   ├── dependabot.yml            # Automated dependency updates
│   ├── security.md               # Security policy
│   ├── ISSUE_TEMPLATE/           # Issue templates
│   └── PULL_REQUEST_TEMPLATE/    # PR templates
├── zap/                          # OWASP ZAP configuration
│   └── rules.tsv                 # Custom security rules
├── setup-devsecops.sh            # Main setup script
├── install-security-tools.sh     # Tool installation script
├── security-toolkit-setup.sh     # Security toolkit setup
├── CODEOWNERS                    # Code review assignments
└── README.md                     # This documentation
```

## Prerequisites

### Install uv (Recommended Python Package Manager)

[uv](https://github.com/astral-sh/uv) is an extremely fast Python package installer and resolver, written in Rust by Astral. It's 10-100x faster than pip.

```bash
# Install uv (macOS/Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with Homebrew (macOS)
brew install uv

# Or with pip (any platform)
pip install uv

# Verify installation
uv --version
```

### Install uvx (Tool Runner)

`uvx` is included with `uv` and allows running Python CLI tools without installing them globally. It automatically creates isolated environments for each tool.

```bash
# uvx comes with uv - verify it works
uvx --version

# Example: Run ruff without installing globally
uvx ruff check .

# Example: Run bandit without installing globally
uvx bandit -r src/

# Example: Run ty (type checker) without installing
uvx ty check src/
```

### Why uv/uvx for DevSecOps?

| Benefit | Description |
|---------|-------------|
| **Speed** | 10-100x faster than pip for dependency resolution |
| **Isolation** | Each tool runs in its own environment |
| **No Conflicts** | Different tools can have different dependencies |
| **No Global Install** | Run tools without polluting global Python |
| **Reproducible** | Lock files ensure consistent versions |

## Quick Start

### 1. Setup DevSecOps Pipeline

```bash
cd scripts/chapter-23

# Run automated setup (installs all tools and configurations)
./setup-devsecops.sh standard

# Or install just the security tools
./install-security-tools.sh
```

### 2. Install Python Dependencies with uv

```bash
# Create virtual environment with uv (10x faster than python -m venv)
uv venv

# Activate virtual environment
source .venv/bin/activate  # Linux/macOS
# or
.venv\Scripts\activate     # Windows

# Install dependencies from pyproject.toml
uv pip install -e ".[dev]"

# Or install specific security tools
uv pip install bandit safety detect-secrets pre-commit

# Install all security scanning tools at once
uv pip install \
    bandit \
    safety \
    detect-secrets \
    pre-commit \
    semgrep \
    checkov \
    ruff
```

### 3. Run Security Tools with uvx (No Installation Required)

The fastest way to run security tools - no installation needed:

```bash
# Python Security Scanning
uvx bandit -r src/                     # Security vulnerabilities in Python code
uvx safety check                        # Check dependencies for known CVEs
uvx semgrep --config auto .            # Pattern-based security scanning

# Python Code Quality
uvx ruff check .                        # Ultra-fast linter (replaces flake8)
uvx ruff format .                       # Code formatter (replaces black)
uvx ty check src/                       # Type checking (Astral's type checker)

# Infrastructure Security
uvx checkov -d terraform/              # IaC security scanning
uvx checkov -f Dockerfile              # Dockerfile security

# Secret Detection
uvx detect-secrets scan .              # Scan for secrets
uvx gitleaks detect                    # Scan git history for secrets

# Container Security
trivy fs .                             # File system vulnerability scan
trivy image myapp:latest               # Container image scan

# Pre-commit (run all hooks)
uvx pre-commit run --all-files         # Run all pre-commit hooks
```

Trivy is a standalone CLI/container image, not a Python package. Install it through your OS package manager, download the release binary, or use the pinned `aquasec/trivy:<version>` image in CI. Do not add `trivy==...` to `requirements.txt`.

### 4. Install Pre-commit Hooks

```bash
# Copy config to your project
cp config/.pre-commit-config.yaml /path/to/your/project/

# Install hooks with uvx (recommended)
cd /path/to/your/project
uvx pre-commit install
uvx pre-commit install --hook-type commit-msg

# Or if pre-commit is installed globally
pre-commit install
pre-commit install --hook-type commit-msg

# Run all hooks manually
uvx pre-commit run --all-files

# Update hooks to latest versions
uvx pre-commit autoupdate
```

### 5. Use Secret Detection

```bash
# Analyze a file for high-entropy secrets
python -m devsecops.entropy_analyzer --file config.py

# Use ML-based detection
python -m devsecops.simple_ml_detector scan /path/to/project

# Run test suite with uvx pytest
uvx pytest devsecops/test_secret_detection.py -v

# Or with installed pytest
python -m pytest devsecops/test_secret_detection.py -v
```

### 6. Type Checking with ty

[ty](https://github.com/astral-sh/ty) is Astral's extremely fast Python type checker.

```bash
# Run type checking on a file
uvx ty check devsecops/entropy_analyzer.py

# Check entire directory
uvx ty check devsecops/

# Check with specific Python version
uvx ty check --python-version 3.12 src/
```

## Chapter Content Modules

### Supply Chain Security (supply_chain_security.py)

Comprehensive security assessment for third-party providers in the iGaming ecosystem.

```python
from devsecops.supply_chain_security import SupplyChainSecurityManager

manager = SupplyChainSecurityManager(redis_client, db_pool)
assessment = await manager.assess_provider_security(provider)
# Returns: overall_score, risk_level, findings, recommendations
```

**Features:**
- SSL/TLS assessment via SSL Labs API
- API authentication and rate limiting tests
- Compliance certification validation (GLI, PCI DSS, ISO 27001)
- SBOM (Software Bill of Materials) generation
- Continuous provider monitoring

**Risk Levels:** CRITICAL (0-49), HIGH (50-69), MEDIUM (70-89), LOW (90-100)

### Vulnerability Management (vulnerability_management.py)

Enterprise-scale vulnerability discovery, triage, and remediation with scanner integrations.

```python
from devsecops.vulnerability_management import VulnerabilityManagementSystem

config = {'nessus_enabled': True, 'snyk_enabled': True, ...}
system = VulnerabilityManagementSystem(redis_client, db_pool, config)
vulnerabilities = await system.discover_vulnerabilities()
```

**Scanner Integrations:** Tenable Nessus, Qualys, Snyk, OWASP Dependency Check

**SLA Definitions:**
| Severity | Remediation | Review |
|----------|-------------|--------|
| CRITICAL | 7 days | 1 day |
| HIGH | 30 days | 3 days |
| MEDIUM | 90 days | 7 days |
| LOW | 180 days | 14 days |

### Security Champion Program (security_champion.py)

Framework for embedding security expertise within development teams.

```python
from devsecops.security_champion import SecurityChampionProgram

program = SecurityChampionProgram(slack_client, training_platform)
await program.initialize_program()
metrics = await program.get_program_metrics()
```

**Program Structure:**
- Champion ratio: 1:20 (one champion per 20 developers)
- Initial training: 40 hours
- Monthly training: 4 hours
- Level progression: BEGINNER → INTERMEDIATE → ADVANCED → EXPERT

## Security Scanning Tools

### Pre-commit Hooks (15+ Checks)

| Hook | Purpose | iGaming Use Case |
|------|---------|------------------|
| detect-secrets | Secret scanning | API keys, tokens |
| gitleaks | Git history secrets | Leaked credentials |
| bandit | Python security | SQL injection, XSS |
| safety | Dependency CVEs | Vulnerable packages |
| checkov | IaC security | Terraform/K8s misconfigs |
| hadolint | Dockerfile lint | Container security |
| shellcheck | Shell script lint | Script vulnerabilities |
| yamllint | YAML validation | Config errors |
| prettier | Code formatting | Consistency |
| eslint | JS/TS security | Frontend security |

### CI/CD Pipeline Stages

| Stage | Tools | Duration | Purpose |
|-------|-------|----------|---------|
| SAST | CodeQL, Semgrep, Bandit | 5-10 min | Code vulnerabilities |
| SCA | Snyk, Safety, npm audit | 2-5 min | Dependency CVEs |
| Container | Trivy, Docker Scout | 3-8 min | Image vulnerabilities |
| IaC | Checkov, tfsec, TFLint | 2-5 min | Infrastructure misconfigs |
| Secrets | Gitleaks, TruffleHog | 1-3 min | Credential leaks |
| DAST | OWASP ZAP | 10-30 min | Runtime vulnerabilities |

## Secret Detection

### Entropy Analyzer

Detects high-entropy strings that may be secrets (API keys, tokens, passwords).

```python
from devsecops.entropy_analyzer import analyze_file, detect_high_entropy_strings

# Analyze a configuration file
result = analyze_file("config.py", min_entropy=4.0)
print(f"Found {result['secrets_found']} potential secrets")

# Analyze raw text
secrets = detect_high_entropy_strings(
    "API_KEY=sk-abc123xyz789...",
    min_entropy=4.0,
    min_length=20
)
```

### ML-Based Detection

Machine learning model trained on known secret patterns.

```python
from devsecops.simple_ml_detector import SimpleMLSecretDetector

detector = SimpleMLSecretDetector()

# Check if a string is a secret
is_secret, confidence = detector.predict("AKIAIOSFODNN7EXAMPLE")
if is_secret and confidence > 0.8:
    print(f"High confidence secret detected: {confidence:.2%}")
```

### Baseline Management

Manage detect-secrets baselines for known/approved secrets.

```python
from devsecops.baseline_manager import BaselineManager

manager = BaselineManager(".secrets.baseline")

# Add approved secret (e.g., test fixtures)
manager.add_to_baseline("test_api_key", "tests/fixtures.py", 42)

# Check if secret is in baseline
if manager.is_in_baseline("test_api_key"):
    print("Secret is approved in baseline")
```

## Pipeline Templates

### GitHub Actions

```yaml
# .github/workflows/security.yml
name: Security Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  security-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run Gitleaks
        uses: gitleaks/gitleaks-action@v2

      - name: Run Trivy
        uses: aquasecurity/trivy-action@0.28.0
        with:
          scan-type: 'fs'
          severity: 'CRITICAL,HIGH'

      - name: Run Checkov
        uses: bridgecrewio/checkov-action@v12
```

### Azure DevOps

```yaml
# azure-pipelines.yml
trigger:
  - main
  - develop

stages:
  - stage: SecurityScan
    jobs:
      - job: SAST
        steps:
          - task: UsePythonVersion@0
          - script: |
              pip install bandit safety
              bandit -r src/ -f json -o bandit-report.json
              safety check --json > safety-report.json
```

## Configuration Files

### Checkov Configuration (.checkov.yml)

```yaml
# Skip specific checks with justification
skip-check:
  - CKV_AWS_18  # S3 access logging (handled separately)
  - CKV_K8S_43  # Image digest (using tags for flexibility)

# Framework selection
framework:
  - terraform
  - kubernetes
  - dockerfile
```

### Pre-commit Configuration

See `config/.pre-commit-config.yaml` for the complete 15+ hook configuration.

## Security Levels

| Level | Setup Time | Target | Features |
|-------|------------|--------|----------|
| Minimal | 5 min | POC/Internal | Basic SAST, dependency scan |
| Standard | 15-20 min | Production | Full SAST, SCA, container, IaC |
| Comprehensive | 25-30 min | Financial/Regulated | + DAST, SLSA Level 2 |
| Maximum | 35-45 min | Critical Infrastructure | + Attestation, code signing |

## OWASP Top 10 Coverage

| OWASP | Vulnerability | Detection Tool |
|-------|---------------|----------------|
| A01 | Broken Access Control | CodeQL, Semgrep |
| A02 | Cryptographic Failures | Bandit, detect-secrets |
| A03 | Injection | CodeQL, Bandit, Semgrep |
| A04 | Insecure Design | Manual review, threat modeling |
| A05 | Security Misconfiguration | Checkov, tfsec, Hadolint |
| A06 | Vulnerable Components | Snyk, Safety, npm audit |
| A07 | Auth Failures | CodeQL, custom rules |
| A08 | Software Integrity | SLSA, Sigstore, SBOM |
| A09 | Security Logging | Custom rules, audit checks |
| A10 | SSRF | CodeQL, Semgrep |

## Metrics and KPIs

### Security Performance

| Metric | Target | Tool |
|--------|--------|------|
| MTTD (Mean Time to Detect) | <4 hours | Pipeline alerts |
| MTTR (Mean Time to Remediate) | <7 days | Jira tracking |
| Vulnerability Scan Coverage | 100% | Snyk, Trivy |
| False Positive Rate | <2% | Baseline tuning |
| Security Test Pass Rate | >98% | CI/CD metrics |

### Compliance

| Standard | Automation | Status |
|----------|------------|--------|
| PCI DSS | Checkov policies | 95%+ |
| ISO 27001 | InSpec profiles | 90%+ |
| SOC 2 | Custom rules | 85%+ |
| GDPR | Data flow checks | Manual |

## Troubleshooting

### Common Issues

**Pre-commit hooks failing:**
```bash
# Update hooks to latest versions
uvx pre-commit autoupdate

# Clear cache
uvx pre-commit clean

# Run manually with verbose output
uvx pre-commit run --all-files -v

# Check specific hook
uvx pre-commit run bandit --all-files
```

**False positives in secret detection:**
```bash
# Add to baseline
uvx detect-secrets scan --update .secrets.baseline

# Or exclude pattern in config
# .pre-commit-config.yaml
exclude: 'tests/fixtures/.*'

# List current baseline
uvx detect-secrets audit .secrets.baseline
```

**Type checking errors with ty:**
```bash
# Check specific file with verbose output
uvx ty check --verbose src/module.py

# Ignore specific rules
uvx ty check --ignore unresolved-import src/

# Check with different Python version
uvx ty check --python-version 3.11 src/
```

**uv/uvx not found:**
```bash
# Reinstall uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Add to PATH (add to ~/.bashrc or ~/.zshrc)
export PATH="$HOME/.cargo/bin:$PATH"

# Verify installation
uv --version
uvx --version
```

**Slow pipeline:**
```bash
# Run scans in parallel using uv's speed
uv pip install --parallel bandit safety checkov

# Use uvx for one-off scans (no install time)
uvx bandit -r src/ &
uvx safety check &
uvx checkov -d terraform/ &
wait

# Limit scan scope to changed files only
git diff --name-only HEAD~1 | xargs uvx bandit
```

## Related Chapters

- [Chapter 24: Security and Compliance](../chapter-24/) - WAF, IDS, network security
- [Chapter 22: Internal Docker Registry](../chapter-22/) - Container security scanning
- [Chapter 19: Fraud Detection](../chapter-19/) - Security monitoring

## References

- [OWASP DevSecOps Guidelines](https://owasp.org/www-project-devsecops-guideline/)
- [SLSA Framework](https://slsa.dev/)
- [Sigstore](https://sigstore.dev/)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
