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

# YubiHSM 2 Certificate Management
# This module manages SSL/TLS certificates using YubiHSM 2 for private key storage

# ACM Certificate for Load Balancer (if domains provided)
resource "aws_acm_certificate" "yubihsm_cert" {
  count = var.enable_certificate_management && length(var.certificate_domains) > 0 ? 1 : 0

  domain_name               = var.certificate_domains[0]
  subject_alternative_names = length(var.certificate_domains) > 1 ? slice(var.certificate_domains, 1, length(var.certificate_domains)) : []
  validation_method         = "DNS"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-cert"
    Type = "YubiHSM-Certificate"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Route 53 Records for Certificate Validation
resource "aws_route53_record" "yubihsm_cert_validation" {
  for_each = var.enable_certificate_management && length(var.certificate_domains) > 0 ? {
    for dvo in aws_acm_certificate.yubihsm_cert[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = data.aws_route53_zone.selected[0].zone_id
}

# Certificate Validation
resource "aws_acm_certificate_validation" "yubihsm_cert" {
  count = var.enable_certificate_management && length(var.certificate_domains) > 0 ? 1 : 0

  certificate_arn         = aws_acm_certificate.yubihsm_cert[0].arn
  validation_record_fqdns = [for record in aws_route53_record.yubihsm_cert_validation : record.fqdn]
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

  role = aws_iam_role.yubihsm_cert_manager[0].arn

  environment {
    variables = {
      YUBIHSM_CONNECTOR_URL = "http://${aws_instance.yubihsm_connector[0].private_ip}:12345"
      YUBIHSM_AUTH_KEY_ID   = var.yubihsm_auth_key_id
      YUBIHSM_AUTH_PASSWORD = var.yubihsm_auth_password
      CERT_VALIDITY_DAYS    = var.certificate_validity_days
      LETS_ENCRYPT_EMAIL    = var.lets_encrypt_email
      ENABLE_LETS_ENCRYPT   = var.enable_lets_encrypt
      LOG_LEVEL             = "INFO"
    }
  }

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.yubihsm_lambda[0].id]
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-cert-manager"
    Type = "YubiHSM-Cert-Manager"
  })
}

# Lambda deployment package for certificate manager
data "archive_file" "yubihsm_cert_manager" {
  count = var.enable_certificate_management ? 1 : 0

  type        = "zip"
  output_path = "${path.module}/lambda/yubihsm_cert_manager.zip"

  source {
    content  = file("${path.module}/lambda/cert_manager.py")
    filename = "lambda_function.py"
  }

  source {
    content  = file("${path.module}/lambda/requirements.txt")
    filename = "requirements.txt"
  }
}

# IAM Role for Certificate Manager Lambda
resource "aws_iam_role" "yubihsm_cert_manager" {
  count = var.enable_certificate_management ? 1 : 0

  name = "${var.project_name}-${var.environment}-yubihsm-cert-manager-role"

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
    Name = "${var.project_name}-${var.environment}-yubihsm-cert-manager-role"
  })
}

resource "aws_iam_role_policy" "yubihsm_cert_manager" {
  count = var.enable_certificate_management ? 1 : 0

  name = "${var.project_name}-${var.environment}-yubihsm-cert-manager-policy"
  role = aws_iam_role.yubihsm_cert_manager[0].id

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
          "acm:DescribeCertificate",
          "acm:GetCertificate",
          "acm:ListCertificates",
          "acm:RequestCertificate",
          "acm:DeleteCertificate"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "route53:GetChange",
          "route53:ChangeResourceRecordSets",
          "route53:ListResourceRecordSets"
        ]
        Resource = "arn:aws:route53:::hostedzone/*"
      },
      {
        Effect = "Allow"
        Action = [
          "route53:ListHostedZones"
        ]
        Resource = "*"
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

# CloudWatch Events for Certificate Operations
resource "aws_cloudwatch_event_rule" "yubihsm_cert_expiry_check" {
  count = var.enable_certificate_management ? 1 : 0

  name                = "${var.project_name}-${var.environment}-yubihsm-cert-expiry-check"
  description         = "Daily check for expiring certificates"
  schedule_expression = "cron(0 3 * * ? *)" # Daily at 3 AM

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-cert-expiry-rule"
  })
}

resource "aws_cloudwatch_event_target" "yubihsm_cert_expiry_check" {
  count = var.enable_certificate_management ? 1 : 0

  rule      = aws_cloudwatch_event_rule.yubihsm_cert_expiry_check[0].name
  target_id = "YubiHSMCertExpiryCheck"
  arn       = aws_lambda_function.yubihsm_cert_manager[0].arn

  input = jsonencode({
    operation  = "check_expiry"
    days_ahead = 30
  })
}

resource "aws_cloudwatch_event_rule" "yubihsm_cert_renewal" {
  count = var.enable_certificate_management && var.enable_lets_encrypt ? 1 : 0

  name                = "${var.project_name}-${var.environment}-yubihsm-cert-renewal"
  description         = "Monthly certificate renewal check"
  schedule_expression = "cron(0 4 1 * ? *)" # First day of month at 4 AM

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-cert-renewal-rule"
  })
}

resource "aws_cloudwatch_event_target" "yubihsm_cert_renewal" {
  count = var.enable_certificate_management && var.enable_lets_encrypt ? 1 : 0

  rule      = aws_cloudwatch_event_rule.yubihsm_cert_renewal[0].name
  target_id = "YubiHSMCertRenewal"
  arn       = aws_lambda_function.yubihsm_cert_manager[0].arn

  input = jsonencode({
    operation = "renew_certificates"
    provider  = "letsencrypt"
  })
}

# Lambda permissions for CloudWatch Events
resource "aws_lambda_permission" "yubihsm_cert_expiry_check" {
  count = var.enable_certificate_management ? 1 : 0

  statement_id  = "AllowExecutionFromCloudWatchCertExpiry"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.yubihsm_cert_manager[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.yubihsm_cert_expiry_check[0].arn
}

resource "aws_lambda_permission" "yubihsm_cert_renewal" {
  count = var.enable_certificate_management && var.enable_lets_encrypt ? 1 : 0

  statement_id  = "AllowExecutionFromCloudWatchCertRenewal"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.yubihsm_cert_manager[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.yubihsm_cert_renewal[0].arn
}

# CloudWatch Alarms for Certificate Management
resource "aws_cloudwatch_metric_alarm" "yubihsm_cert_expiry_warning" {
  count = var.enable_certificate_management ? 1 : 0

  alarm_name          = "${var.project_name}-${var.environment}-yubihsm-cert-expiry-warning"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "CertificatesExpiringWithin30Days"
  namespace           = "AWS/YubiHSM"
  period              = "86400"
  statistic           = "Maximum"
  threshold           = "0"
  alarm_description   = "Certificates expiring within 30 days"
  alarm_actions       = var.alarm_sns_topic_arn != "" ? [var.alarm_sns_topic_arn] : []

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-cert-expiry-alarm"
  })
}

# Data sources
data "aws_route53_zone" "selected" {
  count = var.enable_certificate_management && length(var.certificate_domains) > 0 ? 1 : 0

  name = var.certificate_domains[0]
}

# Outputs
output "certificate_arn" {
  description = "ARN of the ACM certificate"
  value       = var.enable_certificate_management && length(var.certificate_domains) > 0 ? aws_acm_certificate.yubihsm_cert[0].arn : null
}

output "certificate_domain_validation_options" {
  description = "Domain validation options for the certificate"
  value       = var.enable_certificate_management && length(var.certificate_domains) > 0 ? aws_acm_certificate.yubihsm_cert[0].domain_validation_options : []
}

output "cert_manager_lambda_function_name" {
  description = "Name of the certificate manager Lambda function"
  value       = var.enable_certificate_management ? aws_lambda_function.yubihsm_cert_manager[0].function_name : null
}

output "cert_manager_lambda_function_arn" {
  description = "ARN of the certificate manager Lambda function"
  value       = var.enable_certificate_management ? aws_lambda_function.yubihsm_cert_manager[0].arn : null
}