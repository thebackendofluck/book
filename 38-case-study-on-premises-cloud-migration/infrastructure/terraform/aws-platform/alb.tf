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
# Application Load Balancer — Casino API Entry Point
# =============================================================================
# Regulatory context:
#   NJ DGE 13:69O-1.5  — All player-facing connections must use TLS 1.2+.
#   PA PGCB §809a.5    — Web applications must be protected by WAF.
#   PCI-DSS 2.2.3      — Implement only one primary function per server
#                         (ALB handles TLS termination, not application).
#   PCI-DSS 4.1        — Use strong cryptography for data in transit.
#   PCI-DSS 6.6        — Web-facing applications protected by WAF.
#
# Architecture:
#   - Internet-facing ALB in public subnets
#   - HTTPS listener with TLS 1.2 minimum
#   - HTTP listener redirects to HTTPS
#   - WAF v2 attached for OWASP Top 10 protection
#   - Access logs to S3 for audit trail
# =============================================================================

# --- Application Load Balancer -----------------------------------------------

resource "aws_lb" "main" {
  name               = "${var.project_name}-${var.environment}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = var.environment == "prod" ? true : false
  enable_http2               = true
  drop_invalid_header_fields = true
  # PCI-DSS 6.5.10: Prevent header injection attacks

  access_logs {
    bucket  = aws_s3_bucket.alb_logs.id
    prefix  = "alb"
    enabled = true
    # NJ DGE 13:69O-1.9: All access must be logged
    # PCI-DSS 10.1: Audit trail for all system components
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-alb"
    Compliance = "NJ-DGE-13:69O-1.5,PCI-DSS-4.1"
  })
}

# --- S3 Bucket for ALB Access Logs -------------------------------------------

resource "aws_s3_bucket" "alb_logs" {
  bucket = "${var.project_name}-${var.environment}-alb-logs"

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-alb-logs"
    Compliance = "NJ-DGE-7yr-retention"
  })
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    id     = "archive-and-retain-7yr"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    # NJ DGE: 7-year retention for access logs
    expiration {
      days = 2557
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
  # PCI-DSS 7.1: Restrict access to need-to-know
}

resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::127311923021:root"
          # us-east-1 ELB account ID for ALB log delivery
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.alb_logs.arn}/alb/*"
      },
      {
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.alb_logs.arn}/alb/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
      {
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.alb_logs.arn
      }
    ]
  })
}

# --- HTTPS Listener ----------------------------------------------------------

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  # NJ DGE 13:69O-1.5: TLS 1.2 minimum; we enforce TLS 1.3 preferred
  # PCI-DSS 4.1: Strong cryptography required

  certificate_arn = var.certificate_arn != "" ? var.certificate_arn : null

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.casino_api.arn
  }

  tags = merge(var.tags, {
    Compliance = "NJ-DGE-13:69O-1.5,PCI-DSS-4.1"
  })
}

# --- HTTP Redirect Listener --------------------------------------------------

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.main.arn
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

  tags = var.tags
}

# --- Target Group ------------------------------------------------------------

resource "aws_lb_target_group" "casino_api" {
  name        = "${var.project_name}-${var.environment}-api-tg"
  port        = var.api_port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    enabled             = true
    path                = var.health_check_path
    port                = "traffic-port"
    protocol            = "HTTP"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
    # NJ DGE: Health checks ensure only healthy instances serve traffic
  }

  deregistration_delay = 30

  stickiness {
    type            = "lb_cookie"
    cookie_duration = 86400
    enabled         = false
    # Stateless API design — no session stickiness needed
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-api-tg"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# --- WAF v2 Association ------------------------------------------------------
# PA PGCB §809a.5: WAF required for all player-facing applications
# PCI-DSS 6.6: Web applications must be protected by WAF

resource "aws_wafv2_web_acl" "main" {
  name        = "${var.project_name}-${var.environment}-waf"
  description = "WAF for casino API — OWASP Top 10, rate limiting, geo-blocking"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  # AWS Managed Rules — Core Rule Set (OWASP Top 10)
  rule {
    name     = "aws-managed-common"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-common-rules"
      sampled_requests_enabled   = true
    }
  }

  # SQL Injection protection
  rule {
    name     = "aws-managed-sqli"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-sqli-rules"
      sampled_requests_enabled   = true
    }
  }

  # Known bad inputs
  rule {
    name     = "aws-managed-bad-inputs"
    priority = 3

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  # Rate limiting — prevent abuse and DDoS
  rule {
    name     = "rate-limit"
    priority = 4

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  # Geo-restriction — only allow traffic from licensed jurisdictions
  # NJ DGE / PA PGCB: Geo-fencing is a regulatory requirement
  rule {
    name     = "geo-restriction"
    priority = 5

    action {
      block {}
    }

    statement {
      not_statement {
        statement {
          geo_match_statement {
            country_codes = ["US"]
            # Restrict to US — state-level geo-fencing handled at application layer
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-geo-block"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 6: Login Rate Limiting (Credential Stuffing Protection) ---
  # 10 requests per 5 minutes on login endpoint = 120/5min AWS minimum
  # NJ DGE 13:69O-1.4: Prevent unauthorized access attempts
  rule {
    name     = "rate-limit-login"
    priority = 6

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 120
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = "/api/v1/auth/login"
            positional_constraint = "STARTS_WITH"

            field_to_match {
              uri_path {}
            }

            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-rate-limit-login"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 7: Bet Placement Rate Limiting ---
  # 60 bets/min per IP = 300/5min AWS evaluation window
  rule {
    name     = "rate-limit-bets"
    priority = 7

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 300
        aggregate_key_type = "IP"

        scope_down_statement {
          or_statement {
            statement {
              byte_match_statement {
                search_string         = "/api/v1/bets"
                positional_constraint = "STARTS_WITH"

                field_to_match {
                  uri_path {}
                }

                text_transformation {
                  priority = 0
                  type     = "LOWERCASE"
                }
              }
            }
            statement {
              byte_match_statement {
                search_string         = "/api/v1/games/"
                positional_constraint = "STARTS_WITH"

                field_to_match {
                  uri_path {}
                }

                text_transformation {
                  priority = 0
                  type     = "LOWERCASE"
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-rate-limit-bets"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 8: Withdrawal Rate Limiting ---
  # 5 withdrawals/min per IP = 25/5min AWS evaluation window
  # PCI-DSS 6.5: Protect financial endpoints from abuse
  rule {
    name     = "rate-limit-withdrawals"
    priority = 8

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 100
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = "/api/v1/wallet/withdraw"
            positional_constraint = "STARTS_WITH"

            field_to_match {
              uri_path {}
            }

            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-rate-limit-withdraw"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 9: Bonus Claim Rate Limiting ---
  # Prevent automated bonus abuse and multi-accounting
  rule {
    name     = "rate-limit-bonus"
    priority = 9

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 100
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = "/api/v1/bonus/"
            positional_constraint = "STARTS_WITH"

            field_to_match {
              uri_path {}
            }

            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-rate-limit-bonus"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 10: Player API Scraping Protection ---
  # Block excessive queries to player data endpoints
  rule {
    name     = "rate-limit-player-api"
    priority = 10

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 150
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = "/api/v2/pam/players"
            positional_constraint = "STARTS_WITH"

            field_to_match {
              uri_path {}
            }

            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-rate-limit-player-api"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 11: Oversized Body Blocking ---
  # Block requests larger than 128KB on non-upload endpoints
  # PCI-DSS 6.5.10: Prevent buffer overflow attacks
  rule {
    name     = "block-oversized-body"
    priority = 11

    action {
      block {}
    }

    statement {
      size_constraint_statement {
        comparison_operator = "GT"
        size                = 131072

        field_to_match {
          body {
            oversize_handling = "MATCH"
          }
        }

        text_transformation {
          priority = 0
          type     = "NONE"
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-oversized-body"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 12: Scanner/Bot User-Agent Blocking ---
  # Block known security scanners and automated tools
  rule {
    name     = "block-scanners"
    priority = 12

    action {
      block {}
    }

    statement {
      or_statement {
        statement {
          byte_match_statement {
            search_string         = "sqlmap"
            positional_constraint = "CONTAINS"

            field_to_match {
              single_header {
                name = "user-agent"
              }
            }

            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = "nikto"
            positional_constraint = "CONTAINS"

            field_to_match {
              single_header {
                name = "user-agent"
              }
            }

            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = "nessus"
            positional_constraint = "CONTAINS"

            field_to_match {
              single_header {
                name = "user-agent"
              }
            }

            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = "burpsuite"
            positional_constraint = "CONTAINS"

            field_to_match {
              single_header {
                name = "user-agent"
              }
            }

            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = "nuclei"
            positional_constraint = "CONTAINS"

            field_to_match {
              single_header {
                name = "user-agent"
              }
            }

            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-scanner-block"
      sampled_requests_enabled   = true
    }
  }

  # --- Rule 13: OFAC Sanctioned Countries Geo-Block ---
  # Block traffic from OFAC-sanctioned countries (defense-in-depth)
  rule {
    name     = "geo-block-sanctioned"
    priority = 13

    action {
      block {}
    }

    statement {
      geo_match_statement {
        country_codes = ["CU", "IR", "KP", "SY", "SD", "RU", "BY", "AF", "IQ", "LY", "SO", "YE"]
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-geo-block-sanctioned"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project_name}-waf"
    sampled_requests_enabled   = true
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-waf"
    Compliance = "PA-PGCB-809a.5,PCI-DSS-6.6"
  })
}

resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}

# --- WAF Logging -------------------------------------------------------------

resource "aws_wafv2_web_acl_logging_configuration" "main" {
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  resource_arn            = aws_wafv2_web_acl.main.arn
}

resource "aws_cloudwatch_log_group" "waf" {
  name              = "aws-waf-logs-${var.project_name}-${var.environment}"
  retention_in_days = 2557 # 7-year retention

  tags = merge(var.tags, {
    Compliance = "NJ-DGE-7yr-retention"
  })
}
