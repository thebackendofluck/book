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

variable "container_security_group_id" {
  description = "ID of the container security group"
  type        = string
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