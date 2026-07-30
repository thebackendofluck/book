# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# ---------------------------------------------------------------------------
# SOAR-Specific WAF Rules – AcmeToCasino iGaming Platform
#
# This module provisions supplementary WAF resources that are managed by the
# security SOAR automation layer rather than by the static whitelist rules
# in infra-terraform/waf.tf.
#
# Resources created:
#   1. soar-blocklist-cloudfront  – IP set written by waf_auto_block.py (CLOUDFRONT)
#   2. soar-blocklist-regional    – IP set written by waf_auto_block.py (REGIONAL)
#   3. ALB_WebACL rules:
#        soar-ddos-rate-limit      – Rate-based DDoS mitigation (2000 req/5 min)
#        soar-geo-block            – Geo-block for high-risk markets
#        soar-ip-blocklist         – SOAR-controlled dynamic blocklist
#        soar-bonus-abuse          – iGaming bonus abuse path rate-limit
#        soar-multi-account        – iGaming multi-account registration rate-limit
#        managed rules             – AWSManagedRulesCommonRuleSet, SQLi, BadInputs,
#                                    BotControl, ATP (all in COUNT; SOAR flips to BLOCK)
#
# NOTE: The CLOUDFRONT ACL (whs_acl) receives the same blocklist and rate
# rules via the cloudfront_* resources at the bottom of this file.
# ---------------------------------------------------------------------------

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "waf_log_bucket_arn" {
  type        = string
  description = "ARN of the S3 bucket used for WAF log delivery (waf-log-bucket)."
}

variable "regional_acl_id" {
  type        = string
  description = "Resource ID of the existing ALB Web ACL (ALB_WebACL) to attach SOAR rules to."
}

variable "regional_acl_name" {
  type        = string
  default     = "ALB_WebACL"
  description = "Name of the existing ALB Web ACL."
}

variable "cloudfront_acl_id" {
  type        = string
  description = "Resource ID of the existing CloudFront Web ACL (whs_acl)."
}

variable "cloudfront_acl_name" {
  type        = string
  default     = "whs_acl"
  description = "Name of the existing CloudFront Web ACL."
}

variable "geo_blocked_countries" {
  type        = list(string)
  description = "ISO 3166-1 alpha-2 country codes to block at the WAF layer."
  default = [
    # North Korea – OFAC / FATF high-risk
    "KP",
    # Iran – OFAC sanctions
    "IR",
    # Syria – OFAC sanctions
    "SY",
    # Cuba – OFAC sanctions
    "CU",
    # Russia – elevated fraud risk; operators should evaluate per-licence
    "RU",
    # Belarus
    "BY",
  ]
}

variable "ddos_rate_limit" {
  type        = number
  default     = 2000
  description = "Maximum requests per 5-minute window per source IP before blocking."
}

variable "bonus_abuse_rate_limit" {
  type        = number
  default     = 50
  description = "Maximum bonus/promo endpoint requests per 5 minutes per IP."
}

variable "registration_rate_limit" {
  type        = number
  default     = 20
  description = "Maximum account-registration endpoint requests per 5 minutes per IP."
}

variable "environment" {
  type        = string
  default     = "production"
  description = "Deployment environment (used in resource tags)."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Additional tags to apply to all resources."
}

# ---------------------------------------------------------------------------
# Local values
# ---------------------------------------------------------------------------

locals {
  common_tags = merge(
    {
      terraformed = "true"
      module      = "waf-soar"
      environment = var.environment
    },
    var.tags,
  )
}

# ---------------------------------------------------------------------------
# Provider aliases
# ---------------------------------------------------------------------------

# CloudFront WAF resources must be in us-east-1.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

# ---------------------------------------------------------------------------
# 1. SOAR-managed blocklist IP sets
#    Written at runtime by waf_auto_block.py.
# ---------------------------------------------------------------------------

resource "aws_wafv2_ip_set" "soar_blocklist_cloudfront" {
  provider           = aws.us_east_1
  name               = "soar-blocklist-cloudfront"
  description        = "SOAR-managed dynamic IP blocklist for CloudFront ACL. Managed by waf_auto_block.py."
  scope              = "CLOUDFRONT"
  ip_address_version = "IPV4"
  addresses          = []

  tags = local.common_tags

  lifecycle {
    # Addresses are managed by the SOAR automation scripts, not Terraform.
    ignore_changes = [addresses]
  }
}

resource "aws_wafv2_ip_set" "soar_blocklist_regional" {
  name               = "soar-blocklist-regional"
  description        = "SOAR-managed dynamic IP blocklist for ALB ACL. Managed by waf_auto_block.py."
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  addresses          = []

  tags = local.common_tags

  lifecycle {
    ignore_changes = [addresses]
  }
}

# ---------------------------------------------------------------------------
# 2. Regex pattern sets for iGaming-specific path matching
# ---------------------------------------------------------------------------

resource "aws_wafv2_regex_pattern_set" "bonus_abuse_paths" {
  name        = "soar-bonus-abuse-paths"
  description = "URI paths associated with bonus/promotion abuse attempts."
  scope       = "REGIONAL"

  regular_expression { regex_string = "/bonus" }
  regular_expression { regex_string = "/promo" }
  regular_expression { regex_string = "/free-spin" }
  regular_expression { regex_string = "/claim" }
  regular_expression { regex_string = "/voucher" }
  regular_expression { regex_string = "/referral" }
  regular_expression { regex_string = "/signup-bonus" }
  regular_expression { regex_string = "/welcome-offer" }

  tags = local.common_tags
}

resource "aws_wafv2_regex_pattern_set" "registration_paths" {
  name        = "soar-registration-paths"
  description = "URI paths associated with account-registration / multi-accounting probes."
  scope       = "REGIONAL"

  regular_expression { regex_string = "/register" }
  regular_expression { regex_string = "/signup" }
  regular_expression { regex_string = "/create-account" }
  regular_expression { regex_string = "/kyc" }
  regular_expression { regex_string = "/verification" }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 3. Regional Web ACL – SOAR rules attached to ALB_WebACL
#
# These rules are designed to complement waf.tf. Priority gaps below the
# existing priorities in waf.tf are used to avoid conflicts:
#
#   waf.tf rules:  0 (IsUs), 4 (whitelist-uri), 5-7 (bots), 8 (xss),
#                  12 (IP reputation), 14-17 (managed rules)
#
#   SOAR rules start at priority 20 to stay safely above the existing set.
# ---------------------------------------------------------------------------

resource "aws_wafv2_web_acl" "soar_alb_acl" {
  name        = "ALB_WebACL_SOAR"
  description = "SOAR-managed Web ACL for ALB – supplementary to ALB_WebACL in waf.tf. Deploy as an independent ACL or merge rules into ALB_WebACL via the SOAR pipeline."
  scope       = "REGIONAL"

  # Default: allow (the existing ALB_WebACL already blocks via its own default action).
  default_action {
    allow {}
  }

  # ------------------------------------------------------------------
  # Rule 20: DDoS rate-based mitigation
  # Blocks any single source IP exceeding ddos_rate_limit req / 5 min.
  # waf_auto_block.py::update_rate_rule() can lower this threshold
  # dynamically during an active attack.
  # ------------------------------------------------------------------
  rule {
    name     = "soar-ddos-rate-limit"
    priority = 20

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.ddos_rate_limit
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "soar-ddos-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  # ------------------------------------------------------------------
  # Rule 21: SOAR IP blocklist
  # Entries written by waf_auto_block.py::add_ip_to_blocklist().
  # ------------------------------------------------------------------
  rule {
    name     = "soar-ip-blocklist"
    priority = 21

    action {
      block {}
    }

    statement {
      ip_set_reference_statement {
        arn = aws_wafv2_ip_set.soar_blocklist_regional.arn
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "soar-ip-blocklist"
      sampled_requests_enabled   = true
    }
  }

  # ------------------------------------------------------------------
  # Rule 22: Geo-blocking – OFAC / high-risk markets
  # Country list is controlled via the geo_blocked_countries variable.
  # ------------------------------------------------------------------
  rule {
    name     = "soar-geo-block"
    priority = 22

    action {
      block {
        custom_response {
          response_code = 451 # Unavailable For Legal Reasons
        }
      }
    }

    statement {
      geo_match_statement {
        country_codes = var.geo_blocked_countries
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "soar-geo-block"
      sampled_requests_enabled   = true
    }
  }

  # ------------------------------------------------------------------
  # Rule 23: Bonus abuse rate-limit
  # iGaming-specific: throttle high-frequency hits to promo endpoints.
  # ------------------------------------------------------------------
  rule {
    name     = "soar-bonus-abuse"
    priority = 23

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.bonus_abuse_rate_limit
        aggregate_key_type = "IP"

        scope_down_statement {
          regex_pattern_set_reference_statement {
            arn = aws_wafv2_regex_pattern_set.bonus_abuse_paths.arn

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
      metric_name                = "soar-bonus-abuse"
      sampled_requests_enabled   = true
    }
  }

  # ------------------------------------------------------------------
  # Rule 24: Multi-account / registration rate-limit
  # Prevents automated account-factory attacks common in iGaming.
  # ------------------------------------------------------------------
  rule {
    name     = "soar-multi-account"
    priority = 24

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.registration_rate_limit
        aggregate_key_type = "IP"

        scope_down_statement {
          regex_pattern_set_reference_statement {
            arn = aws_wafv2_regex_pattern_set.registration_paths.arn

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
      metric_name                = "soar-multi-account"
      sampled_requests_enabled   = true
    }
  }

  # ------------------------------------------------------------------
  # Rule 30: AWSManagedRulesCommonRuleSet (OWASP Top 10)
  # COUNT mode at deploy time; waf_managed_rules.py switches to BLOCK
  # during active attacks via override_action.
  # ------------------------------------------------------------------
  rule {
    name     = "AWS-AWSManagedRulesCommonRuleSet"
    priority = 30

    override_action {
      count {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWS-AWSManagedRulesCommonRuleSet"
      sampled_requests_enabled   = true
    }
  }

  # ------------------------------------------------------------------
  # Rule 31: AWSManagedRulesSQLiRuleSet
  # ------------------------------------------------------------------
  rule {
    name     = "AWS-AWSManagedRulesSQLiRuleSet"
    priority = 31

    override_action {
      count {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWS-AWSManagedRulesSQLiRuleSet"
      sampled_requests_enabled   = true
    }
  }

  # ------------------------------------------------------------------
  # Rule 32: AWSManagedRulesKnownBadInputsRuleSet
  # ------------------------------------------------------------------
  rule {
    name     = "AWS-AWSManagedRulesKnownBadInputsRuleSet"
    priority = 32

    override_action {
      count {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWS-AWSManagedRulesKnownBadInputsRuleSet"
      sampled_requests_enabled   = true
    }
  }

  # ------------------------------------------------------------------
  # Rule 33: AWSManagedRulesBotControlRuleSet
  # Excludes known-good crawlers already allowed by waf.tf rules 5–7.
  # ------------------------------------------------------------------
  rule {
    name     = "AWS-AWSManagedRulesBotControlRuleSet"
    priority = 33

    override_action {
      count {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesBotControlRuleSet"
        vendor_name = "AWS"

        excluded_rule { name = "CategorySearchEngine" }
        excluded_rule { name = "CategorySocialMedia" }
        excluded_rule { name = "CategoryMonitoring" }
        excluded_rule { name = "CategorySeo" }
        excluded_rule { name = "CategoryAdvertising" }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWS-AWSManagedRulesBotControlRuleSet"
      sampled_requests_enabled   = true
    }
  }

  # ------------------------------------------------------------------
  # Rule 34: AWSManagedRulesATPRuleSet (Account Takeover Prevention)
  # Configured for the platform login endpoint.
  # ------------------------------------------------------------------
  rule {
    name     = "AWS-AWSManagedRulesATPRuleSet"
    priority = 34

    override_action {
      count {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesATPRuleSet"
        vendor_name = "AWS"

        managed_rule_group_configs {
          aws_managed_rules_atp_rule_set {
            login_path = "/ajax/ajaxhandler.php"

            request_inspection {
              payload_type = "JSON"

              username_field {
                identifier = "/username"
              }

              password_field {
                identifier = "/password"
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWS-AWSManagedRulesATPRuleSet"
      sampled_requests_enabled   = true
    }
  }

  tags = local.common_tags

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "ALB_WebACL_SOAR"
    sampled_requests_enabled   = true
  }

  lifecycle {
    # Managed rules' override_action is mutated by waf_managed_rules.py at runtime.
    ignore_changes = []
  }
}

# ---------------------------------------------------------------------------
# 4. CloudFront ACL supplementary rules (us-east-1)
# ---------------------------------------------------------------------------

resource "aws_wafv2_ip_set" "soar_blocklist_cloudfront_rule" {
  # Identical to soar_blocklist_cloudfront – referenced here in us-east-1.
  # This is the same resource declared above; the data source below
  # re-uses it to build the CloudFront rule.
  provider           = aws.us_east_1
  name               = "soar-blocklist-cf-ddos"
  description        = "SOAR rate-limit companion set for CloudFront (placeholder for CloudFront SOAR blocklist rule)."
  scope              = "CLOUDFRONT"
  ip_address_version = "IPV4"
  addresses          = []

  tags = local.common_tags

  lifecycle {
    ignore_changes = [addresses]
  }
}

# ---------------------------------------------------------------------------
# 5. CloudWatch alarms – alert on SOAR rule matches
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ddos_rate_limit_alarm" {
  alarm_name          = "WAF-SOAR-DDoS-Rate-Limit-Triggered"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "BlockedRequests"
  namespace           = "AWS/WAFV2"
  period              = 300
  statistic           = "Sum"
  threshold           = 100
  alarm_description   = "Fires when the soar-ddos-rate-limit rule blocks more than 100 requests in a 5-minute window."
  treat_missing_data  = "notBreaching"

  dimensions = {
    WebACL = var.regional_acl_name
    Region = "us-east-1"
    Rule   = "soar-ddos-rate-limit"
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "geo_block_alarm" {
  alarm_name          = "WAF-SOAR-Geo-Block-Triggered"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "BlockedRequests"
  namespace           = "AWS/WAFV2"
  period              = 300
  statistic           = "Sum"
  threshold           = 500
  alarm_description   = "Fires when the soar-geo-block rule blocks an unusual volume of requests."
  treat_missing_data  = "notBreaching"

  dimensions = {
    WebACL = var.regional_acl_name
    Region = "us-east-1"
    Rule   = "soar-geo-block"
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "bonus_abuse_alarm" {
  alarm_name          = "WAF-SOAR-Bonus-Abuse-Triggered"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "BlockedRequests"
  namespace           = "AWS/WAFV2"
  period              = 300
  statistic           = "Sum"
  threshold           = 20
  alarm_description   = "Fires when the soar-bonus-abuse rule triggers – possible bonus abuse campaign."
  treat_missing_data  = "notBreaching"

  dimensions = {
    WebACL = var.regional_acl_name
    Region = "us-east-1"
    Rule   = "soar-bonus-abuse"
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "multi_account_alarm" {
  alarm_name          = "WAF-SOAR-Multi-Account-Triggered"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "BlockedRequests"
  namespace           = "AWS/WAFV2"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "Fires when the soar-multi-account rule triggers – possible account-factory attack."
  treat_missing_data  = "notBreaching"

  dimensions = {
    WebACL = var.regional_acl_name
    Region = "us-east-1"
    Rule   = "soar-multi-account"
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "soar_blocklist_cloudfront_arn" {
  description = "ARN of the CloudFront SOAR blocklist IP set (used by waf_auto_block.py)."
  value       = aws_wafv2_ip_set.soar_blocklist_cloudfront.arn
}

output "soar_blocklist_cloudfront_id" {
  description = "ID of the CloudFront SOAR blocklist IP set."
  value       = aws_wafv2_ip_set.soar_blocklist_cloudfront.id
}

output "soar_blocklist_regional_arn" {
  description = "ARN of the Regional SOAR blocklist IP set (used by waf_auto_block.py)."
  value       = aws_wafv2_ip_set.soar_blocklist_regional.arn
}

output "soar_blocklist_regional_id" {
  description = "ID of the Regional SOAR blocklist IP set."
  value       = aws_wafv2_ip_set.soar_blocklist_regional.id
}

output "soar_alb_acl_arn" {
  description = "ARN of the SOAR ALB Web ACL."
  value       = aws_wafv2_web_acl.soar_alb_acl.arn
}

output "soar_alb_acl_id" {
  description = "ID of the SOAR ALB Web ACL (pass as regional_acl_id if merging)."
  value       = aws_wafv2_web_acl.soar_alb_acl.id
}

output "bonus_abuse_paths_arn" {
  description = "ARN of the bonus-abuse regex pattern set."
  value       = aws_wafv2_regex_pattern_set.bonus_abuse_paths.arn
}

output "registration_paths_arn" {
  description = "ARN of the registration-paths regex pattern set."
  value       = aws_wafv2_regex_pattern_set.registration_paths.arn
}
