# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Chapter 46: Brazilian Betting Platform -- AWS Infrastructure (sa-east-1)
# =============================================================================
# Provisions production-grade AWS infrastructure for a Lei 14.790/2023
# compliant betting platform in Brazil, including:
#
#   - VPC with public / private / database subnet tiers
#   - EKS cluster (API microservices)
#   - RDS PostgreSQL Multi-AZ with encryption
#   - ElastiCache Redis (cluster mode)
#   - MSK (Managed Kafka) for SIGAP event streaming
#   - WAF + AWS Shield (Standard; enable Advanced separately)
#   - CloudFront distribution (.bet.br origin)
#   - S3 for KYC document storage (SSE-KMS, lifecycle)
#   - Route53 hosted zone (bet.br subdomain)
#   - IAM roles with least-privilege policies
#
# Compliance tags: ISO 27001, SOC2 Type II, LGPD, Lei 14.790/2023
#
# Estimated monthly cost: ~USD 3,500-6,500 (varies with traffic)
# =============================================================================

terraform {
  required_version = "~> 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "s3" {
    bucket       = "betbr-terraform-state"
    key          = "production/main.tfstate"
    region       = "sa-east-1"
    encrypt      = true
    # S3-native state locking (Terraform >= 1.11, replaces DynamoDB locking)
    use_lockfile = true
  }
}

# =============================================================================
# Providers
# =============================================================================

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project          = var.project_name
      Environment      = var.environment
      ManagedBy        = "terraform"
      Compliance       = "lei-14790-lgpd-iso27001"
      DataClassification = "restricted"
      CostCenter       = "tech-infrastructure"
    }
  }
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"  # Required for CloudFront WAF

  default_tags {
    tags = {
      Project    = var.project_name
      ManagedBy  = "terraform"
    }
  }
}

# =============================================================================
# Variables
# =============================================================================

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
  validation {
    condition     = contains(["production", "staging", "development"], var.environment)
    error_message = "Must be production, staging, or development."
  }
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "betbr"
}

variable "aws_region" {
  description = "Primary AWS region"
  type        = string
  default     = "sa-east-1"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.46.0.0/16"
}

variable "eks_node_instance_type" {
  description = "EC2 instance type for EKS worker nodes"
  type        = string
  default     = "t3.xlarge"
}

variable "eks_desired_nodes" {
  description = "Desired number of EKS worker nodes"
  type        = number
  default     = 3
}

variable "rds_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.r6g.large"
}

variable "elasticache_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.r6g.large"
}

variable "domain_name" {
  description = "Primary domain (must be pre-registered .bet.br)"
  type        = string
  default     = "apostas.acmetocasino.bet.br"
}

variable "enable_shield_advanced" {
  description = "Enable AWS Shield Advanced (USD ~3000/month)"
  type        = bool
  default     = false
}

variable "kyc_document_retention_days" {
  description = "KYC document S3 retention period (days). LGPD minimum 5 years."
  type        = number
  default     = 1825  # 5 years
}

# =============================================================================
# Locals
# =============================================================================

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  azs = ["${var.aws_region}a", "${var.aws_region}b", "${var.aws_region}c"]

  public_subnets   = ["10.46.0.0/24", "10.46.1.0/24", "10.46.2.0/24"]
  private_subnets  = ["10.46.10.0/24", "10.46.11.0/24", "10.46.12.0/24"]
  database_subnets = ["10.46.20.0/24", "10.46.21.0/24", "10.46.22.0/24"]

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# =============================================================================
# KMS Keys
# =============================================================================

resource "aws_kms_key" "main" {
  description              = "${local.name_prefix} - primary encryption key"
  deletion_window_in_days  = 30
  enable_key_rotation      = true
  multi_region             = false
}

resource "aws_kms_alias" "main" {
  name          = "alias/${local.name_prefix}-main"
  target_key_id = aws_kms_key.main.key_id
}

resource "aws_kms_key" "kyc" {
  description             = "${local.name_prefix} - KYC documents encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_kms_alias" "kyc" {
  name          = "alias/${local.name_prefix}-kyc"
  target_key_id = aws_kms_key.kyc.key_id
}

# =============================================================================
# VPC
# =============================================================================

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${local.name_prefix}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name_prefix}-igw" }
}

# Public subnets
resource "aws_subnet" "public" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.public_subnets[count.index]
  availability_zone = local.azs[count.index]

  map_public_ip_on_launch = false  # no auto-assign; use NAT/ELB

  tags = {
    Name                     = "${local.name_prefix}-public-${local.azs[count.index]}"
    "kubernetes.io/role/elb" = "1"
  }
}

# Private subnets (EKS workers, Lambda)
resource "aws_subnet" "private" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.private_subnets[count.index]
  availability_zone = local.azs[count.index]

  tags = {
    Name                              = "${local.name_prefix}-private-${local.azs[count.index]}"
    "kubernetes.io/role/internal-elb" = "1"
  }
}

# Database subnets (RDS, ElastiCache, MSK)
resource "aws_subnet" "database" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.database_subnets[count.index]
  availability_zone = local.azs[count.index]

  tags = {
    Name = "${local.name_prefix}-database-${local.azs[count.index]}"
  }
}

# NAT Gateways (one per AZ for HA)
resource "aws_eip" "nat" {
  count  = length(local.azs)
  domain = "vpc"
  tags   = { Name = "${local.name_prefix}-nat-eip-${count.index}" }
}

resource "aws_nat_gateway" "main" {
  count         = length(local.azs)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = { Name = "${local.name_prefix}-nat-${count.index}" }
  depends_on    = [aws_internet_gateway.main]
}

# Route tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name_prefix}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = length(local.azs)
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }
  tags = { Name = "${local.name_prefix}-private-rt-${count.index}" }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# =============================================================================
# Security Groups
# =============================================================================

resource "aws_security_group" "eks_nodes" {
  name        = "${local.name_prefix}-eks-nodes-sg"
  description = "EKS worker nodes"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name_prefix}-eks-nodes-sg" }
}

resource "aws_security_group" "rds" {
  name        = "${local.name_prefix}-rds-sg"
  description = "RDS PostgreSQL access from EKS only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }

  tags = { Name = "${local.name_prefix}-rds-sg" }
}

resource "aws_security_group" "redis" {
  name        = "${local.name_prefix}-redis-sg"
  description = "Redis access from EKS only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }

  tags = { Name = "${local.name_prefix}-redis-sg" }
}

resource "aws_security_group" "msk" {
  name        = "${local.name_prefix}-msk-sg"
  description = "MSK Kafka access from EKS only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 9092
    to_port         = 9092
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }

  ingress {
    from_port       = 9094
    to_port         = 9094
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }

  tags = { Name = "${local.name_prefix}-msk-sg" }
}

# =============================================================================
# EKS Cluster
# =============================================================================

resource "aws_iam_role" "eks_cluster" {
  name = "${local.name_prefix}-eks-cluster-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_eks_cluster" "main" {
  name     = "${local.name_prefix}-eks"
  version  = "1.30"
  role_arn = aws_iam_role.eks_cluster.arn

  vpc_config {
    subnet_ids              = concat(aws_subnet.private[*].id, aws_subnet.public[*].id)
    endpoint_private_access = true
    endpoint_public_access  = true    # restrict to corporate IPs in production
    security_group_ids      = [aws_security_group.eks_nodes.id]
  }

  encryption_config {
    resources = ["secrets"]
    provider {
      key_arn = aws_kms_key.main.arn
    }
  }

  enabled_cluster_log_types = [
    "api", "audit", "authenticator", "controllerManager", "scheduler"
  ]

  depends_on = [aws_iam_role_policy_attachment.eks_cluster_policy]
}

resource "aws_iam_role" "eks_node_group" {
  name = "${local.name_prefix}-eks-node-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_worker_node_policy" {
  role       = aws_iam_role.eks_node_group.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "eks_cni_policy" {
  role       = aws_iam_role.eks_node_group.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "eks_container_registry" {
  role       = aws_iam_role.eks_node_group.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${local.name_prefix}-ng"
  node_role_arn   = aws_iam_role.eks_node_group.arn
  subnet_ids      = aws_subnet.private[*].id
  instance_types  = [var.eks_node_instance_type]

  scaling_config {
    desired_size = var.eks_desired_nodes
    min_size     = 2
    max_size     = 10
  }

  update_config {
    max_unavailable_percentage = 25
  }

  labels = {
    role = "application"
  }

  launch_template {
    name    = aws_launch_template.eks_nodes.name
    version = aws_launch_template.eks_nodes.latest_version
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry,
  ]
}

resource "aws_launch_template" "eks_nodes" {
  name_prefix   = "${local.name_prefix}-eks-"
  instance_type = var.eks_node_instance_type

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 50
      volume_type           = "gp3"
      encrypted             = true
      kms_key_id            = aws_kms_key.main.arn
      delete_on_termination = true
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"  # IMDSv2 only
    http_put_response_hop_limit = 1
  }

  monitoring { enabled = true }
}

# =============================================================================
# RDS PostgreSQL (Multi-AZ, Encrypted)
# =============================================================================

resource "aws_db_subnet_group" "main" {
  name       = "${local.name_prefix}-db-subnet-group"
  subnet_ids = aws_subnet.database[*].id
}

resource "random_password" "rds" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_secretsmanager_secret" "rds_password" {
  name                    = "/${local.name_prefix}/rds/master-password"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "rds_password" {
  secret_id     = aws_secretsmanager_secret.rds_password.id
  secret_string = random_password.rds.result
}

resource "aws_db_parameter_group" "postgres16" {
  name   = "${local.name_prefix}-pg16"
  family = "postgres16"

  parameter {
    name  = "log_connections"
    value = "1"
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
  }

  parameter {
    name  = "log_statement"
    value = "ddl"
  }

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "main" {
  identifier        = "${local.name_prefix}-postgres"
  engine            = "postgres"
  engine_version    = "16.2"
  instance_class    = var.rds_instance_class
  allocated_storage = 100
  max_allocated_storage = 1000
  storage_type      = "gp3"
  storage_encrypted = true
  kms_key_id        = aws_kms_key.main.arn

  db_name  = "betbr"
  username = "betbr_master"
  password = random_password.rds.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.postgres16.name

  multi_az               = true
  publicly_accessible    = false
  deletion_protection    = true
  skip_final_snapshot    = false
  final_snapshot_identifier = "${local.name_prefix}-final-snapshot"

  backup_retention_period = 35   # 35 days retention per Lei 14.790/2023
  backup_window           = "03:00-04:00"
  maintenance_window      = "sun:04:30-sun:05:30"

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.main.arn
  performance_insights_retention_period = 731  # 2 years
}

# =============================================================================
# ElastiCache Redis (Cluster Mode Enabled)
# =============================================================================

resource "aws_elasticache_subnet_group" "main" {
  name       = "${local.name_prefix}-redis-subnet-group"
  subnet_ids = aws_subnet.database[*].id
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${local.name_prefix}-redis"
  description          = "BetBR Redis cluster"

  node_type            = var.elasticache_node_type
  port                 = 6379
  parameter_group_name = "default.redis7.cluster.on"

  num_cache_clusters         = 3
  automatic_failover_enabled = true
  multi_az_enabled           = true

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = aws_kms_key.main.arn

  auth_token = random_password.redis_auth.result

  log_delivery_configuration {
    destination      = aws_cloudwatch_log_group.redis.name
    destination_type = "cloudwatch-logs"
    log_format       = "json"
    log_type         = "slow-log"
  }
}

resource "random_password" "redis_auth" {
  length  = 32
  special = false
}

resource "aws_cloudwatch_log_group" "redis" {
  name              = "/aws/elasticache/${local.name_prefix}"
  retention_in_days = 90
  kms_key_id        = aws_kms_key.main.arn
}

# =============================================================================
# MSK (Managed Kafka) -- SIGAP event streaming
# =============================================================================

resource "aws_msk_cluster" "main" {
  cluster_name           = "${local.name_prefix}-kafka"
  kafka_version          = "3.6.0"
  number_of_broker_nodes = 3

  broker_node_group_info {
    instance_type  = "kafka.m5.large"
    client_subnets = aws_subnet.database[*].id
    storage_info {
      ebs_storage_info {
        volume_size            = 200
        provisioned_throughput {
          enabled           = true
          volume_throughput = 250
        }
      }
    }
    security_groups = [aws_security_group.msk.id]
  }

  encryption_info {
    encryption_at_rest_kms_key_arn = aws_kms_key.main.arn
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  open_monitoring {
    prometheus {
      jmx_exporter  { enabled_in_broker = true }
      node_exporter { enabled_in_broker = true }
    }
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = "/aws/msk/${local.name_prefix}"
      }
    }
  }
}

resource "aws_cloudwatch_log_group" "msk" {
  name              = "/aws/msk/${local.name_prefix}"
  retention_in_days = 90
  kms_key_id        = aws_kms_key.main.arn
}

# =============================================================================
# S3 -- KYC Document Storage
# =============================================================================

resource "aws_s3_bucket" "kyc_documents" {
  bucket = "${local.name_prefix}-kyc-documents-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "kyc" {
  bucket = aws_s3_bucket.kyc_documents.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "kyc" {
  bucket = aws_s3_bucket.kyc_documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.kyc.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "kyc" {
  bucket                  = aws_s3_bucket.kyc_documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "kyc" {
  bucket = aws_s3_bucket.kyc_documents.id
  rule {
    id     = "lgpd-retention"
    status = "Enabled"
    expiration { days = var.kyc_document_retention_days }
    noncurrent_version_expiration { noncurrent_days = 90 }
  }
}

resource "aws_s3_bucket_logging" "kyc" {
  bucket        = aws_s3_bucket.kyc_documents.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "kyc-access-logs/"
}

resource "aws_s3_bucket" "access_logs" {
  bucket = "${local.name_prefix}-access-logs-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket                  = aws_s3_bucket.access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# =============================================================================
# WAF -- Web Application Firewall (CloudFront scope in us-east-1)
# =============================================================================

resource "aws_wafv2_web_acl" "cloudfront" {
  provider    = aws.us_east_1
  name        = "${local.name_prefix}-waf-cf"
  description = "BetBR CloudFront WAF"
  scope       = "CLOUDFRONT"

  default_action { allow {} }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 1
    override_action { none {} }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CommonRuleSetMetric"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 2
    override_action { none {} }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "KnownBadInputsMetric"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "RateLimitPerIP"
    priority = 10
    action { block {} }
    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "RateLimitMetric"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name_prefix}-waf"
    sampled_requests_enabled   = true
  }
}

# =============================================================================
# CloudFront Distribution
# =============================================================================

resource "aws_cloudfront_distribution" "main" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${local.name_prefix} - Brazilian betting platform"
  default_root_object = "index.html"
  aliases             = [var.domain_name]
  price_class         = "PriceClass_200"  # US, EU, SA edge locations
  web_acl_id          = aws_wafv2_web_acl.cloudfront.arn

  origin {
    domain_name = "api.${var.domain_name}"
    origin_id   = "api"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    allowed_methods  = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods   = ["GET", "HEAD", "OPTIONS"]
    target_origin_id = "api"

    forwarded_values {
      query_string = true
      cookies { forward = "none" }
      headers = ["Accept", "Authorization", "Content-Type", "Origin"]
    }

    viewer_protocol_policy = "redirect-to-https"
    min_ttl                = 0
    default_ttl            = 0
    max_ttl                = 86400
    compress               = true
  }

  # Static assets cache
  ordered_cache_behavior {
    path_pattern     = "/static/*"
    allowed_methods  = ["GET", "HEAD"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "api"

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    viewer_protocol_policy = "redirect-to-https"
    min_ttl                = 86400
    default_ttl            = 604800
    max_ttl                = 31536000
    compress               = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"  # Geo handled at app layer (Lei 14.790/2023)
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate.main.arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  logging_config {
    include_cookies = false
    bucket          = aws_s3_bucket.access_logs.bucket_domain_name
    prefix          = "cloudfront/"
  }
}

resource "aws_acm_certificate" "main" {
  provider          = aws.us_east_1
  domain_name       = var.domain_name
  validation_method = "DNS"

  subject_alternative_names = [
    "*.${var.domain_name}",
  ]

  lifecycle { create_before_destroy = true }
}

# =============================================================================
# Data Sources
# =============================================================================

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# =============================================================================
# Outputs
# =============================================================================

output "eks_cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = aws_eks_cluster.main.endpoint
  sensitive   = false
}

output "eks_cluster_name" {
  description = "EKS cluster name"
  value       = aws_eks_cluster.main.name
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint"
  value       = aws_db_instance.main.endpoint
  sensitive   = true
}

output "redis_endpoint" {
  description = "ElastiCache Redis primary endpoint"
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
  sensitive   = true
}

output "msk_bootstrap_brokers_tls" {
  description = "MSK TLS bootstrap brokers"
  value       = aws_msk_cluster.main.bootstrap_brokers_tls
  sensitive   = true
}

output "kyc_s3_bucket" {
  description = "S3 bucket for KYC documents"
  value       = aws_s3_bucket.kyc_documents.bucket
}

output "cloudfront_domain" {
  description = "CloudFront distribution domain"
  value       = aws_cloudfront_distribution.main.domain_name
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

# =============================================================================
# Note: Sports Engine Worker (Cloudflare — not AWS)
# =============================================================================
#
# The live sports data layer is deployed as a separate Cloudflare Worker and
# is NOT provisioned by this Terraform module (which targets AWS sa-east-1).
#
# Worker:     betbr-sports-engine
# Cron:       */15 * * * *  (polls API-Football every 15 minutes)
# KV:         SPORTS_DATA  (id: REPLACE_WITH_ACTUAL_SPORTS_DATA_KV_ID)
# Route:      sports-api.cloud-acmetocasino.com/*
# Config:     writing/new-book/scripts/chapter-46/
#               brazilian-betting-platform/sports-engine/wrangler.toml
# Deploy:     new-platform/scripts/deploy-sports-engine.sh
#
# DNS prerequisite: an A record for sports-api.cloud-acmetocasino.com must
# exist in the cloud-acmetocasino.com Cloudflare zone (proxied) before the
# Worker route resolves. The Cloudflare API token requires dns_records:edit
# scope on that zone.
# =============================================================================
