# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Common variables
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

variable "private_subnet_ids" {
  description = "IDs of private subnets"
  type        = list(string)
}

variable "kms_key_arn" {
  description = "ARN of the KMS key for encryption"
  type        = string
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

# YubiHSM specific variables
variable "enable_yubihsm" {
  description = "Enable YubiHSM 2 infrastructure"
  type        = bool
  default     = true
}

variable "yubihsm_ami_id" {
  description = "AMI ID for YubiHSM connector instance"
  type        = string
  default     = "ami-0abcdef1234567890" # Update with actual AMI
}

variable "yubihsm_instance_type" {
  description = "Instance type for YubiHSM connector"
  type        = string
  default     = "t3.micro"
}

variable "yubihsm_root_volume_size" {
  description = "Root volume size for YubiHSM connector in GB"
  type        = number
  default     = 20
}

variable "yubihsm_connector_version" {
  description = "Version of YubiHSM connector to install"
  type        = string
  default     = "3.0.2"
}

variable "yubihsm_auth_key_id" {
  description = "Authentication key ID for YubiHSM"
  type        = number
  default     = 1
  sensitive   = true
}

variable "yubihsm_auth_password" {
  description = "Authentication password for YubiHSM"
  type        = string
  sensitive   = true
}

variable "yubihsm_device_serial" {
  description = "Serial number of the YubiHSM device"
  type        = string
  default     = ""
  sensitive   = true
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to access YubiHSM connector"
  type        = list(string)
  default     = []
}

variable "management_cidr_blocks" {
  description = "CIDR blocks allowed SSH access for management"
  type        = list(string)
  default     = []
}

variable "backup_retention_days" {
  description = "Number of days to retain backups in S3"
  type        = number
  default     = 365
}

variable "log_retention_days" {
  description = "Number of days to retain CloudWatch logs"
  type        = number
  default     = 30
}

variable "storage_alarm_threshold" {
  description = "Threshold percentage for storage usage alarm"
  type        = number
  default     = 80
}

variable "alarm_sns_topic_arn" {
  description = "ARN of SNS topic for alarms"
  type        = string
  default     = ""
}

# Certificate management variables
variable "enable_certificate_management" {
  description = "Enable certificate management features"
  type        = bool
  default     = true
}

variable "certificate_domains" {
  description = "List of domains for certificate management"
  type        = list(string)
  default     = []
}

variable "certificate_validity_days" {
  description = "Validity period for certificates in days"
  type        = number
  default     = 365
}

variable "enable_lets_encrypt" {
  description = "Enable Let's Encrypt certificate automation"
  type        = bool
  default     = false
}

variable "lets_encrypt_email" {
  description = "Email address for Let's Encrypt notifications"
  type        = string
  default     = ""
}

# Lifecycle management variables
variable "enable_lifecycle_management" {
  description = "Enable lifecycle management features"
  type        = bool
  default     = true
}

variable "cleanup_expired_certificates" {
  description = "Automatically cleanup expired certificates"
  type        = bool
  default     = true
}

variable "cleanup_old_objects_days" {
  description = "Number of days after which to cleanup old objects"
  type        = number
  default     = 90
}

variable "rotation_schedule" {
  description = "Schedule for key rotation (cron format)"
  type        = string
  default     = "0 2 * * 1" # Weekly on Monday at 2 AM
}

variable "backup_schedule" {
  description = "Schedule for automated backups (cron format)"
  type        = string
  default     = "0 1 * * *" # Daily at 1 AM
}

variable "cleanup_schedule" {
  description = "Schedule for cleanup operations (cron format)"
  type        = string
  default     = "0 2 * * 0" # Weekly on Sunday at 2 AM
}