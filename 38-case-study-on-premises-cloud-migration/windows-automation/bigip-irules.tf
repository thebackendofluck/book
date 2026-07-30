# Companion code for "The Backend of Luck" - Chapter 38, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Terraform: F5 BIG-IP iRules and Load Balancer Configuration
# AcmetoCasino on-premises application delivery
# Multi-customer RGS routing with host-based and URI-based rules

terraform {
  required_providers {
    bigip = {
      source = "f5networks/bigip"
    }
  }
  required_version = ">= 0.13"
}

provider "bigip" {
  address  = var.hostname
  username = var.username
  password = var.password
}

variable "hostname" {}
variable "username" {}
variable "password" {}
variable "name" {}

# ============================================
# iRules - Host-based routing for RGS traffic
# Each B2B customer gets dedicated pool routing
# ============================================

resource "bigip_ltm_irule" "customer_a_rgs_irule" {
  name  = "/Common/customer_a_rgs_irule"
  irule = <<EOF
when HTTP_REQUEST {
    set HOST [string tolower [HTTP::host]]
    set URI [string tolower [HTTP::uri]]
    switch -glob $HOST {
        "acmergs.acmetocasino.com"  { pool acmergs.acmetocasino.com }
        "acme2rgs.acmetocasino.com" { pool acme2rgs.acmetocasino.com }
    }
    unset HOST
    unset URI
}
EOF
}

resource "bigip_ltm_irule" "customer_a_engine_irule" {
  name  = "/Common/customer_a_engine_irule"
  irule = <<EOF
when HTTP_REQUEST {
    set HOST [string tolower [HTTP::host]]
    set URI [string tolower [HTTP::uri]]
    switch -glob $URI {
        "/customer_a/*" {pool customer_a_engine}
        "/customer_b/*" {pool customer_b_engine}
        "/customer_c/*" {pool customer_c_engine}
    }
    unset HOST
    unset URI
}
EOF
}

resource "bigip_ltm_irule" "content_irule" {
  name  = "/Common/${var.name}-contentirule"
  irule = <<EOF
when HTTP_REQUEST {
    set HOST [string tolower [HTTP::host]]
    set URI [string tolower [HTTP::uri]]
    switch -glob $HOST {
        "content.acmetocasino.com"     { pool content }
        "fileservice.acmetocasino.com" { pool fileservice }
    }
    unset HOST
    unset URI
}
EOF
}

# ============================================
# Health Monitors - per-customer HTTP checks
# ============================================

resource "bigip_ltm_monitor" "customer_a_rgs_monitor" {
  name     = "/Common/customer_a_rgs.acmetocasino.com-MON"
  parent   = "/Common/http"
  send     = "GET /check.aspx HTTP/1.1\r\nHost: ${var.name}rgsmon.acmetocasino.com\r\nConnection: Close\r\n\r\n"
  timeout  = "10"
  interval = "3"
  receive  = "applications ok"
}

resource "bigip_ltm_monitor" "customer_a_engine_monitor" {
  name     = "/Common/customer_a_engine.acmetocasino.com-MON"
  parent   = "/Common/http"
  send     = "GET /customer_a/check.aspx HTTP/1.1\r\nHost: rgsengines\r\nConnection: Close\r\n\r\n"
  timeout  = "10"
  interval = "3"
  receive  = "applications ok"
}

resource "bigip_ltm_monitor" "content_monitor" {
  name     = "/Common/content.acmetocasino.com-MON"
  parent   = "/Common/http"
  send     = "GET /check.aspx HTTP/1.1\r\nHost: content.acmetocasino.com\r\nConnection: Close\r\n\r\n"
  timeout  = "10"
  interval = "3"
  receive  = "applications ok"
}

# ============================================
# Node Definitions - RGS and Engine backends
# ============================================

resource "bigip_ltm_node" "rgs_nodes" {
  name             = "/Common/${var.name}-rgs0${count.index}"
  address          = "1.1.1.10${count.index}"
  connection_limit = "0"
  count            = 10
  dynamic_ratio    = "1"
  monitor          = "/Common/icmp"
  description      = "${var.name}-RGS${count.index} Node"
  rate_limit       = "disabled"
  fqdn {
    address_family = "ipv4"
    interval       = "3000"
  }
}

resource "bigip_ltm_node" "engine_nodes" {
  name             = "/Common/${var.name}-engine0${count.index}"
  address          = "1.1.1.15${count.index}"
  connection_limit = "0"
  count            = 10
  dynamic_ratio    = "1"
  monitor          = "/Common/icmp"
  description      = "${var.name}-ENGINE${count.index} Node"
  rate_limit       = "disabled"
  fqdn {
    address_family = "ipv4"
    interval       = "3000"
  }
}

# ============================================
# Pools - per-customer with round-robin LB
# ============================================

resource "bigip_ltm_pool" "customer_a_rgs_pool" {
  name                   = "/Common/customer_a_rgs.acmetocasino.com"
  load_balancing_mode    = "round-robin"
  minimum_active_members = 1
  description            = "Customer A RGS pool"
  monitors               = [bigip_ltm_monitor.customer_a_rgs_monitor.name]
}

resource "bigip_ltm_pool_attachment" "customer_a_rgs_attach" {
  pool  = bigip_ltm_pool.customer_a_rgs_pool.name
  node  = "${bigip_ltm_node.rgs_nodes[count.index].name}:80"
  count = 10
}

# ============================================
# Virtual Servers - public-facing entry points
# ============================================

resource "bigip_ltm_virtual_server" "rgs_vs" {
  name                       = "/Common/${var.name}rgs-vs"
  destination                = "10.151.2.130"
  description                = "${var.name}rgs-vs"
  port                       = 80
  security_log_profiles      = ["/Common/global-network"]
  source_address_translation = "automap"
}

resource "bigip_ltm_virtual_server" "engine_vs" {
  name                       = "/Common/${var.name}engine-vs"
  destination                = "10.151.2.133"
  description                = "${var.name}engine-vs"
  port                       = 80
  security_log_profiles      = ["/Common/global-network"]
  source_address_translation = "automap"
}

resource "bigip_ltm_virtual_server" "content_vs" {
  name                       = "/Common/${var.name}content-vs"
  destination                = "10.151.2.135"
  description                = "${var.name}content-vs"
  port                       = 80
  security_log_profiles      = ["/Common/global-network"]
  source_address_translation = "automap"
}
