<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-04.jpg" alt="Volume 4" width="150" /></a>

# Chapter 27d: PostgreSQL Aegis: Testing Layered Encryption End-to-End

**📕 Part of Volume 4 — Compliance, Player Safety, Data Residency, and Governance** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS473SJ) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 27d of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

This directory holds everything the book refers to in chapter 27d. It is
self-contained: a reader who clones the book repo can `cd` here and run
the full stack without touching anything outside `scripts/chapter-27d/`.

```
scripts/chapter-27d/
├── compose-demo/            # laptop-reproducible demo (20 min, docker compose)
├── tests-laptop/            # T01/T02/T06/T10 harness for the compose-demo
├── helm/                    # Helm chart postgres-aegis (1W+1HotStandby + 10R)
├── terraform/               # AWS/LocalStack Terraform module rds-postgres-aegis
└── postgres-aegis-vms/      # Full VM provisioning + prod-scale test matrix
    ├── inventory/           # ansible inventories (lab-server, secondary-host)
    ├── libvirt/             # VM creation via libvirt
    ├── proxmox/             # VM creation via Proxmox API
    ├── ansible/roles/       # luks-data-disk, patroni, pg-aegis, pgcat, haproxy,
    │                        # pgbackrest, monitoring
    ├── scripts/             # add/remove shards, rotate DEK, resize encrypted disk
    └── tests/               # T11 backup+RTO, T12 chaos, T13 disk resize, matrix/
```

## How the five pieces fit together

| Piece | Purpose | Where it runs |
|---|---|---|
| `compose-demo/` | 5-command laptop demo for readers (pgcrypto vs pg_aegis) | reader's laptop, Docker |
| `tests-laptop/` | pgbench + SQL harness that the demo invokes | inside `pgbench-runner` container |
| `helm/` | Kubernetes chart — production deployment | K8s cluster |
| `terraform/` | AWS RDS or LocalStack equivalent | local Terraform |
| `postgres-aegis-vms/` | VM cluster (libvirt or Proxmox) with full encryption, sharding, HA, backup | lab-server or secondary-host |

## Quickstart (laptop only, 20 min)

```bash
cd compose-demo
make up
make status          # pg_aegis_loaded? t or f
make bench-baseline  # T01 — real pgbench numbers
make bench-aegis     # T06 — pg_aegis vs pgcrypto
```

## Lab-scale (libvirt on lab-server, ~30 min)

```bash
cd postgres-aegis-vms
export BAO_TOKEN=$(bao login -method=userpass username=$USER -token-only)
make provision TARGET=lab-server   # 12 VMs (2 writers + 10 readers + 3 etcd + 2 pgcat)
make bootstrap TARGET=lab-server   # Ansible: LUKS → PG → Patroni → pg_aegis → PgCat → HAProxy → pgbackrest
make test      TARGET=lab-server   # runs T01..T13 including backup+restore, chaos, disk resize
```

## Production (Proxmox on secondary-host, after rebuild completes)

```bash
cd postgres-aegis-vms
export BAO_TOKEN=…
export PROXMOX_API_TOKEN_ID=$(bao kv get -field=token_id casino/proxmox/packer)
export PROXMOX_API_TOKEN_SECRET=$(bao kv get -field=token_secret casino/proxmox/packer)

make provision TARGET=secondary-host
make bootstrap TARGET=secondary-host
make test      TARGET=secondary-host
```

## Test inventory

| ID | What it measures | Where |
|---|---|---|
| T01 | Baseline OLTP TPS | laptop (compose) + cluster |
| T02 | TLS in-transit overhead | laptop (if ssl=on) + cluster |
| T03 | LUKS + writer-kill failover | cluster only (real VMs) |
| T04 | HSM DEK rotation | cluster with HSM |
| T05 | pg_aegis vs pgcrypto | laptop + cluster |
| T06 | PgCat shard routing balance | cluster |
| T07 | Read-heavy 10k rps | cluster |
| T08 | Hot-partition failover | cluster |
| T09 | Network partition between shards | cluster |
| T10 | Blue-green backfill 1M rows | laptop (100k) + cluster |
| T11 | Backup + restore + RTO/RPO | cluster |
| T12 | Chaos leader kill loop | cluster |
| T13 | Online resize of encrypted disk | cluster |
| T-commits | Commit/rollback/sync semantics | cluster |
| T-encryption-layers | Every layer is *active*, not just configured | cluster |

## How this directory was produced

See `../../docs/superpowers/plans/`:

- `2026-04-22-postgres-aegis.md` — plan
- `2026-04-22-test-everything-report.md` — validation on lab-server + laptop
- `verification.log` — static lint/validate output

## Path conventions

- Absolute commands reference `scripts/chapter-27d/<sub>/`.
- Internal relatives never cross above `scripts/chapter-27d/`.
- Chapter prose in `../../chapters/27d-postgres-aegis-testing.md` references
  artifacts by their `scripts/chapter-27d/...` path.
- Book-wide plans live at `../../docs/superpowers/plans/`.
