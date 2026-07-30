# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# API Gateway Terraform Module
# Deploys secure API Gateway for remote YubiHSM operations

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  name_prefix = var.name_prefix != "" ? var.name_prefix : "yubihsm-api-gateway"
  common_tags = {
    Project     = "YubiHSM"
    Component   = "API Gateway"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# Security Group for API Gateway
resource "aws_security_group" "api_gateway" {
  name_prefix = "${local.name_prefix}-sg"
  description = "Security group for YubiHSM API Gateway"
  vpc_id      = var.vpc_id

  # Allow HTTPS inbound from allowed CIDRs
  dynamic "ingress" {
    for_each = var.allowed_cidr_blocks
    content {
      from_port   = 8443
      to_port     = 8443
      protocol    = "tcp"
      cidr_blocks = [ingress.value]
      description = "HTTPS API access from ${ingress.value}"
    }
  }

  # Allow SSH from bastion or allowed IPs
  dynamic "ingress" {
    for_each = var.allowed_ssh_cidr_blocks
    content {
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = [ingress.value]
      description = "SSH access from ${ingress.value}"
    }
  }

  # Allow all outbound
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-sg"
  })
}

# IAM Role for API Gateway EC2 instance
resource "aws_iam_role" "api_gateway" {
  name_prefix = "${local.name_prefix}-role"

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

  tags = local.common_tags
}

# IAM Policy for API Gateway
resource "aws_iam_role_policy" "api_gateway" {
  name_prefix = "${local.name_prefix}-policy"
  role        = aws_iam_role.api_gateway.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey",
          "kms:GenerateDataKey"
        ]
        Resource = var.kms_key_arn != "" ? var.kms_key_arn : "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath"
        ]
        Resource = "arn:aws:ssm:*:*:parameter/yubihsm/*"
      }
    ]
  })
}

# IAM Instance Profile
resource "aws_iam_instance_profile" "api_gateway" {
  name_prefix = "${local.name_prefix}-profile"
  role        = aws_iam_role.api_gateway.name

  tags = local.common_tags
}

# EC2 Instance for API Gateway
resource "aws_instance" "api_gateway" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  key_name               = var.key_pair_name
  vpc_security_group_ids = [aws_security_group.api_gateway.id]
  subnet_id              = var.subnet_id
  iam_instance_profile   = aws_iam_instance_profile.api_gateway.name

  root_block_device {
    volume_size = var.root_volume_size
    volume_type = var.root_volume_type
    encrypted   = true
    kms_key_id  = var.kms_key_arn
  }

  user_data = templatefile("${path.module}/templates/user_data.sh.tpl", {
    api_port          = var.api_port
    hsm_connector_url = var.hsm_connector_url
    hsm_auth_key_id   = var.hsm_auth_key_id
    hsm_password_ssm  = var.hsm_password_ssm_param
    cert_dir          = var.cert_directory
    allowed_ips       = join(" ", var.allowed_cidr_blocks)
    rate_limit        = var.rate_limit_per_minute
    environment       = var.environment
  })

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-instance"
  })

  lifecycle {
    ignore_changes = [
      ami,
      user_data
    ]
  }
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "api_gateway" {
  name_prefix       = "/aws/ec2/${local.name_prefix}"
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}

# SSM Parameters for sensitive configuration
resource "aws_ssm_parameter" "hsm_password" {
  count = var.hsm_password_ssm_param == "" ? 1 : 0

  name        = "/yubihsm/api-gateway/hsm-password"
  description = "HSM authentication password for API Gateway"
  type        = "SecureString"
  value       = var.hsm_password
  key_id      = var.kms_key_arn

  tags = local.common_tags
}

# Route 53 Record (optional)
resource "aws_route53_record" "api_gateway" {
  count = var.create_dns_record ? 1 : 0

  zone_id = var.route53_zone_id
  name    = var.dns_record_name
  type    = "A"
  ttl     = "300"
  records = [aws_instance.api_gateway.private_ip]
}

# CloudWatch Alarms
resource "aws_cloudwatch_metric_alarm" "api_gateway_cpu" {
  alarm_name          = "${local.name_prefix}-high-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = "300"
  statistic           = "Average"
  threshold           = var.cpu_alarm_threshold
  alarm_description   = "This metric monitors EC2 CPU utilization"
  alarm_actions       = var.alarm_sns_topic_arn != "" ? [var.alarm_sns_topic_arn] : []

  dimensions = {
    InstanceId = aws_instance.api_gateway.id
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "api_gateway_status" {
  alarm_name          = "${local.name_prefix}-status-check"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = "300"
  statistic           = "Maximum"
  threshold           = "0"
  alarm_description   = "This metric monitors EC2 status checks"
  alarm_actions       = var.alarm_sns_topic_arn != "" ? [var.alarm_sns_topic_arn] : []

  dimensions = {
    InstanceId = aws_instance.api_gateway.id
  }

  tags = local.common_tags
}