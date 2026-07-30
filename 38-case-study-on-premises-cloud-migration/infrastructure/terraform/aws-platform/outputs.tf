# Companion code for "The Backend of Luck" - Chapter 38, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Outputs — AWS iGaming Casino Platform
# =============================================================================

# --- Networking --------------------------------------------------------------

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = aws_subnet.private[*].id
}

output "data_subnet_ids" {
  description = "Data subnet IDs"
  value       = aws_subnet.data[*].id
}

# --- ALB ---------------------------------------------------------------------

output "alb_dns_name" {
  description = "ALB DNS name — point your domain CNAME here"
  value       = aws_lb.main.dns_name
}

output "alb_zone_id" {
  description = "ALB hosted zone ID for Route53 alias record"
  value       = aws_lb.main.zone_id
}

output "alb_arn" {
  description = "ALB ARN"
  value       = aws_lb.main.arn
}

# --- ECS ---------------------------------------------------------------------

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.casino_api.name
}

output "ecs_task_definition" {
  description = "Current ECS task definition ARN"
  value       = aws_ecs_task_definition.casino_api.arn
}

# --- RDS ---------------------------------------------------------------------

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint (host:port)"
  value       = "${aws_db_instance.main.address}:${aws_db_instance.main.port}"
}

output "rds_address" {
  description = "RDS PostgreSQL hostname"
  value       = aws_db_instance.main.address
}

output "rds_instance_id" {
  description = "RDS instance identifier"
  value       = aws_db_instance.main.identifier
}

# --- ElastiCache Redis -------------------------------------------------------

output "redis_endpoint" {
  description = "Redis primary endpoint"
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "redis_reader_endpoint" {
  description = "Redis reader endpoint for read replicas"
  value       = aws_elasticache_replication_group.main.reader_endpoint_address
}

# --- ECR ---------------------------------------------------------------------

output "ecr_repository_url" {
  description = "ECR repository URL for Docker push"
  value       = aws_ecr_repository.casino_api.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN"
  value       = aws_ecr_repository.casino_api.arn
}

# --- Secrets -----------------------------------------------------------------

output "db_password_secret_arn" {
  description = "Secrets Manager ARN for database password"
  value       = aws_secretsmanager_secret.db_password.arn
  sensitive   = true
}

output "jwt_secret_arn" {
  description = "Secrets Manager ARN for JWT signing secret"
  value       = aws_secretsmanager_secret.jwt_secret.arn
  sensitive   = true
}

# --- CI/CD -------------------------------------------------------------------

output "codepipeline_name" {
  description = "CodePipeline name"
  value       = aws_codepipeline.casino_api.name
}

output "codebuild_project" {
  description = "CodeBuild project name"
  value       = aws_codebuild_project.casino_api.name
}

# --- Monitoring --------------------------------------------------------------

output "cloudwatch_dashboard_url" {
  description = "CloudWatch dashboard URL"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.main.dashboard_name}"
}

output "sns_alarm_topic_arn" {
  description = "SNS topic ARN for alarm notifications"
  value       = aws_sns_topic.alarms.arn
}

# --- WAF ---------------------------------------------------------------------

output "waf_web_acl_arn" {
  description = "WAF Web ACL ARN"
  value       = aws_wafv2_web_acl.main.arn
}

# --- Summary (convenience output) -------------------------------------------

output "platform_summary" {
  description = "Quick reference for all platform endpoints"
  value = {
    api_url    = "https://${var.domain_name}"
    alb_dns    = aws_lb.main.dns_name
    rds        = "${aws_db_instance.main.address}:5432"
    redis      = "${aws_elasticache_replication_group.main.primary_endpoint_address}:6379"
    ecr        = aws_ecr_repository.casino_api.repository_url
    dashboard  = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.main.dashboard_name}"
    pipeline   = aws_codepipeline.casino_api.name
    compliance = "NJ-DGE, PA-PGCB, PCI-DSS"
  }
}
