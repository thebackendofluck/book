<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-05.jpg" alt="Volume 5" width="150" /></a>

# Chapter 21: Caching Strategies and Benefits

**📔 Part of Volume 5 — Infrastructure, Datacenter, and Deployment** · €49.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GYYG1HZ3) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 21 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

Enterprise caching infrastructure for iGaming platforms with high-performance requirements.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        iGaming Caching Architecture                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │   Browser    │    │     CDN      │    │   API GW     │                  │
│  │   Cache      │────│    Cache     │────│   Cache      │                  │
│  │  (L1 - 1s)   │    │  (L2 - 5m)   │    │  (L3 - 1m)   │                  │
│  └──────────────┘    └──────────────┘    └──────────────┘                  │
│         │                   │                   │                          │
│         └───────────────────┼───────────────────┘                          │
│                             │                                              │
│                  ┌──────────▼──────────┐                                   │
│                  │  Application Layer  │                                   │
│                  │   (Cache Manager)   │                                   │
│                  └──────────┬──────────┘                                   │
│                             │                                              │
│              ┌──────────────┼──────────────┐                              │
│              │              │              │                              │
│    ┌─────────▼─────────┐   │   ┌─────────▼─────────┐                     │
│    │ Redis Cluster     │   │   │    Memcached      │                     │
│    │ (Session, State)  │   │   │  (Static Content) │                     │
│    │                   │   │   │                   │                     │
│    │ ┌─────┐ ┌─────┐  │   │   │ ┌─────┐ ┌─────┐  │                     │
│    │ │Shard│ │Shard│  │   │   │ │Node │ │Node │  │                     │
│    │ │  1  │ │  2  │  │   │   │ │  1  │ │  2  │  │                     │
│    │ └──┬──┘ └──┬──┘  │   │   │ └─────┘ └─────┘  │                     │
│    │    │       │     │   │   │                   │                     │
│    │ ┌──▼──┐ ┌──▼──┐  │   │   └───────────────────┘                     │
│    │ │Rep 1│ │Rep 2│  │   │                                             │
│    │ └─────┘ └─────┘  │   │                                             │
│    └───────────────────┘   │                                             │
│                             │                                              │
│              ┌──────────────▼──────────────┐                              │
│              │       PostgreSQL/RDS         │                              │
│              │    (Source of Truth)         │                              │
│              └──────────────────────────────┘                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
scripts/chapter-21/
├── cache-patterns/              # Python modules (5 files, ~2,100 lines)
│   ├── __init__.py             # Module exports
│   ├── cache_patterns.py       # Core patterns (cache-aside, write-through)
│   ├── cache_safety.py         # Stampede prevention, distributed locking
│   ├── cache_warmer.py         # Startup and scheduled warming
│   ├── cache_calculator.py     # Sizing and ROI calculators
│   └── cache_monitor.py        # Performance monitoring and alerts
├── terraform/
│   ├── aws/main.tf             # ElastiCache Redis Cluster (~700 lines)
│   └── kubernetes/main.tf      # Redis StatefulSet + Services (~850 lines)
├── docker/
│   ├── docker-compose.yml      # Local development stack
│   ├── setup.sh                # Configuration generator
│   └── config/                 # Generated configs (Redis, Prometheus, Grafana)
├── monitoring/
│   └── alerting-rules.yml      # Prometheus alerting rules
└── README.md                   # This file
```

## Quick Start

### Local Development (Docker Compose)

```bash
# Generate configuration files
cd scripts/chapter-21/docker
./setup.sh

# Start the stack
docker-compose up -d

# Verify Redis is running
docker-compose exec redis redis-cli -a "${REDIS_PASSWORD}" ping

# Access UIs
# Redis Commander: http://localhost:8081 (admin / see the generated .env)
# Grafana: http://localhost:3000 (admin / see the generated .env)
# Prometheus: http://localhost:9090
```

### AWS Deployment (Terraform)

```bash
cd scripts/chapter-21/terraform/aws

# Initialize Terraform
terraform init

# Plan deployment
terraform plan -var="environment=prod"

# Apply
terraform apply -var="environment=prod"

# Outputs
terraform output redis_cluster_endpoint
terraform output redis_auth_secret_arn
```

### Kubernetes Deployment

```bash
cd scripts/chapter-21/terraform/kubernetes

# Apply with Terraform
terraform init
terraform apply -var="namespace=cache-system"

# Or use kubectl
kubectl apply -f kubernetes/
```

## Python Module Usage

### Cache-Aside Pattern (Lazy Loading)

```python
from cache_patterns import CacheManager, CacheConfig

# Initialize
config = CacheConfig(default_ttl=300, namespace="igaming")
cache = CacheManager(redis_client, config)

# Get player balance (cache-aside)
result = await cache.get_player_balance("player123")
if result.hit:
    balance = result.data
else:
    # Fetch from database and cache
    balance = await db.get_balance("player123")
    await cache.set_player_balance("player123", balance)
```

### Stampede Prevention

```python
from cache_patterns import StampedeSafeCache

# Initialize with distributed locking
safe_cache = StampedeSafeCache(redis_client)

# Only one request populates cache
data = await safe_cache.get(
    key="expensive_query",
    fetch_func=lambda: db.expensive_query(),
    ttl=300
)
```

### Cache Warming

```python
from cache_patterns import CacheWarmer

# Initialize
warmer = CacheWarmer(redis_client, db_client)

# Warm player caches on startup
result = await warmer.warm_player_caches()
print(f"Warmed {result.keys_warmed} keys in {result.duration_ms}ms")

# Run scheduled warming
await warmer.run_scheduled_warming(interval_seconds=300)
```

### Cache Sizing Calculator

```python
from cache_patterns import CacheSizingCalculator

calculator = CacheSizingCalculator()

# Calculate requirements for 1M DAU
result = calculator.calculate_requirements(
    daily_active_users=1_000_000,
    data_per_user_kb=50,
    cache_hit_ratio=0.85
)

print(f"Recommended memory: {result.recommended_memory_gb:.1f} GB")
print(f"Instance type: {result.instance_recommendation}")
print(f"Monthly cost: ${result.monthly_cost_estimate:.2f}")
```

## Performance Characteristics

| Metric | Target | Typical Value |
|--------|--------|---------------|
| Cache Hit Rate | >85% | 90-95% |
| Get Latency (P50) | <1ms | 0.3-0.5ms |
| Get Latency (P99) | <5ms | 1-2ms |
| Throughput | >100K ops/s | 150-200K ops/s |
| Memory Efficiency | >70% | 75-85% |

## Caching Solutions Comparison

| Solution | Use Case | Latency | Throughput | Consistency |
|----------|----------|---------|------------|-------------|
| **Redis** | Sessions, State | <1ms | 100K+ ops/s | Strong |
| **Memcached** | Static Content | <0.5ms | 500K+ ops/s | Eventual |
| **Valkey** | Redis Alternative | <1ms | 100K+ ops/s | Strong |
| **Dragonfly** | High Concurrency | <1ms | 1M+ ops/s | Strong |
| **Garnet** | .NET Platforms | <1ms | 100K+ ops/s | Strong |

## AWS Infrastructure

### ElastiCache Redis Cluster

| Component | Configuration | Cost/Month |
|-----------|---------------|------------|
| Redis Cluster | 3 shards, 2 replicas each | ~$800 |
| Node Type | cache.r6g.large (13GB) | - |
| Memcached | 3 nodes (optional) | ~$400 |
| NAT Gateway | Single AZ | ~$45 |
| KMS Key | For encryption | ~$1 |
| **Total** | | **~$1,200-1,800** |

### Security Features

- Encryption at rest (KMS)
- Encryption in transit (TLS)
- AUTH token authentication
- VPC-only access (private subnets)
- Security groups with minimal ingress

### Monitoring

- CloudWatch alarms for CPU, memory, evictions
- Cache hit rate monitoring
- Custom dashboard included

## Kubernetes Configuration

### Redis StatefulSet

- 1 master + 2 replicas
- Persistent volumes (20GB gp3)
- Pod anti-affinity for HA
- Resource limits (2 CPU, 4GB memory)
- Redis Exporter for Prometheus

### Security

- NetworkPolicies (default deny)
- Non-root containers (UID 1000)
- Read-only root filesystem
- No privilege escalation

## Alert Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| Hit Rate | <80% | <60% |
| Latency | >5ms | >10ms |
| Memory | >80% | >90% |
| Evictions | >100/s | >500/s |
| Connections | >80% | >95% |

## Cache Anti-Patterns

### DO NOT Cache

1. **Financial Transactions** - Risk of double-spending
2. **Real-time Multiplayer State** - Race conditions enable cheating
3. **Regulatory Data** - Audit trail breaks
4. **Rapidly Changing Data** - High invalidation overhead

### Common Mistakes

1. **Cache Stampede** - Use distributed locking
2. **Inconsistent TTLs** - Standardize per data type
3. **No Warming** - Pre-populate on startup
4. **Missing Monitoring** - Always track hit rates

## On-Premise Deployment

For on-premise deployments using Docker:

```bash
# Start Redis cluster with Docker Compose
cd docker
./setup.sh
docker-compose up -d

# Scale replicas
docker-compose up -d --scale redis-replica=3

# Monitor
docker-compose logs -f redis
```

## Troubleshooting

### Low Hit Rate

```bash
# Check key distribution
redis-cli INFO keyspace

# Check memory usage
redis-cli INFO memory

# Check slow queries
redis-cli SLOWLOG GET 10
```

### High Latency

```bash
# Check connections
redis-cli CLIENT LIST | wc -l

# Check memory fragmentation
redis-cli INFO memory | grep fragmentation

# Check network
redis-cli --latency-history
```

### Memory Issues

```bash
# Check evictions
redis-cli INFO stats | grep evicted

# Check maxmemory policy
redis-cli CONFIG GET maxmemory-policy

# Analyze keys
redis-cli --bigkeys
```

## References

- [Redis Documentation](https://redis.io/docs)
- [AWS ElastiCache Best Practices](https://docs.aws.amazon.com/AmazonElastiCache/latest/red-ug/best-practices.html)
- [Caching Patterns](https://docs.microsoft.com/en-us/azure/architecture/patterns/cache-aside)
- [Brendan Gregg - Systems Performance](https://www.brendangregg.com/systems-performance-2nd-edition-book.html)

## Validation

All scripts have been validated:

```bash
# Python type checking
ty check cache_patterns/*.py  # All checks passed!

# Terraform formatting
terraform fmt terraform/aws/main.tf      # Formatted
terraform fmt terraform/kubernetes/main.tf  # Formatted

# Security scanning
checkov -f terraform/aws/main.tf  # 38 passed, 7 skipped (by design)
```
