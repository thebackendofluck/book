# Companion code for "The Backend of Luck" - Chapter 28d, Elastic Compute and Cluster Autoscaling on EKS.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Chapter 28d — AWS scaffolding for elastic compute & cluster autoscaling
# -----------------------------------------------------------------------------
# This module provisions the *AWS-side* prerequisites the chapter describes:
#   - VPC + multi-AZ subnets tagged for Karpenter subnet discovery
#   - IAM roles: EKS cluster, worker node, and the Karpenter controller
#   - EKS control plane
#   - SQS interruption queue + EventBridge rules (spot interruption, rebalance,
#     instance state-change) — Karpenter's interruption handling
#   - ECR repositories for multi-arch (amd64 + arm64) image manifest lists
#
# NOT in this module (they are Kubernetes CRDs applied with kubectl/helm to the
# running cluster, shown as YAML in the chapter, not Terraform):
#   - Karpenter NodePool / EC2NodeClass
#   - KEDA ScaledObject / HPA
#   - NetworkPolicy / CiliumNetworkPolicy / ClusterNetworkPolicy
# =============================================================================

data "aws_caller_identity" "current" {}

locals {
  name           = "${var.project_name}-${var.environment}"
  cluster_name   = "${local.name}-eks"
  discovery_tags = { "karpenter.sh/discovery" = local.cluster_name }
}

# --- Network -----------------------------------------------------------------

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(var.tags, { Name = "${local.name}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${local.name}-igw" })
}

resource "aws_subnet" "private" {
  count             = length(var.azs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone = var.azs[count.index]

  # Karpenter discovers placement subnets via this tag; multi-AZ spread is what
  # keeps spot pools diverse and the cluster resilient.
  tags = merge(var.tags, local.discovery_tags, {
    Name                              = "${local.name}-private-${count.index}"
    "kubernetes.io/role/internal-elb" = "1"
  })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${local.name}-rt-private" })
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_security_group" "cluster" {
  name        = "${local.name}-eks-cluster"
  description = "EKS control plane / node shared security group"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, local.discovery_tags, { Name = "${local.name}-eks-sg" })
}

# --- IAM: EKS cluster role ---------------------------------------------------

data "aws_iam_policy_document" "eks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${local.name}-eks-cluster-role"
  assume_role_policy = data.aws_iam_policy_document.eks_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "cluster_policy" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# --- IAM: worker node role (used by Karpenter-provisioned nodes) --------------

data "aws_iam_policy_document" "node_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${local.name}-eks-node-role"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "node_policies" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

# --- IAM: Karpenter controller role + policy ---------------------------------

data "aws_iam_policy_document" "karpenter_assume" {
  statement {
    # EKS Pod Identity for the Karpenter controller
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "karpenter" {
  name               = "${local.name}-karpenter-controller"
  assume_role_policy = data.aws_iam_policy_document.karpenter_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "karpenter" {
  name = "${local.name}-karpenter-policy"
  role = aws_iam_role.karpenter.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Compute"
        Effect = "Allow"
        Action = [
          "ec2:RunInstances", "ec2:CreateFleet", "ec2:CreateLaunchTemplate",
          "ec2:CreateTags", "ec2:TerminateInstances", "ec2:DeleteLaunchTemplate",
          "ec2:DescribeInstances", "ec2:DescribeLaunchTemplates",
          "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups",
          "ec2:DescribeInstanceTypes", "ec2:DescribeInstanceTypeOfferings",
          "ec2:DescribeAvailabilityZones", "ec2:DescribeImages",
          "ec2:DescribeSpotPriceHistory"
        ]
        Resource = "*"
      },
      {
        Sid      = "PassNodeRole"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = aws_iam_role.node.arn
      },
      {
        Sid    = "InstanceProfile"
        Effect = "Allow"
        Action = [
          "iam:CreateInstanceProfile", "iam:AddRoleToInstanceProfile",
          "iam:RemoveRoleFromInstanceProfile", "iam:DeleteInstanceProfile",
          "iam:GetInstanceProfile", "iam:TagInstanceProfile"
        ]
        Resource = "*"
      },
      {
        Sid      = "Pricing"
        Effect   = "Allow"
        Action   = ["pricing:GetProducts", "ssm:GetParameter", "eks:DescribeCluster"]
        Resource = "*"
      },
      {
        Sid      = "Interruption"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.interruption.arn
      }
    ]
  })
}

# --- EKS control plane -------------------------------------------------------

# KMS key for Kubernetes secrets envelope encryption (CIS / ISO A.8.24 / PCI Req 3)
resource "aws_kms_key" "eks" {
  description             = "${local.name} EKS secrets envelope encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  tags                    = var.tags
}

resource "aws_kms_alias" "eks" {
  name          = "alias/${local.name}-eks"
  target_key_id = aws_kms_key.eks.key_id
}

# Control-plane audit logging sink (CIS / ISO A.8.15-A.8.16 / PCI Req 10)
resource "aws_cloudwatch_log_group" "eks" {
  name              = "/aws/eks/${local.cluster_name}/cluster"
  retention_in_days = var.control_plane_log_retention_days
  tags              = var.tags
}

resource "aws_eks_cluster" "this" {
  count    = var.create_eks ? 1 : 0
  name     = local.cluster_name
  role_arn = aws_iam_role.cluster.arn
  version  = var.eks_cluster_version

  # CIS: enable all five control-plane log types
  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  vpc_config {
    subnet_ids              = aws_subnet.private[*].id
    security_group_ids      = [aws_security_group.cluster.id]
    endpoint_private_access = true
    endpoint_public_access  = var.cluster_endpoint_public_access
    public_access_cidrs     = var.cluster_endpoint_public_access_cidrs
  }

  # CIS: envelope-encrypt secrets with a customer-managed KMS key
  encryption_config {
    provider {
      key_arn = aws_kms_key.eks.arn
    }
    resources = ["secrets"]
  }

  tags = merge(var.tags, { Name = local.cluster_name })
  depends_on = [
    aws_iam_role_policy_attachment.cluster_policy,
    aws_cloudwatch_log_group.eks,
  ]
}

# --- Karpenter interruption: SQS + EventBridge -------------------------------

resource "aws_sqs_queue" "interruption" {
  name                      = "${local.name}-karpenter-interruption"
  message_retention_seconds = 300
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

locals {
  interruption_rules = {
    spot_interruption = { source = ["aws.ec2"], detail-type = ["EC2 Spot Instance Interruption Warning"] }
    rebalance         = { source = ["aws.ec2"], detail-type = ["EC2 Instance Rebalance Recommendation"] }
    state_change      = { source = ["aws.ec2"], detail-type = ["EC2 Instance State-change Notification"] }
    scheduled_change  = { source = ["aws.health"], detail-type = ["AWS Health Event"] }
  }
}

resource "aws_cloudwatch_event_rule" "interruption" {
  for_each      = local.interruption_rules
  name          = "${local.name}-karpenter-${each.key}"
  event_pattern = jsonencode({ source = each.value.source, "detail-type" = each.value["detail-type"] })
  tags          = var.tags
}

# Anything that can put a message on this queue can tell Karpenter an instance is
# going away, and Karpenter's response is to cordon and drain it. Left open to
# `events.amazonaws.com` with no condition, that is a cross-account confused
# deputy: an EventBridge rule in *any* AWS account can target this queue by ARN,
# and a crafted "EC2 Spot Instance Interruption Warning" naming a live on-demand
# instance evicts the pods on it. On the stateful-amd64 pool those pods are
# in-progress game sessions, which makes an unauthenticated queue write a player
# dispute and a forced drain of the certified engine.
#
# Two conditions close it: SourceAccount pins the caller to this account, and
# SourceArn pins it to the four rules this module creates. `sqs.amazonaws.com`
# is dropped from the principal list because nothing in this module publishes
# here via SQS itself; the only writers are the rules below.
resource "aws_sqs_queue_policy" "interruption" {
  queue_url = aws_sqs_queue.interruption.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowKarpenterInterruptionRules"
        Effect    = "Allow"
        Principal = { Service = ["events.amazonaws.com"] }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.interruption.arn
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
          ArnEquals = {
            "aws:SourceArn" = [for r in aws_cloudwatch_event_rule.interruption : r.arn]
          }
        }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = { AWS = "*" }
        Action    = "sqs:*"
        Resource  = aws_sqs_queue.interruption.arn
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

resource "aws_cloudwatch_event_target" "interruption" {
  for_each  = aws_cloudwatch_event_rule.interruption
  rule      = each.value.name
  target_id = "karpenter-interruption-queue"
  arn       = aws_sqs_queue.interruption.arn
}

# --- ECR: multi-arch image repositories --------------------------------------

resource "aws_ecr_repository" "service" {
  for_each             = toset(var.ecr_repositories)
  name                 = "${local.name}/${each.value}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(var.tags, {
    Name = "${local.name}/${each.value}"
    Arch = "multi-arch:amd64+arm64"
  })
}
