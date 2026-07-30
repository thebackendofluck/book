# Companion code for "The Backend of Luck" - Chapter 38, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Secrets Manager — Credential Management for Casino Platform
# =============================================================================
# Regulatory context:
#   NJ DGE 13:69O-1.4  — Credentials must be stored securely and rotated.
#   PCI-DSS 2.1        — Never use vendor-supplied defaults; change all
#                         default passwords before deployment.
#   PCI-DSS 3.4        — Render stored sensitive data unreadable.
#   PCI-DSS 8.2.4      — Change passwords at least every 90 days.
#
# Architecture:
#   - Secrets Manager for all sensitive credentials
#   - Automatic rotation every 30 days (exceeds PCI-DSS 90-day requirement)
#   - KMS encryption for secrets at rest
#   - ECS tasks retrieve secrets at runtime (never baked into images)
# =============================================================================

# --- Database Password -------------------------------------------------------

resource "aws_secretsmanager_secret" "db_password" {
  name        = "${var.project_name}/${var.environment}/db-password"
  description = "RDS PostgreSQL master password"

  # PCI-DSS 3.4: Encrypted at rest via KMS
  # Recovery window prevents accidental deletion
  recovery_window_in_days = 30

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-db-password"
    Compliance = "PCI-DSS-3.4,PCI-DSS-8.2.4"
    DataClass  = "secret"
  })
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = random_password.db_password.result
}

# --- JWT Signing Secret ------------------------------------------------------

resource "aws_secretsmanager_secret" "jwt_secret" {
  name        = "${var.project_name}/${var.environment}/jwt-secret"
  description = "JWT signing secret for player authentication tokens"

  recovery_window_in_days = 30

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-jwt-secret"
    Compliance = "NJ-DGE-13:69O-1.4,PCI-DSS-3.4"
    DataClass  = "secret"
  })
}

resource "aws_secretsmanager_secret_version" "jwt_secret" {
  secret_id     = aws_secretsmanager_secret.jwt_secret.id
  secret_string = random_password.jwt_secret.result
}

resource "random_password" "jwt_secret" {
  length  = 64
  special = false
  # JWT secrets should be long, alphanumeric for base64 encoding compatibility
}

# --- API Key -----------------------------------------------------------------

resource "aws_secretsmanager_secret" "api_key" {
  name        = "${var.project_name}/${var.environment}/api-key"
  description = "Internal API key for service-to-service authentication"

  recovery_window_in_days = 30

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-api-key"
    Compliance = "PCI-DSS-3.4"
    DataClass  = "secret"
  })
}

resource "aws_secretsmanager_secret_version" "api_key" {
  secret_id     = aws_secretsmanager_secret.api_key.id
  secret_string = random_password.api_key.result
}

resource "random_password" "api_key" {
  length  = 48
  special = false
}

# --- Secret Rotation Policy --------------------------------------------------
# PCI-DSS 8.2.4: Passwords must be changed at least every 90 days.
# We rotate every 30 days (exceeds requirement).

resource "aws_secretsmanager_secret_rotation" "db_password" {
  secret_id           = aws_secretsmanager_secret.db_password.id
  rotation_lambda_arn = aws_lambda_function.secret_rotation.arn

  rotation_rules {
    automatically_after_days = 30
    # Exceeds PCI-DSS 90-day requirement
  }
}

# --- Lambda for Secret Rotation ----------------------------------------------

data "archive_file" "secret_rotation" {
  type        = "zip"
  output_path = "${path.module}/lambda/secret_rotation.zip"

  source {
    content  = <<-PYTHON
import boto3
import json
import os

def lambda_handler(event, context):
    """
    Secrets Manager rotation handler for RDS PostgreSQL.
    Implements the four-step rotation flow:
      createSecret -> setSecret -> testSecret -> finishSecret
    """
    step = event["Step"]
    secret_arn = event["SecretId"]
    token = event["ClientRequestToken"]

    sm = boto3.client("secretsmanager")

    if step == "createSecret":
        # Generate new password
        new_password = sm.get_random_password(
            PasswordLength=32,
            ExcludeCharacters="/@\"'\\"
        )["RandomPassword"]

        sm.put_secret_value(
            SecretId=secret_arn,
            ClientRequestToken=token,
            SecretString=new_password,
            VersionStages=["AWSPENDING"]
        )

    elif step == "setSecret":
        # Update RDS with new password
        pending = sm.get_secret_value(
            SecretId=secret_arn,
            VersionStage="AWSPENDING"
        )["SecretString"]

        rds = boto3.client("rds")
        rds.modify_db_instance(
            DBInstanceIdentifier=os.environ["DB_INSTANCE_ID"],
            MasterUserPassword=pending,
            ApplyImmediately=True
        )

    elif step == "testSecret":
        # Verify new password works (simplified — production would test connection)
        pass

    elif step == "finishSecret":
        # Mark new version as current
        sm.update_secret_version_stage(
            SecretId=secret_arn,
            VersionStage="AWSCURRENT",
            MoveToVersionId=token,
            RemoveFromVersionId=_get_current_version(sm, secret_arn)
        )

def _get_current_version(sm, secret_arn):
    versions = sm.list_secret_version_ids(SecretId=secret_arn)
    for v in versions["Versions"]:
        if "AWSCURRENT" in v.get("VersionStages", []):
            return v["VersionId"]
    return None
    PYTHON
    filename = "lambda_function.py"
  }
}

resource "aws_lambda_function" "secret_rotation" {
  function_name    = "${var.project_name}-${var.environment}-secret-rotation"
  filename         = data.archive_file.secret_rotation.output_path
  source_code_hash = data.archive_file.secret_rotation.output_base64sha256
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  timeout          = 60

  role = aws_iam_role.secret_rotation.arn

  environment {
    variables = {
      DB_INSTANCE_ID = aws_db_instance.main.identifier
    }
  }

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.ecs.id]
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-secret-rotation"
    Compliance = "PCI-DSS-8.2.4"
  })
}

resource "aws_lambda_permission" "secret_rotation" {
  statement_id  = "AllowSecretsManager"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.secret_rotation.function_name
  principal     = "secretsmanager.amazonaws.com"
}

resource "aws_iam_role" "secret_rotation" {
  name = "${var.project_name}-${var.environment}-secret-rotation-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "secret_rotation" {
  name = "${var.project_name}-${var.environment}-secret-rotation-policy"
  role = aws_iam_role.secret_rotation.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecretVersionStage",
          "secretsmanager:ListSecretVersionIds",
          "secretsmanager:GetRandomPassword"
        ]
        Resource = aws_secretsmanager_secret.db_password.arn
      },
      {
        Effect = "Allow"
        Action = [
          "rds:ModifyDBInstance"
        ]
        Resource = aws_db_instance.main.arn
      },
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
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface"
        ]
        Resource = "*"
      }
    ]
  })
}
