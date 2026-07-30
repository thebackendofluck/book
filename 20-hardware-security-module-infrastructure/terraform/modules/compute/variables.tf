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

variable "private_subnet_ids" {
  description = "IDs of private subnets"
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "IDs of public subnets"
  type        = list(string)
}

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
  default     = ""
}

variable "nitro_enclave_ami_id" {
  description = "AMI ID for Nitro Enclave instances"
  type        = string
  default     = ""
}

variable "key_pair_name" {
  description = "SSH key pair name for EC2 instances"
  type        = string
  default     = ""
}

variable "yubihsm_security_group_id" {
  description = "ID of the YubiHSM security group"
  type        = string
}

variable "app_security_group_id" {
  description = "ID of the application security group"
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the KMS key for encryption"
  type        = string
}

variable "user_data_scripts" {
  description = "User data scripts for instances"
  type        = map(string)
  default     = {}
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}