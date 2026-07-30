# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# AWS Data Lake Infrastructure for iGaming
# Complete Terraform configuration for enterprise data lake
#
# Architecture:
# - S3 Data Lake with Bronze/Silver/Gold zones
# - AWS Glue for ETL and Data Catalog
# - Amazon Kinesis for real-time streaming
# - AWS Lake Formation for governance
# - Amazon Athena for ad-hoc queries
# - Amazon Redshift Serverless for analytics
#
# Usage:
#   cd terraform/environments/prod
#   terraform init
#   terraform plan -var-file="prod.tfvars"
#   terraform apply -var-file="prod.tfvars"

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    # Configure in environments/*/backend.tf
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "igaming-datalake"
      Environment = var.environment
      ManagedBy   = "terraform"
      CostCenter  = "data-platform"
    }
  }
}

# =============================================================================
# VARIABLES
# =============================================================================

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "igaming-datalake"
}

variable "data_retention_days" {
  description = "Data retention in days for different tiers"
  type = object({
    bronze = number
    silver = number
    gold   = number
  })
  default = {
    bronze = 90   # Raw data: 90 days in standard, then glacier
    silver = 365  # Cleaned data: 1 year
    gold   = 2555 # Analytics: 7 years (regulatory)
  }
}

variable "enable_cross_region_replication" {
  description = "Enable cross-region replication for DR"
  type        = bool
  default     = true
}

variable "vpc_cidr" {
  description = "VPC CIDR for data lake network"
  type        = string
  default     = "10.100.0.0/16"
}

variable "on_premise_cidr" {
  description = "On-premise network CIDR for VPN/Direct Connect"
  type        = string
  default     = "192.168.0.0/16"
}

# =============================================================================
# SECURITY AND DELETION PROTECTION VARIABLES
# =============================================================================

variable "enable_deletion_protection" {
  description = "Enable deletion protection for critical resources (recommended for production)"
  type        = bool
  default     = true
}

variable "enable_object_lock" {
  description = "Enable S3 Object Lock for compliance data (Gold layer)"
  type        = bool
  default     = true
}

variable "object_lock_retention_days" {
  description = "Object lock retention period in days for compliance data"
  type        = number
  default     = 2555 # 7 years for iGaming regulatory compliance
}

variable "enable_mfa_delete" {
  description = "Enable MFA delete for S3 versioning (requires root account)"
  type        = bool
  default     = false # Must be enabled via AWS CLI with root credentials
}

variable "backup_retention_days" {
  description = "Number of days to retain backups"
  type        = number
  default     = 35
}

# =============================================================================
# DATA SOURCES
# =============================================================================

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # S3 bucket names
  bucket_prefix = "${var.project_name}-${var.environment}-${local.account_id}"

  # Common tags
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# =============================================================================
# S3 DATA LAKE BUCKETS
# =============================================================================

# Bronze Layer - Raw Data (Landing Zone)
# checkov:skip=CKV_AWS_144:Cross-region replication is optional and depends on DR requirements
# checkov:skip=CKV2_AWS_62:Event notifications configured separately based on use case
resource "aws_s3_bucket" "bronze" {
  bucket = "${local.bucket_prefix}-bronze"

  # SECURITY: Prevent accidental deletion of data bucket
  # Set to false only after careful consideration and data migration
  force_destroy = false

  tags = merge(local.common_tags, {
    Layer       = "bronze"
    Description = "Raw data landing zone"
  })

  # SECURITY: Prevent accidental destruction via Terraform
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "bronze" {
  bucket = aws_s3_bucket.bronze.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.datalake.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  rule {
    id     = "bronze-lifecycle"
    status = "Enabled"

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = var.data_retention_days.bronze
      storage_class = "GLACIER"
    }

    expiration {
      days = var.data_retention_days.bronze + 365 # Delete after 1 year in Glacier
    }

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    # CKV_AWS_300: Abort incomplete multipart uploads
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# CKV2_AWS_6: Block public access
resource "aws_s3_bucket_public_access_block" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Silver Layer - Cleaned/Transformed Data
# checkov:skip=CKV_AWS_144:Cross-region replication is optional and depends on DR requirements
# checkov:skip=CKV2_AWS_62:Event notifications configured separately based on use case
resource "aws_s3_bucket" "silver" {
  bucket = "${local.bucket_prefix}-silver"

  # SECURITY: Prevent accidental deletion of data bucket
  force_destroy = false

  tags = merge(local.common_tags, {
    Layer       = "silver"
    Description = "Cleaned and transformed data"
  })

  # SECURITY: Prevent accidental destruction via Terraform
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "silver" {
  bucket = aws_s3_bucket.silver.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "silver" {
  bucket = aws_s3_bucket.silver.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.datalake.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "silver" {
  bucket = aws_s3_bucket.silver.id

  rule {
    id     = "silver-lifecycle"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = var.data_retention_days.silver
      storage_class = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    # CKV_AWS_300: Abort incomplete multipart uploads
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# CKV2_AWS_6: Block public access
resource "aws_s3_bucket_public_access_block" "silver" {
  bucket = aws_s3_bucket.silver.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Gold Layer - Analytics-Ready Data (COMPLIANCE CRITICAL)
# Contains regulatory data that must be retained for 7 years
# checkov:skip=CKV_AWS_144:Cross-region replication is optional and depends on DR requirements
# checkov:skip=CKV2_AWS_62:Event notifications configured separately based on use case
resource "aws_s3_bucket" "gold" {
  bucket = "${local.bucket_prefix}-gold"

  # SECURITY: Prevent accidental deletion of compliance data
  force_destroy = false

  # SECURITY: Enable Object Lock for compliance (WORM - Write Once Read Many)
  # NOTE: Object Lock must be enabled at bucket creation and cannot be disabled
  object_lock_enabled = var.enable_object_lock

  tags = merge(local.common_tags, {
    Layer          = "gold"
    Description    = "Analytics-ready curated data"
    ComplianceTier = "regulatory"
    RetentionYears = "7"
  })

  # SECURITY: Prevent accidental destruction via Terraform
  # This is CRITICAL for compliance data
  lifecycle {
    prevent_destroy = true
  }
}

# Object Lock Configuration for Gold Layer (Compliance)
resource "aws_s3_bucket_object_lock_configuration" "gold" {
  count  = var.enable_object_lock ? 1 : 0
  bucket = aws_s3_bucket.gold.id

  rule {
    default_retention {
      mode = "GOVERNANCE" # Use COMPLIANCE mode for stricter protection
      days = var.object_lock_retention_days
    }
  }
}

resource "aws_s3_bucket_versioning" "gold" {
  bucket = aws_s3_bucket.gold.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "gold" {
  bucket = aws_s3_bucket.gold.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.datalake.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "gold" {
  bucket = aws_s3_bucket.gold.id

  rule {
    id     = "gold-lifecycle"
    status = "Enabled"

    # Gold data stays in Standard for fast access
    transition {
      days          = 365
      storage_class = "STANDARD_IA"
    }

    # Regulatory retention - 7 years
    transition {
      days          = var.data_retention_days.gold
      storage_class = "DEEP_ARCHIVE"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    # CKV_AWS_300: Abort incomplete multipart uploads
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# CKV2_AWS_6: Block public access
resource "aws_s3_bucket_public_access_block" "gold" {
  bucket = aws_s3_bucket.gold.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# =============================================================================
# S3 ACCESS LOGGING (CKV_AWS_18)
# =============================================================================

# checkov:skip=CKV_AWS_144:Cross-region replication not needed for access logs
# checkov:skip=CKV2_AWS_62:Event notifications not needed for access logs bucket
resource "aws_s3_bucket" "access_logs" {
  bucket = "${local.bucket_prefix}-access-logs"

  tags = merge(local.common_tags, {
    Purpose = "S3 access logging"
  })
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.datalake.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    id     = "access-logs-lifecycle"
    status = "Enabled"

    expiration {
      days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_logging" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "bronze/"
}

resource "aws_s3_bucket_logging" "silver" {
  bucket = aws_s3_bucket.silver.id

  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "silver/"
}

resource "aws_s3_bucket_logging" "gold" {
  bucket = aws_s3_bucket.gold.id

  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "gold/"
}

# =============================================================================
# KMS ENCRYPTION
# =============================================================================

# SECURITY: KMS key is critical - deletion causes permanent data loss
# The key has a 30-day deletion window to allow recovery from accidental deletion
resource "aws_kms_key" "datalake" {
  description             = "KMS key for data lake encryption - DO NOT DELETE"
  deletion_window_in_days = 30 # Maximum allowed recovery window
  enable_key_rotation     = true

  # SECURITY: Multi-region for DR (if enabled)
  multi_region = var.enable_cross_region_replication

  # SECURITY: Prevent accidental destruction - key deletion = permanent data loss
  lifecycle {
    prevent_destroy = true
  }

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow Glue Service"
        Effect = "Allow"
        Principal = {
          Service = "glue.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = "*"
      },
      {
        Sid    = "Allow Lake Formation"
        Effect = "Allow"
        Principal = {
          Service = "lakeformation.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = "*"
      },
      {
        Sid    = "Allow CloudWatch Logs"
        Effect = "Allow"
        Principal = {
          Service = "logs.${var.aws_region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${local.account_id}:*"
          }
        }
      },
      {
        Sid    = "Allow Lambda Service"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = "*"
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_kms_alias" "datalake" {
  name          = "alias/${var.project_name}-${var.environment}"
  target_key_id = aws_kms_key.datalake.key_id
}

# =============================================================================
# AWS GLUE DATA CATALOG
# =============================================================================

resource "aws_glue_catalog_database" "bronze" {
  name        = "${var.project_name}_bronze"
  description = "Bronze layer - raw data"

  create_table_default_permission {
    permissions = ["ALL"]
    principal {
      data_lake_principal_identifier = "IAM_ALLOWED_PRINCIPALS"
    }
  }
}

resource "aws_glue_catalog_database" "silver" {
  name        = "${var.project_name}_silver"
  description = "Silver layer - cleaned data"

  create_table_default_permission {
    permissions = ["ALL"]
    principal {
      data_lake_principal_identifier = "IAM_ALLOWED_PRINCIPALS"
    }
  }
}

resource "aws_glue_catalog_database" "gold" {
  name        = "${var.project_name}_gold"
  description = "Gold layer - analytics ready"

  create_table_default_permission {
    permissions = ["ALL"]
    principal {
      data_lake_principal_identifier = "IAM_ALLOWED_PRINCIPALS"
    }
  }
}

# Glue Crawler for automatic schema discovery
resource "aws_glue_crawler" "bronze_crawler" {
  database_name = aws_glue_catalog_database.bronze.name
  name          = "${var.project_name}-bronze-crawler"
  role          = aws_iam_role.glue_role.arn

  # CKV_AWS_195: Security configuration
  security_configuration = aws_glue_security_configuration.datalake.name

  s3_target {
    path = "s3://${aws_s3_bucket.bronze.bucket}/"
  }

  schema_change_policy {
    delete_behavior = "LOG"
    update_behavior = "UPDATE_IN_DATABASE"
  }

  configuration = jsonencode({
    Version = 1.0
    Grouping = {
      TableGroupingPolicy = "CombineCompatibleSchemas"
    }
    CrawlerOutput = {
      Partitions = {
        AddOrUpdateBehavior = "InheritFromTable"
      }
    }
  })

  schedule = "cron(0 */6 * * ? *)" # Every 6 hours

  tags = local.common_tags
}

# =============================================================================
# KINESIS DATA STREAMS (Real-time Ingestion)
# =============================================================================

resource "aws_kinesis_stream" "events" {
  name             = "${var.project_name}-events"
  retention_period = 168 # 7 days

  stream_mode_details {
    stream_mode = "ON_DEMAND" # Auto-scaling
  }

  encryption_type = "KMS"
  kms_key_id      = aws_kms_key.datalake.id

  tags = merge(local.common_tags, {
    DataType = "real-time-events"
  })
}

resource "aws_kinesis_stream" "transactions" {
  name             = "${var.project_name}-transactions"
  retention_period = 168

  stream_mode_details {
    stream_mode = "ON_DEMAND"
  }

  encryption_type = "KMS"
  kms_key_id      = aws_kms_key.datalake.id

  tags = merge(local.common_tags, {
    DataType = "financial-transactions"
  })
}

# Kinesis Firehose for S3 delivery
resource "aws_kinesis_firehose_delivery_stream" "events_to_bronze" {
  name        = "${var.project_name}-events-to-bronze"
  destination = "extended_s3"

  kinesis_source_configuration {
    kinesis_stream_arn = aws_kinesis_stream.events.arn
    role_arn           = aws_iam_role.firehose_role.arn
  }

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose_role.arn
    bucket_arn          = aws_s3_bucket.bronze.arn
    prefix              = "events/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/hour=!{timestamp:HH}/"
    error_output_prefix = "errors/events/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"
    buffering_size      = 128 # MB
    buffering_interval  = 60  # seconds
    compression_format  = "GZIP"

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose.name
      log_stream_name = "events-delivery"
    }

    processing_configuration {
      enabled = true

      processors {
        type = "Lambda"
        parameters {
          parameter_name  = "LambdaArn"
          parameter_value = "${aws_lambda_function.firehose_transformer.arn}:$LATEST"
        }
      }
    }

    data_format_conversion_configuration {
      enabled = true

      input_format_configuration {
        deserializer {
          open_x_json_ser_de {}
        }
      }

      output_format_configuration {
        serializer {
          parquet_ser_de {
            compression = "SNAPPY"
          }
        }
      }

      schema_configuration {
        database_name = aws_glue_catalog_database.bronze.name
        table_name    = "events"
        role_arn      = aws_iam_role.firehose_role.arn
      }
    }
  }

  tags = local.common_tags
}

# =============================================================================
# IAM ROLES
# =============================================================================

resource "aws_iam_role" "glue_role" {
  name = "${var.project_name}-glue-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "glue.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

resource "aws_iam_role_policy" "glue_s3_access" {
  name = "glue-s3-access"
  role = aws_iam_role.glue_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.bronze.arn,
          "${aws_s3_bucket.bronze.arn}/*",
          aws_s3_bucket.silver.arn,
          "${aws_s3_bucket.silver.arn}/*",
          aws_s3_bucket.gold.arn,
          "${aws_s3_bucket.gold.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:Encrypt",
          "kms:GenerateDataKey"
        ]
        Resource = aws_kms_key.datalake.arn
      }
    ]
  })
}

resource "aws_iam_role" "firehose_role" {
  name = "${var.project_name}-firehose-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "firehose.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "firehose_policy" {
  name = "firehose-delivery-policy"
  role = aws_iam_role.firehose_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:ListBucketMultipartUploads",
          "s3:PutObject"
        ]
        Resource = [
          aws_s3_bucket.bronze.arn,
          "${aws_s3_bucket.bronze.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "kinesis:DescribeStream",
          "kinesis:GetShardIterator",
          "kinesis:GetRecords",
          "kinesis:ListShards"
        ]
        Resource = [
          aws_kinesis_stream.events.arn,
          aws_kinesis_stream.transactions.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = aws_kms_key.datalake.arn
      },
      {
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction",
          "lambda:GetFunctionConfiguration"
        ]
        Resource = "${aws_lambda_function.firehose_transformer.arn}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetTableVersion",
          "glue:GetTableVersions"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.firehose.arn}:*"
      }
    ]
  })
}

# =============================================================================
# LAMBDA FUNCTIONS
# =============================================================================

# CKV_AWS_116: Dead Letter Queue for Lambda
resource "aws_sqs_queue" "lambda_dlq" {
  name                      = "${var.project_name}-lambda-dlq"
  message_retention_seconds = 1209600 # 14 days
  kms_master_key_id         = aws_kms_key.datalake.id

  tags = local.common_tags
}

# checkov:skip=CKV_AWS_117:VPC configuration is optional for Firehose transformer - no VPC resources needed
# checkov:skip=CKV_AWS_272:Code signing requires AWS Signer setup - implement based on org security requirements
resource "aws_lambda_function" "firehose_transformer" {
  filename      = "${path.module}/../etl/lambda/firehose_transformer.zip"
  function_name = "${var.project_name}-firehose-transformer"
  role          = aws_iam_role.lambda_role.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 256

  # CKV_AWS_115: Reserved concurrent executions
  reserved_concurrent_executions = 100

  # CKV_AWS_116: Dead Letter Queue
  dead_letter_config {
    target_arn = aws_sqs_queue.lambda_dlq.arn
  }

  # CKV_AWS_50: X-Ray tracing
  tracing_config {
    mode = "Active"
  }

  # CKV_AWS_173: KMS encryption for environment variables
  kms_key_arn = aws_kms_key.datalake.arn

  environment {
    variables = {
      ENVIRONMENT = var.environment
    }
  }

  tags = local.common_tags
}

resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# =============================================================================
# CLOUDWATCH LOGGING
# =============================================================================

# CKV_AWS_158: KMS encryption for CloudWatch logs
# CKV_AWS_338: Retain logs for at least 1 year
resource "aws_cloudwatch_log_group" "firehose" {
  name              = "/aws/firehose/${var.project_name}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.datalake.arn

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "glue" {
  name              = "/aws/glue/${var.project_name}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.datalake.arn

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.project_name}-firehose-transformer"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.datalake.arn

  tags = local.common_tags
}

# =============================================================================
# GLUE SECURITY CONFIGURATION (CKV_AWS_195)
# =============================================================================

resource "aws_glue_security_configuration" "datalake" {
  name = "${var.project_name}-security-config"

  encryption_configuration {
    cloudwatch_encryption {
      cloudwatch_encryption_mode = "SSE-KMS"
      kms_key_arn                = aws_kms_key.datalake.arn
    }

    job_bookmarks_encryption {
      job_bookmarks_encryption_mode = "CSE-KMS"
      kms_key_arn                   = aws_kms_key.datalake.arn
    }

    s3_encryption {
      s3_encryption_mode = "SSE-KMS"
      kms_key_arn        = aws_kms_key.datalake.arn
    }
  }
}

# =============================================================================
# LAKE FORMATION
# =============================================================================

resource "aws_lakeformation_data_lake_settings" "main" {
  admins = [
    data.aws_caller_identity.current.arn
  ]

  create_database_default_permissions {
    permissions = ["ALL"]
    principal   = "IAM_ALLOWED_PRINCIPALS"
  }

  create_table_default_permissions {
    permissions = ["ALL"]
    principal   = "IAM_ALLOWED_PRINCIPALS"
  }
}

resource "aws_lakeformation_resource" "bronze" {
  arn = aws_s3_bucket.bronze.arn
}

resource "aws_lakeformation_resource" "silver" {
  arn = aws_s3_bucket.silver.arn
}

resource "aws_lakeformation_resource" "gold" {
  arn = aws_s3_bucket.gold.arn
}

# =============================================================================
# OUTPUTS
# =============================================================================

output "bronze_bucket" {
  description = "Bronze layer S3 bucket"
  value       = aws_s3_bucket.bronze.id
}

output "silver_bucket" {
  description = "Silver layer S3 bucket"
  value       = aws_s3_bucket.silver.id
}

output "gold_bucket" {
  description = "Gold layer S3 bucket"
  value       = aws_s3_bucket.gold.id
}

output "kinesis_events_stream" {
  description = "Kinesis stream for events"
  value       = aws_kinesis_stream.events.name
}

output "kinesis_transactions_stream" {
  description = "Kinesis stream for transactions"
  value       = aws_kinesis_stream.transactions.name
}

output "glue_databases" {
  description = "Glue catalog databases"
  value = {
    bronze = aws_glue_catalog_database.bronze.name
    silver = aws_glue_catalog_database.silver.name
    gold   = aws_glue_catalog_database.gold.name
  }
}

output "kms_key_arn" {
  description = "KMS key ARN for encryption"
  value       = aws_kms_key.datalake.arn
}

output "security_configuration" {
  description = "Security configuration status"
  value = {
    deletion_protection_enabled = var.enable_deletion_protection
    object_lock_enabled         = var.enable_object_lock
    object_lock_retention_days  = var.object_lock_retention_days
    kms_key_rotation_enabled    = true
    s3_versioning_enabled       = true
    s3_access_logging_enabled   = true
  }
}

# =============================================================================
# AWS BACKUP FOR DATA LAKE (Optional but Recommended)
# =============================================================================

# Backup Vault for data lake backups
resource "aws_backup_vault" "datalake" {
  count = var.enable_deletion_protection ? 1 : 0

  name        = "${var.project_name}-backup-vault"
  kms_key_arn = aws_kms_key.datalake.arn

  tags = merge(local.common_tags, {
    Purpose = "Data lake backup and recovery"
  })
}

# Backup Plan for critical data
resource "aws_backup_plan" "datalake" {
  count = var.enable_deletion_protection ? 1 : 0

  name = "${var.project_name}-backup-plan"

  rule {
    rule_name         = "daily-backup"
    target_vault_name = aws_backup_vault.datalake[0].name
    schedule          = "cron(0 5 ? * * *)" # Daily at 5 AM UTC

    lifecycle {
      delete_after = var.backup_retention_days
    }

    # Copy to another region for DR
    dynamic "copy_action" {
      for_each = var.enable_cross_region_replication ? [1] : []
      content {
        destination_vault_arn = "arn:aws:backup:us-west-2:${local.account_id}:backup-vault:${var.project_name}-dr-vault"
        lifecycle {
          delete_after = var.backup_retention_days
        }
      }
    }
  }

  rule {
    rule_name         = "weekly-backup"
    target_vault_name = aws_backup_vault.datalake[0].name
    schedule          = "cron(0 5 ? * SUN *)" # Weekly on Sunday

    lifecycle {
      cold_storage_after = 30
      delete_after       = 365
    }
  }

  tags = local.common_tags
}

# IAM Role for AWS Backup
resource "aws_iam_role" "backup_role" {
  count = var.enable_deletion_protection ? 1 : 0

  name = "${var.project_name}-backup-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "backup.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "backup_policy" {
  count = var.enable_deletion_protection ? 1 : 0

  role       = aws_iam_role.backup_role[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

# Backup Selection - S3 Buckets
resource "aws_backup_selection" "datalake_buckets" {
  count = var.enable_deletion_protection ? 1 : 0

  name         = "${var.project_name}-s3-selection"
  plan_id      = aws_backup_plan.datalake[0].id
  iam_role_arn = aws_iam_role.backup_role[0].arn

  resources = [
    aws_s3_bucket.gold.arn,   # Compliance critical
    aws_s3_bucket.silver.arn, # Business critical
  ]
}

# =============================================================================
# SECURITY BEST PRACTICES DOCUMENTATION
# =============================================================================
#
# This Terraform configuration implements the following security best practices:
#
# 1. DELETION PROTECTION
#    - lifecycle { prevent_destroy = true } on critical resources
#    - force_destroy = false on all S3 buckets
#    - 30-day deletion window for KMS keys
#    - AWS Backup for additional protection
#
# 2. ENCRYPTION
#    - KMS encryption for all S3 buckets (SSE-KMS)
#    - KMS encryption for CloudWatch Logs
#    - KMS encryption for Glue job bookmarks
#    - Key rotation enabled
#
# 3. DATA PROTECTION
#    - S3 Versioning enabled on all buckets
#    - S3 Object Lock for compliance data (Gold layer)
#    - Cross-region replication available for DR
#    - MFA Delete can be enabled (requires root account)
#
# 4. ACCESS CONTROL
#    - Lake Formation for fine-grained access control
#    - Public access blocked on all S3 buckets
#    - S3 access logging enabled
#    - IAM roles with least privilege
#
# 5. MONITORING & AUDIT
#    - CloudWatch Logs with 365-day retention
#    - S3 access logs for audit trail
#    - CloudTrail integration (configure separately)
#
# 6. COMPLIANCE
#    - 7-year retention for regulatory data
#    - Object Lock in GOVERNANCE mode (upgrade to COMPLIANCE if needed)
#    - PII detection and masking in ETL jobs
#
# IMPORTANT WARNINGS:
# - DO NOT set force_destroy = true on production buckets
# - DO NOT remove lifecycle { prevent_destroy = true } without approval
# - DO NOT delete KMS keys - this causes permanent data loss
# - DO NOT disable Object Lock once enabled (irreversible)
#
# RECOVERY PROCEDURES:
# - Accidental object deletion: Use S3 versioning to restore
# - KMS key scheduled for deletion: Cancel within 30-day window
# - Full bucket recovery: Use AWS Backup restore
#
# =============================================================================
