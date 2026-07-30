# Ansible Configuration Management for Multi-State US Gaming Infrastructure

Production-derived Ansible playbooks and roles for managing bare-metal servers
across multiple US state jurisdictions. Each state operates an isolated Kubernetes
cluster on physical hardware in a colocation facility, with per-state network
segmentation, monitoring tags, and PostgreSQL database configurations.

## Architecture

```
inventories/
  dc-colo/
    nj/hosts.yml      # New Jersey cluster (masters, workers, DBs)
    pa/hosts.yml       # Pennsylvania cluster
    co/hosts.yml       # Colorado cluster
    ...                # 12+ US state clusters

group_vars/
  nj_dc_machines.yaml  # NJ-specific network ranges, monitoring tags
  pa_dc_machines.yaml  # PA-specific configuration
  ...

roles/
  dc-initial-config/   # Base server provisioning (network, hostname, packages)
  docker-manager/       # Docker lifecycle management (prune, logrotate)
  pg-hba-config/        # PostgreSQL HBA rules per jurisdiction
  ssh-ciphers/          # SSH cipher hardening for compliance
```

## Key Concepts

- **Per-state isolation**: Each US state's gambling commission requires physical
  separation of gaming servers. Ansible inventory groups map 1:1 to jurisdictions.
- **Datadog monitoring tags**: Every server is tagged with jurisdiction, brand,
  environment, and infrastructure type for regulatory audit trails.
- **PostgreSQL HBA per jurisdiction**: Database access rules are state-specific
  because player data cannot cross state boundaries.

## Usage

```bash
# Configure all servers in specific states
ansible-playbook dc-phys-servers.yml -l de_dc_machines:la_dc_machines

# Run only monitoring configuration
ansible-playbook dc-phys-servers.yml --tags monitoring

# Apply SSH cipher hardening
ansible-playbook dc-phys-servers.yml --tags security
```

## Source

Adapted from production infrastructure managing 100+ bare-metal servers across
12 US state jurisdictions. All IPs, hostnames, credentials, and vendor names
have been sanitized.
