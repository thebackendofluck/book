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
      version = ">= 5.0"
    }
  }
}

locals {
  name_prefix = "tenant-${var.tenant_slug}"

  common_tags = merge({
    "caas.tenant"       = var.tenant_slug
    "caas.environment"  = var.environment
    "caas.jurisdiction" = var.jurisdiction
    "caas.managed-by"   = "caasctl"
  }, var.extra_tags)
}

# White-label brand assets (logos, paletas, banners servidos na borda)
resource "aws_s3_bucket" "assets" {
  bucket = "${local.name_prefix}-assets"
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "assets" {
  bucket = aws_s3_bucket.assets.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Backups do banco do tenant (isolados por tenant)
resource "aws_s3_bucket" "backups" {
  bucket = "${local.name_prefix}-backups"
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "expire-old-backups"
    status = "Enabled"

    filter {
      prefix = ""
    }

    expiration {
      days = var.backup_retention_days
    }
  }
}

# Registry de imagem do runtime do tenant
resource "aws_ecr_repository" "runtime" {
  name                 = "${local.name_prefix}-runtime"
  image_tag_mutability = "IMMUTABLE"
  tags                 = local.common_tags

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Metadados do tenant (espelho operacional do control-plane)
resource "aws_dynamodb_table" "tenant_meta" {
  name         = "${local.name_prefix}-meta"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "key"
  tags         = local.common_tags

  attribute {
    name = "key"
    type = "S"
  }
}
