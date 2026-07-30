# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# terraform/aws-pqc-alb.tf
# AWS Application Load Balancer with Post-Quantum Cipher Policy
# Chapter 24g: Post-Quantum Cryptography for iGaming
#
# Deploys:
#   - aws_lb                    (ALB, internet-facing)
#   - aws_lb_listener           (HTTPS:443 with PQC-capable SSL policy)
#   - aws_lb_target_group       (HTTP:8080 to backend instances)
#   - aws_security_group        (allow 443 in, egress open)
#
# AWS PQC SSL Policy Status (as of 2025):
#   AWS has introduced hybrid PQC cipher support in limited regions.
#   - "ELBSecurityPolicy-TLS13-1-3-2021-06" — TLS 1.3 only, no PQC KEM yet.
#   - "ELBSecurityPolicy-TLS13-1-3-FIPS-2023-04" — FIPS-validated TLS 1.3.
#   - Preview: "ELBSecurityPolicy-TLS13-1-3-PQC-2024" — includes
#     X25519Kyber768Draft00 hybrid KEM. Available in opt-in regions.
#     Check: https://docs.aws.amazon.com/elasticloadbalancing/latest/application/create-https-listener.html
#
#   Until the PQC policy is GA, use "ELBSecurityPolicy-TLS13-1-3-2021-06"
#   and plan migration to the PQC policy when available in your region.
#
# Prerequisites:
#   - AWS provider ~> 5.0
#   - ACM certificate ARN (var.acm_certificate_arn)
#   - VPC with public subnets (var.vpc_id, var.public_subnet_ids)
# =============================================================================

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.5.0"
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------
variable "environment" {
  description = "Deployment environment (e.g. production, staging)"
  type        = string
  default     = "production"
}

variable "vpc_id" {
  description = "VPC ID where the ALB will be deployed"
  type        = string
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs for the ALB (minimum 2 AZs)"
  type        = list(string)
}

variable "acm_certificate_arn" {
  description = "ARN of the ACM certificate for TLS termination"
  type        = string
}

variable "target_instance_ids" {
  description = "List of EC2 instance IDs to register with the target group"
  type        = list(string)
  default     = []
}

variable "alb_name" {
  description = "Name prefix for the ALB and associated resources"
  type        = string
  default     = "igaming-pqc"
}

variable "backend_port" {
  description = "Port on which the backend application listens"
  type        = number
  default     = 8080
}

# ---------------------------------------------------------------------------
# Security Group — ALB
# Allows HTTPS inbound; all egress (to reach targets)
# ---------------------------------------------------------------------------
resource "aws_security_group" "alb_sg" {
  name        = "${var.alb_name}-alb-sg"
  description = "Security group for iGaming PQC ALB — HTTPS inbound only"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS from anywhere"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  ingress {
    description = "HTTP redirect (sends 301 to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  egress {
    description = "All outbound (to reach target instances)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.alb_name}-alb-sg"
    Environment = var.environment
    Component   = "pqc-tls-termination"
  }
}

# ---------------------------------------------------------------------------
# Application Load Balancer
# ---------------------------------------------------------------------------
resource "aws_lb" "igaming_alb" {
  name               = "${var.alb_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb_sg.id]
  subnets            = var.public_subnet_ids

  # Enable deletion protection in production
  enable_deletion_protection = var.environment == "production"

  # Enable cross-zone load balancing
  enable_cross_zone_load_balancing = true

  # Access logs — uncomment and configure S3 bucket for production
  # access_logs {
  #   bucket  = aws_s3_bucket.alb_logs.id
  #   prefix  = var.alb_name
  #   enabled = true
  # }

  # Drop invalid HTTP headers (security hardening)
  drop_invalid_header_fields = true

  tags = {
    Name        = "${var.alb_name}-alb"
    Environment = var.environment
    Component   = "pqc-tls-termination"
  }
}

# ---------------------------------------------------------------------------
# Target Group
# ---------------------------------------------------------------------------
resource "aws_lb_target_group" "igaming_tg" {
  name        = "${var.alb_name}-tg"
  port        = var.backend_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "instance"

  # Health check configuration
  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 15
    path                = "/healthz"
    matcher             = "200"
    protocol            = "HTTP"
  }

  # Stickiness for session-sensitive iGaming workloads
  stickiness {
    type            = "lb_cookie"
    cookie_duration = 3600  # 1 hour — matches typical player session length
    enabled         = true
  }

  # Deregistration delay — give in-flight connections time to complete
  deregistration_delay = 30

  tags = {
    Name        = "${var.alb_name}-tg"
    Environment = var.environment
    Component   = "igaming-backend"
  }
}

# ---------------------------------------------------------------------------
# Target Group Attachment (for known instance IDs)
# In practice, use an auto-scaling group attachment instead
# ---------------------------------------------------------------------------
resource "aws_lb_target_group_attachment" "igaming_targets" {
  count            = length(var.target_instance_ids)
  target_group_arn = aws_lb_target_group.igaming_tg.arn
  target_id        = var.target_instance_ids[count.index]
  port             = var.backend_port
}

# ---------------------------------------------------------------------------
# HTTPS Listener — TLS termination with PQC-capable SSL policy
#
# SSL Policy options (ranked by PQC readiness):
#
#   "ELBSecurityPolicy-TLS13-1-3-PQC-2024"
#     The preview PQC policy. Includes X25519Kyber768Draft00 hybrid KEM
#     for TLS 1.3 key exchange. Not yet GA in all regions.
#     CHECK: aws elbv2 describe-ssl-policies --names ELBSecurityPolicy-TLS13-1-3-PQC-2024
#
#   "ELBSecurityPolicy-TLS13-1-3-2021-06"  ← current default
#     TLS 1.3 only with AES-256-GCM-SHA384 and CHACHA20-POLY1305-SHA256.
#     No PQC KEM, but modern and safe for classical threat model.
#
#   "ELBSecurityPolicy-TLS13-1-3-FIPS-2023-04"
#     FIPS-validated TLS 1.3 policy. Use in regulated iGaming jurisdictions
#     (e.g. New Jersey, Pennsylvania) that require FIPS 140-3 compliance.
#
# When AWS releases the PQC policy as GA, change ssl_policy to:
#   "ELBSecurityPolicy-TLS13-1-3-PQC-2024"
# ---------------------------------------------------------------------------
locals {
  # Toggle between PQC preview policy and standard TLS 1.3 policy.
  # Set use_pqc_policy = true once the policy is available in your region.
  use_pqc_policy = false
  ssl_policy = local.use_pqc_policy ? "ELBSecurityPolicy-TLS13-1-3-PQC-2024" : "ELBSecurityPolicy-TLS13-1-3-2021-06"
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.igaming_alb.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = local.ssl_policy
  certificate_arn   = var.acm_certificate_arn

  # Default action: forward to target group
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.igaming_tg.arn
  }

  tags = {
    Name        = "${var.alb_name}-https-listener"
    Environment = var.environment
    SSLPolicy   = local.ssl_policy
    PQCEnabled  = tostring(local.use_pqc_policy)
  }
}

# HTTP listener — redirect to HTTPS (308 preserves POST method)
resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.igaming_alb.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  tags = {
    Name        = "${var.alb_name}-http-redirect"
    Environment = var.environment
  }
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
output "alb_dns_name" {
  description = "DNS name of the ALB — create a CNAME record pointing to this value"
  value       = aws_lb.igaming_alb.dns_name
}

output "alb_arn" {
  description = "ARN of the Application Load Balancer"
  value       = aws_lb.igaming_alb.arn
}

output "alb_zone_id" {
  description = "Hosted zone ID of the ALB (for Route 53 alias records)"
  value       = aws_lb.igaming_alb.zone_id
}

output "https_listener_arn" {
  description = "ARN of the HTTPS listener"
  value       = aws_lb_listener.https.arn
}

output "target_group_arn" {
  description = "ARN of the backend target group"
  value       = aws_lb_target_group.igaming_tg.arn
}

output "ssl_policy_in_use" {
  description = "The AWS SSL policy currently applied to the listener"
  value       = local.ssl_policy
}

output "pqc_enabled" {
  description = "Whether the PQC SSL policy is active on this listener"
  value       = local.use_pqc_policy
}
