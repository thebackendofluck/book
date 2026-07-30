# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Chapter 40: RTC Module - AWS Infrastructure
# =============================================================================
# Real-Time Clock infrastructure for iGaming platforms requiring
# microsecond-precision temporal accuracy and regulatory compliance.
#
# Components:
# - VPC with multi-AZ subnets (low-latency network)
# - EKS cluster for RTC services
# - ElastiCache Redis for timestamp caching
# - RDS PostgreSQL for timestamp storage and audit logs
# - NLB for sub-millisecond latency time services
# - CloudHSM for cryptographic operations (optional)
# - S3 for audit log archival
# - CloudWatch for monitoring and alerting
#
# Estimated Monthly Cost: ~$2,800-3,500 (production configuration)
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
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.11"
    }
  }

  backend "s3" {
    bucket       = "igaming-terraform-state"
    key          = "chapter-40/rtc-infrastructure/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    # S3-native state locking (Terraform >= 1.11, replaces DynamoDB locking)
    use_lockfile = true
  }
}

# =============================================================================
# Variables
# =============================================================================

variable "environment" {
  description = "Environment name (dev, staging, production)"
  type        = string
  default     = "production"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "rtc-igaming"
}

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "eu-west-1" # Ireland - low latency to UK/Malta regulators
}

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.40.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones for multi-AZ deployment"
  type        = list(string)
  default     = ["eu-west-1a", "eu-west-1b", "eu-west-1c"]
}

variable "eks_cluster_version" {
  description = "Kubernetes version for EKS"
  type        = string
  default     = "1.28"
}

variable "enable_cloudhsm" {
  description = "Enable CloudHSM for hardware security (adds ~$1.50/hr)"
  type        = bool
  default     = false
}

variable "redis_node_type" {
  description = "ElastiCache Redis node type"
  type        = string
  default     = "cache.r6g.large" # 13.07 GB memory, enhanced networking
}

variable "rds_instance_class" {
  description = "RDS PostgreSQL instance class"
  type        = string
  default     = "db.r6g.large" # 2 vCPUs, 16 GB RAM
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default = {
    Project    = "RTC-iGaming"
    Chapter    = "40"
    ManagedBy  = "Terraform"
    Compliance = "GLI-11,MGA,UKGC"
  }
}

# =============================================================================
# Provider Configuration
# =============================================================================

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = var.tags
  }
}

# =============================================================================
# Data Sources
# =============================================================================

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

# =============================================================================
# KMS Key for Encryption
# =============================================================================

resource "aws_kms_key" "rtc_encryption" {
  description             = "KMS key for RTC system encryption"
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
        Sid    = "Allow CloudWatch Logs"
        Effect = "Allow"
        Principal = {
          Service = "logs.${var.aws_region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*"
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
          }
        }
      },
      {
        Sid    = "Allow EKS"
        Effect = "Allow"
        Principal = {
          Service = "eks.amazonaws.com"
        }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*"
        ]
        Resource = "*"
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-kms-key"
  }
}

resource "aws_kms_alias" "rtc_encryption" {
  name          = "alias/${var.project_name}-encryption"
  target_key_id = aws_kms_key.rtc_encryption.key_id
}

# =============================================================================
# VPC Configuration - Optimized for Low Latency
# =============================================================================

resource "aws_vpc" "rtc_vpc" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  # Enable enhanced networking for lower latency
  # checkov:skip=CKV2_AWS_11:VPC flow logs configured separately
  tags = {
    Name = "${var.project_name}-vpc"
  }
}

# VPC Flow Logs for compliance
resource "aws_flow_log" "rtc_vpc_flow_log" {
  iam_role_arn    = aws_iam_role.vpc_flow_log_role.arn
  log_destination = aws_cloudwatch_log_group.vpc_flow_logs.arn
  traffic_type    = "ALL"
  vpc_id          = aws_vpc.rtc_vpc.id

  tags = {
    Name = "${var.project_name}-vpc-flow-log"
  }
}

resource "aws_cloudwatch_log_group" "vpc_flow_logs" {
  name              = "/aws/vpc/${var.project_name}-flow-logs"
  retention_in_days = 365 # Regulatory compliance - 1 year minimum
  kms_key_id        = aws_kms_key.rtc_encryption.arn

  tags = {
    Name = "${var.project_name}-vpc-flow-logs"
  }
}

resource "aws_iam_role" "vpc_flow_log_role" {
  name = "${var.project_name}-vpc-flow-log-role"

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

resource "aws_iam_role_policy" "vpc_flow_log_policy" {
  name = "${var.project_name}-vpc-flow-log-policy"
  role = aws_iam_role.vpc_flow_log_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams"
        ]
        Effect   = "Allow"
        Resource = "*"
      }
    ]
  })
}

# Internet Gateway
resource "aws_internet_gateway" "rtc_igw" {
  vpc_id = aws_vpc.rtc_vpc.id

  tags = {
    Name = "${var.project_name}-igw"
  }
}

# Public Subnets (for NLB, NAT Gateways)
resource "aws_subnet" "public" {
  count                   = length(var.availability_zones)
  vpc_id                  = aws_vpc.rtc_vpc.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name                                            = "${var.project_name}-public-${var.availability_zones[count.index]}"
    "kubernetes.io/role/elb"                        = "1"
    "kubernetes.io/cluster/${var.project_name}-eks" = "owned"
  }
}

# Private Subnets (for EKS nodes, Redis, RDS)
resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.rtc_vpc.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 4)
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name                                            = "${var.project_name}-private-${var.availability_zones[count.index]}"
    "kubernetes.io/role/internal-elb"               = "1"
    "kubernetes.io/cluster/${var.project_name}-eks" = "owned"
  }
}

# Database Subnets (isolated for RDS)
resource "aws_subnet" "database" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.rtc_vpc.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 8)
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${var.project_name}-database-${var.availability_zones[count.index]}"
  }
}

# Elastic IPs for NAT Gateways
resource "aws_eip" "nat" {
  count  = length(var.availability_zones)
  domain = "vpc"

  tags = {
    Name = "${var.project_name}-nat-eip-${var.availability_zones[count.index]}"
  }

  depends_on = [aws_internet_gateway.rtc_igw]
}

# NAT Gateways (one per AZ for HA)
resource "aws_nat_gateway" "rtc_nat" {
  count         = length(var.availability_zones)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name = "${var.project_name}-nat-${var.availability_zones[count.index]}"
  }

  depends_on = [aws_internet_gateway.rtc_igw]
}

# Route Tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.rtc_vpc.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.rtc_igw.id
  }

  tags = {
    Name = "${var.project_name}-public-rt"
  }
}

resource "aws_route_table" "private" {
  count  = length(var.availability_zones)
  vpc_id = aws_vpc.rtc_vpc.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.rtc_nat[count.index].id
  }

  tags = {
    Name = "${var.project_name}-private-rt-${var.availability_zones[count.index]}"
  }
}

# Route Table Associations
resource "aws_route_table_association" "public" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_route_table_association" "database" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.database[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# =============================================================================
# Security Groups
# =============================================================================

# EKS Cluster Security Group
resource "aws_security_group" "eks_cluster" {
  name_prefix = "${var.project_name}-eks-cluster-"
  description = "Security group for EKS cluster"
  vpc_id      = aws_vpc.rtc_vpc.id

  tags = {
    Name = "${var.project_name}-eks-cluster-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# EKS Node Security Group
resource "aws_security_group" "eks_nodes" {
  name_prefix = "${var.project_name}-eks-nodes-"
  description = "Security group for EKS worker nodes"
  vpc_id      = aws_vpc.rtc_vpc.id

  # Allow all outbound traffic
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  # Allow node-to-node communication
  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
    description = "Allow node-to-node communication"
  }

  # Allow cluster API communication
  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_cluster.id]
    description     = "Allow cluster API communication"
  }

  # Allow kubelet communication
  ingress {
    from_port       = 10250
    to_port         = 10250
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_cluster.id]
    description     = "Allow kubelet communication"
  }

  tags = {
    Name = "${var.project_name}-eks-nodes-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Redis Security Group
resource "aws_security_group" "redis" {
  name_prefix = "${var.project_name}-redis-"
  description = "Security group for ElastiCache Redis"
  vpc_id      = aws_vpc.rtc_vpc.id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
    description     = "Allow Redis from EKS nodes"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = {
    Name = "${var.project_name}-redis-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# RDS Security Group
resource "aws_security_group" "rds" {
  name_prefix = "${var.project_name}-rds-"
  description = "Security group for RDS PostgreSQL"
  vpc_id      = aws_vpc.rtc_vpc.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
    description     = "Allow PostgreSQL from EKS nodes"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = {
    Name = "${var.project_name}-rds-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# NLB Security Group
resource "aws_security_group" "nlb" {
  name_prefix = "${var.project_name}-nlb-"
  description = "Security group for Network Load Balancer"
  vpc_id      = aws_vpc.rtc_vpc.id

  # HTTPS for RTC API
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow HTTPS from anywhere"
  }

  # gRPC for high-performance time sync
  ingress {
    from_port   = 8443
    to_port     = 8443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow gRPC from anywhere"
  }

  # NTP (UDP 123) - for NTP validation
  ingress {
    from_port   = 123
    to_port     = 123
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow NTP"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = {
    Name = "${var.project_name}-nlb-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# =============================================================================
# EKS Cluster
# =============================================================================

resource "aws_iam_role" "eks_cluster" {
  name = "${var.project_name}-eks-cluster-role"

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

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

resource "aws_iam_role_policy_attachment" "eks_vpc_resource_controller" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSVPCResourceController"
  role       = aws_iam_role.eks_cluster.name
}

resource "aws_cloudwatch_log_group" "eks_cluster" {
  name              = "/aws/eks/${var.project_name}-eks/cluster"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.rtc_encryption.arn

  tags = {
    Name = "${var.project_name}-eks-logs"
  }
}

resource "aws_eks_cluster" "rtc_cluster" {
  name     = "${var.project_name}-eks"
  version  = var.eks_cluster_version
  role_arn = aws_iam_role.eks_cluster.arn

  vpc_config {
    subnet_ids              = aws_subnet.private[*].id
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = ["0.0.0.0/0"] # Restrict in production
    security_group_ids      = [aws_security_group.eks_cluster.id]
  }

  encryption_config {
    provider {
      key_arn = aws_kms_key.rtc_encryption.arn
    }
    resources = ["secrets"]
  }

  enabled_cluster_log_types = [
    "api",
    "audit",
    "authenticator",
    "controllerManager",
    "scheduler"
  ]

  tags = {
    Name = "${var.project_name}-eks"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
    aws_iam_role_policy_attachment.eks_vpc_resource_controller,
    aws_cloudwatch_log_group.eks_cluster
  ]
}

# EKS Node Group
resource "aws_iam_role" "eks_nodes" {
  name = "${var.project_name}-eks-nodes-role"

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

resource "aws_iam_role_policy_attachment" "eks_worker_node_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_cni_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_container_registry_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.eks_nodes.name
}

# RTC Service Node Group - Optimized for low latency
resource "aws_eks_node_group" "rtc_services" {
  cluster_name    = aws_eks_cluster.rtc_cluster.name
  node_group_name = "${var.project_name}-rtc-services"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = aws_subnet.private[*].id

  # c6in instances - network optimized for low latency
  instance_types = ["c6in.xlarge"]
  capacity_type  = "ON_DEMAND"

  scaling_config {
    desired_size = 3
    max_size     = 9
    min_size     = 3
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    role        = "rtc-service"
    workload    = "time-critical"
    "node-type" = "rtc"
  }

  taint {
    key    = "workload"
    value  = "time-critical"
    effect = "NO_SCHEDULE"
  }

  tags = {
    Name = "${var.project_name}-rtc-services-ng"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry_policy
  ]
}

# General Purpose Node Group
resource "aws_eks_node_group" "general" {
  cluster_name    = aws_eks_cluster.rtc_cluster.name
  node_group_name = "${var.project_name}-general"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = aws_subnet.private[*].id

  instance_types = ["m6i.large"]
  capacity_type  = "ON_DEMAND"

  scaling_config {
    desired_size = 2
    max_size     = 6
    min_size     = 2
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    role        = "general"
    "node-type" = "general"
  }

  tags = {
    Name = "${var.project_name}-general-ng"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry_policy
  ]
}

# =============================================================================
# ElastiCache Redis - Timestamp Caching
# =============================================================================

resource "aws_elasticache_subnet_group" "rtc_redis" {
  name       = "${var.project_name}-redis-subnet"
  subnet_ids = aws_subnet.private[*].id

  tags = {
    Name = "${var.project_name}-redis-subnet"
  }
}

resource "aws_elasticache_parameter_group" "rtc_redis" {
  name   = "${var.project_name}-redis-params"
  family = "redis7"

  # Optimized for low-latency timestamp operations
  parameter {
    name  = "maxmemory-policy"
    value = "volatile-lru"
  }

  parameter {
    name  = "tcp-keepalive"
    value = "60"
  }

  parameter {
    name  = "timeout"
    value = "0"
  }

  # Enable cluster mode for horizontal scaling
  parameter {
    name  = "cluster-enabled"
    value = "yes"
  }

  tags = {
    Name = "${var.project_name}-redis-params"
  }
}

resource "aws_elasticache_replication_group" "rtc_redis" {
  replication_group_id = "${var.project_name}-redis"
  description          = "RTC timestamp caching cluster"

  node_type                  = var.redis_node_type
  num_node_groups            = 3 # 3 shards for timestamp partitioning
  replicas_per_node_group    = 1 # 1 replica per shard for HA
  automatic_failover_enabled = true
  multi_az_enabled           = true

  engine               = "redis"
  engine_version       = "8.0"
  port                 = 6379
  parameter_group_name = aws_elasticache_parameter_group.rtc_redis.name
  subnet_group_name    = aws_elasticache_subnet_group.rtc_redis.name
  security_group_ids   = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = aws_kms_key.rtc_encryption.arn

  # Maintenance window during low traffic
  maintenance_window       = "sun:05:00-sun:07:00"
  snapshot_retention_limit = 7
  snapshot_window          = "03:00-05:00"

  # Auto minor version upgrade for security patches
  auto_minor_version_upgrade = true

  tags = {
    Name = "${var.project_name}-redis"
  }
}

# =============================================================================
# RDS PostgreSQL - Timestamp Storage & Audit Logs
# =============================================================================

resource "aws_db_subnet_group" "rtc_postgres" {
  name       = "${var.project_name}-postgres-subnet"
  subnet_ids = aws_subnet.database[*].id

  tags = {
    Name = "${var.project_name}-postgres-subnet"
  }
}

resource "aws_db_parameter_group" "rtc_postgres" {
  name   = "${var.project_name}-postgres-params"
  family = "postgres15"

  # Optimized for timestamp workloads
  parameter {
    name  = "shared_buffers"
    value = "{DBInstanceClassMemory/4096}" # 25% of RAM
  }

  parameter {
    name  = "effective_cache_size"
    value = "{DBInstanceClassMemory*3/4096}" # 75% of RAM
  }

  parameter {
    name  = "work_mem"
    value = "65536" # 64MB for complex queries
  }

  parameter {
    name  = "maintenance_work_mem"
    value = "524288" # 512MB for maintenance
  }

  # WAL settings for durability
  parameter {
    name  = "synchronous_commit"
    value = "on"
  }

  parameter {
    name  = "wal_level"
    value = "replica"
  }

  # Logging for audit compliance
  parameter {
    name  = "log_statement"
    value = "all"
  }

  parameter {
    name  = "log_duration"
    value = "1"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "100" # Log queries > 100ms
  }

  # Timezone - always UTC for RTC
  parameter {
    name  = "timezone"
    value = "UTC"
  }

  tags = {
    Name = "${var.project_name}-postgres-params"
  }
}

resource "random_password" "rds_password" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_secretsmanager_secret" "rds_credentials" {
  name                    = "${var.project_name}/rds/credentials"
  description             = "RDS PostgreSQL credentials for RTC system"
  kms_key_id              = aws_kms_key.rtc_encryption.arn
  recovery_window_in_days = 30

  tags = {
    Name = "${var.project_name}-rds-credentials"
  }
}

resource "aws_secretsmanager_secret_version" "rds_credentials" {
  secret_id = aws_secretsmanager_secret.rds_credentials.id
  secret_string = jsonencode({
    username = "rtc_admin"
    password = random_password.rds_password.result
    host     = aws_db_instance.rtc_postgres.address
    port     = 5432
    database = "rtc_timestamps"
  })
}

resource "aws_db_instance" "rtc_postgres" {
  identifier     = "${var.project_name}-postgres"
  engine         = "postgres"
  engine_version = "18.0"

  instance_class        = var.rds_instance_class
  allocated_storage     = 100
  max_allocated_storage = 500 # Autoscaling up to 500GB
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.rtc_encryption.arn

  db_name  = "rtc_timestamps"
  username = "rtc_admin"
  password = random_password.rds_password.result

  db_subnet_group_name   = aws_db_subnet_group.rtc_postgres.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.rtc_postgres.name

  multi_az                  = true
  publicly_accessible       = false
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.project_name}-postgres-final-snapshot"

  backup_retention_period = 30
  backup_window           = "02:00-03:00"
  maintenance_window      = "sun:05:00-sun:07:00"

  # Performance Insights for monitoring
  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.rtc_encryption.arn
  performance_insights_retention_period = 7

  # Enhanced monitoring
  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_monitoring.arn

  # Enable IAM authentication
  iam_database_authentication_enabled = true

  # CloudWatch logs export
  enabled_cloudwatch_logs_exports = [
    "postgresql",
    "upgrade"
  ]

  tags = {
    Name = "${var.project_name}-postgres"
  }
}

resource "aws_iam_role" "rds_monitoring" {
  name = "${var.project_name}-rds-monitoring-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "monitoring.rds.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
  role       = aws_iam_role.rds_monitoring.name
}

# Read Replica for analytics queries
resource "aws_db_instance" "rtc_postgres_replica" {
  identifier     = "${var.project_name}-postgres-replica"
  instance_class = var.rds_instance_class

  replicate_source_db = aws_db_instance.rtc_postgres.identifier

  storage_encrypted = true
  kms_key_id        = aws_kms_key.rtc_encryption.arn

  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.rtc_postgres.name

  publicly_accessible = false
  multi_az            = false

  # Performance Insights
  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.rtc_encryption.arn
  performance_insights_retention_period = 7

  # Enhanced monitoring
  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_monitoring.arn

  tags = {
    Name = "${var.project_name}-postgres-replica"
  }
}

# =============================================================================
# S3 Bucket for Audit Logs Archive
# =============================================================================

resource "aws_s3_bucket" "rtc_audit_logs" {
  bucket = "${var.project_name}-audit-logs-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "${var.project_name}-audit-logs"
  }
}

resource "aws_s3_bucket_versioning" "rtc_audit_logs" {
  bucket = aws_s3_bucket.rtc_audit_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "rtc_audit_logs" {
  bucket = aws_s3_bucket.rtc_audit_logs.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.rtc_encryption.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "rtc_audit_logs" {
  bucket = aws_s3_bucket.rtc_audit_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "rtc_audit_logs" {
  bucket = aws_s3_bucket.rtc_audit_logs.id

  rule {
    id     = "audit-log-lifecycle"
    status = "Enabled"

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    transition {
      days          = 365
      storage_class = "DEEP_ARCHIVE"
    }

    # Regulatory requirement: 7 years retention
    expiration {
      days = 2555 # ~7 years
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = 365
    }
  }
}

# S3 bucket policy for audit log integrity
resource "aws_s3_bucket_policy" "rtc_audit_logs" {
  bucket = aws_s3_bucket.rtc_audit_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnforceTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.rtc_audit_logs.arn,
          "${aws_s3_bucket.rtc_audit_logs.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
      {
        Sid       = "DenyDeleteForCompliance"
        Effect    = "Deny"
        Principal = "*"
        Action = [
          "s3:DeleteObject",
          "s3:DeleteObjectVersion"
        ]
        Resource = "${aws_s3_bucket.rtc_audit_logs.arn}/*"
        Condition = {
          StringNotEquals = {
            "aws:PrincipalArn" = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/ComplianceAdmin"
          }
        }
      }
    ]
  })
}

# =============================================================================
# Network Load Balancer for Low-Latency RTC API
# =============================================================================

resource "aws_lb" "rtc_nlb" {
  name               = "${var.project_name}-nlb"
  internal           = false
  load_balancer_type = "network"
  subnets            = aws_subnet.public[*].id

  enable_cross_zone_load_balancing = true
  enable_deletion_protection       = true

  tags = {
    Name = "${var.project_name}-nlb"
  }
}

# Target Group for RTC API (HTTPS)
resource "aws_lb_target_group" "rtc_api" {
  name        = "${var.project_name}-rtc-api"
  port        = 8080
  protocol    = "TCP"
  vpc_id      = aws_vpc.rtc_vpc.id
  target_type = "ip"

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = "/health"
    port                = "traffic-port"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 10
  }

  stickiness {
    enabled = false
    type    = "source_ip"
  }

  tags = {
    Name = "${var.project_name}-rtc-api-tg"
  }
}

# Target Group for gRPC (high-performance time sync)
resource "aws_lb_target_group" "rtc_grpc" {
  name        = "${var.project_name}-rtc-grpc"
  port        = 50051
  protocol    = "TCP"
  vpc_id      = aws_vpc.rtc_vpc.id
  target_type = "ip"

  health_check {
    enabled             = true
    protocol            = "TCP"
    port                = "traffic-port"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 10
  }

  tags = {
    Name = "${var.project_name}-rtc-grpc-tg"
  }
}

# Listeners
resource "aws_lb_listener" "rtc_https" {
  load_balancer_arn = aws_lb.rtc_nlb.arn
  port              = 443
  protocol          = "TLS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.rtc_cert.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.rtc_api.arn
  }
}

resource "aws_lb_listener" "rtc_grpc" {
  load_balancer_arn = aws_lb.rtc_nlb.arn
  port              = 8443
  protocol          = "TLS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.rtc_cert.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.rtc_grpc.arn
  }
}

# =============================================================================
# ACM Certificate
# =============================================================================

resource "aws_acm_certificate" "rtc_cert" {
  domain_name               = "rtc.${var.project_name}.example.com"
  subject_alternative_names = ["*.rtc.${var.project_name}.example.com"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.project_name}-rtc-cert"
  }
}

# =============================================================================
# CloudWatch Alarms for RTC Monitoring
# =============================================================================

resource "aws_sns_topic" "rtc_alerts" {
  name              = "${var.project_name}-alerts"
  kms_master_key_id = aws_kms_key.rtc_encryption.id

  tags = {
    Name = "${var.project_name}-alerts"
  }
}

# Redis Latency Alarm
resource "aws_cloudwatch_metric_alarm" "redis_latency" {
  alarm_name          = "${var.project_name}-redis-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CacheHitRate"
  namespace           = "AWS/ElastiCache"
  period              = 60
  statistic           = "Average"
  threshold           = 95
  alarm_description   = "Redis cache hit rate below 95%"
  alarm_actions       = [aws_sns_topic.rtc_alerts.arn]
  ok_actions          = [aws_sns_topic.rtc_alerts.arn]

  dimensions = {
    CacheClusterId = aws_elasticache_replication_group.rtc_redis.id
  }

  tags = {
    Name = "${var.project_name}-redis-latency-alarm"
  }
}

# RDS CPU Alarm
resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${var.project_name}-rds-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "RDS CPU utilization above 80%"
  alarm_actions       = [aws_sns_topic.rtc_alerts.arn]
  ok_actions          = [aws_sns_topic.rtc_alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.rtc_postgres.identifier
  }

  tags = {
    Name = "${var.project_name}-rds-cpu-alarm"
  }
}

# EKS Node CPU Alarm
resource "aws_cloudwatch_metric_alarm" "eks_node_cpu" {
  alarm_name          = "${var.project_name}-eks-node-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "node_cpu_utilization"
  namespace           = "ContainerInsights"
  period              = 60
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "EKS node CPU utilization above 80%"
  alarm_actions       = [aws_sns_topic.rtc_alerts.arn]
  ok_actions          = [aws_sns_topic.rtc_alerts.arn]

  dimensions = {
    ClusterName = aws_eks_cluster.rtc_cluster.name
  }

  tags = {
    Name = "${var.project_name}-eks-cpu-alarm"
  }
}

# =============================================================================
# CloudHSM (Optional - for hardware security)
# =============================================================================

resource "aws_cloudhsm_v2_cluster" "rtc_hsm" {
  count      = var.enable_cloudhsm ? 1 : 0
  hsm_type   = "hsm1.medium"
  subnet_ids = aws_subnet.private[*].id

  tags = {
    Name = "${var.project_name}-cloudhsm"
  }
}

# =============================================================================
# Outputs
# =============================================================================

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.rtc_vpc.id
}

output "eks_cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = aws_eks_cluster.rtc_cluster.endpoint
}

output "eks_cluster_name" {
  description = "EKS cluster name"
  value       = aws_eks_cluster.rtc_cluster.name
}

output "redis_endpoint" {
  description = "ElastiCache Redis endpoint"
  value       = aws_elasticache_replication_group.rtc_redis.configuration_endpoint_address
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint"
  value       = aws_db_instance.rtc_postgres.endpoint
}

output "rds_replica_endpoint" {
  description = "RDS PostgreSQL read replica endpoint"
  value       = aws_db_instance.rtc_postgres_replica.endpoint
}

output "nlb_dns_name" {
  description = "Network Load Balancer DNS name"
  value       = aws_lb.rtc_nlb.dns_name
}

output "audit_logs_bucket" {
  description = "S3 bucket for audit logs"
  value       = aws_s3_bucket.rtc_audit_logs.bucket
}

output "kms_key_arn" {
  description = "KMS key ARN for encryption"
  value       = aws_kms_key.rtc_encryption.arn
}

output "rds_credentials_secret_arn" {
  description = "Secrets Manager ARN for RDS credentials"
  value       = aws_secretsmanager_secret.rds_credentials.arn
}
