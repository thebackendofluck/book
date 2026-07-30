<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-03.jpg" alt="Volume 3" width="150" /></a>

# Chapter 24i: Blue-Green Cluster Switching for iGaming Kubernetes Environments

**📙 Part of Volume 3 — Security Engineering and Runtime Defense** · €84.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZCRSTMH) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 24i of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Daily K3s cluster rotation for zero-drift, fully auditable iGaming infrastructure — provisioning, switchover, and chaos validation.

## Overview

Automation for the blue-green cluster ephemerality pattern: a new K3s cluster is provisioned each night from infrastructure-as-code baseline, live traffic is switched from the previous cluster, and the old cluster is destroyed. Includes the full provisioning script, switchover automation, systemd timers, chaos testing, and Grafana alerting for the rotation lifecycle.

## Contents

- `bash/create_casino_cluster.sh` — K3s cluster provisioning from version-controlled baseline
- `bash/switchover.sh` — Orchestrates the full blue→green traffic switch with health gates
- `bash/pre_switch_validation.sh` — Seven-check smoke suite: health, wallet API, auth/JWT, game listing, Redis-backed balance read, WebSocket, TLS expiry. Run by `rotation-driver.sh` after provisioning and again by `switchover.sh` as its gate; a non-zero exit aborts the switchover
- `bash/dns_switchover.sh` — Updates DNS/HAProxy upstream to point to the new cluster
- `bash/rotation-driver.sh` — Master driver: `provision` | `validate` | `switchover` | `destroy`. Provisioning only marks a cluster pending once validation passes; a failed teardown is fatal and leaves the state file alone
- `bash/monitor_switchover.sh` — Real-time switchover progress monitoring
- `bash/run_migrations.sh` — MANUAL, GATED. Applies expand-only migrations to the shared database. Nothing calls it automatically; refuses to run without `MIGRATION_EXPAND_ONLY_CONFIRMED=yes`. See the expand/contract rule in the script header
- `bash/iptables_isolation.sh` — Drops routing between the blue and green pod CIDRs, verifying each rule with `iptables -C` before persisting `rules.v4`
- `bash/chaos_test.sh` — Injects failures (pod kill, network partition) into Green before accepting traffic
- `bash/push_rotation_metrics.sh` — Pushes rotation duration and success metrics to Prometheus Pushgateway
- `bash/cluster.conf` — Shared cluster configuration (node IPs, K3s version, registry, namespaces)
- `python/wallet_startup.py` — Primary-writer lease: claim, heartbeat renewal, fencing epoch, and fail-closed demotion. Decides whether a wallet pod may write player balances
- `python/synthetic_tests.py` — Synthetic transaction tests run against Green before traffic switch
- `sql/cluster_registry.sql` — Cluster registry, primary-writer lease, and the `assert_primary_lease` fence every balance-mutating transaction calls
- `systemd/casino-cluster-provision.{service,timer}` — Provisions the next colour at 02:00 UTC daily
- `systemd/casino-cluster-switchover.{service,timer}` — Switches traffic at 03:00 UTC daily
- `systemd/casino-cluster-destroy.timer` — Fires at 04:00 UTC. Note there is no matching `.service` unit and no `destroy_cluster.sh`; see "Not implemented" below
- `manifests/` — Kubernetes RBAC, wallet service Deployment, Grafana rotation alerts
- `json/rotation-state.json` — Persistent rotation state file (tracks current Blue/Green assignment)
- `vault/` — OpenBao policies and secret paths for per-cluster credential rotation

## Technology Stack

- **Orchestration:** Bash, Python 3.11+
- **Infrastructure:** K3s ≥ 1.34, HAProxy, systemd
- **Secrets:** OpenBao (Vault fork)
- **Monitoring:** Grafana, Prometheus Pushgateway
- **State:** PostgreSQL, Redis (shared, external to clusters)

## Prerequisites

- ops-host host running systemd with `sudo` access
- K3s installer binary available at `/usr/local/bin/k3s`
- OpenBao accessible at `VAULT_ADDR`; `VAULT_TOKEN` set
- `kubectl`, `helm`, `jq`, `curl` installed
- Grafana accessible for alert validation

## How to Run

```bash
# Phases, in order. There is no single "run everything" entry point: the phases
# are separated by systemd timers and by a deliberate review window.
bash bash/rotation-driver.sh provision    # create + validate the next colour
bash bash/rotation-driver.sh validate     # re-run both validation suites
bash bash/rotation-driver.sh switchover   # gate, hand over the lease, cut traffic
bash bash/rotation-driver.sh destroy      # tear down the previous colour

# Or the individual scripts. switchover.sh takes the CURRENTLY ACTIVE colour and
# switches away from it, so this is how you go from blue to green:
bash bash/create_casino_cluster.sh green /etc/casino/green-cluster.conf
bash bash/pre_switch_validation.sh green
bash bash/switchover.sh blue
```

`switchover.sh` needs `CASINO_DB_URL` (a libpq URL for the shared player
database) and `SYNTHETIC_TEST_PASSWORD` in its environment, both from OpenBao via
an `EnvironmentFile`, never on a command line.

Apply `sql/cluster_registry.sql` to the shared database before the first rotation.
It is idempotent apart from the unique index on `cluster_color`, which will fail
if an older registry accumulated duplicate rows per colour.

### Not implemented

`destroy_cluster.sh` is referenced by `rotation-driver.sh destroy`, by the
chapter's rotation timeline, and by `systemd/casino-cluster-destroy.timer` (which
also has no matching `.service` unit), but it is not in this bundle. Teardown is
manual. `rotation-driver.sh destroy` fails loudly rather than recording a
successful destroy of a cluster that is still running.

`bash/chaos_test.sh` is deliberately not wired into the rotation: it kills pods
and partitions networks, which belongs in a test environment and not in an
unattended nightly path against production.

## Security Notes

Each new cluster receives freshly rotated secrets from OpenBao and freshly issued mTLS certificates. The `vault/` directory contains the per-cluster secret renewal policies — run `bash/rotation-driver.sh` rather than switching manually to ensure all secrets are rotated before traffic moves.

## Related

- See Chapter 24i in the book for the full daily rotation architecture, regulatory audit rationale, and measured switchover times.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 2 · last updated 2026-04-16.</sub>
