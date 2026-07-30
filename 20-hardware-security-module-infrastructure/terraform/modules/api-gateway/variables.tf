# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# API Gateway Module Variables

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
  default     = ""
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "vpc_id" {
  description = "VPC ID where API Gateway will be deployed"
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID for API Gateway instance"
  type        = string
}

variable "allowed_cidr_blocks" {
  description = "List of CIDR blocks allowed to access API Gateway"
  type        = list(string)
  default     = []
}

variable "allowed_ssh_cidr_blocks" {
  description = "List of CIDR blocks allowed SSH access"
  type        = list(string)
  default     = []
}

variable "ami_id" {
  description = "AMI ID for EC2 instance"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.micro"
}

variable "key_pair_name" {
  description = "SSH key pair name"
  type        = string
}

variable "root_volume_size" {
  description = "Root volume size in GB"
  type        = number
  default     = 20
}

variable "root_volume_type" {
  description = "Root volume type"
  type        = string
  default     = "gp3"
}

variable "kms_key_arn" {
  description = "KMS key ARN for encryption"
  type        = string
  default     = ""
}

variable "api_port" {
  description = "API Gateway port"
  type        = number
  default     = 8443
}

variable "hsm_connector_url" {
  description = "YubiHSM connector URL"
  type        = string
  default     = "http://localhost:12345"
}

variable "hsm_auth_key_id" {
  description = "HSM authentication key ID"
  type        = number
  default     = 2
}

variable "hsm_password" {
  description = "HSM authentication password"
  type        = string
  sensitive   = true
}

variable "hsm_password_ssm_param" {
  description = "SSM parameter name for HSM password (if using SSM)"
  type        = string
  default     = ""
}

variable "cert_directory" {
  description = "Directory for SSL certificates"
  type        = string
  default     = "/etc/ssl/api_gateway"
}

variable "rate_limit_per_minute" {
  description = "Rate limit per client per minute"
  type        = number
  default     = 100
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "create_dns_record" {
  description = "Whether to create Route 53 DNS record"
  type        = bool
  default     = false
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone ID"
  type        = string
  default     = ""
}

variable "dns_record_name" {
  description = "DNS record name"
  type        = string
  default     = ""
}

variable "cpu_alarm_threshold" {
  description = "CPU utilization alarm threshold"
  type        = number
  default     = 80
}

variable "alarm_sns_topic_arn" {
  description = "SNS topic ARN for alarms"
  type        = string
  default     = ""
}