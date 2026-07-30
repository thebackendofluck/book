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
# Application Load Balancers -- Per-Service Isolation
# =============================================================================
# CONTEXT: On-premises, all services shared a pair of F5 load balancers with
# manually maintained configuration files. In the cloud, each service gets its
# own ALB with dedicated health checks, TLS termination, and access logging.
#
# This pattern was replicated for every migrated service: game-service,
# lookup-service, CRM integration, player tracking, risk alerting, and
# affiliate management. The for_each construct on security groups meant
# adding a new service's networking was a one-line change to a variable file.
# =============================================================================

data "aws_elb_service_account" "main" {}

# --- Game Service ALB ---------------------------------------------------------
# First service migrated. Handles game provider API integration and session
# management. Backend runs on port 8080 (Java/Spring Boot).

module "game_service_alb" {
  source  = "terraform-aws-modules/alb/aws"
  version = "5.13.0"

  name = "game-service-alb"

  load_balancer_type = "application"

  vpc_id          = var.default_vpc
  subnets         = var.default_subnets
  security_groups = [aws_security_group.alb_security_groups["game-service"].id]
  idle_timeout    = 600

  access_logs = {
    bucket = aws_s3_bucket.alb_logs["game-service"].bucket
  }

  target_groups = [
    {
      name_prefix      = "gmesvc"
      backend_protocol = "HTTP"
      backend_port     = 8080
      target_type      = "instance"
      health_check = {
        enabled             = true
        interval            = 30
        path                = "/health"
        port                = "traffic-port"
        healthy_threshold   = 5
        unhealthy_threshold = 2
        timeout             = 5
        protocol            = "HTTP"
        matcher             = "404"
      }
    }
  ]

  https_listeners = [
    {
      port               = 443
      protocol           = "HTTPS"
      certificate_arn    = local.acm_wildcard_cert
      target_group_index = 0
      action_type        = "fixed-response"
      fixed_response = {
        content_type = "text/plain"
        message_body = "Not Found"
        status_code  = "404"
      }
    }
  ]

  tags = {
    App         = "game-service"
    Environment = var.env
    Name        = "game_service_alb"
    Terraform   = "true"
  }
}

resource "aws_lb_target_group_attachment" "game_service_alb_attach" {
  target_group_arn = module.game_service_alb.target_group_arns[0]
  target_id        = module.game_service_instance.id[0]
  port             = 8080
}

# --- Risk Alert ALB -----------------------------------------------------------
# Real-time risk and fraud detection service. Receives event streams from MSK
# (Kafka) and exposes an API for the back-office to query risk scores.
# HTTP-to-HTTPS redirect enforced at the ALB layer.

module "risk_alert_alb" {
  source  = "terraform-aws-modules/alb/aws"
  version = "= 5.13.0"

  name = "risk-alert-alb"

  load_balancer_type = "application"

  vpc_id          = var.default_vpc
  subnets         = var.default_subnets
  security_groups = [aws_security_group.risk_alert_alb.id]

  access_logs = {
    bucket = aws_s3_bucket.alb_logs["risk-alert-prod"].bucket
  }

  target_groups = [
    {
      name_prefix      = "risk"
      backend_protocol = "HTTP"
      backend_port     = 8888
      target_type      = "instance"
      health_check = {
        enabled             = true
        interval            = 30
        path                = "/health"
        port                = "traffic-port"
        healthy_threshold   = 5
        unhealthy_threshold = 2
        timeout             = 5
        protocol            = "HTTP"
        matcher             = "200"
      }
    }
  ]

  # Force HTTPS -- no unencrypted traffic in production
  http_tcp_listeners = [
    {
      port        = 80
      protocol    = "HTTP"
      action_type = "redirect"
      redirect = {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  ]

  https_listeners = [
    {
      port               = 443
      protocol           = "HTTPS"
      certificate_arn    = local.acm_wildcard_cert
      target_group_index = 0
    }
  ]

  tags = {
    App         = "risk-alert"
    Environment = var.env
    Name        = "risk-alert-alb"
    Terraform   = "true"
  }
}

resource "aws_lb_target_group_attachment" "risk_alert_alb" {
  target_group_arn = module.risk_alert_alb.target_group_arns[0]
  target_id        = module.risk_alert.id[0]
  port             = 8888
}
