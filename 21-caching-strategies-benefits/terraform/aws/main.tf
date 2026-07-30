# Companion code for "The Backend of Luck" - Chapter 21, Caching Strategies and Benefits.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Chapter 38: Caching Strategies - AWS Infrastructure
# ElastiCache Redis Cluster for iGaming Platform
#
# Features:
# - Redis 8.x Cluster Mode with 3 shards
# - Multi-AZ deployment for high availability
# - Encryption at rest and in transit
# - Automatic failover
# - CloudWatch monitoring and alarms
#
# Cost Estimate: ~$1,200-1,800/month (3-shard cluster)

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }

  backend "s3" {
    bucket         = "igaming-terraform-state"
    key            = "chapter-38/caching/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    # S3-native state locking (Terraform >= 1.11, replaces DynamoDB locking)
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "igaming-platform"
      Chapter     = "38-caching"
      Environment = var.environment
      ManagedBy   = "terraform"
      CostCenter  = "infrastructure"
    }
  }
}

# -----------------------------------------------------------------------------
# Variables
# -----------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "project_name" {
  description = "Project name prefix"
  type        = string
  default     = "igaming-cache"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.38.0.0/16"
}

variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.r6g.large"
}

variable "redis_num_shards" {
  description = "Number of Redis shards (cluster mode)"
  type        = number
  default     = 3
}

variable "redis_replicas_per_shard" {
  description = "Number of replicas per shard"
  type        = number
  default     = 2
}

variable "enable_memcached" {
  description = "Enable Memcached cluster for static content"
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# VPC and Networking
# -----------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "${var.project_name}-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-igw"
  }
}

# Private subnets for ElastiCache (3 AZs)
resource "aws_subnet" "private" {
  count             = 3
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "${var.project_name}-private-${count.index + 1}"
    Tier = "private"
  }
}

# Public subnets for NAT Gateway
resource "aws_subnet" "public" {
  count                   = 3
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index + 8)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.project_name}-public-${count.index + 1}"
    Tier = "public"
  }
}

# Elastic IP for NAT Gateway
resource "aws_eip" "nat" {
  domain = "vpc"

  tags = {
    Name = "${var.project_name}-nat-eip"
  }

  depends_on = [aws_internet_gateway.main]
}

# NAT Gateway (single for cost optimization)
resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id

  tags = {
    Name = "${var.project_name}-nat"
  }
}

# Route tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-public-rt"
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-private-rt"
  }
}

resource "aws_route_table_association" "public" {
  count          = 3
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 3
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# -----------------------------------------------------------------------------
# Security Groups
# -----------------------------------------------------------------------------

# Redis security group
resource "aws_security_group" "redis" {
  name_prefix = "${var.project_name}-redis-"
  description = "Security group for Redis cluster"
  vpc_id      = aws_vpc.main.id

  # Redis port from VPC
  ingress {
    description = "Redis from VPC"
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Cluster bus port
  ingress {
    description = "Redis cluster bus from VPC"
    from_port   = 16379
    to_port     = 16379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = {
    Name = "${var.project_name}-redis-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Memcached security group
resource "aws_security_group" "memcached" {
  count       = var.enable_memcached ? 1 : 0
  name_prefix = "${var.project_name}-memcached-"
  description = "Security group for Memcached cluster"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Memcached from VPC"
    from_port   = 11211
    to_port     = 11211
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = {
    Name = "${var.project_name}-memcached-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# -----------------------------------------------------------------------------
# KMS Key for Encryption
# -----------------------------------------------------------------------------

resource "aws_kms_key" "cache" {
  description             = "KMS key for ElastiCache encryption"
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
        Sid    = "Allow ElastiCache"
        Effect = "Allow"
        Principal = {
          Service = "elasticache.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
          "kms:CreateGrant"
        ]
        Resource = "*"
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-kms"
  }
}

resource "aws_kms_alias" "cache" {
  name          = "alias/${var.project_name}-cache"
  target_key_id = aws_kms_key.cache.key_id
}

# -----------------------------------------------------------------------------
# ElastiCache Redis Cluster
# -----------------------------------------------------------------------------

# Subnet group
resource "aws_elasticache_subnet_group" "redis" {
  name       = "${var.project_name}-redis-subnet"
  subnet_ids = aws_subnet.private[*].id

  tags = {
    Name = "${var.project_name}-redis-subnet-group"
  }
}

# Parameter group for Redis 8
resource "aws_elasticache_parameter_group" "redis" {
  family = "redis7"
  name   = "${var.project_name}-redis-params"

  # Performance optimizations for iGaming
  parameter {
    name  = "maxmemory-policy"
    value = "volatile-lru"
  }

  parameter {
    name  = "timeout"
    value = "300"
  }

  parameter {
    name  = "tcp-keepalive"
    value = "300"
  }

  parameter {
    name  = "notify-keyspace-events"
    value = "Ex"
  }

  # Cluster mode settings
  parameter {
    name  = "cluster-enabled"
    value = "yes"
  }

  tags = {
    Name = "${var.project_name}-redis-params"
  }
}

# Auth token for Redis
resource "random_password" "redis_auth" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "redis_auth" {
  name                    = "${var.project_name}/redis/auth-token"
  description             = "Redis AUTH token"
  recovery_window_in_days = 7
  kms_key_id              = aws_kms_key.cache.arn

  tags = {
    Name = "${var.project_name}-redis-auth"
  }
}

resource "aws_secretsmanager_secret_version" "redis_auth" {
  secret_id = aws_secretsmanager_secret.redis_auth.id
  secret_string = jsonencode({
    auth_token = random_password.redis_auth.result
  })
}

# Redis Cluster (Cluster Mode Enabled)
resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${var.project_name}-redis"
  description          = "Redis cluster for iGaming caching"

  # Node configuration
  node_type               = var.redis_node_type
  num_node_groups         = var.redis_num_shards
  replicas_per_node_group = var.redis_replicas_per_shard

  # Engine
  engine         = "redis"
  engine_version = "8.0"
  port           = 6379

  # Cluster mode
  parameter_group_name = aws_elasticache_parameter_group.redis.name

  # Networking
  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  # Security
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = aws_kms_key.cache.arn
  auth_token                 = random_password.redis_auth.result

  # High availability
  automatic_failover_enabled = true
  multi_az_enabled           = true
  auto_minor_version_upgrade = true

  # Maintenance
  maintenance_window       = "sun:05:00-sun:06:00"
  snapshot_retention_limit = 7
  snapshot_window          = "03:00-04:00"

  # Notifications
  notification_topic_arn = aws_sns_topic.cache_alerts.arn

  tags = {
    Name = "${var.project_name}-redis-cluster"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# -----------------------------------------------------------------------------
# Memcached Cluster (Optional - for static content)
# -----------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "memcached" {
  count      = var.enable_memcached ? 1 : 0
  name       = "${var.project_name}-memcached-subnet"
  subnet_ids = aws_subnet.private[*].id

  tags = {
    Name = "${var.project_name}-memcached-subnet-group"
  }
}

resource "aws_elasticache_parameter_group" "memcached" {
  count  = var.enable_memcached ? 1 : 0
  family = "memcached1.6"
  name   = "${var.project_name}-memcached-params"

  parameter {
    name  = "max_item_size"
    value = "10485760" # 10MB
  }

  tags = {
    Name = "${var.project_name}-memcached-params"
  }
}

resource "aws_elasticache_cluster" "memcached" {
  count                = var.enable_memcached ? 1 : 0
  cluster_id           = "${var.project_name}-memcached"
  engine               = "memcached"
  engine_version       = "1.6.22"
  node_type            = "cache.r6g.large"
  num_cache_nodes      = 3
  port                 = 11211
  az_mode              = "cross-az"
  parameter_group_name = aws_elasticache_parameter_group.memcached[0].name
  subnet_group_name    = aws_elasticache_subnet_group.memcached[0].name
  security_group_ids   = [aws_security_group.memcached[0].id]

  maintenance_window = "sun:06:00-sun:07:00"

  notification_topic_arn = aws_sns_topic.cache_alerts.arn

  tags = {
    Name = "${var.project_name}-memcached-cluster"
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Alarms
# -----------------------------------------------------------------------------

resource "aws_sns_topic" "cache_alerts" {
  name              = "${var.project_name}-cache-alerts"
  kms_master_key_id = aws_kms_key.cache.id

  tags = {
    Name = "${var.project_name}-cache-alerts"
  }
}

# Redis CPU utilization alarm
resource "aws_cloudwatch_metric_alarm" "redis_cpu" {
  alarm_name          = "${var.project_name}-redis-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "EngineCPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 75
  alarm_description   = "Redis CPU utilization above 75%"
  alarm_actions       = [aws_sns_topic.cache_alerts.arn]
  ok_actions          = [aws_sns_topic.cache_alerts.arn]

  dimensions = {
    CacheClusterId = aws_elasticache_replication_group.redis.id
  }

  tags = {
    Name = "${var.project_name}-redis-cpu-alarm"
  }
}

# Redis memory utilization alarm
resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "${var.project_name}-redis-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "Redis memory usage above 80%"
  alarm_actions       = [aws_sns_topic.cache_alerts.arn]
  ok_actions          = [aws_sns_topic.cache_alerts.arn]

  dimensions = {
    CacheClusterId = aws_elasticache_replication_group.redis.id
  }

  tags = {
    Name = "${var.project_name}-redis-memory-alarm"
  }
}

# Redis evictions alarm
resource "aws_cloudwatch_metric_alarm" "redis_evictions" {
  alarm_name          = "${var.project_name}-redis-evictions-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Evictions"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Sum"
  threshold           = 1000
  alarm_description   = "High eviction rate - consider scaling"
  alarm_actions       = [aws_sns_topic.cache_alerts.arn]

  dimensions = {
    CacheClusterId = aws_elasticache_replication_group.redis.id
  }

  tags = {
    Name = "${var.project_name}-redis-evictions-alarm"
  }
}

# Cache hit ratio alarm
resource "aws_cloudwatch_metric_alarm" "redis_hit_rate" {
  alarm_name          = "${var.project_name}-redis-hit-rate-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CacheHitRate"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 0.80
  alarm_description   = "Cache hit rate below 80%"
  alarm_actions       = [aws_sns_topic.cache_alerts.arn]

  dimensions = {
    CacheClusterId = aws_elasticache_replication_group.redis.id
  }

  tags = {
    Name = "${var.project_name}-redis-hit-rate-alarm"
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Dashboard
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "cache" {
  dashboard_name = "${var.project_name}-cache-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Cache Hit Rate"
          region = var.aws_region
          metrics = [
            ["AWS/ElastiCache", "CacheHitRate", "CacheClusterId", aws_elasticache_replication_group.redis.id]
          ]
          period = 300
          stat   = "Average"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "CPU Utilization"
          region = var.aws_region
          metrics = [
            ["AWS/ElastiCache", "EngineCPUUtilization", "CacheClusterId", aws_elasticache_replication_group.redis.id]
          ]
          period = 300
          stat   = "Average"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Memory Usage"
          region = var.aws_region
          metrics = [
            ["AWS/ElastiCache", "DatabaseMemoryUsagePercentage", "CacheClusterId", aws_elasticache_replication_group.redis.id]
          ]
          period = 300
          stat   = "Average"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Evictions"
          region = var.aws_region
          metrics = [
            ["AWS/ElastiCache", "Evictions", "CacheClusterId", aws_elasticache_replication_group.redis.id]
          ]
          period = 300
          stat   = "Sum"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          title  = "Current Connections"
          region = var.aws_region
          metrics = [
            ["AWS/ElastiCache", "CurrConnections", "CacheClusterId", aws_elasticache_replication_group.redis.id]
          ]
          period = 60
          stat   = "Average"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          title  = "Operations Per Second"
          region = var.aws_region
          metrics = [
            ["AWS/ElastiCache", "GetTypeCmds", "CacheClusterId", aws_elasticache_replication_group.redis.id],
            [".", "SetTypeCmds", ".", "."]
          ]
          period = 60
          stat   = "Sum"
        }
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "redis_cluster_endpoint" {
  description = "Redis cluster configuration endpoint"
  value       = aws_elasticache_replication_group.redis.configuration_endpoint_address
}

output "redis_cluster_port" {
  description = "Redis cluster port"
  value       = aws_elasticache_replication_group.redis.port
}

output "redis_auth_secret_arn" {
  description = "ARN of the Redis auth token secret"
  value       = aws_secretsmanager_secret.redis_auth.arn
}

output "memcached_endpoint" {
  description = "Memcached cluster endpoint"
  value       = var.enable_memcached ? aws_elasticache_cluster.memcached[0].configuration_endpoint : null
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = aws_subnet.private[*].id
}

output "redis_security_group_id" {
  description = "Redis security group ID"
  value       = aws_security_group.redis.id
}

output "kms_key_arn" {
  description = "KMS key ARN for encryption"
  value       = aws_kms_key.cache.arn
}

output "sns_topic_arn" {
  description = "SNS topic ARN for alerts"
  value       = aws_sns_topic.cache_alerts.arn
}

output "dashboard_url" {
  description = "CloudWatch dashboard URL"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.cache.dashboard_name}"
}
