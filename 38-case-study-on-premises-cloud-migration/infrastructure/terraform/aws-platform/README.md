# AWS iGaming Casino Platform — Terraform Infrastructure

Complete AWS infrastructure for a regulated iGaming casino platform, built for
compliance with NJ DGE, PA PGCB, and PCI-DSS requirements.

## Architecture

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                     INTERNET                           │
                    └───────────────────────┬─────────────────────────────────┘
                                            │
                                    ┌───────▼───────┐
                                    │   WAF v2      │  Geo-fence, rate limit,
                                    │   (OWASP)     │  SQL injection protection
                                    └───────┬───────┘
                                            │
                    ┌───────────────────────▼─────────────────────────────────┐
                    │              PUBLIC SUBNETS (3 AZs)                     │
                    │  ┌─────────────────────────────────────────────────┐    │
                    │  │         Application Load Balancer               │    │
                    │  │         (HTTPS / TLS 1.3, access logs)         │    │
                    │  └─────────────────────┬───────────────────────────┘    │
                    │  NAT GW (a)    NAT GW (b)    NAT GW (c)               │
                    └───────────────────────┬─────────────────────────────────┘
                                            │
                    ┌───────────────────────▼─────────────────────────────────┐
                    │              PRIVATE SUBNETS (3 AZs)                    │
                    │  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
                    │  │ ECS Task │  │ ECS Task │  │ ECS Task │  Fargate    │
                    │  │ (FastAPI)│  │ (FastAPI)│  │ (FastAPI)│  Auto-scale │
                    │  └────┬─────┘  └────┬─────┘  └────┬─────┘             │
                    │       │             │             │                     │
                    │       └─────────────┼─────────────┘                     │
                    │                     │                                    │
                    └─────────────────────┼────────────────────────────────────┘
                                          │
                    ┌─────────────────────▼────────────────────────────────────┐
                    │              DATA SUBNETS (3 AZs) — No internet route    │
                    │                                                          │
                    │  ┌──────────────────┐    ┌──────────────────┐           │
                    │  │  RDS PostgreSQL  │    │  ElastiCache     │           │
                    │  │  16 (Multi-AZ)   │    │  Redis 7.1       │           │
                    │  │  Encrypted, 7yr  │    │  Multi-AZ,       │           │
                    │  │  backup          │    │  Encrypted       │           │
                    │  └──────────────────┘    └──────────────────┘           │
                    │                                                          │
                    └──────────────────────────────────────────────────────────┘

    Supporting Services:
    ┌─────────────┐  ┌─────────────┐  ┌────────────────┐  ┌──────────────┐
    │ ECR         │  │ Secrets     │  │ CloudWatch     │  │ CodePipeline │
    │ (Images)    │  │ Manager     │  │ (Monitoring)   │  │ + CodeBuild  │
    │ Immutable   │  │ Auto-rotate │  │ 7yr retention  │  │ (CI/CD)      │
    └─────────────┘  └─────────────┘  └────────────────┘  └──────────────┘
```

## Prerequisites

1. **AWS Account** with permissions to create VPC, ECS, RDS, ElastiCache, ALB,
   WAF, Secrets Manager, CloudWatch, CodePipeline, CodeBuild, S3, IAM.

2. **Terraform >= 1.5.0** installed locally.

3. **AWS CLI v2** configured with credentials:
   ```bash
   aws configure --profile acmetocasino
   export AWS_PROFILE=acmetocasino
   ```

4. **ACM Certificate** for your domain (request via AWS Console or CLI):
   ```bash
   aws acm request-certificate \
     --domain-name api.acmetocasino.com \
     --validation-method DNS
   ```

5. **CodeStar Connection** to GitHub (created in AWS Console under
   Developer Tools > Connections).

6. **S3 backend bucket** for Terraform state (optional but recommended):
   ```bash
   aws s3 mb s3://acmetocasino-terraform-state --region us-east-1
   aws dynamodb create-table \
     --table-name terraform-lock \
     --attribute-definitions AttributeName=LockID,AttributeType=S \
     --key-schema AttributeName=LockID,KeyType=HASH \
     --billing-mode PAY_PER_REQUEST
   ```

## Quick Start

```bash
# 1. Clone and navigate
cd new-platform/terraform/aws-platform

# 2. Copy example variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values (certificate_arn, codestar_connection_arn, etc.)

# 3. Initialize Terraform
terraform init

# 4. Preview changes
terraform plan -var-file="terraform.tfvars"

# 5. Apply infrastructure
terraform apply -var-file="terraform.tfvars"

# 6. Verify outputs
terraform output platform_summary
```

## Compliance Mapping

| Requirement | Resource | File |
|---|---|---|
| NJ DGE 13:69O-1.2 — Network redundancy | VPC 3-AZ, NAT per AZ | vpc.tf |
| NJ DGE 13:69O-1.3 — High availability | ECS Multi-AZ, RDS Multi-AZ | ecs.tf, rds.tf |
| NJ DGE 13:69O-1.4 — Session security | Redis encrypted at rest/transit | elasticache.tf |
| NJ DGE 13:69O-1.5 — TLS 1.2+ | ALB TLS 1.3 policy | alb.tf |
| NJ DGE 13:69O-1.6 — Encrypted storage | RDS encryption, KMS | rds.tf |
| NJ DGE 13:69O-1.7 — Artifact traceability | ECR immutable tags, CodePipeline | ecr.tf, ci-cd.tf |
| NJ DGE 13:69O-1.8 — Least privilege | IAM scoped roles | iam.tf |
| NJ DGE 13:69O-1.9 — 7-year log retention | CloudWatch 2557-day retention | cloudwatch.tf |
| PA PGCB §809a.5 — WAF required | WAF v2 with OWASP rules | alb.tf |
| PA PGCB §809a.8 — Auto-scaling | ECS target tracking policies | ecs.tf |
| PCI-DSS 1.3 — Network segmentation | 3-tier subnets, security groups | vpc.tf |
| PCI-DSS 3.4 — Encryption at rest | RDS, Redis, S3 encryption | rds.tf, elasticache.tf |
| PCI-DSS 4.1 — Encryption in transit | TLS 1.3, Redis transit encryption | alb.tf, elasticache.tf |
| PCI-DSS 6.6 — WAF protection | WAF v2, rate limiting, geo-fence | alb.tf |
| PCI-DSS 8.2.4 — Password rotation | Secrets Manager 30-day rotation | secrets.tf |
| PCI-DSS 10.1 — Audit trails | VPC Flow Logs, CloudWatch | vpc.tf, cloudwatch.tf |

## Cost Estimate (Small Operator, ~$500/mo)

| Service | Configuration | Monthly Est. |
|---|---|---|
| ECS Fargate | 2 tasks x 0.5 vCPU / 1 GB | $30 |
| RDS PostgreSQL | db.t4g.medium, Multi-AZ, 50 GB | $130 |
| ElastiCache Redis | cache.t4g.medium, 2 nodes | $95 |
| ALB | 1 ALB + data processing | $25 |
| NAT Gateway | 3 NAT GWs + data transfer | $110 |
| WAF v2 | Web ACL + 5 rules + requests | $25 |
| CloudWatch | Logs, metrics, alarms, dashboard | $30 |
| Secrets Manager | 3 secrets + API calls | $3 |
| ECR | Image storage | $2 |
| S3 | ALB logs, pipeline artifacts | $5 |
| CodeBuild | ~30 builds/mo | $5 |
| AWS Backup | RDS snapshots (Glacier) | $15 |
| **Total** | | **~$475/mo** |

Note: Costs vary by traffic volume and data transfer. For cost optimization:
- Use `FARGATE_SPOT` for non-production environments
- Use reserved instances for RDS (1-year: ~30% savings)
- Reduce to 1 NAT Gateway in non-prod (~$70/mo savings)

## Destroying Infrastructure

```bash
# CAUTION: This will destroy all resources including databases.
# Ensure backups are verified before proceeding.

# Disable deletion protection first (if prod)
terraform apply -var="db_deletion_protection=false"

# Destroy all resources
terraform destroy -var-file="terraform.tfvars"
```

## File Structure

```
aws-platform/
├── main.tf                     # Provider config, backend
├── variables.tf                # All input variables
├── outputs.tf                  # All outputs
├── vpc.tf                      # VPC, subnets, NAT, flow logs, security groups
├── ecs.tf                      # ECS Fargate cluster, task, service, auto-scaling
├── rds.tf                      # RDS PostgreSQL, parameter group, AWS Backup
├── elasticache.tf              # ElastiCache Redis, parameter group
├── alb.tf                      # ALB, listeners, target group, WAF v2
├── ecr.tf                      # ECR repository, lifecycle policy
├── secrets.tf                  # Secrets Manager, rotation Lambda
├── iam.tf                      # IAM roles for ECS, CodeBuild, CodePipeline
├── cloudwatch.tf               # Log groups, alarms, dashboard
├── ci-cd.tf                    # CodePipeline, CodeBuild, S3 artifacts
├── buildspec.yml               # CodeBuild build specification
├── terraform.tfvars.example    # Example variable values
├── README.md                   # This file
└── .github/
    └── workflows/
        └── aws-deploy.yml      # GitHub Actions deploy workflow
```

## Book Context

These configurations are from the book's simulation platform (Chapter 38: Cloud Migration).
They demonstrate migrating an on-premises iGaming platform to AWS while maintaining
regulatory compliance. Source files: `new-platform/terraform/aws-platform/`
