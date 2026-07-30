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
# Terraform: Cloudflare Geo-Restriction Rules for iGaming Platforms
# =============================================================================
#
# Configures Cloudflare's WAF, Firewall Rules, and Workers to enforce
# jurisdiction-based access control. Cloudflare is the preferred choice for
# operators targeting Latin America (low latency to LATAM PoPs) and for
# platforms requiring sub-state blocking (e.g., blocking specific US states
# without blocking the entire country).
#
# Architecture:
#   Client → Cloudflare Edge (WAF + Geo Rules) → Origin (nginx/ALB)
#
# Blocking happens at the edge before the request reaches your infrastructure.
# This eliminates bandwidth consumption and protects against DDoS amplification
# via blocked jurisdictions.
#
# Usage:
#   export CLOUDFLARE_API_TOKEN="your-token"
#   export CLOUDFLARE_ZONE_ID="your-zone-id"
#   terraform init && terraform apply
#
# Required Cloudflare plan: Pro or higher (for Firewall Rules and Rate Limiting)
# Workers requires Workers Paid plan ($5/month) for state-level blocking.
# =============================================================================

terraform {
  required_version = ">= 1.5"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = ">= 4.0"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

# =============================================================================
# Variables
# =============================================================================
variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone:Edit and Workers:Edit permissions"
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Cloudflare Zone ID for the casino domain"
  type        = string
}

variable "cloudflare_account_id" {
  description = "Cloudflare Account ID"
  type        = string
}

variable "domain_name" {
  description = "Primary domain name (e.g., casino.example.com)"
  type        = string
}

# =============================================================================
# WAF Custom Ruleset: Block prohibited jurisdictions
#
# This uses Cloudflare's Rules API (newer, replaces legacy Firewall Rules).
# Rules are evaluated in priority order (lowest number = highest priority).
# =============================================================================
resource "cloudflare_ruleset" "geo_blocking_waf" {
  zone_id     = var.cloudflare_zone_id
  name        = "iGaming Jurisdiction Geo-Blocking"
  description = "Blocks access from prohibited gambling jurisdictions. Updated per license portfolio."
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  # ------------------------------------------------------------------
  # Rule 1 (priority 1): Allow health-check endpoints unconditionally
  # Load balancer probes must not be blocked.
  # ------------------------------------------------------------------
  rules {
    action = "skip"
    action_parameters {
      ruleset = "current"
    }
    expression  = "(http.request.uri.path eq \"/healthz\" or http.request.uri.path eq \"/ping\")"
    description = "Allow health-check probes from any location"
    enabled     = true
  }

  # ------------------------------------------------------------------
  # Rule 2 (priority 2): Allow legitimate Cloudflare infrastructure IPs
  # ------------------------------------------------------------------
  rules {
    action = "skip"
    action_parameters {
      ruleset = "current"
    }
    expression  = "cf.client.bot"
    description = "Skip rules for verified Cloudflare bots (e.g., Security Insights)"
    enabled     = true
  }

  # ------------------------------------------------------------------
  # Rule 3 (priority 3): Block complete-prohibition jurisdictions
  # Countries where all online gambling is prohibited by law.
  # Returns HTTP 451 Unavailable For Legal Reasons.
  # ------------------------------------------------------------------
  rules {
    action = "block"
    action_parameters {
      response {
        status_code  = 451
        content_type = "application/json"
        content = jsonencode({
          error   = "access_restricted"
          message = "This service is not available in your jurisdiction due to local regulations."
          code    = "GEO_BLOCK_PROHIBITED"
          support = "support@casino.example.com"
        })
      }
    }
    expression  = <<-EOT
      (
        ip.geoip.country in {
          "AE" "SA" "QA" "KW" "BH" "OM" "YE" "LY" "SD"
          "CN" "KP" "KH" "DZ" "MA" "PK" "BD" "AF" "IQ" "IR"
        }
      )
    EOT
    description = "Block prohibited gambling jurisdictions (complete prohibition)"
    enabled     = true
    logging {
      enabled = true # Always log for regulatory audit trail
    }
  }

  # ------------------------------------------------------------------
  # Rule 4 (priority 4): Block anonymous proxies and VPNs
  # Cloudflare maintains a threat intelligence feed for known proxy/VPN
  # endpoints. This reduces geo-circumvention attempts.
  # Note: cf.threat_score > 50 may generate false positives — tune as needed.
  # ------------------------------------------------------------------
  rules {
    action = "block"
    action_parameters {
      response {
        status_code  = 403
        content_type = "application/json"
        content = jsonencode({
          error   = "proxy_detected"
          message = "Proxy and VPN connections are not permitted on this platform."
          code    = "PROXY_BLOCK"
        })
      }
    }
    expression  = "(cf.threat_score gt 50) and not (cf.client.bot)"
    description = "Block high-threat-score IPs (VPNs, proxies, botnets)"
    enabled     = true
    logging {
      enabled = true
    }
  }

  # ------------------------------------------------------------------
  # Rule 5 (priority 5): Rate-limit suspicious geo-bypass patterns
  # Rapid country code switches on the same account = VPN rotation.
  # This is handled at application layer — this rule enforces at edge.
  # ------------------------------------------------------------------
  rules {
    action      = "managed_challenge"
    expression  = <<-EOT
      (
        cf.threat_score gt 14 and
        http.request.uri.path contains "/api/" and
        not ip.geoip.country in {"GB" "MT" "IE" "DE" "ES" "IT" "FR" "SE" "DK" "NO" "NL" "BE" "AT" "PT" "FI" "PL" "CZ" "HU" "RO"}
      )
    EOT
    description = "CAPTCHA challenge for medium-threat-score IPs accessing API from outside licensed markets"
    enabled     = true
    logging {
      enabled = true
    }
  }
}

# =============================================================================
# Cloudflare Rate Limiting: per-country abuse prevention
# Prevents blocked-country IP ranges from hammering the compliance endpoint.
# =============================================================================
resource "cloudflare_ruleset" "rate_limiting" {
  zone_id     = var.cloudflare_zone_id
  name        = "iGaming Rate Limiting"
  description = "Rate limits for geo-compliance and API protection"
  kind        = "zone"
  phase       = "http_ratelimit"

  rules {
    action = "block"
    action_parameters {
      response {
        status_code  = 429
        content_type = "application/json"
        content = jsonencode({
          error   = "rate_limit_exceeded"
          message = "Too many requests. Please try again later."
          code    = "RATE_LIMIT"
        })
      }
    }
    ratelimit {
      characteristics     = ["ip.src"]
      period              = 60
      requests_per_period = 100
      mitigation_timeout  = 600
    }
    expression  = "(http.request.uri.path contains \"/api/auth/\" or http.request.uri.path contains \"/api/register\")"
    description = "Rate limit authentication and registration endpoints"
    enabled     = true
  }
}

# =============================================================================
# Cloudflare Worker: sub-national geo-blocking
#
# Route 53 and standard WAF rules work at the country level.
# For operators licensed in specific US states, you need sub-state blocking.
# This Worker intercepts every request, reads Cloudflare's cf.region header,
# and enforces state-level access control.
#
# The worker script is deployed inline. For larger scripts, use
# cloudflare_worker_script with external file references.
# =============================================================================
resource "cloudflare_worker_script" "geo_state_blocker" {
  account_id = var.cloudflare_account_id
  name       = "igaming-geo-state-blocker"

  content = <<-WORKER_SCRIPT
    // Cloudflare Worker: Sub-national geo-blocking for iGaming
    // Blocks specific US states where the operator holds no license.
    // Cloudflare provides cf.regionCode (US state codes) via the request.cf object.

    const BLOCKED_US_STATES = new Set([
      // States with no legal online casino framework or where operator is not licensed
      "UT",  // Utah — state constitution prohibits all gambling (Utah Code § 76-10-1101)
      "HI",  // Hawaii — no commercial gambling permitted (HRS § 712-1220)
      "AL",  // Alabama — Code of Alabama § 13A-12-20 et seq.
      "ID",  // Idaho — Idaho Code § 18-3801
      "WI",  // Wisconsin — limited tribal compacts only, no online casino
      "SD",  // South Dakota — online casino not yet authorized
      "KY",  // Kentucky — no online casino license framework
      "MS",  // Mississippi — land-based only
      "GA",  // Georgia — O.C.G.A. § 16-12-20
      "TX",  // Texas — Penal Code § 47.02
      "AR",  // Arkansas — Amendment 100 allows limited games but not online casino
    ]);

    // Countries where we have an MGA license (European operations)
    // All others outside this list + not US-licensed states = require review
    const LICENSED_COUNTRIES = new Set([
      "MT", "GB", "IE", "DE", "ES", "IT", "FR", "SE", "DK", "NO",
      "NL", "BE", "AT", "PT", "FI", "PL", "CZ", "HU", "RO", "GR",
      "HR", "BG", "SK", "SI", "LT", "LV", "EE", "LU", "CY",
      // Add other licensed markets here
    ]);

    async function handleRequest(request) {
      const cf = request.cf;
      const country = cf.country || "XX";
      const regionCode = cf.regionCode || "";   // e.g., "CA" for California, "NY" for New York
      const url = new URL(request.url);

      // Always allow health checks
      if (url.pathname === "/healthz" || url.pathname === "/ping") {
        return fetch(request);
      }

      // US-specific sub-state blocking
      if (country === "US") {
        if (BLOCKED_US_STATES.has(regionCode)) {
          return new Response(
            JSON.stringify({
              error: "access_restricted",
              message: "This service is not available in your state.",
              code: "GEO_BLOCK_STATE",
              state: regionCode,
              country: country,
            }),
            {
              status: 451,
              headers: {
                "Content-Type": "application/json",
                "Cache-Control": "no-store",
                "X-Blocked-Region": `US-${regionCode}`,
              },
            }
          );
        }
        // US states that ARE licensed — pass through
        return fetch(request);
      }

      // Pass through licensed countries
      if (LICENSED_COUNTRIES.has(country)) {
        // Attach geolocation metadata for downstream application
        const modifiedRequest = new Request(request, {
          headers: {
            ...Object.fromEntries(request.headers),
            "X-Player-Country": country,
            "X-Player-Region": regionCode,
            "X-Cloudflare-Ray": cf.rayId || "",
          },
        });
        return fetch(modifiedRequest);
      }

      // Unknown/unlicensed country: log and pass to application layer for
      // secondary verification (the application layer makes the final decision)
      return fetch(request);
    }

    addEventListener("fetch", event => {
      event.respondWith(handleRequest(event.request));
    });
  WORKER_SCRIPT

  plain_text_bindings {
    name = "ENVIRONMENT"
    text = "production"
  }
}

# Attach the Worker to the zone (all requests go through it)
resource "cloudflare_worker_route" "geo_state_blocker_route" {
  zone_id     = var.cloudflare_zone_id
  pattern     = "${var.domain_name}/*"
  script_name = cloudflare_worker_script.geo_state_blocker.name
}

# =============================================================================
# Cloudflare Analytics: geo-block monitoring dashboard
# Creates a custom analytics dashboard to track blocking events.
# =============================================================================
resource "cloudflare_logpush_job" "geo_block_logs" {
  zone_id          = var.cloudflare_zone_id
  name             = "geo-block-audit-logs"
  enabled          = true
  logpull_options  = "fields=ClientCountry,ClientIP,ClientRequestURI,EdgeStartTimestamp,FirewallMatchesActions,FirewallMatchesRuleIDs,FirewallMatchesSources,EdgeResponseStatus&timestamps=rfc3339"
  destination_conf = "s3://your-compliance-logs-bucket/cloudflare-geo-blocks?region=eu-west-1&sse=AES256"
  dataset          = "firewall_events"

  output_options {
    field_names = [
      "ClientCountry",
      "ClientIP",
      "ClientRequestURI",
      "EdgeStartTimestamp",
      "FirewallMatchesActions",
      "FirewallMatchesRuleIDs",
      "EdgeResponseStatus",
    ]
    timestamp_format = "rfc3339"
    batch_prefix     = "geo_block_"
  }
}

# =============================================================================
# Outputs
# =============================================================================
output "waf_ruleset_id" {
  description = "ID of the geo-blocking WAF ruleset"
  value       = cloudflare_ruleset.geo_blocking_waf.id
}

output "worker_script_name" {
  description = "Name of the sub-national geo-blocking Worker"
  value       = cloudflare_worker_script.geo_state_blocker.name
}

output "rate_limit_ruleset_id" {
  description = "ID of the rate limiting ruleset"
  value       = cloudflare_ruleset.rate_limiting.id
}
