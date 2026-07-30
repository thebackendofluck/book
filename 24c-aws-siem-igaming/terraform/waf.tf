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
# AWS WAF - Web Application Firewall for iGaming
# =============================================================================
# WAF sits in front of ALB/CloudFront and filters malicious traffic before
# it reaches the application. For iGaming, WAF provides:
#   - Geo-blocking: Enforce license boundaries (only allow NJ/PA/MI traffic)
#   - Rate limiting: Prevent credential stuffing, API abuse, DDoS
#   - Bot protection: Block automated betting bots
#   - SQL injection/XSS: Protect player account and payment endpoints
#   - Custom rules: iGaming-specific protections (bet API rate limiting)
#
# Regulatory justification:
#   NJ DGE 13:69O-1.4: Intrusion detection/prevention
#   All jurisdictions: Geo-fencing as defense-in-depth
#   PCI DSS 6.6: Web application firewall for payment applications
# =============================================================================

# --- WAF Web ACL ---
# The Web ACL is the container for all WAF rules.
resource "aws_wafv2_web_acl" "igaming" {
  name        = "${local.name_prefix}-waf"
  description = "iGaming WAF - geo-blocking, rate limiting, bot protection"
  scope       = var.waf_scope # REGIONAL for ALB, CLOUDFRONT for CloudFront

  default_action {
    allow {}
  }

  # --- Rule 1: AWS Managed Core Rule Set (OWASP Top 10) ---
  # Covers SQL injection, XSS, SSRF, and other common web attacks.
  # This protects player account pages, payment forms, and admin panels.
  rule {
    name     = "aws-managed-core-rules"
    priority = 1

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
      metric_name                = "${local.name_prefix}-core-rules"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 2: SQL Injection Protection ---
  # Extra layer for SQL injection -- player database contains wallets,
  # balances, and PII. A SQLi on the player API is catastrophic.
  rule {
    name     = "aws-managed-sqli"
    priority = 2

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
      metric_name                = "${local.name_prefix}-sqli-rules"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 3: Known Bad Inputs ---
  # Blocks request patterns associated with exploitation of vulnerabilities
  # in common web technologies (Java deserialization, Log4j, etc.).
  rule {
    name     = "aws-managed-known-bad-inputs"
    priority = 3

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
      metric_name                = "${local.name_prefix}-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 4: Bot Control ---
  # Detects and manages bot traffic. Automated betting bots violate
  # terms of service and can exploit promotions/bonuses.
  rule {
    name     = "aws-managed-bot-control"
    priority = 4

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesBotControlRuleSet"
        vendor_name = "AWS"

        managed_rule_group_configs {
          aws_managed_rules_bot_control_rule_set {
            inspection_level = "COMMON"
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-bot-control"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 5: IP Reputation ---
  # Blocks IPs with known bad reputation (scanners, botnets, etc.).
  rule {
    name     = "aws-managed-ip-reputation"
    priority = 5

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-ip-reputation"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 6: Anonymous IP List ---
  # Detects requests from VPNs, Tor nodes, and hosting providers.
  # Players using VPNs to circumvent geo-restrictions is a license violation.
  rule {
    name     = "aws-managed-anonymous-ip"
    priority = 6

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAnonymousIpList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-anonymous-ip"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 7: Geo-Blocking ---
  # Block traffic from countries where the operator is NOT licensed.
  # This is defense-in-depth -- application-layer geo verification is
  # still required, but WAF catches the obvious cases at the edge.
  rule {
    name     = "geo-block-unlicensed"
    priority = 7

    action {
      block {}
    }

    statement {
      not_statement {
        statement {
          geo_match_statement {
            country_codes = var.allowed_countries
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-geo-block"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 8: Global Rate Limiting ---
  # Limit overall requests per IP to prevent DDoS and abuse.
  rule {
    name     = "rate-limit-global"
    priority = 8

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit_global
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 9: Login Endpoint Rate Limiting ---
  # Tighter rate limit on authentication endpoints to prevent
  # credential stuffing attacks.
  rule {
    name     = "rate-limit-login"
    priority = 9

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit_login
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
      metric_name                = "${local.name_prefix}-rate-limit-login"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 10: Bet API Rate Limiting ---
  # Rate limit the bet placement API to prevent automated betting bots
  # and API abuse. Legitimate players don't place 100 bets per minute.
  rule {
    name     = "rate-limit-bet-api"
    priority = 10

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit_bet_api
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = "/api/game/bet"
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
      metric_name                = "${local.name_prefix}-rate-limit-bet"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 11: Withdrawal Rate Limiting ---
  # PCI-DSS 6.5: Protect financial endpoints from automated abuse
  rule {
    name     = "rate-limit-withdrawals"
    priority = 11

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 100
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = "/api/v1/wallet/withdraw"
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
      metric_name                = "${local.name_prefix}-rate-limit-withdraw"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 12: Bonus Claim Rate Limiting ---
  rule {
    name     = "rate-limit-bonus"
    priority = 12

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 100
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = "/api/v1/bonus/"
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
      metric_name                = "${local.name_prefix}-rate-limit-bonus"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 13: Player API Scraping Protection ---
  rule {
    name     = "rate-limit-player-api"
    priority = 13

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 150
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = "/api/v2/pam/players"
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
      metric_name                = "${local.name_prefix}-rate-limit-player-api"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 14: Scanner User-Agent Blocking ---
  rule {
    name     = "block-scanners"
    priority = 14

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
            search_string         = "nuclei"
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
      metric_name                = "${local.name_prefix}-scanner-block"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 15: Oversized Body Blocking ---
  rule {
    name     = "block-oversized-body"
    priority = 15

    action {
      block {}
    }

    statement {
      size_constraint_statement {
        comparison_operator = "GT"
        size                = 131072

        field_to_match {
          body {
            oversize_handling = "MATCH"
          }
        }

        text_transformation {
          priority = 0
          type     = "NONE"
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-oversized-body"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 16: OFAC Sanctioned Countries ---
  rule {
    name     = "geo-block-sanctioned"
    priority = 16

    action {
      block {}
    }

    statement {
      geo_match_statement {
        country_codes = ["CU", "IR", "KP", "SY", "SD", "RU", "BY", "AF", "IQ", "LY", "SO", "YE"]
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-geo-block-sanctioned"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name_prefix}-waf"
    sampled_requests_enabled   = true
  }

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-waf"
    Purpose = "web-application-protection"
  })
}

# --- Web ACL Association ---
# Sixteen rules protect nothing until the Web ACL is attached to something. An
# unassociated Web ACL still bills, still emits metrics for evaluated requests
# (of which there are none), and still looks correct in a Terraform plan, which
# is exactly why this is a common and expensive omission: the operator believes
# the login and withdrawal rate limits are live, and they are not.
#
# For REGIONAL scope, attach to the ALB (or API Gateway stage / AppSync API)
# that fronts the platform. The ARNs come from wherever those live -- usually a
# separate networking module -- so they are an input here rather than a resource
# this module creates.
#
# For CLOUDFRONT scope there is no association resource: set the Web ACL on the
# distribution itself, using the waf_web_acl_arn output from this module.
#
#   resource "aws_cloudfront_distribution" "cdn" {
#     web_acl_id = module.siem.waf_web_acl_arn
#     # ...
#   }
resource "aws_wafv2_web_acl_association" "igaming" {
  for_each = var.waf_scope == "REGIONAL" ? toset(var.waf_protected_resource_arns) : toset([])

  resource_arn = each.value
  web_acl_arn  = aws_wafv2_web_acl.igaming.arn
}

# Plan-time warning rather than a hard error: a fresh deployment legitimately
# has no ALB yet, but shipping a REGIONAL Web ACL with nothing attached should
# never be silent.
check "waf_web_acl_is_attached" {
  assert {
    condition     = var.waf_scope != "REGIONAL" || length(var.waf_protected_resource_arns) > 0
    error_message = "WAF Web ACL has REGIONAL scope but waf_protected_resource_arns is empty: the geo-block, login, bet and withdrawal rate limits are evaluating no traffic. Set waf_protected_resource_arns to the ALB/API Gateway ARNs to protect, or use CLOUDFRONT scope and attach the ACL to the distribution."
  }
}

# --- WAF Logging ---
# Send WAF logs to CloudWatch for analysis. This captures every
# request that WAF evaluates, including blocked requests.
resource "aws_cloudwatch_log_group" "waf" {
  name              = "aws-waf-logs-${local.name_prefix}"
  retention_in_days = var.cloudwatch_retention_days
  kms_key_id        = aws_kms_key.cloudtrail.arn

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-waf-logs"
    Purpose = "waf-request-logging"
  })
}

resource "aws_wafv2_web_acl_logging_configuration" "igaming" {
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  resource_arn            = aws_wafv2_web_acl.igaming.arn

  # Only log blocked requests and requests matching rules
  # to reduce log volume and cost
  logging_filter {
    default_behavior = "DROP"

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

# --- WAF IP Set for Manual Blocking ---
# Allows the security team to manually block IPs (e.g., confirmed
# fraud sources, bonus abuse networks).
resource "aws_wafv2_ip_set" "manual_blocklist" {
  name               = "${local.name_prefix}-manual-blocklist"
  description        = "Manually blocked IPs - fraud, abuse, attacks"
  scope              = var.waf_scope
  ip_address_version = "IPV4"
  addresses          = var.waf_blocked_ips

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-manual-blocklist"
    Purpose = "manual-ip-blocking"
  })
}
