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
# CloudWatch - Log Aggregation, Metrics, and Alarms for iGaming
# =============================================================================
# CloudWatch is the real-time monitoring layer. Application logs from game
# servers, payment processors, and authentication services flow into CloudWatch
# Log Groups. Metric filters extract security-relevant patterns, and alarms
# fire when thresholds are breached.
#
# For iGaming, CloudWatch detects:
#   - Credential stuffing attacks (rapid failed logins)
#   - Suspicious betting patterns (money laundering indicators)
#   - Admin access outside business hours
#   - Multi-accounting attempts (same IP, multiple accounts)
#   - Unusual withdrawal patterns (structuring detection)
#
# Regulatory justification:
#   NJ DGE 13:69O-1.1: Continuous monitoring
#   PA PGCB: Real-time monitoring of gaming activity
#   AML/BSA: Suspicious activity detection and reporting
# =============================================================================

# --- Application Log Groups ---
# Each application tier gets its own log group for access control and retention.

resource "aws_cloudwatch_log_group" "application" {
  name              = "/igaming/${local.name_prefix}/application"
  retention_in_days = var.cloudwatch_retention_days
  kms_key_id        = aws_kms_key.cloudtrail.arn

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-app-logs"
    Purpose = "application-event-logging"
  })
}

resource "aws_cloudwatch_log_group" "authentication" {
  name              = "/igaming/${local.name_prefix}/authentication"
  retention_in_days = var.cloudwatch_retention_days
  kms_key_id        = aws_kms_key.cloudtrail.arn

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-auth-logs"
    Purpose = "authentication-event-logging"
  })
}

resource "aws_cloudwatch_log_group" "payment" {
  name              = "/igaming/${local.name_prefix}/payment"
  retention_in_days = var.cloudwatch_retention_days
  kms_key_id        = aws_kms_key.cloudtrail.arn

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-payment-logs"
    Purpose = "payment-transaction-logging"
  })
}

resource "aws_cloudwatch_log_group" "game_events" {
  name              = "/igaming/${local.name_prefix}/game-events"
  retention_in_days = var.cloudwatch_retention_days
  kms_key_id        = aws_kms_key.cloudtrail.arn

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-game-logs"
    Purpose = "game-event-logging"
  })
}

resource "aws_cloudwatch_log_group" "security_events" {
  name              = "/igaming/${local.name_prefix}/security"
  retention_in_days = var.cloudwatch_retention_days
  kms_key_id        = aws_kms_key.cloudtrail.arn

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-security-logs"
    Purpose = "security-event-logging"
  })
}

# =============================================================================
# Metric Filters - Detect Suspicious Patterns
# =============================================================================

# --- Failed Login Detection (Credential Stuffing) ---
# More than 10 failed logins per minute from the same source indicates
# an automated credential stuffing attack.
resource "aws_cloudwatch_log_metric_filter" "failed_logins" {
  name           = "${local.name_prefix}-failed-logins"
  log_group_name = aws_cloudwatch_log_group.authentication.name
  pattern        = "{ $.event_type = \"LOGIN_FAILED\" }"

  metric_transformation {
    name          = "FailedLoginCount"
    namespace     = "iGaming/Security"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "failed_logins_high" {
  alarm_name          = "${local.name_prefix}-failed-logins-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FailedLoginCount"
  namespace           = "iGaming/Security"
  period              = 300 # 5 minutes
  statistic           = "Sum"
  threshold           = var.failed_login_threshold
  alarm_description   = "High rate of failed logins detected - potential credential stuffing attack"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.security_critical.arn]
  ok_actions    = [aws_sns_topic.security_critical.arn]

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-failed-login-alarm"
    Severity = "HIGH"
  })
}

# --- Suspicious Bet Amount Detection (AML) ---
# Bets exceeding the configured threshold may indicate money laundering.
# BSA/AML requires reporting of suspicious financial activity.
resource "aws_cloudwatch_log_metric_filter" "large_bets" {
  name           = "${local.name_prefix}-large-bets"
  log_group_name = aws_cloudwatch_log_group.game_events.name
  pattern        = "{ $.event_type = \"BET_PLACED\" && $.amount > ${var.suspicious_bet_threshold} }"

  metric_transformation {
    name          = "LargeBetCount"
    namespace     = "iGaming/Fraud"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "large_bets_high" {
  alarm_name          = "${local.name_prefix}-large-bets-alert"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "LargeBetCount"
  namespace           = "iGaming/Fraud"
  period              = 600 # 10 minutes
  statistic           = "Sum"
  threshold           = var.large_bet_count_threshold
  alarm_description   = "Multiple large bets detected - potential money laundering indicator"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.fraud_alerts.arn]

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-large-bet-alarm"
    Severity = "HIGH"
    Category = "AML"
  })
}

# --- Withdrawal Volume (Platform-Wide) ---
# What this measures, precisely: how many withdrawal requests the whole platform
# received in the last hour. It is a volume signal, not a per-player one. A
# metric filter counts matching log lines; it cannot group by player, compare
# amounts across events, or hold state between them, so no filter in this file
# can detect "the same player made six withdrawals in ten minutes."
#
# Naming this alarm after structuring would be worse than leaving it out. An
# analyst who believes structuring is covered here stops looking for it, and the
# real per-account correlation (Athena over the app-logs/ archive in S3, or the
# fraud pipeline in Chapter 19) never gets built. Alarms must say what they
# measure.
resource "aws_cloudwatch_log_metric_filter" "withdrawal_volume" {
  name           = "${local.name_prefix}-withdrawal-volume"
  log_group_name = aws_cloudwatch_log_group.payment.name
  pattern        = "{ $.event_type = \"WITHDRAWAL_REQUEST\" }"

  metric_transformation {
    name          = "WithdrawalRequestCount"
    namespace     = "iGaming/Fraud"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "withdrawal_volume" {
  alarm_name          = "${local.name_prefix}-withdrawal-volume-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "WithdrawalRequestCount"
  namespace           = "iGaming/Fraud"
  period              = 3600 # 1 hour
  statistic           = "Sum"
  threshold           = var.withdrawal_volume_threshold
  alarm_description   = "Platform-wide withdrawal request volume above the hourly baseline. Volume signal only: no per-player or per-amount correlation. Investigate alongside the structuring-band alarm and the payment log archive."
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.fraud_alerts.arn]

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-withdrawal-volume-alarm"
    Severity = "MEDIUM"
    Category = "AML"
  })
}

# --- Withdrawals in the Structuring Band ---
# The one piece of structuring detection a metric filter genuinely can do: an
# amount comparison on a single event. Withdrawals deliberately sized just under
# the currency transaction reporting threshold are the classic structuring
# pattern, and a JSON filter pattern can express that band directly.
#
# A single withdrawal in the band is unremarkable. A cluster of them in one hour
# is the signal, which is why the alarm counts rather than fires on the first.
# The correlation this still does not do -- same player, same beneficiary, same
# device across the band -- belongs to the fraud pipeline, not to CloudWatch.
check "structuring_band_is_a_band" {
  assert {
    condition     = var.structuring_band_floor < var.cash_report_threshold
    error_message = "structuring_band_floor must be below cash_report_threshold, otherwise the structuring-band filter matches nothing and the alarm never fires."
  }
}

resource "aws_cloudwatch_log_metric_filter" "structuring_band_withdrawals" {
  name           = "${local.name_prefix}-structuring-band-withdrawals"
  log_group_name = aws_cloudwatch_log_group.payment.name
  pattern        = "{ $.event_type = \"WITHDRAWAL_REQUEST\" && $.amount >= ${var.structuring_band_floor} && $.amount < ${var.cash_report_threshold} }"

  metric_transformation {
    name          = "StructuringBandWithdrawalCount"
    namespace     = "iGaming/Fraud"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "structuring_band_withdrawals" {
  alarm_name          = "${local.name_prefix}-structuring-band-withdrawals"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "StructuringBandWithdrawalCount"
  namespace           = "iGaming/Fraud"
  period              = 3600 # 1 hour
  statistic           = "Sum"
  threshold           = var.structuring_band_count_threshold
  alarm_description   = "Multiple withdrawals sized just below the cash reporting threshold within one hour - structuring indicator requiring SAR review"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.fraud_alerts.arn]

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-structuring-band-alarm"
    Severity = "HIGH"
    Category = "AML"
  })
}

# --- Admin Access Outside Business Hours ---
# Admin API calls between 10 PM and 6 AM should be investigated.
resource "aws_cloudwatch_log_metric_filter" "admin_after_hours" {
  name           = "${local.name_prefix}-admin-after-hours"
  log_group_name = aws_cloudwatch_log_group.application.name
  pattern        = "{ $.event_type = \"ADMIN_API_CALL\" && $.is_after_hours = true }"

  metric_transformation {
    name          = "AdminAfterHoursCount"
    namespace     = "iGaming/Security"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "admin_after_hours" {
  alarm_name          = "${local.name_prefix}-admin-after-hours"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AdminAfterHoursCount"
  namespace           = "iGaming/Security"
  period              = 300 # 5 minutes
  statistic           = "Sum"
  threshold           = 0 # Any after-hours admin access triggers alert
  alarm_description   = "Admin API access detected outside business hours"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.security_critical.arn]

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-admin-after-hours-alarm"
    Severity = "MEDIUM"
  })
}

# --- Account Creation Rate (Platform-Wide) ---
# Same caveat as withdrawal volume, and worth stating because "multi-accounting"
# is what everyone wants this alarm to be. Multi-accounting means several
# accounts sharing an IP, a device fingerprint or a payment instrument. Grouping
# by any of those requires a dimension on the metric, and a per-IP dimension is
# unbounded cardinality: CloudWatch bills per custom metric, so one metric per
# source IP turns a fraud control into a five-figure monthly bill and still
# cannot alarm on "any IP exceeds N" without one alarm per IP.
#
# So this alarm measures the platform-wide registration rate, which is a real and
# useful signal (a bonus-abuse farm shows up as a spike). The identity graph that
# actually links accounts is a fraud-pipeline job over the authentication log
# archive in S3, and the enrichment Lambda in lambda.tf is where per-finding
# context gets attached.
resource "aws_cloudwatch_log_metric_filter" "account_creation_rate" {
  name           = "${local.name_prefix}-account-creation-rate"
  log_group_name = aws_cloudwatch_log_group.authentication.name
  pattern        = "{ $.event_type = \"ACCOUNT_CREATED\" }"

  metric_transformation {
    name          = "AccountCreationCount"
    namespace     = "iGaming/Fraud"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "account_creation_rate" {
  alarm_name          = "${local.name_prefix}-account-creation-rate-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "AccountCreationCount"
  namespace           = "iGaming/Fraud"
  period              = 3600 # 1 hour
  statistic           = "Sum"
  threshold           = var.account_creation_threshold
  alarm_description   = "Platform-wide account creation rate above the hourly baseline - possible bonus-abuse farm. Rate signal only: no IP, device or payment-instrument grouping, so linking the accounts is a follow-up query against the authentication log archive."
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.fraud_alerts.arn]

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-account-creation-rate-alarm"
    Severity = "MEDIUM"
    Category = "fraud"
  })
}

# --- Unauthorized API Calls (from CloudTrail) ---
# AWS is not consistent about how it names authorization failures, and an
# exact-match list is how this filter ends up blind to the most common ones:
#
#   AccessDenied                    S3, STS, and most older APIs
#   AccessDeniedException           the majority of services, including KMS,
#                                   Secrets Manager, DynamoDB, Lambda
#   UnauthorizedOperation           EC2
#   Client.UnauthorizedOperation    EC2, older SDK paths
#   UnauthorizedAccess              a handful of services
#
# A compromised role enumerating EC2 -- describe instances, try to create a
# security group, try to launch -- produces nothing but UnauthorizedOperation.
# Matching only AccessDenied and UnauthorizedAccess lets exactly that
# reconnaissance pattern run silently.
#
# The two wildcard terms below are the CIS AWS Foundations Benchmark 3.1 filter,
# which is what the Security Hub CIS standard checks for. They cover all five
# codes: "AccessDenied*" catches AccessDenied and AccessDeniedException,
# "*UnauthorizedOperation" catches both the plain and Client.-prefixed forms.
resource "aws_cloudwatch_log_metric_filter" "unauthorized_api" {
  name           = "${local.name_prefix}-unauthorized-api"
  log_group_name = aws_cloudwatch_log_group.cloudtrail.name
  pattern        = "{ ($.errorCode = \"AccessDenied*\") || ($.errorCode = \"*UnauthorizedOperation\") || ($.errorCode = \"UnauthorizedAccess*\") }"

  metric_transformation {
    name          = "UnauthorizedAPICount"
    namespace     = "iGaming/Security"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "unauthorized_api" {
  alarm_name          = "${local.name_prefix}-unauthorized-api"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "UnauthorizedAPICount"
  namespace           = "iGaming/Security"
  period              = 300 # 5 minutes
  statistic           = "Sum"
  threshold           = 10 # More than 10 unauthorized calls in 5 min
  alarm_description   = "High rate of unauthorized API calls - potential credential compromise"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.security_critical.arn]

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-unauthorized-api-alarm"
    Severity = "CRITICAL"
  })
}

# --- Root Account Usage ---
# Root account should NEVER be used in production. Any usage is suspicious.
resource "aws_cloudwatch_log_metric_filter" "root_usage" {
  name           = "${local.name_prefix}-root-usage"
  log_group_name = aws_cloudwatch_log_group.cloudtrail.name
  pattern        = "{ $.userIdentity.type = \"Root\" && $.userIdentity.invokedBy NOT EXISTS && $.eventType != \"AwsServiceEvent\" }"

  metric_transformation {
    name          = "RootAccountUsage"
    namespace     = "iGaming/Security"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "root_usage" {
  alarm_name          = "${local.name_prefix}-root-account-usage"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "RootAccountUsage"
  namespace           = "iGaming/Security"
  period              = 60 # 1 minute -- immediate detection
  statistic           = "Sum"
  threshold           = 0 # Any root usage triggers alert
  alarm_description   = "ROOT ACCOUNT USED - Investigate immediately"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.security_critical.arn]

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-root-usage-alarm"
    Severity = "CRITICAL"
  })
}

# --- Console Login Without MFA ---
resource "aws_cloudwatch_log_metric_filter" "console_no_mfa" {
  name           = "${local.name_prefix}-console-no-mfa"
  log_group_name = aws_cloudwatch_log_group.cloudtrail.name
  pattern        = "{ $.eventName = \"ConsoleLogin\" && $.additionalEventData.MFAUsed != \"Yes\" }"

  metric_transformation {
    name          = "ConsoleLoginWithoutMFA"
    namespace     = "iGaming/Security"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "console_no_mfa" {
  alarm_name          = "${local.name_prefix}-console-no-mfa"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ConsoleLoginWithoutMFA"
  namespace           = "iGaming/Security"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Console login without MFA detected - PCI DSS 8.3 violation"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.security_critical.arn]

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-console-no-mfa-alarm"
    Severity = "HIGH"
  })
}
