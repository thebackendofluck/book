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
# ECR Container Registry -- Centralized Image Management
# =============================================================================
# CONTEXT: Moving from on-premises to cloud meant containerizing all services.
# ECR serves as the central registry for all Docker images, with cross-account
# pull policies allowing dev, staging, and production accounts to share images
# from a single source of truth.
#
# The prevent_destroy lifecycle rule is critical -- accidentally deleting a
# repository would break deployments across all environments. Each repository
# gets a read-only policy granting pull access to every AWS account in the
# organization (dev, stage, prod, plus jurisdiction-specific accounts).
# =============================================================================

# Dynamic repository creation from a variable list.
# Adding a new microservice's container registry is a one-line change
# to the ecr_repos variable in variables.tf.
resource "aws_ecr_repository" "ecr_repos" {
  for_each = { for name in var.ecr_repos : name => name }
  name     = each.value
  lifecycle {
    prevent_destroy = true
  }
}

# Cross-account pull policy applied to all repositories.
# This enables the CI/CD pipeline to push images once and have every
# environment pull from the same registry -- no image promotion needed.
resource "aws_ecr_repository_policy" "ecr_policy" {
  for_each   = { for name in var.ecr_repos : name => name }
  repository = each.value

  policy = local.ecr_policy_read_only
}

# --- Pre-existing repositories (created before Terraform adoption) -----------
# These were imported into Terraform state to bring them under IaC management.

data "aws_ecr_repository" "platform_runtime" {
  name = "platform-runtime"
}

data "aws_ecr_repository" "backoffice" {
  name = "backoffice"
}

data "aws_ecr_repository" "game_service" {
  name = "game-service"
}

data "aws_ecr_repository" "payments" {
  name = "payments"
}

data "aws_ecr_repository" "risk_matrix" {
  name = "risk-matrix"
}

# Policies for pre-existing repositories
resource "aws_ecr_repository_policy" "platform_runtime" {
  repository = data.aws_ecr_repository.platform_runtime.name
  policy     = local.ecr_policy_read_only
}

resource "aws_ecr_repository_policy" "backoffice" {
  repository = data.aws_ecr_repository.backoffice.name
  policy     = local.ecr_policy_read_only
}

resource "aws_ecr_repository_policy" "game_service" {
  repository = data.aws_ecr_repository.game_service.name
  policy     = local.ecr_policy_read_only
}

resource "aws_ecr_repository_policy" "payments" {
  repository = data.aws_ecr_repository.payments.name
  policy     = local.ecr_policy_read_only
}

resource "aws_ecr_repository_policy" "risk_matrix" {
  repository = data.aws_ecr_repository.risk_matrix.name
  policy     = local.ecr_policy_read_only
}
