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
# CI/CD — CodePipeline + CodeBuild for Casino API
# =============================================================================
# Regulatory context:
#   NJ DGE 13:69O-1.7  — All deployments must be automated, auditable, and
#                         traceable to specific code commits.
#   PA PGCB §809a.6    — Change management processes must include automated
#                         testing before production deployment.
#   PCI-DSS 6.3        — Develop software applications in accordance with
#                         PCI DSS; based on industry best practices.
#   PCI-DSS 6.4        — Follow change control procedures for all changes
#                         to system components.
#   PCI-DSS 6.4.5      — Change control must include documentation of impact,
#                         management sign-off, and testing.
#
# Pipeline stages:
#   1. Source  — Pull from GitHub (CodeStar connection)
#   2. Build   — Docker build, test, push to ECR (CodeBuild)
#   3. Deploy  — Update ECS service with new task definition
# =============================================================================

# --- S3 Bucket for Pipeline Artifacts ----------------------------------------

resource "aws_s3_bucket" "pipeline_artifacts" {
  bucket = "${var.project_name}-${var.environment}-pipeline-artifacts"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-pipeline-artifacts"
  })
}

resource "aws_s3_bucket_server_side_encryption_configuration" "pipeline_artifacts" {
  bucket = aws_s3_bucket.pipeline_artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "pipeline_artifacts" {
  bucket = aws_s3_bucket.pipeline_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "pipeline_artifacts" {
  bucket = aws_s3_bucket.pipeline_artifacts.id

  rule {
    id     = "expire-old-artifacts"
    status = "Enabled"

    expiration {
      days = 90
    }
  }
}

# --- CodeBuild Project -------------------------------------------------------

resource "aws_codebuild_project" "casino_api" {
  name          = "${var.project_name}-${var.environment}-casino-api-build"
  description   = "Build and test casino API Docker image"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 20

  artifacts {
    type = "CODEPIPELINE"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_SMALL"
    image                       = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    type                        = "LINUX_CONTAINER"
    image_pull_credentials_type = "CODEBUILD"
    privileged_mode             = true # Required for Docker builds

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = data.aws_caller_identity.current.account_id
    }

    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = var.aws_region
    }

    environment_variable {
      name  = "ECR_REPO_URL"
      value = aws_ecr_repository.casino_api.repository_url
    }

    environment_variable {
      name  = "ECS_CLUSTER"
      value = aws_ecs_cluster.main.name
    }

    environment_variable {
      name  = "ECS_SERVICE"
      value = aws_ecs_service.casino_api.name
    }

    environment_variable {
      name  = "TASK_FAMILY"
      value = aws_ecs_task_definition.casino_api.family
    }
  }

  source {
    type      = "CODEPIPELINE"
    buildspec = "buildspec.yml"
  }

  logs_config {
    cloudwatch_logs {
      group_name  = "/aws/codebuild/${var.project_name}-${var.environment}-casino-api"
      stream_name = "build"
    }
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-casino-api-build"
    Compliance = "PCI-DSS-6.3,NJ-DGE-13:69O-1.7"
  })
}

resource "aws_cloudwatch_log_group" "codebuild" {
  name              = "/aws/codebuild/${var.project_name}-${var.environment}-casino-api"
  retention_in_days = 2557 # 7-year retention

  tags = merge(var.tags, {
    Compliance = "NJ-DGE-7yr-retention"
  })
}

# --- CodePipeline ------------------------------------------------------------

resource "aws_codepipeline" "casino_api" {
  name     = "${var.project_name}-${var.environment}-casino-api-pipeline"
  role_arn = aws_iam_role.codepipeline.arn

  artifact_store {
    location = aws_s3_bucket.pipeline_artifacts.bucket
    type     = "S3"
  }

  # Stage 1: Source — Pull from GitHub
  stage {
    name = "Source"

    action {
      name             = "GitHub"
      category         = "Source"
      owner            = "AWS"
      provider         = "CodeStarSourceConnection"
      version          = "1"
      output_artifacts = ["source_output"]

      configuration = {
        ConnectionArn    = var.codestar_connection_arn
        FullRepositoryId = var.github_repo
        BranchName       = var.github_branch
        # NJ DGE: Deployments traceable to specific commits
      }
    }
  }

  # Stage 2: Build — Docker build, test, push to ECR
  stage {
    name = "Build"

    action {
      name             = "DockerBuild"
      category         = "Build"
      owner            = "AWS"
      provider         = "CodeBuild"
      version          = "1"
      input_artifacts  = ["source_output"]
      output_artifacts = ["build_output"]

      configuration = {
        ProjectName = aws_codebuild_project.casino_api.name
        # PA PGCB: Automated testing before production
      }
    }
  }

  # Stage 3: Deploy — Update ECS service
  stage {
    name = "Deploy"

    action {
      name            = "DeployToECS"
      category        = "Deploy"
      owner           = "AWS"
      provider        = "ECS"
      version         = "1"
      input_artifacts = ["build_output"]

      configuration = {
        ClusterName = aws_ecs_cluster.main.name
        ServiceName = aws_ecs_service.casino_api.name
        FileName    = "imagedefinitions.json"
        # PCI-DSS 6.4: Controlled change deployment
      }
    }
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-pipeline"
    Compliance = "NJ-DGE-13:69O-1.7,PCI-DSS-6.4"
  })
}

# --- Data Sources ------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
