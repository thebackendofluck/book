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

# Data sources
data "aws_ami" "amazon_linux_2" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*-x86_64-gp2"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# YubiHSM Connector Security Group
resource "aws_security_group" "yubihsm_connector" {
  count = var.enable_yubihsm ? 1 : 0

  name_prefix = "${var.project_name}-${var.environment}-yubihsm-connector-"
  vpc_id      = var.vpc_id

  # YubiHSM connector port
  ingress {
    from_port   = 12345
    to_port     = 12345
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  # SSH for management
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.management_cidr_blocks
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-connector-sg"
  })
}

# Lambda Security Group
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

# YubiHSM Connector EC2 Instance
resource "aws_instance" "yubihsm_connector" {
  count = var.enable_yubihsm ? 1 : 0

  ami           = var.yubihsm_ami_id != "" ? var.yubihsm_ami_id : data.aws_ami.amazon_linux_2.id
  instance_type = var.yubihsm_instance_type

  subnet_id                   = var.private_subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.yubihsm_connector[0].id]
  associate_public_ip_address = false

  user_data = templatefile("${path.module}/../../scripts/yubihsm_connector_init.sh", {
    yubihsm_connector_version = var.yubihsm_connector_version
    yubihsm_auth_key_id       = var.yubihsm_auth_key_id
    yubihsm_auth_password     = var.yubihsm_auth_password
    yubihsm_device_serial     = var.yubihsm_device_serial
  })

  root_block_device {
    encrypted   = true
    kms_key_id  = var.kms_key_arn
    volume_size = var.yubihsm_root_volume_size
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-connector"
  })
}

# S3 Bucket for YubiHSM Backups
resource "aws_s3_bucket" "yubihsm_backups" {
  count = var.enable_yubihsm ? 1 : 0

  bucket = "${var.project_name}-${var.environment}-yubihsm-backups-${random_string.bucket_suffix[0].result}"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-backups"
  })
}

resource "random_string" "bucket_suffix" {
  count = var.enable_yubihsm ? 1 : 0

  length  = 8
  lower   = true
  upper   = false
  numeric = true
  special = false
}

resource "aws_s3_bucket_versioning" "yubihsm_backups" {
  count = var.enable_yubihsm ? 1 : 0

  bucket = aws_s3_bucket.yubihsm_backups[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "yubihsm_backups" {
  count = var.enable_yubihsm ? 1 : 0

  bucket = aws_s3_bucket.yubihsm_backups[0].id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = var.kms_key_arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "yubihsm_backups" {
  count = var.enable_yubihsm ? 1 : 0

  bucket = aws_s3_bucket.yubihsm_backups[0].id

  rule {
    id     = "backup_retention"
    status = "Enabled"

    expiration {
      days = var.backup_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.backup_retention_days
    }
  }
}

# CloudWatch Log Group for YubiHSM
resource "aws_cloudwatch_log_group" "yubihsm" {
  count = var.enable_yubihsm ? 1 : 0

  name              = "/aws/yubihsm/${var.project_name}/${var.environment}"
  retention_in_days = var.log_retention_days

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-logs"
  })
}

# IAM Role for Lambda Functions
resource "aws_iam_role" "yubihsm_lambda" {
  count = var.enable_lifecycle_management || var.enable_certificate_management ? 1 : 0

  name = "${var.project_name}-${var.environment}-yubihsm-lambda-role"

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
    Name = "${var.project_name}-${var.environment}-yubihsm-lambda-role"
  })
}

# IAM Policy for Lambda Functions
resource "aws_iam_role_policy" "yubihsm_lambda" {
  count = var.enable_lifecycle_management || var.enable_certificate_management ? 1 : 0

  name = "${var.project_name}-${var.environment}-yubihsm-lambda-policy"
  role = aws_iam_role.yubihsm_lambda[0].id

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
          "ec2:DescribeInstances",
          "ec2:CreateTags",
          "ec2:DeleteTags"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = var.enable_yubihsm ? "${aws_s3_bucket.yubihsm_backups[0].arn}/*" : "*"
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

# Lambda Function for Lifecycle Management
resource "aws_lambda_function" "yubihsm_lifecycle" {
  count = var.enable_lifecycle_management ? 1 : 0

  function_name = "${var.project_name}-${var.environment}-yubihsm-lifecycle"
  runtime       = "python3.9"
  handler       = "lambda_function.lambda_handler"
  timeout       = 300

  filename         = data.archive_file.yubihsm_lifecycle[0].output_path
  source_code_hash = data.archive_file.yubihsm_lifecycle[0].output_base64sha256

  role = aws_iam_role.yubihsm_lambda[0].arn

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.yubihsm_lambda[0].id]
  }

  environment {
    variables = {
      YUBIHSM_CONNECTOR_HOST = var.enable_yubihsm ? aws_instance.yubihsm_connector[0].private_ip : ""
      YUBIHSM_AUTH_KEY_ID    = var.yubihsm_auth_key_id
      BACKUP_BUCKET          = var.enable_yubihsm ? aws_s3_bucket.yubihsm_backups[0].id : ""
      CLEANUP_EXPIRED        = var.cleanup_expired_certificates
      CLEANUP_OLD_DAYS       = var.cleanup_old_objects_days
    }
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-lifecycle"
  })
}

data "archive_file" "yubihsm_lifecycle" {
  count = var.enable_lifecycle_management ? 1 : 0

  type        = "zip"
  output_path = "${path.module}/lambda-lifecycle.zip"

  source {
    content  = file("${path.module}/../lifecycle.py")
    filename = "lambda_function.py"
  }
}

# CloudWatch Event Rule for Lifecycle Management
resource "aws_cloudwatch_event_rule" "yubihsm_lifecycle" {
  count = var.enable_lifecycle_management ? 1 : 0

  name                = "${var.project_name}-${var.environment}-yubihsm-lifecycle"
  description         = "Scheduled lifecycle management for YubiHSM"
  schedule_expression = "cron(${var.cleanup_schedule})"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-lifecycle-rule"
  })
}

resource "aws_cloudwatch_event_target" "yubihsm_lifecycle" {
  count = var.enable_lifecycle_management ? 1 : 0

  rule      = aws_cloudwatch_event_rule.yubihsm_lifecycle[0].name
  target_id = "yubihsm-lifecycle-lambda"
  arn       = aws_lambda_function.yubihsm_lifecycle[0].arn
}

resource "aws_lambda_permission" "yubihsm_lifecycle" {
  count = var.enable_lifecycle_management ? 1 : 0

  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.yubihsm_lifecycle[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.yubihsm_lifecycle[0].arn
}

# ACM Certificate for YubiHSM
resource "aws_acm_certificate" "yubihsm_cert" {
  count = var.enable_certificate_management && length(var.certificate_domains) > 0 ? 1 : 0

  domain_name               = var.certificate_domains[0]
  subject_alternative_names = length(var.certificate_domains) > 1 ? slice(var.certificate_domains, 1, length(var.certificate_domains)) : []
  validation_method         = "DNS"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-cert"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Lambda Function for Certificate Management
resource "aws_lambda_function" "yubihsm_cert_manager" {
  count = var.enable_certificate_management ? 1 : 0

  function_name = "${var.project_name}-${var.environment}-yubihsm-cert-manager"
  runtime       = "python3.9"
  handler       = "lambda_function.lambda_handler"
  timeout       = 300

  filename         = data.archive_file.yubihsm_cert_manager[0].output_path
  source_code_hash = data.archive_file.yubihsm_cert_manager[0].output_base64sha256

  role = aws_iam_role.yubihsm_lambda[0].arn

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.yubihsm_lambda[0].id]
  }

  environment {
    variables = {
      YUBIHSM_CONNECTOR_HOST = var.enable_yubihsm ? aws_instance.yubihsm_connector[0].private_ip : ""
      YUBIHSM_AUTH_KEY_ID    = var.yubihsm_auth_key_id
      LETS_ENCRYPT_ENABLED   = var.enable_lets_encrypt
      LETS_ENCRYPT_EMAIL     = var.lets_encrypt_email
      CERT_VALIDITY_DAYS     = var.certificate_validity_days
    }
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-cert-manager"
  })
}

data "archive_file" "yubihsm_cert_manager" {
  count = var.enable_certificate_management ? 1 : 0

  type        = "zip"
  output_path = "${path.module}/lambda-cert-manager.zip"

  source {
    content  = file("${path.module}/../certificates.py")
    filename = "lambda_function.py"
  }
}

# CloudWatch Event Rule for Certificate Rotation
resource "aws_cloudwatch_event_rule" "yubihsm_cert_rotation" {
  count = var.enable_certificate_management ? 1 : 0

  name                = "${var.project_name}-${var.environment}-yubihsm-cert-rotation"
  description         = "Scheduled certificate rotation for YubiHSM"
  schedule_expression = "cron(${var.rotation_schedule})"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-cert-rotation-rule"
  })
}

resource "aws_cloudwatch_event_target" "yubihsm_cert_rotation" {
  count = var.enable_certificate_management ? 1 : 0

  rule      = aws_cloudwatch_event_rule.yubihsm_cert_rotation[0].name
  target_id = "yubihsm-cert-rotation-lambda"
  arn       = aws_lambda_function.yubihsm_cert_manager[0].arn
}

resource "aws_lambda_permission" "yubihsm_cert_rotation" {
  count = var.enable_certificate_management ? 1 : 0

  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.yubihsm_cert_manager[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.yubihsm_cert_rotation[0].arn
}

# CloudWatch Alarms for Storage Monitoring
resource "aws_cloudwatch_metric_alarm" "yubihsm_storage_usage" {
  count = var.enable_yubihsm && var.alarm_sns_topic_arn != "" ? 1 : 0

  alarm_name          = "${var.project_name}-${var.environment}-yubihsm-storage-usage"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization" # Placeholder - would need custom metrics
  namespace           = "AWS/EC2"
  period              = "300"
  statistic           = "Average"
  threshold           = var.storage_alarm_threshold
  alarm_description   = "YubiHSM storage usage above ${var.storage_alarm_threshold}%"
  alarm_actions       = [var.alarm_sns_topic_arn]

  dimensions = {
    InstanceId = aws_instance.yubihsm_connector[0].id
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-storage-alarm"
  })
}