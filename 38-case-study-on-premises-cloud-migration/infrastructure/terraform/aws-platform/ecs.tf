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
# ECS Fargate — Casino API Service
# =============================================================================
# Regulatory context:
#   NJ DGE 13:69O-1.3  — Application servers must support high availability
#                         and automatic failover.
#   PA PGCB §809a.8    — Systems must scale to handle peak player load without
#                         degradation of service.
#   PCI-DSS 6.6        — Application must be behind a WAF or undergo regular
#                         code review (WAF integration via ALB).
#
# Architecture:
#   - ECS Fargate (serverless containers, no EC2 patching overhead)
#   - FastAPI casino API running in private subnets
#   - Auto-scaling based on CPU, memory, and request count
#   - Health checks with automatic task replacement
# =============================================================================

# --- ECS Cluster -------------------------------------------------------------

resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-${var.environment}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
    # PCI-DSS 10.1: Container-level monitoring and logging
  }

  configuration {
    execute_command_configuration {
      logging = "OVERRIDE"

      log_configuration {
        cloud_watch_log_group_name = aws_cloudwatch_log_group.ecs_exec.name
      }
    }
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-cluster"
    Compliance = "NJ-DGE-13:69O-1.3"
  })
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = var.api_min_count
    # NJ DGE: Base capacity on FARGATE (not SPOT) for guaranteed availability
  }
}

resource "aws_cloudwatch_log_group" "ecs_exec" {
  name              = "/aws/ecs/${var.project_name}-${var.environment}/exec"
  retention_in_days = 2557 # ~7 years — NJ DGE audit trail for exec sessions

  tags = merge(var.tags, {
    Compliance = "NJ-DGE-7yr-retention"
  })
}

# --- Task Definition ---------------------------------------------------------

resource "aws_ecs_task_definition" "casino_api" {
  family                   = "${var.project_name}-${var.environment}-casino-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name  = "casino-api"
      image = "${aws_ecr_repository.casino_api.repository_url}:${var.api_image_tag}"

      essential = true

      portMappings = [
        {
          containerPort = var.api_port
          protocol      = "tcp"
        }
      ]

      # Environment variables — non-sensitive config
      environment = [
        {
          name  = "ENVIRONMENT"
          value = var.environment
        },
        {
          name  = "PORT"
          value = tostring(var.api_port)
        },
        {
          name  = "DB_HOST"
          value = aws_db_instance.main.address
        },
        {
          name  = "DB_PORT"
          value = "5432"
        },
        {
          name  = "DB_NAME"
          value = var.db_name
        },
        {
          name  = "REDIS_HOST"
          value = aws_elasticache_replication_group.main.primary_endpoint_address
        },
        {
          name  = "REDIS_PORT"
          value = "6379"
        },
        {
          name  = "LOG_LEVEL"
          value = var.environment == "prod" ? "INFO" : "DEBUG"
        }
      ]

      # Secrets — pulled from Secrets Manager at runtime
      # PCI-DSS 2.1: Never store credentials in plaintext
      secrets = [
        {
          name      = "DB_PASSWORD"
          valueFrom = aws_secretsmanager_secret.db_password.arn
        },
        {
          name      = "JWT_SECRET"
          valueFrom = aws_secretsmanager_secret.jwt_secret.arn
        },
        {
          name      = "API_KEY"
          valueFrom = aws_secretsmanager_secret.api_key.arn
        }
      ]

      # Logging — all container output to CloudWatch
      # NJ DGE 13:69O-1.9: Application logs must be retained 7 years
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.casino_api.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }

      # Health check at container level
      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:${var.api_port}/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      # Resource limits
      ulimits = [
        {
          name      = "nofile"
          softLimit = 65536
          hardLimit = 65536
        }
      ]
    }
  ])

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-casino-api-task"
  })
}

resource "aws_cloudwatch_log_group" "casino_api" {
  name              = "/aws/ecs/${var.project_name}-${var.environment}/casino-api"
  retention_in_days = 2557 # ~7 years — NJ DGE requirement

  tags = merge(var.tags, {
    Compliance = "NJ-DGE-7yr-retention"
  })
}

# --- ECS Service -------------------------------------------------------------

resource "aws_ecs_service" "casino_api" {
  name            = "${var.project_name}-${var.environment}-casino-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.casino_api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  # NJ DGE: Rolling deployments with health checks prevent downtime
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 120

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
    # PCI-DSS 1.3: Application containers in private subnets only
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.casino_api.arn
    container_name   = "casino-api"
    container_port   = var.api_port
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
    # Automatic rollback on failed deployments
  }

  # Spread tasks across AZs for high availability
  # NJ DGE 13:69O-1.3: Multi-AZ required
  ordered_placement_strategy {
    type  = "spread"
    field = "attribute:ecs.availability-zone"
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-casino-api-service"
    Compliance = "NJ-DGE-13:69O-1.3"
  })

  lifecycle {
    ignore_changes = [desired_count, task_definition]
  }
}

# --- Auto-Scaling ------------------------------------------------------------
# PA PGCB §809a.8: Must handle peak load without service degradation

resource "aws_appautoscaling_target" "casino_api" {
  max_capacity       = var.api_max_count
  min_capacity       = var.api_min_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.casino_api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

# Scale on CPU utilization
resource "aws_appautoscaling_policy" "cpu" {
  name               = "${var.project_name}-${var.environment}-cpu-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.casino_api.resource_id
  scalable_dimension = aws_appautoscaling_target.casino_api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.casino_api.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 65.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

# Scale on memory utilization
resource "aws_appautoscaling_policy" "memory" {
  name               = "${var.project_name}-${var.environment}-memory-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.casino_api.resource_id
  scalable_dimension = aws_appautoscaling_target.casino_api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.casino_api.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
    target_value       = 75.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

# Scale on ALB request count per target
resource "aws_appautoscaling_policy" "requests" {
  name               = "${var.project_name}-${var.environment}-request-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.casino_api.resource_id
  scalable_dimension = aws_appautoscaling_target.casino_api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.casino_api.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.main.arn_suffix}/${aws_lb_target_group.casino_api.arn_suffix}"
    }
    target_value       = 1000
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
