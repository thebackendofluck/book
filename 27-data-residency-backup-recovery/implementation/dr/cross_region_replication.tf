# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Cross-Region Replication for iGaming Platform
# =============================================================================
# Terraform configuration for multi-region data replication with
# jurisdiction-compliant storage. Covers:
#   - RDS cross-region read replicas
#   - S3 cross-region replication with encryption
#   - ElastiCache (Redis) Global Datastore
#   - DynamoDB Global Tables
#
# Jurisdictions: UK (eu-west-2), Malta/EU (eu-central-1), Ontario (ca-central-1)
#
# Usage:
#   terraform init
#   terraform plan -var="jurisdiction=UK"
#   terraform apply -var="jurisdiction=UK"
# =============================================================================

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------
variable "jurisdiction" {
  description = "Target jurisdiction (UK, MT, ON)"
  type        = string
  default     = "UK"

  validation {
    condition     = contains(["UK", "MT", "ON"], var.jurisdiction)
    error_message = "Jurisdiction must be UK, MT, or ON."
  }
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

variable "db_instance_class" {
  description = "RDS instance class for primary"
  type        = string
  default     = "db.r6g.2xlarge"
}

variable "db_allocated_storage" {
  description = "Storage in GB"
  type        = number
  default     = 500
}

# Jurisdiction-specific region mapping
locals {
  jurisdiction_config = {
    UK = {
      primary_region   = "eu-west-2" # London
      replica_region   = "eu-west-1" # Ireland (UK IDTA allows EU)
      s3_backup_region = "eu-west-2" # Keep backups in UK
      description      = "United Kingdom (UKGC)"
    }
    MT = {
      primary_region   = "eu-central-1" # Frankfurt
      replica_region   = "eu-west-1"    # Ireland
      s3_backup_region = "eu-north-1"   # Stockholm
      description      = "Malta / EU (MGA)"
    }
    ON = {
      primary_region   = "ca-central-1" # Montreal
      replica_region   = "us-east-1"    # Virginia (PIPEDA allows)
      s3_backup_region = "ca-central-1" # Keep in Canada
      description      = "Ontario, Canada (AGCO)"
    }
  }

  config = local.jurisdiction_config[var.jurisdiction]

  common_tags = {
    Project      = "igaming-platform"
    Jurisdiction = var.jurisdiction
    Environment  = var.environment
    ManagedBy    = "terraform"
    Compliance   = "data-residency"
  }
}

# ---------------------------------------------------------------------------
# Provider configuration (multi-region)
# ---------------------------------------------------------------------------
provider "aws" {
  region = local.config.primary_region
  alias  = "primary"

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  region = local.config.replica_region
  alias  = "replica"

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  region = local.config.s3_backup_region
  alias  = "backup"

  default_tags {
    tags = local.common_tags
  }
}

# ---------------------------------------------------------------------------
# KMS Keys (jurisdiction-separated encryption)
# ---------------------------------------------------------------------------
resource "aws_kms_key" "primary_db" {
  provider = aws.primary

  description             = "iGaming DB encryption key - ${var.jurisdiction} primary"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  # Key rotation every 365 days (AWS managed)
  # For 90-day rotation required by some regulators, use external key store

  tags = {
    Name         = "igaming-db-${var.jurisdiction}-primary"
    DataType     = "database"
    Jurisdiction = var.jurisdiction
  }
}

resource "aws_kms_alias" "primary_db" {
  provider = aws.primary

  name          = "alias/igaming-db-${lower(var.jurisdiction)}-primary"
  target_key_id = aws_kms_key.primary_db.key_id
}

resource "aws_kms_key" "replica_db" {
  provider = aws.replica

  description             = "iGaming DB encryption key - ${var.jurisdiction} replica"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Name         = "igaming-db-${var.jurisdiction}-replica"
    DataType     = "database"
    Jurisdiction = var.jurisdiction
  }
}

resource "aws_kms_key" "backup_s3" {
  provider = aws.backup

  description             = "iGaming S3 backup encryption - ${var.jurisdiction}"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Name         = "igaming-s3-${var.jurisdiction}-backup"
    DataType     = "backup"
    Jurisdiction = var.jurisdiction
  }
}

# ---------------------------------------------------------------------------
# RDS Primary Database
# ---------------------------------------------------------------------------
resource "aws_db_subnet_group" "primary" {
  provider = aws.primary

  name       = "igaming-${lower(var.jurisdiction)}-primary"
  subnet_ids = data.aws_subnets.primary_private.ids

  tags = {
    Name = "igaming-db-subnet-${var.jurisdiction}-primary"
  }
}

resource "aws_db_instance" "primary" {
  provider = aws.primary

  identifier = "igaming-${lower(var.jurisdiction)}-primary"

  engine         = "postgres"
  engine_version = "16.4"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_allocated_storage * 2
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.primary_db.arn

  db_name  = "igaming_${lower(var.jurisdiction)}"
  username = "igaming_admin"
  # Password managed via AWS Secrets Manager (not in Terraform state)
  manage_master_user_password = true

  multi_az               = true # Non-negotiable for regulated ops
  db_subnet_group_name   = aws_db_subnet_group.primary.name
  vpc_security_group_ids = [aws_security_group.db_primary.id]

  backup_retention_period   = 35 # 35-day rolling backups
  backup_window             = "02:00-03:00"
  maintenance_window        = "Mon:03:00-Mon:04:00"
  copy_tags_to_snapshot     = true
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "igaming-${lower(var.jurisdiction)}-final-${formatdate("YYYYMMDD", timestamp())}"

  # Monitoring
  monitoring_interval                   = 60
  monitoring_role_arn                   = aws_iam_role.rds_monitoring.arn
  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.primary_db.arn
  performance_insights_retention_period = 731 # 2 years

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  # Parameters for iGaming workloads
  parameter_group_name = aws_db_parameter_group.igaming.name

  tags = {
    Name         = "igaming-primary-${var.jurisdiction}"
    DataTier     = "tier-1"
    BackupPolicy = "continuous"
  }
}

resource "aws_db_parameter_group" "igaming" {
  provider = aws.primary

  name   = "igaming-${lower(var.jurisdiction)}-pg16"
  family = "postgres16"

  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements,auto_explain"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "100" # Log queries > 100ms
  }

  parameter {
    name  = "max_connections"
    value = "500"
  }

  parameter {
    name  = "wal_level"
    value = "logical" # Supports both physical and logical replication
  }

  tags = {
    Name = "igaming-params-${var.jurisdiction}"
  }
}

# ---------------------------------------------------------------------------
# RDS Cross-Region Read Replica
# ---------------------------------------------------------------------------
resource "aws_db_instance" "cross_region_replica" {
  provider = aws.replica

  identifier          = "igaming-${lower(var.jurisdiction)}-replica"
  replicate_source_db = aws_db_instance.primary.arn
  instance_class      = var.db_instance_class

  storage_encrypted = true
  kms_key_id        = aws_kms_key.replica_db.arn

  multi_az               = false # Replica doesn't need multi-AZ
  vpc_security_group_ids = [aws_security_group.db_replica.id]

  monitoring_interval                   = 60
  monitoring_role_arn                   = aws_iam_role.rds_monitoring_replica.arn
  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.replica_db.arn
  performance_insights_retention_period = 731

  tags = {
    Name      = "igaming-replica-${var.jurisdiction}"
    DataTier  = "tier-1"
    Purpose   = "cross-region-dr"
    ReplicaOf = aws_db_instance.primary.identifier
  }
}

# ---------------------------------------------------------------------------
# S3 Backup Bucket with Cross-Region Replication
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "backup_primary" {
  provider = aws.primary

  bucket = "igaming-backup-${lower(var.jurisdiction)}-primary"

  tags = {
    Name         = "igaming-backup-primary"
    DataType     = "backup"
    Jurisdiction = var.jurisdiction
  }
}

resource "aws_s3_bucket_versioning" "backup_primary" {
  provider = aws.primary
  bucket   = aws_s3_bucket.backup_primary.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backup_primary" {
  provider = aws.primary
  bucket   = aws_s3_bucket.backup_primary.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.primary_db.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backup_primary" {
  provider = aws.primary
  bucket   = aws_s3_bucket.backup_primary.id

  rule {
    id     = "backup-lifecycle"
    status = "Enabled"

    # Tier to cheaper storage over time
    transition {
      days          = 30
      storage_class = "STANDARD_IA" # Infrequent Access after 30 days
    }

    transition {
      days          = 90
      storage_class = "GLACIER" # Glacier after 90 days
    }

    transition {
      days          = 365
      storage_class = "DEEP_ARCHIVE" # Deep Archive after 1 year
    }

    # Regulatory retention: 7 years (2555 days)
    expiration {
      days = 2555
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# S3 backup destination bucket (different region for 3-2-1 rule)
resource "aws_s3_bucket" "backup_replica" {
  provider = aws.backup

  bucket = "igaming-backup-${lower(var.jurisdiction)}-replica"

  tags = {
    Name         = "igaming-backup-replica"
    DataType     = "backup-offsite"
    Jurisdiction = var.jurisdiction
  }
}

resource "aws_s3_bucket_versioning" "backup_replica" {
  provider = aws.backup
  bucket   = aws_s3_bucket.backup_replica.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backup_replica" {
  provider = aws.backup
  bucket   = aws_s3_bucket.backup_replica.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.backup_s3.arn
    }
    bucket_key_enabled = true
  }
}

# S3 Replication Configuration
resource "aws_s3_bucket_replication_configuration" "backup_replication" {
  provider = aws.primary
  bucket   = aws_s3_bucket.backup_primary.id

  role = aws_iam_role.s3_replication.arn

  rule {
    id     = "replicate-backups"
    status = "Enabled"

    destination {
      bucket        = aws_s3_bucket.backup_replica.arn
      storage_class = "STANDARD_IA"

      encryption_configuration {
        replica_kms_key_id = aws_kms_key.backup_s3.arn
      }
    }

    source_selection_criteria {
      sse_kms_encrypted_objects {
        status = "Enabled"
      }
    }
  }

  depends_on = [
    aws_s3_bucket_versioning.backup_primary,
    aws_s3_bucket_versioning.backup_replica,
  ]
}

# ---------------------------------------------------------------------------
# ElastiCache Global Datastore (Redis)
# ---------------------------------------------------------------------------
resource "aws_elasticache_global_replication_group" "igaming" {
  provider = aws.primary

  global_replication_group_id_suffix   = "igaming-${lower(var.jurisdiction)}"
  primary_replication_group_id         = aws_elasticache_replication_group.primary.id
  global_replication_group_description = "iGaming Redis - ${var.jurisdiction}"
}

resource "aws_elasticache_replication_group" "primary" {
  provider = aws.primary

  replication_group_id = "igaming-${lower(var.jurisdiction)}-primary"
  description          = "iGaming Redis primary - ${var.jurisdiction}"

  node_type                  = "cache.r6g.xlarge"
  num_cache_clusters         = 2 # Primary + 1 replica in same region
  automatic_failover_enabled = true
  multi_az_enabled           = true

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = aws_kms_key.primary_db.arn

  subnet_group_name  = aws_elasticache_subnet_group.primary.name
  security_group_ids = [aws_security_group.redis_primary.id]

  snapshot_retention_limit = 7
  snapshot_window          = "03:00-04:00"
  maintenance_window       = "Mon:04:00-Mon:05:00"

  tags = {
    Name         = "igaming-redis-primary"
    DataTier     = "tier-1"
    Jurisdiction = var.jurisdiction
  }
}

# ---------------------------------------------------------------------------
# IAM Roles
# ---------------------------------------------------------------------------
resource "aws_iam_role" "rds_monitoring" {
  provider = aws.primary
  name     = "igaming-rds-monitoring-${lower(var.jurisdiction)}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "monitoring.rds.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  provider   = aws.primary
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_iam_role" "rds_monitoring_replica" {
  provider = aws.replica
  name     = "igaming-rds-monitoring-${lower(var.jurisdiction)}-replica"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "monitoring.rds.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring_replica" {
  provider   = aws.replica
  role       = aws_iam_role.rds_monitoring_replica.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_iam_role" "s3_replication" {
  provider = aws.primary
  name     = "igaming-s3-replication-${lower(var.jurisdiction)}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "s3.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "s3_replication" {
  provider = aws.primary
  name     = "igaming-s3-replication-policy"
  role     = aws_iam_role.s3_replication.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetReplicationConfiguration",
          "s3:ListBucket",
        ]
        Resource = [aws_s3_bucket.backup_primary.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObjectVersionForReplication",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging",
        ]
        Resource = ["${aws_s3_bucket.backup_primary.arn}/*"]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ReplicateObject",
          "s3:ReplicateDelete",
          "s3:ReplicateTags",
        ]
        Resource = ["${aws_s3_bucket.backup_replica.arn}/*"]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = [
          aws_kms_key.primary_db.arn,
          aws_kms_key.backup_s3.arn,
        ]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Security Groups (placeholders -- customize per VPC)
# ---------------------------------------------------------------------------
resource "aws_security_group" "db_primary" {
  provider = aws.primary
  name     = "igaming-db-primary-${lower(var.jurisdiction)}"
  vpc_id   = data.aws_vpc.primary.id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.primary.cidr_block]
    description = "PostgreSQL from VPC"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "igaming-db-sg-primary-${var.jurisdiction}"
  }
}

resource "aws_security_group" "db_replica" {
  provider = aws.replica
  name     = "igaming-db-replica-${lower(var.jurisdiction)}"
  vpc_id   = data.aws_vpc.replica.id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.replica.cidr_block]
    description = "PostgreSQL from VPC"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "igaming-db-sg-replica-${var.jurisdiction}"
  }
}

resource "aws_security_group" "redis_primary" {
  provider = aws.primary
  name     = "igaming-redis-primary-${lower(var.jurisdiction)}"
  vpc_id   = data.aws_vpc.primary.id

  ingress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.primary.cidr_block]
    description = "Redis from VPC"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "igaming-redis-sg-primary-${var.jurisdiction}"
  }
}

# ---------------------------------------------------------------------------
# Data sources (VPC, subnets -- must exist)
# ---------------------------------------------------------------------------
data "aws_vpc" "primary" {
  provider = aws.primary
  default  = true
}

data "aws_vpc" "replica" {
  provider = aws.replica
  default  = true
}

data "aws_subnets" "primary_private" {
  provider = aws.primary

  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.primary.id]
  }

  tags = {
    Tier = "private"
  }
}

resource "aws_elasticache_subnet_group" "primary" {
  provider   = aws.primary
  name       = "igaming-redis-${lower(var.jurisdiction)}"
  subnet_ids = data.aws_subnets.primary_private.ids
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
output "primary_db_endpoint" {
  value = aws_db_instance.primary.endpoint
}

output "replica_db_endpoint" {
  value = aws_db_instance.cross_region_replica.endpoint
}

output "backup_bucket_primary" {
  value = aws_s3_bucket.backup_primary.id
}

output "backup_bucket_replica" {
  value = aws_s3_bucket.backup_replica.id
}

output "redis_primary_endpoint" {
  value = aws_elasticache_replication_group.primary.primary_endpoint_address
}

output "jurisdiction_config" {
  value = local.config
}
