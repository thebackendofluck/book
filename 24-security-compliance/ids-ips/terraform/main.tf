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
# terraform/main.tf
# AWS Network Firewall infrastructure for iGaming IDS/IPS deployment
# =============================================================================

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "environment" {
  description = "Deployment environment (e.g. production, staging)"
  type        = string
  default     = "production"
}

variable "account_id" {
  description = "AWS account ID"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "player_api_cidr" {
  description = "CIDR block for the player-facing API tier"
  type        = string
  default     = "10.0.0.0/16"
}

variable "firewall_subnet_ids" {
  description = "List of subnet IDs for firewall endpoints (one per AZ)"
  type        = list(string)
}

variable "vpc_id" {
  description = "VPC ID where the firewall is deployed"
  type        = string
}

variable "flow_log_bucket_name" {
  description = "S3 bucket name for Network Firewall flow logs"
  type        = string
  default     = ""
}

locals {
  name_prefix = "igaming-${var.environment}"

  common_tags = {
    Environment        = var.environment
    Project            = "iGaming-IDS"
    ManagedBy          = "Terraform"
    Compliance         = "PCI-DSS,GDPR,UKGC"
    DataClassification = "Confidential"
    CostCenter         = "security-ops"
  }

  flow_bucket_name = (
    var.flow_log_bucket_name != ""
    ? var.flow_log_bucket_name
    : "${local.name_prefix}-nfw-flow-logs-${var.account_id}"
  )
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.common_tags
  }
}

# ---------------------------------------------------------------------------
# Stateful rule group – iGaming-specific Suricata rules
# ---------------------------------------------------------------------------

resource "aws_networkfirewall_rule_group" "igaming_stateful" {
  name        = "${local.name_prefix}-igaming-stateful-rules"
  description = "iGaming IDS stateful Suricata rules: payment fraud, API abuse, AML, account security, compliance"
  type        = "STATEFUL"
  capacity    = 5000

  rule_group {
    stateful_rule_options {
      rule_order = "STRICT_ORDER"
    }

    rules_source {
      rules_string = <<-RULES
        # -----------------------------------------------------------------------
        # Payment fraud detection (SID range 9000001-9000099)
        # -----------------------------------------------------------------------
        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming Payment Rapid Sequence Multiple Payment Methods"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/api/v"; content:"/payment"; distance:0; \
           threshold: type both, track by_src, count 5, seconds 60; \
           sid:9000001; rev:1; classtype:policy-violation; \
           metadata:policy balanced-ips alert, \
             created_at 2024_01_01, updated_at 2024_01_01;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming Payment Large Single Deposit Anomaly"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/deposit"; \
           http.request_body; content:"amount"; \
           pcre:"/\"amount\"\s*:\s*[0-9]{5,}/"; \
           sid:9000002; rev:1; classtype:policy-violation; \
           metadata:policy balanced-ips alert;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming Payment Card BIN Enumeration Attempt"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/payment/validate"; \
           threshold: type threshold, track by_src, count 10, seconds 120; \
           sid:9000003; rev:1; classtype:attempted-recon; \
           metadata:policy balanced-ips alert;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming Payment Chargeback Pattern Detected"; \
           flow:established,to_server; \
           http.uri; content:"/chargeback"; \
           threshold: type both, track by_src, count 3, seconds 3600; \
           sid:9000010; rev:1; classtype:policy-violation;)

        # -----------------------------------------------------------------------
        # API abuse detection (SID range 9000100-9000199)
        # -----------------------------------------------------------------------
        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming API Rate Limit Bypass Attempt – X-Forwarded-For Cycling"; \
           flow:established,to_server; \
           http.header; content:"X-Forwarded-For"; \
           pcre:"/X-Forwarded-For\s*:\s*([0-9]{1,3}\.){3}[0-9]{1,3}/"; \
           threshold: type threshold, track by_dst, count 200, seconds 60; \
           sid:9000100; rev:1; classtype:attempted-dos;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming API Credential Stuffing – Login Burst"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/auth/login"; \
           threshold: type both, track by_src, count 30, seconds 60; \
           sid:9000101; rev:1; classtype:attempted-user;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming API GraphQL Introspection Abuse"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/graphql"; \
           http.request_body; content:"__schema"; \
           threshold: type threshold, track by_src, count 5, seconds 300; \
           sid:9000110; rev:1; classtype:attempted-recon;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming API Odds Feed Scraping Detected"; \
           flow:established,to_server; \
           http.uri; content:"/api/odds"; \
           threshold: type both, track by_src, count 500, seconds 60; \
           sid:9000120; rev:1; classtype:policy-violation;)

        # -----------------------------------------------------------------------
        # Account security (SID range 9000200-9000299)
        # -----------------------------------------------------------------------
        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming Account Takeover – Password Reset Flood"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/account/reset-password"; \
           threshold: type both, track by_src, count 5, seconds 300; \
           sid:9000200; rev:1; classtype:attempted-user;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming Bonus Abuse – Multi-Account Registration Pattern"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/register"; \
           threshold: type both, track by_src, count 3, seconds 3600; \
           sid:9000210; rev:1; classtype:policy-violation;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming Account Profile Mass Modification"; \
           flow:established,to_server; \
           http.method; content:"PUT"; \
           http.uri; content:"/account/profile"; \
           threshold: type threshold, track by_src, count 10, seconds 60; \
           sid:9000220; rev:1; classtype:policy-violation;)

        # -----------------------------------------------------------------------
        # AML detection (SID range 9000300-9000399)
        # -----------------------------------------------------------------------
        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming AML Structuring – Deposits Below Reporting Threshold"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/deposit"; \
           http.request_body; content:"amount"; \
           pcre:"/\"amount\"\s*:\s*9[0-9]{3}(\.[0-9]+)?[^0-9]/"; \
           threshold: type both, track by_src, count 5, seconds 86400; \
           sid:9000300; rev:1; classtype:policy-violation; \
           metadata:compliance AML,POCA2002;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming AML Round-Trip Suspicious Withdrawal Pattern"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/withdraw"; \
           threshold: type both, track by_src, count 5, seconds 3600; \
           sid:9000310; rev:1; classtype:policy-violation; \
           metadata:compliance AML;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming AML High-Value Transfer – SAR Trigger Candidate"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/transfer"; \
           http.request_body; content:"amount"; \
           pcre:"/\"amount\"\s*:\s*[1-9][0-9]{4,}/"; \
           sid:9000320; rev:1; classtype:policy-violation; \
           metadata:compliance AML,SAR;)

        # -----------------------------------------------------------------------
        # Compliance (SID range 9000400-9000499)
        # -----------------------------------------------------------------------
        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming Compliance Geolocation Restriction Bypass Attempt"; \
           flow:established,to_server; \
           http.header; content:"X-Country-Code"; \
           pcre:"/X-Country-Code\s*:\s*(US|IR|KP|CU|SY)/i"; \
           sid:9000400; rev:1; classtype:policy-violation; \
           metadata:compliance OFAC,sanctions;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming Compliance Self-Exclusion Bypass Attempt"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/session/new"; \
           sid:9000410; rev:1; classtype:policy-violation; \
           metadata:compliance UKGC,RG;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming Compliance KYC Document Endpoint Mass Access"; \
           flow:established,to_server; \
           http.uri; content:"/kyc/documents"; \
           threshold: type threshold, track by_src, count 20, seconds 300; \
           sid:9000420; rev:1; classtype:attempted-recon; \
           metadata:compliance GDPR,KYC;)

        alert http $EXTERNAL_NET any -> $HOME_NET any \
          (msg:"iGaming Compliance Responsible Gambling Limit Override Attempt"; \
           flow:established,to_server; \
           http.method; content:"POST"; \
           http.uri; content:"/limits"; \
           http.request_body; content:"limit_type"; \
           threshold: type threshold, track by_src, count 10, seconds 600; \
           sid:9000430; rev:1; classtype:policy-violation; \
           metadata:compliance UKGC,GDPR;)
      RULES
    }
  }

  tags = merge(local.common_tags, {
    Name      = "${local.name_prefix}-igaming-stateful-rules"
    RuleType  = "stateful"
  })
}

# ---------------------------------------------------------------------------
# Stateless rule group – rate limiting / early drop
# ---------------------------------------------------------------------------

resource "aws_networkfirewall_rule_group" "rate_limiting_stateless" {
  name        = "${local.name_prefix}-rate-limiting-stateless"
  description = "Stateless rate limiting rules for iGaming player API traffic"
  type        = "STATELESS"
  capacity    = 100

  rule_group {
    rules_source {
      stateless_rules_and_custom_actions {
        stateless_rule {
          priority = 10
          rule_definition {
            actions = ["aws:drop"]
            match_attributes {
              # Drop TCP SYN floods from a single source exceeding threshold
              protocols = [6]  # TCP
              source {
                address_definition = "0.0.0.0/0"
              }
              destination {
                address_definition = var.player_api_cidr
              }
              destination_port {
                from_port = 443
                to_port   = 443
              }
              tcp_flag {
                flags = ["SYN"]
                masks = ["SYN", "ACK"]
              }
            }
          }
        }

        stateless_rule {
          priority = 20
          rule_definition {
            actions = ["aws:forward_to_sfe"]
            match_attributes {
              protocols = [6]  # TCP
              source {
                address_definition = "0.0.0.0/0"
              }
              destination {
                address_definition = var.player_api_cidr
              }
              destination_port {
                from_port = 443
                to_port   = 443
              }
            }
          }
        }

        stateless_rule {
          priority = 30
          rule_definition {
            actions = ["aws:forward_to_sfe"]
            match_attributes {
              protocols = [17]  # UDP
              source {
                address_definition = "0.0.0.0/0"
              }
              destination {
                address_definition = var.player_api_cidr
              }
            }
          }
        }

        # Default: pass all other traffic to stateful engine
        stateless_rule {
          priority = 1000
          rule_definition {
            actions = ["aws:forward_to_sfe"]
            match_attributes {
              source {
                address_definition = "0.0.0.0/0"
              }
              destination {
                address_definition = "0.0.0.0/0"
              }
            }
          }
        }
      }
    }
  }

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-rate-limiting-stateless"
    RuleType = "stateless"
  })
}

# ---------------------------------------------------------------------------
# Firewall policy
# ---------------------------------------------------------------------------

resource "aws_networkfirewall_firewall_policy" "igaming" {
  name        = "${local.name_prefix}-firewall-policy"
  description = "iGaming IDS/IPS firewall policy combining stateless rate limiting and stateful Suricata inspection"

  firewall_policy {
    stateless_default_actions          = ["aws:forward_to_sfe"]
    stateless_fragment_default_actions = ["aws:forward_to_sfe"]

    stateless_rule_group_reference {
      priority     = 10
      resource_arn = aws_networkfirewall_rule_group.rate_limiting_stateless.arn
    }

    stateful_engine_options {
      rule_order              = "STRICT_ORDER"
      stream_exception_policy = "DROP"
    }

    stateful_default_actions = ["aws:drop_strict"]

    stateful_rule_group_reference {
      priority     = 100
      resource_arn = aws_networkfirewall_rule_group.igaming_stateful.arn
    }
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-firewall-policy"
  })
}

# ---------------------------------------------------------------------------
# Network Firewall
# ---------------------------------------------------------------------------

resource "aws_networkfirewall_firewall" "igaming" {
  name                = "${local.name_prefix}-network-firewall"
  description         = "iGaming Network Firewall – multi-AZ stateful inspection"
  vpc_id              = var.vpc_id
  firewall_policy_arn = aws_networkfirewall_firewall_policy.igaming.arn

  # Protect policy from accidental changes via the console
  firewall_policy_change_protection = true
  subnet_change_protection          = true

  dynamic "subnet_mapping" {
    for_each = var.firewall_subnet_ids
    content {
      subnet_id = subnet_mapping.value
    }
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-network-firewall"
  })
}

# ---------------------------------------------------------------------------
# S3 bucket – flow log storage
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "flow_logs" {
  bucket        = local.flow_bucket_name
  force_destroy = false

  tags = merge(local.common_tags, {
    Name              = local.flow_bucket_name
    DataRetentionDays = "2557"
    Purpose           = "network-firewall-flow-logs"
  })
}

resource "aws_s3_bucket_versioning" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id

  rule {
    id     = "tiered-retention"
    status = "Enabled"

    # Transition to Intelligent-Tiering after 90 days
    transition {
      days          = 90
      storage_class = "INTELLIGENT_TIERING"
    }

    # Transition to Glacier after 365 days
    transition {
      days          = 365
      storage_class = "GLACIER"
    }

    # Expire objects after 2557 days (~7 years) for regulatory compliance
    expiration {
      days = 2557
    }

    # Clean up incomplete multipart uploads
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ---------------------------------------------------------------------------
# CloudWatch log group – alert logs
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "nfw_alerts" {
  name              = "/aws/network-firewall/${local.name_prefix}/alerts"
  retention_in_days = 365

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-nfw-alerts"
    Purpose = "network-firewall-alert-logs"
  })
}

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

resource "aws_networkfirewall_logging_configuration" "igaming" {
  firewall_arn = aws_networkfirewall_firewall.igaming.arn

  logging_configuration {
    # Flow logs → S3 (cost-effective long-term storage)
    log_destination_config {
      log_type             = "FLOW"
      log_destination_type = "S3"
      log_destination = {
        bucketName = aws_s3_bucket.flow_logs.bucket
        prefix     = "flow-logs/${var.environment}"
      }
    }

    # Alert logs → CloudWatch (real-time SIEM/dashboarding)
    log_destination_config {
      log_type             = "ALERT"
      log_destination_type = "CloudWatchLogs"
      log_destination = {
        logGroup = aws_cloudwatch_log_group.nfw_alerts.name
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "firewall_arn" {
  description = "ARN of the Network Firewall"
  value       = aws_networkfirewall_firewall.igaming.arn
}

output "firewall_endpoint_ids" {
  description = "Map of AZ → firewall endpoint ID"
  value = {
    for s in aws_networkfirewall_firewall.igaming.firewall_status[0].sync_states :
    s.availability_zone => s.attachment[0].endpoint_id
  }
}

output "flow_log_bucket" {
  description = "S3 bucket name for flow logs"
  value       = aws_s3_bucket.flow_logs.bucket
}

output "alert_log_group" {
  description = "CloudWatch log group name for alert logs"
  value       = aws_cloudwatch_log_group.nfw_alerts.name
}

output "stateful_rule_group_arn" {
  description = "ARN of the iGaming stateful rule group"
  value       = aws_networkfirewall_rule_group.igaming_stateful.arn
}
