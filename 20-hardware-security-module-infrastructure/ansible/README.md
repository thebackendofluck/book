# Chapter 20 — HSM Infrastructure: Ansible Playbooks

These files are part of the book's simulation platform for Chapter 20 (HSM Infrastructure).

## Files

| File | Purpose |
|------|---------|
| `playbooks/playbook-yubihsm-setup.yml` | Installs and configures a YubiHSM 2 device: yubihsm-connector service, PKCS#11 integration, FIPS mode, and initial key generation test |
| `inventory/hosts.yml` | Inventory defining the `hsm_servers` host group |

## Dependencies

- Target hosts: Ubuntu 22.04+ or Debian 12+
- YubiHSM 2 device connected via USB on the target host
- Root/sudo access on target hosts
- No prior playbook run required — this is the foundational HSM setup

## How to Run

```bash
# Install dependencies
ansible-galaxy install -r requirements.yml   # if present

# Dry-run first (recommended)
ansible-playbook -i inventory/hosts.yml playbooks/playbook-yubihsm-setup.yml --check --diff

# Full deploy
ansible-playbook -i inventory/hosts.yml playbooks/playbook-yubihsm-setup.yml
```

## Source

Original file: `new-platform/ansible/hsm/`

These playbooks are from the book's simulation platform. They demonstrate production-grade HSM provisioning for a regulated iGaming environment.
