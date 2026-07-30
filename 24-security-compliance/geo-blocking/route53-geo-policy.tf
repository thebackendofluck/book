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
# Terraform: Route 53 Geolocation Routing for iGaming Geo-Blocking
# =============================================================================
#
# Implements a two-tier DNS strategy:
#   1. Allowed jurisdictions → Route to production infrastructure
#   2. Blocked jurisdictions → Route to a "geo-blocked" compliance server
#      that returns HTTP 451 (Unavailable For Legal Reasons)
#
# This is the outermost layer of the geo-blocking stack. It prevents TCP
# connections from even reaching your production infrastructure for blocked
# countries, reducing attack surface and bandwidth costs.
#
# Usage:
#   terraform init
#   terraform plan -var="hosted_zone_id=Z1234ABCD" -var="domain_name=casino.example.com"
#   terraform apply
#
# Requirements:
#   - Terraform >= 1.5
#   - AWS provider >= 5.0
#   - An ACM certificate in us-east-1 (for CloudFront)
#   - A Route 53 hosted zone for your domain
# =============================================================================

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.primary_region
  default_tags {
    tags = {
      Project    = "igaming-geo-blocking"
      ManagedBy  = "terraform"
      Compliance = "geo-restriction"
    }
  }
}

# CloudFront must be provisioned in us-east-1 (AWS requirement)
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

# =============================================================================
# Variables
# =============================================================================
variable "hosted_zone_id" {
  description = "Route 53 hosted zone ID for the casino domain"
  type        = string
}

variable "domain_name" {
  description = "Primary domain name (e.g., casino.example.com)"
  type        = string
}

variable "primary_region" {
  description = "Primary AWS region for infrastructure"
  type        = string
  default     = "eu-west-1"
}

variable "compliance_page_bucket" {
  description = "S3 bucket name for the geo-block compliance page"
  type        = string
  default     = "casino-geo-block-compliance"
}

variable "alb_dns_name" {
  description = "DNS name of the production Application Load Balancer"
  type        = string
}

variable "alb_hosted_zone_id" {
  description = "Hosted zone ID of the production ALB"
  type        = string
}

# Countries where the operator holds no license or gambling is prohibited.
# ISO 3166-1 alpha-2 codes.
variable "blocked_country_codes" {
  description = "List of ISO 3166-1 alpha-2 country codes to block"
  type        = list(string)
  default = [
    "AE", # United Arab Emirates — Federal Law No. 6 of 2018
    "SA", # Saudi Arabia — Anti-Cybercrime Law + Islamic law
    "QA", # Qatar — Law No. 14 of 2014
    "KW", # Kuwait — Law No. 31 of 1970
    "BH", # Bahrain — Decree-Law No. 15 of 1976
    "OM", # Oman — Penal Code Article 263
    "YE", # Yemen — Islamic Penal Code provisions
    "LY", # Libya — Penal Code Chapter 4
    "SD", # Sudan — Gambling Act 1974 + Sharia codification
    "CN", # China — Criminal Law Article 303
    "KP", # North Korea — complete prohibition
    "KH", # Cambodia — Sub-Decree No. 176 of 2019
    "DZ", # Algeria — Ordinance No. 75-58
    "MA", # Morocco — Dahir gambling code
    "PK", # Pakistan — Prevention of Gambling Act 1977
    "BD", # Bangladesh — Public Gambling Act 1867
    "AF", # Afghanistan — Penal Code Article 277
    "IQ", # Iraq — Penal Code No. 111 of 1969
    "IR", # Iran — Islamic Penal Code Chapter 20
  ]
}

# =============================================================================
# S3 bucket: static compliance/geo-block page
# Serves HTTP 451 with a user-friendly explanation.
# =============================================================================
resource "aws_s3_bucket" "geo_block_compliance" {
  bucket = var.compliance_page_bucket
}

resource "aws_s3_bucket_public_access_block" "geo_block_compliance" {
  bucket                  = aws_s3_bucket.geo_block_compliance.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "geo_block_compliance" {
  bucket = aws_s3_bucket.geo_block_compliance.id

  index_document { suffix = "index.html" }
  error_document { key = "index.html" }
}

resource "aws_s3_bucket_policy" "geo_block_public_read" {
  bucket = aws_s3_bucket.geo_block_compliance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.geo_block_compliance.arn}/*"
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.geo_block_compliance]
}

resource "aws_s3_object" "geo_block_index" {
  bucket       = aws_s3_bucket.geo_block_compliance.id
  key          = "index.html"
  content_type = "text/html"
  content      = <<-HTML
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Service Unavailable In Your Region</title>
      <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #0f172a; color: #e2e8f0; display: flex; justify-content: center;
               align-items: center; min-height: 100vh; margin: 0; }
        .container { max-width: 600px; padding: 2rem; text-align: center; }
        h1 { font-size: 1.5rem; color: #f87171; }
        p  { color: #94a3b8; line-height: 1.6; }
        .code { font-size: 4rem; font-weight: 700; color: #334155; }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="code">451</div>
        <h1>Service Not Available In Your Region</h1>
        <p>
          Online gambling services are not available in your jurisdiction due to
          legal restrictions. If you believe you have received this message in error,
          please contact support.
        </p>
      </div>
    </body>
    </html>
  HTML
}

# =============================================================================
# CloudFront distribution for the compliance page
# Needed because Route 53 alias records require CloudFront/ALB endpoints.
# =============================================================================
resource "aws_cloudfront_distribution" "geo_block_compliance" {
  provider = aws.us_east_1

  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100" # US/EU edge only (compliance page is lightweight)
  comment             = "Geo-block compliance page — returns 451 for blocked jurisdictions"
  http_version        = "http2and3"

  origin {
    domain_name = aws_s3_bucket_website_configuration.geo_block_compliance.website_endpoint
    origin_id   = "s3-compliance-page"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # S3 website endpoint doesn't support HTTPS
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    allowed_methods  = ["GET", "HEAD"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "s3-compliance-page"

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    viewer_protocol_policy = "redirect-to-https"
    min_ttl                = 0
    default_ttl            = 300 # 5-minute cache — low, so we can update the page quickly
    max_ttl                = 3600
    compress               = true

    # Return 451 instead of 200 (CloudFront response headers policy)
    # Note: Response code manipulation requires Lambda@Edge; see the comment below.
    # The S3 page returns 200 but the nginx layer returns 451 for the actual app.
    # For pure DNS-layer enforcement the 2xx from S3 is acceptable.
  }

  restrictions {
    geo_restriction {
      restriction_type = "none" # The compliance page should be globally accessible
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = {
    Purpose = "geo-block-compliance-page"
  }
}

# =============================================================================
# Route 53: Geolocation routing records
#
# Strategy:
#   - One BLOCKED record per blocked country → compliance CloudFront distribution
#   - One DEFAULT record → production ALB
#
# Route 53 evaluates geolocation records in order:
#   1. Country-specific match
#   2. Continent-level match
#   3. Default (*)
# =============================================================================

# Default record: all unlisted countries route to production infrastructure
resource "aws_route53_record" "production_default" {
  zone_id        = var.hosted_zone_id
  name           = var.domain_name
  type           = "A"
  set_identifier = "production-default"

  geolocation_routing_policy {
    country = "*" # Catch-all for all locations not matched by a country-specific record
  }

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_hosted_zone_id
    evaluate_target_health = true
  }
}

# Blocked country records: each blocked country gets its own record
resource "aws_route53_record" "geo_blocked" {
  for_each = toset(var.blocked_country_codes)

  zone_id        = var.hosted_zone_id
  name           = var.domain_name
  type           = "A"
  set_identifier = "geo-blocked-${lower(each.value)}"

  geolocation_routing_policy {
    country = each.value
  }

  alias {
    name                   = aws_cloudfront_distribution.geo_block_compliance.domain_name
    zone_id                = aws_cloudfront_distribution.geo_block_compliance.hosted_zone_id
    evaluate_target_health = false
  }
}

# Health check on production ALB
resource "aws_route53_health_check" "production_alb" {
  fqdn              = var.alb_dns_name
  port              = 443
  type              = "HTTPS"
  resource_path     = "/healthz"
  failure_threshold = 3
  request_interval  = 30

  tags = {
    Name = "production-alb-health-check"
  }
}

# =============================================================================
# CloudWatch: monitor geo-block effectiveness
# =============================================================================
resource "aws_cloudwatch_log_group" "geo_block_logs" {
  name              = "/igaming/geo-blocking/dns-layer"
  retention_in_days = 365 # Retain 1 year for regulatory audit
}

resource "aws_cloudwatch_metric_alarm" "geo_block_spike" {
  alarm_name          = "geo-block-request-spike"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Requests"
  namespace           = "AWS/CloudFront"
  period              = 300
  statistic           = "Sum"
  threshold           = 10000 # Alert if >10k requests hit the compliance page in 5 min
  alarm_description   = "High volume of geo-blocked requests — possible VPN bypass attempt or misconfiguration"

  dimensions = {
    DistributionId = aws_cloudfront_distribution.geo_block_compliance.id
    Region         = "Global"
  }

  alarm_actions = [aws_sns_topic.geo_block_alerts.arn]
}

resource "aws_sns_topic" "geo_block_alerts" {
  name = "geo-block-compliance-alerts"
}

# =============================================================================
# Outputs
# =============================================================================
output "compliance_cloudfront_domain" {
  description = "CloudFront domain for the geo-block compliance page"
  value       = aws_cloudfront_distribution.geo_block_compliance.domain_name
}

output "blocked_countries_count" {
  description = "Number of countries blocked via Route 53 geolocation routing"
  value       = length(var.blocked_country_codes)
}

output "production_record_name" {
  description = "Route 53 record for production traffic"
  value       = aws_route53_record.production_default.fqdn
}
