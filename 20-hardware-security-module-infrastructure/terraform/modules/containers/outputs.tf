# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

output "ecs_cluster_id" {
  description = "ID of the ECS cluster"
  value       = aws_ecs_cluster.main.id
}

output "ecs_cluster_arn" {
  description = "ARN of the ECS cluster"
  value       = aws_ecs_cluster.main.arn
}

output "vaultwarden_service_id" {
  description = "ID of the Vaultwarden ECS service"
  value       = aws_ecs_service.vaultwarden.id
}

output "vaultwarden_service_arn" {
  description = "ARN of the Vaultwarden ECS service"
  value       = aws_ecs_service.vaultwarden.id
}

output "yubihsm_connector_service_id" {
  description = "ID of the YubiHSM Connector ECS service"
  value       = aws_ecs_service.yubihsm_connector.id
}

output "yubihsm_connector_service_arn" {
  description = "ARN of the YubiHSM Connector ECS service"
  value       = aws_ecs_service.yubihsm_connector.id
}

output "ecs_execution_role_arn" {
  description = "ARN of the ECS execution role"
  value       = aws_iam_role.ecs_execution.arn
}

output "ecs_task_role_arn" {
  description = "ARN of the ECS task role"
  value       = aws_iam_role.ecs_task.arn
}