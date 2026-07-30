<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-03.jpg" alt="Volume 3" width="150" /></a>

# Chapter 19: Anti-Fraud System Deep Dive

**📙 Part of Volume 3 — Security Engineering and Runtime Defense** · €84.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZCRSTMH) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 19 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## System Overview

This project implements a complete end-to-end real-time anti-fraud detection and prevention system for casino operations. The system identifies, analyzes, and responds to fraudulent activities across multiple channels including online gaming, sports betting, slot machines, table games, and financial transactions. It supports both cloud-native deployment on AWS with Databricks and on-premises deployment using containerized microservices architecture.

### Key Objectives
- Real-time fraud detection with sub-second latency
- Multi-channel integration (gaming platforms, payments, user behavior)
- Machine learning-driven risk scoring
- Comprehensive alerting and response systems
- Regulatory compliance and audit capabilities
- High availability and scalability

## Architecture Overview

The system follows a layered architecture with distinct components for data ingestion, processing, modeling, and alerting.

```mermaid
graph TB
    subgraph "Data Sources"
        A1[Gaming Platforms]
        A2[Payment Gateways]
        A3[User Behavior Tracking]
        A4[Geolocation Services]
        A5[KYC/AML Systems]
        A6[External Data Feeds]
    end

    subgraph "Ingestion Layer"
        B1[Apache Kafka]
        B2[AWS Kinesis]
        B3[Redis Streams]
        B4[CDC Debezium]
    end

    subgraph "Processing Layer"
        C1[Stream Processing<br/>Apache Spark]
        C2[Feature Engineering<br/>Polars/Python]
        C3[Batch Processing<br/>Databricks]
    end

    subgraph "ML Layer"
        D1[Unsupervised Models<br/>Isolation Forest]
        D2[Supervised Models<br/>XGBoost]
        D3[Online Learning<br/>Reinforcement Learning]
        D4[Model Serving<br/>MLflow]
    end

    subgraph "Alerting & Response"
        E1[Real-time Alerts<br/>WebSocket/SMS]
        E2[Case Management]
        E3[Dashboard UI]
    end

    subgraph "Storage & Monitoring"
        F1[Delta Lake<br/>S3]
        F2[Redis Cluster]
        F3[PostgreSQL<br/>TimescaleDB]
        F4[Prometheus<br/>Grafana]
    end

    A1 --> B1
    A2 --> B2
    A3 --> B3
    A4 --> B4
    A5 --> B1
    A6 --> B2

    B1 --> C1
    B2 --> C1
    B3 --> C2
    B4 --> C2

    C1 --> D1
    C2 --> D2
    C1 --> D3
    C2 --> D4

    D1 --> E1
    D2 --> E1
    D3 --> E2
    D4 --> E3

    C1 --> F1
    C2 --> F2
    D1 --> F3
    D2 --> F4
```

## Data Flow Architecture

### Real-Time Data Collection Flow

```mermaid
flowchart TD
    A[Data Source] --> B{Data Type}
    B -->|Transaction| C[Kafka Topic: transactions]
    B -->|User Event| D[Kafka Topic: user_events]
    B -->|Game Event| E[Kafka Topic: game_events]

    C --> F[Stream Processor]
    D --> F
    E --> F

    F --> G[Feature Engineering<br/>Polars Pipeline]
    G --> H[Risk Scoring Engine]
    H --> I{Alert Threshold}

    I -->|High Risk| J[Immediate Alert<br/>SMS/Phone]
    I -->|Medium Risk| K[Investigation Queue]
    I -->|Low Risk| L[Monitoring Dashboard]

    H --> M[Model Update<br/>Online Learning]
    M --> H
```

### Feature Engineering Pipeline (Using Polars)

The feature engineering pipeline leverages Polars for high-performance data processing:

```python
import polars as pl
from polars import col

def create_player_behavior_features(df: pl.DataFrame) -> pl.DataFrame:
    """Create player behavior features using Polars expressions"""

    # Session duration patterns
    session_features = (
        df.group_by("player_id")
        .agg([
            col("session_duration").mean().alias("avg_session_duration"),
            col("session_duration").std().alias("session_duration_std"),
            col("session_duration").rolling_mean(window_size=10).alias("session_trend")
        ])
    )

    # Betting velocity metrics
    betting_features = (
        df.group_by("player_id")
        .agg([
            col("bet_amount").rolling_sum(window_size=100).alias("rolling_bet_sum"),
            col("bet_amount").diff().abs().mean().alias("bet_acceleration"),
            col("win_loss_ratio").ewm_mean(alpha=0.1).alias("smoothed_win_ratio")
        ])
    )

    # Time-based patterns
    time_features = (
        df.with_columns([
            col("timestamp").dt.hour().alias("hour_of_day"),
            col("timestamp").dt.weekday().alias("day_of_week")
        ])
        .group_by(["player_id", "hour_of_day"])
        .agg(col("bet_amount").sum().alias("hourly_bet_total"))
    )

    return session_features.join(betting_features, on="player_id").join(time_features, on="player_id")

# Usage in streaming pipeline
def process_stream_batch(batch_df: pl.DataFrame) -> pl.DataFrame:
    features = create_player_behavior_features(batch_df)
    risk_scores = calculate_risk_scores(features)
    return features.join(risk_scores, on="player_id")
```

### Machine Learning Model Architecture

```mermaid
graph LR
    subgraph "Feature Store"
        A[Online Features<br/>Redis]
        B[Offline Features<br/>Delta Lake]
    end

    subgraph "Model Training"
        C[Data Preparation]
        D[Feature Selection]
        E[Model Training<br/>XGBoost/Random Forest]
        F[Model Validation]
        G[Model Registry<br/>MLflow]
    end

    subgraph "Model Serving"
        H[Real-time Scoring<br/>REST API]
        I[Batch Scoring<br/>Spark]
        J[A/B Testing]
        K[Model Monitoring<br/>Drift Detection]
    end

    A --> H
    B --> C
    C --> D
    D --> E
    E --> F
    F --> G
    G --> H
    G --> I
    H --> J
    I --> K
    K --> G
```

## Deployment Architectures

### AWS with Databricks Architecture

```mermaid
graph TB
    subgraph "AWS Cloud"
        A[API Gateway]
        B[Lambda Functions]
        C[Kinesis Data Streams]
        D[S3 Buckets<br/>Bronze/Silver/Gold]
        E[Databricks Workspace]
        F[EMR Clusters]
        G[SageMaker]
        H[Step Functions]
        I[CloudWatch]
    end

    subgraph "Databricks Components"
        J[Delta Live Tables]
        K[MLflow Model Registry]
        L[Databricks SQL]
        M[Photon Engine]
    end

    A --> B
    B --> C
    C --> J
    J --> D
    D --> L
    L --> K
    K --> G
    G --> H
    H --> I
    E --> F
    F --> M
```

### On-Premises Containerized Architecture

```mermaid
graph TB
    subgraph "Kubernetes Cluster"
        A[Ingress Controller<br/>Istio Gateway]
        B[Data Ingestion Service<br/>3 replicas]
        C[Feature Engineering Service<br/>5 replicas]
        D[Model Serving Service<br/>10 replicas]
        E[Alert Management Service<br/>2 replicas]
        F[Dashboard Service<br/>2 replicas]
    end

    subgraph "Storage Layer"
        G[PostgreSQL<br/>TimescaleDB]
        H[Redis Cluster]
        I[MinIO S3<br/>Compatible]
        J[Apache Druid]
    end

    subgraph "Monitoring Stack"
        K[Prometheus]
        L[Grafana]
        M[ELK Stack]
    end

    A --> B
    B --> C
    C --> D
    D --> E
    E --> F

    B --> G
    C --> H
    D --> I
    F --> J

    B --> K
    C --> K
    D --> L
    E --> M
```

## Real-Time Alerting System Flow

```mermaid
stateDiagram-v2
    [*] --> Detection
    Detection --> Evaluation: Risk Score Calculated

    Evaluation --> Critical: Score > 0.9
    Evaluation --> High: Score 0.7-0.9
    Evaluation --> Medium: Score 0.5-0.7
    Evaluation --> Low: Score < 0.5

    Critical --> ImmediateAction: SMS/Phone Call
    High --> Investigation: Create Case
    Medium --> Monitoring: Dashboard Alert
    Low --> Logging: Record for Analysis

    ImmediateAction --> Response
    Investigation --> Response
    Monitoring --> Response
    Logging --> [*]

    Response --> Feedback: Human Review
    Feedback --> ModelUpdate: Adjust Thresholds
    ModelUpdate --> Detection

    Response --> Resolution
    Resolution --> [*]
```

## Implementation Phases

### Phase 1: Foundation (Weeks 1-4)
- Infrastructure setup (AWS/on-premises)
- Data ingestion pipeline implementation
- Basic monitoring deployment

### Phase 2: Core Features (Weeks 5-12)
- Feature engineering pipeline development
- Initial ML model training and deployment
- Basic alerting system implementation

### Phase 3: Advanced Features (Weeks 13-20)
- Advanced ML models deployment
- Real-time dashboard implementation
- Regulatory compliance features addition

### Phase 4: Optimization (Weeks 21-24)
- Performance tuning
- Cost optimization implementation
- Full integration testing

### Phase 5: Production Rollout (Weeks 25-28)
- Staged deployment execution
- User training completion
- Documentation finalization
- Operations handover

## Detailed Implementation Flow

### Data Ingestion Flow

```mermaid
flowchart TD
    A[External Data Sources] --> B{Data Type Router}
    B -->|Transactions| C[Transaction Validator]
    B -->|User Events| D[Event Normalizer]
    B -->|Game Data| E[Game Data Processor]

    C --> F[Kafka Producer<br/>Topic: transactions]
    D --> G[Kafka Producer<br/>Topic: user_events]
    E --> H[Kafka Producer<br/>Topic: game_events]

    F --> I[Stream Processing Layer]
    G --> I
    H --> I

    I --> J[Data Quality Checks]
    J --> K[Schema Validation]
    K --> L[Duplicate Detection]

    L --> M{Valid Data?}
    M -->|Yes| N[Enriched Data Store]
    M -->|No| O[Dead Letter Queue]

    N --> P[Feature Engineering Pipeline]
```

### Feature Engineering Process Flow

```mermaid
flowchart TD
    A[Raw Transaction Data] --> B[Data Cleaning<br/>Polars]
    B --> C[Player Aggregation]
    C --> D[Time Window Features]

    D --> E{Feature Category}
    E -->|Player Behavior| F[Session Analysis]
    E -->|Transaction Patterns| G[Velocity Calculations]
    E -->|Network Analysis| H[Relationship Mapping]

    F --> I[Feature Validation]
    G --> I
    H --> I

    I --> J{Features Valid?}
    J -->|Yes| K[Feature Store<br/>Redis/Delta Lake]
    J -->|No| L[Feature Reprocessing]

    K --> M[ML Model Pipeline]
    L --> B
```

### ML Model Training and Deployment Flow

```mermaid
flowchart TD
    A[Training Data] --> B[Data Preparation]
    B --> C[Feature Selection]
    C --> D[Train/Validation Split]

    D --> E{Model Type}
    E -->|Supervised| F[XGBoost Training]
    E -->|Unsupervised| G[Isolation Forest]
    E -->|Sequence| H[LSTM Training]

    F --> I[Hyperparameter Tuning<br/>Optuna]
    G --> I
    H --> I

    I --> J[Model Validation]
    J --> K{Cross-Validation<br/>Score > Threshold?}

    K -->|Yes| L[Model Registry<br/>MLflow]
    K -->|No| M[Model Retraining<br/>Parameter Adjustment]

    L --> N[Model Deployment]
    N --> O[A/B Testing Setup]
    O --> P[Production Traffic<br/>Gradual Rollout]

    P --> Q[Performance Monitoring]
    Q --> R{Drift Detected?}
    R -->|Yes| S[Model Retraining Trigger]
    R -->|No| P

    S --> D
```

### Alert Processing and Response Flow

```mermaid
flowchart TD
    A[Risk Score Generated] --> B[Alert Rule Evaluation]
    B --> C{Alert Condition<br/>Met?}

    C -->|No| D[Continue Monitoring]
    C -->|Yes| E[Alert Generation]

    E --> F[Alert Enrichment<br/>Context Addition]
    F --> G[Deduplication Check]
    G --> H{Is Duplicate?}

    H -->|Yes| I[Update Existing Alert]
    H -->|No| J[Create New Alert]

    J --> K[Severity Classification]
    K --> L{Alert Severity}

    L -->|Critical| M[Immediate Escalation<br/>SMS/Phone/Email]
    L -->|High| N[Team Notification<br/>Slack/Email]
    L -->|Medium| O[Dashboard Alert<br/>Investigation Queue]
    L -->|Low| P[Logging Only<br/>Trend Analysis]

    M --> Q[Case Creation]
    N --> Q
    O --> Q

    Q --> R[Investigation Workflow]
    R --> S[Evidence Gathering]
    S --> T[Risk Assessment]
    T --> U{Action Required?}

    U -->|Block/Alert| V[Execute Action]
    U -->|Monitor| W[Enhanced Monitoring]
    U -->|No Action| X[Case Closure]

    V --> Y[Documentation]
    W --> Y
    X --> Y

    Y --> Z[Feedback Loop<br/>Model Improvement]
    Z --> A
```

### End-to-End System Flow

```mermaid
flowchart TD
    subgraph "Data Sources"
        A1[Gaming Platforms]
        A2[Payment Gateways]
        A3[User Behavior Systems]
        A4[External Data Feeds]
    end

    subgraph "Ingestion & Processing"
        B1[Apache Kafka<br/>Message Queue]
        B2[Stream Processing<br/>Apache Spark]
        B3[Feature Engineering<br/>Polars Pipeline]
        B4[Data Quality<br/>Validation]
    end

    subgraph "ML & Risk Scoring"
        C1[Model Serving<br/>REST API]
        C2[Ensemble Engine<br/>Risk Calculation]
        C3[Online Learning<br/>Model Adaptation]
        C4[Model Monitoring<br/>Drift Detection]
    end

    subgraph "Alerting & Response"
        D1[Alert Generation<br/>Rule Engine]
        D2[Case Management<br/>Investigation]
        D3[Notification System<br/>Multi-Channel]
        D4[Response Automation<br/>Action Execution]
    end

    subgraph "Monitoring & Analytics"
        E1[Prometheus<br/>Metrics Collection]
        E2[Grafana<br/>Dashboards]
        E3[ELK Stack<br/>Log Analysis]
        E4[Business Intelligence<br/>Reporting]
    end

    subgraph "Storage & Persistence"
        F1[Delta Lake<br/>Data Lake]
        F2[Redis<br/>Feature Cache]
        F3[PostgreSQL<br/>Metadata]
        F4[S3<br/>Long-term Storage]
    end

    A1 --> B1
    A2 --> B1
    A3 --> B1
    A4 --> B1

    B1 --> B2
    B2 --> B3
    B3 --> B4
    B4 --> C1

    C1 --> C2
    C2 --> C3
    C3 --> C4
    C4 --> D1

    D1 --> D2
    D2 --> D3
    D3 --> D4

    B2 --> E1
    C2 --> E1
    D2 --> E1
    E1 --> E2
    E1 --> E3
    E1 --> E4

    B3 --> F1
    C2 --> F2
    D2 --> F3
    F1 --> F4

    style A1 fill:#e1f5fe
    style B1 fill:#f3e5f5
    style C1 fill:#e8f5e8
    style D1 fill:#fff3e0
    style E1 fill:#fce4ec
    style F1 fill:#f1f8e9
```

### CI/CD Pipeline Flow

```mermaid
flowchart TD
    A[Code Commit] --> B[Automated Testing]
    B --> C{Tests Pass?}

    C -->|No| D[Fix Issues]
    D --> B

    C -->|Yes| E[Build Artifacts]
    E --> F[Security Scanning]
    F --> G{Security OK?}

    G -->|No| H[Security Review]
    H --> E

    G -->|Yes| I[Container Build]
    I --> J[Integration Testing]
    J --> K{Integration OK?}

    K -->|No| L[Debug Integration]
    L --> I

    K -->|Yes| M[Staging Deployment]
    M --> N[Smoke Tests]
    N --> O{Smoke Tests Pass?}

    O -->|No| P[Staging Rollback]
    P --> M

    O -->|Yes| Q[Performance Testing]
    Q --> R{Performance OK?}

    R -->|No| S[Performance Optimization]
    S --> Q

    R -->|Yes| T[Production Deployment]
    T --> U[Canary Release]
    U --> V[Traffic Monitoring]
    V --> W{Stability OK?}

    W -->|No| X[Traffic Rollback]
    X --> T

    W -->|Yes| Y[Full Production Release]
    Y --> Z[Post-Deployment Monitoring]
```

### Disaster Recovery Flow

```mermaid
flowchart TD
    A[Incident Detected] --> B[Alert Triggered]
    B --> C[Primary Assessment<br/>5 minutes]

    C --> D{System Impact}
    D -->|Full Outage| E[Disaster Recovery<br/>Activation]
    D -->|Partial Outage| F[Component Recovery]
    D -->|Performance Issue| G[Performance Recovery]

    E --> H[Failover to DR Site]
    H --> I[DNS Update]
    I --> J[Traffic Redirection]
    J --> K[Service Verification]

    F --> L[Identify Failed Component]
    L --> M[Component Restart]
    M --> N[Health Verification]

    G --> O[Resource Scaling]
    O --> P[Load Balancing]
    P --> Q[Performance Monitoring]

    K --> R[Business Continuity<br/>Confirmation]
    N --> R
    Q --> R

    R --> S[Communication Update]
    S --> T[Root Cause Analysis]
    T --> U[Preventive Measures]
    U --> V[Recovery Complete]
```

## Documentation Index

This project includes comprehensive documentation covering all aspects of the fraud detection system. Below is an index of all documentation files with brief descriptions:

### Architecture Documentation (`architecture/`)

| Document | Description |
|----------|-------------|
| [`data_collection_architecture.md`](architecture/data_collection_architecture.md) | Detailed design of the real-time data ingestion pipeline, including Kafka architecture, stream processing, data validation, and error handling strategies |
| [`feature_engineering_pipeline.md`](architecture/feature_engineering_pipeline.md) | Comprehensive guide to the Polars-based feature engineering pipeline, covering player behavior features, transaction analysis, network features, and real-time processing |
| [`ml_models_architecture.md`](architecture/ml_models_architecture.md) | Complete ML architecture including unsupervised models (Isolation Forest, Autoencoders), supervised models (XGBoost, Random Forest), sequence models (LSTM), and ensemble methods |
| [`aws_databricks_implementation.md`](architecture/aws_databricks_implementation.md) | Cloud-native AWS implementation using Databricks, covering Delta Lake architecture, MLflow integration, security configurations, and cost optimization |
| [`on_premises_deployment.md`](architecture/on_premises_deployment.md) | Containerized Kubernetes deployment for on-premises environments, including Istio service mesh, Helm charts, and monitoring stack setup |
| [`alerting_system.md`](architecture/alerting_system.md) | Real-time alerting system design with multi-channel notifications, case management, deduplication, correlation, and escalation workflows |

### Deployment Documentation (`deployment/`)

| Document | Description |
|----------|-------------|
| [`staged_deployment_plan.md`](deployment/staged_deployment_plan.md) | Comprehensive 7-phase deployment strategy with infrastructure setup, testing procedures, rollback plans, and production cutover procedures |

### General Documentation (`docs/`)

| Document | Description |
|----------|-------------|
| [`api_documentation.md`](docs/api_documentation.md) | Complete REST API reference with authentication, rate limiting, error codes, SDK examples, and webhook integrations |
| [`operational_runbooks.md`](docs/operational_runbooks.md) | Operational procedures for daily operations, incident response, maintenance schedules, and troubleshooting guides |
| [`handover_to_operations.md`](docs/handover_to_operations.md) | Complete handover package for transitioning to operations teams, including access procedures, monitoring setup, and support contacts |

### Training Materials (`training/`)

| Document | Description |
|----------|-------------|
| [`user_training_materials.md`](training/user_training_materials.md) | Comprehensive 6-module training program covering system operations, monitoring, incident response, maintenance, and best practices |

### Test Documentation (`tests/`)

| Test Suite | Description |
|------------|-------------|
| [`test_full_system_integration.py`](tests/integration/test_full_system_integration.py) | End-to-end integration tests covering service health, data flow validation, alerting verification, and cross-service communication |
| [`test_performance_load.py`](tests/integration/test_performance_load.py) | Performance and load testing suite with configurable user simulation, resource monitoring, and scalability analysis |

## Production Code Reference: Multi-Matrix Risk Scoring

This section presents sanitized production Scala code from two real iGaming systems: a **multi-matrix risk scoring engine** and a **real-time risk alerting pipeline**. These patterns are unique to the gambling industry, where regulatory compliance requires continuous, multi-dimensional risk assessment of every player.

### Why Multi-Matrix Scoring?

Gambling regulators (UKGC, MGA) require operators to evaluate players across multiple risk dimensions simultaneously. A single "risk score" is insufficient -- operators must independently track:

| Matrix | Purpose | Example Rule |
|--------|---------|-------------|
| **RG** (Responsible Gambling) | Player protection | "3+ payment options created in 7 days" |
| **CIR** (Customer Intelligence Response) | Behavioral analysis | "20+ declined deposits in 24 hours" |
| **CRA** (Customer Risk Assessment) | Financial risk | "Net losses in top 50th percentile" |
| **VIP** (VIP Alert) | High-value player monitoring | "First deposit >= $5000" |
| **AFF** (Affordability) | Spending sustainability | "Cash hold > 5000 GBP in 30 days" |
| **RGMX** (Real-time Gambling Matrix) | Live session monitoring | Composite product + payment risk scores |

Each matrix has independent levels (e.g., Yellow/Orange/Red) with configurable score thresholds that trigger escalating compliance actions.

### Architecture Overview

The system has two complementary components:

```
                    +---------------------------+
                    |   Risk Matrix Service     |
                    |   (Scala/Play Framework)  |
                    +---------------------------+
                              |
    Kafka Topics              |              PostgreSQL
    +------------------+      |      +---------------------+
    | score_trigger    |----->|----->| matrix config       |
    | daily_stats      |      |      | user scores/levels  |
    | global_scores    |      |      | audit trails        |
    +------------------+      |      +---------------------+
                              |
                    +---------------------------+
                    |   Risk Alerting Service   |
                    |   (Kafka Streams)         |
                    +---------------------------+
                              |
    +------------------+      |      +---------------------+
    | payment events   |----->|----->| OpsGenie API        |
    | withdrawal events|      |      | Alert DB (cases)    |
    +------------------+      |      +---------------------+
```

### Code Examples (`risk-scoring/`)

The `risk-scoring/` directory contains sanitized production code organized as follows:

| File | Description |
|------|-------------|
| [`ScoreMatrix.scala`](risk-scoring/ScoreMatrix.scala) | Domain model: six matrices, score types, levels, audit entities |
| [`MatrixScorer.scala`](risk-scoring/MatrixScorer.scala) | Core engine: Groovy condition evaluation with pluggable metric providers |
| [`MatrixScoreTransactionalService.scala`](risk-scoring/MatrixScoreTransactionalService.scala) | Transaction orchestration: score + level calculation + alert dispatch |
| [`MetricProviders.scala`](risk-scoring/MetricProviders.scala) | Event-specific metric resolution (deposits, timeouts, resets, etc.) |
| [`Messages.scala`](risk-scoring/Messages.scala) | Kafka message types including daily stats with gambling-specific metrics |
| [`EventTypes.scala`](risk-scoring/EventTypes.scala) | Enumeration of scoring trigger events |
| [`RiskAlertingApp.scala`](risk-scoring/RiskAlertingApp.scala) | Kafka Streams application wiring 20+ alert detection topologies |
| [`AlertStreams.scala`](risk-scoring/AlertStreams.scala) | Stream implementations: windowed aggregation, consecutive counting |
| [`OpsgenieIntegration.scala`](risk-scoring/OpsgenieIntegration.scala) | OpsGenie model, API client, database and notification sinks |
| [`schema.sql`](risk-scoring/schema.sql) | Complete PostgreSQL schema for both systems |

### Key Design Patterns

**1. Groovy-based Rule Engine** -- Scoring rules are stored in the database as Groovy expressions (e.g., `depositTotal >= 7500 AND age < 25`). The `MatrixScorer` creates a lazy-loading variable binding that resolves metrics on demand, caching results across rules sharing the same time window. This allows compliance teams to add or modify rules without code deployments.

**2. Pluggable Metric Providers** -- Each event type dispatches to a specific `MatrixScoreDataProvider` that knows which metrics are relevant. For example, `DepositConfirmedMetricProvider` resolves `depositCount`, `depositTotal`, and `paymentOptionsCreated`, while `TimeoutAppliedMetricProvider` resolves `timeout`, `timeout1d`, and `cam_amber` (cross-matrix level check).

**3. Jurisdiction-Aware Scoring** -- Rules can be filtered by jurisdiction (UKGC, MGA). When both a generic and jurisdiction-specific rule exist for the same ID, the jurisdiction-specific one takes precedence. Entire matrices can be enabled/disabled per jurisdiction via `score_matrix_jurisdiction`.

**4. Sliding Window Alert Detection** -- The Kafka Streams alerting system uses sliding windows (e.g., 24-hour window with 10-second grace period) to detect threshold breaches in real time. The `TotalAmountOfDepositsIn24Hours` stream demonstrates this pattern with currency-aware amount aggregation.

**5. Dual Sink Pattern** -- Every alert flows to both OpsGenie (immediate team notification) and PostgreSQL (persistent case management). The database sink implements deduplication to handle Kafka's at-least-once delivery semantics.

**6. Transactional Outbox** -- The risk matrix service uses a transactional outbox pattern for reliable Kafka message production, ensuring that score changes and their corresponding alerts/interactions are atomically committed.

## Project Structure

```
fraud-detection-system/
├── architecture/                    # System architecture documentation
│   ├── data_collection_architecture.md     # Data ingestion pipeline design
│   ├── feature_engineering_pipeline.md     # Polars feature engineering
│   ├── ml_models_architecture.md          # ML models and training pipeline
│   ├── aws_databricks_implementation.md    # AWS cloud deployment
│   ├── on_premises_deployment.md          # Kubernetes on-premises deployment
│   └── alerting_system.md                 # Real-time alerting architecture
├── deployment/                      # Deployment configurations
│   ├── staged_deployment_plan.md          # 7-phase deployment strategy
│   ├── docker-compose.yml
│   ├── kubernetes/                        # K8s manifests
│   └── aws/                              # CloudFormation templates
├── docs/                           # General documentation
│   ├── api_documentation.md              # REST API reference
│   ├── operational_runbooks.md           # Operations procedures
│   └── handover_to_operations.md         # Operations handover package
├── src/                            # Source code (organized by service)
│   ├── data_ingestion/                   # Kafka-based data collection
│   ├── feature_engineering/              # Polars feature processing
│   ├── ml_models/                        # ML model serving and training
│   ├── alerting/                         # Alert generation and management
│   ├── compliance/                       # Regulatory compliance features
│   ├── cost_optimization/                # Cost analysis and optimization
│   └── dashboard/                        # Real-time monitoring dashboard
├── tests/                          # Comprehensive test suites
│   ├── unit/                            # Unit tests for individual components
│   ├── integration/                      # Integration and performance tests
│   │   ├── test_full_system_integration.py    # End-to-end system tests
│   │   └── test_performance_load.py           # Load and performance testing
│   └── e2e/                             # End-to-end user journey tests
├── training/                       # User training materials
│   └── user_training_materials.md       # Complete training program
├── scripts/                        # Utility and automation scripts
├── config/                         # Configuration files and templates
└── README.md                       # This comprehensive overview
```

## Quick Start Guide

### Local Development Setup

1. **Clone Repository**
   ```bash
   git clone https://github.com/thebackendofluck/book.git
   cd book/19-anti-fraud-system-deep-dive
   ```

2. **Environment Setup**
   ```bash
   # Create virtual environment
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate

   # Install dependencies (per-service requirement sets)
   make install
   ```

3. **Start Local Services**
   ```bash
   # Start with Docker Compose
   docker-compose up -d

   # Or run individual services
   python -m src.data_ingestion.app &
   python -m src.feature_engineering.app &
   python -m src.ml_models.app &
   ```

4. **Run Tests**
   ```bash
   # Unit tests
   pytest tests/unit/

   # Integration tests
   pytest tests/integration/

   # Performance tests
   python tests/integration/test_performance_load.py
   ```

5. **Access Interfaces**
   - API Documentation: http://localhost:8000/docs
   - Monitoring Dashboard: http://localhost:3000
   - System Health: http://localhost:8000/health

### Production Deployment

#### Terraform Infrastructure

The system includes comprehensive Terraform configurations for AWS and Kubernetes deployment:

**AWS Infrastructure (`terraform/aws/main.tf`):**
- VPC with 3 availability zones (public/private subnets, NAT gateways)
- EKS cluster (v1.28) with managed node groups
- MSK (Managed Kafka) - 3 brokers, 500GB storage each
- ElastiCache Redis - Multi-AZ replication group
- RDS PostgreSQL - Multi-AZ, 100GB gp3 storage
- KMS encryption for all data at rest
- S3 buckets for data and models
- ECR repositories for container images

**Kubernetes Deployment (`terraform/kubernetes/main.tf`):**
- 6 microservice deployments with HPA
- Network policies for security isolation
- Pod disruption budgets for availability
- Ingress with TLS termination
- ConfigMaps and Secrets management
- Prometheus service discovery annotations

1. **Infrastructure Setup**
   ```bash
   # AWS deployment with Terraform
   cd terraform/aws
   terraform init
   terraform plan -out=tfplan
   terraform apply tfplan

   # Get outputs for Kubernetes deployment
   export MSK_BROKERS=$(terraform output -raw msk_bootstrap_brokers_tls)
   export REDIS_HOST=$(terraform output -raw redis_endpoint)
   export RDS_HOST=$(terraform output -raw rds_endpoint)
   ```

2. **Kubernetes Deployment with Terraform**
   ```bash
   # Update kubeconfig
   aws eks update-kubeconfig --name fraud-detection-production

   # Deploy Kubernetes resources
   cd ../kubernetes
   terraform init
   terraform apply \
     -var="kafka_brokers=${MSK_BROKERS}" \
     -var="redis_host=${REDIS_HOST}" \
     -var="postgres_host=${RDS_HOST}"
   ```

2. **Configuration**
   ```bash
   # Set environment variables
   export KAFKA_BROKERS="kafka-cluster:9092"
   export REDIS_URL="redis://redis-cluster:6379"
   export DATABASE_URL="postgresql://user:pass@db:5432/fraud_db"
   ```

3. **Service Deployment**
   ```bash
   # Deploy all services
   kubectl apply -f deployment/kubernetes/

   # Verify deployment
   kubectl get pods -n fraud-detection
   kubectl get services -n fraud-detection
   ```

4. **Health Verification**
   ```bash
   # Check all services
   curl -f https://api.fraud-detection.com/health

   # Verify monitoring
   curl -f https://monitoring.fraud-detection.com/-/healthy
   ```

## Troubleshooting

### Common Issues

1. **Service Startup Failures**
   ```bash
   # Check logs
   kubectl logs -l app=failed-service -n fraud-detection

   # Check resource constraints
   kubectl describe pod failed-pod -n fraud-detection

   # Check dependencies
   kubectl get services -n fraud-detection
   ```

2. **Performance Issues**
   ```bash
   # Monitor resource usage
   kubectl top pods -n fraud-detection

   # Check database connections
   kubectl exec -it postgres-pod -- psql -c "SELECT count(*) FROM pg_stat_activity;"

   # Review slow queries
   kubectl exec -it postgres-pod -- psql -c "
   SELECT query, mean_time, calls
   FROM pg_stat_statements
   ORDER BY mean_time DESC
   LIMIT 10;
   "
   ```

3. **Data Pipeline Issues**
   ```bash
   # Check Kafka topics
   kafka-topics --list --bootstrap-server kafka-cluster:9092

   # Check consumer lag
   kafka-consumer-groups --bootstrap-server kafka-cluster:9092 \
     --group fraud-detection-consumer \
     --describe
   ```

### Debug Commands

```bash
# System health check
curl -s http://localhost:8000/health | jq .

# Service logs
kubectl logs -f deployment/data-ingestion-service -n fraud-detection

# Database connectivity
kubectl run test-connection --image=postgres:18 --rm -it --restart=Never \
  -- psql postgresql://user:pass@postgres:5432/fraud_db -c "SELECT version();"

# Network connectivity
kubectl run test-network --image=busybox --rm -it --restart=Never \
  -- telnet kafka-cluster 9092

# Performance profiling
py-spy top --pid $(pgrep -f "python.*app")
```

## Contributing

### Development Workflow

1. **Create Feature Branch**
   ```bash
   git checkout -b feature/new-fraud-detection-algorithm
   ```

2. **Implement Changes**
   ```bash
   # Write code with tests
   # Follow PEP 8 style guide
   # Add comprehensive documentation
   ```

3. **Run Tests**
   ```bash
   # Unit tests
   pytest tests/unit/ -v

   # Integration tests
   pytest tests/integration/ -v

   # Performance tests
   python tests/integration/test_performance_load.py
   ```

4. **Code Review**
   ```bash
   # Create pull request
   # Address review comments
   # Ensure CI/CD passes
   ```

5. **Merge and Deploy**
   ```bash
   # Merge to main branch
   # Automated deployment triggered
   # Monitor production deployment
   ```

### Code Quality Standards

- **Python**: PEP 8 compliance, type hints, comprehensive docstrings
- **Testing**: >80% code coverage, integration and performance tests
- **Documentation**: All public APIs documented, usage examples provided
- **Security**: Input validation, secure coding practices, regular security reviews

## Roadmap

### Q1 2024: Foundation
- [x] Core system architecture
- [x] Basic data ingestion pipeline
- [x] Initial ML models
- [x] Production deployment

### Q2 2024: Enhancement
- [ ] Advanced ML algorithms (Graph Neural Networks)
- [ ] Real-time model adaptation
- [ ] Enhanced compliance features
- [ ] Multi-region deployment

### Q3 2024: Intelligence
- [ ] Predictive analytics dashboard
- [ ] Automated threat intelligence
- [ ] Advanced behavioral analysis
- [ ] Integration with external fraud databases

### Q4 2024: Optimization
- [ ] AI-powered cost optimization
- [ ] Advanced monitoring and alerting
- [ ] Performance benchmarking suite
- [ ] Enterprise security features

## Support and Contact

This is companion code for the book *The Backend of Luck*. It ships as a
reference implementation, so there is no vendor behind it: no support desk,
no sales team, and no 24/7 hotline. Once you deploy it, on-call and incident
response are yours to staff, and the escalation paths in
`docs/handover_to_operations.md` are a template to fill in with your own
contacts.

- **Questions and corrections**: gustavo@thebackendofluck.com
- **Book and errata**: https://thebackendofluck.com
- **Issue tracker**: https://github.com/thebackendofluck/book/issues

---

*This comprehensive README provides complete documentation for the Real-Time Anti-Fraud System, including architecture, implementation details, deployment procedures, and operational guidance. For specific implementation details, refer to the respective directories and documentation files.*

## Performance Requirements

- **Latency Targets:**
  - P50: < 10ms for risk scoring
  - P95: < 50ms for complex fraud detection
  - P99: < 100ms for end-to-end processing

- **Throughput:**
  - Minimum 100,000 transactions per second
  - Burst capacity to 500,000 TPS

- **Availability:** 99.99% uptime SLA

## Monitoring and Observability

### Key Metrics to Monitor
- System uptime and availability
- Processing latency percentiles
- Model accuracy and precision
- Data quality scores
- Fraud detection rate
- False positive reduction

### Dashboard Structure
- Executive dashboard with KPIs
- Operational dashboard for SOC team
- Technical dashboard for engineering
- Compliance dashboard for regulators

## Compliance and Security

### Regulatory Requirements
- PCI DSS for payment processing
- GDPR for data privacy
- CCPA for California residents
- Gaming commission requirements
- AML/KYC regulations

### Security Measures
- End-to-end encryption
- Role-based access control
- Audit logging and monitoring
- Data anonymization for PII
- Regular security assessments

## Technology Stack

### Core Technologies
- **Data Processing:** Apache Spark, Polars, Python
- **Streaming:** Apache Kafka, AWS Kinesis, Redis Streams
- **Storage:** Delta Lake, PostgreSQL, Redis, S3
- **ML:** XGBoost, Scikit-learn, MLflow
- **Deployment:** Docker, Kubernetes, AWS Services
- **Monitoring:** Prometheus, Grafana, ELK Stack

### Development Tools
- **Version Control:** Git
- **CI/CD:** GitHub Actions, Jenkins
- **Testing:** Pytest, Locust for load testing
- **Documentation:** Sphinx, MkDocs

## Getting Started

### Prerequisites
- Python 3.9+
- Docker and Docker Compose
- Kubernetes cluster (for on-premises)
- AWS CLI (for cloud deployment)

### Quick Start
1. Clone the repository
2. Set up environment variables
3. Run `docker-compose up` for local development
4. Deploy to Kubernetes or AWS as needed

### Configuration
- Environment-specific configuration files
- Secret management with AWS Secrets Manager or Kubernetes secrets
- Feature flags for gradual rollouts

## API Documentation

### Core APIs
- `/api/v1/risk-score` - Real-time risk scoring
- `/api/v1/alerts` - Alert management
- `/api/v1/features` - Feature engineering
- `/api/v1/models` - Model management

### Integration APIs
- Webhook endpoints for third-party integrations
- REST APIs for casino management systems
- Streaming APIs for real-time data feeds

## Operational Runbooks

### Incident Response
1. Alert triggered
2. Initial assessment
3. Escalation if needed
4. Investigation and resolution
5. Post-incident review

### Maintenance Procedures
- Regular model retraining
- Data pipeline monitoring
- Infrastructure scaling
- Backup verification

## Cost Optimization

### Cloud Optimization
- Spot instances for batch processing
- Reserved instances for baseline capacity
- Auto-scaling based on load patterns
- Data lifecycle management

### On-Premises Optimization
- Resource pooling and sharing
- Workload scheduling optimization
- Storage tiering strategies

## Disaster Recovery

### Backup Strategies
- Real-time replication for critical data
- Point-in-time recovery capability
- Cross-region backups for cloud
- Offline backups for on-premises

### Failover Procedures
- Automated failover with health checks
- Manual override capabilities
- Rollback procedures
- Communication protocols

## Success Metrics and KPIs

### Technical Metrics
- System uptime: >99.99%
- Processing latency P95: <50ms
- Model accuracy: >95%
- Data quality score: >98%

### Business Metrics
- Fraud detection rate: >90%
- False positive rate: <5%
- Revenue protection: $X million annually
- Operational cost reduction: 20%

## Contributing

### Development Workflow
1. Create feature branch
2. Implement changes with tests
3. Code review and approval
4. Merge to main branch
5. Automated deployment

### Code Standards
- PEP 8 for Python code
- Comprehensive unit and integration tests
- Documentation for all public APIs
- Security code reviews

## License

Licensing terms for this companion code are stated in the repository root of
https://github.com/thebackendofluck/book.

## Apache Iceberg Fraud Lakehouse (Level 2 Pipeline)

The `iceberg-lakehouse/` directory contains the full implementation of a Level-2
fraud pipeline that adds a proper data lakehouse layer on top of the existing
Elasticsearch-based setup.

### Pipeline Evolution

```
Level 1 (baseline — already deployed):
  Casino (203.0.113.1)
    → Fraud API (:8180)
    → Elasticsearch (casino-events-*, fraud-alerts-*)

Level 2 (lakehouse — deployed to ops-host 10.0.0.11):
  Elasticsearch
    → es_to_kafka_bridge.py (cron, every 60s)
    → Kafka topic: fraud.raw.events (4 partitions)
    → Flink JobManager (real-time scoring: velocity, geo, device, bonus abuse)
    → Iceberg tables (bronze/silver/gold)
    → Spark batch (nightly feature engineering + ensemble scoring)

Level 3 (future — Chapter 43):
  Iceberg gold layer
    → ML model serving (XGBoost + LSTM + GNN ensemble)
    → LLM anomaly narration
```

### Deployed Components (ops-host)

| Container | Image | Port | Role |
|---|---|---|---|
| iceberg-minio | minio/minio | :9010 (S3), :9091 (console) | Object storage for Iceberg data files |
| iceberg-postgres | postgres:16-alpine | :5433 | Catalog metadata (JDBC backend) |
| iceberg-rest | tabulario/iceberg-rest:1.5.0 | :8181 | Iceberg REST catalog API |
| iceberg-spark | apache/spark:3.5.5 | :7077, :8088 | Batch fraud processing |
| iceberg-spark-worker | apache/spark:3.5.5 | — | Spark executor (4 cores, 6 GB) |
| iceberg-flink-jobmanager | flink:1.18 | :8086 | Flink real-time streaming |
| iceberg-flink-taskmanager | flink:1.18 | — | Flink executor (4 slots, 3 GB) |

Total RAM used: ~1.7 GB (limit: 21 GB configured).

### Iceberg Tables (namespace: fraud_analytics)

| Table | Write mode | Partitioning | Purpose |
|---|---|---|---|
| transactions | merge-on-read | day + jurisdiction + risk_level | Raw event store |
| player_sessions | copy-on-write | day + jurisdiction | Aggregated session features |
| fraud_alerts | merge-on-read | day + jurisdiction + risk_level | Detection output |
| risk_scores | copy-on-write | day + jurisdiction | ML model scores |

### ES → Kafka Bridge

`es_to_kafka_bridge.py` polls Elasticsearch every 60 seconds and forwards new
casino events to the `fraud.raw.events` Kafka topic.

- Backfill run on deploy: **38,186 events** forwarded in ~4 seconds (0 errors)
- State tracked in: `~/Projetos/fraud-iceberg-lakehouse/bridge-state.json`
- Cron schedule: `* * * * *` (every minute)
- Idempotent producer: `enable_idempotence=True`, keyed by `player_id`

### Deployment

```bash
# From this repo (local machine with SSH to ops-host):
bash iceberg-lakehouse/deploy_lakehouse_ops-host.sh

# Or step by step on ops-host:
cd ~/Projetos/fraud-iceberg-lakehouse
docker compose -f docker-compose-lakehouse-ops-host.yml up -d
```

### Running the Spark Batch Job

```bash
# Inside the iceberg-spark container:
docker exec iceberg-spark /opt/spark/bin/spark-submit \
  --master spark://spark:7077 \
  --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0,\
org.apache.hadoop:hadoop-aws:3.3.4,\
com.amazonaws:aws-java-sdk-bundle:1.12.262 \
  /opt/fraud-scripts/spark_fraud_batch.py \
  --date $(date +%Y-%m-%d) \
  --catalog-uri http://iceberg-rest:8181
```

### Time-Travel Queries

```python
from pyiceberg.catalog import load_catalog
from datetime import datetime, timedelta, timezone

catalog = load_catalog('fraud_catalog', **{
    'type': 'rest',
    'uri': 'http://localhost:8181',
    's3.endpoint': 'http://localhost:9010',
    's3.access-key-id': 'minioadmin',
    's3.secret-access-key': os.environ['MINIO_ROOT_PASSWORD'],
})

lakehouse = FraudLakehouse(LakehouseConfig(catalog_uri='http://localhost:8181'))
lakehouse.initialize()

# Query fraud alerts as of yesterday
yesterday = datetime.now(timezone.utc) - timedelta(days=1)
alerts = lakehouse.time_travel_query('fraud_alerts', as_of=yesterday)

# Compare fraud rates: before vs after a model deployment
week_ago = datetime.now(timezone.utc) - timedelta(days=7)
comparison = lakehouse.compare_fraud_rates(week_ago, datetime.now(timezone.utc))
```

### Integration with Chapter 34 Analytics

The Iceberg gold layer (`risk_scores`, `fraud_alerts`) can be queried directly
by the Databricks/Spark pipelines from Chapter 34:

- `bronze_to_silver_delta.py`: replace Delta Lake source with Iceberg REST catalog
- `silver_to_gold_delta.py`: write fraud features back to Iceberg for auditability
- `realtime_analytics.py`: subscribe to `fraud.raw.events` Kafka topic instead of
  the raw game-events topic

Both pipelines share the same Kafka broker (fraud-detection-kafka, internal
listener `kafka:29092`) and the same Iceberg REST catalog (`http://iceberg-rest:8181`).

---

## Support

This is companion code for the book *The Backend of Luck*, not a hosted product.
For questions and corrections:
- Email: gustavo@thebackendofluck.com
- Book and errata: https://thebackendofluck.com
- Issue tracker: https://github.com/thebackendofluck/book/issues

---

*This README provides a comprehensive overview of the Real-Time Anti-Fraud System architecture and implementation plan. For detailed code examples, configuration files, and deployment scripts, refer to the respective directories in this repository.*
