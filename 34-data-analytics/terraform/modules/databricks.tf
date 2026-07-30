# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# DATABRICKS ON AWS - TERRAFORM CONFIGURATION
# =============================================================================
#
# This module provisions Databricks workspace on AWS with:
# - E2 Architecture (Customer-managed VPC)
# - Unity Catalog for data governance
# - Delta Lake integration with existing S3 data lake
# - Cluster policies for cost control
# - Instance pools for faster cluster startup
#
# Prerequisites:
# - Databricks account with E2 enabled
# - AWS account with appropriate permissions
# - Existing VPC and subnets (from networking.tf)
#
# =============================================================================

terraform {
  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.30"
    }
  }
}

# =============================================================================
# VARIABLES
# =============================================================================

variable "databricks_account_id" {
  description = "Databricks account ID"
  type        = string
}

variable "databricks_workspace_name" {
  description = "Name for the Databricks workspace"
  type        = string
  default     = "igaming-datalake-workspace"
}

variable "enable_databricks" {
  description = "Enable Databricks workspace deployment"
  type        = bool
  default     = true
}

variable "databricks_pricing_tier" {
  description = "Databricks pricing tier: STANDARD, PREMIUM, or ENTERPRISE"
  type        = string
  default     = "PREMIUM" # Required for Unity Catalog

  validation {
    condition     = contains(["STANDARD", "PREMIUM", "ENTERPRISE"], var.databricks_pricing_tier)
    error_message = "Pricing tier must be STANDARD, PREMIUM, or ENTERPRISE."
  }
}

variable "cluster_node_types" {
  description = "Node types for different cluster purposes"
  type = object({
    etl_driver  = string
    etl_worker  = string
    analytics   = string
    ml_training = string
  })
  default = {
    etl_driver  = "m5.xlarge"   # 4 vCPU, 16 GB - ETL driver
    etl_worker  = "m5.2xlarge"  # 8 vCPU, 32 GB - ETL workers
    analytics   = "r5.xlarge"   # 4 vCPU, 32 GB - Memory optimized
    ml_training = "g4dn.xlarge" # 4 vCPU, 16 GB + GPU - ML training
  }
}

variable "max_cluster_workers" {
  description = "Maximum workers for auto-scaling clusters"
  type = object({
    etl       = number
    analytics = number
    ml        = number
  })
  default = {
    etl       = 10
    analytics = 5
    ml        = 8
  }
}

variable "enable_unity_catalog" {
  description = "Enable Unity Catalog for data governance"
  type        = bool
  default     = true
}

# =============================================================================
# DATA SOURCES
# =============================================================================

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # Databricks-specific tags
  databricks_tags = {
    Vendor      = "databricks"
    ManagedBy   = "terraform"
    Environment = var.environment
    Project     = var.project_name
  }
}

# =============================================================================
# IAM ROLE FOR DATABRICKS CROSS-ACCOUNT ACCESS
# =============================================================================

# checkov:skip=CKV_AWS_61:Cross-account access required by Databricks E2 architecture
resource "aws_iam_role" "databricks_cross_account" {
  count = var.enable_databricks ? 1 : 0

  name = "${var.project_name}-databricks-cross-account"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::414351767826:root" # Databricks AWS account
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = var.databricks_account_id
          }
        }
      }
    ]
  })

  tags = local.databricks_tags
}

# checkov:skip=CKV_AWS_290:Wide permissions required by Databricks for EC2 management
# checkov:skip=CKV_AWS_355:Databricks requires describe permissions on all EC2 resources
resource "aws_iam_role_policy" "databricks_cross_account" {
  count = var.enable_databricks ? 1 : 0

  name = "${var.project_name}-databricks-cross-account-policy"
  role = aws_iam_role.databricks_cross_account[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "NonResourceBasedPermissions"
        Effect = "Allow"
        Action = [
          "ec2:CancelSpotInstanceRequests",
          "ec2:DescribeAvailabilityZones",
          "ec2:DescribeIamInstanceProfileAssociations",
          "ec2:DescribeInstanceStatus",
          "ec2:DescribeInstances",
          "ec2:DescribeInternetGateways",
          "ec2:DescribeNatGateways",
          "ec2:DescribeNetworkAcls",
          "ec2:DescribePrefixLists",
          "ec2:DescribeReservedInstancesOfferings",
          "ec2:DescribeRouteTables",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSpotInstanceRequests",
          "ec2:DescribeSpotPriceHistory",
          "ec2:DescribeSubnets",
          "ec2:DescribeVolumes",
          "ec2:DescribeVpcAttribute",
          "ec2:DescribeVpcs",
          "ec2:CreateTags",
          "ec2:DeleteTags",
          "ec2:RequestSpotInstances"
        ]
        Resource = "*"
      },
      {
        Sid    = "InstancePoolsSupport"
        Effect = "Allow"
        Action = [
          "ec2:AssociateIamInstanceProfile",
          "ec2:DisassociateIamInstanceProfile",
          "ec2:ReplaceIamInstanceProfileAssociation"
        ]
        Resource = "arn:aws:ec2:${local.region}:${local.account_id}:instance/*"
        Condition = {
          StringEquals = {
            "ec2:ResourceTag/Vendor" = "Databricks"
          }
        }
      },
      {
        Sid    = "AllowEc2RunInstances"
        Effect = "Allow"
        Action = "ec2:RunInstances"
        Resource = [
          "arn:aws:ec2:${local.region}:${local.account_id}:volume/*",
          "arn:aws:ec2:${local.region}:${local.account_id}:instance/*",
          "arn:aws:ec2:${local.region}:${local.account_id}:network-interface/*",
          "arn:aws:ec2:${local.region}:${local.account_id}:security-group/*",
          "arn:aws:ec2:${local.region}::image/*"
        ]
      },
      {
        Sid      = "AllowEc2RunInstancesSubnet"
        Effect   = "Allow"
        Action   = "ec2:RunInstances"
        Resource = "arn:aws:ec2:${local.region}:${local.account_id}:subnet/*"
      },
      {
        Sid    = "EC2TerminateInstances"
        Effect = "Allow"
        Action = [
          "ec2:TerminateInstances"
        ]
        Resource = "arn:aws:ec2:${local.region}:${local.account_id}:instance/*"
        Condition = {
          StringEquals = {
            "ec2:ResourceTag/Vendor" = "Databricks"
          }
        }
      },
      {
        Sid    = "EC2AttachDetachVolume"
        Effect = "Allow"
        Action = [
          "ec2:AttachVolume",
          "ec2:DetachVolume"
        ]
        Resource = [
          "arn:aws:ec2:${local.region}:${local.account_id}:instance/*",
          "arn:aws:ec2:${local.region}:${local.account_id}:volume/*"
        ]
        Condition = {
          StringEquals = {
            "ec2:ResourceTag/Vendor" = "Databricks"
          }
        }
      },
      {
        Sid      = "EC2CreateVolume"
        Effect   = "Allow"
        Action   = "ec2:CreateVolume"
        Resource = "arn:aws:ec2:${local.region}:${local.account_id}:volume/*"
        Condition = {
          StringEquals = {
            "ec2:ResourceTag/Vendor" = "Databricks"
          }
        }
      },
      {
        Sid      = "EC2DeleteVolume"
        Effect   = "Allow"
        Action   = "ec2:DeleteVolume"
        Resource = "arn:aws:ec2:${local.region}:${local.account_id}:volume/*"
        Condition = {
          StringEquals = {
            "ec2:ResourceTag/Vendor" = "Databricks"
          }
        }
      },
      {
        Sid      = "PassRoleForInstanceProfile"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "arn:aws:iam::${local.account_id}:role/${var.project_name}-databricks-*"
      },
      {
        Sid      = "CreateServiceLinkedRole"
        Effect   = "Allow"
        Action   = "iam:CreateServiceLinkedRole"
        Resource = "arn:aws:iam::*:role/aws-service-role/spot.amazonaws.com/AWSServiceRoleForEC2Spot"
        Condition = {
          StringEquals = {
            "iam:AWSServiceName" = "spot.amazonaws.com"
          }
        }
      }
    ]
  })
}

# =============================================================================
# S3 BUCKET FOR DATABRICKS WORKSPACE (DBFS ROOT)
# =============================================================================

# checkov:skip=CKV_AWS_144:Cross-region replication optional for Databricks DBFS
# checkov:skip=CKV2_AWS_62:Event notifications optional for Databricks DBFS
resource "aws_s3_bucket" "databricks_root" {
  count = var.enable_databricks ? 1 : 0

  bucket = "${var.project_name}-${var.environment}-databricks-root-${local.account_id}"

  force_destroy = false

  tags = merge(local.databricks_tags, {
    Purpose = "Databricks DBFS root storage"
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "databricks_root" {
  count  = var.enable_databricks ? 1 : 0
  bucket = aws_s3_bucket.databricks_root[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "databricks_root" {
  count  = var.enable_databricks ? 1 : 0
  bucket = aws_s3_bucket.databricks_root[0].id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.datalake.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "databricks_root" {
  count  = var.enable_databricks ? 1 : 0
  bucket = aws_s3_bucket.databricks_root[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_logging" "databricks_root" {
  count  = var.enable_databricks ? 1 : 0
  bucket = aws_s3_bucket.databricks_root[0].id

  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "databricks-root/"
}

resource "aws_s3_bucket_lifecycle_configuration" "databricks_root" {
  count  = var.enable_databricks ? 1 : 0
  bucket = aws_s3_bucket.databricks_root[0].id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "transition-to-ia"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
  }
}

# =============================================================================
# S3 BUCKET FOR UNITY CATALOG METASTORE
# =============================================================================

# checkov:skip=CKV_AWS_144:Cross-region replication optional for Unity Catalog
# checkov:skip=CKV2_AWS_62:Event notifications optional for Unity Catalog
resource "aws_s3_bucket" "unity_catalog" {
  count = var.enable_databricks && var.enable_unity_catalog ? 1 : 0

  bucket = "${var.project_name}-${var.environment}-unity-catalog-${local.account_id}"

  force_destroy = false

  tags = merge(local.databricks_tags, {
    Purpose = "Unity Catalog metastore storage"
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "unity_catalog" {
  count  = var.enable_databricks && var.enable_unity_catalog ? 1 : 0
  bucket = aws_s3_bucket.unity_catalog[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "unity_catalog" {
  count  = var.enable_databricks && var.enable_unity_catalog ? 1 : 0
  bucket = aws_s3_bucket.unity_catalog[0].id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.datalake.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "unity_catalog" {
  count  = var.enable_databricks && var.enable_unity_catalog ? 1 : 0
  bucket = aws_s3_bucket.unity_catalog[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_logging" "unity_catalog" {
  count  = var.enable_databricks && var.enable_unity_catalog ? 1 : 0
  bucket = aws_s3_bucket.unity_catalog[0].id

  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "unity-catalog/"
}

resource "aws_s3_bucket_lifecycle_configuration" "unity_catalog" {
  count  = var.enable_databricks && var.enable_unity_catalog ? 1 : 0
  bucket = aws_s3_bucket.unity_catalog[0].id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "transition-to-ia"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
  }
}

# =============================================================================
# IAM ROLE FOR DATABRICKS DATA ACCESS (INSTANCE PROFILE)
# =============================================================================

resource "aws_iam_role" "databricks_data_access" {
  count = var.enable_databricks ? 1 : 0

  name = "${var.project_name}-databricks-data-access"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.databricks_tags
}

resource "aws_iam_role_policy" "databricks_s3_access" {
  count = var.enable_databricks ? 1 : 0

  name = "${var.project_name}-databricks-s3-access"
  role = aws_iam_role.databricks_data_access[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ListBuckets"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = [
          aws_s3_bucket.bronze.arn,
          aws_s3_bucket.silver.arn,
          aws_s3_bucket.gold.arn,
          aws_s3_bucket.databricks_root[0].arn
        ]
      },
      {
        Sid    = "ReadWriteObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:GetObjectVersion"
        ]
        Resource = [
          "${aws_s3_bucket.bronze.arn}/*",
          "${aws_s3_bucket.silver.arn}/*",
          "${aws_s3_bucket.gold.arn}/*",
          "${aws_s3_bucket.databricks_root[0].arn}/*"
        ]
      },
      {
        Sid    = "KMSAccess"
        Effect = "Allow"
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = aws_kms_key.datalake.arn
      },
      {
        Sid    = "GlueAccess"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase*",
          "glue:GetTable*",
          "glue:GetPartition*",
          "glue:CreateTable",
          "glue:UpdateTable",
          "glue:DeleteTable",
          "glue:BatchCreatePartition",
          "glue:BatchDeletePartition"
        ]
        Resource = [
          "arn:aws:glue:${local.region}:${local.account_id}:catalog",
          "arn:aws:glue:${local.region}:${local.account_id}:database/*",
          "arn:aws:glue:${local.region}:${local.account_id}:table/*"
        ]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "databricks_data_access" {
  count = var.enable_databricks ? 1 : 0

  name = "${var.project_name}-databricks-data-access"
  role = aws_iam_role.databricks_data_access[0].name
}

# =============================================================================
# SECURITY GROUP FOR DATABRICKS CLUSTERS
# =============================================================================

# checkov:skip=CKV2_AWS_5:Security group attached via databricks_mws_networks when workspace is created
# checkov:skip=CKV_AWS_23:Description provided at security group level, individual rules have descriptions
resource "aws_security_group" "databricks" {
  count = var.enable_databricks ? 1 : 0

  name        = "${var.project_name}-databricks-sg"
  description = "Security group for Databricks clusters - allows internal cluster communication and outbound access to Databricks control plane"
  vpc_id      = aws_vpc.datalake.id

  # Allow all internal traffic within the security group
  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
    description = "Allow all internal cluster communication"
  }

  # Allow outbound to internet (for package downloads, etc.)
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS outbound"
  }

  egress {
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "Databricks metastore"
  }

  egress {
    from_port   = 6666
    to_port     = 6666
    protocol    = "tcp"
    self        = true
    description = "Databricks internal"
  }

  egress {
    from_port   = 2443
    to_port     = 2443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Databricks secure cluster connectivity"
  }

  egress {
    from_port   = 8443
    to_port     = 8451
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Databricks control plane"
  }

  tags = merge(local.databricks_tags, {
    Name = "${var.project_name}-databricks-sg"
  })
}

# =============================================================================
# DATABRICKS WORKSPACE (requires Databricks provider configuration)
# =============================================================================

# Note: The actual workspace creation requires the Databricks provider
# to be configured with account-level credentials. This is typically done
# in a separate Terraform configuration or via Databricks Account Console.

# Uncomment and configure when ready to deploy:

# provider "databricks" {
#   alias      = "mws"
#   host       = "https://accounts.cloud.databricks.com"
#   account_id = var.databricks_account_id
#   # Configure authentication via environment variables:
#   # DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET
# }

# resource "databricks_mws_credentials" "this" {
#   provider         = databricks.mws
#   account_id       = var.databricks_account_id
#   credentials_name = "${var.project_name}-credentials"
#   role_arn         = aws_iam_role.databricks_cross_account[0].arn
# }

# resource "databricks_mws_storage_configurations" "this" {
#   provider                   = databricks.mws
#   account_id                 = var.databricks_account_id
#   storage_configuration_name = "${var.project_name}-storage"
#   bucket_name                = aws_s3_bucket.databricks_root[0].id
# }

# resource "databricks_mws_networks" "this" {
#   provider           = databricks.mws
#   account_id         = var.databricks_account_id
#   network_name       = "${var.project_name}-network"
#   vpc_id             = aws_vpc.datalake.id
#   subnet_ids         = aws_subnet.private[*].id
#   security_group_ids = [aws_security_group.databricks[0].id]
# }

# resource "databricks_mws_workspaces" "this" {
#   provider        = databricks.mws
#   account_id      = var.databricks_account_id
#   workspace_name  = var.databricks_workspace_name
#   aws_region      = local.region
#
#   credentials_id           = databricks_mws_credentials.this.credentials_id
#   storage_configuration_id = databricks_mws_storage_configurations.this.storage_configuration_id
#   network_id               = databricks_mws_networks.this.network_id
#
#   pricing_tier = var.databricks_pricing_tier
# }

# =============================================================================
# OUTPUTS
# =============================================================================

output "databricks_cross_account_role_arn" {
  description = "ARN of the cross-account IAM role for Databricks"
  value       = var.enable_databricks ? aws_iam_role.databricks_cross_account[0].arn : null
}

output "databricks_instance_profile_arn" {
  description = "ARN of the instance profile for Databricks data access"
  value       = var.enable_databricks ? aws_iam_instance_profile.databricks_data_access[0].arn : null
}

output "databricks_root_bucket" {
  description = "S3 bucket for Databricks DBFS root"
  value       = var.enable_databricks ? aws_s3_bucket.databricks_root[0].id : null
}

output "unity_catalog_bucket" {
  description = "S3 bucket for Unity Catalog metastore"
  value       = var.enable_databricks && var.enable_unity_catalog ? aws_s3_bucket.unity_catalog[0].id : null
}

output "databricks_security_group_id" {
  description = "Security group ID for Databricks clusters"
  value       = var.enable_databricks ? aws_security_group.databricks[0].id : null
}

# =============================================================================
# COST ESTIMATION - DATABRICKS ON AWS
# =============================================================================
#
# Databricks Pricing (as of 2024, US regions):
#
# COMPUTE (DBU - Databricks Units):
# ┌─────────────────────┬──────────────────┬───────────────┬────────────────┐
# │ Workload            │ STANDARD         │ PREMIUM       │ ENTERPRISE     │
# ├─────────────────────┼──────────────────┼───────────────┼────────────────┤
# │ Jobs Compute        │ $0.07/DBU        │ $0.10/DBU     │ $0.14/DBU      │
# │ Jobs Compute Light  │ $0.07/DBU        │ $0.07/DBU     │ $0.10/DBU      │
# │ All-Purpose Compute │ $0.40/DBU        │ $0.55/DBU     │ $0.65/DBU      │
# │ Delta Live Tables   │ $0.20/DBU (Core) │ $0.25/DBU     │ $0.30/DBU      │
# │ SQL Compute         │ N/A              │ $0.22/DBU     │ $0.22/DBU      │
# │ SQL Serverless      │ N/A              │ $0.70/DBU     │ $0.70/DBU      │
# │ Model Serving       │ N/A              │ $0.07/DBU     │ $0.07/DBU      │
# └─────────────────────┴──────────────────┴───────────────┴────────────────┘
#
# DBU CONSUMPTION BY INSTANCE TYPE:
# ┌──────────────────┬───────────┬────────────┬──────────────────────────────┐
# │ Instance Type    │ vCPU      │ Memory     │ DBU/Hour                     │
# ├──────────────────┼───────────┼────────────┼──────────────────────────────┤
# │ m5.large         │ 2         │ 8 GB       │ 0.75                         │
# │ m5.xlarge        │ 4         │ 16 GB      │ 1.5                          │
# │ m5.2xlarge       │ 8         │ 32 GB      │ 3.0                          │
# │ m5.4xlarge       │ 16        │ 64 GB      │ 6.0                          │
# │ r5.xlarge        │ 4         │ 32 GB      │ 1.5                          │
# │ r5.2xlarge       │ 8         │ 64 GB      │ 3.0                          │
# │ i3.xlarge        │ 4         │ 30.5 GB    │ 2.0 (storage optimized)      │
# │ g4dn.xlarge      │ 4         │ 16 GB      │ 2.5 (GPU)                    │
# └──────────────────┴───────────┴────────────┴──────────────────────────────┘
#
# EXAMPLE MONTHLY COST (PREMIUM tier, production iGaming workload):
#
# ETL Workload:
#   - 1x m5.xlarge driver (1.5 DBU) × 8 hrs/day × 30 days = 360 DBU
#   - 5x m5.2xlarge workers (3.0 DBU × 5 = 15 DBU) × 8 hrs/day × 30 days = 3,600 DBU
#   - Jobs Compute: 3,960 DBU × $0.10 = $396/month
#   - EC2 costs: ~$500/month (On-Demand), ~$300/month (Spot)
#
# Analytics/BI:
#   - SQL Warehouse (Medium): 12 DBU/hr × 10 hrs/day × 22 days = 2,640 DBU
#   - SQL Compute: 2,640 DBU × $0.22 = $581/month
#
# ML Training (occasional):
#   - 1x g4dn.xlarge driver (2.5 DBU) + 4x workers (10 DBU) × 20 hrs/month = 250 DBU
#   - All-Purpose: 250 DBU × $0.55 = $138/month
#
# Unity Catalog & Governance:
#   - Included in PREMIUM tier (no additional DBU cost)
#   - S3 storage: ~$50/month for metadata
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ TOTAL MONTHLY ESTIMATE (PREMIUM)                                       │
# ├───────────────────────────────────┬─────────────────────────────────────┤
# │ ETL Jobs (Databricks)             │ $396                                │
# │ ETL Jobs (EC2 Spot)               │ $300                                │
# │ SQL Analytics                     │ $581                                │
# │ ML Training                       │ $138                                │
# │ S3 Storage (DBFS + Unity)         │ $100                                │
# │ Data Transfer                     │ $50                                 │
# ├───────────────────────────────────┼─────────────────────────────────────┤
# │ TOTAL                             │ $1,565/month                        │
# │ Annual Estimate                   │ ~$18,780/year                       │
# └───────────────────────────────────┴─────────────────────────────────────┘
#
# COST OPTIMIZATION TIPS:
# 1. Use Spot instances for ETL workers (50-70% savings)
# 2. Use Jobs Compute instead of All-Purpose for production pipelines
# 3. Enable auto-termination on clusters (default: 120 minutes)
# 4. Use instance pools to reduce cluster startup time
# 5. Schedule clusters to run only during business hours
# 6. Use serverless SQL for unpredictable query patterns
# 7. Photon engine can reduce DBU consumption by 30-50%
#
# COMPARISON WITH AWS GLUE:
# ┌───────────────────────┬───────────────────┬───────────────────────────────┐
# │ Metric                │ AWS Glue          │ Databricks                    │
# ├───────────────────────┼───────────────────┼───────────────────────────────┤
# │ Pricing Model         │ DPU-hour ($0.44)  │ DBU-hour ($0.07-$0.70)        │
# │ Minimum Cost          │ 10 DPU × $0.44    │ 1 DBU × $0.07                 │
# │ Serverless            │ Yes               │ Yes (SQL, Jobs)               │
# │ Delta Lake            │ Limited           │ Native                        │
# │ ML Support            │ Basic             │ MLflow, AutoML                │
# │ Governance            │ Lake Formation    │ Unity Catalog                 │
# │ Streaming             │ Structured        │ Structured + Delta Live       │
# └───────────────────────┴───────────────────┴───────────────────────────────┘
#
# =============================================================================
