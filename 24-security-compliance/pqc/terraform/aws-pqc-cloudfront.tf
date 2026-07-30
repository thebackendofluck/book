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
# terraform/aws-pqc-cloudfront.tf
# AWS CloudFront Distribution with Post-Quantum TLS for iGaming
# Chapter 24g: Post-Quantum Cryptography for iGaming
#
# Deploys:
#   - aws_cloudfront_distribution  (global CDN with TLS 1.3)
#   - Associated security policy using the most restrictive modern TLS
#
# CloudFront PQC Status (as of 2025):
#   CloudFront automatically negotiates X25519Kyber768 hybrid key exchange
#   when both the client and CloudFront's edge PoP support it. No explicit
#   configuration is required — CloudFront enables PQC hybrid by default
#   for TLS 1.3 connections.
#
#   Relevant CloudFront security policies:
#   - "TLSv1.2_2021"          — TLS 1.2+, no PQC
#   - "TLSv1.2_2019"          — TLS 1.2+, no PQC
#   - "TLSv1"                 — Legacy; do not use
#   - Custom security policy  — Use "TLSv1.2_2021" or set minimum_protocol_version
#                               to "TLSv1.2" or "TLSv1.3_2022" (where available)
#
#   CloudFront does not expose a per-distribution PQC toggle; PQC KEM
#   negotiation is handled transparently at the edge. Monitor via:
#   aws cloudfront get-distribution --id <ID> | jq .Distribution.DistributionConfig.ViewerCertificate
#
# Prerequisites:
#   - ACM certificate in us-east-1 (CloudFront requires us-east-1)
#   - S3 bucket or HTTP origin for backend
# =============================================================================

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# CloudFront requires ACM certs in us-east-1
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------
variable "cf_environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}

variable "cf_domain_name" {
  description = "Primary domain name for the CloudFront distribution (e.g. www.igaming.example.com)"
  type        = string
  default     = "www.igaming.example.com"
}

variable "cf_additional_domains" {
  description = "Additional domain aliases (e.g. igaming.example.com)"
  type        = list(string)
  default     = []
}

variable "cf_acm_certificate_arn" {
  description = "ACM certificate ARN in us-east-1 for CloudFront"
  type        = string
}

variable "origin_domain_name" {
  description = "Origin domain name — ALB DNS name or API Gateway domain"
  type        = string
}

variable "origin_id" {
  description = "Logical identifier for the origin in CloudFront"
  type        = string
  default     = "igaming-api-origin"
}

variable "cf_price_class" {
  description = "CloudFront price class (PriceClass_100=NA+EU, PriceClass_200=+Asia, PriceClass_All=global)"
  type        = string
  default     = "PriceClass_100"
}

variable "waf_web_acl_id" {
  description = "ARN of the AWS WAF Web ACL to associate with this distribution (optional)"
  type        = string
  default     = null
}

# ---------------------------------------------------------------------------
# CloudFront Origin Access Control (for ALB origins)
# ---------------------------------------------------------------------------
resource "aws_cloudfront_origin_access_control" "api" {
  name                              = "${var.cf_environment}-igaming-api-oac"
  description                       = "OAC for iGaming API ALB origin"
  origin_access_control_origin_type = "custom"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ---------------------------------------------------------------------------
# CloudFront Cache Policy — minimal caching for dynamic API responses
# ---------------------------------------------------------------------------
resource "aws_cloudfront_cache_policy" "api_dynamic" {
  name        = "${var.cf_environment}-igaming-api-nocache"
  comment     = "Pass-through policy for dynamic iGaming API endpoints"
  default_ttl = 0
  max_ttl     = 0
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "all"
    }
    headers_config {
      header_behavior = "whitelist"
      headers {
        items = ["Authorization", "X-Player-ID", "X-Session-Token"]
      }
    }
    query_strings_config {
      query_string_behavior = "all"
    }
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true
  }
}

# Cache policy for static game assets
resource "aws_cloudfront_cache_policy" "static_assets" {
  name        = "${var.cf_environment}-igaming-static-cache"
  comment     = "Long-lived cache for immutable game assets (JS, CSS, images)"
  default_ttl = 86400    # 1 day
  max_ttl     = 31536000 # 1 year
  min_ttl     = 3600     # 1 hour

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      # Cache-bust via ?v=hash query param only
      query_string_behavior = "whitelist"
      query_strings {
        items = ["v", "version"]
      }
    }
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true
  }
}

# ---------------------------------------------------------------------------
# CloudFront Origin Request Policy — forward necessary headers to origin
# ---------------------------------------------------------------------------
resource "aws_cloudfront_origin_request_policy" "api_forward" {
  name    = "${var.cf_environment}-igaming-api-forward"
  comment = "Forward player and session headers to the iGaming API origin"

  cookies_config {
    cookie_behavior = "all"
  }
  headers_config {
    header_behavior = "whitelist"
    headers {
      items = [
        "Authorization",
        "CloudFront-Viewer-Country",
        "CloudFront-Viewer-City",
        "X-Forwarded-For",
        "X-Real-IP",
        "Host",
      ]
    }
  }
  query_strings_config {
    query_string_behavior = "all"
  }
}

# ---------------------------------------------------------------------------
# CloudFront Distribution
#
# TLS Configuration notes:
#   minimum_protocol_version = "TLSv1.2_2021"
#     Enforces TLS 1.2+ between viewer and CloudFront edge.
#     CloudFront automatically upgrades to TLS 1.3 (and PQC hybrid KEM)
#     when the client supports it. No additional configuration is required
#     to enable PQC — CloudFront handles this at the PoP level.
#
#   ssl_support_method = "sni-only"
#     SNI-only mode is required for custom domain certificates.
#     "vip" (dedicated IP) is no longer needed and costs extra.
#
#   Origin protocol / minimum TLS between CloudFront and ALB:
#     Set origin_ssl_protocols to ["TLSv1.2"] minimum.
#     CloudFront→origin connection is within AWS backbone; PQC here
#     provides limited additional value vs viewer-to-edge PQC.
# ---------------------------------------------------------------------------
resource "aws_cloudfront_distribution" "igaming" {
  aliases = concat([var.cf_domain_name], var.cf_additional_domains)
  comment = "iGaming PQC-enabled CDN distribution (${var.cf_environment})"
  enabled = true

  # IPv6 support — modern clients including mobile apps
  is_ipv6_enabled = true

  # Price class — start with NA+EU; expand to PriceClass_All for global iGaming
  price_class = var.cf_price_class

  # WAF association (optional — highly recommended for iGaming)
  web_acl_id = var.waf_web_acl_id

  # HTTP version — enable HTTP/2 and HTTP/3 (QUIC)
  # HTTP/3 uses QUIC which also benefits from PQC hybrid key exchange when
  # RFC 9001 PQC extensions are supported by the client
  http_version = "http2and3"

  # Default root object (for SPA frontends)
  default_root_object = "index.html"

  # ---------------------------------------------------------------------------
  # Origin — iGaming API / ALB
  # ---------------------------------------------------------------------------
  origin {
    domain_name = var.origin_domain_name
    origin_id   = var.origin_id

    # Custom origin settings (for ALB / EC2 origins)
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"

      # TLS between CloudFront edge and origin (ALB)
      # "TLSv1.2" means TLS 1.2 minimum; CloudFront will use TLS 1.3 if supported
      origin_ssl_protocols = ["TLSv1.2"]

      # These settings control CloudFront→origin TLS timeouts
      origin_read_timeout    = 30
      origin_keepalive_timeout = 5
    }

    # Custom headers sent to origin to verify requests come via CloudFront
    custom_header {
      name  = "X-CloudFront-Secret"
      value = "replace-with-secretsmanager-value"  # Use aws_secretsmanager_secret in prod
    }
  }

  # ---------------------------------------------------------------------------
  # Default cache behavior — API / dynamic content (no caching)
  # ---------------------------------------------------------------------------
  default_cache_behavior {
    target_origin_id         = var.origin_id
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = true
    cache_policy_id          = aws_cloudfront_cache_policy.api_dynamic.id
    origin_request_policy_id = aws_cloudfront_origin_request_policy.api_forward.id

    # Function associations for edge logic (e.g. JWT validation at edge)
    # function_association {
    #   event_type   = "viewer-request"
    #   function_arn = aws_cloudfront_function.jwt_verify.arn
    # }
  }

  # ---------------------------------------------------------------------------
  # Ordered cache behavior — static game assets (long-lived cache)
  # ---------------------------------------------------------------------------
  ordered_cache_behavior {
    path_pattern             = "/static/*"
    target_origin_id         = var.origin_id
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = true
    cache_policy_id          = aws_cloudfront_cache_policy.static_assets.id
  }

  # ---------------------------------------------------------------------------
  # Ordered cache behavior — WebSocket pass-through (live casino, betting)
  # CloudFront supports WebSocket connections natively since 2018.
  # ---------------------------------------------------------------------------
  ordered_cache_behavior {
    path_pattern           = "/ws/*"
    target_origin_id       = var.origin_id
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    compress               = false
    cache_policy_id        = aws_cloudfront_cache_policy.api_dynamic.id
  }

  # ---------------------------------------------------------------------------
  # TLS / Viewer certificate
  #
  # minimum_protocol_version controls the viewer-to-edge TLS policy.
  # "TLSv1.2_2021" is the recommended minimum as of 2025:
  #   - Supports TLS 1.2 and 1.3
  #   - CloudFront automatically negotiates PQC hybrid KEM when available
  #   - Cipher suites: AES-128-GCM, AES-256-GCM, CHACHA20-POLY1305
  #
  # To observe PQC negotiation: curl --tlsv1.3 --curves X25519Kyber768Draft00 \
  #   https://<distribution-domain> -v 2>&1 | grep "SSL connection"
  # ---------------------------------------------------------------------------
  viewer_certificate {
    acm_certificate_arn            = var.cf_acm_certificate_arn
    ssl_support_method             = "sni-only"
    minimum_protocol_version       = "TLSv1.2_2021"
  }

  # Geographic restrictions (iGaming compliance — restrict sanctioned countries)
  restrictions {
    geo_restriction {
      restriction_type = "blacklist"
      # Adjust based on iGaming licence jurisdiction requirements
      locations = ["KP", "IR", "CU", "SY", "RU"]
    }
  }

  tags = {
    Name        = "${var.cf_environment}-igaming-cf"
    Environment = var.cf_environment
    Component   = "cdn-pqc"
    PQCNotes    = "PQC hybrid KEM negotiated automatically by CloudFront edge for TLS1.3 clients"
  }
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID (needed for cache invalidation)"
  value       = aws_cloudfront_distribution.igaming.id
}

output "cloudfront_domain_name" {
  description = "CloudFront-assigned domain name (create CNAME alias to this)"
  value       = aws_cloudfront_distribution.igaming.domain_name
}

output "cloudfront_hosted_zone_id" {
  description = "Hosted zone ID for Route 53 alias records"
  value       = aws_cloudfront_distribution.igaming.hosted_zone_id
}

output "cloudfront_arn" {
  description = "ARN of the CloudFront distribution"
  value       = aws_cloudfront_distribution.igaming.arn
}

output "tls_minimum_version" {
  description = "Minimum TLS version negotiated with viewers"
  value       = "TLSv1.2_2021 (CloudFront auto-negotiates PQC hybrid KEM for TLS 1.3 clients)"
}
