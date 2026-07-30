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
# ECR — Container Registry for Casino API
# =============================================================================
# Regulatory context:
#   NJ DGE 13:69O-1.7  — All software artifacts must be versioned, signed,
#                         and retained for audit purposes.
#   PCI-DSS 6.3.2      — Custom application code must be reviewed before
#                         release to production.
#   PCI-DSS 6.4        — Separate development/test from production.
#
# Architecture:
#   - Private ECR repository with image scanning enabled
#   - Lifecycle policy retains last 30 tagged images + 7 days untagged
#   - Image immutability prevents tag overwriting in production
# =============================================================================

resource "aws_ecr_repository" "casino_api" {
  name = "${var.project_name}-${var.environment}-casino-api"

  # Prevent tag overwriting — ensures deployment audit trail
  # NJ DGE 13:69O-1.7: Artifact immutability for version tracking
  image_tag_mutability = "IMMUTABLE"

  # Scan images on push for CVEs
  # PCI-DSS 6.1: Identify and rank new security vulnerabilities
  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-casino-api"
    Compliance = "NJ-DGE-13:69O-1.7,PCI-DSS-6.1"
  })
}

# --- Lifecycle Policy --------------------------------------------------------

resource "aws_ecr_lifecycle_policy" "casino_api" {
  repository = aws_ecr_repository.casino_api.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 30 tagged production images for rollback"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v", "release", "prod"]
          countType     = "imageCountMoreThan"
          countNumber   = 30
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Remove untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# --- Repository Policy (restrict to same account) ---------------------------

resource "aws_ecr_repository_policy" "casino_api" {
  repository = aws_ecr_repository.casino_api.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowPullFromECS"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.ecs_execution.arn
        }
        Action = [
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability"
        ]
      },
      {
        Sid    = "AllowPushFromCodeBuild"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.codebuild.arn
        }
        Action = [
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload"
        ]
      }
    ]
  })
}
