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

variable "yubihsm_auth_key_id" {
  description = "YubiHSM authentication key ID"
  type        = number
  sensitive   = true
}

variable "yubihsm_password" {
  description = "YubiHSM authentication password"
  type        = string
  sensitive   = true
}

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

variable "additional_kms_grants" {
  description = "Additional KMS grants"
  type = list(object({
    sid                   = string
    effect                = string
    principal_type        = string
    principal_identifiers = list(string)
    actions               = list(string)
    resources             = list(string)
  }))
  default = []
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}