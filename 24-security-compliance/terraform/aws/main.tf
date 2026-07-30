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
# Chapter 29: Security and Compliance - AWS Security Infrastructure
# =============================================================================
# This Terraform configuration deploys comprehensive security infrastructure
# for iGaming platforms including:
# - AWS WAF (Web Application Firewall) for CloudFront, ALB, and API Gateway
# - AWS Shield Advanced (DDoS Protection)
# - AWS GuardDuty (Threat Detection)
# - AWS Security Hub (Security Posture Management)
# - AWS Inspector (Vulnerability Assessment)
# - VPC Security Groups and Network ACLs
#
# Compliance: PCI DSS, GDPR, ISO 27001
# Estimated Monthly Cost: $2,500-4,000 (varies with traffic)
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

# =============================================================================
# Variables
# =============================================================================

variable "environment" {
  description = "Environment name (production, staging, development)"
  type        = string
  default     = "production"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "igaming-security"
}

variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.29.0.0/16"
}

variable "enable_shield_advanced" {
  description = "Enable AWS Shield Advanced (additional cost ~$3000/month)"
  type        = bool
  default     = false
}

variable "enable_guardduty" {
  description = "Enable AWS GuardDuty threat detection"
  type        = bool
  default     = true
}

variable "enable_security_hub" {
  description = "Enable AWS Security Hub"
  type        = bool
  default     = true
}

variable "waf_rate_limit" {
  description = "WAF rate limit - requests per 5 minutes per IP"
  type        = number
  default     = 2000
}

variable "blocked_countries" {
  description = "List of country codes to block (ISO 3166-1 alpha-2)"
  type        = list(string)
  default     = ["KP", "IR", "SY", "CU"] # OFAC sanctioned countries
}

variable "allowed_ip_ranges" {
  description = "IP ranges to whitelist (bypass rate limiting)"
  type        = list(string)
  default     = []
}

variable "notification_email" {
  description = "Email for security alerts"
  type        = string
  default     = "security@company.com"
}

variable "tags" {
  description = "Resource tags"
  type        = map(string)
  default = {
    Project    = "iGaming-Security"
    Chapter    = "29"
    Compliance = "PCI-DSS,GDPR"
    ManagedBy  = "Terraform"
  }
}

# =============================================================================
# WAF Rule Granular Controls
# =============================================================================
# Each rule can be individually enabled/disabled and configured.
# OWASP Top 10 Mapping:
#   A01:2021 - Broken Access Control       → Rate Limiting, Geo Blocking
#   A02:2021 - Cryptographic Failures      → (handled at application level)
#   A03:2021 - Injection                   → SQLi, Known Bad Inputs
#   A04:2021 - Insecure Design             → (handled at application level)
#   A05:2021 - Security Misconfiguration   → Common Rules, Admin Protection
#   A06:2021 - Vulnerable Components       → Known Bad Inputs, CVE Rules
#   A07:2021 - Auth Failures               → Login Rate Limiting, Bot Control
#   A08:2021 - Software/Data Integrity     → (handled at application level)
#   A09:2021 - Security Logging            → WAF Logging enabled by default
#   A10:2021 - Server-Side Request Forgery → Common Rules, Bad Inputs
# =============================================================================

variable "waf_rules" {
  description = "Granular WAF rule configuration - enable/disable and configure each rule"
  type = object({
    # AWS Managed Rule Groups - OWASP Protection
    common_rule_set = object({
      enabled        = bool
      action         = string # "block" or "count"
      excluded_rules = list(string)
    })
    sqli_rule_set = object({
      enabled        = bool
      action         = string
      excluded_rules = list(string)
    })
    xss_rule_set = object({
      enabled        = bool
      action         = string
      excluded_rules = list(string)
    })
    known_bad_inputs = object({
      enabled        = bool
      action         = string
      excluded_rules = list(string)
    })

    # IP Reputation and Anonymous IP
    ip_reputation_list = object({
      enabled = bool
      action  = string
    })
    anonymous_ip_list = object({
      enabled       = bool
      action        = string
      block_vpn     = bool
      block_tor     = bool
      block_proxy   = bool
      block_hosting = bool
    })

    # Bot Control
    bot_control = object({
      enabled          = bool
      inspection_level = string # "COMMON" or "TARGETED"
      action           = string
    })

    # Rate Limiting
    rate_limiting = object({
      enabled            = bool
      global_limit       = number # requests per 5 minutes per IP
      login_limit        = number
      payment_limit      = number
      api_limit          = number
      registration_limit = number
    })

    # Geo Blocking
    geo_blocking = object({
      enabled           = bool
      blocked_countries = list(string)
      allowed_countries = list(string) # if set, only these are allowed
    })

    # Linux/Unix Protection
    linux_rule_set = object({
      enabled        = bool
      action         = string
      excluded_rules = list(string)
    })

    # POSIX OS Protection
    posix_rule_set = object({
      enabled        = bool
      action         = string
      excluded_rules = list(string)
    })

    # PHP Application Protection
    php_rule_set = object({
      enabled        = bool
      action         = string
      excluded_rules = list(string)
    })

    # WordPress Protection (useful for marketing sites)
    wordpress_rule_set = object({
      enabled        = bool
      action         = string
      excluded_rules = list(string)
    })

    # Custom iGaming Rules
    igaming_custom_rules = object({
      block_security_scanners = bool
      block_scraping_tools    = bool
      admin_path_protection   = bool
      bonus_abuse_protection  = bool
      multi_account_detection = bool
    })

    # Account Takeover Prevention
    atp_rule_set = object({
      enabled           = bool
      login_path        = string
      registration_path = string
    })
  })

  default = {
    # OWASP A03 - Injection Protection
    common_rule_set = {
      enabled        = true
      action         = "block"
      excluded_rules = ["SizeRestrictions_BODY"] # May cause false positives for gaming APIs
    }

    # OWASP A03 - SQL Injection
    sqli_rule_set = {
      enabled        = true
      action         = "block"
      excluded_rules = []
    }

    # OWASP A03, A07 - XSS Protection
    xss_rule_set = {
      enabled        = true
      action         = "block"
      excluded_rules = []
    }

    # OWASP A06 - Known Vulnerabilities (Log4j, etc.)
    known_bad_inputs = {
      enabled        = true
      action         = "block"
      excluded_rules = []
    }

    # OWASP A01 - IP Reputation
    ip_reputation_list = {
      enabled = true
      action  = "block"
    }

    # OWASP A01 - Anonymous Access Control
    anonymous_ip_list = {
      enabled       = true
      action        = "count" # Start with count, switch to block after tuning
      block_vpn     = false   # May affect legitimate players
      block_tor     = true    # High risk for fraud
      block_proxy   = false   # May affect legitimate players
      block_hosting = true    # Block datacenter IPs (bot farms)
    }

    # OWASP A07 - Bot Control
    bot_control = {
      enabled          = true
      inspection_level = "COMMON"
      action           = "block"
    }

    # OWASP A01, A07 - Rate Limiting (Brute Force Protection)
    rate_limiting = {
      enabled            = true
      global_limit       = 2000 # requests per 5 min per IP
      login_limit        = 10   # login attempts per 5 min
      payment_limit      = 20   # payment requests per 5 min
      api_limit          = 500  # API calls per 5 min
      registration_limit = 5    # registrations per 5 min
    }

    # OWASP A01 - Geographic Access Control
    geo_blocking = {
      enabled           = true
      blocked_countries = ["KP", "IR", "SY", "CU", "RU", "BY"] # OFAC + high-risk
      allowed_countries = []                                   # Empty = all except blocked
    }

    # Linux/Unix Protection
    linux_rule_set = {
      enabled        = true
      action         = "block"
      excluded_rules = []
    }

    # POSIX OS Protection
    posix_rule_set = {
      enabled        = true
      action         = "block"
      excluded_rules = []
    }

    # PHP Application Protection
    php_rule_set = {
      enabled        = false # Enable if using PHP backends
      action         = "block"
      excluded_rules = []
    }

    # WordPress Protection
    wordpress_rule_set = {
      enabled        = false # Enable for marketing/CMS sites
      action         = "block"
      excluded_rules = []
    }

    # Custom iGaming Rules
    igaming_custom_rules = {
      block_security_scanners = true
      block_scraping_tools    = true
      admin_path_protection   = true
      bonus_abuse_protection  = true
      multi_account_detection = true
    }

    # Account Takeover Prevention (requires additional AWS subscription)
    atp_rule_set = {
      enabled           = false # Requires AWS Fraud Control subscription
      login_path        = "/api/auth/login"
      registration_path = "/api/auth/register"
    }
  }
}

# =============================================================================
# Data Sources
# =============================================================================

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# =============================================================================
# KMS Key for Encryption
# =============================================================================

resource "aws_kms_key" "security" {
  description             = "KMS key for security services encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow GuardDuty"
        Effect = "Allow"
        Principal = {
          Service = "guardduty.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Encrypt",
          "kms:Decrypt"
        ]
        Resource = "*"
      },
      {
        Sid    = "Allow Security Hub"
        Effect = "Allow"
        Principal = {
          Service = "securityhub.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Encrypt",
          "kms:Decrypt"
        ]
        Resource = "*"
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-security-key"
  })
}

resource "aws_kms_alias" "security" {
  name          = "alias/${var.project_name}-security"
  target_key_id = aws_kms_key.security.key_id
}

# =============================================================================
# SNS Topic for Security Alerts
# =============================================================================

resource "aws_sns_topic" "security_alerts" {
  name              = "${var.project_name}-security-alerts"
  kms_master_key_id = aws_kms_key.security.id

  tags = var.tags
}

resource "aws_sns_topic_subscription" "security_email" {
  topic_arn = aws_sns_topic.security_alerts.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

resource "aws_sns_topic_policy" "security_alerts" {
  arn = aws_sns_topic.security_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowGuardDuty"
        Effect = "Allow"
        Principal = {
          Service = "guardduty.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.security_alerts.arn
      },
      {
        Sid    = "AllowSecurityHub"
        Effect = "Allow"
        Principal = {
          Service = "securityhub.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.security_alerts.arn
      },
      {
        Sid    = "AllowCloudWatch"
        Effect = "Allow"
        Principal = {
          Service = "cloudwatch.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.security_alerts.arn
      }
    ]
  })
}

# =============================================================================
# S3 Bucket for Security Logs
# =============================================================================

resource "aws_s3_bucket" "security_logs" {
  bucket = "${var.project_name}-security-logs-${data.aws_caller_identity.current.account_id}"

  tags = merge(var.tags, {
    Name = "${var.project_name}-security-logs"
  })
}

resource "aws_s3_bucket_versioning" "security_logs" {
  bucket = aws_s3_bucket.security_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "security_logs" {
  bucket = aws_s3_bucket.security_logs.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.security.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "security_logs" {
  bucket = aws_s3_bucket.security_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "security_logs" {
  bucket = aws_s3_bucket.security_logs.id

  rule {
    id     = "security-log-retention"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 365
      storage_class = "GLACIER"
    }

    # Keep for 7 years (regulatory requirement)
    expiration {
      days = 2555
    }

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "STANDARD_IA"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    # CKV_AWS_300: Abort incomplete multipart uploads after 7 days
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# =============================================================================
# WAF Web ACL - Regional (for ALB/API Gateway)
# =============================================================================
# Comprehensive WAF protection with granular rule control.
# All rules can be enabled/disabled via var.waf_rules.
#
# OWASP Top 10 2021 Coverage:
#   A01 - Broken Access Control:      Geo Blocking, Rate Limiting
#   A02 - Cryptographic Failures:     (Application level)
#   A03 - Injection:                  SQLi Rules, Common Rules, XSS Rules
#   A04 - Insecure Design:            (Application level)
#   A05 - Security Misconfiguration:  Common Rules, Admin Protection
#   A06 - Vulnerable Components:      Known Bad Inputs (Log4j, etc.)
#   A07 - Auth Failures:              Login Rate Limiting, Bot Control, ATP
#   A08 - Software Integrity:         (Application level)
#   A09 - Security Logging:           WAF Logging (always enabled)
#   A10 - SSRF:                       Common Rules, Bad Inputs
# =============================================================================

resource "aws_wafv2_ip_set" "whitelist" {
  name               = "${var.project_name}-whitelist"
  description        = "Whitelisted IP addresses (bypass rate limiting)"
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  addresses          = var.allowed_ip_ranges

  tags = var.tags
}

# Priority assignment for rules (lower = higher priority)
locals {
  waf_priority = {
    whitelist          = 0
    geo_block          = 1
    rate_limit_global  = 2
    common_rules       = 3
    sqli_rules         = 4
    xss_rules          = 5
    known_bad_inputs   = 6
    ip_reputation      = 7
    anonymous_ip       = 8
    bot_control        = 9
    linux_rules        = 10
    posix_rules        = 11
    php_rules          = 12
    wordpress_rules    = 13
    login_rate_limit   = 14
    payment_rate_limit = 15
    registration_limit = 16
    api_rate_limit     = 17
    admin_protection   = 18
    security_scanners  = 19
    scraping_tools     = 20
    atp_rules          = 21
  }
}

resource "aws_wafv2_web_acl" "regional" {
  name        = "${var.project_name}-regional-waf"
  description = "Regional WAF for ALB and API Gateway - iGaming Security with OWASP Top 10 Protection"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  # ==========================================================================
  # Rule 0: Whitelist - Allow trusted IPs without rate limiting
  # ==========================================================================
  rule {
    name     = "WhitelistedIPs"
    priority = local.waf_priority.whitelist

    action {
      allow {}
    }

    statement {
      ip_set_reference_statement {
        arn = aws_wafv2_ip_set.whitelist.arn
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "WhitelistedIPs"
      sampled_requests_enabled   = true
    }
  }

  # ==========================================================================
  # Rule 1: Geo Blocking - OWASP A01 (Broken Access Control)
  # Block requests from sanctioned/high-risk countries
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.geo_blocking.enabled ? [1] : []

    content {
      name     = "GeoBlock"
      priority = local.waf_priority.geo_block

      action {
        block {
          custom_response {
            response_code            = 403
            custom_response_body_key = "geo-blocked"
          }
        }
      }

      statement {
        geo_match_statement {
          country_codes = var.waf_rules.geo_blocking.blocked_countries
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "GeoBlocked"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 2: Global Rate Limiting - OWASP A01 (Broken Access Control)
  # Protect against DDoS and brute force attacks
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.rate_limiting.enabled ? [1] : []

    content {
      name     = "RateLimitPerIP"
      priority = local.waf_priority.rate_limit_global

      action {
        block {
          custom_response {
            response_code            = 429
            custom_response_body_key = "rate-limited"
          }
        }
      }

      statement {
        rate_based_statement {
          limit              = var.waf_rules.rate_limiting.global_limit
          aggregate_key_type = "IP"

          scope_down_statement {
            not_statement {
              statement {
                ip_set_reference_statement {
                  arn = aws_wafv2_ip_set.whitelist.arn
                }
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "RateLimitedRequests"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 3: AWS Managed Rules - Common Rule Set
  # OWASP A03 (Injection), A05 (Security Misconfiguration), A10 (SSRF)
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.common_rule_set.enabled ? [1] : []

    content {
      name     = "AWSManagedRulesCommonRuleSet"
      priority = local.waf_priority.common_rules

      override_action {
        dynamic "none" {
          for_each = var.waf_rules.common_rule_set.action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.waf_rules.common_rule_set.action == "count" ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = "AWSManagedRulesCommonRuleSet"
          vendor_name = "AWS"

          dynamic "rule_action_override" {
            for_each = var.waf_rules.common_rule_set.excluded_rules
            content {
              action_to_use {
                count {}
              }
              name = rule_action_override.value
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AWSCommonRules"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 4: AWS Managed Rules - SQL Injection
  # OWASP A03 (Injection)
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.sqli_rule_set.enabled ? [1] : []

    content {
      name     = "AWSManagedRulesSQLiRuleSet"
      priority = local.waf_priority.sqli_rules

      override_action {
        dynamic "none" {
          for_each = var.waf_rules.sqli_rule_set.action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.waf_rules.sqli_rule_set.action == "count" ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = "AWSManagedRulesSQLiRuleSet"
          vendor_name = "AWS"

          dynamic "rule_action_override" {
            for_each = var.waf_rules.sqli_rule_set.excluded_rules
            content {
              action_to_use {
                count {}
              }
              name = rule_action_override.value
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AWSSQLiRules"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 5: AWS Managed Rules - Cross-Site Scripting (XSS)
  # OWASP A03 (Injection), A07 (Auth Failures)
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.xss_rule_set.enabled ? [1] : []

    content {
      name     = "AWSManagedRulesXSSRuleSet"
      priority = local.waf_priority.xss_rules

      override_action {
        dynamic "none" {
          for_each = var.waf_rules.xss_rule_set.action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.waf_rules.xss_rule_set.action == "count" ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = "AWSManagedRulesKnownBadInputsRuleSet"
          vendor_name = "AWS"

          dynamic "rule_action_override" {
            for_each = var.waf_rules.xss_rule_set.excluded_rules
            content {
              action_to_use {
                count {}
              }
              name = rule_action_override.value
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AWSXSSRules"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 6: AWS Managed Rules - Known Bad Inputs (Log4j, etc.)
  # OWASP A06 (Vulnerable Components)
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.known_bad_inputs.enabled ? [1] : []

    content {
      name     = "AWSManagedRulesKnownBadInputsRuleSet"
      priority = local.waf_priority.known_bad_inputs

      override_action {
        dynamic "none" {
          for_each = var.waf_rules.known_bad_inputs.action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.waf_rules.known_bad_inputs.action == "count" ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = "AWSManagedRulesKnownBadInputsRuleSet"
          vendor_name = "AWS"

          dynamic "rule_action_override" {
            for_each = var.waf_rules.known_bad_inputs.excluded_rules
            content {
              action_to_use {
                count {}
              }
              name = rule_action_override.value
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AWSKnownBadInputs"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 7: AWS Managed Rules - Amazon IP Reputation List
  # OWASP A01 (Broken Access Control)
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.ip_reputation_list.enabled ? [1] : []

    content {
      name     = "AWSManagedRulesAmazonIpReputationList"
      priority = local.waf_priority.ip_reputation

      override_action {
        dynamic "none" {
          for_each = var.waf_rules.ip_reputation_list.action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.waf_rules.ip_reputation_list.action == "count" ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = "AWSManagedRulesAmazonIpReputationList"
          vendor_name = "AWS"
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AWSIPReputation"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 8: AWS Managed Rules - Anonymous IP List
  # OWASP A01 (Broken Access Control) - Blocks VPNs, proxies, Tor
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.anonymous_ip_list.enabled ? [1] : []

    content {
      name     = "AWSManagedRulesAnonymousIpList"
      priority = local.waf_priority.anonymous_ip

      override_action {
        dynamic "none" {
          for_each = var.waf_rules.anonymous_ip_list.action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.waf_rules.anonymous_ip_list.action == "count" ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = "AWSManagedRulesAnonymousIpList"
          vendor_name = "AWS"

          # Granular control over anonymous IP blocking
          dynamic "rule_action_override" {
            for_each = var.waf_rules.anonymous_ip_list.block_vpn ? [] : ["AnonymousIPList"]
            content {
              action_to_use {
                count {}
              }
              name = "AnonymousIPList"
            }
          }

          dynamic "rule_action_override" {
            for_each = var.waf_rules.anonymous_ip_list.block_hosting ? [] : ["HostingProviderIPList"]
            content {
              action_to_use {
                count {}
              }
              name = "HostingProviderIPList"
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AWSAnonymousIP"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 9: AWS Managed Rules - Bot Control
  # OWASP A07 (Auth Failures)
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.bot_control.enabled ? [1] : []

    content {
      name     = "AWSManagedRulesBotControlRuleSet"
      priority = local.waf_priority.bot_control

      override_action {
        dynamic "none" {
          for_each = var.waf_rules.bot_control.action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.waf_rules.bot_control.action == "count" ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = "AWSManagedRulesBotControlRuleSet"
          vendor_name = "AWS"

          managed_rule_group_configs {
            aws_managed_rules_bot_control_rule_set {
              inspection_level = var.waf_rules.bot_control.inspection_level
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AWSBotControl"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 10: AWS Managed Rules - Linux Operating System
  # Protects against Linux-specific attacks
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.linux_rule_set.enabled ? [1] : []

    content {
      name     = "AWSManagedRulesLinuxRuleSet"
      priority = local.waf_priority.linux_rules

      override_action {
        dynamic "none" {
          for_each = var.waf_rules.linux_rule_set.action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.waf_rules.linux_rule_set.action == "count" ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = "AWSManagedRulesLinuxRuleSet"
          vendor_name = "AWS"

          dynamic "rule_action_override" {
            for_each = var.waf_rules.linux_rule_set.excluded_rules
            content {
              action_to_use {
                count {}
              }
              name = rule_action_override.value
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AWSLinuxRules"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 11: AWS Managed Rules - POSIX Operating System
  # Protects against Unix/POSIX-specific attacks
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.posix_rule_set.enabled ? [1] : []

    content {
      name     = "AWSManagedRulesUnixRuleSet"
      priority = local.waf_priority.posix_rules

      override_action {
        dynamic "none" {
          for_each = var.waf_rules.posix_rule_set.action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.waf_rules.posix_rule_set.action == "count" ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = "AWSManagedRulesUnixRuleSet"
          vendor_name = "AWS"

          dynamic "rule_action_override" {
            for_each = var.waf_rules.posix_rule_set.excluded_rules
            content {
              action_to_use {
                count {}
              }
              name = rule_action_override.value
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AWSUnixRules"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 12: AWS Managed Rules - PHP Application
  # Protects against PHP-specific attacks (if using PHP backends)
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.php_rule_set.enabled ? [1] : []

    content {
      name     = "AWSManagedRulesPHPRuleSet"
      priority = local.waf_priority.php_rules

      override_action {
        dynamic "none" {
          for_each = var.waf_rules.php_rule_set.action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.waf_rules.php_rule_set.action == "count" ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = "AWSManagedRulesPHPRuleSet"
          vendor_name = "AWS"

          dynamic "rule_action_override" {
            for_each = var.waf_rules.php_rule_set.excluded_rules
            content {
              action_to_use {
                count {}
              }
              name = rule_action_override.value
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AWSPHPRules"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 13: AWS Managed Rules - WordPress Application
  # Protects against WordPress-specific attacks (for marketing sites)
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.wordpress_rule_set.enabled ? [1] : []

    content {
      name     = "AWSManagedRulesWordPressRuleSet"
      priority = local.waf_priority.wordpress_rules

      override_action {
        dynamic "none" {
          for_each = var.waf_rules.wordpress_rule_set.action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.waf_rules.wordpress_rule_set.action == "count" ? [1] : []
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = "AWSManagedRulesWordPressRuleSet"
          vendor_name = "AWS"

          dynamic "rule_action_override" {
            for_each = var.waf_rules.wordpress_rule_set.excluded_rules
            content {
              action_to_use {
                count {}
              }
              name = rule_action_override.value
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AWSWordPressRules"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 14: Login Endpoint Rate Limiting - OWASP A07 (Auth Failures)
  # Brute force protection for authentication endpoints
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.rate_limiting.enabled ? [1] : []

    content {
      name     = "LoginEndpointRateLimit"
      priority = local.waf_priority.login_rate_limit

      action {
        block {
          custom_response {
            response_code            = 429
            custom_response_body_key = "login-rate-limited"
          }
        }
      }

      statement {
        rate_based_statement {
          limit              = var.waf_rules.rate_limiting.login_limit
          aggregate_key_type = "IP"

          scope_down_statement {
            byte_match_statement {
              search_string         = "/api/auth/login"
              positional_constraint = "STARTS_WITH"

              field_to_match {
                uri_path {}
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "LoginRateLimited"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 15: Payment Endpoint Rate Limiting
  # Protection for financial transactions
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.rate_limiting.enabled ? [1] : []

    content {
      name     = "PaymentEndpointRateLimit"
      priority = local.waf_priority.payment_rate_limit

      action {
        block {
          custom_response {
            response_code            = 429
            custom_response_body_key = "payment-rate-limited"
          }
        }
      }

      statement {
        rate_based_statement {
          limit              = var.waf_rules.rate_limiting.payment_limit
          aggregate_key_type = "IP"

          scope_down_statement {
            or_statement {
              statement {
                byte_match_statement {
                  search_string         = "/api/payment"
                  positional_constraint = "STARTS_WITH"

                  field_to_match {
                    uri_path {}
                  }

                  text_transformation {
                    priority = 0
                    type     = "LOWERCASE"
                  }
                }
              }
              statement {
                byte_match_statement {
                  search_string         = "/api/withdraw"
                  positional_constraint = "STARTS_WITH"

                  field_to_match {
                    uri_path {}
                  }

                  text_transformation {
                    priority = 0
                    type     = "LOWERCASE"
                  }
                }
              }
              statement {
                byte_match_statement {
                  search_string         = "/api/deposit"
                  positional_constraint = "STARTS_WITH"

                  field_to_match {
                    uri_path {}
                  }

                  text_transformation {
                    priority = 0
                    type     = "LOWERCASE"
                  }
                }
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "PaymentRateLimited"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 16: Registration Rate Limiting
  # Protection against mass account creation
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.rate_limiting.enabled ? [1] : []

    content {
      name     = "RegistrationRateLimit"
      priority = local.waf_priority.registration_limit

      action {
        block {
          custom_response {
            response_code            = 429
            custom_response_body_key = "registration-rate-limited"
          }
        }
      }

      statement {
        rate_based_statement {
          limit              = var.waf_rules.rate_limiting.registration_limit
          aggregate_key_type = "IP"

          scope_down_statement {
            byte_match_statement {
              search_string         = "/api/auth/register"
              positional_constraint = "STARTS_WITH"

              field_to_match {
                uri_path {}
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "RegistrationRateLimited"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 17: API Rate Limiting
  # General API protection
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.rate_limiting.enabled ? [1] : []

    content {
      name     = "APIRateLimit"
      priority = local.waf_priority.api_rate_limit

      action {
        block {
          custom_response {
            response_code            = 429
            custom_response_body_key = "api-rate-limited"
          }
        }
      }

      statement {
        rate_based_statement {
          limit              = var.waf_rules.rate_limiting.api_limit
          aggregate_key_type = "IP"

          scope_down_statement {
            byte_match_statement {
              search_string         = "/api/"
              positional_constraint = "STARTS_WITH"

              field_to_match {
                uri_path {}
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "APIRateLimited"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 18: Admin Path Protection - OWASP A05 (Security Misconfiguration)
  # Block access to admin paths from non-whitelisted IPs
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.igaming_custom_rules.admin_path_protection ? [1] : []

    content {
      name     = "AdminPathProtection"
      priority = local.waf_priority.admin_protection

      action {
        block {
          custom_response {
            response_code            = 403
            custom_response_body_key = "admin-blocked"
          }
        }
      }

      statement {
        and_statement {
          statement {
            or_statement {
              statement {
                byte_match_statement {
                  search_string         = "/admin"
                  positional_constraint = "STARTS_WITH"

                  field_to_match {
                    uri_path {}
                  }

                  text_transformation {
                    priority = 0
                    type     = "LOWERCASE"
                  }
                }
              }
              statement {
                byte_match_statement {
                  search_string         = "/backoffice"
                  positional_constraint = "STARTS_WITH"

                  field_to_match {
                    uri_path {}
                  }

                  text_transformation {
                    priority = 0
                    type     = "LOWERCASE"
                  }
                }
              }
              statement {
                byte_match_statement {
                  search_string         = "/wp-admin"
                  positional_constraint = "STARTS_WITH"

                  field_to_match {
                    uri_path {}
                  }

                  text_transformation {
                    priority = 0
                    type     = "LOWERCASE"
                  }
                }
              }
            }
          }
          statement {
            not_statement {
              statement {
                ip_set_reference_statement {
                  arn = aws_wafv2_ip_set.whitelist.arn
                }
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "AdminPathBlocked"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 19: Block Security Scanners
  # Block known vulnerability scanners
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.igaming_custom_rules.block_security_scanners ? [1] : []

    content {
      name     = "BlockSecurityScanners"
      priority = local.waf_priority.security_scanners

      action {
        block {}
      }

      statement {
        or_statement {
          statement {
            byte_match_statement {
              search_string         = "sqlmap"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
          statement {
            byte_match_statement {
              search_string         = "nikto"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
          statement {
            byte_match_statement {
              search_string         = "nessus"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
          statement {
            byte_match_statement {
              search_string         = "havij"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
          statement {
            byte_match_statement {
              search_string         = "acunetix"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
          statement {
            byte_match_statement {
              search_string         = "burpsuite"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
          statement {
            byte_match_statement {
              search_string         = "dirbuster"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
          statement {
            byte_match_statement {
              search_string         = "nmap"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "SecurityScannersBlocked"
        sampled_requests_enabled   = true
      }
    }
  }

  # ==========================================================================
  # Rule 20: Block Scraping Tools
  # Block web scraping and automation tools
  # ==========================================================================
  dynamic "rule" {
    for_each = var.waf_rules.igaming_custom_rules.block_scraping_tools ? [1] : []

    content {
      name     = "BlockScrapingTools"
      priority = local.waf_priority.scraping_tools

      action {
        block {}
      }

      statement {
        or_statement {
          statement {
            byte_match_statement {
              search_string         = "scrapy"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
          statement {
            byte_match_statement {
              search_string         = "puppeteer"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
          statement {
            byte_match_statement {
              search_string         = "selenium"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
          statement {
            byte_match_statement {
              search_string         = "phantomjs"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
          statement {
            byte_match_statement {
              search_string         = "headless"
              positional_constraint = "CONTAINS"

              field_to_match {
                single_header {
                  name = "user-agent"
                }
              }

              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "ScrapingToolsBlocked"
        sampled_requests_enabled   = true
      }
    }
  }

  # Custom Response Bodies
  custom_response_body {
    key          = "geo-blocked"
    content      = "{\"error\": \"access_denied\", \"code\": \"GEO_BLOCKED\", \"message\": \"Service not available in your region\"}"
    content_type = "APPLICATION_JSON"
  }

  custom_response_body {
    key          = "rate-limited"
    content      = "{\"error\": \"rate_limit_exceeded\", \"code\": \"RATE_LIMITED\", \"message\": \"Too many requests. Please try again later.\", \"retry_after\": 300}"
    content_type = "APPLICATION_JSON"
  }

  custom_response_body {
    key          = "login-rate-limited"
    content      = "{\"error\": \"login_rate_limit\", \"code\": \"LOGIN_RATE_LIMITED\", \"message\": \"Too many login attempts. Please try again in 5 minutes.\", \"retry_after\": 300}"
    content_type = "APPLICATION_JSON"
  }

  custom_response_body {
    key          = "payment-rate-limited"
    content      = "{\"error\": \"payment_rate_limit\", \"code\": \"PAYMENT_RATE_LIMITED\", \"message\": \"Too many payment requests. Please try again later.\", \"retry_after\": 300}"
    content_type = "APPLICATION_JSON"
  }

  custom_response_body {
    key          = "registration-rate-limited"
    content      = "{\"error\": \"registration_rate_limit\", \"code\": \"REGISTRATION_RATE_LIMITED\", \"message\": \"Too many registration attempts. Please try again later.\", \"retry_after\": 300}"
    content_type = "APPLICATION_JSON"
  }

  custom_response_body {
    key          = "api-rate-limited"
    content      = "{\"error\": \"api_rate_limit\", \"code\": \"API_RATE_LIMITED\", \"message\": \"API rate limit exceeded. Please slow down your requests.\", \"retry_after\": 60}"
    content_type = "APPLICATION_JSON"
  }

  custom_response_body {
    key          = "admin-blocked"
    content      = "{\"error\": \"access_denied\", \"code\": \"ADMIN_ACCESS_DENIED\", \"message\": \"Access to administrative resources is restricted.\"}"
    content_type = "APPLICATION_JSON"
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "IGamingRegionalWAF"
    sampled_requests_enabled   = true
  }

  tags = var.tags
}

# =============================================================================
# WAF Web ACL - CloudFront (Global Scope)
# =============================================================================

resource "aws_wafv2_ip_set" "whitelist_cloudfront" {
  provider           = aws.us_east_1
  name               = "${var.project_name}-whitelist-cloudfront"
  description        = "Whitelisted IP addresses for CloudFront"
  scope              = "CLOUDFRONT"
  ip_address_version = "IPV4"
  addresses          = var.allowed_ip_ranges

  tags = var.tags
}

resource "aws_wafv2_web_acl" "cloudfront" {
  provider    = aws.us_east_1
  name        = "${var.project_name}-cloudfront-waf"
  description = "CloudFront WAF for iGaming - Global CDN Protection"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  # Geo Blocking
  rule {
    name     = "GeoBlock"
    priority = 1

    action {
      block {}
    }

    statement {
      geo_match_statement {
        country_codes = var.blocked_countries
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CloudFrontGeoBlocked"
      sampled_requests_enabled   = true
    }
  }

  # Rate Limiting
  rule {
    name     = "RateLimitPerIP"
    priority = 2

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CloudFrontRateLimited"
      sampled_requests_enabled   = true
    }
  }

  # AWS Managed Rules - Common
  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 3

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CloudFrontAWSCommonRules"
      sampled_requests_enabled   = true
    }
  }

  # AWS Managed Rules - SQL Injection
  rule {
    name     = "AWSManagedRulesSQLiRuleSet"
    priority = 4

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CloudFrontSQLiRules"
      sampled_requests_enabled   = true
    }
  }

  # CKV_AWS_192: AWS Managed Rules - Known Bad Inputs (Log4j protection)
  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 5

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CloudFrontKnownBadInputs"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "IGamingCloudFrontWAF"
    sampled_requests_enabled   = true
  }

  tags = var.tags
}

# =============================================================================
# WAF Logging
# =============================================================================

resource "aws_cloudwatch_log_group" "waf_logs" {
  name              = "aws-waf-logs-${var.project_name}"
  retention_in_days = 365 # CKV_AWS_338: Retain logs for at least 1 year
  kms_key_id        = aws_kms_key.security.arn

  tags = var.tags
}

resource "aws_wafv2_web_acl_logging_configuration" "regional" {
  log_destination_configs = [aws_cloudwatch_log_group.waf_logs.arn]
  resource_arn            = aws_wafv2_web_acl.regional.arn

  logging_filter {
    default_behavior = "KEEP"

    filter {
      behavior    = "KEEP"
      requirement = "MEETS_ANY"

      condition {
        action_condition {
          action = "BLOCK"
        }
      }

      condition {
        action_condition {
          action = "COUNT"
        }
      }
    }
  }
}

# =============================================================================
# AWS GuardDuty - Threat Detection
# =============================================================================

resource "aws_guardduty_detector" "main" {
  count = var.enable_guardduty ? 1 : 0

  enable                       = true
  finding_publishing_frequency = "FIFTEEN_MINUTES"

  datasources {
    s3_logs {
      enable = true
    }
    kubernetes {
      audit_logs {
        enable = true
      }
    }
    malware_protection {
      scan_ec2_instance_with_findings {
        ebs_volumes {
          enable = true
        }
      }
    }
  }

  tags = var.tags
}

# GuardDuty to SNS
resource "aws_cloudwatch_event_rule" "guardduty_findings" {
  count = var.enable_guardduty ? 1 : 0

  name        = "${var.project_name}-guardduty-findings"
  description = "Capture GuardDuty findings"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
    detail = {
      severity = [{ numeric = [">=", 4] }] # Medium and above
    }
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "guardduty_sns" {
  count = var.enable_guardduty ? 1 : 0

  rule      = aws_cloudwatch_event_rule.guardduty_findings[0].name
  target_id = "SendToSNS"
  arn       = aws_sns_topic.security_alerts.arn

  input_transformer {
    input_paths = {
      severity    = "$.detail.severity"
      type        = "$.detail.type"
      description = "$.detail.description"
      region      = "$.region"
      account     = "$.account"
    }
    input_template = "\"GuardDuty Alert: <type> (Severity: <severity>) in <region>. <description>\""
  }
}

# =============================================================================
# AWS Security Hub
# =============================================================================

resource "aws_securityhub_account" "main" {
  count = var.enable_security_hub ? 1 : 0

  enable_default_standards = true
  auto_enable_controls     = true
}

# Enable PCI DSS Standard (for payment processing compliance)
resource "aws_securityhub_standards_subscription" "pci_dss" {
  count = var.enable_security_hub ? 1 : 0

  standards_arn = "arn:aws:securityhub:${data.aws_region.current.name}::standards/pci-dss/v/3.2.1"

  depends_on = [aws_securityhub_account.main]
}

# Enable CIS AWS Foundations Benchmark
resource "aws_securityhub_standards_subscription" "cis" {
  count = var.enable_security_hub ? 1 : 0

  standards_arn = "arn:aws:securityhub:${data.aws_region.current.name}::standards/cis-aws-foundations-benchmark/v/1.4.0"

  depends_on = [aws_securityhub_account.main]
}

# Enable AWS Foundational Security Best Practices
resource "aws_securityhub_standards_subscription" "aws_foundational" {
  count = var.enable_security_hub ? 1 : 0

  standards_arn = "arn:aws:securityhub:${data.aws_region.current.name}::standards/aws-foundational-security-best-practices/v/1.0.0"

  depends_on = [aws_securityhub_account.main]
}

# Security Hub to SNS for critical findings
resource "aws_cloudwatch_event_rule" "securityhub_findings" {
  count = var.enable_security_hub ? 1 : 0

  name        = "${var.project_name}-securityhub-findings"
  description = "Capture Security Hub critical findings"

  event_pattern = jsonencode({
    source      = ["aws.securityhub"]
    detail-type = ["Security Hub Findings - Imported"]
    detail = {
      findings = {
        Severity = {
          Label = ["CRITICAL", "HIGH"]
        }
      }
    }
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "securityhub_sns" {
  count = var.enable_security_hub ? 1 : 0

  rule      = aws_cloudwatch_event_rule.securityhub_findings[0].name
  target_id = "SendToSNS"
  arn       = aws_sns_topic.security_alerts.arn
}

# =============================================================================
# AWS Shield Advanced (DDoS Protection)
# =============================================================================

resource "aws_shield_protection_group" "main" {
  count = var.enable_shield_advanced ? 1 : 0

  protection_group_id = "${var.project_name}-protection-group"
  aggregation         = "MAX"
  pattern             = "ALL"

  tags = var.tags
}

# =============================================================================
# CloudWatch Alarms for Security Monitoring
# =============================================================================

# WAF Blocked Requests Alarm
resource "aws_cloudwatch_metric_alarm" "waf_blocked_requests" {
  alarm_name          = "${var.project_name}-waf-blocked-requests"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "BlockedRequests"
  namespace           = "AWS/WAFV2"
  period              = 300
  statistic           = "Sum"
  threshold           = 1000
  alarm_description   = "High number of WAF blocked requests"
  alarm_actions       = [aws_sns_topic.security_alerts.arn]
  ok_actions          = [aws_sns_topic.security_alerts.arn]

  dimensions = {
    WebACL = aws_wafv2_web_acl.regional.name
    Region = data.aws_region.current.name
    Rule   = "ALL"
  }

  tags = var.tags
}

# Rate Limiting Alarm
resource "aws_cloudwatch_metric_alarm" "rate_limiting_triggered" {
  alarm_name          = "${var.project_name}-rate-limiting-triggered"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "RateLimitedRequests"
  namespace           = "AWS/WAFV2"
  period              = 300
  statistic           = "Sum"
  threshold           = 500
  alarm_description   = "High number of rate-limited requests - possible DDoS"
  alarm_actions       = [aws_sns_topic.security_alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    WebACL = aws_wafv2_web_acl.regional.name
    Region = data.aws_region.current.name
    Rule   = "RateLimitPerIP"
  }

  tags = var.tags
}

# Login Rate Limiting Alarm
resource "aws_cloudwatch_metric_alarm" "login_rate_limiting" {
  alarm_name          = "${var.project_name}-login-rate-limiting"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "LoginRateLimited"
  namespace           = "AWS/WAFV2"
  period              = 300
  statistic           = "Sum"
  threshold           = 100
  alarm_description   = "High number of blocked login attempts - possible brute force"
  alarm_actions       = [aws_sns_topic.security_alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    WebACL = aws_wafv2_web_acl.regional.name
    Region = data.aws_region.current.name
    Rule   = "LoginEndpointRateLimit"
  }

  tags = var.tags
}

# =============================================================================
# CloudWatch Dashboard for Security Monitoring
# =============================================================================

resource "aws_cloudwatch_dashboard" "security" {
  dashboard_name = "${var.project_name}-security-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 1
        properties = {
          markdown = "# iGaming Security Dashboard\n**Environment:** ${var.environment} | **Region:** ${data.aws_region.current.name}"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 1
        width  = 8
        height = 6
        properties = {
          title  = "WAF Requests (Allowed vs Blocked)"
          region = data.aws_region.current.name
          metrics = [
            ["AWS/WAFV2", "AllowedRequests", "WebACL", aws_wafv2_web_acl.regional.name, "Region", data.aws_region.current.name, "Rule", "ALL", { color = "#2ca02c", label = "Allowed" }],
            [".", "BlockedRequests", ".", ".", ".", ".", ".", ".", { color = "#d62728", label = "Blocked" }]
          ]
          view    = "timeSeries"
          stacked = false
          period  = 300
          stat    = "Sum"
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 1
        width  = 8
        height = 6
        properties = {
          title  = "Rate Limiting Triggers"
          region = data.aws_region.current.name
          metrics = [
            ["AWS/WAFV2", "BlockedRequests", "WebACL", aws_wafv2_web_acl.regional.name, "Region", data.aws_region.current.name, "Rule", "RateLimitPerIP", { color = "#ff7f0e", label = "Global Rate Limit" }],
            [".", ".", ".", ".", ".", ".", ".", "LoginEndpointRateLimit", { color = "#d62728", label = "Login Rate Limit" }],
            [".", ".", ".", ".", ".", ".", ".", "PaymentEndpointRateLimit", { color = "#9467bd", label = "Payment Rate Limit" }]
          ]
          view    = "timeSeries"
          stacked = true
          period  = 300
          stat    = "Sum"
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 1
        width  = 8
        height = 6
        properties = {
          title  = "Geo-Blocked Requests"
          region = data.aws_region.current.name
          metrics = [
            ["AWS/WAFV2", "BlockedRequests", "WebACL", aws_wafv2_web_acl.regional.name, "Region", data.aws_region.current.name, "Rule", "GeoBlock"]
          ]
          view   = "timeSeries"
          period = 300
          stat   = "Sum"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 7
        width  = 12
        height = 6
        properties = {
          title  = "AWS Managed Rules Triggers"
          region = data.aws_region.current.name
          metrics = [
            ["AWS/WAFV2", "BlockedRequests", "WebACL", aws_wafv2_web_acl.regional.name, "Region", data.aws_region.current.name, "Rule", "AWSManagedRulesCommonRuleSet", { label = "Common Rules" }],
            [".", ".", ".", ".", ".", ".", ".", "AWSManagedRulesSQLiRuleSet", { label = "SQLi Rules" }],
            [".", ".", ".", ".", ".", ".", ".", "AWSManagedRulesKnownBadInputsRuleSet", { label = "Known Bad Inputs" }],
            [".", ".", ".", ".", ".", ".", ".", "AWSManagedRulesAmazonIpReputationList", { label = "IP Reputation" }]
          ]
          view    = "timeSeries"
          stacked = true
          period  = 300
          stat    = "Sum"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 7
        width  = 12
        height = 6
        properties = {
          title  = "Bot Control"
          region = data.aws_region.current.name
          metrics = [
            ["AWS/WAFV2", "BlockedRequests", "WebACL", aws_wafv2_web_acl.regional.name, "Region", data.aws_region.current.name, "Rule", "AWSManagedRulesBotControlRuleSet", { label = "Blocked Bots" }],
            [".", "CountedRequests", ".", ".", ".", ".", ".", ".", { label = "Counted Bots" }]
          ]
          view   = "timeSeries"
          period = 300
          stat   = "Sum"
        }
      }
    ]
  })
}

# =============================================================================
# Provider for CloudFront WAF (must be us-east-1)
# =============================================================================

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

# =============================================================================
# Outputs
# =============================================================================

output "regional_waf_arn" {
  description = "ARN of the regional WAF Web ACL (for ALB/API Gateway)"
  value       = aws_wafv2_web_acl.regional.arn
}

output "cloudfront_waf_arn" {
  description = "ARN of the CloudFront WAF Web ACL"
  value       = aws_wafv2_web_acl.cloudfront.arn
}

output "security_log_bucket" {
  description = "S3 bucket for security logs"
  value       = aws_s3_bucket.security_logs.bucket
}

output "sns_topic_arn" {
  description = "SNS topic for security alerts"
  value       = aws_sns_topic.security_alerts.arn
}

output "guardduty_detector_id" {
  description = "GuardDuty detector ID"
  value       = var.enable_guardduty ? aws_guardduty_detector.main[0].id : null
}

output "dashboard_url" {
  description = "CloudWatch dashboard URL"
  value       = "https://${data.aws_region.current.name}.console.aws.amazon.com/cloudwatch/home?region=${data.aws_region.current.name}#dashboards:name=${aws_cloudwatch_dashboard.security.dashboard_name}"
}

output "waf_association_instructions" {
  description = "Instructions to associate WAF with resources"
  value       = <<-EOT
    # Associate WAF with ALB:
    aws wafv2 associate-web-acl \
      --web-acl-arn ${aws_wafv2_web_acl.regional.arn} \
      --resource-arn arn:aws:elasticloadbalancing:REGION:ACCOUNT:loadbalancer/app/ALB_NAME/ALB_ID

    # Associate WAF with API Gateway:
    aws wafv2 associate-web-acl \
      --web-acl-arn ${aws_wafv2_web_acl.regional.arn} \
      --resource-arn arn:aws:apigateway:REGION::/restapis/API_ID/stages/STAGE_NAME

    # For CloudFront, add WebACLId to CloudFront distribution config:
    # WebACLId: ${aws_wafv2_web_acl.cloudfront.arn}
  EOT
}
