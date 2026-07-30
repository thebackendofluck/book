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
# modules/cloudflare/main.tf
# Chapter 24: Cloudflare edge layer — KV namespaces, Workers, WAF, firewall
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables (passed from root module)
# ---------------------------------------------------------------------------

variable "account_id" { type = string }
variable "zone_id" { type = string }
variable "domain" { type = string }
variable "environment" { type = string }
variable "name_suffix" { type = string }
variable "worker_route_pattern" { type = string }
variable "classifier_route_pattern" { type = string }
variable "worker_script_path" { type = string }
variable "classifier_script_path" { type = string }
variable "security_level" { type = string }
variable "bot_management_enabled" { type = bool }
variable "waf_managed_ruleset_enabled" { type = bool }
variable "owasp_ruleset_enabled" { type = bool }
variable "fraud_score_block_threshold" { type = number }
variable "fraud_score_review_threshold" { type = number }
variable "rate_limit_thresholds" {
  type = object({
    requests_per_minute = number
    requests_per_5min   = number
    requests_per_hour   = number
  })
}

# ---------------------------------------------------------------------------
# Local values
# ---------------------------------------------------------------------------

locals {
  # Build a config object serialised into the Worker's environment binding.
  # Workers read this via env.GATE_CONFIG (plain-text JSON KV entry).
  gate_config_json = jsonencode({
    fraud_block_threshold  = var.fraud_score_block_threshold
    fraud_review_threshold = var.fraud_score_review_threshold
    rate_limit = {
      per_minute = var.rate_limit_thresholds.requests_per_minute
      per_5min   = var.rate_limit_thresholds.requests_per_5min
      per_hour   = var.rate_limit_thresholds.requests_per_hour
    }
    environment = var.environment
  })
}

# =============================================================================
# KV Namespaces
# =============================================================================

# IP_BLACKLIST — banned IPs with optional TTL expiry.
resource "cloudflare_workers_kv_namespace" "ip_blacklist" {
  account_id = var.account_id
  title      = "ip-blacklist${var.name_suffix}"
}

# DEVICE_FINGERPRINTS — JA3 hash history per IP and per hash.
resource "cloudflare_workers_kv_namespace" "device_fingerprints" {
  account_id = var.account_id
  title      = "device-fingerprints${var.name_suffix}"
}

# FRAUD_VELOCITY — per-IP velocity counters (req/min, req/5min, req/hour).
resource "cloudflare_workers_kv_namespace" "fraud_velocity" {
  account_id = var.account_id
  title      = "fraud-velocity${var.name_suffix}"
}

# SANCTIONS_LIST — OFAC country codes and SDN name tokens.
resource "cloudflare_workers_kv_namespace" "sanctions_list" {
  account_id = var.account_id
  title      = "sanctions-list${var.name_suffix}"
}

# RATE_LIMITS — per-endpoint override thresholds.
resource "cloudflare_workers_kv_namespace" "rate_limits" {
  account_id = var.account_id
  title      = "rate-limits${var.name_suffix}"
}

# CAMPAIGNS — active marketing campaign metadata (scale hints for the Worker).
resource "cloudflare_workers_kv_namespace" "campaigns" {
  account_id = var.account_id
  title      = "campaigns${var.name_suffix}"
}

# ATTACK_LOG — recent attack event metadata written by Workers for correlation.
resource "cloudflare_workers_kv_namespace" "attack_log" {
  account_id = var.account_id
  title      = "attack-log${var.name_suffix}"
}

# JA3_BLOCKLIST — TLS fingerprint hashes associated with known attack tools.
resource "cloudflare_workers_kv_namespace" "ja3_blocklist" {
  account_id = var.account_id
  title      = "ja3-blocklist${var.name_suffix}"
}

# =============================================================================
# KV seed data — OFAC / EU sanctioned country codes
# Mirrors HARDCODED_SANCTIONED_COUNTRIES in sanctions.ts; KV allows runtime
# updates without redeploying the Worker.
# =============================================================================

locals {
  sanctioned_countries = toset([
    "CU", "IR", "KP", "RU", "SY", "VE", "BY",
    "MM", "SS", "SD", "SO", "LY", "YE", "ZW",
    "CF", "ML", "NI", "HT",
  ])
}

resource "cloudflare_workers_kv" "sanctioned_country" {
  for_each     = local.sanctioned_countries
  account_id   = var.account_id
  namespace_id = cloudflare_workers_kv_namespace.sanctions_list.id
  key          = "sanctions:country:${each.value}"
  value        = "1"
}

# Gate config KV entry — read by the Worker at cold-start and cached per isolate.
resource "cloudflare_workers_kv" "gate_config" {
  account_id   = var.account_id
  namespace_id = cloudflare_workers_kv_namespace.rate_limits.id
  key          = "gate:config:v1"
  value        = local.gate_config_json
}

# =============================================================================
# Workers Scripts
# =============================================================================

# ip-detection-worker — main 8-gate pipeline, runs on every request.
resource "cloudflare_workers_script" "ip_detection" {
  account_id = var.account_id
  name       = "ip-detection${var.name_suffix}"
  content    = file(var.worker_script_path)

  kv_namespace_binding {
    name         = "IP_BLACKLIST"
    namespace_id = cloudflare_workers_kv_namespace.ip_blacklist.id
  }

  kv_namespace_binding {
    name         = "DEVICE_FINGERPRINTS"
    namespace_id = cloudflare_workers_kv_namespace.device_fingerprints.id
  }

  kv_namespace_binding {
    name         = "FRAUD_VELOCITY"
    namespace_id = cloudflare_workers_kv_namespace.fraud_velocity.id
  }

  kv_namespace_binding {
    name         = "SANCTIONS_LIST"
    namespace_id = cloudflare_workers_kv_namespace.sanctions_list.id
  }

  kv_namespace_binding {
    name         = "RATE_LIMITS"
    namespace_id = cloudflare_workers_kv_namespace.rate_limits.id
  }

  kv_namespace_binding {
    name         = "CAMPAIGNS"
    namespace_id = cloudflare_workers_kv_namespace.campaigns.id
  }

  kv_namespace_binding {
    name         = "ATTACK_LOG"
    namespace_id = cloudflare_workers_kv_namespace.attack_log.id
  }

  kv_namespace_binding {
    name         = "JA3_BLOCKLIST"
    namespace_id = cloudflare_workers_kv_namespace.ja3_blocklist.id
  }

  plain_text_binding {
    name = "ENVIRONMENT"
    text = var.environment
  }

  plain_text_binding {
    name = "GATE_CONFIG_KEY"
    text = "gate:config:v1"
  }
}

# edge-classifier — lightweight pre-filter that short-circuits obvious attacks
# before the full 8-gate pipeline runs.
resource "cloudflare_workers_script" "edge_classifier" {
  account_id = var.account_id
  name       = "edge-classifier${var.name_suffix}"
  content    = file(var.classifier_script_path)

  kv_namespace_binding {
    name         = "IP_BLACKLIST"
    namespace_id = cloudflare_workers_kv_namespace.ip_blacklist.id
  }

  kv_namespace_binding {
    name         = "JA3_BLOCKLIST"
    namespace_id = cloudflare_workers_kv_namespace.ja3_blocklist.id
  }

  kv_namespace_binding {
    name         = "ATTACK_LOG"
    namespace_id = cloudflare_workers_kv_namespace.attack_log.id
  }

  plain_text_binding {
    name = "ENVIRONMENT"
    text = var.environment
  }
}

# =============================================================================
# Worker Routes
# =============================================================================

resource "cloudflare_workers_route" "ip_detection" {
  zone_id     = var.zone_id
  pattern     = var.worker_route_pattern
  script_name = cloudflare_workers_script.ip_detection.name
}

resource "cloudflare_workers_route" "edge_classifier" {
  zone_id     = var.zone_id
  pattern     = var.classifier_route_pattern
  script_name = cloudflare_workers_script.edge_classifier.name
}

# =============================================================================
# Zone Settings — security hardening
# =============================================================================

# Security level (medium/high/under_attack).
resource "cloudflare_zone_settings_override" "security" {
  zone_id = var.zone_id

  settings {
    security_level           = var.security_level
    ssl                      = "strict"
    min_tls_version          = "1.2"
    tls_1_3                  = "on"
    http3                    = "on"
    zero_rtt                 = "on"
    opportunistic_encryption = "on"
    automatic_https_rewrites = "on"
    browser_check            = "on"

    # Always use HTTPS — redirect HTTP to HTTPS.
    always_use_https = "on"

    # Challenge TTL: how long a passed challenge is valid (seconds).
    challenge_ttl = 1800

    # Privacy Pass support — reduces challenge rates for legitimate users.
    privacy_pass = "on"
  }
}

# =============================================================================
# Rate Limiting — via cloudflare_ruleset (http_ratelimit phase)
# Replaces deprecated cloudflare_rate_limit resources.
# =============================================================================

resource "cloudflare_ruleset" "rate_limiting" {
  zone_id     = var.zone_id
  name        = "ip-detection-rate-limits${var.name_suffix}"
  description = "Rate limiting rules for iGaming IP detection pipeline"
  kind        = "zone"
  phase       = "http_ratelimit"

  # Rule 1: High-frequency rate limit — general brute-force protection.
  rules {
    action      = "log"
    description = "ip-detection: general rate limit (${var.rate_limit_thresholds.requests_per_minute} req/min)"
    enabled     = true
    expression  = "(http.host eq \"${var.domain}\")"
    action_parameters {
      response {
        status_code  = 429
        content_type = "application/json"
        content      = jsonencode({ error = "RATE_LIMITED", message = "Too many requests. Please slow down." })
      }
    }
    ratelimit {
      characteristics     = ["cf.colo.id", "ip.src"]
      period              = 60
      requests_per_period = var.rate_limit_thresholds.requests_per_minute
      mitigation_timeout  = 60
    }
  }

  # Rule 2: Login-endpoint rate limit — protect auth endpoints from credential stuffing.
  rules {
    action      = "block"
    description = "ip-detection: login endpoint rate limit (10 req/min)"
    enabled     = true
    expression  = "(http.host eq \"${var.domain}\" and http.request.uri.path contains \"/login\" and http.request.method eq \"POST\")"
    action_parameters {
      response {
        status_code  = 429
        content_type = "application/json"
        content      = jsonencode({ error = "RATE_LIMITED", message = "Too many login attempts. Account temporarily restricted." })
      }
    }
    ratelimit {
      characteristics     = ["cf.colo.id", "ip.src"]
      period              = 60
      requests_per_period = 10
      mitigation_timeout  = 600
    }
  }

  # Rule 3: API endpoint rate limit — protect game and payment APIs.
  rules {
    action      = "managed_challenge"
    description = "ip-detection: API rate limit (${var.rate_limit_thresholds.requests_per_5min} req/5min)"
    enabled     = true
    expression  = "(http.host eq \"${var.domain}\" and starts_with(http.request.uri.path, \"/api/\"))"
    ratelimit {
      characteristics     = ["cf.colo.id", "ip.src"]
      period              = 300
      requests_per_period = var.rate_limit_thresholds.requests_per_5min
      mitigation_timeout  = 300
    }
  }
}

# =============================================================================
# WAF Managed Rulesets
# =============================================================================

# Cloudflare Managed Ruleset — protects against known CVEs and attack patterns.
resource "cloudflare_ruleset" "waf_managed" {
  count       = var.waf_managed_ruleset_enabled ? 1 : 0
  zone_id     = var.zone_id
  name        = "ip-detection-waf-managed${var.name_suffix}"
  description = "Cloudflare Managed Ruleset for iGaming IP detection pipeline"
  kind        = "zone"
  phase       = "http_request_firewall_managed"

  rules {
    action = "execute"
    action_parameters {
      id = "efb7b8c949ac4650a09736fc376e9aee" # Cloudflare Managed Ruleset
      overrides {
        action = "block"
        # Enable all rules in the Cloudflare Managed Ruleset.
        enabled = true
      }
    }
    expression  = "true"
    description = "Execute Cloudflare Managed Ruleset"
    enabled     = true
  }
}

# OWASP Core Ruleset — industry-standard web application firewall rules.
resource "cloudflare_ruleset" "waf_owasp" {
  count       = var.owasp_ruleset_enabled ? 1 : 0
  zone_id     = var.zone_id
  name        = "ip-detection-waf-owasp${var.name_suffix}"
  description = "OWASP Core Ruleset for iGaming IP detection pipeline"
  kind        = "zone"
  phase       = "http_request_firewall_managed"

  rules {
    action = "execute"
    action_parameters {
      id = "4814384a9e5d4991b9815dcfc25d2f1f" # Cloudflare OWASP Core Ruleset
      overrides {
        action  = "block"
        enabled = true
        rules {
          id              = "6179ae15870a4bb7b2d480d4843b323c" # Paranoia Level 1
          enabled         = true
          score_threshold = 60
        }
      }
    }
    expression  = "true"
    description = "Execute OWASP Core Ruleset at Paranoia Level 1"
    enabled     = true
  }
}

# Custom firewall ruleset — layer on top of managed rules.
resource "cloudflare_ruleset" "firewall_custom" {
  zone_id     = var.zone_id
  name        = "ip-detection-custom-firewall${var.name_suffix}"
  description = "Custom firewall rules for iGaming platform"
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  # Rule 1: Block requests with suspicious User-Agent patterns (bots, scanners).
  rules {
    action      = "block"
    expression  = "(http.user_agent contains \"sqlmap\") or (http.user_agent contains \"nikto\") or (http.user_agent contains \"masscan\") or (http.user_agent contains \"zgrab\") or (http.user_agent contains \"python-requests\" and not cf.bot_management.verified_bot) or (http.user_agent eq \"\")"
    description = "Block known scanner and empty User-Agent strings"
    enabled     = true
  }

  # Rule 2: Challenge requests from Tor exit nodes (CF.is_known_bad_ip covers many,
  # but we add an extra managed challenge for TOR ASNs flagged at edge).
  rules {
    action      = "managed_challenge"
    expression  = "cf.threat_score gt 14"
    description = "Managed challenge for elevated threat scores"
    enabled     = true
  }

  # Rule 3: Block direct IP access to origin (bypasses WAF).
  rules {
    action      = "block"
    expression  = "(http.host matches r\"^(\\d{1,3}\\.){3}\\d{1,3}$\")"
    description = "Block requests using bare IP as Host header"
    enabled     = true
  }

  # Rule 4: DDoS emergency — allow only GET + HEAD during active attack.
  # Disabled by default; enable via Terraform or KV flag during incidents.
  rules {
    action      = "block"
    expression  = "(http.request.method ne \"GET\" and http.request.method ne \"HEAD\") and cf.threat_score gt 50"
    description = "DDoS emergency: block non-GET/HEAD from high-threat IPs"
    enabled     = false
  }
}

# =============================================================================
# Bot Management (Enterprise only)
# =============================================================================

resource "cloudflare_bot_management" "main" {
  count   = var.bot_management_enabled ? 1 : 0
  zone_id = var.zone_id

  enable_js              = true
  fight_mode             = false # Set true only during confirmed bot attacks
  auto_update_model      = true
  suppress_session_score = false
}

# =============================================================================
# Page Rules — emergency DDoS cache-everything mode
# Disabled by default; activate during L7 DDoS incidents.
# =============================================================================

resource "cloudflare_page_rule" "ddos_cache_everything" {
  zone_id  = var.zone_id
  target   = "${var.domain}/*"
  priority = 100
  status   = "disabled" # Enable manually during incidents via: terraform apply -var ddos_mode=true

  actions {
    cache_level    = "cache_everything"
    edge_cache_ttl = 300
  }
}

# =============================================================================
# Outputs
# =============================================================================

output "kv_ip_blacklist_id" { value = cloudflare_workers_kv_namespace.ip_blacklist.id }
output "kv_device_fingerprints_id" { value = cloudflare_workers_kv_namespace.device_fingerprints.id }
output "kv_fraud_velocity_id" { value = cloudflare_workers_kv_namespace.fraud_velocity.id }
output "kv_sanctions_list_id" { value = cloudflare_workers_kv_namespace.sanctions_list.id }
output "kv_rate_limits_id" { value = cloudflare_workers_kv_namespace.rate_limits.id }
output "kv_campaigns_id" { value = cloudflare_workers_kv_namespace.campaigns.id }
output "kv_attack_log_id" { value = cloudflare_workers_kv_namespace.attack_log.id }
output "kv_ja3_blocklist_id" { value = cloudflare_workers_kv_namespace.ja3_blocklist.id }
output "worker_ip_detection_id" { value = cloudflare_workers_script.ip_detection.id }
output "worker_edge_classifier_id" { value = cloudflare_workers_script.edge_classifier.id }
