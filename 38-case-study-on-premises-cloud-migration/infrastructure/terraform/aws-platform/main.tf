# Companion code for "The Backend of Luck" - Chapter 38, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Main Configuration — AWS iGaming Casino Platform
# =============================================================================
# This Terraform configuration deploys a complete AWS infrastructure stack
# for a regulated iGaming casino platform, designed for compliance with:
#   - NJ DGE (New Jersey Division of Gaming Enforcement)
#   - PA PGCB (Pennsylvania Gaming Control Board)
#   - PCI-DSS (Payment Card Industry Data Security Standard)
#
# Services deployed:
#   - VPC with 3-tier network architecture (public/private/data)
#   - ECS Fargate for containerized casino API (FastAPI)
#   - RDS PostgreSQL 16 (Multi-AZ, encrypted, 7-year backup)
#   - ElastiCache Redis (Multi-AZ, encrypted at rest and in transit)
#   - Application Load Balancer with WAF v2
#   - ECR for Docker image storage
#   - Secrets Manager with automatic rotation
#   - CloudWatch monitoring, dashboards, and alarms
#   - CodePipeline + CodeBuild CI/CD
#
# Usage:
#   terraform init
#   terraform plan -var-file="terraform.tfvars"
#   terraform apply -var-file="terraform.tfvars"
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  # Uncomment and configure for remote state (recommended for production)
  # backend "s3" {
  #   bucket         = "acmetocasino-terraform-state"
  #   key            = "aws-platform/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   dynamodb_table = "terraform-lock"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = var.tags
  }
}
