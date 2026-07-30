# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

variable "name" {
  description = "Logical name prefix for all resources (e.g. casino-aegis)."
  type        = string
}

variable "environment" {
  description = "Environment tag (dev, stg, prod)."
  type        = string
  validation {
    condition     = contains(["dev", "stg", "prod", "localstack"], var.environment)
    error_message = "The environment value must be one of: dev, stg, prod, localstack."
  }
}

variable "engine_version" {
  description = "PostgreSQL engine version. 16.4 is the current stable tested with pg_aegis stub."
  type        = string
  default     = "16.4"
}

variable "instance_class" {
  description = "Instance class for the primary writer."
  type        = string
  default     = "db.m6g.large"
}

variable "reader_instance_class" {
  description = "Instance class for read replicas."
  type        = string
  default     = "db.m6g.large"
}

variable "reader_count" {
  description = "Number of read replicas. RDS-for-PostgreSQL hard limit is 15; Aurora 15."
  type        = number
  default     = 5
  validation {
    condition     = var.reader_count >= 0 && var.reader_count <= 15
    error_message = "The reader_count value must be between 0 and 15."
  }
}

variable "allocated_storage_gb" {
  description = "Writer storage in GB. Read replicas inherit."
  type        = number
  default     = 100
}

variable "storage_type" {
  description = "gp3 recommended; io2 for sustained >16k IOPS."
  type        = string
  default     = "gp3"
}

variable "db_subnet_ids" {
  description = "Subnet IDs for the DB subnet group (at least 2 AZs for prod)."
  type        = list(string)
}

variable "vpc_id" {
  description = "VPC ID — used for the security group."
  type        = string
}

variable "allowed_cidr_blocks" {
  description = "CIDRs allowed to connect to port 5432. Keep tight."
  type        = list(string)
  default     = []
}

variable "kms_key_arn" {
  description = "Customer Managed Key ARN. When null, the module creates one."
  type        = string
  default     = null
}

variable "enable_iam_auth" {
  description = "Enable IAM database authentication on the writer and replicas."
  type        = bool
  default     = true
}

variable "backup_retention_days" {
  description = "Automated backup retention window."
  type        = number
  default     = 14
}

variable "deletion_protection" {
  description = "Deletion protection. Default true; override to false for ephemeral/test envs."
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = "When true, no final snapshot on destroy. Only safe in dev/localstack."
  type        = bool
  default     = false
}

variable "force_ssl" {
  description = "Append rds.force_ssl=1 to the parameter group (rejects non-TLS connections)."
  type        = bool
  default     = true
}

variable "extra_parameters" {
  description = "Extra parameter-group entries. Merged with the base set."
  type = list(object({
    name         = string
    value        = string
    apply_method = string
  }))
  default = []
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default     = {}
}
