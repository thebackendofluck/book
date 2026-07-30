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
# Application Log Delivery: CloudWatch Logs -> Firehose -> S3 Archive
# =============================================================================
# The five application log groups in cloudwatch.tf sit at 90 days of CloudWatch
# retention (var.cloudwatch_retention_days). That is hot-query retention, not
# the retention a regulator asks about. Every AML and fraud metric filter in
# cloudwatch.tf reads from one of these groups: withdrawals, bets, account
# creation, admin access, authentication. Without a delivery path to S3, the
# evidence behind each of those alarms expires after 90 days and the "7-year
# archive" only ever covered CloudTrail, Config, and the findings the alert
# processor Lambda writes.
#
# That gap is the one that ends badly. An examiner asking for the withdrawal
# history of a player from 18 months ago, or a fraud analyst reopening a
# structuring case from two years back, needs the raw events, not the alarm
# that fired at the time.
#
# The path implemented here:
#   log group -> subscription filter -> Firehose -> s3://.../app-logs/<tier>/
#
# Firehose decompresses the CloudWatch Logs payload natively and extracts the
# individual log events, so what lands in S3 is newline-delimited JSON that
# Athena can query directly. Objects are encrypted with the same CMK as the
# rest of the archive and are covered by the "app-logs-lifecycle" rule in
# s3.tf, which gives them the same tiered 7-year schedule as CloudTrail.
#
# Regulatory justification:
#   NJ DGE 13:69O-1.1(b): 7-year log retention -- applies to gaming and
#                         financial transaction records, not only API calls
#   AML/BSA: suspicious activity records must survive the investigation window
#   PCI DSS 10.7: audit trail history available for analysis
# =============================================================================

locals {
  # Log groups that must survive past CloudWatch retention. The map key becomes
  # the S3 prefix and the Firehose stream suffix.
  archived_log_groups = {
    application     = aws_cloudwatch_log_group.application.name
    authentication  = aws_cloudwatch_log_group.authentication.name
    payment         = aws_cloudwatch_log_group.payment.name
    game-events     = aws_cloudwatch_log_group.game_events.name
    security-events = aws_cloudwatch_log_group.security_events.name
  }
}

# --- Firehose Error Logging ---
# A delivery stream that cannot write to S3 fails silently unless its own
# errors are logged somewhere. Without this, the archive stops filling and
# nobody notices until an examiner asks for the data.
resource "aws_cloudwatch_log_group" "firehose_archive" {
  name              = "/aws/kinesisfirehose/${local.name_prefix}-log-archive"
  retention_in_days = var.cloudwatch_retention_days
  kms_key_id        = aws_kms_key.cloudtrail.arn

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-firehose-archive-logs"
    Purpose = "log-delivery-error-visibility"
  })
}

resource "aws_cloudwatch_log_stream" "firehose_archive" {
  for_each = local.archived_log_groups

  name           = each.key
  log_group_name = aws_cloudwatch_log_group.firehose_archive.name
}

# --- IAM Role for Firehose -> S3 ---
resource "aws_iam_role" "firehose_archive" {
  name = "${local.name_prefix}-firehose-archive-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "firehose.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = local.account_id
          }
        }
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-firehose-archive-role"
  })
}

resource "aws_iam_role_policy" "firehose_archive" {
  name = "${local.name_prefix}-firehose-archive-policy"
  role = aws_iam_role.firehose_archive.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "WriteToLogArchive"
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:ListBucketMultipartUploads",
          "s3:PutObject"
        ]
        Resource = [
          aws_s3_bucket.log_archive.arn,
          "${aws_s3_bucket.log_archive.arn}/app-logs/*"
        ]
      },
      {
        # The bucket enforces SSE-KMS and denies any put without it, so the
        # delivery stream needs the data-key grant or every record is rejected.
        Sid    = "EncryptWithArchiveKey"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = aws_kms_key.cloudtrail.arn
        Condition = {
          StringEquals = {
            "kms:ViaService" = "s3.${local.region}.amazonaws.com"
          }
        }
      },
      {
        Sid      = "DeliveryErrorLogging"
        Effect   = "Allow"
        Action   = ["logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.firehose_archive.arn}:*"
      }
    ]
  })
}

# --- IAM Role for CloudWatch Logs -> Firehose ---
# The subscription filter assumes this role to push records into Firehose.
resource "aws_iam_role" "cwl_to_firehose" {
  name = "${local.name_prefix}-cwl-to-firehose-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "logs.${local.region}.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = local.account_id
          }
          ArnLike = {
            "aws:SourceArn" = "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:*"
          }
        }
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-cwl-to-firehose-role"
  })
}

resource "aws_iam_role_policy" "cwl_to_firehose" {
  name = "${local.name_prefix}-cwl-to-firehose-policy"
  role = aws_iam_role.cwl_to_firehose.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "firehose:PutRecord",
          "firehose:PutRecordBatch"
        ]
        Resource = [for s in aws_kinesis_firehose_delivery_stream.log_archive : s.arn]
      }
    ]
  })
}

# --- Delivery Streams ---
# One stream per log group so a delivery failure or throughput problem on the
# game-event firehose (by far the highest volume) cannot starve the payment
# stream, and so each tier lands under its own S3 prefix.
resource "aws_kinesis_firehose_delivery_stream" "log_archive" {
  for_each = local.archived_log_groups

  name        = "${local.name_prefix}-${each.key}-archive"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn    = aws_iam_role.firehose_archive.arn
    bucket_arn  = aws_s3_bucket.log_archive.arn
    kms_key_arn = aws_kms_key.cloudtrail.arn

    # Hive-style partitioning so Athena can prune by date. An examiner request
    # scoped to one month should not scan seven years.
    prefix              = "app-logs/${each.key}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"
    error_output_prefix = "app-logs/${each.key}/delivery-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"

    # 5 MiB / 5 minutes. Larger objects cost less to store and scan; five
    # minutes of delay is irrelevant for an archive whose horizon is 7 years.
    buffering_size     = 5
    buffering_interval = 300
    compression_format = "GZIP"

    # CloudWatch Logs delivers a gzipped envelope containing many log events.
    # Written as-is it is neither greppable nor Athena-queryable, which is how
    # archives end up technically complete and practically useless. Decompress
    # the envelope, extract the events, and delimit them with newlines.
    processing_configuration {
      enabled = true

      processors {
        type = "Decompression"

        parameters {
          parameter_name  = "CompressionFormat"
          parameter_value = "GZIP"
        }
      }

      processors {
        type = "CloudWatchLogProcessing"

        parameters {
          parameter_name  = "DataMessageExtraction"
          parameter_value = "true"
        }
      }

      # Delimiter defaults to a newline, which is what makes the object
      # newline-delimited JSON rather than one unreadable concatenated blob.
      processors {
        type = "AppendDelimiterToRecord"
      }
    }

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose_archive.name
      log_stream_name = aws_cloudwatch_log_stream.firehose_archive[each.key].name
    }
  }

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-${each.key}-archive"
    Purpose = "application-log-7yr-delivery"
  })
}

# --- Subscription Filters ---
# An empty filter pattern ships every event. Filtering here would mean deciding
# today which events a regulator will ask about in three years.
resource "aws_cloudwatch_log_subscription_filter" "log_archive" {
  for_each = local.archived_log_groups

  name            = "${local.name_prefix}-${each.key}-to-archive"
  log_group_name  = each.value
  filter_pattern  = ""
  destination_arn = aws_kinesis_firehose_delivery_stream.log_archive[each.key].arn
  role_arn        = aws_iam_role.cwl_to_firehose.arn

  depends_on = [aws_iam_role_policy.cwl_to_firehose]
}

# --- Delivery Failure Alarm ---
# A stream that stops delivering is a retention breach in slow motion. Alarm on
# it rather than discovering the hole during an examination.
resource "aws_cloudwatch_metric_alarm" "log_archive_delivery_failure" {
  for_each = local.archived_log_groups

  alarm_name          = "${local.name_prefix}-${each.key}-archive-delivery-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DeliveryToS3.DataFreshness"
  namespace           = "AWS/Firehose"
  period              = 300
  statistic           = "Maximum"
  threshold           = 900 # oldest undelivered record older than 15 minutes
  alarm_description   = "Application log delivery to the 7-year S3 archive is stalled - retention gap accumulating"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DeliveryStreamName = aws_kinesis_firehose_delivery_stream.log_archive[each.key].name
  }

  alarm_actions = [aws_sns_topic.compliance_alerts.arn]
  ok_actions    = [aws_sns_topic.compliance_alerts.arn]

  tags = merge(local.common_tags, {
    Name     = "${local.name_prefix}-${each.key}-delivery-alarm"
    Severity = "HIGH"
    Category = "retention"
  })
}
