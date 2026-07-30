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
# IAM -- Identity and Access Management
# =============================================================================
# CONTEXT: The multi-account AWS organization uses cross-account role assumption
# for access control. Each environment (dev, stage, prod) lives in a separate
# AWS account. Users authenticate in the central (hub) account and assume roles
# into target accounts based on their group membership.
#
# This pattern enforces least-privilege: developers can assume a dev role in
# staging but cannot access production. Admins can assume admin roles across
# all accounts. The ECR read-only policy grants cross-account image pull
# access so all environments share a single container registry.
# =============================================================================

# --- Admin Group --------------------------------------------------------------
# Full access across all AWS accounts. Membership is tightly controlled.

resource "aws_iam_group" "admin" {
  name = "admin"
  path = "/"
}

# Cross-account role assumption policies -- one per target account.
# Each policy allows STS AssumeRole into a specific account's admin role.

resource "aws_iam_group_policy" "assume_role_dev_admin" {
  group  = aws_iam_group.admin.id
  name   = "assume_role_dev_admin"
  policy = <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [{
     "Effect": "Allow",
     "Action": "sts:AssumeRole",
     "Resource": "arn:aws:iam::${local.dev_account}:role/assume_admin"
  }]
}
POLICY
}

resource "aws_iam_group_policy" "assume_role_stage_admin" {
  group  = aws_iam_group.admin.id
  name   = "assume_role_stage_admin"
  policy = <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [{
     "Effect": "Allow",
     "Action": "sts:AssumeRole",
     "Resource": "arn:aws:iam::${local.stage_account}:role/assume_admin"
  }]
}
POLICY
}

resource "aws_iam_group_policy" "assume_role_prod_admin" {
  group  = aws_iam_group.admin.id
  name   = "assume_role_prod_admin"
  policy = <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [{
     "Effect": "Allow",
     "Action": "sts:AssumeRole",
     "Resource": "arn:aws:iam::${local.prod_account}:role/assume_admin"
  }]
}
POLICY
}

resource "aws_iam_group_policy" "assume_role_infra_admin" {
  group  = aws_iam_group.admin.id
  name   = "assume_role_infra_admin"
  policy = <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [{
     "Effect": "Allow",
     "Action": "sts:AssumeRole",
     "Resource": "arn:aws:iam::${local.infra_account}:role/assume_admin"
  }]
}
POLICY
}

resource "aws_iam_group_policy" "assume_role_security_admin" {
  group  = aws_iam_group.admin.id
  name   = "assume_role_security_admin"
  policy = <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [{
     "Effect": "Allow",
     "Action": "sts:AssumeRole",
     "Resource": "arn:aws:iam::${local.security_account}:role/assume_admin"
  }]
}
POLICY
}

# --- Developer Group ----------------------------------------------------------
# Developers can access staging but not production directly.
# This enforces the deployment pipeline as the only path to prod.

resource "aws_iam_group" "developers" {
  name = "developers"
  path = "/"
}

resource "aws_iam_user" "developers" {
  for_each = toset(var.developers_users)
  name     = each.value
  path     = "/developers/"
}

resource "aws_iam_group_policy" "assume_role_stage_dev" {
  group  = aws_iam_group.developers.id
  name   = "assume_role_stage_dev"
  policy = <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [{
     "Effect": "Allow",
     "Action": "sts:AssumeRole",
     "Resource": "arn:aws:iam::${local.stage_account}:role/assume_dev"
  }]
}
POLICY
}

# --- Automation Users ---------------------------------------------------------
# Service accounts for CI/CD pipelines. These get scoped permissions --
# not admin access -- following the principle of least privilege.

resource "aws_iam_user" "cicd_deployer" {
  name = "cicd-deployer"
  path = "/automation/"
}

resource "aws_iam_access_key" "cicd_deployer" {
  user = aws_iam_user.cicd_deployer.name
}

# --- Database Protection ------------------------------------------------------
# Prevent accidental deletion of critical RDS instances even by admins.
# This is a safety net -- deletion_protection in RDS config is the first line.

resource "aws_iam_policy" "deny_rds_delete" {
  name        = "deny-rds-delete-critical"
  description = "Prevent deletion of critical database instances"

  policy = <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Deny",
    "Action": [
      "rds:DeleteDBInstance",
      "rds:DeleteDBCluster"
    ],
    "Resource": [
      "arn:aws:rds:*:*:db:player-db-prod",
      "arn:aws:rds:*:*:db:player-db-readreplica"
    ]
  }]
}
POLICY
}
