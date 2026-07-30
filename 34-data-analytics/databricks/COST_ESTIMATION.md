# Databricks Cost Estimation for iGaming Data Lake

This document provides detailed cost estimates for running Databricks on AWS for an iGaming data platform.

## Pricing Model Overview

Databricks charges for:
1. **DBU (Databricks Units)** - Compute processing units
2. **AWS Infrastructure** - EC2, S3, data transfer
3. **Premium Features** - Unity Catalog, SQL Serverless

## DBU Pricing by Tier (2024)

| Workload Type | STANDARD | PREMIUM | ENTERPRISE |
|---------------|----------|---------|------------|
| Jobs Compute | $0.07/DBU | $0.10/DBU | $0.14/DBU |
| Jobs Compute Light | $0.07/DBU | $0.07/DBU | $0.10/DBU |
| All-Purpose Compute | $0.40/DBU | $0.55/DBU | $0.65/DBU |
| Delta Live Tables (Core) | $0.20/DBU | $0.25/DBU | $0.30/DBU |
| Delta Live Tables (Pro) | N/A | $0.36/DBU | $0.38/DBU |
| SQL Compute | N/A | $0.22/DBU | $0.22/DBU |
| SQL Serverless | N/A | $0.70/DBU | $0.70/DBU |
| Model Serving | N/A | $0.07/DBU | $0.07/DBU |

## DBU Consumption by Instance Type

| Instance Type | vCPU | Memory | DBU/Hour | Best For |
|---------------|------|--------|----------|----------|
| m5.large | 2 | 8 GB | 0.75 | Small jobs |
| m5.xlarge | 4 | 16 GB | 1.5 | Driver nodes |
| m5.2xlarge | 8 | 32 GB | 3.0 | ETL workers |
| m5.4xlarge | 16 | 64 GB | 6.0 | Large aggregations |
| r5.xlarge | 4 | 32 GB | 1.5 | Memory-heavy |
| r5.2xlarge | 8 | 64 GB | 3.0 | Large datasets |
| i3.xlarge | 4 | 30.5 GB | 2.0 | Storage optimized |
| i3.2xlarge | 8 | 61 GB | 4.0 | Delta caching |
| g4dn.xlarge | 4 | 16 GB | 2.5 | ML/GPU |
| g4dn.2xlarge | 8 | 32 GB | 5.0 | Deep learning |

---

## iGaming Workload Scenarios

### Scenario 1: Small Platform (Startup)
- 100K daily active players
- 1M daily transactions
- 5 GB daily data ingestion

### Scenario 2: Medium Platform
- 500K daily active players
- 10M daily transactions
- 50 GB daily data ingestion

### Scenario 3: Large Platform (Enterprise)
- 2M+ daily active players
- 100M+ daily transactions
- 500 GB daily data ingestion

---

## Detailed Cost Breakdown: Medium Platform (PREMIUM Tier)

### ETL Processing (Daily Pipeline)

| Component | Configuration | Hours/Day | DBU/Hour | Daily DBU | Monthly DBU |
|-----------|--------------|-----------|----------|-----------|-------------|
| Bronze→Silver Driver | m5.xlarge | 4 | 1.5 | 6 | 180 |
| Bronze→Silver Workers | 4x m5.2xlarge | 4 | 12 | 48 | 1,440 |
| Silver→Gold Driver | m5.xlarge | 3 | 1.5 | 4.5 | 135 |
| Silver→Gold Workers | 4x m5.2xlarge | 3 | 12 | 36 | 1,080 |
| Maintenance (OPTIMIZE) | 2x m5.large | 1 | 1.5 | 1.5 | 45 |
| **Total ETL** | | | | **96** | **2,880** |

**ETL Cost (Jobs Compute @ $0.10/DBU):** 2,880 × $0.10 = **$288/month**

### SQL Analytics (Business Intelligence)

| Component | Size | Hours/Day | DBU/Hour | Daily DBU | Monthly DBU |
|-----------|------|-----------|----------|-----------|-------------|
| SQL Warehouse (Medium) | 4-8 nodes | 10 | 12 | 120 | 2,640 |
| Ad-hoc Queries (Small) | 2 nodes | 4 | 4 | 16 | 352 |
| **Total SQL** | | | | **136** | **2,992** |

**SQL Cost (@ $0.22/DBU):** 2,992 × $0.22 = **$658/month**

### ML/Analytics (Weekly)

| Component | Configuration | Hours/Week | DBU/Hour | Weekly DBU | Monthly DBU |
|-----------|--------------|------------|----------|------------|-------------|
| Feature Engineering | 4x r5.2xlarge | 8 | 12 | 96 | 384 |
| Model Training | 2x g4dn.xlarge | 4 | 5 | 20 | 80 |
| **Total ML** | | | | **116** | **464** |

**ML Cost (All-Purpose @ $0.55/DBU):** 464 × $0.55 = **$255/month**

### Infrastructure Costs (AWS)

| Resource | Specification | Monthly Cost |
|----------|--------------|--------------|
| EC2 (Spot instances, avg) | ~500 hours m5.2xlarge equiv | $300 |
| S3 Storage (Bronze) | 1.5 TB × $0.023 | $35 |
| S3 Storage (Silver) | 500 GB × $0.023 | $12 |
| S3 Storage (Gold) | 200 GB × $0.023 | $5 |
| S3 Storage (DBFS/Unity) | 100 GB × $0.023 | $3 |
| S3 Requests | ~100M requests | $50 |
| Data Transfer | 500 GB inter-AZ | $10 |
| KMS | Key usage | $10 |
| **Total AWS** | | **$425/month** |

### Total Monthly Cost (Medium Platform)

| Category | Monthly Cost |
|----------|--------------|
| ETL Processing (Jobs) | $288 |
| SQL Analytics | $658 |
| ML/Analytics | $255 |
| AWS Infrastructure | $425 |
| **TOTAL** | **$1,626/month** |
| **Annual Estimate** | **$19,512/year** |

---

## Cost Comparison: Databricks vs AWS Glue

### ETL Processing (Same Workload)

| Metric | AWS Glue | Databricks | Difference |
|--------|----------|------------|------------|
| Pricing Unit | DPU ($0.44/hour) | DBU ($0.10/hour) | - |
| Minimum | 2 DPU | 1 DBU | - |
| Bronze→Silver (4 hrs) | 10 DPU × 4 × $0.44 = $17.60 | 96 DBU × $0.10 = $9.60 | -45% |
| Silver→Gold (3 hrs) | 10 DPU × 3 × $0.44 = $13.20 | 72 DBU × $0.10 = $7.20 | -45% |
| Monthly ETL | ~$930 | ~$500 | -46% |

### Features Comparison

| Feature | AWS Glue | Databricks | Winner |
|---------|----------|------------|--------|
| Delta Lake | Limited | Native | Databricks |
| Streaming | Structured Streaming | Structured + DLT | Databricks |
| SQL Analytics | Athena ($5/TB scanned) | SQL Warehouses | Tie |
| Governance | Lake Formation | Unity Catalog | Databricks |
| ML/AI | SageMaker (separate) | MLflow, AutoML | Databricks |
| Notebooks | Glue Studio | Full IDE | Databricks |
| Version Control | Limited | Git integration | Databricks |
| Collaboration | Basic | Full workspace | Databricks |
| Photon Engine | No | Yes (3x faster) | Databricks |

### When to Choose Each

**Choose AWS Glue When:**
- Simple ETL with few transformations
- Already invested in AWS-native stack
- Budget-constrained (<$500/month)
- No need for interactive analytics
- Team familiar with Glue/Spark

**Choose Databricks When:**
- Complex ETL with Delta Lake
- Need interactive SQL analytics
- ML/AI workloads
- Team collaboration required
- Performance critical (Photon)
- Multi-cloud strategy planned

---

## Cost Optimization Strategies

### 1. Use Spot Instances (50-70% savings)
```json
"aws_attributes": {
  "availability": "SPOT_WITH_FALLBACK",
  "spot_bid_price_percent": 100
}
```

### 2. Auto-termination (Prevent idle costs)
```python
# Cluster auto-terminates after 30 minutes of inactivity
autotermination_minutes = 30
```

### 3. Jobs Compute vs All-Purpose
| Workload Type | Recommendation | Savings |
|---------------|----------------|---------|
| Scheduled ETL | Jobs Compute | 82% vs All-Purpose |
| Ad-hoc analysis | All-Purpose | - |
| Development | All-Purpose | - |
| Production ML | Jobs Compute | 82% |

### 4. Instance Pools (Faster startup, better spot availability)
```python
# Pre-warm instances for faster cluster startup
instance_pool {
  min_idle_instances = 2
  max_capacity = 20
  idle_instance_autotermination_minutes = 30
}
```

### 5. Photon Engine (30-50% faster = fewer DBUs)
```json
"runtime_engine": "PHOTON"
```

### 6. Cluster Sizing Best Practices

| Data Size | Recommended Workers | Node Type |
|-----------|--------------------:|-----------|
| < 10 GB | 2-4 | m5.large |
| 10-100 GB | 4-8 | m5.xlarge |
| 100-500 GB | 8-16 | m5.2xlarge |
| 500 GB - 1 TB | 16-32 | m5.4xlarge |
| > 1 TB | 32+ | i3.2xlarge |

### 7. SQL Serverless vs Provisioned

| Pattern | Serverless | Provisioned |
|---------|------------|-------------|
| Unpredictable queries | Better | - |
| Consistent load | - | Better |
| Cost (light usage) | Lower | Higher |
| Cost (heavy usage) | Higher | Lower |
| Startup time | Instant | 2-5 min |

---

## Monthly Cost Summary by Platform Size

| Platform Size | ETL | SQL | ML | AWS | **Total/Month** | **Total/Year** |
|---------------|-----|-----|----|----|----------------|----------------|
| **Startup** | $150 | $200 | $50 | $150 | **$550** | **$6,600** |
| **Medium** | $288 | $658 | $255 | $425 | **$1,626** | **$19,512** |
| **Enterprise** | $1,200 | $3,000 | $1,500 | $2,000 | **$7,700** | **$92,400** |

---

## Reserved Capacity Discounts

Databricks offers committed use discounts:

| Commitment | Discount |
|------------|----------|
| 1-year commit | 20-30% |
| 3-year commit | 40-50% |

**Example (Medium Platform with 1-year commit):**
- List price: $19,512/year
- With 25% discount: **$14,634/year**
- Savings: $4,878/year

---

## ROI Analysis

### Break-Even Analysis: Databricks vs Manual Spark on EC2

| Factor | Manual Spark | Databricks |
|--------|--------------|------------|
| Infrastructure Cost | $800/month | Included |
| DevOps/Admin (0.5 FTE) | $5,000/month | $0 |
| Development Speed | 1x | 3x faster |
| Data Engineer Productivity | Baseline | +40% |
| Time to Production | 3-6 months | 2-4 weeks |
| **Total Cost of Ownership** | ~$7,000/month | ~$1,600/month |

### Value Drivers

1. **Faster Time-to-Insight**: 3x faster development
2. **Better Data Quality**: Delta Lake ACID transactions
3. **Reduced Operations**: Managed service
4. **Improved Collaboration**: Shared workspaces
5. **Future-Proof**: Multi-cloud capability

---

## Appendix: Pricing Calculator Links

- [Databricks Pricing](https://www.databricks.com/product/pricing)
- [AWS EC2 Pricing](https://aws.amazon.com/ec2/pricing/)
- [AWS S3 Pricing](https://aws.amazon.com/s3/pricing/)

*Last updated: December 2024*
*Prices subject to change. Verify current pricing with vendors.*
