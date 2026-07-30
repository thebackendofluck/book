# Companion code for "The Backend of Luck" - Chapter 22, Internal Docker Registry.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Chapter 37: Internal Docker Registry - AWS Infrastructure
# =============================================================================
# Enterprise-grade Docker registry infrastructure for iGaming platforms.
#
# Components:
# - EKS cluster for registry deployment
# - ECR for backup/mirror registry
# - S3 for registry storage backend
# - ElastiCache Redis for caching
# - NLB for low-latency access
# - WAF for security
#
# Estimated Monthly Cost: ~$800-1,200 (production configuration)
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.23"
    }
  }

  backend "s3" {
    bucket  = "igaming-terraform-state"
    key     = "chapter-37/registry/terraform.tfstate"
    region  = "eu-west-1"
    encrypt = true
    # S3-native state locking (Terraform >= 1.11, replaces DynamoDB locking)
    use_lockfile = true
  }
}

# =============================================================================
# Variables
# =============================================================================

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "registry-igaming"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.37.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones"
  type        = list(string)
  default     = ["eu-west-1a", "eu-west-1b", "eu-west-1c"]
}

variable "registry_storage_size" {
  description = "Registry storage size in GB"
  type        = number
  default     = 500
}

variable "enable_ecr_mirror" {
  description = "Enable ECR as backup/mirror registry"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Common tags"
  type        = map(string)
  default = {
    Project    = "Registry-iGaming"
    Chapter    = "37"
    ManagedBy  = "Terraform"
    Compliance = "PCI-DSS,SOX"
  }
}

# =============================================================================
# Provider
# =============================================================================

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = var.tags
  }
}

data "aws_caller_identity" "current" {}

# =============================================================================
# KMS Key for Encryption
# =============================================================================

resource "aws_kms_key" "registry" {
  description             = "KMS key for registry encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow S3 Service"
        Effect = "Allow"
        Principal = {
          Service = "s3.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:GenerateDataKey*"
        ]
        Resource = "*"
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-kms"
  }
}

resource "aws_kms_alias" "registry" {
  name          = "alias/${var.project_name}"
  target_key_id = aws_kms_key.registry.key_id
}

# =============================================================================
# VPC
# =============================================================================

resource "aws_vpc" "registry" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "${var.project_name}-vpc"
  }
}

# Restrict default security group (CKV2_AWS_12)
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.registry.id

  tags = {
    Name = "${var.project_name}-default-sg-restricted"
  }
}

# VPC Flow Logs (CKV2_AWS_11)
resource "aws_flow_log" "vpc" {
  vpc_id                   = aws_vpc.registry.id
  traffic_type             = "ALL"
  iam_role_arn             = aws_iam_role.flow_log.arn
  log_destination          = aws_cloudwatch_log_group.flow_log.arn
  log_destination_type     = "cloud-watch-logs"
  max_aggregation_interval = 60

  tags = {
    Name = "${var.project_name}-vpc-flow-log"
  }
}

resource "aws_cloudwatch_log_group" "flow_log" {
  name              = "/aws/vpc/${var.project_name}/flow-logs"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.registry.arn

  tags = {
    Name = "${var.project_name}-flow-logs"
  }
}

resource "aws_iam_role" "flow_log" {
  name = "${var.project_name}-vpc-flow-log"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "vpc-flow-logs.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "flow_log" {
  name = "${var.project_name}-flow-log-policy"
  role = aws_iam_role.flow_log.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_internet_gateway" "registry" {
  vpc_id = aws_vpc.registry.id

  tags = {
    Name = "${var.project_name}-igw"
  }
}

# Public Subnets
resource "aws_subnet" "public" {
  count                   = length(var.availability_zones)
  vpc_id                  = aws_vpc.registry.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name                                            = "${var.project_name}-public-${var.availability_zones[count.index]}"
    "kubernetes.io/role/elb"                        = "1"
    "kubernetes.io/cluster/${var.project_name}-eks" = "owned"
  }
}

# Private Subnets
resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.registry.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 4)
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name                                            = "${var.project_name}-private-${var.availability_zones[count.index]}"
    "kubernetes.io/role/internal-elb"               = "1"
    "kubernetes.io/cluster/${var.project_name}-eks" = "owned"
  }
}

# NAT Gateway
resource "aws_eip" "nat" {
  count  = 1 # Single NAT for cost savings
  domain = "vpc"

  tags = {
    Name = "${var.project_name}-nat-eip"
  }
}

resource "aws_nat_gateway" "registry" {
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id

  tags = {
    Name = "${var.project_name}-nat"
  }

  depends_on = [aws_internet_gateway.registry]
}

# Route Tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.registry.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.registry.id
  }

  tags = {
    Name = "${var.project_name}-public-rt"
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.registry.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.registry.id
  }

  tags = {
    Name = "${var.project_name}-private-rt"
  }
}

resource "aws_route_table_association" "public" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# =============================================================================
# S3 Bucket for Registry Storage
# =============================================================================

resource "aws_s3_bucket" "registry_storage" {
  bucket = "${var.project_name}-storage-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "${var.project_name}-storage"
  }
}

resource "aws_s3_bucket_versioning" "registry_storage" {
  bucket = aws_s3_bucket.registry_storage.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "registry_storage" {
  bucket = aws_s3_bucket.registry_storage.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.registry.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "registry_storage" {
  bucket = aws_s3_bucket.registry_storage.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "registry_storage" {
  bucket = aws_s3_bucket.registry_storage.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnforceSSLOnly"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.registry_storage.arn,
          "${aws_s3_bucket.registry_storage.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
      {
        Sid       = "DenyUnencryptedUploads"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.registry_storage.arn}/*"
        Condition = {
          StringNotEquals = {
            "s3:x-amz-server-side-encryption" = "aws:kms"
          }
        }
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.registry_storage]
}

resource "aws_s3_bucket_lifecycle_configuration" "registry_storage" {
  bucket = aws_s3_bucket.registry_storage.id

  rule {
    id     = "cleanup-incomplete-uploads"
    status = "Enabled"

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "transition-old-versions"
    status = "Enabled"

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "STANDARD_IA"
    }

    noncurrent_version_transition {
      noncurrent_days = 90
      storage_class   = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = 365
    }
  }
}

# =============================================================================
# ECR Repository (Optional Mirror)
# =============================================================================

resource "aws_ecr_repository" "mirror" {
  count = var.enable_ecr_mirror ? 1 : 0
  name  = "${var.project_name}-mirror"

  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.registry.arn
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "${var.project_name}-ecr-mirror"
  }
}

resource "aws_ecr_lifecycle_policy" "mirror" {
  count      = var.enable_ecr_mirror ? 1 : 0
  repository = aws_ecr_repository.mirror[0].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 50 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 50
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# =============================================================================
# ElastiCache Redis for Registry Cache
# =============================================================================

resource "aws_elasticache_subnet_group" "registry" {
  name       = "${var.project_name}-redis"
  subnet_ids = aws_subnet.private[*].id

  tags = {
    Name = "${var.project_name}-redis-subnet"
  }
}

resource "aws_security_group" "redis" {
  name_prefix = "${var.project_name}-redis-"
  vpc_id      = aws_vpc.registry.id

  ingress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "Redis from VPC"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound"
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.project_name}-redis-sg"
  }
}

resource "aws_elasticache_cluster" "registry" {
  cluster_id           = "${var.project_name}-cache"
  engine               = "redis"
  engine_version       = "8.0"
  node_type            = "cache.t3.medium"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.registry.name
  security_group_ids   = [aws_security_group.redis.id]

  snapshot_retention_limit = 7
  snapshot_window          = "03:00-05:00"
  maintenance_window       = "sun:05:00-sun:07:00"

  tags = {
    Name = "${var.project_name}-redis"
  }
}

# =============================================================================
# EKS Cluster for Registry
# =============================================================================

resource "aws_iam_role" "eks_cluster" {
  name = "${var.project_name}-eks-cluster"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "eks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "eks_cluster" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

resource "aws_eks_cluster" "registry" {
  name     = "${var.project_name}-eks"
  role_arn = aws_iam_role.eks_cluster.arn
  version  = "1.28"

  vpc_config {
    subnet_ids              = aws_subnet.private[*].id
    endpoint_private_access = true
    endpoint_public_access  = true
    # Illustrative: restrict in production to corporate/VPN CIDRs. 0.0.0.0/0
    # exposes the Kubernetes API server to the entire internet.
    public_access_cidrs = ["0.0.0.0/0"]
  }

  encryption_config {
    provider {
      key_arn = aws_kms_key.registry.arn
    }
    resources = ["secrets"]
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  tags = {
    Name = "${var.project_name}-eks"
  }

  depends_on = [aws_iam_role_policy_attachment.eks_cluster]
}

# EKS Node Group
resource "aws_iam_role" "eks_nodes" {
  name = "${var.project_name}-eks-nodes"

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
}

resource "aws_iam_role_policy_attachment" "eks_worker" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_cni" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_ecr" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.eks_nodes.name
}

# S3 access for registry storage
resource "aws_iam_role_policy" "eks_s3_access" {
  name = "${var.project_name}-s3-access"
  role = aws_iam_role.eks_nodes.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.registry_storage.arn,
          "${aws_s3_bucket.registry_storage.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_eks_node_group" "registry" {
  cluster_name    = aws_eks_cluster.registry.name
  node_group_name = "${var.project_name}-nodes"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = aws_subnet.private[*].id

  instance_types = ["m6i.large"]
  capacity_type  = "ON_DEMAND"

  scaling_config {
    desired_size = 2
    max_size     = 4
    min_size     = 2
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    role = "registry"
  }

  tags = {
    Name = "${var.project_name}-nodes"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker,
    aws_iam_role_policy_attachment.eks_cni,
    aws_iam_role_policy_attachment.eks_ecr
  ]
}

# =============================================================================
# Network Load Balancer
# =============================================================================

resource "aws_lb" "registry" {
  name               = "${var.project_name}-nlb"
  internal           = false
  load_balancer_type = "network"
  subnets            = aws_subnet.public[*].id

  enable_cross_zone_load_balancing = true

  tags = {
    Name = "${var.project_name}-nlb"
  }
}

resource "aws_lb_target_group" "registry" {
  name        = "${var.project_name}-registry"
  port        = 5000
  protocol    = "TCP"
  vpc_id      = aws_vpc.registry.id
  target_type = "ip"

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = "/v2/"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 10
  }

  tags = {
    Name = "${var.project_name}-registry-tg"
  }
}

resource "aws_lb_listener" "registry" {
  load_balancer_arn = aws_lb.registry.arn
  port              = 443
  protocol          = "TLS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.registry.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.registry.arn
  }
}

# =============================================================================
# ACM Certificate
# =============================================================================

resource "aws_acm_certificate" "registry" {
  domain_name               = "registry.${var.project_name}.example.com"
  subject_alternative_names = ["*.registry.${var.project_name}.example.com"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.project_name}-cert"
  }
}

# =============================================================================
# Outputs
# =============================================================================

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.registry.id
}

output "eks_cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = aws_eks_cluster.registry.endpoint
}

output "eks_cluster_name" {
  description = "EKS cluster name"
  value       = aws_eks_cluster.registry.name
}

output "registry_storage_bucket" {
  description = "S3 bucket for registry storage"
  value       = aws_s3_bucket.registry_storage.bucket
}

output "redis_endpoint" {
  description = "ElastiCache Redis endpoint"
  value       = aws_elasticache_cluster.registry.cache_nodes[0].address
}

output "nlb_dns_name" {
  description = "NLB DNS name"
  value       = aws_lb.registry.dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL"
  value       = var.enable_ecr_mirror ? aws_ecr_repository.mirror[0].repository_url : null
}
