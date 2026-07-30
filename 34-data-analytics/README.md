<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 34: Data and Analytics

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 34 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

This directory contains comprehensive implementations for building an enterprise data lake on AWS for iGaming platforms, including Terraform infrastructure, ETL pipelines, on-premise connectors, and data governance.

## Directory Structure

```
scripts/chapter-34/
├── README.md
├── terraform/                      # AWS Infrastructure as Code
│   ├── main.tf                    # Core infrastructure (S3, Kinesis, Glue, KMS)
│   ├── modules/
│   │   ├── networking.tf          # VPC, VPN Gateway, security groups
│   │   └── databricks.tf          # Databricks workspace and IAM
│   └── environments/
│       ├── dev/
│       ├── staging/
│       └── prod/
├── etl/                           # AWS Glue ETL Pipelines
│   ├── glue-jobs/
│   │   ├── bronze_to_silver.py   # Data cleaning and validation
│   │   └── silver_to_gold.py     # Aggregations and analytics tables
│   ├── lambda/
│   │   └── firehose_transformer/ # Real-time transformation
│   └── extractors/
├── databricks/                    # Databricks ETL (Alternative to Glue)
│   ├── notebooks/
│   │   ├── bronze_to_silver_delta.py  # Delta Lake ETL
│   │   └── silver_to_gold_delta.py    # Gold aggregations
│   ├── jobs/
│   │   └── etl_workflow.json     # Databricks workflow definition
│   └── COST_ESTIMATION.md        # Detailed cost analysis
├── connectors/                    # Data Source Connectors
│   ├── on-premise/
│   │   └── database_extractor.py # PostgreSQL, MySQL, SQL Server extraction
│   ├── databases/
│   └── streaming/
├── governance/                    # Data Governance & Privacy
│   ├── retention_policies.py     # Retention management and compliance
│   ├── data_catalog.py           # Metadata management and classification
│   ├── anonymization.py          # K-anonymity, pseudonymization, masking
│   ├── privacy_operations.py     # GDPR rights (access, erasure, portability)
│   └── data_lineage.py           # Lineage tracking and impact analysis
├── data-platform/                 # Core Platform Classes
│   ├── __init__.py
│   ├── data_platform.py          # EnterpriseDataPlatform
│   ├── player_analytics.py       # Player ML features and segmentation
│   └── business_intelligence.py  # BI metrics and dashboards
├── sizing-tools/                  # Capacity Planning
│   ├── __init__.py
│   └── database_sizer.py         # Database sizing calculator
├── analytics-pipeline/            # Real-time Streaming
│   ├── __init__.py
│   └── realtime_analytics.py     # Stream processors
└── affiliate-stats/               # Kafka Streams Aggregation (Scala)
    ├── README.md                 # Architecture and pattern overview
    ├── HourlyStatsStream.scala   # Tumbling-window aggregation topology
    ├── HourlyStatsConsumer.scala # Consumer -> PostgreSQL persistence
    ├── EventStream.scala         # Base stream lifecycle management
    ├── ConsumedMessagesDAO.scala  # Slick DAO for hourly stats
    ├── schema.sql                # PostgreSQL schema + evolutions
    └── HourlyStatsStreamSpec.scala # TopologyTestDriver unit tests
├── daily-stats/                   # Batch Analytics Pipeline (Scala)
│   ├── StatsStep.scala           # Trait hierarchy: step, table-based, date-based
│   ├── DailyPlayerStatsStep.scala # 40+ metric aggregation from transaction log
│   ├── DailyPlayerRevenueStep.scala # GGR, cash hold, gaming duty, payment risk
│   ├── Run.scala                 # Pipeline orchestrator with step selection
│   └── StepChooser.scala         # Flexible step selection (range, list, single)
```

## Quick Start

### 1. Deploy Infrastructure

```bash
cd terraform/environments/prod
terraform init
terraform plan -var-file="prod.tfvars" -out=tfplan
terraform apply tfplan
```

### 2. Configure On-Premise Extraction

```bash
# Set environment variables
export DB_USERNAME="your_username"
export DB_PASSWORD="your_password"
export KMS_KEY_ID="arn:aws:kms:us-east-1:123456789:key/xxx"

# Run extraction
python connectors/on-premise/database_extractor.py
```

### 3. Deploy ETL Jobs

```bash
# Upload Glue jobs to S3
aws s3 cp etl/glue-jobs/bronze_to_silver.py s3://your-bucket/glue-scripts/
aws s3 cp etl/glue-jobs/silver_to_gold.py s3://your-bucket/glue-scripts/

# Create Glue jobs via AWS Console or Terraform
```

---

## Component Details

### Terraform Infrastructure (`terraform/`)

Creates complete AWS Data Lake infrastructure:

| Resource | Purpose |
|----------|---------|
| S3 Buckets | Bronze/Silver/Gold data layers with lifecycle policies |
| KMS Key | Encryption for all data at rest |
| Kinesis Streams | Real-time event ingestion |
| Kinesis Firehose | Auto-delivery to S3 with Parquet conversion |
| Glue Databases | Data catalog for Bronze/Silver/Gold |
| Glue Crawlers | Automatic schema discovery |
| Lake Formation | Data governance and permissions |
| VPC + VPN Gateway | Secure on-premise connectivity |
| IAM Roles | Least-privilege access control |

**Key Variables:**

```hcl
variable "data_retention_days" {
  default = {
    bronze = 90      # Raw data: 90 days
    silver = 365     # Cleaned: 1 year
    gold   = 2555    # Analytics: 7 years (regulatory)
  }
}
```

#### Security Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `enable_deletion_protection` | `true` | Enables lifecycle prevent_destroy on critical resources |
| `enable_object_lock` | `true` | Enables S3 Object Lock for compliance data (Gold layer) |
| `object_lock_retention_days` | `2555` | Object lock retention (7 years for iGaming) |
| `enable_mfa_delete` | `false` | MFA delete for S3 versioning (requires root) |
| `backup_retention_days` | `35` | AWS Backup retention period |

**Security Features Implemented:**

| Feature | Implementation | Purpose |
|---------|----------------|---------|
| **Deletion Protection** | `lifecycle { prevent_destroy = true }` | Prevents accidental terraform destroy |
| **Force Destroy** | `force_destroy = false` | Prevents bucket deletion with objects |
| **Object Lock** | GOVERNANCE mode, 7 years | WORM compliance for regulatory data |
| **KMS Encryption** | SSE-KMS for all buckets | Data at rest encryption |
| **Key Rotation** | Enabled | Annual key rotation |
| **Versioning** | Enabled on all buckets | Object recovery |
| **Access Logging** | Enabled | Audit trail |
| **Public Access Block** | All blocked | Prevent public exposure |
| **AWS Backup** | Daily + Weekly | Disaster recovery |

**Critical Warnings:**

```text
⚠️  DO NOT set force_destroy = true on production buckets
⚠️  DO NOT remove lifecycle { prevent_destroy = true } without approval
⚠️  DO NOT delete KMS keys - causes PERMANENT data loss
⚠️  DO NOT disable Object Lock once enabled (irreversible)
```

**Recovery Procedures:**

| Scenario | Solution |
|----------|----------|
| Accidental object deletion | Use S3 versioning to restore previous version |
| KMS key scheduled for deletion | Cancel within 30-day window via AWS CLI |
| Full bucket recovery | Use AWS Backup restore job |
| terraform destroy blocked | Remove `prevent_destroy` only after data migration |

### ETL Pipeline (`etl/`)

#### Bronze to Silver (`bronze_to_silver.py`)

Transforms raw data to cleaned, validated format:

- **Schema Validation**: Enforce data types
- **Deduplication**: Remove duplicates by primary key
- **Timestamp Normalization**: Convert to UTC
- **PII Masking**: Mask email, phone, IP addresses
- **Range Validation**: Filter invalid values
- **Partitioning**: Add year/month/day columns

#### Silver to Gold (`silver_to_gold.py`)

Creates analytics-ready aggregations:

| Output Table | Description |
|--------------|-------------|
| `player_daily_summary` | Daily metrics per player |
| `game_performance` | Game RTP, popularity |
| `revenue_daily` | Revenue by date/jurisdiction |
| `cohort_analysis` | Retention by registration cohort |
| `risk_scoring` | ML features for risk detection |

### Databricks ETL (`databricks/`)

Alternative to AWS Glue using Databricks with Delta Lake for enterprise-grade data processing.

#### Features

| Feature | AWS Glue | Databricks |
|---------|----------|------------|
| Delta Lake | Limited | Native |
| ACID Transactions | No | Yes |
| Time Travel | No | Yes |
| Schema Evolution | Manual | Automatic |
| MERGE (CDC) | Complex | Simple |
| Governance | Lake Formation | Unity Catalog |
| SQL Analytics | Athena | SQL Warehouses |
| ML/AI | SageMaker | MLflow, AutoML |

#### Notebooks

**`bronze_to_silver_delta.py`** - Delta Lake ETL:
- Schema enforcement with StructType
- MERGE for upserts (CDC pattern)
- PII masking functions
- Data quality validation
- Automatic Z-ORDER optimization

**`silver_to_gold_delta.py`** - Gold Aggregations:
- Player daily metrics
- Game performance analytics
- Revenue summaries
- Player lifetime value (LTV)
- Risk indicators for ML

#### Deployment

```bash
# Deploy Databricks infrastructure
cd terraform
terraform apply -var="enable_databricks=true"

# Import workflow to Databricks
databricks jobs create --json @databricks/jobs/etl_workflow.json
```

#### Cost Comparison (Medium Platform)

| Category | AWS Glue | Databricks | Savings |
|----------|----------|------------|---------|
| ETL Processing | $930/mo | $500/mo | 46% |
| SQL Analytics | $500/mo | $658/mo | -32% |
| Total (with ML) | $1,800/mo | $1,626/mo | 10% |

**When to Choose Databricks:**
- Complex ETL with Delta Lake ACID
- Interactive SQL analytics needed
- ML/AI workloads
- Team collaboration
- Multi-cloud strategy

See `databricks/COST_ESTIMATION.md` for detailed pricing.

### On-Premise Connector (`connectors/on-premise/`)

Extracts data from on-premise databases:

**Supported Databases:**
- PostgreSQL (asyncpg)
- MySQL (aiomysql)
- SQL Server (pyodbc)
- Oracle (oracledb)

**Features:**
- Incremental extraction (CDC-style)
- Parallel table extraction
- GZIP compression
- SSE-KMS encryption
- Checksum validation
- Retry logic

```python
from database_extractor import DatabaseExtractor, DatabaseConfig, S3Config

config = ExtractionConfig(
    database=DatabaseConfig(
        db_type=DatabaseType.POSTGRESQL,
        host="on-premise-db.internal",
        port=5432,
        database="casino",
        ssl_enabled=True,
    ),
    s3=S3Config(
        bucket="igaming-datalake-bronze",
        prefix="on-premise",
        kms_key_id="arn:aws:kms:...",
    ),
    batch_size=100000,
    max_parallel_tables=4,
)

extractor = DatabaseExtractor(config)
await extractor.connect()
results = await extractor.extract_tables(tables)
```

### Data Governance (`governance/`)

Comprehensive data governance including retention, metadata, anonymization, privacy, and lineage.

#### Retention Policies (`retention_policies.py`)

**iGaming Retention Requirements:**

| Policy | Retention | Regulatory Reference |
|--------|-----------|---------------------|
| transactions | 7 years | UK GC LCCP 15.2.1, MGA |
| player_profiles | 5 years after closure | GDPR Art. 17, AML 5 |
| game_logs | 5 years | UK GC LCCP |
| audit_logs | 7 years | SOX, PCI-DSS |

**Lifecycle Management:**
- Day 0-30: S3 Standard ($0.023/GB)
- Day 30-90: S3 Standard-IA ($0.0125/GB)
- Day 90-365: Glacier IR ($0.004/GB)
- Day 365+: Deep Archive ($0.00099/GB)

#### Data Catalog (`data_catalog.py`)

**Features:**
- Dataset registration and discovery
- Schema versioning
- Data classification (PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED)
- PII detection and tagging
- Business glossary management
- AWS Glue Catalog integration

```python
from data_catalog import DataCatalog, DatasetMetadata, ColumnMetadata

catalog = DataCatalog(table_name="data-catalog")

# Register dataset with classification
dataset = DatasetMetadata(
    name="player_profiles",
    columns=[
        ColumnMetadata(name="email", contains_pii=True),
        ColumnMetadata(name="balance", classification=DataClassification.CONFIDENTIAL),
    ],
)

await catalog.register_dataset(dataset)
pii_inventory = await catalog.get_pii_inventory()
```

#### Anonymization (`anonymization.py`)

**Techniques Implemented:**
- **Suppression**: Remove sensitive values
- **Generalization**: Replace with broader categories
- **Masking**: Partial value hiding (email → j***@e***.com)
- **Pseudonymization**: Reversible replacement with key
- **Tokenization**: Token vault replacement
- **Hashing**: One-way SHA256 with salt
- **Encryption**: AES-256 (Fernet)
- **Noise Addition**: Differential privacy (Laplace)
- **Bucketing**: Range binning

```python
from anonymization import DataAnonymizer, get_igaming_player_policy

anonymizer = DataAnonymizer()
policy = get_igaming_player_policy()

df_anon, result = anonymizer.anonymize_dataset(df, policy)

print(f"K-anonymity achieved: {result.k_anonymity_achieved}")
print(f"Records anonymized: {result.records_affected}")
```

#### Privacy Operations (`privacy_operations.py`)

**GDPR Data Subject Rights:**

| Right | Article | Implementation |
|-------|---------|----------------|
| Access | Art. 15 | Export all data to JSON |
| Rectification | Art. 16 | Update across datasets |
| Erasure | Art. 17 | Delete or anonymize |
| Portability | Art. 20 | Machine-readable export |
| Restriction | Art. 18 | Mark as restricted |

```python
from privacy_operations import PrivacyOperations, PrivacyRequest

privacy_ops = PrivacyOperations(
    data_lake_bucket="igaming-datalake",
    audit_bucket="igaming-audit",
)

# Process Subject Access Request
request = PrivacyRequest(
    subject_id="PLAYER_12345",
    request_type=PrivacyRequestType.ACCESS,
)

result = await privacy_ops.process_request(request)
print(f"Export location: {result.export_location}")
```

#### Data Lineage (`data_lineage.py`)

**Features:**
- Dataset-level lineage tracking
- Column-level lineage
- Impact analysis (downstream effects)
- Root cause analysis (upstream sources)
- NetworkX graph integration

```python
from data_lineage import LineageTracker

tracker = LineageTracker(table_name="data-lineage")

# Record transformation
await tracker.record_transformation(
    job_id="glue-job-123",
    job_name="bronze_to_silver",
    source_nodes=["bronze_transactions"],
    target_node="silver_transactions",
    transformation_type=TransformationType.AGGREGATION,
)

# Analyze impact
impact = await tracker.analyze_impact("silver_transactions")
print(f"Impacted datasets: {impact.impacted_datasets}")
```

### Data Platform (`data-platform/`)

Core platform for real-time analytics:

```python
from data_platform import EnterpriseDataPlatform, DataPipelineConfig

config = DataPipelineConfig(
    kafka_brokers=["localhost:9092"],
    clickhouse_host="localhost",
    postgres_url="postgresql://localhost/casino",
    redis_url="redis://localhost:6379",
)

platform = EnterpriseDataPlatform(config)
await platform.initialize()
await platform.ingest_event(event)
analytics = await platform.get_player_analytics("player_123")
```

### Player Analytics (`data-platform/player_analytics.py`)

ML feature engineering and segmentation:

```python
from data_platform import PlayerAnalytics

analytics = PlayerAnalytics(clickhouse_client)
features = analytics.create_ml_features("player_123", days=30)
segment = analytics.classify_player_value(features)
insights = analytics.get_player_insights("player_123")
```

### Database Sizing (`sizing-tools/`)

Capacity planning calculator:

```python
from sizing_tools import DatabasePerformanceSizer, DatabaseSizingRequirements

sizer = DatabasePerformanceSizer()

requirements = DatabaseSizingRequirements(
    concurrent_users=100000,
    peak_daily_transactions=50_000_000,
    data_retention_days=2555,
    read_write_ratio=10,
    high_availability=True,
)

recommendation = sizer.calculate_sizing(requirements)
sizer.print_sizing_report(requirements, recommendation)
```

---

## Cost Estimation

### Monthly Infrastructure Costs

| Component | Small (10TB) | Medium (50TB) | Large (100TB) |
|-----------|-------------|---------------|---------------|
| S3 Storage | $395 | $1,975 | $3,950 |
| Kinesis | $800 | $3,200 | $8,000 |
| AWS Glue | $900 | $3,600 | $9,000 |
| Networking | $281 | $926 | $2,252 |
| Other (KMS, CW) | $60 | $180 | $500 |
| **Total Monthly** | **$2,436** | **$9,881** | **$22,702** |

### 7-Year TCO (50TB initial, 3% monthly growth)

```
Without lifecycle: ~$2.1M
With lifecycle:    ~$1.5M (30% savings)
```

---

## Installation

### Dependencies

```bash
# Core dependencies
pip install boto3 pandas pyarrow asyncpg aiomysql

# For Glue jobs (installed in Glue environment)
# pyspark, awsglue

# Optional for local testing
pip install moto pytest pytest-asyncio
```

### Using uv

```bash
uv pip install boto3 pandas pyarrow asyncpg aiomysql
```

---

## Verification

All Python scripts verified with ty type checker:

```bash
cd scripts/chapter-34

# Check all modules
ty check governance/retention_policies.py
ty check connectors/on-premise/database_extractor.py
ty check data-platform/data_platform.py
ty check data-platform/player_analytics.py
ty check data-platform/business_intelligence.py
ty check sizing-tools/database_sizer.py
ty check analytics-pipeline/realtime_analytics.py
```

**Verification Date:** December 2024

Note: ETL Glue jobs require PySpark/AWS Glue environment for execution.

---

## Architecture Diagrams

### Data Flow

```
On-Premise DB ──┬──> VPN ──> AWS
API Events ─────┼──> Kinesis ──> Firehose ──> S3 Bronze
Game Servers ───┘
                                    │
                              AWS Glue ETL
                                    │
                              S3 Silver
                                    │
                              AWS Glue ETL
                                    │
                              S3 Gold
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                 Athena        Redshift       QuickSight
```

### Retention Lifecycle

```
Day 0        Day 30       Day 90       Day 365      Day 2555
  │            │            │            │            │
  ▼            ▼            ▼            ▼            ▼
Standard → Standard-IA → Glacier IR → Deep Archive → Delete
$0.023/GB    $0.0125/GB   $0.004/GB    $0.00099/GB
```

---

## Related Chapters

- Chapter 28: Database Design and Optimization
- Chapter 35: Incident Management
- Chapter 46: Fraud Detection Case Studies
