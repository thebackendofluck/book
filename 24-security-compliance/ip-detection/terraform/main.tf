# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# main.tf — Root module
# Chapter 24: IP Detection & DDoS Protection — Multi-Platform Terraform
#
# Provisions all three platform layers:
#   1. Cloudflare  — edge KV, Workers, WAF, firewall rules
#   2. AWS         — DynamoDB, Lambda, API GW, WAF WebACL, CloudWatch, SNS
#   3. On-premises — FastAPI service deployment via SSH null_resource
#
# Usage:
#   terraform workspace new staging
#   terraform init
#   terraform plan -var-file=terraform.tfvars
#   terraform apply -var-file=terraform.tfvars
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Recommended: use remote state for team environments.
  # Uncomment and configure the backend that matches your setup.
  #
  # backend "s3" {
  #   bucket         = "your-terraform-state-bucket"
  #   key            = "ip-detection/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   dynamodb_table = "terraform-state-lock"
  # }
}

# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

provider "cloudflare" {
  # Reads CLOUDFLARE_API_TOKEN from the environment.
  # The token requires: Workers Scripts:Edit, Workers KV Storage:Edit,
  # Zone WAF:Edit, Zone Settings:Edit, DNS:Edit.
  # Never embed credentials in this file.
}

provider "null" {}
provider "random" {}

# ---------------------------------------------------------------------------
# Locals — shared values
# ---------------------------------------------------------------------------

locals {
  # Terraform workspace drives environment naming when not explicitly set.
  workspace_env = terraform.workspace == "default" ? var.environment : terraform.workspace

  # Consistent name suffix across all platforms.
  name_suffix = local.workspace_env == "production" ? "" : "-${local.workspace_env}"

  # AWS cron uses a 6-field format; convert from standard 5-field cron.
  # threat_list_schedule var is standard cron (e.g. "0 0,8,16 * * *").
  # EventBridge requires: cron(minute hour day month weekday year)
  aws_eventbridge_schedule = "cron(${var.threat_list_schedule} *)"

  common_tags = merge(
    {
      Service     = "igaming-ip-detection"
      Environment = local.workspace_env
      ManagedBy   = "terraform"
      Chapter     = "24"
    },
    var.tags
  )
}

# ---------------------------------------------------------------------------
# Module: Cloudflare — edge KV namespaces, Workers, WAF
# ---------------------------------------------------------------------------

module "cloudflare" {
  source = "./modules/cloudflare"

  account_id                   = var.cloudflare_account_id
  zone_id                      = var.cloudflare_zone_id
  domain                       = var.cloudflare_domain
  environment                  = local.workspace_env
  name_suffix                  = local.name_suffix
  worker_route_pattern         = var.cloudflare_worker_route_pattern
  classifier_route_pattern     = var.cloudflare_classifier_route_pattern
  worker_script_path           = var.cloudflare_worker_script_path
  classifier_script_path       = var.cloudflare_classifier_script_path
  security_level               = var.cloudflare_security_level
  bot_management_enabled       = var.cloudflare_bot_management_enabled
  waf_managed_ruleset_enabled  = var.cloudflare_waf_managed_ruleset_enabled
  owasp_ruleset_enabled        = var.cloudflare_owasp_ruleset_enabled
  rate_limit_thresholds        = var.rate_limit_thresholds
  fraud_score_block_threshold  = var.fraud_score_block_threshold
  fraud_score_review_threshold = var.fraud_score_review_threshold
}

# ---------------------------------------------------------------------------
# Module: AWS — DynamoDB, Lambda, API GW, WAF, CloudWatch, SNS, IAM
# ---------------------------------------------------------------------------

module "aws" {
  source = "./modules/aws"

  aws_region                     = var.aws_region
  aws_account_id                 = var.aws_account_id
  environment                    = local.workspace_env
  name_suffix                    = local.name_suffix
  kms_key_arn                    = var.kms_key_arn
  lambda_memory_mb               = var.lambda_memory_mb
  lambda_timeout_seconds         = var.lambda_timeout_seconds
  fraud_score_block_threshold    = var.fraud_score_block_threshold
  fraud_score_review_threshold   = var.fraud_score_review_threshold
  elasticache_enabled            = var.elasticache_enabled
  elasticache_node_type          = var.elasticache_node_type
  attack_evidence_retention_days = var.attack_evidence_retention_days
  waf_block_threshold            = var.waf_block_threshold
  threat_list_schedule           = local.aws_eventbridge_schedule
  alert_email                    = var.alert_email
  alert_pagerduty_url            = var.alert_pagerduty_url
  scale_profiles                 = var.scale_profiles
  maxmind_license_key            = var.maxmind_license_key
  ip_reputation_api_key          = var.ip_reputation_api_key
  common_tags                    = local.common_tags
}

# ---------------------------------------------------------------------------
# Module: On-premises — FastAPI ip-detection service via SSH
# ---------------------------------------------------------------------------

module "onpremise" {
  source = "./modules/onpremise"

  host                   = var.onpremise_host
  user                   = var.onpremise_user
  ssh_key_path           = var.onpremise_ssh_key_path
  ssh_port               = var.onpremise_ssh_port
  app_dir                = var.onpremise_app_dir
  redis_url              = var.onpremise_redis_url
  maxmind_db_path        = var.onpremise_maxmind_db_path
  maxmind_city_db_path   = var.onpremise_maxmind_city_db_path
  kyc_service_url        = var.onpremise_kyc_service_url
  threat_list_schedule   = var.threat_list_schedule
  environment            = local.workspace_env
  fraud_block_threshold  = var.fraud_score_block_threshold
  fraud_review_threshold = var.fraud_score_review_threshold
  source_dir             = var.onpremise_source_dir
  threat_list_source_dir = var.onpremise_threat_list_source_dir
  rate_limit_thresholds  = var.rate_limit_thresholds
}
