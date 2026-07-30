# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# terraform/dns.tf — DNS Records for cloud-acmetocasino.com
#
# Manages all Cloudflare DNS records for the AcmetoCasino Cloudflare Workers
# deployment.  Terraform owns DNS; Wrangler owns Worker deployments.
#
# Design principle: DNS records are version-controlled here so that every
# change is reviewable in a pull request, auditable in Terraform state, and
# reproducible across environments.  The CI/CD pipeline applies this file
# after a successful Worker deployment (deploy.yml: terraform apply step).
#
# NOTE: The 192.0.2.1 address is a documentation-range IP (RFC 5737).
# When Cloudflare proxy is enabled (proxied = true), requests never reach
# this IP — they terminate at the Worker at the edge.
#
# Apply:
#   cd terraform/
#   terraform init
#   terraform plan -var="cloudflare_api_token=$CLOUDFLARE_API_TOKEN"
#   terraform apply -var="cloudflare_api_token=$CLOUDFLARE_API_TOKEN"
#
# Reference: Chapter 44 — Deploying iGaming on Cloudflare Workers / DNS Records

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
  }

  # Remote state — Cloudflare R2 backend (keeps state out of the repo)
  # backend "s3" {
  #   bucket                      = "acmetocasino-terraform-state"
  #   key                         = "cloudflare-workers/dns/terraform.tfstate"
  #   region                      = "auto"
  #   skip_credentials_validation = true
  #   skip_metadata_api_check     = true
  #   skip_region_validation      = true
  #   force_path_style            = true
  #   endpoints = {
  #     s3 = "https://<account_id>.r2.cloudflarestorage.com"
  #   }
  # }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "cloudflare_api_token" {
  type        = string
  description = "Cloudflare API token with DNS:Edit and Zone:Read permissions."
  sensitive   = true
}

variable "cloudflare_zone_id" {
  type        = string
  description = "Zone ID for cloud-acmetocasino.com. Find in the Cloudflare dashboard."
  # Set via TF_VAR_cloudflare_zone_id or -var flag — never hard-code in version control.
  default = ""
}

variable "cloudflare_account_id" {
  type        = string
  description = "Cloudflare account ID (<your-cf-account-id> for production)."
  default     = ""
  sensitive   = true
}

variable "pages_project_subdomain" {
  type        = string
  description = "Cloudflare Pages project subdomain for the casino lobby."
  default     = "cloud-acmetocasino-lobby.pages.dev"
}

variable "bet_brazil_pages_subdomain" {
  type        = string
  description = "Cloudflare Pages subdomain for the Brazil brand."
  default     = "cloud-acmetocasino.pages.dev"
}


# ---------------------------------------------------------------------------
# Local values
# ---------------------------------------------------------------------------

locals {
  # Placeholder IP for proxied Worker records (RFC 5737 documentation range).
  # Requests never reach this IP — Cloudflare proxy intercepts them at the edge.
  worker_placeholder_ip = "192.0.2.1"

  zone_id = var.cloudflare_zone_id
}


# ---------------------------------------------------------------------------
# Worker subdomain — primary API Worker
# Routed to acmetocasino-api Worker via wrangler.toml [[routes]]
# Pattern: cloudflare.cloud-acmetocasino.com/*
# ---------------------------------------------------------------------------

resource "cloudflare_record" "cloudflare_subdomain" {
  zone_id = local.zone_id
  name    = "cloudflare"
  content = local.worker_placeholder_ip
  type    = "A"
  proxied = true

  comment = "Primary API Worker (acmetocasino-api). Managed by Terraform."
}


# ---------------------------------------------------------------------------
# Casino lobby — Cloudflare Pages deployment
# ---------------------------------------------------------------------------

resource "cloudflare_record" "casino" {
  zone_id = local.zone_id
  name    = "casino"
  content = var.pages_project_subdomain
  type    = "CNAME"
  proxied = true

  comment = "Casino lobby — Cloudflare Pages. Managed by Terraform."
}


# ---------------------------------------------------------------------------
# API microservices — proxied A records
# Traffic routes to the corresponding Worker or origin via WAF rules.
# ---------------------------------------------------------------------------

resource "cloudflare_record" "api" {
  zone_id = local.zone_id
  name    = "api"
  content = local.worker_placeholder_ip
  type    = "A"
  proxied = true

  comment = "Main API gateway Worker. Managed by Terraform."
}

resource "cloudflare_record" "payments" {
  zone_id = local.zone_id
  name    = "payments"
  content = local.worker_placeholder_ip
  type    = "A"
  proxied = true

  comment = "Payment processing Worker / PSP proxy. Managed by Terraform."
}

resource "cloudflare_record" "backoffice" {
  zone_id = local.zone_id
  name    = "backoffice"
  content = local.worker_placeholder_ip
  type    = "A"
  proxied = true

  comment = "Back-office admin Worker (access via Zero Trust). Managed by Terraform."
}


# ---------------------------------------------------------------------------
# Risk engine — fraud scoring and transaction monitoring
# ---------------------------------------------------------------------------

resource "cloudflare_record" "risk" {
  zone_id = local.zone_id
  name    = "risk"
  content = local.worker_placeholder_ip
  type    = "A"
  proxied = true

  comment = "Risk engine Worker — fraud scoring, velocity checks. Managed by Terraform."
}


# ---------------------------------------------------------------------------
# Self-exclusion register endpoint
# OASIS (Germany) / CRUKS (Netherlands) / Spelpaus (Sweden) integration
# ---------------------------------------------------------------------------

resource "cloudflare_record" "exclusion" {
  zone_id = local.zone_id
  name    = "exclusion"
  content = local.worker_placeholder_ip
  type    = "A"
  proxied = true

  comment = "Self-exclusion register endpoint (OASIS/CRUKS/Spelpaus). Managed by Terraform."
}


# ---------------------------------------------------------------------------
# Brazil brand subdomain — AcmeBet Brazil
# Cloudflare Pages deployment for the Brazilian market (Portaria SPA/MF 1.612/2023)
# ---------------------------------------------------------------------------

resource "cloudflare_record" "bet_brazil" {
  zone_id = local.zone_id
  name    = "bet-brazil"
  content = var.bet_brazil_pages_subdomain
  type    = "CNAME"
  proxied = true

  comment = "AcmeBet Brazil brand — Cloudflare Pages. Managed by Terraform."
}


# ---------------------------------------------------------------------------
# Staging subdomain — mirrors production Worker with staging bindings
# ---------------------------------------------------------------------------

resource "cloudflare_record" "staging" {
  zone_id = local.zone_id
  name    = "staging"
  content = local.worker_placeholder_ip
  type    = "A"
  proxied = true

  comment = "Staging Worker (separate D1/KV bindings). Managed by Terraform."
}


# ---------------------------------------------------------------------------
# Multi-brand Workers: AcmeDice, AcmeGate, AcmeVegas
# Each brand has its own Worker with jurisdiction-specific game catalogues.
# ---------------------------------------------------------------------------

resource "cloudflare_record" "acmedice" {
  zone_id = local.zone_id
  name    = "acmedice"
  content = local.worker_placeholder_ip
  type    = "A"
  proxied = true

  comment = "AcmeDice brand Worker (EU market). Managed by Terraform."
}

resource "cloudflare_record" "acmegate" {
  zone_id = local.zone_id
  name    = "acmegate"
  content = local.worker_placeholder_ip
  type    = "A"
  proxied = true

  comment = "AcmeGate brand Worker (UKGC-licensed). Managed by Terraform."
}

resource "cloudflare_record" "acmevegas" {
  zone_id = local.zone_id
  name    = "acmevegas"
  content = local.worker_placeholder_ip
  type    = "A"
  proxied = true

  comment = "AcmeVegas brand Worker (MGA-licensed). Managed by Terraform."
}


# ---------------------------------------------------------------------------
# Outputs — useful for CI/CD verification and cross-module references
# ---------------------------------------------------------------------------

output "worker_hostnames" {
  description = "Cloudflare hostnames managed by this module"
  value = {
    primary    = "cloudflare.cloud-acmetocasino.com"
    api        = "api.cloud-acmetocasino.com"
    payments   = "payments.cloud-acmetocasino.com"
    backoffice = "backoffice.cloud-acmetocasino.com"
    risk       = "risk.cloud-acmetocasino.com"
    exclusion  = "exclusion.cloud-acmetocasino.com"
    staging    = "staging.cloud-acmetocasino.com"
    acmedice   = "acmedice.cloud-acmetocasino.com"
    acmegate   = "acmegate.cloud-acmetocasino.com"
    acmevegas  = "acmevegas.cloud-acmetocasino.com"
  }
}

output "pages_hostnames" {
  description = "Cloudflare Pages hostnames"
  value = {
    casino     = "casino.cloud-acmetocasino.com"
    bet_brazil = "bet-brazil.cloud-acmetocasino.com"
  }
}
