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
# CloudWatch — Monitoring & Alerting for Casino Platform
# =============================================================================
# Regulatory context:
#   NJ DGE 13:69O-1.9  — Continuous monitoring of all platform components
#                         with automated alerting for anomalies.
#   PA PGCB §809a.10   — Real-time dashboards for platform health required.
#   PCI-DSS 10.5       — Secure audit trails so they cannot be altered.
#   PCI-DSS 10.6       — Review logs and security events at least daily.
#   PCI-DSS 10.7       — Retain audit trail history for at least one year,
#                         with minimum 3 months immediately available.
#
# Alarms:
#   - API latency (P99 > threshold)
#   - API error rate (5xx > threshold)
#   - Database connections (approaching max)
#   - ECS CPU/memory utilization
#   - Redis cache hit ratio
#   - Unhealthy ALB targets
# =============================================================================

# --- SNS Topic for Alarms ---------------------------------------------------

resource "aws_sns_topic" "alarms" {
  name = "${var.project_name}-${var.environment}-alarms"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-alarms"
  })
}

resource "aws_sns_topic_subscription" "alarms_email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_sns_email
}

# --- API Latency Alarm -------------------------------------------------------
# NJ DGE: Player experience must not be degraded; latency SLA monitoring

resource "aws_cloudwatch_metric_alarm" "api_latency_p99" {
  alarm_name          = "${var.project_name}-${var.environment}-api-latency-p99"
  alarm_description   = "Casino API P99 latency exceeds ${var.api_latency_threshold_ms}ms"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "p99"
  threshold           = var.api_latency_threshold_ms / 1000 # Convert to seconds
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.casino_api.arn_suffix
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-api-latency-alarm"
    Compliance = "NJ-DGE-13:69O-1.9"
  })
}

# --- API Error Rate Alarm ----------------------------------------------------
# PA PGCB: Error rates must be monitored for service quality

resource "aws_cloudwatch_metric_alarm" "api_5xx_rate" {
  alarm_name          = "${var.project_name}-${var.environment}-api-5xx-rate"
  alarm_description   = "Casino API 5xx error rate exceeds ${var.api_error_rate_threshold}%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = var.api_error_rate_threshold

  metric_query {
    id          = "error_rate"
    expression  = "(errors / total) * 100"
    label       = "5xx Error Rate %"
    return_data = true
  }

  metric_query {
    id = "errors"
    metric {
      metric_name = "HTTPCode_Target_5XX_Count"
      namespace   = "AWS/ApplicationELB"
      period      = 300
      stat        = "Sum"
      dimensions = {
        LoadBalancer = aws_lb.main.arn_suffix
        TargetGroup  = aws_lb_target_group.casino_api.arn_suffix
      }
    }
  }

  metric_query {
    id = "total"
    metric {
      metric_name = "RequestCount"
      namespace   = "AWS/ApplicationELB"
      period      = 300
      stat        = "Sum"
      dimensions = {
        LoadBalancer = aws_lb.main.arn_suffix
        TargetGroup  = aws_lb_target_group.casino_api.arn_suffix
      }
    }
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-api-error-alarm"
    Compliance = "PA-PGCB-809a.10"
  })
}

# --- Database Connection Alarm -----------------------------------------------
# PCI-DSS 10.6: Monitor database access patterns

resource "aws_cloudwatch_metric_alarm" "db_connections" {
  alarm_name          = "${var.project_name}-${var.environment}-db-connections"
  alarm_description   = "RDS connections approaching max (${var.db_connections_threshold}%)"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = var.db_connections_threshold

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-db-connections-alarm"
    Compliance = "PCI-DSS-10.6"
  })
}

# --- Database CPU Alarm ------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "db_cpu" {
  alarm_name          = "${var.project_name}-${var.environment}-db-cpu"
  alarm_description   = "RDS CPU utilization exceeds 80%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  alarm_actions = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-db-cpu-alarm"
  })
}

# --- Database Free Storage Alarm ---------------------------------------------

resource "aws_cloudwatch_metric_alarm" "db_storage" {
  alarm_name          = "${var.project_name}-${var.environment}-db-free-storage"
  alarm_description   = "RDS free storage below 10GB"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 10737418240 # 10 GB in bytes

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  alarm_actions = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-db-storage-alarm"
  })
}

# --- ECS CPU Alarm -----------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ecs_cpu" {
  alarm_name          = "${var.project_name}-${var.environment}-ecs-cpu"
  alarm_description   = "ECS service CPU exceeds 80%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 300
  statistic           = "Average"
  threshold           = 80

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.casino_api.name
  }

  alarm_actions = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-ecs-cpu-alarm"
  })
}

# --- ECS Memory Alarm --------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ecs_memory" {
  alarm_name          = "${var.project_name}-${var.environment}-ecs-memory"
  alarm_description   = "ECS service memory exceeds 85%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = 300
  statistic           = "Average"
  threshold           = 85

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.casino_api.name
  }

  alarm_actions = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-ecs-memory-alarm"
  })
}

# --- Redis Engine CPU Alarm --------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "redis_cpu" {
  alarm_name          = "${var.project_name}-${var.environment}-redis-cpu"
  alarm_description   = "Redis engine CPU exceeds 70%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "EngineCPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 70

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.main.id
  }

  alarm_actions = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-redis-cpu-alarm"
  })
}

# --- Redis Memory Alarm ------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "${var.project_name}-${var.environment}-redis-memory"
  alarm_description   = "Redis memory usage exceeds 80%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 80

  dimensions = {
    ReplicationGroupId = aws_elasticache_replication_group.main.id
  }

  alarm_actions = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-redis-memory-alarm"
  })
}

# --- Unhealthy Target Alarm --------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  alarm_name          = "${var.project_name}-${var.environment}-unhealthy-targets"
  alarm_description   = "ALB has unhealthy targets — potential service degradation"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.casino_api.arn_suffix
  }

  alarm_actions = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-unhealthy-targets-alarm"
    Compliance = "NJ-DGE-13:69O-1.3"
  })
}

# --- CloudWatch Dashboard ---------------------------------------------------
# PA PGCB §809a.10: Real-time dashboards for platform health

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.project_name}-${var.environment}-platform"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title = "API Request Count & Latency"
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.main.arn_suffix, { stat = "Sum" }],
            [".", "TargetResponseTime", ".", ".", { stat = "p99", yAxis = "right" }]
          ]
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title = "API Error Rates"
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.main.arn_suffix, { stat = "Sum" }],
            [".", "HTTPCode_Target_4XX_Count", ".", ".", { stat = "Sum" }]
          ]
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title = "ECS Service Health"
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.casino_api.name],
            [".", "MemoryUtilization", ".", ".", ".", "."]
          ]
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title = "Database Health"
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.main.identifier],
            [".", "DatabaseConnections", ".", ".", { yAxis = "right" }]
          ]
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          title = "Redis Cache"
          metrics = [
            ["AWS/ElastiCache", "EngineCPUUtilization", "ReplicationGroupId", aws_elasticache_replication_group.main.id],
            [".", "DatabaseMemoryUsagePercentage", ".", ".", { yAxis = "right" }]
          ]
          period = 300
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          title = "WAF Block Rate"
          metrics = [
            ["AWS/WAFV2", "BlockedRequests", "WebACL", aws_wafv2_web_acl.main.name, "Region", var.aws_region, "Rule", "ALL"],
            [".", "AllowedRequests", ".", ".", ".", ".", ".", "."]
          ]
          period = 300
          view   = "timeSeries"
        }
      }
    ]
  })
}
