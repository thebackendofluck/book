# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ECS Cluster
resource "aws_ecs_cluster" "main" {
  name = var.ecs_cluster_name

  tags = merge(var.tags, {
    Name = var.ecs_cluster_name
  })
}

# ECS Task Definition for Vaultwarden
resource "aws_ecs_task_definition" "vaultwarden" {
  family                   = "${var.project_name}-${var.environment}-vaultwarden"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.vaultwarden_cpu
  memory                   = var.vaultwarden_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name  = "vaultwarden"
      image = var.vaultwarden_image
      portMappings = [
        {
          containerPort = 80
          hostPort      = 80
          protocol      = "tcp"
        }
      ]
      environment = [
        {
          name  = "DOMAIN"
          value = "https://vaultwarden.${var.project_name}.local"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.vaultwarden.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-vaultwarden-task"
  })
}

# ECS Task Definition for YubiHSM Connector
resource "aws_ecs_task_definition" "yubihsm_connector" {
  family                   = "${var.project_name}-${var.environment}-yubihsm-connector"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.yubihsm_connector_cpu
  memory                   = var.yubihsm_connector_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name  = "yubihsm-connector"
      image = var.yubihsm_connector_image
      portMappings = [
        {
          containerPort = 12345
          hostPort      = 12345
          protocol      = "tcp"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.yubihsm_connector.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-connector-task"
  })
}

# ECS Service for Vaultwarden
resource "aws_ecs_service" "vaultwarden" {
  name            = "${var.project_name}-${var.environment}-vaultwarden"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.vaultwarden.arn
  desired_count   = 1

  network_configuration {
    security_groups  = [var.container_security_group_id]
    subnets          = var.private_subnet_ids
    assign_public_ip = false
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-vaultwarden-service"
  })
}

# ECS Service for YubiHSM Connector
resource "aws_ecs_service" "yubihsm_connector" {
  name            = "${var.project_name}-${var.environment}-yubihsm-connector"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.yubihsm_connector.arn
  desired_count   = 1

  network_configuration {
    security_groups  = [var.container_security_group_id]
    subnets          = var.private_subnet_ids
    assign_public_ip = false
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-connector-service"
  })
}

# IAM Roles
resource "aws_iam_role" "ecs_execution" {
  name = "${var.project_name}-${var.environment}-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-ecs-execution-role"
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_task" {
  name = "${var.project_name}-${var.environment}-ecs-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-ecs-task-role"
  })
}

# CloudWatch Log Groups
resource "aws_cloudwatch_log_group" "vaultwarden" {
  name              = "/ecs/${var.project_name}-${var.environment}-vaultwarden"
  retention_in_days = 30
  kms_key_id        = var.kms_key_arn

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-vaultwarden-logs"
  })
}

resource "aws_cloudwatch_log_group" "yubihsm_connector" {
  name              = "/ecs/${var.project_name}-${var.environment}-yubihsm-connector"
  retention_in_days = 30
  kms_key_id        = var.kms_key_arn

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-yubihsm-connector-logs"
  })
}

# Data sources
data "aws_region" "current" {}