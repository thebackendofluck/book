# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Project Configuration
variable "project_name" {
  description = "Name of the project"
  type        = string
  default     = "yubihsm"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "owner" {
  description = "Owner of the infrastructure"
  type        = string
  default     = "security-team"
}

variable "cost_center" {
  description = "Cost center for billing"
  type        = string
  default     = "security"
}

# AWS Configuration
variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "us-east-1"
}

variable "availability_zones" {
  description = "List of availability zones"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

# Networking
variable "vpc_cidr_block" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets"
  type        = list(string)
  default     = ["10.0.10.0/24", "10.0.11.0/24", "10.0.12.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets"
  type        = list(string)
  default     = ["10.0.20.0/24", "10.0.21.0/24", "10.0.22.0/24"]
}

variable "database_subnet_cidrs" {
  description = "CIDR blocks for database subnets"
  type        = list(string)
  default     = ["10.0.30.0/24", "10.0.31.0/24", "10.0.32.0/24"]
}

variable "enable_nat_gateway" {
  description = "Enable NAT Gateway for private subnets"
  type        = bool
  default     = true
}

variable "single_nat_gateway" {
  description = "Use single NAT Gateway instead of one per AZ"
  type        = bool
  default     = false
}

# YubiHSM Configuration
variable "yubihsm_auth_key_id" {
  description = "YubiHSM authentication key ID"
  type        = number
  default     = 1
  sensitive   = true
}

variable "yubihsm_password" {
  description = "YubiHSM authentication password"
  type        = string
  sensitive   = true
}

# Security Configuration
variable "enable_key_rotation" {
  description = "Enable automatic KMS key rotation"
  type        = bool
  default     = true
}

variable "key_rotation_days" {
  description = "Days between key rotations"
  type        = number
  default     = 90
}

# Compute Configuration
variable "app_server_instance_type" {
  description = "EC2 instance type for application servers"
  type        = string
  default     = "t3.medium"
}

variable "nitro_enclave_instance_type" {
  description = "EC2 instance type for Nitro Enclave"
  type        = string
  default     = "c5.xlarge"
}

variable "app_server_ami_id" {
  description = "AMI ID for application servers"
  type        = string
  default     = "" # Will use latest Amazon Linux 2 if not specified
}

variable "nitro_enclave_ami_id" {
  description = "AMI ID for Nitro Enclave instances"
  type        = string
  default     = "" # Will use latest Amazon Linux 2 if not specified
}

variable "key_pair_name" {
  description = "SSH key pair name for EC2 instances"
  type        = string
  default     = ""
}

# Storage Configuration
variable "ebs_volume_size" {
  description = "Size of EBS volumes in GB"
  type        = number
  default     = 100
}

variable "ebs_encrypted" {
  description = "Enable EBS encryption"
  type        = bool
  default     = true
}

variable "enable_efs" {
  description = "Enable EFS file system"
  type        = bool
  default     = true
}

variable "efs_encrypted" {
  description = "Enable EFS encryption"
  type        = bool
  default     = true
}

variable "s3_bucket_name" {
  description = "Name of S3 bucket for backups and configuration"
  type        = string
  default     = ""
}

variable "s3_versioning" {
  description = "Enable S3 versioning"
  type        = bool
  default     = true
}

# Database Configuration
variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.medium"
}

variable "db_engine_version" {
  description = "PostgreSQL engine version"
  type        = string
  default     = "16.1"
}

variable "db_allocated_storage" {
  description = "Initial allocated storage in GB"
  type        = number
  default     = 100
}

variable "db_max_allocated_storage" {
  description = "Maximum allocated storage in GB (for autoscaling)"
  type        = number
  default     = 1000
}

variable "db_name" {
  description = "Database name"
  type        = string
  default     = "yubihsm_db"
}

variable "db_username" {
  description = "Database username"
  type        = string
  default     = "yubihsm_admin"
}

variable "db_password" {
  description = "Database password"
  type        = string
  sensitive   = true
}

variable "db_multi_az" {
  description = "Enable Multi-AZ deployment"
  type        = bool
  default     = true
}

variable "db_backup_retention_period" {
  description = "Backup retention period in days"
  type        = number
  default     = 30
}

variable "enable_tde" {
  description = "Enable Transparent Data Encryption"
  type        = bool
  default     = true
}

# Container Configuration
variable "ecs_cluster_name" {
  description = "Name of ECS cluster"
  type        = string
  default     = "yubihsm-cluster"
}

variable "vaultwarden_image" {
  description = "Vaultwarden Docker image"
  type        = string
  default     = "vaultwarden/server:latest"
}

variable "vaultwarden_cpu" {
  description = "CPU units for Vaultwarden container"
  type        = number
  default     = 512
}

variable "vaultwarden_memory" {
  description = "Memory for Vaultwarden container in MB"
  type        = number
  default     = 1024
}

variable "yubihsm_connector_image" {
  description = "YubiHSM Connector Docker image"
  type        = string
  default     = "yubico/yubihsm-connector:latest"
}

variable "yubihsm_connector_cpu" {
  description = "CPU units for YubiHSM Connector container"
  type        = number
  default     = 256
}

variable "yubihsm_connector_memory" {
  description = "Memory for YubiHSM Connector container in MB"
  type        = number
  default     = 512
}

# Application Configuration
variable "vaultwarden_domain" {
  description = "Domain name for Vaultwarden"
  type        = string
  default     = ""
}

# Monitoring and Alerting
variable "alert_email" {
  description = "Email address for alerts"
  type        = string
  default     = ""
}

# Terraform Backend Configuration
variable "terraform_state_bucket" {
  description = "S3 bucket for Terraform state"
  type        = string
  default     = ""
}

variable "terraform_state_key" {
  description = "S3 key for Terraform state"
  type        = string
  default     = "yubihsm-infrastructure/terraform.tfstate"
}

variable "terraform_state_region" {
  description = "Region for Terraform state bucket"
  type        = string
  default     = "us-east-1"
}