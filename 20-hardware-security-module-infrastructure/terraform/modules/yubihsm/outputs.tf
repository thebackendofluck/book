# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# YubiHSM Module Outputs

# Storage outputs
output "yubihsm_connector_instance_id" {
  description = "Instance ID of the YubiHSM connector"
  value       = var.enable_yubihsm ? aws_instance.yubihsm_connector[0].id : null
}

output "yubihsm_connector_private_ip" {
  description = "Private IP of the YubiHSM connector"
  value       = var.enable_yubihsm ? aws_instance.yubihsm_connector[0].private_ip : null
}

output "yubihsm_backup_bucket_name" {
  description = "Name of the S3 bucket for YubiHSM backups"
  value       = var.enable_yubihsm ? aws_s3_bucket.yubihsm_backups[0].id : null
}

output "yubihsm_backup_bucket_arn" {
  description = "ARN of the S3 bucket for YubiHSM backups"
  value       = var.enable_yubihsm ? aws_s3_bucket.yubihsm_backups[0].arn : null
}

# Lifecycle outputs
output "lifecycle_lambda_function_name" {
  description = "Name of the lifecycle management Lambda function"
  value       = var.enable_lifecycle_management ? aws_lambda_function.yubihsm_lifecycle[0].function_name : null
}

output "lifecycle_lambda_function_arn" {
  description = "ARN of the lifecycle management Lambda function"
  value       = var.enable_lifecycle_management ? aws_lambda_function.yubihsm_lifecycle[0].arn : null
}

# Certificate outputs
output "certificate_arn" {
  description = "ARN of the ACM certificate"
  value       = var.enable_certificate_management && length(var.certificate_domains) > 0 ? aws_acm_certificate.yubihsm_cert[0].arn : null
}

output "certificate_domain_validation_options" {
  description = "Domain validation options for the certificate"
  value       = var.enable_certificate_management && length(var.certificate_domains) > 0 ? aws_acm_certificate.yubihsm_cert[0].domain_validation_options : []
}

output "cert_manager_lambda_function_name" {
  description = "Name of the certificate manager Lambda function"
  value       = var.enable_certificate_management ? aws_lambda_function.yubihsm_cert_manager[0].function_name : null
}

output "cert_manager_lambda_function_arn" {
  description = "ARN of the certificate manager Lambda function"
  value       = var.enable_certificate_management ? aws_lambda_function.yubihsm_cert_manager[0].arn : null
}

# Security outputs
output "yubihsm_security_group_id" {
  description = "ID of the YubiHSM connector security group"
  value       = var.enable_yubihsm ? aws_security_group.yubihsm_connector[0].id : null
}

output "yubihsm_lambda_security_group_id" {
  description = "ID of the Lambda security group"
  value       = var.enable_lifecycle_management ? aws_security_group.yubihsm_lambda[0].id : null
}

# Monitoring outputs
output "yubihsm_log_group_name" {
  description = "Name of the CloudWatch log group for YubiHSM"
  value       = var.enable_yubihsm ? aws_cloudwatch_log_group.yubihsm[0].name : null
}

output "yubihsm_log_group_arn" {
  description = "ARN of the CloudWatch log group for YubiHSM"
  value       = var.enable_yubihsm ? aws_cloudwatch_log_group.yubihsm[0].arn : null
}