# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# VPC and Networking Outputs
output "vpc_id" {
  description = "ID of the VPC"
  value       = module.networking.vpc_id
}

output "vpc_cidr_block" {
  description = "CIDR block of the VPC"
  value       = module.networking.vpc_cidr_block
}

output "public_subnet_ids" {
  description = "IDs of public subnets"
  value       = module.networking.public_subnet_ids
}

output "private_subnet_ids" {
  description = "IDs of private subnets"
  value       = module.networking.private_subnet_ids
}

output "database_subnet_ids" {
  description = "IDs of database subnets"
  value       = module.networking.database_subnet_ids
}

output "nat_gateway_ids" {
  description = "IDs of NAT Gateways"
  value       = module.networking.nat_gateway_ids
}

# Security Outputs
output "kms_key_arn" {
  description = "ARN of the KMS key"
  value       = module.security.kms_key_arn
  sensitive   = true
}

output "kms_key_id" {
  description = "ID of the KMS key"
  value       = module.security.kms_key_id
}

output "yubihsm_security_group_id" {
  description = "ID of YubiHSM security group"
  value       = module.security.yubihsm_security_group_id
}

output "app_security_group_id" {
  description = "ID of application security group"
  value       = module.security.app_security_group_id
}

output "database_security_group_id" {
  description = "ID of database security group"
  value       = module.security.database_security_group_id
}

output "container_security_group_id" {
  description = "ID of container security group"
  value       = module.security.container_security_group_id
}

# Compute Outputs
output "app_server_instance_id" {
  description = "ID of the application server EC2 instance"
  value       = module.compute.app_server_instance_id
}

output "app_server_private_ip" {
  description = "Private IP of the application server"
  value       = module.compute.app_server_private_ip
}

output "nitro_enclave_instance_id" {
  description = "ID of the Nitro Enclave EC2 instance"
  value       = module.compute.nitro_enclave_instance_id
}

output "nitro_enclave_private_ip" {
  description = "Private IP of the Nitro Enclave instance"
  value       = module.compute.nitro_enclave_private_ip
}

# Storage Outputs
output "ebs_volume_id" {
  description = "ID of EBS volume"
  value       = module.storage.ebs_volume_id
}

output "efs_file_system_id" {
  description = "ID of EFS file system"
  value       = module.storage.efs_file_system_id
}

output "efs_file_system_arn" {
  description = "ARN of EFS file system"
  value       = module.storage.efs_file_system_arn
}

output "s3_bucket_id" {
  description = "ID of S3 bucket"
  value       = module.storage.s3_bucket_id
}

output "s3_bucket_arn" {
  description = "ARN of S3 bucket"
  value       = module.storage.s3_bucket_arn
}

# Database Outputs
output "db_endpoint" {
  description = "Endpoint of RDS instance"
  value       = module.database.db_endpoint
}

output "db_port" {
  description = "Port of RDS instance"
  value       = module.database.db_port
}

output "db_instance_arn" {
  description = "ARN of RDS instance"
  value       = module.database.db_instance_arn
}

# Container Outputs
output "ecs_cluster_id" {
  description = "ID of ECS cluster"
  value       = module.containers.ecs_cluster_id
}

output "ecs_cluster_arn" {
  description = "ARN of ECS cluster"
  value       = module.containers.ecs_cluster_arn
}

output "vaultwarden_service_id" {
  description = "ID of Vaultwarden ECS service"
  value       = module.containers.vaultwarden_service_id
}

output "yubihsm_connector_service_id" {
  description = "ID of YubiHSM Connector ECS service"
  value       = module.containers.yubihsm_connector_service_id
}

# Application URLs
output "vaultwarden_url" {
  description = "URL for Vaultwarden web interface"
  value       = var.vaultwarden_domain != "" ? "https://${var.vaultwarden_domain}" : null
}

output "yubihsm_connector_url" {
  description = "URL for YubiHSM Connector API"
  value       = "http://${module.compute.app_server_private_ip}:12345"
}

# Monitoring Outputs
output "cloudwatch_log_group_name" {
  description = "Name of CloudWatch log group"
  value       = "/aws/yubihsm/${var.environment}"
}

output "sns_topic_arn" {
  description = "ARN of SNS topic for alerts"
  value       = aws_sns_topic.alerts.arn
}

# Terraform State Outputs
output "terraform_state_bucket" {
  description = "S3 bucket used for Terraform state"
  value       = var.terraform_state_bucket
}

output "terraform_state_key" {
  description = "S3 key used for Terraform state"
  value       = var.terraform_state_key
}

# Summary Information
output "infrastructure_summary" {
  description = "Summary of deployed infrastructure"
  value = {
    project     = var.project_name
    environment = var.environment
    region      = var.aws_region
    vpc_id      = module.networking.vpc_id
    app_server  = module.compute.app_server_instance_id
    database    = module.database.db_endpoint
    kms_key     = module.security.kms_key_id
  }
}