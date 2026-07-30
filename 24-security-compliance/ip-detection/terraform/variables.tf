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
# variables.tf — Root module input variables
# Chapter 24: IP Detection & DDoS Protection — Multi-Platform Terraform
# =============================================================================

# ---------------------------------------------------------------------------
# Environment & workspace
# ---------------------------------------------------------------------------

variable "environment" {
  description = "Deployment environment. Use terraform workspace (dev/staging/prod) instead when possible."
  type        = string
  default     = "production"

  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be one of: dev, staging, production."
  }
}

# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region for all resources (us-east-1 required for WAF Classic scope)."
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "AWS account ID. Used to construct ARNs and unique bucket names."
  type        = string
}

variable "lambda_memory_mb" {
  description = "Lambda ip-gate memory in MB. Higher values allocate more vCPU. Range: 256–3008."
  type        = number
  default     = 512

  validation {
    condition     = var.lambda_memory_mb >= 256 && var.lambda_memory_mb <= 3008
    error_message = "lambda_memory_mb must be between 256 and 3008."
  }
}

variable "lambda_timeout_seconds" {
  description = "Lambda ip-gate timeout in seconds. Must be less than 29 (API Gateway HTTP v2 limit)."
  type        = number
  default     = 10

  validation {
    condition     = var.lambda_timeout_seconds >= 3 && var.lambda_timeout_seconds <= 28
    error_message = "lambda_timeout_seconds must be between 3 and 28."
  }
}

variable "fraud_score_block_threshold" {
  description = "Fraud score (0-100) above which requests are blocked at Gate 5."
  type        = number
  default     = 75

  validation {
    condition     = var.fraud_score_block_threshold > 0 && var.fraud_score_block_threshold <= 100
    error_message = "fraud_score_block_threshold must be between 1 and 100."
  }
}

variable "fraud_score_review_threshold" {
  description = "Fraud score (0-100) above which requests are flagged for review at Gate 5."
  type        = number
  default     = 50

  validation {
    condition     = var.fraud_score_review_threshold > 0 && var.fraud_score_review_threshold <= 100
    error_message = "fraud_score_review_threshold must be between 1 and 100."
  }
}

variable "elasticache_enabled" {
  description = "Whether to provision an ElastiCache Redis cluster for velocity tracking."
  type        = bool
  default     = false
}

variable "elasticache_node_type" {
  description = "ElastiCache node type. Only used when elasticache_enabled = true."
  type        = string
  default     = "cache.t4g.micro"
}

variable "kms_key_arn" {
  description = "Optional ARN of an existing KMS key for DynamoDB + S3 encryption. Leave empty to use AWS-managed keys."
  type        = string
  default     = ""
  sensitive   = true
}

variable "attack_evidence_retention_days" {
  description = "Number of days to retain attack evidence in S3 before expiration."
  type        = number
  default     = 365

  validation {
    condition     = var.attack_evidence_retention_days >= 90
    error_message = "attack_evidence_retention_days must be at least 90 for regulatory compliance."
  }
}

variable "waf_block_threshold" {
  description = "Number of requests per 5-minute window per IP before WAF rate-based rule triggers."
  type        = number
  default     = 2000
}

variable "maxmind_license_key" {
  description = "MaxMind license key for downloading GeoLite2 databases. Stored in SSM Parameter Store, not state."
  type        = string
  default     = ""
  sensitive   = true
}

variable "ip_reputation_api_key" {
  description = "IPQualityScore (or compatible) API key for VPN detection Gate 2. Stored in SSM, not state."
  type        = string
  default     = ""
  sensitive   = true
}

# ---------------------------------------------------------------------------
# Cloudflare
# ---------------------------------------------------------------------------

variable "cloudflare_account_id" {
  description = "Cloudflare account ID found in the dashboard URL."
  type        = string
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for the primary domain."
  type        = string
}

variable "cloudflare_domain" {
  description = "Primary domain managed by Cloudflare (e.g. acmetocasino.com)."
  type        = string
}

variable "cloudflare_worker_route_pattern" {
  description = "URL pattern for the ip-detection Worker route (e.g. acmetocasino.com/*)."
  type        = string
}

variable "cloudflare_classifier_route_pattern" {
  description = "URL pattern for the edge-classifier Worker route (e.g. acmetocasino.com/api/*)."
  type        = string
}

variable "cloudflare_waf_managed_ruleset_enabled" {
  description = "Whether to enable the Cloudflare Managed Ruleset (WAF phase:http_request_firewall_managed)."
  type        = bool
  default     = true
}

variable "cloudflare_owasp_ruleset_enabled" {
  description = "Whether to enable the OWASP Core Ruleset via Cloudflare WAF."
  type        = bool
  default     = true
}

variable "cloudflare_bot_management_enabled" {
  description = "Whether to enable Cloudflare Bot Management (requires Enterprise plan)."
  type        = bool
  default     = false
}

variable "cloudflare_security_level" {
  description = "Cloudflare security level: off, essentially_off, low, medium, high, under_attack."
  type        = string
  default     = "high"

  validation {
    condition     = contains(["off", "essentially_off", "low", "medium", "high", "under_attack"], var.cloudflare_security_level)
    error_message = "cloudflare_security_level must be one of: off, essentially_off, low, medium, high, under_attack."
  }
}

variable "cloudflare_worker_script_path" {
  description = "Filesystem path to the compiled ip-detection Worker JS bundle."
  type        = string
  default     = "../cloudflare/dist/ip-detection-worker.js"
}

variable "cloudflare_classifier_script_path" {
  description = "Filesystem path to the compiled edge-classifier Worker JS bundle."
  type        = string
  default     = "../cloudflare/dist/edge-classifier.js"
}

# ---------------------------------------------------------------------------
# On-premises
# ---------------------------------------------------------------------------

variable "onpremise_host" {
  description = "SSH hostname or IP of the on-premises server running the FastAPI ip-detection service."
  type        = string
}

variable "onpremise_user" {
  description = "SSH user for on-premises provisioning."
  type        = string
  default     = "deploy"
}

variable "onpremise_ssh_key_path" {
  description = "Local filesystem path to the SSH private key for on-premises provisioning."
  type        = string
  default     = "~/.ssh/id_ed25519"
}

variable "onpremise_ssh_port" {
  description = "SSH port for on-premises server."
  type        = number
  default     = 22
}

variable "onpremise_app_dir" {
  description = "Absolute path on the remote server where the ip-detection service is installed."
  type        = string
  default     = "/opt/ip-detection"
}

variable "onpremise_redis_url" {
  description = "Redis connection URL on the on-premises server."
  type        = string
  default     = "redis://localhost:6379/0"
}

variable "onpremise_maxmind_db_path" {
  description = "Absolute path on the remote server for the GeoLite2-ASN.mmdb file."
  type        = string
  default     = "/var/lib/GeoIP/GeoLite2-ASN.mmdb"
}

variable "onpremise_maxmind_city_db_path" {
  description = "Absolute path on the remote server for the GeoLite2-City.mmdb file."
  type        = string
  default     = "/var/lib/GeoIP/GeoLite2-City.mmdb"
}

variable "onpremise_kyc_service_url" {
  description = "Internal URL for the KYC service reachable from the on-premises server."
  type        = string
  default     = "http://kyc-service:8080"
}

variable "onpremise_source_dir" {
  description = "Local path to the on-premises Python source files to deploy."
  type        = string
  default     = "../onpremise"
}

variable "onpremise_threat_list_source_dir" {
  description = "Local path to the threat-lists consolidation scripts to deploy."
  type        = string
  default     = "../threat-lists"
}

# ---------------------------------------------------------------------------
# Shared / cross-platform
# ---------------------------------------------------------------------------

variable "threat_list_schedule" {
  description = "Cron expression (cron(minute hour day month weekday year) for AWS, or standard cron for on-prem) for threat-list refresh."
  type        = string
  default     = "0 0,8,16 * * *"
}

variable "alert_email" {
  description = "Email address for NOC security alert notifications (SNS subscription). Leave empty to disable."
  type        = string
  default     = ""
}

variable "alert_pagerduty_url" {
  description = "PagerDuty HTTPS integration URL for critical alerts. Leave empty to disable."
  type        = string
  default     = ""
  sensitive   = true
}

variable "rate_limit_thresholds" {
  description = "Rate limit thresholds per time window per IP address."
  type = object({
    requests_per_minute = number
    requests_per_5min   = number
    requests_per_hour   = number
  })
  default = {
    requests_per_minute = 60
    requests_per_5min   = 200
    requests_per_hour   = 1000
  }
}

variable "scale_profiles" {
  description = "Named scaling profiles for campaign-autoscaler Lambda. Map of profile name to min/max capacity."
  type = map(object({
    min_capacity     = number
    max_capacity     = number
    cooldown_seconds = number
  }))
  default = {
    normal = {
      min_capacity     = 2
      max_capacity     = 10
      cooldown_seconds = 300
    }
    campaign_small = {
      min_capacity     = 5
      max_capacity     = 30
      cooldown_seconds = 120
    }
    campaign_large = {
      min_capacity     = 20
      max_capacity     = 100
      cooldown_seconds = 60
    }
    black_friday = {
      min_capacity     = 50
      max_capacity     = 200
      cooldown_seconds = 30
    }
  }
}

variable "tags" {
  description = "Additional tags to apply to all AWS resources."
  type        = map(string)
  default     = {}
}
