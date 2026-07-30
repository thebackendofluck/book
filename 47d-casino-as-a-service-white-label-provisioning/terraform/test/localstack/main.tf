# Companion code for "The Backend of Luck" - Chapter 47d, Casino as a Service.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: BUSL-1.1
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "localstack_endpoint" {
  description = "LocalStack endpoint. Override with TF_VAR_localstack_endpoint if needed."
  type        = string
  default     = "http://localstack:4566"
}

variable "tenant_slug" {
  description = "Tenant de teste a provisionar no LocalStack."
  type        = string
  default     = "demo-tenant"
}

# Provider apontado para o LocalStack: credenciais fake, validacoes desligadas,
# path-style p/ S3. Em producao usa-se um provider sem o bloco endpoints.
provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
  s3_use_path_style           = true

  endpoints {
    s3        = var.localstack_endpoint
    s3control = var.localstack_endpoint
    ecr       = var.localstack_endpoint
    dynamodb  = var.localstack_endpoint
    sts       = var.localstack_endpoint
    iam       = var.localstack_endpoint
  }
}

module "tenant" {
  source = "../../modules/tenant-aws"

  tenant_slug           = var.tenant_slug
  environment           = "dev"
  jurisdiction          = "demo"
  backup_retention_days = 7
}

output "assets_bucket" {
  value = module.tenant.assets_bucket
}

output "backups_bucket" {
  value = module.tenant.backups_bucket
}

output "ecr_repository_url" {
  value = module.tenant.ecr_repository_url
}

output "meta_table" {
  value = module.tenant.meta_table
}
