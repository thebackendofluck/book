# Chapter 24b — Wazuh SIEM: Ansible Playbooks

These files are part of the book's simulation platform for Chapter 24b (Wazuh SIEM deployment).

## Files

| File | Purpose |
|------|---------|
| `playbooks/playbook-wazuh-manager.yml` | Deploys the Wazuh all-in-one stack (Manager + Indexer + Dashboard) on `wazuh001`. Includes custom iGaming FIM rules, log collection configuration, and threat detection rules |
| `playbooks/playbook-wazuh-agent.yml` | Enrolls Wazuh agents on monitored casino hosts. Agents report to the manager at `wazuh001` (10.0.10.26) |
| `inventory/hosts.yml` | Inventory defining `wazuh_manager` and `wazuh_agents` host groups |

## Roles

The playbooks do not carry their own copy of the roles. Both reference the
simulation platform tree by path, relative to the playbook directory:

| Playbook | Role |
|----------|------|
| `playbooks/playbook-wazuh-manager.yml` | `new-platform/ansible/wazuh/roles/wazuh-manager` |
| `playbooks/playbook-wazuh-agent.yml` | `new-platform/ansible/wazuh/roles/wazuh-agent` |

That is where the FIM paths, log collection and the custom iGaming detection
rules live, so there is one copy to maintain rather than two that drift. The
paths resolve from any working directory. If you lift these playbooks out of
the book repository, copy that role tree in beside them or point `roles_path`
at wherever you put it. Verify with:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/playbook-wazuh-manager.yml --syntax-check
```

## Dependencies

- Run `playbook-wazuh-manager.yml` first — the manager must be up before agents can enroll
- Target hosts: Ubuntu 22.04+ or compatible
- Wazuh manager host: `wazuh001` at `10.0.10.26`
- Application logs must be JSON, and must include a `srcip` key on
  authentication events. The custom rules select on `decoded_as json`, and the
  brute-force correlation counts per source address, so a syslog-formatted
  file or a missing `srcip` silently disables those rules

## How to Run

```bash
# Step 1 — Deploy the Wazuh manager (all-in-one)
ansible-playbook -i inventory/hosts.yml playbooks/playbook-wazuh-manager.yml

# Dry-run
ansible-playbook -i inventory/hosts.yml playbooks/playbook-wazuh-manager.yml --check --diff

# Step 2 — Enroll agents on casino hosts
ansible-playbook -i inventory/hosts.yml playbooks/playbook-wazuh-agent.yml

# Deploy agent to a specific host
ansible-playbook -i inventory/hosts.yml playbooks/playbook-wazuh-agent.yml --limit casino001
```

## Source

Original files: `new-platform/ansible/wazuh/`

These playbooks are from the book's simulation platform. They demonstrate a production-grade SIEM deployment meeting iGaming compliance requirements for security monitoring and incident detection.
