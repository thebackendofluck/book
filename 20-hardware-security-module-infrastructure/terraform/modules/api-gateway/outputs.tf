# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# API Gateway Module Outputs

output "instance_id" {
  description = "API Gateway EC2 instance ID"
  value       = aws_instance.api_gateway.id
}

output "instance_private_ip" {
  description = "API Gateway private IP address"
  value       = aws_instance.api_gateway.private_ip
}

output "instance_public_ip" {
  description = "API Gateway public IP address"
  value       = aws_instance.api_gateway.public_ip
}

output "security_group_id" {
  description = "API Gateway security group ID"
  value       = aws_security_group.api_gateway.id
}

output "iam_role_arn" {
  description = "API Gateway IAM role ARN"
  value       = aws_iam_role.api_gateway.arn
}

output "iam_instance_profile_name" {
  description = "API Gateway IAM instance profile name"
  value       = aws_iam_instance_profile.api_gateway.name
}

output "cloudwatch_log_group_name" {
  description = "CloudWatch log group name"
  value       = aws_cloudwatch_log_group.api_gateway.name
}

output "route53_record_fqdn" {
  description = "Route 53 record FQDN"
  value       = var.create_dns_record ? aws_route53_record.api_gateway[0].fqdn : null
}

output "ssm_parameter_arn" {
  description = "SSM parameter ARN for HSM password"
  value       = var.hsm_password_ssm_param == "" ? aws_ssm_parameter.hsm_password[0].arn : null
}