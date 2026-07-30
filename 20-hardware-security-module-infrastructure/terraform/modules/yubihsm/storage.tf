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

# YubiHSM 2 Storage Configuration
# This module manages YubiHSM 2 storage resources and configurations

# EC2 Instance for YubiHSM Connector
resource "aws_instance" "yubihsm_connector" {
  count = var.enable_yubihsm ? 1 : 0

  ami           = var.yubihsm_ami_id
  instance_type = var.yubihsm_instance_type

  subnet_id                   = var.private_subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.yubihsm_connector[0].id]
  associate_public_ip_address = false

  iam_instance_profile = aws_iam_instance_profile.yubihsm_connector[0].name

  root_block_device {
    volume_size = var.yubihsm_root_volume_size
    volume_type = "gp3"
    encrypted   = true
    kms_key_id  = var.kms_key_arn
  }

  user_data = templatefile("${path.module}/templates/yubihsm_connector_init.sh.tpl", {
    yubihsm_connector_version = var.yubihsm_connector_version
    yubihsm_auth_key_id       = var.yubihsm_auth_key_id
    yubihsm_auth_password     = var.yubihsm_auth_password
    yubihsm_device_serial     = var.yubihsm_device_serial
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-connector"
    Type = "YubiHSM-Connector"
  })

  lifecycle {
    ignore_changes = [
      user_data,
    ]
  }
}

# Security Group for YubiHSM Connector
resource "aws_security_group" "yubihsm_connector" {
  count = var.enable_yubihsm ? 1 : 0

  name_prefix = "${var.project_name}-${var.environment}-yubihsm-connector-"
  vpc_id      = var.vpc_id

  # YubiHSM Connector port (default 12345)
  ingress {
    from_port   = 12345
    to_port     = 12345
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  # SSH access for management
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

# IAM Role and Instance Profile for YubiHSM Connector
resource "aws_iam_role" "yubihsm_connector" {
  count = var.enable_yubihsm ? 1 : 0

  name = "${var.project_name}-${var.environment}-yubihsm-connector-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-connector-role"
  })
}

resource "aws_iam_role_policy_attachment" "yubihsm_connector_ssm" {
  count = var.enable_yubihsm ? 1 : 0

  role       = aws_iam_role.yubihsm_connector[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "yubihsm_connector_kms" {
  count = var.enable_yubihsm ? 1 : 0

  role       = aws_iam_role.yubihsm_connector[0].name
  policy_arn = "arn:aws:iam::aws:policy/AWSKeyManagementServicePowerUser"
}

resource "aws_iam_instance_profile" "yubihsm_connector" {
  count = var.enable_yubihsm ? 1 : 0

  name = "${var.project_name}-${var.environment}-yubihsm-connector-profile"
  role = aws_iam_role.yubihsm_connector[0].name
}

# S3 Bucket for YubiHSM Backups and Configuration
resource "aws_s3_bucket" "yubihsm_backups" {
  count = var.enable_yubihsm ? 1 : 0

  bucket = "${var.project_name}-${var.environment}-yubihsm-backups-${random_string.bucket_suffix[0].result}"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-backups"
    Type = "YubiHSM-Backups"
  })
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

resource "aws_s3_bucket_public_access_block" "yubihsm_backups" {
  count = var.enable_yubihsm ? 1 : 0

  bucket = aws_s3_bucket.yubihsm_backups[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "yubihsm_backups" {
  count = var.enable_yubihsm ? 1 : 0

  bucket = aws_s3_bucket.yubihsm_backups[0].id

  rule {
    id     = "backup_retention"
    status = "Enabled"

    filter {
      prefix = "backups/"
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    expiration {
      days = var.backup_retention_days
    }
  }
}

# Random suffix for S3 bucket
resource "random_string" "bucket_suffix" {
  count = var.enable_yubihsm ? 1 : 0

  length  = 8
  lower   = true
  upper   = false
  numeric = true
  special = false
}

# CloudWatch Log Group for YubiHSM Monitoring
resource "aws_cloudwatch_log_group" "yubihsm" {
  count = var.enable_yubihsm ? 1 : 0

  name              = "/aws/yubihsm/${var.project_name}/${var.environment}"
  retention_in_days = var.log_retention_days

  kms_key_id = var.kms_key_arn

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-logs"
  })
}

# CloudWatch Alarm for YubiHSM Storage Usage
resource "aws_cloudwatch_metric_alarm" "yubihsm_storage_high" {
  count = var.enable_yubihsm ? 1 : 0

  alarm_name          = "${var.project_name}-${var.environment}-yubihsm-storage-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "YubiHSMStorageUsage"
  namespace           = "AWS/YubiHSM"
  period              = "300"
  statistic           = "Maximum"
  threshold           = var.storage_alarm_threshold
  alarm_description   = "YubiHSM storage usage is above ${var.storage_alarm_threshold}%"
  alarm_actions       = var.alarm_sns_topic_arn != "" ? [var.alarm_sns_topic_arn] : []

  dimensions = {
    InstanceId = aws_instance.yubihsm_connector[0].id
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-storage-alarm"
  })
}

# Data sources
data "aws_region" "current" {}