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
# Variables - AWS SIEM for iGaming Compliance
# =============================================================================

# --- General ---

variable "project_name" {
  description = "Project name used as prefix for all resource names"
  type        = string
  default     = "igaming-siem"
}

variable "environment" {
  description = "Deployment environment (prod, staging, dev)"
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["prod", "staging", "dev"], var.environment)
    error_message = "Environment must be prod, staging, or dev."
  }
}

variable "aws_region" {
  description = "AWS region for deployment. us-east-1 for NJ/PA, us-east-2 for MI"
  type        = string
  default     = "us-east-1"
}

variable "compliance_frameworks" {
  description = "List of compliance frameworks this deployment supports"
  type        = list(string)
  default     = ["pci-dss", "nj-dge", "pa-pgcb", "mi-mgcb"]
}

# --- GuardDuty ---

variable "enable_eks_protection" {
  description = "Enable GuardDuty EKS Audit Log monitoring (set true if using EKS for game servers)"
  type        = bool
  default     = false
}

variable "enable_malware_protection" {
  description = "Enable GuardDuty malware scanning for EBS volumes and S3"
  type        = bool
  default     = true
}

variable "enable_rds_protection" {
  description = "Enable GuardDuty RDS login event monitoring"
  type        = bool
  default     = true
}

variable "enable_lambda_protection" {
  description = "Enable GuardDuty Lambda network activity monitoring"
  type        = bool
  default     = true
}

variable "enable_runtime_monitoring" {
  description = "Enable GuardDuty Runtime Monitoring for EC2/ECS/EKS"
  type        = bool
  default     = true
}

variable "custom_threat_list_s3_uri" {
  description = "S3 URI for custom GuardDuty threat list (known bad IPs from fraud investigations)"
  type        = string
  default     = ""
}

variable "trusted_ip_list_s3_uri" {
  description = "S3 URI for GuardDuty trusted IP list (office IPs, VPN, game provider IPs)"
  type        = string
  default     = ""
}

# --- Security Hub ---

variable "enable_nist_standard" {
  description = "Enable NIST 800-53 Rev 5 standard in Security Hub"
  type        = bool
  default     = false
}

# --- CloudTrail ---

variable "is_organization_trail" {
  description = "Set true if using AWS Organizations (multi-account setup for multi-state operations)"
  type        = bool
  default     = false
}

# --- CloudWatch ---

variable "cloudwatch_retention_days" {
  description = "CloudWatch Logs retention in days. Hot retention for real-time queries (S3 handles 7-year archive)"
  type        = number
  default     = 90
}

# --- Alert Thresholds ---

variable "failed_login_threshold" {
  description = "Number of failed logins in 5 minutes before triggering alarm"
  type        = number
  default     = 50
}

variable "suspicious_bet_threshold" {
  description = "Bet amount (in cents) above which a bet is flagged as suspicious"
  type        = number
  default     = 1000000 # $10,000 in cents
}

variable "large_bet_count_threshold" {
  description = "Number of large bets in 10 minutes before triggering AML alarm"
  type        = number
  default     = 5
}

variable "withdrawal_volume_threshold" {
  description = "Platform-wide withdrawal requests per hour before triggering the volume alarm. This is a volume signal, not per-player structuring detection"
  type        = number
  default     = 10
}

variable "cash_report_threshold" {
  description = "Currency transaction reporting threshold in cents. Withdrawals at or above this are reportable; withdrawals sized just below it are the structuring signal. FinCEN CTR is USD 10,000; confirm the value for each jurisdiction"
  type        = number
  default     = 1000000 # $10,000 in cents
}

variable "structuring_band_floor" {
  description = "Lower bound in cents of the structuring band. Withdrawals in [structuring_band_floor, cash_report_threshold) are counted as structuring indicators"
  type        = number
  default     = 800000 # $8,000 in cents

  validation {
    condition     = var.structuring_band_floor > 0
    error_message = "structuring_band_floor must be positive."
  }
}

variable "structuring_band_count_threshold" {
  description = "Number of structuring-band withdrawals in one hour before triggering the AML alarm"
  type        = number
  default     = 3
}

variable "account_creation_threshold" {
  description = "Platform-wide new accounts per hour before triggering the account-creation-rate alarm. Linking accounts to each other is a fraud-pipeline job, not a metric filter"
  type        = number
  default     = 20
}

# --- SNS Notification Endpoints ---

variable "security_team_email" {
  description = "Email address for critical security alerts"
  type        = string
  default     = ""
}

variable "compliance_team_email" {
  description = "Email address for compliance alerts (Security Hub, Config failures)"
  type        = string
  default     = ""
}

variable "fraud_team_email" {
  description = "Email address for fraud/AML alerts"
  type        = string
  default     = ""
}

variable "pagerduty_webhook_url" {
  description = "PagerDuty webhook URL for critical security alerts (HTTPS endpoint)"
  type        = string
  default     = ""
  sensitive   = true
}

# --- WAF ---

variable "waf_scope" {
  description = "WAF scope: REGIONAL for ALB, CLOUDFRONT for CloudFront distributions"
  type        = string
  default     = "REGIONAL"

  validation {
    condition     = contains(["REGIONAL", "CLOUDFRONT"], var.waf_scope)
    error_message = "WAF scope must be REGIONAL or CLOUDFRONT."
  }
}

variable "allowed_countries" {
  description = "List of ISO 3166-1 alpha-2 country codes allowed through WAF geo-blocking"
  type        = list(string)
  default     = ["US"] # US only for NJ/PA/MI licensed operators
}

variable "waf_rate_limit_global" {
  description = "Maximum requests per 5-minute window per IP (global rate limit)"
  type        = number
  default     = 2000
}

variable "waf_rate_limit_login" {
  description = "Maximum login attempts per 5-minute window per IP"
  type        = number
  default     = 100
}

variable "waf_rate_limit_bet_api" {
  description = "Maximum bet API calls per 5-minute window per IP"
  type        = number
  default     = 300
}

variable "waf_blocked_ips" {
  description = "List of IPs to manually block (CIDR notation, e.g., ['1.2.3.4/32'])"
  type        = list(string)
  default     = []
}

variable "waf_protected_resource_arns" {
  description = "ARNs of the REGIONAL resources the Web ACL is attached to: ALB, API Gateway v1 stage, AppSync GraphQL API, Cognito user pool, App Runner service or Verified Access instance. Leave empty only when waf_scope is CLOUDFRONT, where the ACL is attached via the distribution's web_acl_id instead. A REGIONAL Web ACL with no association evaluates no traffic and protects nothing"
  type        = list(string)
  default     = []
}
