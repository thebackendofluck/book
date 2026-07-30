# openbao.hcl — OpenBao server configuration template
# Node: bao-01 (with YubiHSM 2 directly connected via USB)
# Replace placeholder values with node-specific settings before deploying.
#
# References:
#   - OpenBao docs: https://openbao.org/docs/configuration/
#   - PCI DSS v4.0.1 Req. 3.5, 3.6, 3.7
#   - FIPS 140-2 Level 3 (YubiHSM 2 wrap key)

# ── Global settings ───────────────────────────────────────────────────────────
ui             = true
disable_mlock  = false      # MUST be false in production — prevents RAM swap of secrets
log_level      = "info"
log_format     = "json"     # Structured logging — required for SIEM integration

# ── Raft integrated storage ───────────────────────────────────────────────────
# No external Consul dependency. 3-node cluster for 99.95% availability.
storage "raft" {
  path    = "/opt/openbao/data"
  node_id = "bao-01"           # CHANGE per node: bao-01 | bao-02 | bao-03

  # Retry-join: cluster members automatically join on restart
  retry_join {
    leader_api_addr         = "https://bao-02:8200"
    leader_ca_cert_file     = "/opt/openbao/tls/ca.crt"
    leader_client_cert_file = "/opt/openbao/tls/bao-01.crt"   # CHANGE per node
    leader_client_key_file  = "/opt/openbao/tls/bao-01.key"   # CHANGE per node
  }
  retry_join {
    leader_api_addr         = "https://bao-03:8200"
    leader_ca_cert_file     = "/opt/openbao/tls/ca.crt"
    leader_client_cert_file = "/opt/openbao/tls/bao-01.crt"   # CHANGE per node
    leader_client_key_file  = "/opt/openbao/tls/bao-01.key"   # CHANGE per node
  }

  # Performance tuning for iGaming workload
  performance_multiplier = 1
}

# ── TLS listener ──────────────────────────────────────────────────────────────
# mTLS: VMs present client certificates issued by the PKI engine (production)
listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_cert_file      = "/opt/openbao/tls/bao-01.crt"   # CHANGE per node
  tls_key_file       = "/opt/openbao/tls/bao-01.key"   # CHANGE per node
  tls_client_ca_cert = "/opt/openbao/tls/ca.crt"

  # TLS 1.3 minimum — PCI DSS Req. 4
  tls_min_version = "tls13"

  # Enable mTLS once VMs have PKI-issued certs:
  # tls_require_and_verify_client_cert = true

  # Restrict cipher suites to FIPS-approved (optional hardening)
  # tls_cipher_suites = "TLS_AES_256_GCM_SHA384,TLS_CHACHA20_POLY1305_SHA256"
}

# ── PKCS#11 auto-unseal via YubiHSM 2 ────────────────────────────────────────
# OpenBao 2.2+ OSS — PKCS#11 seal is FREE (no Enterprise license needed).
# The wrap key (bao-root-key-aes) lives ONLY in the HSM hardware.
# AES-GCM mechanism (0x1087 = CKM_AES_GCM) wraps the cluster's barrier key.
#
# PIN: NEVER put the PIN here — it is injected via environment variable.
# See: /etc/systemd/system/openbao.service.d/hsm.conf (chmod 600)
seal "pkcs11" {
  lib       = "/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so"
  slot      = "0"
  key_label = "bao-root-key-aes"
  mechanism = "0x1087"    # CKM_AES_GCM — AES-256-GCM wrap
  # token_label = "YubiHSM"  # optional: match by token label instead of slot
}

# ── Cluster addresses ─────────────────────────────────────────────────────────
# api_addr: advertised to clients and peer nodes
# cluster_addr: Raft/gossip port (internal cluster communication)
api_addr     = "https://bao-01:8200"    # CHANGE per node IP/hostname
cluster_addr = "https://bao-01:8201"    # CHANGE per node IP/hostname

# ── Service registration (optional — Consul/Nomad) ───────────────────────────
# service_registration "consul" {
#   address = "127.0.0.1:8500"
#   scheme  = "https"
#   tls_ca_file = "/opt/openbao/tls/consul-ca.crt"
# }

# ── Telemetry — Prometheus metrics for Grafana ────────────────────────────────
# Exposes /v1/sys/metrics endpoint for Prometheus scraping
telemetry {
  prometheus_retention_time = "60s"
  disable_hostname          = false
  # Uncomment for StatsD integration:
  # statsd_address = "127.0.0.1:8125"
}
