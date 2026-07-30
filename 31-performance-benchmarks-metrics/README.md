<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 31: Performance Benchmarks and Metrics

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 31 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

This directory contains all the code examples and frameworks referenced in Chapter 31 of the iGaming Technical Book.

## Directory Structure

```
scripts/chapter-31/
├── README.md                           # This file
├── performance-monitor/                # Performance Monitoring Framework
│   ├── __init__.py                     # Package initialization
│   ├── api_performance.py              # API performance monitoring
│   ├── database_performance.py         # Database performance monitoring
│   ├── frontend_performance.py         # Frontend/Core Web Vitals monitoring
│   ├── gaming_performance.py           # Gaming-specific metrics
│   ├── monitoring_alerting.py          # Monitoring and alerting framework
│   ├── optimization_framework.py       # Performance optimization strategies
│   └── business_impact.py              # Business impact analysis
├── infra-benchmarks/                   # Infrastructure Component Benchmarks
│   ├── __init__.py                     # Package initialization
│   ├── kafka_benchmark.py              # Apache Kafka performance benchmarks
│   ├── opensearch_benchmark.py         # OpenSearch/Elasticsearch benchmarks
│   ├── rds_benchmark.py                # AWS RDS (PostgreSQL/MySQL) benchmarks
│   ├── redis_benchmark.py              # Redis/ElastiCache benchmarks
│   └── benchmark_runner.py             # Unified benchmark runner
├── linux-tools/                        # Linux Performance Tools (Brendan Gregg)
│   ├── __init__.py                     # Package initialization
│   ├── performance_analyzer.py         # Linux performance analysis utilities
│   ├── kernel_comparison.py            # Kernel version comparison
│   ├── performance_audit.sh            # USE method performance audit script
│   └── mtu_check.sh                    # MTU validation and network config generator
└── datadog-integration/                # Datadog Integration
    ├── __init__.py                     # Package initialization
    ├── datadog_monitor.py              # Datadog monitoring configuration
    └── config/
        └── datadog.yaml                # Datadog agent configuration
```

## Performance Monitoring Framework

The performance monitoring framework provides comprehensive monitoring for iGaming platforms.

### Components

| Module | Class | Description |
|--------|-------|-------------|
| `api_performance.py` | `APIPerformanceMonitor` | API response times, throughput, error rates |
| `database_performance.py` | `DatabasePerformanceMonitor` | Query performance, connections, replication |
| `frontend_performance.py` | `FrontendPerformanceMonitor` | Core Web Vitals, UX metrics |
| `gaming_performance.py` | `GamingPerformanceMonitor` | Game loading, betting, live dealer metrics |
| `monitoring_alerting.py` | `MonitoringAlertingFramework` | Alert configuration, escalation |
| `optimization_framework.py` | `PerformanceOptimizationFramework` | Optimization strategies |
| `business_impact.py` | `PerformanceBusinessImpactAnalyzer` | Revenue and business impact |

### Installation

The core framework uses only Python standard library. No external dependencies required.

#### Using pip

```bash
# Navigate to the performance-monitor directory
cd scripts/chapter-31/performance-monitor

# Core functionality works out of the box with Python 3.9+
# No dependencies required for core functionality

# For optional cloud integrations:
pip install boto3 google-cloud-monitoring azure-monitor-query
```

#### Using uv (Recommended)

[uv](https://github.com/astral-sh/uv) is an extremely fast Python package installer and resolver, written in Rust by Astral.

```bash
# Install uv (if not already installed)
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Navigate to the directory
cd scripts/chapter-31/performance-monitor

# Install optional cloud integrations with uv
uv pip install boto3 google-cloud-monitoring azure-monitor-query
```

#### Using uvx for One-off Execution

`uvx` allows you to run Python scripts with dependencies without installing them permanently:

```bash
# Run the performance analyzer with cloud dependencies
uvx --with boto3 python -c "
import asyncio
from performance_monitor import APIPerformanceMonitor
result = asyncio.run(APIPerformanceMonitor({}).monitor_api_performance())
print(f'Performance Score: {result[\"performance_score\"]:.0%}')
"
```

### Quick Start

```python
import asyncio
from performance_monitor import (
    APIPerformanceMonitor,
    DatabasePerformanceMonitor,
    FrontendPerformanceMonitor,
    GamingPerformanceMonitor,
    MonitoringAlertingFramework,
    PerformanceOptimizationFramework,
    PerformanceBusinessImpactAnalyzer
)

async def run_performance_analysis():
    # API Performance Monitoring
    api_monitor = APIPerformanceMonitor({
        "organization": "igaming_corp",
        "environment": "production"
    })
    api_results = await api_monitor.monitor_api_performance()
    print(f"API Performance Score: {api_results['performance_score']:.0%}")

    # Database Performance Monitoring
    db_monitor = DatabasePerformanceMonitor({
        "database_type": "postgresql"
    })
    db_results = await db_monitor.monitor_database_performance()
    print(f"Database Performance Score: {db_results['overall_performance_score']:.0%}")

    # Gaming Performance Monitoring
    gaming_monitor = GamingPerformanceMonitor({})
    gaming_results = await gaming_monitor.monitor_gaming_performance()
    print(f"Gaming Performance Score: {gaming_results['gaming_performance_score']:.0%}")

asyncio.run(run_performance_analysis())
```

## Infrastructure Component Benchmarks

The infrastructure benchmarks module provides comprehensive performance testing for critical infrastructure components used in iGaming platforms.

### Components

| Module | Class | Description |
|--------|-------|-------------|
| `kafka_benchmark.py` | `KafkaBenchmark` | Apache Kafka throughput, latency, and lag testing |
| `opensearch_benchmark.py` | `OpenSearchBenchmark` | Search and indexing performance |
| `rds_benchmark.py` | `RDSBenchmark` | AWS RDS (PostgreSQL/MySQL) query performance |
| `redis_benchmark.py` | `RedisBenchmark` | Redis/ElastiCache latency and throughput |
| `benchmark_runner.py` | `InfrastructureBenchmarkRunner` | Unified runner for all benchmarks |

### Performance Targets for iGaming

| Component | Metric | Target | Critical Threshold |
|-----------|--------|--------|-------------------|
| **Kafka** | Producer latency P99 | <10ms | <25ms |
| **Kafka** | Consumer lag | <1000 msg | <5000 msg |
| **Kafka** | Throughput | >100K msg/s | >50K msg/s |
| **OpenSearch** | Search latency P99 | <100ms | <200ms |
| **OpenSearch** | Indexing throughput | >10K docs/s | >5K docs/s |
| **RDS** | Simple query P99 | <10ms | <25ms |
| **RDS** | Complex query P99 | <100ms | <200ms |
| **RDS** | Connection pool efficiency | >90% | >80% |
| **Redis** | GET latency P99 | <1ms | <5ms |
| **Redis** | SET latency P99 | <2ms | <10ms |
| **Redis** | Cache hit rate | >95% | >85% |

### Quick Start

```bash
# Install dependencies
uv pip install aiokafka opensearch-py asyncpg redis boto3

# Run all infrastructure benchmarks
python -m infra_benchmarks.benchmark_runner

# Or run specific benchmarks
python -m infra_benchmarks.kafka_benchmark
python -m infra_benchmarks.opensearch_benchmark
python -m infra_benchmarks.rds_benchmark
python -m infra_benchmarks.redis_benchmark
```

### Example Usage

```python
import asyncio
from infra_benchmarks import InfrastructureBenchmarkRunner

async def run_benchmarks():
    runner = InfrastructureBenchmarkRunner()

    # Run all benchmarks
    results = await runner.run_all_benchmarks()

    # Print comprehensive report
    runner.print_comprehensive_report(results)

    # Get overall health score
    score = runner.get_overall_health_score()
    print(f"Overall Infrastructure Health: {score}%")

    # Get critical issues
    issues = runner.get_critical_issues()
    if issues:
        print("Critical issues found:")
        for issue in issues:
            print(f"  - {issue}")

    # Export to JSON for CI/CD integration
    runner.export_json("benchmark_results.json")

asyncio.run(run_benchmarks())
```

### Sample Output

```
======================================================================
  🏗️  INFRASTRUCTURE PERFORMANCE BENCHMARK REPORT
  2025-12-18 15:30:00 UTC
======================================================================

📊 OVERALL INFRASTRUCTURE HEALTH: 87.5% [█████████████████░░░░░░░░░░░░░░] 🟢

┌─────────────────┬────────┬──────────┬────────┬──────────┬────────────┐
│ Component       │ Tests  │ Passed   │ Warn   │ Failed   │ Health     │
├─────────────────┼────────┼──────────┼────────┼──────────┼────────────┤
│ Kafka           │      9 │        8 │      1 │        0 │ 🟢  94.4%  │
│ OpenSearch      │      9 │        7 │      2 │        0 │ 🟡  88.9%  │
│ AWS RDS         │     10 │        8 │      1 │        1 │ 🟡  82.5%  │
│ Redis           │      9 │        8 │      1 │        0 │ 🟢  94.4%  │
└─────────────────┴────────┴──────────┴────────┴──────────┴────────────┘
```

## Linux Performance Tools

The Linux tools module provides interfaces to common Linux performance utilities and kernel comparison features, based on **Brendan Gregg's** methodologies from:

- **"Systems Performance: Enterprise and the Cloud"** (2nd Edition, 2020)
- **"BPF Performance Tools"** (2019)

### Components

| Module | Class/Script | Description |
|--------|--------------|-------------|
| `performance_analyzer.py` | `LinuxPerformanceAnalyzer` | System metrics collection and analysis |
| `performance_analyzer.py` | `LinuxToolCommands` | Ready-to-use command templates |
| `kernel_comparison.py` | `KernelPerformanceComparison` | Kernel version comparison |
| `performance_audit.sh` | Shell Script | USE method performance audit |
| `mtu_check.sh` | Shell Script | MTU validation and sysctl.conf generator |

### Performance Audit Script (Brendan Gregg's USE Method)

Run a comprehensive performance audit based on the USE methodology:

```bash
# Quick audit
./linux-tools/performance_audit.sh --quick

# Full audit with extended sampling
./linux-tools/performance_audit.sh --full

# The script checks:
# - CPU: Utilization, run queue saturation, errors
# - Memory: Usage, swap activity, page faults, OOM events
# - Disk: I/O utilization, latency, queue depth, errors
# - Network: Interface stats, drops, connection states
```

### MTU Check Script

Validate jumbo frames support and generate optimized network configuration:

```bash
# Quick MTU check to a specific target
./linux-tools/mtu_check.sh 10.0.1.50

# Full analysis with throughput test
./linux-tools/mtu_check.sh 10.0.1.50 --full

# Scan entire subnet for jumbo frame support
./linux-tools/mtu_check.sh --scan 10.0.1.0/24

# Check local interface configuration
./linux-tools/mtu_check.sh --local

# Generate optimized sysctl.conf for different bandwidths
./linux-tools/mtu_check.sh --configure 10g   # 10 Gbps
./linux-tools/mtu_check.sh --configure 25g   # 25 Gbps
./linux-tools/mtu_check.sh --configure 50g   # 50 Gbps
./linux-tools/mtu_check.sh --configure 100g  # 100 Gbps
./linux-tools/mtu_check.sh --configure 200g  # 200 Gbps
```

The script generates both `/etc/sysctl.d/99-network-performance.conf` and `/etc/security/limits.d/99-network.conf` configurations.

### Available Linux Tools

The framework provides interfaces to:

- **perf**: CPU profiling and hardware counters
- **htop/top**: Process monitoring
- **iostat**: Disk I/O statistics
- **vmstat**: Virtual memory statistics
- **netstat/ss**: Network statistics
- **sar**: System activity reporter
- **tcpdump**: Network packet analysis

### Kernel Version Comparison

```python
from linux_tools import KernelPerformanceComparison

comparator = KernelPerformanceComparison()

# Compare two kernel versions
comparison = comparator.compare_kernels("5.15", "6.6")
print(f"Performance improvement: {comparison['performance_comparison']['improvement_percent']:.1f}%")

# Get recommended kernel for iGaming
recommendation = comparator.get_recommended_kernel_for_igaming()
print(f"Recommended kernel: {recommendation['recommended_version']}")
```

### Recommended Kernel Parameters for iGaming

```bash
# Network tuning
sysctl -w net.core.somaxconn=65535
sysctl -w net.ipv4.tcp_max_syn_backlog=65535
# Bounds orphaned FIN_WAIT_2 sockets (TIME_WAIT is fixed at 60s in the kernel); see Chapter 28a
sysctl -w net.ipv4.tcp_fin_timeout=5

# Memory tuning
sysctl -w vm.swappiness=10
sysctl -w vm.dirty_ratio=15

# File limits
sysctl -w fs.file-max=2097152
```

## Datadog Integration

The Datadog integration module provides comprehensive configuration for iGaming platform monitoring.

### Features

- **APM Tracing**: Distributed tracing for all services
- **Custom Metrics**: Gaming-specific metrics (bets, games, payments)
- **Dashboards**: Pre-configured dashboards for different teams
- **Alerts**: Multi-severity alert configuration
- **Log Pipelines**: Structured log processing

### Quick Start

```python
from datadog_integration import DatadogIGamingIntegration

integration = DatadogIGamingIntegration({
    "api_key": "your-api-key",
    "site": "datadoghq.com"
})

# Get agent configuration
agent_config = integration.get_agent_configuration()

# Get custom metrics definitions
metrics = integration.get_custom_metrics_definition()

# Get monitor definitions
monitors = integration.get_monitor_definitions()

# Get dashboard definition
dashboard = integration.get_dashboard_definition()
```

### Custom Gaming Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `igaming.bets.placed` | Counter | Number of bets placed |
| `igaming.bets.amount` | Distribution | Bet amount distribution |
| `igaming.games.active` | Gauge | Active game sessions |
| `igaming.games.launch_time` | Histogram | Game launch time |
| `igaming.deposits.count` | Counter | Number of deposits |
| `igaming.api.response_time` | Histogram | API response time |

## Requirements

- Python 3.9+
- Linux system (for linux-tools module)
- Datadog account (for datadog-integration)

## Related Documentation

- [Chapter 31: Performance Benchmarks and Metrics](../../19-performance-benchmarks-metrics.md)
- [Datadog Documentation](https://docs.datadoghq.com/)
- [Linux perf Wiki](https://perf.wiki.kernel.org/)

## Code Quality Verification

All code in this repository has been verified using industry-standard tools:

### Type Checking with ty (Astral)

All Python modules have been checked with [ty](https://github.com/astral-sh/ty) type checker:

| Directory | Files Checked | Status |
|-----------|---------------|--------|
| `performance-monitor/` | 7 modules | All passed |
| `infra-benchmarks/` | 4 modules | All passed |
| `linux-tools/` | 2 modules | All passed |
| `datadog-integration/` | 1 module | All passed |

### Shell Script Linting with ShellCheck

| Script | Tool | Status |
|--------|------|--------|
| `linux-tools/performance_audit.sh` | ShellCheck | Passed |
| `linux-tools/mtu_check.sh` | ShellCheck | Passed |

### Running Verification

```bash
# Type check Performance Monitor modules
for f in performance-monitor/*.py; do ty check "$f"; done

# Type check Infrastructure Benchmarks modules
for f in infra-benchmarks/*.py; do ty check "$f"; done

# Type check Linux Tools modules
for f in linux-tools/*.py; do ty check "$f"; done

# Type check Datadog Integration modules
for f in datadog-integration/*.py; do ty check "$f"; done

# Lint shell scripts
shellcheck linux-tools/performance_audit.sh
shellcheck linux-tools/mtu_check.sh
```

**Verification Date:** December 2025
**Tools Used:**
- ty 0.0.1-alpha.32 (Astral type checker)
- ShellCheck 0.10.0

## License

Apache 2.0 - See the main repository for details.
