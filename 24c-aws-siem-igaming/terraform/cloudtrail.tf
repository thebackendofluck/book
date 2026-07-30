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
# AWS CloudTrail - API Audit Trail for iGaming
# =============================================================================
# CloudTrail records every API call in the AWS account. For iGaming, this is
# the audit trail that regulators will request during investigations.
#
# When the NJ DGE asks "who changed the RTP configuration on game X at
# 2:47 AM?", CloudTrail provides the answer: which IAM user/role, from
# which IP address, at what exact time, and what the request looked like.
#
# Regulatory justification:
#   NJ DGE 13:69O-1.2: "Audit trail of all transactions"
#   NJ DGE 13:69O-1.1(b): "7-year log retention"
#   PA PGCB: "Change management audit trail"
#   MI MGCB: "Every change recorded with who/what/when"
# =============================================================================

# --- KMS Key for CloudTrail Encryption ---
# All CloudTrail logs must be encrypted at rest. This key is dedicated
# to security logs -- separate from application encryption keys.
resource "aws_kms_key" "cloudtrail" {
  description             = "KMS key for CloudTrail log encryption - iGaming compliance"
  deletion_window_in_days = 30   # Maximum protection against accidental deletion
  enable_key_rotation     = true # Annual rotation - PCI DSS requirement

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccountFullAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:${local.partition}:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowCloudTrailEncrypt"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          StringLike = {
            "kms:EncryptionContext:aws:cloudtrail:arn" = "arn:${local.partition}:cloudtrail:*:${local.account_id}:trail/*"
          }
        }
      },
      {
        Sid    = "AllowCloudTrailDecrypt"
        Effect = "Allow"
        Principal = {
          AWS = "arn:${local.partition}:iam::${local.account_id}:root"
        }
        Action = [
          "kms:Decrypt",
          "kms:ReEncryptFrom"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = local.account_id
          }
          StringLike = {
            "kms:EncryptionContext:aws:cloudtrail:arn" = "arn:${local.partition}:cloudtrail:*:${local.account_id}:trail/*"
          }
        }
      },
      {
        Sid    = "AllowCloudWatchLogsEncrypt"
        Effect = "Allow"
        Principal = {
          Service = "logs.${local.region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*"
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:*"
          }
        }
      },
      {
        # All four SNS topics in sns.tf are encrypted with this CMK, and the
        # services below publish to them as bare service principals rather than
        # through an IAM role: CloudWatch for the metric alarms, EventBridge for
        # the GuardDuty and Security Hub rules, Config for the delivery channel
        # (which also uses this key to SSE-KMS its snapshots into the archive).
        # A service principal that can call sns:Publish but cannot get a data
        # key from the CMK gets KMS AccessDenied, and SNS drops the message.
        # The alarm still turns red in the console and nobody is paged, which is
        # the worst possible failure mode for a 3 a.m. alert.
        #
        # No SourceArn or ViaService condition here: these principals call KMS
        # directly on their own behalf when publishing to an encrypted topic and
        # do not populate those keys, so adding one denies the very calls this
        # statement exists to permit.
        Sid    = "AllowServicePublishToEncryptedTopics"
        Effect = "Allow"
        Principal = {
          Service = [
            "cloudwatch.amazonaws.com",
            "events.amazonaws.com",
            "config.amazonaws.com"
          ]
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey*"
        ]
        Resource = "*"
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-cloudtrail-kms"
    Purpose = "cloudtrail-log-encryption"
  })
}

resource "aws_kms_alias" "cloudtrail" {
  name          = "alias/${local.name_prefix}-security-logs"
  target_key_id = aws_kms_key.cloudtrail.key_id
}

# --- CloudWatch Log Group for CloudTrail ---
# CloudTrail sends logs here in addition to S3 for real-time analysis.
# Retention is 90 days in CloudWatch (hot), 7 years in S3 (archive).
resource "aws_cloudwatch_log_group" "cloudtrail" {
  name              = "/aws/cloudtrail/${local.name_prefix}"
  retention_in_days = 90 # Hot retention for real-time queries
  kms_key_id        = aws_kms_key.cloudtrail.arn

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-cloudtrail-logs"
    Purpose = "api-audit-trail"
  })
}

# --- IAM Role for CloudTrail -> CloudWatch Logs ---
resource "aws_iam_role" "cloudtrail_cloudwatch" {
  name = "${local.name_prefix}-cloudtrail-cw-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-cloudtrail-cw-role"
  })
}

resource "aws_iam_role_policy" "cloudtrail_cloudwatch" {
  name = "${local.name_prefix}-cloudtrail-cw-policy"
  role = aws_iam_role.cloudtrail_cloudwatch.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.cloudtrail.arn}:*"
      }
    ]
  })
}

# --- CloudTrail Trail ---
# Multi-region trail capturing all management and data events.
# This is the primary audit trail for regulatory compliance.
resource "aws_cloudtrail" "main" {
  name = "${local.name_prefix}-trail"

  # S3 bucket for 7-year retention (defined in s3.tf)
  s3_bucket_name = aws_s3_bucket.log_archive.id
  s3_key_prefix  = "cloudtrail"

  # CloudWatch Logs for real-time analysis
  cloud_watch_logs_group_arn = "${aws_cloudwatch_log_group.cloudtrail.arn}:*"
  cloud_watch_logs_role_arn  = aws_iam_role.cloudtrail_cloudwatch.arn

  # KMS encryption - NJ DGE 13:69O-1.3 requires encryption at rest
  kms_key_id = aws_kms_key.cloudtrail.arn

  # Multi-region: catch activity in ALL regions, not just us-east-1.
  # Attackers often spin up resources in unused regions to avoid detection.
  is_multi_region_trail = true

  # Organization trail: if using AWS Organizations for multi-state accounts
  is_organization_trail = var.is_organization_trail

  # Log file validation: cryptographic proof that logs haven't been tampered with.
  # Regulators may ask for proof of log integrity during audits.
  enable_log_file_validation = true

  # Include global service events (IAM, STS, CloudFront)
  include_global_service_events = true

  # Enable logging (can be toggled for maintenance)
  enable_logging = true

  # --- Data Events ---
  # Log S3 object-level operations on player data buckets
  event_selector {
    read_write_type           = "All"
    include_management_events = true

    # S3 data events: who accessed player data, KYC documents, financial records
    data_resource {
      type   = "AWS::S3::Object"
      values = ["arn:${local.partition}:s3"]
    }
  }

  # Log Lambda invocations: track execution of game logic and payment functions
  event_selector {
    read_write_type           = "All"
    include_management_events = false

    data_resource {
      type   = "AWS::Lambda::Function"
      values = ["arn:${local.partition}:lambda"]
    }
  }

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-cloudtrail"
    Purpose = "api-audit-trail-7yr-retention"
  })

  depends_on = [
    aws_s3_bucket_policy.log_archive,
    aws_iam_role_policy.cloudtrail_cloudwatch
  ]
}
