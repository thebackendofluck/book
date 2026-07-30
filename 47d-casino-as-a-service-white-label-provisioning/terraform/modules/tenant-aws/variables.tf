# Companion code for "The Backend of Luck" - Chapter 47d, Casino as a Service.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: BUSL-1.1
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

variable "tenant_slug" {
  description = "Immutable tenant slug (for example: acme, neonpalace). Used as the prefix for every resource."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,38}[a-z0-9]$", var.tenant_slug))
    error_message = "tenant_slug must be kebab-case, 3-40 characters, starting with a letter."
  }
}

variable "environment" {
  description = "Target environment (dev, staging, prod)."
  type        = string
  default     = "staging"
}

variable "jurisdiction" {
  description = "Regulatory jurisdiction of the tenant (br, mga, ukgc, demo). Drives retention and compliance tags."
  type        = string
  default     = "demo"
}

variable "backup_retention_days" {
  description = "Number of days tenant backups are retained before expiring in S3."
  type        = number
  default     = 30
}

variable "extra_tags" {
  description = "Additional tags applied to every resource."
  type        = map(string)
  default     = {}
}
