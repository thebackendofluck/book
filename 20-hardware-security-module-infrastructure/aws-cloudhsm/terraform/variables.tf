# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

variable "env" {
  description = "Environment name (dev, staging, prod)"
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.env)
    error_message = "env must be one of: dev, staging, prod"
  }
}

variable "region" {
  description = "AWS region for CloudHSM cluster"
  type        = string
  default     = "eu-west-1"
}

variable "vpc_id" {
  description = "VPC ID for the CloudHSM cluster and OpenBao nodes"
  type        = string
}

variable "hsm_subnet_ids" {
  description = "Private subnet IDs for the CloudHSM cluster — one per AZ. Minimum 2 for HA, 3 recommended."
  type        = list(string)

  validation {
    condition     = length(var.hsm_subnet_ids) >= 2
    error_message = "At least 2 subnets (AZs) required for CloudHSM HA."
  }
}

variable "hsm_type" {
  description = "CloudHSM HSM instance type"
  type        = string
  default     = "hsm1.medium"

  validation {
    condition     = var.hsm_type == "hsm1.medium"
    error_message = "Only hsm1.medium is currently supported by AWS CloudHSM v2."
  }
}

variable "openbao_ami_id" {
  description = "AMI ID for OpenBao EC2 nodes (Amazon Linux 2023 or Ubuntu 22.04 recommended)"
  type        = string
}

variable "openbao_instance_type" {
  description = "EC2 instance type for OpenBao nodes"
  type        = string
  default     = "t3.medium"
}

variable "openbao_key_name" {
  description = "EC2 key pair name for OpenBao nodes (SSH access for emergency management)"
  type        = string
}

variable "openbao_min_size" {
  description = "Minimum number of OpenBao nodes in the ASG (≥ 3 for Raft quorum)"
  type        = number
  default     = 3

  validation {
    condition     = var.openbao_min_size >= 3
    error_message = "OpenBao requires at least 3 nodes for Raft quorum."
  }
}

variable "openbao_max_size" {
  description = "Maximum number of OpenBao nodes in the ASG"
  type        = number
  default     = 5
}

variable "openbao_desired_capacity" {
  description = "Desired number of OpenBao nodes"
  type        = number
  default     = 3
}

variable "openbao_allowed_cidrs" {
  description = "CIDR blocks allowed to reach OpenBao API on port 8200. Should be restricted to platform subnets."
  type        = list(string)
  default     = []
}

variable "cloudhsm_pin_ssm_parameter" {
  description = "AWS SSM Parameter Store path containing the CloudHSM CU PIN (format: hsm-app:<password>). Must be a SecureString."
  type        = string
  default     = "/igaming/cloudhsm/cu-pin"
}

variable "log_retention_days" {
  description = "CloudWatch log group retention in days (PCI DSS requires ≥ 12 months online)"
  type        = number
  default     = 365
}

variable "alarm_sns_topic_arns" {
  description = "SNS topic ARNs for CloudWatch alarm notifications"
  type        = list(string)
  default     = []
}

variable "cost_centre" {
  description = "Cost centre tag for financial tracking"
  type        = string
  default     = "platform-security"
}

variable "business_unit" {
  description = "Business unit tag for financial tracking"
  type        = string
  default     = "igaming-platform"
}

variable "tags" {
  description = "Additional resource tags to merge with defaults"
  type        = map(string)
  default     = {}
}
