# Companion code for "The Backend of Luck" - Chapter 47d, Casino as a Service.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: BUSL-1.1
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

output "assets_bucket" {
  description = "S3 bucket holding the tenant white-label assets."
  value       = aws_s3_bucket.assets.bucket
}

output "backups_bucket" {
  description = "S3 bucket holding the tenant backups."
  value       = aws_s3_bucket.backups.bucket
}

output "ecr_repository_url" {
  description = "URL of the ECR repository for the tenant runtime image."
  value       = aws_ecr_repository.runtime.repository_url
}

output "meta_table" {
  description = "DynamoDB table holding the tenant metadata."
  value       = aws_dynamodb_table.tenant_meta.name
}

output "tenant_slug" {
  description = "Slug of the provisioned tenant."
  value       = var.tenant_slug
}
