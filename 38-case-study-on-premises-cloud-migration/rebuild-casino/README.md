# Rebuild Casino - Ansible Playbooks

Production Ansible playbooks used to rebuild and redeploy casino CMS environments. This tooling was critical during on-premises operations where multiple casino brands ran on shared infrastructure and needed coordinated git-pull-and-rebuild workflows.

## What This Does

- Pulls latest code from git for each casino brand's CMS repositories
- Runs post-pull build scripts (for Angular/Node.js-based brands)
- Supports targeting specific casino groups (by datacenter or brand cluster)
- Uses Ansible's `delegate_to` pattern to execute on the correct host per brand

## Architecture

```
inventories/
  stage/
    hosts.yml          # Stage environment hosts (sanitized)
roles/
  rebuild-casino/
    tasks/main.yml     # Core rebuild logic: git pull + postpull
    vars/
      casino_filter.yml      # Selective rebuild (specific brands)
      brand_alpha_all.yml    # All brands on alpha cluster
      brand_beta_all.yml     # All brands on beta cluster
      brand_gamma_all.yml    # All brands on gamma cluster
```

## Usage

Rebuild a specific casino group on staging:

```bash
ansible-playbook acme.yml -i inventories/stage \
  --tags "rebuild-casino" \
  --extra-vars "target=<servername> casinogroup=<casino_filter|brand_alpha_all|brand_beta_all|brand_gamma_all>"
```

## Key Patterns for Book Readers

1. **Multi-brand CMS management**: Each casino brand has its own site repo and ng (Angular) repo
2. **Delegated execution**: Ansible delegates tasks to the host where each brand lives
3. **Parallel group rebuilds**: Brands are organized into groups to allow parallel operations
4. **Post-pull hooks**: Angular brands run `postpull.sh` to rebuild after git pull

## Chapter Reference

Chapter 38: Case Study - On-Premises to Cloud Migration
