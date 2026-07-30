# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Chapter 24 - IP Detection Pipeline
# Terraform configuration for Cloudflare KV namespaces and D1 database.
#
# Prerequisites:
#   - Cloudflare provider ~> 4.x
#   - CLOUDFLARE_API_TOKEN environment variable with Workers + KV + D1 permissions
#
# Usage:
#   terraform init
#   terraform plan
#   terraform apply
#
# After apply, copy the output values into wrangler.toml.

terraform {
  required_version = ">= 1.5"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

provider "cloudflare" {
  # api_token is read from CLOUDFLARE_API_TOKEN env var.
  # Never hard-code credentials here.
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "cloudflare_account_id" {
  description = "Cloudflare account ID (found in the dashboard URL)"
  type        = string
}

variable "environment" {
  description = "Deployment environment: staging or production"
  type        = string
  default     = "production"

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be 'staging' or 'production'."
  }
}

locals {
  name_suffix = var.environment == "production" ? "" : "-${var.environment}"
}

# ---------------------------------------------------------------------------
# KV Namespace: IP_BLACKLIST
# Stores banned IPs with optional TTL expiration.
# ---------------------------------------------------------------------------

resource "cloudflare_workers_kv_namespace" "ip_blacklist" {
  account_id = var.cloudflare_account_id
  title      = "ip-blacklist${local.name_suffix}"
}

# ---------------------------------------------------------------------------
# KV Namespace: DEVICE_FINGERPRINTS
# Stores JA3 hash history per IP and per hash.
# ---------------------------------------------------------------------------

resource "cloudflare_workers_kv_namespace" "device_fingerprints" {
  account_id = var.cloudflare_account_id
  title      = "device-fingerprints${local.name_suffix}"
}

# ---------------------------------------------------------------------------
# KV Namespace: FRAUD_VELOCITY
# Stores velocity counters (requests/min, requests/5min, requests/hour).
# ---------------------------------------------------------------------------

resource "cloudflare_workers_kv_namespace" "fraud_velocity" {
  account_id = var.cloudflare_account_id
  title      = "fraud-velocity${local.name_suffix}"
}

# ---------------------------------------------------------------------------
# KV Namespace: SANCTIONS_LIST
# Stores OFAC country codes and SDN name tokens.
# ---------------------------------------------------------------------------

resource "cloudflare_workers_kv_namespace" "sanctions_list" {
  account_id = var.cloudflare_account_id
  title      = "sanctions-list${local.name_suffix}"
}

# ---------------------------------------------------------------------------
# Seed: Sanctions country data
# Bootstrap the top-level OFAC/EU sanctioned countries into KV.
# This list mirrors HARDCODED_SANCTIONED_COUNTRIES in sanctions.ts
# but KV allows runtime updates without redeployment.
# ---------------------------------------------------------------------------

locals {
  sanctioned_countries = toset([
    "CU", "IR", "KP", "RU", "SY", "VE", "BY",
    "MM", "SS", "SD", "SO", "LY", "YE", "ZW",
    "CF", "ML", "NI", "HT",
  ])
}

resource "cloudflare_workers_kv" "sanctioned_country" {
  for_each     = local.sanctioned_countries
  account_id   = var.cloudflare_account_id
  namespace_id = cloudflare_workers_kv_namespace.sanctions_list.id
  key          = "sanctions:country:${each.value}"
  value        = "1"
}

# ---------------------------------------------------------------------------
# D1 Database: PLAYER_DB
# Stores player KYC verification records.
# ---------------------------------------------------------------------------

resource "cloudflare_d1_database" "player_db" {
  account_id = var.cloudflare_account_id
  name       = "casino-player-db${local.name_suffix}"
}

# ---------------------------------------------------------------------------
# D1 Migration: player_kyc table
# Cloudflare Terraform provider does not directly execute SQL migrations —
# run migrations with wrangler after provisioning:
#
#   wrangler d1 execute casino-player-db \
#     --file=./migrations/001_create_player_kyc.sql \
#     --env production
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Outputs (paste into wrangler.toml after `terraform apply`)
# ---------------------------------------------------------------------------

output "ip_blacklist_kv_id" {
  description = "Paste this into wrangler.toml as IP_BLACKLIST id"
  value       = cloudflare_workers_kv_namespace.ip_blacklist.id
}

output "device_fingerprints_kv_id" {
  description = "Paste this into wrangler.toml as DEVICE_FINGERPRINTS id"
  value       = cloudflare_workers_kv_namespace.device_fingerprints.id
}

output "fraud_velocity_kv_id" {
  description = "Paste this into wrangler.toml as FRAUD_VELOCITY id"
  value       = cloudflare_workers_kv_namespace.fraud_velocity.id
}

output "sanctions_list_kv_id" {
  description = "Paste this into wrangler.toml as SANCTIONS_LIST id"
  value       = cloudflare_workers_kv_namespace.sanctions_list.id
}

output "player_db_d1_id" {
  description = "Paste this into wrangler.toml as PLAYER_DB database_id"
  value       = cloudflare_d1_database.player_db.id
}
