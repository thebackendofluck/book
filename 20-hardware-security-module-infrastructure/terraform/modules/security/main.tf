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

# KMS Key for encryption
resource "aws_kms_key" "main" {
  description             = "KMS key for ${var.project_name}-${var.environment} encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = var.enable_key_rotation

  policy = data.aws_iam_policy_document.kms_policy.json

  tags = merge(
    var.tags,
    {
      Name = "${var.project_name}-${var.environment}-kms-key"
    }
  )
}

resource "aws_kms_alias" "main" {
  name          = "alias/${var.project_name}-${var.environment}-key"
  target_key_id = aws_kms_key.main.key_id
}

# KMS Key Policy
data "aws_iam_policy_document" "kms_policy" {
  statement {
    sid    = "Enable IAM User Permissions"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    actions   = ["kms:*"]
    resources = ["*"]
  }

  statement {
    sid    = "Allow Nitro Enclave"
    effect = "Allow"
    principals {
      type = "AWS"
      identifiers = [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project_name}-${var.environment}-nitro-enclave-role"
      ]
    }
    actions = [
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:GenerateDataKey"
    ]
    resources = ["*"]
  }

  statement {
    sid    = "Allow EC2 Instance"
    effect = "Allow"
    principals {
      type = "AWS"
      identifiers = [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project_name}-${var.environment}-ec2-role"
      ]
    }
    actions = [
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:GenerateDataKey",
      "kms:CreateGrant"
    ]
    resources = ["*"]
  }

  dynamic "statement" {
    for_each = var.additional_kms_grants
    content {
      sid    = statement.value.sid
      effect = statement.value.effect
      principals {
        type        = statement.value.principal_type
        identifiers = statement.value.principal_identifiers
      }
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }
}

# Security Groups
resource "aws_security_group" "yubihsm" {
  name_prefix = "${var.project_name}-${var.environment}-yubihsm-"
  vpc_id      = var.vpc_id

  # YubiHSM Connector port
  ingress {
    from_port   = 12345
    to_port     = 12345
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.main.cidr_block]
    description = "YubiHSM Connector"
  }

  # SSH for management (restrict in production)
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"] # VPC CIDR - adjust as needed
    description = "SSH"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound"
  }

  tags = merge(
    var.tags,
    {
      Name = "${var.project_name}-${var.environment}-yubihsm-sg"
    }
  )
}

resource "aws_security_group" "app" {
  name_prefix = "${var.project_name}-${var.environment}-app-"
  vpc_id      = var.vpc_id

  # HTTP
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP"
  }

  # HTTPS
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS"
  }

  # SSH for management
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"] # VPC CIDR - adjust as needed
    description = "SSH"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound"
  }

  tags = merge(
    var.tags,
    {
      Name = "${var.project_name}-${var.environment}-app-sg"
    }
  )
}

resource "aws_security_group" "database" {
  name_prefix = "${var.project_name}-${var.environment}-database-"
  vpc_id      = var.vpc_id

  # PostgreSQL
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
    description     = "PostgreSQL from app servers"
  }

  # PostgreSQL from containers
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.container.id]
    description     = "PostgreSQL from containers"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound"
  }

  tags = merge(
    var.tags,
    {
      Name = "${var.project_name}-${var.environment}-database-sg"
    }
  )
}

resource "aws_security_group" "container" {
  name_prefix = "${var.project_name}-${var.environment}-container-"
  vpc_id      = var.vpc_id

  # Allow all outbound
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound"
  }

  tags = merge(
    var.tags,
    {
      Name = "${var.project_name}-${var.environment}-container-sg"
    }
  )
}

# IAM Roles and Policies
resource "aws_iam_role" "ec2" {
  name = "${var.project_name}-${var.environment}-ec2-role"

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

  tags = merge(
    var.tags,
    {
      Name = "${var.project_name}-${var.environment}-ec2-role"
    }
  )
}

resource "aws_iam_role_policy_attachment" "ec2_ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ec2_cloudwatch" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_iam_role_policy" "ec2_kms" {
  name = "${var.project_name}-${var.environment}-ec2-kms-policy"
  role = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey",
          "kms:GenerateDataKey",
          "kms:CreateGrant"
        ]
        Resource = aws_kms_key.main.arn
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.project_name}-${var.environment}-ec2-profile"
  role = aws_iam_role.ec2.name
}

# Nitro Enclave Role
resource "aws_iam_role" "nitro_enclave" {
  name = "${var.project_name}-${var.environment}-nitro-enclave-role"

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

  tags = merge(
    var.tags,
    {
      Name = "${var.project_name}-${var.environment}-nitro-enclave-role"
    }
  )
}

resource "aws_iam_role_policy" "nitro_enclave_kms" {
  name = "${var.project_name}-${var.environment}-nitro-enclave-kms-policy"
  role = aws_iam_role.nitro_enclave.id

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
        Resource = aws_kms_key.main.arn
      }
    ]
  })
}

# ECS Task Role
resource "aws_iam_role" "ecs_task" {
  name = "${var.project_name}-${var.environment}-ecs-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = merge(
    var.tags,
    {
      Name = "${var.project_name}-${var.environment}-ecs-task-role"
    }
  )
}

resource "aws_iam_role_policy" "ecs_task_kms" {
  name = "${var.project_name}-${var.environment}-ecs-task-kms-policy"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = aws_kms_key.main.arn
      }
    ]
  })
}

# Data sources
data "aws_caller_identity" "current" {}
data "aws_vpc" "main" {
  id = var.vpc_id
}