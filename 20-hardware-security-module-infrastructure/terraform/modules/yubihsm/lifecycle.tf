# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# YubiHSM 2 Lifecycle Management
# This module manages lifecycle policies and automated operations for YubiHSM 2

# Lambda Function for Lifecycle Management
resource "aws_lambda_function" "yubihsm_lifecycle" {
  count = var.enable_lifecycle_management ? 1 : 0

  function_name = "${var.project_name}-${var.environment}-yubihsm-lifecycle"
  runtime       = "python3.9"
  handler       = "lambda_function.lambda_handler"
  timeout       = 300

  filename         = data.archive_file.yubihsm_lifecycle[0].output_path
  source_code_hash = data.archive_file.yubihsm_lifecycle[0].output_base64sha256

  role = aws_iam_role.yubihsm_lifecycle[0].arn

  environment {
    variables = {
      YUBIHSM_CONNECTOR_URL = "http://${aws_instance.yubihsm_connector[0].private_ip}:12345"
      YUBIHSM_AUTH_KEY_ID   = var.yubihsm_auth_key_id
      YUBIHSM_AUTH_PASSWORD = var.yubihsm_auth_password
      S3_BACKUP_BUCKET      = aws_s3_bucket.yubihsm_backups[0].id
      CLEANUP_OLD_DAYS      = var.cleanup_old_objects_days
      LOG_LEVEL             = "INFO"
    }
  }

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.yubihsm_lambda[0].id]
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-lifecycle"
    Type = "YubiHSM-Lifecycle"
  })
}

# Lambda deployment package
data "archive_file" "yubihsm_lifecycle" {
  count = var.enable_lifecycle_management ? 1 : 0

  type        = "zip"
  output_path = "${path.module}/lambda/yubihsm_lifecycle.zip"

  source {
    content  = file("${path.module}/lambda/lifecycle.py")
    filename = "lambda_function.py"
  }

  source {
    content  = file("${path.module}/lambda/requirements.txt")
    filename = "requirements.txt"
  }
}

# IAM Role for Lifecycle Lambda
resource "aws_iam_role" "yubihsm_lifecycle" {
  count = var.enable_lifecycle_management ? 1 : 0

  name = "${var.project_name}-${var.environment}-yubihsm-lifecycle-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-lifecycle-role"
  })
}

resource "aws_iam_role_policy" "yubihsm_lifecycle" {
  count = var.enable_lifecycle_management ? 1 : 0

  name = "${var.project_name}-${var.environment}-yubihsm-lifecycle-policy"
  role = aws_iam_role.yubihsm_lifecycle[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = "${aws_s3_bucket.yubihsm_backups[0].arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.yubihsm_backups[0].arn
      },
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = var.kms_key_arn
      }
    ]
  })
}

# Security Group for Lambda
resource "aws_security_group" "yubihsm_lambda" {
  count = var.enable_lifecycle_management ? 1 : 0

  name_prefix = "${var.project_name}-${var.environment}-yubihsm-lambda-"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-lambda-sg"
  })
}

# CloudWatch Events for Scheduled Lifecycle Operations
resource "aws_cloudwatch_event_rule" "yubihsm_cleanup" {
  count = var.enable_lifecycle_management ? 1 : 0

  name                = "${var.project_name}-${var.environment}-yubihsm-cleanup"
  description         = "Scheduled cleanup of expired certificates and old objects"
  schedule_expression = "cron(${var.cleanup_schedule})"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-cleanup-rule"
  })
}

resource "aws_cloudwatch_event_target" "yubihsm_cleanup" {
  count = var.enable_lifecycle_management ? 1 : 0

  rule      = aws_cloudwatch_event_rule.yubihsm_cleanup[0].name
  target_id = "YubiHSMCleanup"
  arn       = aws_lambda_function.yubihsm_lifecycle[0].arn

  input = jsonencode({
    operation = "cleanup"
    actions = [
      "cleanup_expired_certificates",
      "cleanup_old_objects"
    ]
  })
}

resource "aws_cloudwatch_event_rule" "yubihsm_backup" {
  count = var.enable_lifecycle_management ? 1 : 0

  name                = "${var.project_name}-${var.environment}-yubihsm-backup"
  description         = "Scheduled backup of YubiHSM objects"
  schedule_expression = "cron(${var.backup_schedule})"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-backup-rule"
  })
}

resource "aws_cloudwatch_event_target" "yubihsm_backup" {
  count = var.enable_lifecycle_management ? 1 : 0

  rule      = aws_cloudwatch_event_rule.yubihsm_backup[0].name
  target_id = "YubiHSMBackup"
  arn       = aws_lambda_function.yubihsm_lifecycle[0].arn

  input = jsonencode({
    operation = "backup"
    type      = "full"
  })
}

resource "aws_cloudwatch_event_rule" "yubihsm_rotation" {
  count = var.enable_lifecycle_management ? 1 : 0

  name                = "${var.project_name}-${var.environment}-yubihsm-rotation"
  description         = "Scheduled rotation of encryption keys"
  schedule_expression = "cron(${var.rotation_schedule})"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-rotation-rule"
  })
}

resource "aws_cloudwatch_event_target" "yubihsm_rotation" {
  count = var.enable_lifecycle_management ? 1 : 0

  rule      = aws_cloudwatch_event_rule.yubihsm_rotation[0].name
  target_id = "YubiHSMRotation"
  arn       = aws_lambda_function.yubihsm_lifecycle[0].arn

  input = jsonencode({
    operation = "rotate_keys"
    key_types = ["encryption", "signing"]
  })
}

# Lambda permissions for CloudWatch Events
resource "aws_lambda_permission" "yubihsm_cleanup" {
  count = var.enable_lifecycle_management ? 1 : 0

  statement_id  = "AllowExecutionFromCloudWatchCleanup"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.yubihsm_lifecycle[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.yubihsm_cleanup[0].arn
}

resource "aws_lambda_permission" "yubihsm_backup" {
  count = var.enable_lifecycle_management ? 1 : 0

  statement_id  = "AllowExecutionFromCloudWatchBackup"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.yubihsm_lifecycle[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.yubihsm_backup[0].arn
}

resource "aws_lambda_permission" "yubihsm_rotation" {
  count = var.enable_lifecycle_management ? 1 : 0

  statement_id  = "AllowExecutionFromCloudWatchRotation"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.yubihsm_lifecycle[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.yubihsm_rotation[0].arn
}

# CloudWatch Alarms for Lifecycle Operations
resource "aws_cloudwatch_metric_alarm" "yubihsm_lifecycle_failure" {
  count = var.enable_lifecycle_management ? 1 : 0

  alarm_name          = "${var.project_name}-${var.environment}-yubihsm-lifecycle-failure"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = "300"
  statistic           = "Sum"
  threshold           = "0"
  alarm_description   = "YubiHSM lifecycle Lambda function failed"
  alarm_actions       = var.alarm_sns_topic_arn != "" ? [var.alarm_sns_topic_arn] : []

  dimensions = {
    FunctionName = aws_lambda_function.yubihsm_lifecycle[0].function_name
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-lifecycle-alarm"
  })
}

# SNS Topic for Lifecycle Notifications (if not provided)
resource "aws_sns_topic" "yubihsm_lifecycle" {
  count = var.enable_lifecycle_management && var.alarm_sns_topic_arn == "" ? 1 : 0

  name = "${var.project_name}-${var.environment}-yubihsm-lifecycle"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-lifecycle-topic"
  })
}

# Outputs
output "lifecycle_lambda_function_name" {
  description = "Name of the lifecycle management Lambda function"
  value       = var.enable_lifecycle_management ? aws_lambda_function.yubihsm_lifecycle[0].function_name : null
}

output "lifecycle_lambda_function_arn" {
  description = "ARN of the lifecycle management Lambda function"
  value       = var.enable_lifecycle_management ? aws_lambda_function.yubihsm_lifecycle[0].arn : null
}

output "backup_bucket_name" {
  description = "Name of the S3 bucket for YubiHSM backups"
  value       = var.enable_yubihsm ? aws_s3_bucket.yubihsm_backups[0].id : null
}

output "lifecycle_sns_topic_arn" {
  description = "ARN of the SNS topic for lifecycle notifications"
  value       = var.enable_lifecycle_management && var.alarm_sns_topic_arn == "" ? aws_sns_topic.yubihsm_lifecycle[0].arn : var.alarm_sns_topic_arn
}