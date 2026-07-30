# Companion code for "The Backend of Luck" - Chapter 24c, AWS SIEM Implementation for iGaming Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# AWS SIEM for iGaming Compliance - Main Configuration
# =============================================================================
# This Terraform configuration deploys a complete AWS security monitoring stack
# designed for iGaming regulatory compliance (NJ DGE, PA PGCB, MI MGCB).
#
# Services deployed:
#   - GuardDuty (threat detection)
#   - Security Hub (compliance scoring)
#   - CloudTrail (API audit trail)
#   - AWS Config (configuration compliance)
#   - CloudWatch (log aggregation, metrics, alarms)
#   - SNS (alert routing)
#   - Lambda (custom alert processing)
#   - S3 (7-year log archive)
#   - WAF (web application protection)
#   - KMS (encryption at rest for all security data)
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  # Uncomment and configure for remote state (recommended for production)
  # backend "s3" {
  #   bucket         = "igaming-terraform-state"
  #   key            = "aws-siem/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   dynamodb_table = "terraform-locks"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "igaming-siem"
      Environment = var.environment
      ManagedBy   = "terraform"
      Compliance  = "nj-dge-pa-pgcb-mi-mgcb"
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_partition" "current" {}

# Current account ID - used across multiple modules for IAM policies
locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition

  # Common naming prefix for all resources
  name_prefix = "${var.project_name}-${var.environment}"

  # Common tags applied to all resources
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    Compliance  = join(",", var.compliance_frameworks)
  }
}
