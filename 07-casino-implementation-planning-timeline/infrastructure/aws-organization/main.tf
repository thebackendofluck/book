# Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# AWS Organization Structure for Online Gambling Platform
#
# Creates a multi-account AWS Organization with gambling-specific OUs:
#   - Production (player-facing services)
#   - Staging (pre-production testing)
#   - Compliance (audit logs, regulatory reporting, data retention)
#   - Audit (read-only cross-account access for external auditors)
#   - Development (sandboxed dev environments)
#   - Security (centralized security tooling, GuardDuty, Security Hub)
#
# Usage:
#   terraform init
#   terraform plan -out=plan.tfplan
#   terraform apply plan.tfplan
#
# Prerequisites:
#   - AWS root account with Organizations enabled
#   - Terraform >= 1.5
#   - AWS CLI configured with admin credentials
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket       = "casino-terraform-state"
    key          = "organization/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    # S3-native state locking (Terraform >= 1.11, replaces DynamoDB locking)
    use_lockfile = true
  }
}

provider "aws" {
  region = var.primary_region

  default_tags {
    tags = {
      Project     = "casino-platform"
      ManagedBy   = "terraform"
      Environment = "organization"
      Compliance  = "gambling-regulated"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "primary_region" {
  description = "Primary AWS region for the organization"
  type        = string
  default     = "eu-west-1" # Ireland - common for EU-licensed gambling ops
}

variable "company_name" {
  description = "Company name used in resource naming"
  type        = string
  default     = "acme-casino"
}

variable "admin_email_domain" {
  description = "Email domain for AWS account root emails"
  type        = string
  default     = "acme-casino.com"
}

variable "environments" {
  description = "Map of environment names to configurations"
  type = map(object({
    email_prefix   = string
    ou_name        = string
    account_name   = string
    scp_policy_ids = list(string)
  }))
  default = {
    production = {
      email_prefix   = "aws-prod"
      ou_name        = "Production"
      account_name   = "casino-production"
      scp_policy_ids = ["restrict-regions", "deny-public-s3", "require-encryption"]
    }
    staging = {
      email_prefix   = "aws-staging"
      ou_name        = "Staging"
      account_name   = "casino-staging"
      scp_policy_ids = ["restrict-regions", "deny-public-s3"]
    }
    compliance = {
      email_prefix   = "aws-compliance"
      ou_name        = "Compliance"
      account_name   = "casino-compliance"
      scp_policy_ids = ["restrict-regions", "deny-delete-logs", "require-encryption"]
    }
    audit = {
      email_prefix   = "aws-audit"
      ou_name        = "Audit"
      account_name   = "casino-audit"
      scp_policy_ids = ["restrict-regions", "audit-read-only"]
    }
    development = {
      email_prefix   = "aws-dev"
      ou_name        = "Development"
      account_name   = "casino-development"
      scp_policy_ids = ["restrict-regions", "limit-instance-types"]
    }
    security = {
      email_prefix   = "aws-security"
      ou_name        = "Security"
      account_name   = "casino-security"
      scp_policy_ids = ["restrict-regions", "require-encryption"]
    }
  }
}

# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------

resource "aws_organizations_organization" "casino" {
  aws_service_access_principals = [
    "cloudtrail.amazonaws.com",
    "config.amazonaws.com",
    "guardduty.amazonaws.com",
    "securityhub.amazonaws.com",
    "sso.amazonaws.com",
    "tagpolicies.tag.amazonaws.com",
    "reporting.trustedadvisor.amazonaws.com",
    "access-analyzer.amazonaws.com",
    "macie.amazonaws.com",
  ]

  enabled_policy_types = [
    "SERVICE_CONTROL_POLICY",
    "TAG_POLICY",
  ]

  feature_set = "ALL"
}

# ---------------------------------------------------------------------------
# Organizational Units
# ---------------------------------------------------------------------------

resource "aws_organizations_organizational_unit" "gambling_platform" {
  name      = "${var.company_name}-platform"
  parent_id = aws_organizations_organization.casino.roots[0].id
}

resource "aws_organizations_organizational_unit" "environments" {
  for_each  = var.environments
  name      = each.value.ou_name
  parent_id = aws_organizations_organizational_unit.gambling_platform.id
}

# ---------------------------------------------------------------------------
# Member Accounts
# ---------------------------------------------------------------------------

resource "aws_organizations_account" "environment" {
  for_each = var.environments

  name      = each.value.account_name
  email     = "${each.value.email_prefix}@${var.admin_email_domain}"
  parent_id = aws_organizations_organizational_unit.environments[each.key].id

  role_name = "OrganizationAccountAccessRole"

  # Prevent accidental deletion of accounts
  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Environment = each.key
    OU          = each.value.ou_name
  }
}

# ---------------------------------------------------------------------------
# CloudTrail - Organization-wide audit trail (gambling compliance requirement)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "cloudtrail" {
  bucket = "${var.company_name}-org-cloudtrail"

  tags = {
    Purpose    = "organization-audit-trail"
    Retention  = "7-years" # Gambling regulations often require 5-7 year retention
    Compliance = "mandatory"
  }
}

resource "aws_s3_bucket_versioning" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.audit.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    id     = "archive-after-1-year"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 365
      storage_class = "GLACIER"
    }

    # 7-year retention for gambling compliance
    expiration {
      days = 2555
    }
  }
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AWSCloudTrailAclCheck"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.cloudtrail.arn
      },
      {
        Sid    = "AWSCloudTrailWrite"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.cloudtrail.arn}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
      {
        Sid       = "DenyUnencryptedObjectUploads"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.cloudtrail.arn}/*"
        Condition = {
          StringNotEquals = {
            "s3:x-amz-server-side-encryption" = "aws:kms"
          }
        }
      },
      {
        Sid       = "DenyDeleteActions"
        Effect    = "Deny"
        Principal = "*"
        Action = [
          "s3:DeleteObject",
          "s3:DeleteObjectVersion",
          "s3:DeleteBucket",
        ]
        Resource = [
          aws_s3_bucket.cloudtrail.arn,
          "${aws_s3_bucket.cloudtrail.arn}/*",
        ]
      }
    ]
  })
}

resource "aws_cloudtrail" "organization" {
  name                          = "${var.company_name}-org-trail"
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  is_organization_trail         = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true
  kms_key_id                    = aws_kms_key.audit.arn
  include_global_service_events = true

  event_selector {
    read_write_type           = "All"
    include_management_events = true

    data_resource {
      type   = "AWS::S3::Object"
      values = ["arn:aws:s3:::"]
    }
  }

  # Insight events for anomaly detection (unusual API activity)
  insight_selector {
    insight_type = "ApiCallRateInsight"
  }

  insight_selector {
    insight_type = "ApiErrorRateInsight"
  }

  tags = {
    Purpose    = "gambling-compliance-audit"
    Compliance = "mandatory"
  }
}

# ---------------------------------------------------------------------------
# KMS Key for audit encryption
# ---------------------------------------------------------------------------

resource "aws_kms_key" "audit" {
  description             = "KMS key for gambling platform audit trail encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  multi_region            = false

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RootAccountAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "CloudTrailAccess"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
      },
    ]
  })

  tags = {
    Purpose = "audit-trail-encryption"
  }
}

resource "aws_kms_alias" "audit" {
  name          = "alias/${var.company_name}-audit"
  target_key_id = aws_kms_key.audit.key_id
}

# ---------------------------------------------------------------------------
# AWS Config - Organization-wide configuration compliance
# ---------------------------------------------------------------------------

resource "aws_config_configuration_recorder" "org" {
  name     = "${var.company_name}-config"
  role_arn = aws_iam_role.config.arn

  recording_group {
    all_supported                 = true
    include_global_resource_types = true
  }
}

resource "aws_iam_role" "config" {
  name = "${var.company_name}-config-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "config.amazonaws.com"
        }
      }
    ]
  })

  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AWS_ConfigRole",
  ]
}

# ---------------------------------------------------------------------------
# GuardDuty - Organization-wide threat detection
# ---------------------------------------------------------------------------

resource "aws_guardduty_organization_admin_account" "security" {
  admin_account_id = aws_organizations_account.environment["security"].id
}

# ---------------------------------------------------------------------------
# Tag Policies - Enforce consistent tagging for compliance
# ---------------------------------------------------------------------------

resource "aws_organizations_policy" "tag_policy" {
  name        = "gambling-platform-tags"
  description = "Enforce required tags for gambling platform resources"
  type        = "TAG_POLICY"

  content = jsonencode({
    tags = {
      Environment = {
        tag_key = {
          "@@assign" = "Environment"
        }
        tag_value = {
          "@@assign" = ["production", "staging", "development", "compliance", "audit", "security"]
        }
        enforced_for = {
          "@@assign" = [
            "ec2:instance",
            "ec2:volume",
            "rds:db",
            "s3:bucket",
            "lambda:function",
          ]
        }
      }
      Compliance = {
        tag_key = {
          "@@assign" = "Compliance"
        }
        tag_value = {
          "@@assign" = ["gambling-regulated", "pci-dss", "gdpr", "aml", "internal"]
        }
      }
      DataClassification = {
        tag_key = {
          "@@assign" = "DataClassification"
        }
        tag_value = {
          "@@assign" = ["public", "internal", "confidential", "restricted", "pii", "financial"]
        }
      }
    }
  })
}

resource "aws_organizations_policy_attachment" "tag_policy" {
  policy_id = aws_organizations_policy.tag_policy.id
  target_id = aws_organizations_organizational_unit.gambling_platform.id
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "organization_id" {
  description = "AWS Organization ID"
  value       = aws_organizations_organization.casino.id
}

output "organization_root_id" {
  description = "Root OU ID"
  value       = aws_organizations_organization.casino.roots[0].id
}

output "account_ids" {
  description = "Map of environment to AWS account IDs"
  value = {
    for key, account in aws_organizations_account.environment :
    key => account.id
  }
}

output "ou_ids" {
  description = "Map of environment to OU IDs"
  value = {
    for key, ou in aws_organizations_organizational_unit.environments :
    key => ou.id
  }
}

output "cloudtrail_bucket" {
  description = "S3 bucket for organization CloudTrail"
  value       = aws_s3_bucket.cloudtrail.id
}
