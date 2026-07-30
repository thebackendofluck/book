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
# Variables and Locals -- Multi-Environment Configuration
# =============================================================================
# CONTEXT: The multi-account AWS organization separates environments into
# distinct AWS accounts for blast-radius isolation. Each account ID is stored
# as a local value and referenced throughout the Terraform configuration.
#
# The ecr_repos variable drives dynamic resource creation -- adding a new
# microservice's container registry is a single line change here, and
# Terraform creates the ECR repository, applies cross-account policies,
# and sets up lifecycle rules automatically.
# =============================================================================

variable "env" {
  description = "Environment name (dev, stage, prod)"
  default     = "prod"
}

variable "default_vpc" {
  description = "VPC ID for the production environment"
  type        = string
}

variable "default_subnets" {
  description = "Subnet IDs across availability zones"
  type        = list(string)
}

variable "ssh_key_name" {
  description = "EC2 key pair name for SSH access"
  type        = string
}

variable "base_ubuntu_ami" {
  description = "Corporate base AMI (hardened Ubuntu 20.04 + agents)"
  type        = string
}

variable "db_admin_username" {
  description = "RDS admin username (rotate via Secrets Manager)"
  type        = string
  sensitive   = true
}

variable "db_admin_password" {
  description = "RDS admin password (rotate via Secrets Manager)"
  type        = string
  sensitive   = true
}

# --- ECR Repositories ---------------------------------------------------------
# Each line represents a microservice or shared component that gets its own
# container registry. The naming convention uses slashes for organizational
# grouping (e.g., corp/ubuntu20 for base images, sre/terraform for tooling).

variable "ecr_repos" {
  description = "List of ECR repository names to create"
  default = [
    "sre/terraform",
    "sre/ansible",
    "sre/ansible-mitogen",
    "platform-build",
    "platform-runtime",
    "game-service",
    "lookup-service",
    "real-time-analytics",
    "change-management",
    "pixel-tracking",
    "cashier-ui",
    "backoffice",
    "payments",
    "risk-matrix",
    "account-history-service",
    "vip-processor",
    "geo-compliance-service",
    "corp/tomcat9",
    "corp/php7",
    "corp/node14",
    "corp/jdk11",
    "corp/postgres11",
    "corp/nginx1-19",
    "corp/ubuntu20",
    "corp/redis",
    "affiliate-portal",
    "affiliate-exporter",
    "affiliate-stats",
    "error-reporting",
    "document-storage"
  ]
}

variable "developers_users" {
  description = "List of developer IAM usernames"
  type        = list(string)
  default     = []
  # Populated from tfvars per environment -- not committed to version control
}

# --- Apps behind ALB ----------------------------------------------------------
variable "alb_apps" {
  description = "Services that get their own Application Load Balancer"
  type        = list(string)
  default = [
    "game-service",
    "lookup-service",
    "player-tracking",
    "risk-alert",
    "affiliate-portal"
  ]
}

# --- Multi-Account Organization -----------------------------------------------
# Each environment lives in a separate AWS account for blast-radius isolation.
# The hub account holds IAM users; they assume roles into target accounts.

locals {
  dev_account      = "111111111111"
  stage_account    = "222222222222"
  prod_account     = "333333333333"
  infra_account    = "444444444444"
  security_account = "555555555555"
  logging_account  = "666666666666"

  # Cross-account ECR pull policy
  # Grants read access to all environment accounts so they can pull
  # container images from the central registry.
  ecr_policy_read_only = <<EOF
{
  "Version": "2008-10-17",
  "Statement": [
    {
      "Sid": "ECRPullAccess",
      "Effect": "Allow",
      "Principal": {
        "AWS": [
          "arn:aws:iam::${local.dev_account}:root",
          "arn:aws:iam::${local.stage_account}:root",
          "arn:aws:iam::${local.prod_account}:root",
          "arn:aws:iam::${local.infra_account}:root"
        ]
      },
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:DescribeImages",
        "ecr:DescribeRepositories",
        "ecr:GetDownloadUrlForLayer",
        "ecr:GetRepositoryPolicy",
        "ecr:ListImages",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage"
      ]
    }
  ]
}
EOF
}
