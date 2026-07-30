# ===========================================================================
# Consul Service Discovery Configuration for Gambling Microservices
# ===========================================================================
#
# Chapter 42 - Complete Platform Architecture
#
# Configures HashiCorp Consul for service discovery, health checking,
# and configuration management across the gambling platform.
#
# GLI-11 Compliance:
# - Service health must be continuously monitored
# - RNG service must have enhanced health checks
# - Payment services require circuit breaker integration
# - All service-to-service communication must be authorized
#
# Usage:
#   consul agent -config-file=consul-config.hcl
#   consul validate consul-config.hcl
#
# ===========================================================================

# ---------------------------------------------------------------------------
# Server Configuration
# ---------------------------------------------------------------------------

datacenter = "casino-prod-eu"
node_name  = "consul-server-1"
data_dir   = "/opt/consul/data"
log_level  = "INFO"

server           = true
bootstrap_expect = 3    # 3-node Consul cluster for HA

bind_addr   = "0.0.0.0"
client_addr = "0.0.0.0"

ui_config {
  enabled = true
}

# Encryption (gossip protocol)
encrypt = "CHANGE_ME_BASE64_ENCODED_KEY"

# TLS Configuration
tls {
  defaults {
    ca_file   = "/etc/consul/tls/ca.pem"
    cert_file = "/etc/consul/tls/server-cert.pem"
    key_file  = "/etc/consul/tls/server-key.pem"
    verify_incoming = true
    verify_outgoing = true
  }
  internal_rpc {
    verify_server_hostname = true
  }
}

# ACL Configuration
acl {
  enabled                  = true
  default_policy           = "deny"
  enable_token_persistence = true

  tokens {
    initial_management = "CHANGE_ME_MANAGEMENT_TOKEN"
    agent              = "CHANGE_ME_AGENT_TOKEN"
  }
}

# Connect (Service Mesh) Configuration
connect {
  enabled = true
}

# Performance tuning for gambling platform
performance {
  raft_multiplier = 1    # Lowest latency for leader election
}

# Telemetry
telemetry {
  prometheus_retention_time = "24h"
  disable_hostname          = true
}

# ---------------------------------------------------------------------------
# Service Definitions
# ---------------------------------------------------------------------------

# ── Game Engine Service ──
services {
  name = "game-engine"
  id   = "game-engine-1"
  port = 8080
  tags = ["tier-1", "critical", "gli-11", "v1"]

  meta {
    version     = "1.0.0"
    team        = "game-platform"
    tier        = "critical"
    gli_11      = "true"
    environment = "production"
  }

  checks = [
    {
      name     = "HTTP Health"
      http     = "http://localhost:8080/health"
      interval = "5s"
      timeout  = "3s"

      deregister_critical_service_after = "90s"
    },
    {
      name     = "Game Round Latency"
      http     = "http://localhost:8080/health/detailed"
      interval = "10s"
      timeout  = "5s"
      header   = { "Accept" = ["application/json"] }
    },
    {
      name     = "TCP Port Check"
      tcp      = "localhost:8080"
      interval = "10s"
      timeout  = "2s"
    }
  ]

  connect {
    sidecar_service {
      proxy {
        upstreams = [
          {
            destination_name = "rng-service"
            local_bind_port  = 9001
          },
          {
            destination_name = "wallet-service"
            local_bind_port  = 9002
          },
          {
            destination_name = "player-service"
            local_bind_port  = 9003
          }
        ]
      }
    }
  }
}

# ── RNG Service (GLI-11 Critical) ──
services {
  name = "rng-service"
  id   = "rng-service-1"
  port = 8443
  tags = ["tier-1", "critical", "gli-11", "rng", "isolated"]

  meta {
    version     = "1.0.0"
    team        = "rng-security"
    tier        = "critical"
    gli_11      = "true"
    isolation   = "full"
    description = "GLI-11 Certified Random Number Generator"
  }

  checks = [
    {
      name     = "RNG Service Health"
      http     = "http://localhost:8443/health"
      interval = "5s"
      timeout  = "2s"

      deregister_critical_service_after = "30s"
    },
    {
      name     = "RNG Statistical Health"
      http     = "http://localhost:8443/health/detailed"
      interval = "30s"
      timeout  = "10s"
      header   = { "X-API-Key" = ["internal-health-check-key"] }
    },
    {
      name     = "RNG Entropy Level"
      args     = ["/usr/local/bin/check-rng-entropy.sh"]
      interval = "15s"
      timeout  = "5s"
    }
  ]

  # RNG service should NOT connect to most services (isolation principle)
  connect {
    sidecar_service {
      proxy {
        upstreams = []  # No outbound connections - RNG is a leaf service
      }
    }
  }
}

# ── Payment Service ──
services {
  name = "payment-service"
  id   = "payment-service-1"
  port = 8080
  tags = ["tier-1", "financial", "pci-dss", "v1"]

  meta {
    version    = "1.0.0"
    team       = "payments"
    tier       = "financial-critical"
    pci_scope  = "true"
    compliance = "pci-dss-level-1"
  }

  checks = [
    {
      name     = "HTTP Health"
      http     = "http://localhost:8080/health"
      interval = "5s"
      timeout  = "3s"
    },
    {
      name     = "PSP Connectivity"
      http     = "http://localhost:8080/health/psp"
      interval = "30s"
      timeout  = "10s"
    },
    {
      name     = "Payment Queue Depth"
      http     = "http://localhost:8080/health/queue"
      interval = "15s"
      timeout  = "5s"
    }
  ]

  connect {
    sidecar_service {
      proxy {
        upstreams = [
          {
            destination_name = "wallet-service"
            local_bind_port  = 9002
          },
          {
            destination_name = "player-service"
            local_bind_port  = 9003
          },
          {
            destination_name = "notification-service"
            local_bind_port  = 9004
          }
        ]
      }
    }
  }
}

# ── Wallet Service ──
services {
  name = "wallet-service"
  id   = "wallet-service-1"
  port = 8080
  tags = ["tier-1", "financial", "v1"]

  meta {
    version = "1.0.0"
    team    = "payments"
    tier    = "financial-critical"
  }

  checks = [
    {
      name     = "HTTP Health"
      http     = "http://localhost:8080/health"
      interval = "5s"
      timeout  = "3s"
    },
    {
      name     = "Database Connection"
      http     = "http://localhost:8080/health/db"
      interval = "10s"
      timeout  = "5s"
    }
  ]

  connect {
    sidecar_service {
      proxy {
        upstreams = [
          {
            destination_name = "player-service"
            local_bind_port  = 9003
          }
        ]
      }
    }
  }
}

# ── Player Service ──
services {
  name = "player-service"
  id   = "player-service-1"
  port = 8080
  tags = ["tier-2", "player-facing", "v1"]

  meta {
    version = "1.0.0"
    team    = "player-platform"
    tier    = "player-facing"
  }

  checks = [
    {
      name     = "HTTP Health"
      http     = "http://localhost:8080/health"
      interval = "10s"
      timeout  = "3s"
    }
  ]

  connect {
    sidecar_service {
      proxy {
        upstreams = [
          {
            destination_name = "notification-service"
            local_bind_port  = 9004
          }
        ]
      }
    }
  }
}

# ── Notification Service ──
services {
  name = "notification-service"
  id   = "notification-service-1"
  port = 8080
  tags = ["tier-3", "non-critical", "v1"]

  meta {
    version = "1.0.0"
    team    = "communications"
    tier    = "non-critical"
  }

  checks = [
    {
      name     = "HTTP Health"
      http     = "http://localhost:8080/health"
      interval = "15s"
      timeout  = "5s"
    }
  ]
}

# ── Compliance Service ──
services {
  name = "compliance-service"
  id   = "compliance-service-1"
  port = 8080
  tags = ["tier-2", "compliance", "gli-11", "v1"]

  meta {
    version = "1.0.0"
    team    = "compliance"
    tier    = "compliance"
  }

  checks = [
    {
      name     = "HTTP Health"
      http     = "http://localhost:8080/health"
      interval = "10s"
      timeout  = "5s"
    }
  ]

  connect {
    sidecar_service {
      proxy {
        upstreams = [
          {
            destination_name = "player-service"
            local_bind_port  = 9003
          },
          {
            destination_name = "game-engine"
            local_bind_port  = 9005
          }
        ]
      }
    }
  }
}

# ── Backoffice Service ──
services {
  name = "backoffice"
  id   = "backoffice-1"
  port = 3000
  tags = ["tier-3", "internal", "v1"]

  meta {
    version = "1.0.0"
    team    = "backoffice"
    tier    = "non-critical"
  }

  checks = [
    {
      name     = "HTTP Health"
      http     = "http://localhost:3000/health"
      interval = "30s"
      timeout  = "5s"
    }
  ]
}

# ---------------------------------------------------------------------------
# Service Defaults (intentions)
# ---------------------------------------------------------------------------

# Default deny all service-to-service communication
# Allow specific paths via consul intentions:
#
#   consul intention create game-engine rng-service
#   consul intention create game-engine wallet-service
#   consul intention create game-engine player-service
#   consul intention create payment-service wallet-service
#   consul intention create payment-service player-service
#   consul intention create payment-service notification-service
#   consul intention create compliance-service player-service
#   consul intention create compliance-service game-engine
#
# Deny direct access to RNG from non-game services:
#   consul intention create -deny payment-service rng-service
#   consul intention create -deny backoffice rng-service
#   consul intention create -deny notification-service rng-service

# ---------------------------------------------------------------------------
# KV Store Defaults (configuration management)
# ---------------------------------------------------------------------------

# Platform-wide configuration stored in Consul KV:
#
#   consul kv put config/platform/environment production
#   consul kv put config/platform/jurisdiction MGA
#   consul kv put config/rng/min_entropy_sources 2
#   consul kv put config/rng/reseed_interval_seconds 60
#   consul kv put config/payments/max_deposit_eur 50000
#   consul kv put config/payments/max_withdrawal_eur 25000
#   consul kv put config/games/max_bet_eur 1000
#   consul kv put config/responsible_gaming/session_timeout_minutes 60
#   consul kv put config/responsible_gaming/reality_check_interval_minutes 30
