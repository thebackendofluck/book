# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# CloudFlare DDoS Protection for iGaming Platforms
# GLI-GSF Phase 3 - Network Protection Controls
#
# Provisions CloudFlare WAF rules, rate limiting, bot management,
# IP reputation filtering, and challenge pages for gambling platforms.
#
# GLI-GSF-5 Reference: Section 3.4 - Platform Availability Controls
# OGIS-5 Requirement: 99.9% uptime with DDoS mitigation
#
# Usage:
#   terraform init
#   terraform plan -var-file="production.tfvars"
#   terraform apply -var-file="production.tfvars"
#
# Required Variables (in production.tfvars):
#   cloudflare_api_token = "your-api-token"
#   cloudflare_zone_id   = "your-zone-id"
#   domain               = "casino.example.com"
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }

  backend "s3" {
    bucket       = "acme-casino-terraform-state"
    key          = "cloudflare/ddos-protection.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    # S3-native state locking (Terraform >= 1.11, replaces DynamoDB locking)
    use_lockfile = true
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------
variable "cloudflare_api_token" {
  description = "CloudFlare API token with Zone:Edit permissions"
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "CloudFlare Zone ID for the gambling domain"
  type        = string
}

variable "domain" {
  description = "Primary domain (e.g., casino.example.com)"
  type        = string
}

variable "environment" {
  description = "Environment name (production, staging)"
  type        = string
  default     = "production"
}

# GLI-GSF rate limits per endpoint category
variable "rate_limit_login" {
  description = "Max login attempts per minute per IP"
  type        = number
  default     = 10
}

variable "rate_limit_deposit" {
  description = "Max deposit requests per minute per IP"
  type        = number
  default     = 5
}

variable "rate_limit_game_round" {
  description = "Max game round initiations per minute per IP"
  type        = number
  default     = 60
}

variable "rate_limit_api_general" {
  description = "Max general API requests per minute per IP"
  type        = number
  default     = 120
}

variable "rate_limit_registration" {
  description = "Max registration attempts per hour per IP"
  type        = number
  default     = 3
}

variable "allowed_countries" {
  description = "ISO country codes where gambling is licensed"
  type        = list(string)
  default     = ["GB", "MT", "GI", "IE", "SE", "DK", "ES", "PT", "IT", "DE"]
}

variable "blocked_countries" {
  description = "ISO country codes where gambling is prohibited"
  type        = list(string)
  default     = ["US", "AU", "FR", "TR", "CN", "KP", "IR", "SY", "CU"]
}

variable "origin_ips" {
  description = "Origin server IPs to protect (never expose directly)"
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

# ---------------------------------------------------------------------------
# Data Sources
# ---------------------------------------------------------------------------
data "cloudflare_zone" "casino" {
  zone_id = var.cloudflare_zone_id
}

# ---------------------------------------------------------------------------
# WAF Managed Rulesets
# GLI-GSF requires WAF with OWASP Core Rule Set
# ---------------------------------------------------------------------------
resource "cloudflare_ruleset" "waf_managed" {
  zone_id     = var.cloudflare_zone_id
  name        = "GLI-GSF WAF Managed Rules"
  description = "OWASP CRS and CloudFlare managed rules for gambling platform"
  kind        = "zone"
  phase       = "http_request_firewall_managed"

  # CloudFlare OWASP Core Ruleset
  rules {
    action = "execute"
    action_parameters {
      id = "efb7b8c949ac4650a09736fc376e9aee" # CloudFlare OWASP CRS
      overrides {
        # Set paranoia level 2 for gambling platforms (stricter)
        categories {
          category = "paranoia-level-2"
          action   = "block"
        }
        # Block SQL injection (critical for player databases)
        categories {
          category = "sqli"
          action   = "block"
        }
        # Block XSS (protect player sessions)
        categories {
          category = "xss"
          action   = "block"
        }
        # Block RCE (protect game servers)
        categories {
          category = "rce"
          action   = "block"
        }
      }
    }
    expression  = "true"
    description = "OWASP CRS - GLI-GSF-5 Section 4.2 compliance"
    enabled     = true
  }

  # CloudFlare Managed Ruleset
  rules {
    action = "execute"
    action_parameters {
      id = "4814384a9e5d4991b9815dcfc25d2f1f" # CloudFlare Managed Rules
    }
    expression  = "true"
    description = "CloudFlare managed rules for known attack patterns"
    enabled     = true
  }
}

# ---------------------------------------------------------------------------
# Rate Limiting Rules
# GLI-GSF requires rate limiting on authentication, financial, and game endpoints
# ---------------------------------------------------------------------------
resource "cloudflare_ruleset" "rate_limiting" {
  zone_id     = var.cloudflare_zone_id
  name        = "GLI-GSF Rate Limiting"
  description = "Endpoint-specific rate limiting for gambling platform"
  kind        = "zone"
  phase       = "http_ratelimit"

  # Login endpoint rate limiting (brute force protection)
  rules {
    action = "block"
    action_parameters {
      response {
        status_code  = 429
        content      = "{\"error\":\"rate_limited\",\"message\":\"Too many login attempts. Try again later.\",\"retry_after\":60}"
        content_type = "application/json"
      }
    }
    ratelimit {
      characteristics     = ["ip.src", "cf.colo.id"]
      period              = 60
      requests_per_period = var.rate_limit_login
      mitigation_timeout  = 60
    }
    expression  = "(http.request.uri.path contains \"/auth/login\" or http.request.uri.path contains \"/api/v1/login\") and http.request.method eq \"POST\""
    description = "Login rate limit: ${var.rate_limit_login}/min per IP (OGIS-2 brute force protection)"
    enabled     = true
  }

  # Registration rate limiting (anti-multi-accounting)
  rules {
    action = "block"
    action_parameters {
      response {
        status_code  = 429
        content      = "{\"error\":\"rate_limited\",\"message\":\"Registration limit reached.\"}"
        content_type = "application/json"
      }
    }
    ratelimit {
      characteristics     = ["ip.src"]
      period              = 3600
      requests_per_period = var.rate_limit_registration
      mitigation_timeout  = 3600
    }
    expression  = "(http.request.uri.path contains \"/register\" or http.request.uri.path contains \"/signup\") and http.request.method eq \"POST\""
    description = "Registration rate limit: ${var.rate_limit_registration}/hour per IP (anti-multi-accounting)"
    enabled     = true
  }

  # Deposit/withdrawal rate limiting (financial transaction protection)
  rules {
    action = "block"
    action_parameters {
      response {
        status_code  = 429
        content      = "{\"error\":\"rate_limited\",\"message\":\"Financial transaction rate limit exceeded.\"}"
        content_type = "application/json"
      }
    }
    ratelimit {
      characteristics     = ["ip.src"]
      period              = 60
      requests_per_period = var.rate_limit_deposit
      mitigation_timeout  = 120
    }
    expression  = "(http.request.uri.path contains \"/deposit\" or http.request.uri.path contains \"/withdraw\" or http.request.uri.path contains \"/cashout\") and http.request.method eq \"POST\""
    description = "Financial endpoint rate limit: ${var.rate_limit_deposit}/min (OGIS-2 fraud prevention)"
    enabled     = true
  }

  # Game round rate limiting (anti-bot for rapid game play)
  rules {
    action = "block"
    action_parameters {
      response {
        status_code  = 429
        content      = "{\"error\":\"rate_limited\",\"message\":\"Game round rate limit exceeded.\"}"
        content_type = "application/json"
      }
    }
    ratelimit {
      characteristics     = ["ip.src"]
      period              = 60
      requests_per_period = var.rate_limit_game_round
      mitigation_timeout  = 30
    }
    expression  = "(http.request.uri.path contains \"/game/round\" or http.request.uri.path contains \"/game/spin\" or http.request.uri.path contains \"/game/bet\") and http.request.method eq \"POST\""
    description = "Game round rate limit: ${var.rate_limit_game_round}/min (OGIS-4 bot prevention)"
    enabled     = true
  }

  # General API rate limiting
  rules {
    action = "block"
    action_parameters {
      response {
        status_code  = 429
        content      = "{\"error\":\"rate_limited\",\"message\":\"API rate limit exceeded.\"}"
        content_type = "application/json"
      }
    }
    ratelimit {
      characteristics     = ["ip.src"]
      period              = 60
      requests_per_period = var.rate_limit_api_general
      mitigation_timeout  = 60
    }
    expression  = "http.request.uri.path contains \"/api/\""
    description = "General API rate limit: ${var.rate_limit_api_general}/min per IP"
    enabled     = true
  }
}

# ---------------------------------------------------------------------------
# Bot Management
# GLI-GSF OGIS-4 requires 99%+ bot block rate
# ---------------------------------------------------------------------------
resource "cloudflare_bot_management" "casino" {
  zone_id                = var.cloudflare_zone_id
  enable_js              = true
  fight_mode             = true
  using_latest_model     = true
  suppress_session_score = false
}

# Custom bot blocking rules
resource "cloudflare_ruleset" "bot_rules" {
  zone_id     = var.cloudflare_zone_id
  name        = "GLI-GSF Bot Management Rules"
  description = "Bot detection and blocking for gambling platform"
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  # Block definitely automated traffic on sensitive endpoints
  rules {
    action      = "block"
    expression  = "(cf.bot_management.score lt 10) and (http.request.uri.path contains \"/api/v1/\" or http.request.uri.path contains \"/game/\")"
    description = "Block definite bots on API/game endpoints (OGIS-4)"
    enabled     = true
  }

  # Challenge likely automated traffic on odds endpoints
  rules {
    action      = "managed_challenge"
    expression  = "(cf.bot_management.score lt 30) and (http.request.uri.path contains \"/odds\" or http.request.uri.path contains \"/lines\" or http.request.uri.path contains \"/markets\")"
    description = "Challenge likely bots on odds/lines endpoints (anti-scraping)"
    enabled     = true
  }

  # Challenge suspicious traffic on financial endpoints
  rules {
    action      = "managed_challenge"
    expression  = "(cf.bot_management.score lt 50) and (http.request.uri.path contains \"/deposit\" or http.request.uri.path contains \"/withdraw\" or http.request.uri.path contains \"/bonus\")"
    description = "Challenge suspicious traffic on financial endpoints"
    enabled     = true
  }

  # Block known gambling-specific bot user agents
  rules {
    action      = "block"
    expression  = "(http.user_agent contains \"oddsscraper\" or http.user_agent contains \"betbot\" or http.user_agent contains \"arbitragebot\" or http.user_agent contains \"bonusbot\" or http.user_agent contains \"surebet\")"
    description = "Block known gambling bot user agents"
    enabled     = true
  }
}

# ---------------------------------------------------------------------------
# Geo-Blocking for Unlicensed Jurisdictions
# ---------------------------------------------------------------------------
resource "cloudflare_ruleset" "geo_blocking" {
  zone_id     = var.cloudflare_zone_id
  name        = "GLI-GSF Geo-Blocking"
  description = "Block traffic from jurisdictions where platform is not licensed"
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  rules {
    action      = "block"
    expression  = "(not ip.geoip.country in {\"${join("\" \"", var.allowed_countries)}\"})"
    description = "Block traffic from unlicensed jurisdictions"
    enabled     = var.environment == "production"
  }
}

# ---------------------------------------------------------------------------
# IP Reputation and Access Rules
# ---------------------------------------------------------------------------
resource "cloudflare_ruleset" "ip_reputation" {
  zone_id     = var.cloudflare_zone_id
  name        = "GLI-GSF IP Reputation Rules"
  description = "IP reputation filtering for gambling platform"
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  # Challenge high-threat-score IPs on API endpoints
  rules {
    action      = "managed_challenge"
    expression  = "(cf.threat_score gt 14) and (http.request.uri.path contains \"/api/\")"
    description = "Challenge high-threat-score IPs on API endpoints"
    enabled     = true
  }

  # Block very high threat score traffic
  rules {
    action      = "block"
    expression  = "(cf.threat_score gt 50)"
    description = "Block IPs with very high threat score (>50)"
    enabled     = true
  }
}

# ---------------------------------------------------------------------------
# DDoS Protection Overrides (Layer 7)
# ---------------------------------------------------------------------------
resource "cloudflare_ruleset" "ddos_l7" {
  zone_id     = var.cloudflare_zone_id
  name        = "GLI-GSF L7 DDoS Overrides"
  description = "Layer 7 DDoS protection tuning for gambling platform"
  kind        = "zone"
  phase       = "ddos_l7"

  rules {
    action = "execute"
    action_parameters {
      id = "4d21379b4f9f4bb088e0729962c8b3cf" # CloudFlare HTTP DDoS
      overrides {
        sensitivity_level = "high"
      }
    }
    expression  = "true"
    description = "High sensitivity L7 DDoS protection for gambling platform"
    enabled     = true
  }
}

# ---------------------------------------------------------------------------
# Page Rules for Challenge Pages
# ---------------------------------------------------------------------------
resource "cloudflare_page_rule" "backoffice_challenge" {
  zone_id  = var.cloudflare_zone_id
  target   = "${var.domain}/backoffice/*"
  priority = 1

  actions {
    security_level = "under_attack"
  }
}

resource "cloudflare_page_rule" "admin_api_challenge" {
  zone_id  = var.cloudflare_zone_id
  target   = "${var.domain}/api/v1/admin/*"
  priority = 2

  actions {
    security_level = "under_attack"
  }
}

resource "cloudflare_page_rule" "ssl_strict" {
  zone_id  = var.cloudflare_zone_id
  target   = "${var.domain}/*"
  priority = 3

  actions {
    ssl                      = "strict"
    always_use_https         = true
    automatic_https_rewrites = "on"
    min_tls_version          = "1.2"
  }
}

# ---------------------------------------------------------------------------
# Notification Policy for DDoS Events
# ---------------------------------------------------------------------------
resource "cloudflare_notification_policy" "ddos_alert" {
  account_id  = data.cloudflare_zone.casino.account_id
  name        = "GLI-GSF DDoS Attack Alert"
  description = "Alert on DDoS attacks targeting gambling platform"
  enabled     = true
  alert_type  = "advanced_ddos_attack_l7_alert"

  email_integration {
    id   = ""
    name = "Security Team"
  }
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
output "zone_id" {
  description = "CloudFlare Zone ID"
  value       = var.cloudflare_zone_id
}

output "waf_ruleset_id" {
  description = "WAF managed ruleset ID"
  value       = cloudflare_ruleset.waf_managed.id
}

output "rate_limit_ruleset_id" {
  description = "Rate limiting ruleset ID"
  value       = cloudflare_ruleset.rate_limiting.id
}

output "configuration_summary" {
  description = "Summary of DDoS protection configuration"
  value = {
    domain                = var.domain
    environment           = var.environment
    rate_limit_login      = "${var.rate_limit_login}/min"
    rate_limit_deposit    = "${var.rate_limit_deposit}/min"
    rate_limit_game_round = "${var.rate_limit_game_round}/min"
    rate_limit_api        = "${var.rate_limit_api_general}/min"
    blocked_countries     = var.blocked_countries
    gli_gsf_reference     = "GLI-GSF-5, OGIS-4, OGIS-5"
  }
}
