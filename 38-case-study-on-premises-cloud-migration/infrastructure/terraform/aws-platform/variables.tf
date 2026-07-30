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
# Variables — AWS iGaming Casino Platform
# =============================================================================
# All configurable parameters for the casino platform infrastructure.
# Defaults are tuned for a small-to-mid-size operator running in NJ-regulated
# market (us-east-1).  Override via terraform.tfvars or -var flags.
# =============================================================================

# --- General -----------------------------------------------------------------

variable "project_name" {
  description = "Project name prefix for all AWS resources"
  type        = string
  default     = "acmetocasino"
}

variable "environment" {
  description = "Deployment environment (prod, staging, dev)"
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["prod", "staging", "dev"], var.environment)
    error_message = "Environment must be prod, staging, or dev."
  }
}

variable "aws_region" {
  description = "AWS region — us-east-1 for NJ/PA, us-east-2 for MI"
  type        = string
  default     = "us-east-1"
}

variable "tags" {
  description = "Common tags applied to every resource for cost allocation"
  type        = map(string)
  default = {
    Project     = "acmetocasino"
    ManagedBy   = "terraform"
    Compliance  = "NJ-DGE,PA-PGCB,PCI-DSS"
    Environment = "prod"
  }
}

# --- Networking --------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the platform VPC"
  type        = string
  default     = "10.20.0.0/16"
}

variable "availability_zones" {
  description = "List of AZs — minimum 3 for regulatory multi-AZ requirement"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (ALB, NAT GW)"
  type        = list(string)
  default     = ["10.20.1.0/24", "10.20.2.0/24", "10.20.3.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (ECS, Lambda)"
  type        = list(string)
  default     = ["10.20.10.0/24", "10.20.11.0/24", "10.20.12.0/24"]
}

variable "data_subnet_cidrs" {
  description = "CIDR blocks for data subnets (RDS, ElastiCache)"
  type        = list(string)
  default     = ["10.20.20.0/24", "10.20.21.0/24", "10.20.22.0/24"]
}

variable "enable_vpc_flow_logs" {
  description = "Enable VPC Flow Logs — required by NJ DGE for network audit trail"
  type        = bool
  default     = true
}

# --- ECS / Fargate -----------------------------------------------------------

variable "api_image_tag" {
  description = "Docker image tag for the casino API (overridden by CI/CD)"
  type        = string
  default     = "latest"
}

variable "api_cpu" {
  description = "Fargate vCPU units (256 = 0.25 vCPU)"
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "Fargate memory in MiB"
  type        = number
  default     = 1024
}

variable "api_desired_count" {
  description = "Desired number of API task replicas"
  type        = number
  default     = 2
}

variable "api_min_count" {
  description = "Minimum tasks for auto-scaling"
  type        = number
  default     = 2
}

variable "api_max_count" {
  description = "Maximum tasks for auto-scaling"
  type        = number
  default     = 10
}

variable "api_port" {
  description = "Container port for the FastAPI application"
  type        = number
  default     = 8000
}

# --- RDS PostgreSQL ----------------------------------------------------------

variable "db_instance_class" {
  description = "RDS instance class — db.t4g.medium is the minimum for production"
  type        = string
  default     = "db.t4g.medium"
}

variable "db_allocated_storage" {
  description = "Initial storage in GB"
  type        = number
  default     = 50
}

variable "db_max_allocated_storage" {
  description = "Maximum auto-scaled storage in GB"
  type        = number
  default     = 500
}

variable "db_name" {
  description = "Name of the default database"
  type        = string
  default     = "casino_platform"
}

variable "db_username" {
  description = "Master database username"
  type        = string
  default     = "casino_admin"
}

variable "db_backup_retention_days" {
  description = "Backup retention in days — 2555 = ~7 years (NJ DGE requirement)"
  type        = number
  default     = 35
  # NOTE: AWS RDS max automated backup retention is 35 days.
  # For 7-year retention, use AWS Backup with a vault lock policy.
  # See aws_backup_plan resource in rds.tf for the full 7-year strategy.
}

variable "db_backup_7yr_retention_days" {
  description = "Long-term backup retention via AWS Backup (7 years = 2555 days)"
  type        = number
  default     = 2555
}

variable "db_deletion_protection" {
  description = "Prevent accidental deletion of the database"
  type        = bool
  default     = true
}

# --- ElastiCache Redis -------------------------------------------------------

variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t4g.medium"
}

variable "redis_num_cache_clusters" {
  description = "Number of cache nodes (multi-AZ requires >= 2)"
  type        = number
  default     = 2
}

variable "redis_engine_version" {
  description = "Redis engine version"
  type        = string
  default     = "7.1"
}

# --- ALB / HTTPS -------------------------------------------------------------

variable "domain_name" {
  description = "Primary domain for the casino platform"
  type        = string
  default     = "api.acmetocasino.com"
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS termination"
  type        = string
  default     = ""
}

variable "health_check_path" {
  description = "ALB health check path"
  type        = string
  default     = "/health"
}

# --- CI/CD -------------------------------------------------------------------

variable "github_repo" {
  description = "GitHub repository in owner/repo format"
  type        = string
  default     = "acmetocasino/casino-platform"
}

variable "github_branch" {
  description = "Branch that triggers production deploys"
  type        = string
  default     = "main"
}

variable "codestar_connection_arn" {
  description = "CodeStar connection ARN for GitHub integration"
  type        = string
  default     = ""
}

# --- Monitoring / Alarms ----------------------------------------------------

variable "alarm_sns_email" {
  description = "Email for CloudWatch alarm notifications"
  type        = string
  default     = "ops@acmetocasino.com"
}

variable "api_latency_threshold_ms" {
  description = "P99 latency threshold in milliseconds before alarm triggers"
  type        = number
  default     = 500
}

variable "api_error_rate_threshold" {
  description = "5xx error rate percentage threshold"
  type        = number
  default     = 1
}

variable "db_connections_threshold" {
  description = "Maximum DB connections before alarm triggers"
  type        = number
  default     = 80
}
