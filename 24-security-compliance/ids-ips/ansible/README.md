# Suricata IDS/IPS — Ansible Deployment

Ansible role and playbooks for deploying Suricata 7.x as a production IDS or IPS on bare-metal and VM servers in an iGaming platform.

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Ansible | >= 2.14 |
| Python | >= 3.9 (control node) |
| Target OS | Ubuntu 22.04 / 24.04, Rocky Linux 9 |
| RAM (target) | >= 4 GB (8 GB recommended) |
| CPU (target) | >= 2 cores (4+ recommended) |

Install required Ansible collections:

```bash
ansible-galaxy collection install \
  ansible.posix \
  community.general \
  ansible.utils
```

Encrypt the vault file before first use:

```bash
ansible-vault encrypt group_vars/vault.yml
```

## Directory Structure

```
ansible/
├── inventory/
│   ├── production.yml       # Production sensors (DMZ, APP, DB, PAYMENT zones)
│   └── staging.yml          # Staging sensors
├── group_vars/
│   ├── all.yml              # Organisation-wide variables
│   ├── suricata_sensors.yml # All Suricata variables with comments
│   └── vault.yml            # Encrypted secrets (ET Pro oinkcode, Slack webhook)
├── roles/
│   └── suricata/
│       ├── defaults/main.yml  # Safe defaults for all variables
│       ├── tasks/             # install, configure, tuning, rules, monitoring, hardening
│       ├── templates/         # suricata.yaml, service unit, health check, etc.
│       ├── handlers/main.yml  # restart, reload-rules, validate
│       └── files/
│           └── igaming-custom.rules  # Custom rules for iGaming threats
├── playbooks/
│   ├── site.yml             # Master playbook
│   ├── deploy_suricata.yml  # Full deployment
│   ├── update_rules.yml     # Rules-only hot update
│   └── rotate_to_ips.yml    # Controlled IDS → IPS transition
└── tests/
    └── test_suricata.yml    # Post-deployment validation
```

## Quick Start

### 1. Full deployment to staging

```bash
ansible-playbook -i inventory/staging.yml playbooks/site.yml \
  --ask-vault-pass
```

### 2. Full deployment to production

```bash
ansible-playbook -i inventory/production.yml playbooks/site.yml \
  --ask-vault-pass
```

### 3. Deploy to a specific zone or host

```bash
# Single host
ansible-playbook -i inventory/production.yml playbooks/deploy_suricata.yml \
  --limit ids-dmz-01.prod.igaming.internal

# All DMZ sensors
ansible-playbook -i inventory/production.yml playbooks/deploy_suricata.yml \
  --limit dmz_sensors
```

### 4. Update rules only (no restart)

```bash
# All sensors — safe to run any time
ansible-playbook -i inventory/production.yml playbooks/update_rules.yml

# Single sensor
ansible-playbook -i inventory/production.yml playbooks/update_rules.yml \
  --limit ids-app-01.prod.igaming.internal
```

### 5. Run validation tests

```bash
ansible-playbook -i inventory/production.yml tests/test_suricata.yml
```

### 6. Transition a sensor from IDS to IPS mode

After 30+ days of IDS operation with confirmed low drop rates:

```bash
# Check the sensor passes all safety gates before committing
ansible-playbook -i inventory/production.yml playbooks/rotate_to_ips.yml \
  --limit ids-payment-01.prod.igaming.internal --check

# Execute the rotation
ansible-playbook -i inventory/production.yml playbooks/rotate_to_ips.yml \
  --limit ids-payment-01.prod.igaming.internal
```

## Key Variables

All variables are documented with comments in `group_vars/suricata_sensors.yml`.
The most important ones to review before deployment:

| Variable | Default | Description |
|----------|---------|-------------|
| `suricata_version` | `7.0.7` | Suricata version to install |
| `suricata_mode` | `ids` | `ids` (passive) or `ips` (inline) |
| `suricata_home_net` | RFC-1918 | Your network address space |
| `stream_memcap` | `1gb` | Stream reassembly memory cap |
| `flow_memcap` | `512mb` | Flow tracking memory cap |
| `worker_count` | `4` | Worker thread count (set per-host) |
| `afpacket_ring_size` | `65536` | AF_PACKET ring buffer slots |
| `et_open_enabled` | `true` | Enable Emerging Threats Open rules |
| `et_pro_enabled` | `false` | Enable ET Pro (requires oinkcode in vault) |
| `kernel_drops_warn_pct` | `0.01` | Drop rate warning threshold (%) |
| `suricata_eve_payload` | `true` | Log raw payloads in EVE JSON |
| `log_retention_days` | `30` | EVE JSON retention period |

### Per-host overrides

Set host-specific values directly in the inventory:

```yaml
ids-payment-01.prod.igaming.internal:
  ansible_host: 10.0.3.11
  monitored_interface: ens6f0
  sensor_zone: payment
  worker_count: 2
  suricata_eve_payload: false      # PCI-DSS: no raw payload in payment zone
  suricata_eve_payload_printable: false
  log_retention_days: 90           # PCI-DSS requires 90 days
```

## Tags Reference

Run only specific phases using `--tags`:

```bash
--tags install      # Package installation and directories only
--tags configure    # Configuration file deployment only
--tags tuning       # NIC, kernel, NUMA, and CPU tuning only
--tags rules        # Rule fetch and hot reload only
--tags monitoring   # Health check and cron setup only
--tags hardening    # Permissions, AppArmor/SELinux, auditing only
```

## Custom iGaming Rules

The file `roles/suricata/files/igaming-custom.rules` contains rules covering:

- **Bonus abuse** (brute-force, multiple claims, manipulation)
- **AML patterns** (smurfing, chip-dumping, high-risk jurisdictions)
- **Account takeover** (credential stuffing, password spray, session hijacking)
- **Multi-accounting** (same-IP registrations, VPN detection)
- **Game manipulation** (RNG seed injection, outcome tampering, bot detection)
- **Data exfiltration** (PII leaks, card numbers in responses)
- **API abuse** (scraping, SQLi, path traversal)

SID range: `9000000–9000799`.

## Performance Sizing Guide

| Operator size | RAM | CPUs | `stream_memcap` | `flow_memcap` | `worker_count` |
|---------------|-----|------|-----------------|---------------|----------------|
| Small (< 1 Gbps) | 8 GB | 4 | 256mb | 128mb | 2 |
| Medium (1–10 Gbps) | 32 GB | 8–16 | 1gb | 512mb | 8 |
| Large (10–40 Gbps) | 64+ GB | 32+ | 4gb | 2gb | 16–32 |

## Monitoring

- **Health check**: `/usr/local/bin/suricata-health-check` runs every 5 minutes via cron.
- **Prometheus metrics**: exported to `/var/lib/node_exporter/textfile_collector/suricata.prom`.
- **Rule updates**: `/usr/local/bin/suricata-rule-update` runs daily at 03:00 UTC.
- **Slack alerts**: configured via `vault_slack_webhook_url` in `group_vars/vault.yml`.
