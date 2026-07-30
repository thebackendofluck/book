# Chapter 27 — Data Residency & Encryption: Ansible Playbooks

These files are part of the book's simulation platform for Chapter 27 (Data Residency & Encryption).

## Files

| File | Purpose |
|------|---------|
| `playbooks/playbook-disk-encryption.yml` | Deploys LUKS2 disk encryption on the data partition of database hosts. Configures auto-mount via crypttab/fstab. IMPORTANT: formats the target partition — only run on new/empty disks |
| `playbooks/playbook-mariadb-tde.yml` | Deploys MariaDB with native TDE using the file-key-management plugin. Enables `innodb_encrypt_tables = FORCE`, binlog encryption, and redo/undo log encryption. Keys are generated via HSM |
| `playbooks/playbook-postgresql-tde.yml` | Deploys PostgreSQL 16 with pgcrypto column-level encryption, SSL connections, and automated key rotation. Includes `encrypt_pii`/`decrypt_pii` helper functions for PCI-DSS 3.4 PAN protection |
| `inventory/hosts.yml` | Inventory defining host groups for database and encryption targets |

## Dependencies

Run playbooks in this order:

1. **Chapter 20 HSM setup must be complete first** — encryption keys are HSM-backed
2. `playbook-disk-encryption.yml` — encrypt the data partition before installing the DB engine
3. `playbook-mariadb-tde.yml` or `playbook-postgresql-tde.yml` — install the database with TDE on the encrypted partition

## How to Run

```bash
# Dry-run first (strongly recommended — disk-encryption is destructive)
ansible-playbook -i inventory/hosts.yml playbooks/playbook-disk-encryption.yml --check --diff

# Step 1 — Encrypt the data partition
ansible-playbook -i inventory/hosts.yml playbooks/playbook-disk-encryption.yml

# Step 2a — MariaDB with TDE (primary node only first)
ansible-playbook -i inventory/hosts.yml playbooks/playbook-mariadb-tde.yml --limit mdb-tde-01

# Step 2b — PostgreSQL with TDE (primary node only first)
ansible-playbook -i inventory/hosts.yml playbooks/playbook-postgresql-tde.yml --limit pg-tde-01
```

## Source

Original files: `new-platform/ansible/encryption/`

These playbooks are from the book's simulation platform. They demonstrate layered encryption (disk + DB engine) meeting PCI-DSS and iGaming data residency requirements.
