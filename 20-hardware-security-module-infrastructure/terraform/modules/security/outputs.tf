# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

output "kms_key_arn" {
  description = "ARN of the KMS key"
  value       = aws_kms_key.main.arn
  sensitive   = true
}

output "kms_key_id" {
  description = "ID of the KMS key"
  value       = aws_kms_key.main.key_id
}

output "kms_key_alias" {
  description = "Alias of the KMS key"
  value       = aws_kms_alias.main.name
}

output "yubihsm_security_group_id" {
  description = "ID of YubiHSM security group"
  value       = aws_security_group.yubihsm.id
}

output "app_security_group_id" {
  description = "ID of application security group"
  value       = aws_security_group.app.id
}

output "database_security_group_id" {
  description = "ID of database security group"
  value       = aws_security_group.database.id
}

output "container_security_group_id" {
  description = "ID of container security group"
  value       = aws_security_group.container.id
}

output "ec2_iam_role_arn" {
  description = "ARN of EC2 IAM role"
  value       = aws_iam_role.ec2.arn
}

output "ec2_iam_role_name" {
  description = "Name of EC2 IAM role"
  value       = aws_iam_role.ec2.name
}

output "ec2_instance_profile_name" {
  description = "Name of EC2 instance profile"
  value       = aws_iam_instance_profile.ec2.name
}

output "nitro_enclave_iam_role_arn" {
  description = "ARN of Nitro Enclave IAM role"
  value       = aws_iam_role.nitro_enclave.arn
}

output "nitro_enclave_iam_role_name" {
  description = "Name of Nitro Enclave IAM role"
  value       = aws_iam_role.nitro_enclave.name
}

output "ecs_task_iam_role_arn" {
  description = "ARN of ECS task IAM role"
  value       = aws_iam_role.ecs_task.arn
}

output "ecs_task_iam_role_name" {
  description = "Name of ECS task IAM role"
  value       = aws_iam_role.ecs_task.name
}