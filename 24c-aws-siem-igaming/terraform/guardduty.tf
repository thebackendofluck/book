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
# Amazon GuardDuty - Threat Detection for iGaming
# =============================================================================
# GuardDuty continuously monitors VPC Flow Logs, DNS logs, and CloudTrail
# events for threats. For iGaming, it detects:
#   - Credential compromise (admin accounts, API keys)
#   - Data exfiltration (player PII, financial records)
#   - Cryptocurrency mining on compromised game servers
#   - Communication with known C2 servers
#   - Unauthorized API calls (game config changes, wallet manipulation)
#
# Regulatory justification:
#   NJ DGE 13:69O-1.4: "Intrusion detection systems shall be deployed"
#   PA PGCB: "Real-time monitoring of gaming activity"
#   MI MGCB: "Continuous network monitoring"
# =============================================================================

# --- GuardDuty Detector ---
# The detector is the core resource. Once enabled, GuardDuty begins analyzing
# VPC Flow Logs, DNS logs, and CloudTrail events automatically.
resource "aws_guardduty_detector" "main" {
  enable = true

  # Publish findings every 15 minutes (fastest available)
  # For iGaming, speed of detection matters -- a compromised game server
  # can manipulate outcomes in real-time.
  finding_publishing_frequency = "FIFTEEN_MINUTES"

  # S3 data source: detect unusual access to player data buckets
  datasources {
    s3_logs {
      enable = true
    }

    # Kubernetes audit logs: detect compromised containers in EKS game clusters
    kubernetes {
      audit_logs {
        enable = var.enable_eks_protection
      }
    }

    # Malware scanning: detect malicious files in S3 (KYC document uploads)
    # and EBS volumes (game server filesystems)
    malware_protection {
      scan_ec2_instance_with_findings {
        ebs_volumes {
          enable = var.enable_malware_protection
        }
      }
    }
  }

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-guardduty"
    Service = "guardduty"
    Purpose = "threat-detection"
  })
}

# --- GuardDuty Feature: RDS Protection ---
# Detects suspicious login attempts to RDS databases containing player data,
# wallet balances, and transaction records.
resource "aws_guardduty_detector_feature" "rds_protection" {
  count = var.enable_rds_protection ? 1 : 0

  detector_id = aws_guardduty_detector.main.id
  name        = "RDS_LOGIN_EVENTS"
  status      = "ENABLED"
}

# --- GuardDuty Feature: Lambda Protection ---
# Detects compromised Lambda functions -- critical for operators using
# serverless game logic or payment processing functions.
resource "aws_guardduty_detector_feature" "lambda_protection" {
  count = var.enable_lambda_protection ? 1 : 0

  detector_id = aws_guardduty_detector.main.id
  name        = "LAMBDA_NETWORK_LOGS"
  status      = "ENABLED"
}

# --- GuardDuty Feature: Runtime Monitoring ---
# Process-level monitoring on EC2/ECS/EKS -- detects process injection,
# privilege escalation, and suspicious process execution on game servers.
resource "aws_guardduty_detector_feature" "runtime_monitoring" {
  count = var.enable_runtime_monitoring ? 1 : 0

  detector_id = aws_guardduty_detector.main.id
  name        = "RUNTIME_MONITORING"
  status      = "ENABLED"

  additional_configuration {
    name   = "EKS_ADDON_MANAGEMENT"
    status = var.enable_eks_protection ? "ENABLED" : "DISABLED"
  }

  additional_configuration {
    name   = "ECS_FARGATE_AGENT_MANAGEMENT"
    status = "ENABLED"
  }

  additional_configuration {
    name   = "EC2_AGENT_MANAGEMENT"
    status = "ENABLED"
  }
}

# --- GuardDuty Custom Threat List ---
# Upload known bad IPs associated with gambling fraud, bonus abuse networks,
# and previous attackers. This supplements the AWS-managed threat intelligence.
resource "aws_guardduty_threatintelset" "igaming_threats" {
  count = var.custom_threat_list_s3_uri != "" ? 1 : 0

  activate    = true
  detector_id = aws_guardduty_detector.main.id
  format      = "TXT"
  location    = var.custom_threat_list_s3_uri
  name        = "${local.name_prefix}-igaming-threat-list"

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-igaming-threats"
    Purpose = "custom-threat-intelligence"
  })
}

# --- GuardDuty Trusted IP List ---
# Whitelist office IPs, VPN endpoints, and known-good third-party provider IPs
# to reduce false positives from game provider API calls.
resource "aws_guardduty_ipset" "trusted_ips" {
  count = var.trusted_ip_list_s3_uri != "" ? 1 : 0

  activate    = true
  detector_id = aws_guardduty_detector.main.id
  format      = "TXT"
  location    = var.trusted_ip_list_s3_uri
  name        = "${local.name_prefix}-trusted-ips"

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-trusted-ips"
    Purpose = "false-positive-reduction"
  })
}

# --- EventBridge Rule for GuardDuty Findings ---
# Routes GuardDuty findings to SNS for alerting and Lambda for processing.
# HIGH and CRITICAL findings trigger immediate alerts.
resource "aws_cloudwatch_event_rule" "guardduty_findings" {
  name        = "${local.name_prefix}-guardduty-findings"
  description = "Route GuardDuty findings for iGaming security alerting"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
    detail = {
      severity = [{ numeric = [">=", 4.0] }] # MEDIUM and above
    }
  })

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-guardduty-events"
  })
}

# Target: Send HIGH/CRITICAL findings to the critical SNS topic
resource "aws_cloudwatch_event_target" "guardduty_to_sns" {
  rule      = aws_cloudwatch_event_rule.guardduty_findings.name
  target_id = "guardduty-to-sns-critical"
  arn       = aws_sns_topic.security_critical.arn
}

# Target: Send all findings to Lambda for enrichment and routing
resource "aws_cloudwatch_event_target" "guardduty_to_lambda" {
  rule      = aws_cloudwatch_event_rule.guardduty_findings.name
  target_id = "guardduty-to-lambda"
  arn       = aws_lambda_function.alert_processor.arn
}
