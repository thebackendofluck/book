<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 32: Testing and QA in Gambling

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 32 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

Comprehensive testing and QA framework for gambling platforms, covering RNG certification, load testing, compliance testing, and continuous integration.

## Architecture Overview

```
scripts/chapter-32/
├── testing-qa/           # Python testing modules
│   ├── __init__.py              # Module exports
│   ├── rng_certification.py     # RNG certification system (GLI/eCOGRA/iTech)
│   ├── load_testing.py          # Load testing for 1M+ concurrent users
│   ├── sports_betting_testing.py    # Sports betting system testing
│   ├── pari_mutuel_testing.py   # Pari-mutuel wagering testing
│   ├── compliance_testing.py    # Multi-jurisdictional compliance
│   └── continuous_testing.py    # CI/CD testing framework
├── load-testing/         # Locust-based load testing
│   ├── locustfile.py           # Main Locust test file
│   ├── bet_placer.py           # Bet placement simulator
│   ├── validator.py            # Response validators
│   ├── get_users.py            # User management
│   ├── locust_logger.py        # Logging utilities
│   └── real_dumb.py            # Simple test scenarios
├── kubernetes/           # Kubernetes manifests
│   ├── master-deployment.yml   # Locust master deployment
│   ├── worker-deployment.yml   # Locust worker deployment
│   ├── service.yml             # Load balancer service
│   ├── configmap.yml           # Configuration
│   ├── rbac.yml                # RBAC permissions
│   └── pvc.yml                 # Persistent volume claims
├── docker/               # Docker configurations
│   ├── Dockerfile              # Multi-stage build with uv
│   └── docker-compose.yml      # Local development setup
├── monitoring/           # Monitoring configs
│   ├── prometheus.yml          # Prometheus configuration
│   ├── alertmanager.yml        # Alert rules
│   ├── grafana-dashboard.json  # Grafana dashboard
│   └── docker-compose.monitoring.yml
└── config/               # Test configuration schemas
    ├── aggressive.yaml         # Aggressive bettor profile
    ├── casual.yaml             # Casual bettor profile
    ├── peruser.yaml            # Per-user configuration
    ├── real-simple.yaml        # Simple test scenarios
    └── tweets.yaml             # Social betting config
```

## Prerequisites

### Install uv (Fast Python Package Manager)

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Homebrew
brew install uv

# pip
pip install uv
```

### Install Python Dependencies with uv

```bash
cd scripts/chapter-32

# Create virtual environment
uv venv

# Activate virtual environment
source .venv/bin/activate  # Linux/macOS
# or: .venv\Scripts\activate  # Windows

# Install dependencies
uv pip install -r requirements.txt
```

### Run Tools with uvx (No Installation Required)

```bash
# Type checking
uvx ty check testing-qa/

# Security scanning
uvx bandit -r testing-qa/

# Linting
uvx ruff check .

# Run Locust
uvx locust -f load-testing/locustfile.py
```

## Testing Components

### 1. RNG Certification System

Comprehensive RNG testing and certification supporting:
- **GLI** (Gaming Laboratories International) - US/International
- **eCOGRA** - UK/International
- **iTech Labs** - Malta/International

| Test Type | Description | Jurisdiction |
|-----------|-------------|--------------|
| Chi-Square | Goodness of fit for uniform distribution | All |
| Kolmogorov-Smirnov | Continuous distribution testing | UK, NJ |
| Runs Test | Randomness in binary sequences | UK, NJ |
| Serial Test | Pattern detection in sequences | UK, NJ |
| Poker Test | Group pattern analysis | UK |
| Diehard Battery | Comprehensive RNG quality | Malta, NJ |
| NIST SP 800-22 | Federal randomness standards | Malta, NJ |

**Usage:**
```python
from testing_qa import RNGCertificationSystem

system = RNGCertificationSystem(redis_client, db_pool)
result = await system.certify_rng("blackjack_v2", "UK")
print(f"Certification Status: {result['status']}")
```

### 2. Load Testing System

Enterprise load testing for million+ concurrent users:

| Test Type | Description | Duration |
|-----------|-------------|----------|
| Spike Test | Sudden load increase | 15-30 min |
| Stress Test | System limit discovery | 1-2 hours |
| Endurance Test | Long-duration stability | 4-24 hours |
| Volume Test | Data throughput capacity | 2-4 hours |
| Scalability Test | Auto-scaling validation | 1-2 hours |

**Locust Quick Start:**
```bash
# Local master with web UI
locust -f load-testing/locustfile.py --host=https://api.example.com

# Headless with 10K users
locust -f load-testing/locustfile.py \
    --host=https://api.example.com \
    --headless \
    --users=10000 \
    --spawn-rate=100
```

### 3. Sports Betting Testing

Testing framework for sports betting platforms:

| Test Category | Tests |
|---------------|-------|
| Rules Evaluation | Moneyline, Spread, Total, Parlay, Teaser, Futures |
| Bet Placement | Valid bets, Invalid odds, Insufficient balance |
| Odds Calculation | American, Decimal, Fractional formats |
| Live Events | Odds updates, Event suspension, Score handling |
| Settlement | Winning/losing bets, Push, Void, Dead heat |

### 4. Compliance Testing

Multi-jurisdictional compliance testing:

| Jurisdiction | Regulator | Key Tests | Coverage |
|--------------|-----------|-----------|----------|
| UK | UKGC | RNG, RTP, Responsible Gaming, GDPR | 95% min |
| Malta | MGA | Game Integrity, Fund Segregation | 98% min |
| New Jersey | NJ DGE | Geolocation, Age Verification | 99% min |
| Sweden | Spelinspektionen | Bonus Regulations, Self-exclusion | 97% min |

## Kubernetes Deployment

### Deploy to Kubernetes

```bash
# Apply all manifests
kubectl apply -f kubernetes/

# Scale workers
kubectl scale deployment locust-worker --replicas=10

# View logs
kubectl logs -l app=locust-master -f
```

### Kubernetes Resources

| Resource | Purpose | Replicas |
|----------|---------|----------|
| locust-master | Web UI and coordination | 1 |
| locust-worker | Load generation | 1-50 |
| locust-config | Configuration settings | - |
| locust-service | Load balancer | - |

## Docker Development

### Build and Run

```bash
cd docker/

# Build image
docker build -t igaming-loadtest:latest ..

# Run master
docker-compose up -d locust-master

# Run with workers
docker-compose up -d --scale locust-worker=5
```

### Docker Compose Services

| Service | Port | Purpose |
|---------|------|---------|
| locust-master | 8089 | Web UI |
| locust-worker | - | Load generation |
| prometheus | 9090 | Metrics |
| grafana | 3000 | Dashboards |

## Monitoring

### Prometheus Metrics

Key metrics collected during load testing:

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `locust_requests_total` | Total requests | - |
| `locust_failures_total` | Failed requests | >1% |
| `locust_response_time_p95` | 95th percentile latency | >1000ms |
| `locust_users` | Active simulated users | - |
| `locust_rps` | Requests per second | - |

### Grafana Dashboards

Pre-configured dashboard includes:
- Request rate and error rate
- Response time percentiles (p50, p95, p99)
- Active users over time
- System resource utilization
- Geographic distribution

## Configuration

### Bettor Profiles

Located in `config/`:

| Profile | Bet Frequency | Session Duration |
|---------|---------------|------------------|
| aggressive | 2 seconds | 60 minutes |
| casual | 15 seconds | 30 minutes |
| real-simple | 30 seconds | 15 minutes |

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TARGET_HOST` | API endpoint | localhost:8080 |
| `LOG_DIR` | Log directory | ./ |
| `KAMBI_OFFERING_URL` | Odds provider URL | - |
| `USE_PREREG` | Use pre-registered users | false |

## Regulatory Compliance

### Certification Bodies

| Body | Region | Standards |
|------|--------|-----------|
| GLI | US/International | GLI-11 v3.0, GLI-13 v3.0, GLI-19 v3.0, GLI-28 v1.0 |
| eCOGRA | UK/International | UKGC Technical Standards |
| iTech Labs | Malta/International | MGA Technical Guidelines |
| BMM Testlabs | Global | ISO/IEC 17025 |

### GLI-28 Player UI Compliance

`gli-28/` — GLI-28 v1.0 (Player User Interface Systems) certification toolkit. Plugged into the existing `ComplianceTestingFramework` via `testing-qa/gli28_runner.py` (call `register(framework)`); standalone CI gate at `gli-28/.gitlab-ci.yml`.

| Script | Purpose | Verdict semantics |
|--------|---------|-------------------|
| `gli-28/gli-28-disclosure-check.py` | DOM scan asserting RTP / paytable / max-win / responsible-gaming links are present and reachable within the GLI-28 click-budget | Deploy-blocking |
| `gli-28/gli-28-counter-drift.py` | Long-running session-timer + loss-counter drift check (counter-freeze / monotonicity violations) | Deploy-blocking |
| `gli-28/gli-28-a11y.sh` | `@axe-core/cli` against the certified game URL list, WCAG 2.1 AA tags, JUnit XML output | Deploy-blocking |
| `gli-28/gli-28-evidence-pack.sh` | Bundles disclosure JSON + drift CSV + axe reports + screenshots into a GPG-signed tarball for the GLI submission | One-shot (audit submission) |
| `testing-qa/gli28_runner.py` | Unified orchestrator — runs all three checks in order, emits single JUnit XML for the CI gate, exposes `register(framework)` for `compliance_testing.py` integration | Deploy-blocking |
| `gli-28/.gitlab-ci.yml` | Reference GitLab CI gate, `allow_failure: false`, `expire_in: 5 years` artifact retention | — |

### Testing Investment (Annual, 1M+ Players)

| Category | Cost Range |
|----------|------------|
| RNG Certification | $200,000-350,000 |
| Load Testing Infrastructure | $150,000-250,000 |
| Compliance Testing Automation | $120,000-200,000 |
| Game Provider Integration | $80,000-150,000 |
| Chaos Engineering | $60,000-120,000 |

## Troubleshooting

### Common Issues

**1. Locust workers not connecting:**
```bash
# Check master is running
kubectl get pods -l app=locust-master

# Verify service discovery
kubectl describe service locust-master
```

**2. High response times:**
- Check database connection pool limits
- Verify Redis cluster health
- Review API server logs

**3. ty check errors:**
```bash
# External packages (expected)
# These are not errors - packages like locust, scipy are external
uvx ty check testing-qa/rng_certification.py
```

### Type Checking Notes

External package imports (locust, scipy, numpy) will show as unresolved when checking individual files. This is expected behavior when the packages are not installed in the current environment.

## Further Reading

- [GLI-11 Gaming Devices](https://gaminglabs.com/gli-11)
- [UKGC Technical Standards](https://www.gamblingcommission.gov.uk/guidance/remote-gambling-and-software-technical-standards)
- [Locust Documentation](https://docs.locust.io)
- [Chaos Engineering Principles](https://principlesofchaos.org)

## Related Chapters

- Chapter 23: DevSecOps for iGaming
- Chapter 17: RNG and Game Fairness
- Chapter 18: Real-Time Clock Systems
