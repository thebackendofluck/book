# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

variable "project_name" {
  description = "Name of the project"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC"
  type        = string
}

variable "database_subnet_ids" {
  description = "IDs of database subnets"
  type        = list(string)
}

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

variable "kms_key_arn" {
  description = "ARN of the KMS key for encryption"
  type        = string
}

variable "db_security_group_id" {
  description = "ID of the database security group"
  type        = string
}

variable "enable_tde" {
  description = "Enable Transparent Data Encryption"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}