# postgres-aegis-vms — full provisioning + test automation

End-to-end automation to provision, encrypt, bootstrap, shard, backup, restore
and load-test a 2-shard x (1 writer + 5 readers) PostgreSQL cluster with
layered encryption (LUKS + TLS + pg_aegis + HSM-wrapped DEK). Two targets:

- **lab-server / libvirt** — shadow cluster, reachable today.
- **secondary-host / Proxmox** — production cluster, after the secondary-host rebuild completes.

Both targets share all the Ansible roles and test scripts; only the
VM-creation layer differs.

## Architecture (1W+10R → 2 shards)

```
                    ┌─────────────┐
  app / bench  ───► │    PgCat    │  hash-shards on player_id
                    │  (stateless │  pool_mode=transaction
                    │   pooler)   │  server_tls_sslmode=verify-full
                    └──┬───────┬──┘
                       │       │
         ┌─────────────┘       └──────────────┐
         ▼                                    ▼
    ┌─────────┐                          ┌─────────┐
    │ shard-A │                          │ shard-B │
    │ Patroni │                          │ Patroni │
    │ scope=A │                          │ scope=B │
    │         │                          │         │
    │ writerA │                          │ writerB │
    │    │    │                          │    │    │
    │    ├──► 5 readers  ──── WAL ──►    │    ├──► 5 readers
    │    │   readerA-1..5               │    │   readerB-1..5
    │    │                              │    │
    │    LUKS /dev/vdb              LUKS /dev/vdb
    │    pg_aegis (AEAD col)        pg_aegis (AEAD col)
    │    pgbackrest → Wasabi        pgbackrest → Wasabi
    └─────────┘                     └─────────┘
         │                                    │
         └─────────── HSM-wrapped DEK ────────┘
                     via OpenBao (lab-server)
                     root-of-trust: YubiHSM 2
```

Total VMs: **12** (2 writers + 10 readers). Add 2-3 for DCS (etcd) and PgCat
pods if you run PgCat as a pod set.

## Pre-requisites on the orchestrator host (laptop)

- `ssh lab-server` working with your key.
- `ansible` >= 2.16.
- `jq`.
- `make`.

## Pre-requisites on lab-server

- libvirt / KVM (already present — k8s-prov_server confirms).
- `/raid_nvme01/vms` disk pool with ≥ 200 GB free.
- OpenBao unsealed (already — `bao status` returns `Sealed: false`, v2.5.2+hsm).
- OpenBao token for your user/role with read on `casino/postgres/aegis/*`
  and write on `casino/postgres/aegis/rotated/*`.
- YubiHSM connector on `127.0.0.1:12345` (already — v3.0.5 up 12 h).

## Pre-requisites on secondary-host (only for the Proxmox path)

- Packer template `ubuntu-jammy-pgdb-template` built once (see
  `../../../boilerplates/packer/proxmox/ubuntu-server-jammy-pgdb/`).
- Proxmox API token from `dashboard.acmetocasino.com` or `casino/proxmox/packer`
  in OpenBao.
- Storage pool `nvme-zfs-vms` with ≥ 200 GB free.

## Quickstart

### Shadow cluster on lab-server (recommended first)

```bash
cd infrastructure/provisioning/postgres-aegis-vms

# 1. Authenticate to OpenBao on lab-server (one time per shell)
export BAO_TOKEN=$(bao login -method=userpass username=$USER -token-only)
# or: export BAO_TOKEN=$(dashboard.acmetocasino.com get-bao-token $USER)

# 2. Provision VMs on lab-server (libvirt)
make provision TARGET=lab-server

# 3. Bootstrap Patroni + pg_aegis + PgCat + pgbackrest via Ansible
make bootstrap TARGET=lab-server

# 4. Run the full test matrix
make test TARGET=lab-server                # T01..T12 including backup/restore

# 5. Tear down
make destroy TARGET=lab-server
```

### Production cluster on secondary-host (after rebuild)

```bash
export PROXMOX_API_TOKEN=$(bao kv get -field=token_secret casino/proxmox/packer)

make provision TARGET=secondary-host
make bootstrap TARGET=secondary-host
make test      TARGET=secondary-host
```

## What the test matrix covers

| ID  | Name | Measures | Env |
|-----|---|---|---|
| T01 | baseline OLTP pgbench | TPS, p99, CPU | A, B |
| T02 | TLS in-transit overhead | TPS Δ vs T01 | A, B |
| T03 | LUKS + writer-kill failover | RTO, RPO | A, B |
| T04 | HSM-wrapped DEK rotation | zero-downtime | A |
| T05 | pg_aegis vs pgcrypto column | INSERT/SELECT speedup | A, B |
| T06 | Shard routing (PgCat) | balance across shards | A, B |
| T07 | Read-heavy 10k rps | lag, TPS per replica | A, B |
| T08 | Hot-partition on sessions | p99, lag | A, B |
| T09 | Network partition between shards | convergence, errors | A, B |
| T10 | Blue-green backfill 1M rows | rows/s/worker | A, B |
| T11 | **Backup + restore + RTO/RPO** | restore time, data loss | A, B |
| T12 | Chaos: random leader kill every 10 min | availability % | A, B |

## Files

```
Makefile                               # one-command entry point
inventory/
  lab-server.yml                          # 12 libvirt VMs on lab-server
  proxmox-secondary-host.yml                     # same layout for Proxmox
libvirt/
  create-cluster.sh                    # libvirt-based provisioning
  cloud-init/{user-data.tmpl,meta-data.tmpl}
proxmox/
  create-cluster.sh                    # Proxmox API-driven (uses Packer template)
ansible/
  site.yml
  requirements.yml
  group_vars/all.yml                   # shared defaults (LUKS pass path, HSM, BAO)
  roles/
    luks-data-disk/                    # LUKS format + systemd unlock via HSM
    postgres-16/                       # repos + packages
    patroni/                           # DCS=etcd; 2 scopes (A,B); 5 replicas each
    pg-aegis/                          # extension install or pgcrypto fallback
    pgbouncer-pgcat/                   # PgCat w/ hash shards on player_id
    pgbackrest/                        # daily full + hourly incremental to Wasabi
    monitoring/                        # node + postgres exporter
tests/
  run-prod-matrix.sh                   # T01..T12 orchestrator
  T11_backup_restore_rto.sh            # backup, destroy, restore, measure RTO
  T12_chaos_leader_kill.sh             # virsh destroy leader every N minutes
  rw-split-k6.js                       # k6 driver with reader/writer split
  schemas/
    casino-wallet.sql                  # realistic iGaming wallet schema
    seed-1m.sql                        # 1M synthetic rows for T10/T11
```

## Safety guardrails

- `make destroy` **refuses** to run against production IPs unless `FORCE=yes`.
- `T11` takes a **real backup first**, writes the restore to a throwaway
  namespace, compares row counts, and destroys the throwaway — never
  touches the primary cluster's data path.
- All `bao kv put` calls pair with `bao kv metadata get` immediately after,
  so you see the version number the script just wrote.
- The chaos script `T12` skips the run if `uptime` on any VM is < 10 min
  (something just came up; don't kill it again).
