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
# IAM — Roles and Policies for Casino Platform
# =============================================================================
# Regulatory context:
#   NJ DGE 13:69O-1.8  — Principle of least privilege must be enforced for
#                         all system access.
#   PCI-DSS 7.1        — Limit access to system components to only those
#                         individuals whose job requires such access.
#   PCI-DSS 7.2        — Establish an access control system that restricts
#                         access based on need-to-know.
#   PCI-DSS 10.2       — Audit trails for all access to system resources.
#
# Roles:
#   - ECS Execution Role: Pull images from ECR, retrieve secrets
#   - ECS Task Role: Application-level AWS API access
#   - CodeBuild Role: Build and push Docker images
#   - CodePipeline Role: Orchestrate CI/CD pipeline
# =============================================================================

# --- ECS Execution Role ------------------------------------------------------
# Used by ECS agent to pull images, retrieve secrets, write logs.
# PCI-DSS 7.1: Minimal permissions for container runtime operations.

resource "aws_iam_role" "ecs_execution" {
  name = "${var.project_name}-${var.environment}-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-ecs-execution"
    Compliance = "PCI-DSS-7.1"
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution_base" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "${var.project_name}-${var.environment}-ecs-secrets"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadSecrets"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.db_password.arn,
          aws_secretsmanager_secret.jwt_secret.arn,
          aws_secretsmanager_secret.api_key.arn
        ]
        # PCI-DSS 7.2: Restrict secret access to specific ARNs
      },
      {
        Sid    = "DecryptSecrets"
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = ["*"]
        Condition = {
          StringEquals = {
            "kms:ViaService" = "secretsmanager.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

# --- ECS Task Role -----------------------------------------------------------
# Used by the running application for AWS API calls.
# PCI-DSS 7.1: Only permissions the casino API actually needs.

resource "aws_iam_role" "ecs_task" {
  name = "${var.project_name}-${var.environment}-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-ecs-task"
    Compliance = "PCI-DSS-7.1,NJ-DGE-13:69O-1.8"
  })
}

resource "aws_iam_role_policy" "ecs_task" {
  name = "${var.project_name}-${var.environment}-ecs-task-policy"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "${var.project_name}/${var.environment}"
          }
        }
      },
      {
        Sid    = "SESForTransactionalEmail"
        Effect = "Allow"
        Action = [
          "ses:SendEmail",
          "ses:SendRawEmail"
        ]
        Resource = "*"
        # For player verification emails, password resets, responsible gaming alerts
      },
      {
        Sid    = "SSMParameterStore"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/${var.project_name}/${var.environment}/*"
        # Non-secret configuration (feature flags, game settings)
      },
      {
        Sid    = "XRayTracing"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords"
        ]
        Resource = "*"
        # Distributed tracing for game round transactions
      }
    ]
  })
}

# --- CodeBuild Role ----------------------------------------------------------
# PCI-DSS 6.3: Build process must be controlled and auditable.

resource "aws_iam_role" "codebuild" {
  name = "${var.project_name}-${var.environment}-codebuild"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "codebuild.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-codebuild"
    Compliance = "PCI-DSS-6.3"
  })
}

resource "aws_iam_role_policy" "codebuild" {
  name = "${var.project_name}-${var.environment}-codebuild-policy"
  role = aws_iam_role.codebuild.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRAccess"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:*:log-group:/aws/codebuild/${var.project_name}-*"
      },
      {
        Sid    = "S3Artifacts"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:GetBucketAcl",
          "s3:GetBucketLocation"
        ]
        Resource = [
          aws_s3_bucket.pipeline_artifacts.arn,
          "${aws_s3_bucket.pipeline_artifacts.arn}/*"
        ]
      },
      {
        Sid    = "ECSUpdate"
        Effect = "Allow"
        Action = [
          "ecs:UpdateService",
          "ecs:DescribeServices",
          "ecs:RegisterTaskDefinition",
          "ecs:DescribeTaskDefinition"
        ]
        Resource = "*"
      }
    ]
  })
}

# --- CodePipeline Role -------------------------------------------------------

resource "aws_iam_role" "codepipeline" {
  name = "${var.project_name}-${var.environment}-codepipeline"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "codepipeline.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-codepipeline"
  })
}

resource "aws_iam_role_policy" "codepipeline" {
  name = "${var.project_name}-${var.environment}-codepipeline-policy"
  role = aws_iam_role.codepipeline.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3Artifacts"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:GetBucketVersioning"
        ]
        Resource = [
          aws_s3_bucket.pipeline_artifacts.arn,
          "${aws_s3_bucket.pipeline_artifacts.arn}/*"
        ]
      },
      {
        Sid    = "CodeBuild"
        Effect = "Allow"
        Action = [
          "codebuild:StartBuild",
          "codebuild:BatchGetBuilds"
        ]
        Resource = aws_codebuild_project.casino_api.arn
      },
      {
        Sid    = "ECS"
        Effect = "Allow"
        Action = [
          "ecs:DescribeServices",
          "ecs:UpdateService",
          "ecs:RegisterTaskDefinition",
          "ecs:DescribeTaskDefinition"
        ]
        Resource = "*"
      },
      {
        Sid    = "CodeStarConnection"
        Effect = "Allow"
        Action = [
          "codestar-connections:UseConnection"
        ]
        Resource = var.codestar_connection_arn != "" ? var.codestar_connection_arn : "*"
      },
      {
        Sid    = "PassRole"
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = [
          aws_iam_role.ecs_execution.arn,
          aws_iam_role.ecs_task.arn
        ]
      }
    ]
  })
}
