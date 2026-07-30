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
# SNS Topics - Alert Routing for iGaming Security
# =============================================================================
# SNS topics are the fanout mechanism for security alerts. Each topic
# corresponds to a severity level and team responsibility:
#
#   - security-critical: PagerDuty, on-call phone, Slack #security-critical
#   - compliance-alerts: Compliance team email, Jira ticket creation
#   - fraud-alerts: Fraud team Slack, case management system
#   - security-info: Low-priority notifications, daily digest
#
# Regulatory justification:
#   NJ DGE 13:69O-1.7: "Incident response procedures"
#   PA PGCB: "Real-time alerting on security events"
# =============================================================================

# --- Critical Security Alerts ---
# GuardDuty HIGH/CRITICAL, unauthorized API calls, root account usage.
# These wake people up at 3 AM.
resource "aws_sns_topic" "security_critical" {
  name              = "${local.name_prefix}-security-critical"
  kms_master_key_id = aws_kms_key.cloudtrail.id

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-security-critical"
    Severity = "CRITICAL"
    Purpose  = "immediate-response-alerts"
  })
}

# Email subscription for critical alerts
resource "aws_sns_topic_subscription" "security_critical_email" {
  count = var.security_team_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.security_critical.arn
  protocol  = "email"
  endpoint  = var.security_team_email
}

# Webhook subscription (PagerDuty, Opsgenie, etc.)
resource "aws_sns_topic_subscription" "security_critical_webhook" {
  count = var.pagerduty_webhook_url != "" ? 1 : 0

  topic_arn = aws_sns_topic.security_critical.arn
  protocol  = "https"
  endpoint  = var.pagerduty_webhook_url
}

# --- Compliance Alerts ---
# Security Hub failures, Config non-compliance, compliance score changes.
# These go to the compliance team during business hours.
resource "aws_sns_topic" "compliance_alerts" {
  name              = "${local.name_prefix}-compliance-alerts"
  kms_master_key_id = aws_kms_key.cloudtrail.id

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-compliance-alerts"
    Severity = "HIGH"
    Purpose  = "compliance-violation-alerts"
  })
}

resource "aws_sns_topic_subscription" "compliance_email" {
  count = var.compliance_team_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.compliance_alerts.arn
  protocol  = "email"
  endpoint  = var.compliance_team_email
}

# --- Fraud Alerts ---
# Suspicious betting patterns, multi-accounting, AML triggers.
# These go to the fraud/risk team.
resource "aws_sns_topic" "fraud_alerts" {
  name              = "${local.name_prefix}-fraud-alerts"
  kms_master_key_id = aws_kms_key.cloudtrail.id

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-fraud-alerts"
    Severity = "HIGH"
    Purpose  = "fraud-detection-alerts"
  })
}

resource "aws_sns_topic_subscription" "fraud_email" {
  count = var.fraud_team_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.fraud_alerts.arn
  protocol  = "email"
  endpoint  = var.fraud_team_email
}

# --- Informational Security Alerts ---
# Low-severity findings, daily summaries, non-urgent notifications.
resource "aws_sns_topic" "security_info" {
  name              = "${local.name_prefix}-security-info"
  kms_master_key_id = aws_kms_key.cloudtrail.id

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-security-info"
    Severity = "LOW"
    Purpose  = "informational-alerts"
  })
}

# --- SNS Topic Policies ---
# Allow EventBridge and CloudWatch to publish to topics.
resource "aws_sns_topic_policy" "security_critical" {
  arn = aws_sns_topic.security_critical.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowEventBridgePublish"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.security_critical.arn
      },
      {
        Sid    = "AllowCloudWatchPublish"
        Effect = "Allow"
        Principal = {
          Service = "cloudwatch.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.security_critical.arn
      }
    ]
  })
}

resource "aws_sns_topic_policy" "compliance_alerts" {
  arn = aws_sns_topic.compliance_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowEventBridgePublish"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.compliance_alerts.arn
      },
      {
        Sid    = "AllowConfigPublish"
        Effect = "Allow"
        Principal = {
          Service = "config.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.compliance_alerts.arn
      }
    ]
  })
}

resource "aws_sns_topic_policy" "fraud_alerts" {
  arn = aws_sns_topic.fraud_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudWatchPublish"
        Effect = "Allow"
        Principal = {
          Service = "cloudwatch.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.fraud_alerts.arn
      }
    ]
  })
}
