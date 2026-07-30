# postgres-aegis — Horizontal scaling guide

Covers read + write shard autoscaling, HAProxy integration, data-sync
gates before accepting traffic, and the procedure-level details for 10 TB
per shard.

## 1. The 3-layer connection plane

```
   app clients  (up to 10 k concurrent)
        │
        ▼
   ┌──────────────┐  ← layer 1: HAProxy (TCP balancer)
   │   HAProxy    │    • pool: writer = single leader / shard
   │  (5000…5001) │    • pool: reader = leastconn over healthy replicas
   │   (pgcat VM) │    • health probe: Patroni REST /master, /replica?lag=
   └──────┬───────┘
          │
          ▼
   ┌──────────────┐  ← layer 2: PgCat (hash-sharder + pooler)
   │     PgCat    │    • pool_mode = transaction
   │   (6432)     │    • shards keyed on player_id via pg_bigint_hash
   │              │    • server_tls_sslmode = verify-full
   └─────┬────┬───┘
         │    │
         ▼    ▼
   ┌─────────┴────────────┐  ← layer 3: Patroni PostgreSQL (per shard)
   │  pg-shard-<x>-writer │    • 1 writer + N readers (N ≥ 3)
   │  pg-shard-<x>-reader │    • LUKS on data disk, pg_aegis columns
   │  pg-shard-<x>-reader │    • Streaming replication, async by default
   └──────────────────────┘
```

**Why three layers?**

- HAProxy gives an L4 proxy with zero knowledge of Postgres protocol, so
  it can health-check and pool connections to thousands of clients with
  small CPU. It also fronts PgCat so PgCat can be restarted without
  disconnecting clients.
- PgCat owns the shard routing: it parses `player_id` from incoming
  statements and hashes to a shard. It also holds the per-shard backend
  connection pool (transaction mode), so 10 k app connections become
  ~50 backend connections per shard.
- Patroni owns one shard's HA: leader election via etcd, streaming
  replication, `pg_rewind` after failover, read replica placement via
  `tags.noloadbalance`.

## 2. Adding a read replica (scripted)

```bash
export BAO_TOKEN=$(cat /etc/vault/token.env | sed 's/^VAULT_TOKEN=//')
bash scripts/add-read-shard.sh shard-a 10.0.42.36
```

What the script does:

1. Provisions the VM (libvirt on lab-server or Proxmox clone on secondary-host).
2. Waits for SSH to come up (up to 5 min).
3. Appends the host to `inventory/<target>.yml`.
4. Runs Ansible for the new host only: LUKS → postgres-16 → pg-aegis →
   patroni → monitoring.
5. Polls `pg_stat_replication` on the leader until the new replica's
   application_name appears in state=`streaming`.
6. Runs Ansible on pgcat hosts with `--tags haproxy,pgcat` — adds the new
   server to both HAProxy backends and the PgCat shard replicas list.
7. Returns only after the replica has served one successful SELECT via
   HAProxy (health-check bounce). **Data sync is guaranteed before the
   replica accepts traffic**: Patroni's REST `/replica?lag=N` returns 503
   while the replica is not caught up.

## 3. Adding a write shard (scripted, multi-stage)

Write-shard addition is irreversible in the sense that data moves. The
`scripts/add-write-shard.sh` script takes 5 staged steps — each
reversible up to the PgCat ring weight flip:

1. Provision 1 writer + 5 readers for `shard-<letter>`.
2. Bootstrap Patroni on the new scope; identical schema + extensions.
3. Extend the PgCat ring with the new shard at **weight=0** (shard is
   visible to PgCat's admin interface but never routed to).
4. Resharder (`scripts/_reshard.py`) moves hash buckets that now belong
   to the new shard from their current owner. Each bucket:
   - Create a pg_logical replication slot on the origin shard.
   - COPY the bucket's rows to the new shard.
   - Apply any WAL captured during COPY so the new shard is caught up to
     the origin's commit LSN.
   - Atomically flip the PgCat ring bucket to the new shard (single admin
     command, not multi-step).
   - Delete moved rows from the origin (off hot path).
5. Flip the shard's weight to normal and drop the shard's entries from
   the origin shard.

**Invariant**: at every step, any given row exists in exactly one shard
(or transactionally in zero during the flip, with reads failing closed
by PgCat returning a retryable error).

## 4. Removing a read replica

```bash
bash scripts/remove-read-shard.sh pg-shard-a-reader-5 --destroy-vm
```

1. Drain the replica in HAProxy (`set server state drain` via admin
   socket). Existing connections finish; no new ones arrive.
2. Wait up to 2 min for `pg_stat_activity` to drain.
3. Stop Patroni on the node. Patroni removes itself from the cluster.
4. Remove the host from inventory; re-run `ansible-playbook --tags
   haproxy,pgcat` to update backends.
5. Optionally destroy the VM.

## 5. Autoscaler

`scripts/autoscaler.py` implements the policy. Drive it as a systemd
unit or a cron one-shot (`--once`). Defaults:

| Dimension | Scale out | Scale in | Cooldown |
|---|---|---|---|
| Read replicas | mean saturation > 70% for 5 min, lag < 50 MB | mean saturation < 20% for 30 min | 10 min |
| Write shards | WAL rate > 80% of 24 h p95, disk free < 30% | **manual only** (resharder work) | 40 min |

The autoscaler calls the same shell scripts — no duplicated logic.

## 6. Data sync guarantees when adding a read replica

1. VM boot + Ansible LUKS role — data disk is formatted + mounted with
   LUKS2 (AES-XTS 512). No data yet.
2. Ansible `patroni` role joins the scope, Patroni sees a fresh data dir
   and triggers `pg_basebackup` from the current leader. Base backup
   runs on the `replicator` role over TLS 1.3.
3. Replica moves to state=`starting` → `catchup` → `streaming`. The
   *book* convention: traffic only starts flowing to the replica in
   step 4.
4. HAProxy backend server starts `check` at port `patroni_rest_api_port`.
   Patroni's `/replica?lag=1048576` endpoint returns 200 only when the
   replica's `replay_lsn` is within 1 MB of the primary's `sent_lsn`.
5. First successful `check` bumps the server to `UP` in HAProxy; real
   traffic follows the next LB decision.

Net result: **a new replica never receives production traffic before
data is caught up to within 1 MB of the leader.**

## 7. Encrypted rows + sharding

Encrypted columns (`pg_aegis` or `pgcrypto` fallback) travel as-is
between shards because:

- The **same DEK** is used across every shard (wrapped by the same
  YubiHSM wrap key; every VM's Vault Agent renders the same unwrapped
  AES-128 into tmpfs).
- Ciphertext is opaque to PgCat; it never parses it. PgCat only reads
  the shard key (`player_id`, which is plaintext by design — it's an
  integer).
- Resharder COPY does not decrypt and re-encrypt; it carries
  `bytea` + `nonce` verbatim.

**Regulatory note**: for FIPS 140-3 regimes, pg_aegis is NOT validated.
The book's decision matrix in `chapters/27d-postgres-aegis-testing.md §1`
lists which PII columns stay on AES-256-GCM for those jurisdictions.

## 8. 10 TB per shard — what is different

### Storage

- Writer data disk: 15 TB ssd-fast-vms allocation, ZFS `zstd-3`, 64k
  recordsize. Reader replicas: 15 TB each (you can reduce to 12 TB with
  `vacuum full` on hot partitions, but plan for the full footprint).
- pg_wal: separate 500 GB allocation for 24 h PITR. Consider a dedicated
  tablespace for pg_wal if the WAL rate exceeds 100 MB/s sustained.
- pgbackrest repo on Wasabi S3 with `repo1-retention-full=30`,
  `repo1-cipher-type=aes-256-cbc`. Expect ~8 TB compressed full.

### Memory

- Writer: `shared_buffers = 16 GB` (1/4 of 64 GB RAM), `work_mem = 32 MB`
  per backend × 500 backends = worst-case 16 GB.
- Reader: `shared_buffers = 8 GB` (1/4 of 32 GB), hot dataset in page
  cache via `effective_cache_size = 24 GB`.

### CPU

- 8 vCPU writer, 4 vCPU reader. iGaming hot path is mostly indexed
  lookups + single-row INSERTs; CPU rarely dominates, but pg_aegis
  encrypt/decrypt adds ~1 core of headroom per 1k RPS.

### Time envelope

For reference, at the T15 reference rate (run `T15_10tb_projection.sh`
on your hardware to get actual numbers, don't trust my projection):

- Initial generation of 10 TB with 32 workers: ~2-3 days.
- Base `pg_basebackup` to a new replica over 10 GbE: ~3 h.
- Full pgbackrest backup: ~6 h (compress + encrypt).
- PITR restore of 10 TB + 2 h of WAL: ~4 h RTO on the restore node.
- Online LUKS resize from 15 TB → 20 TB: seconds (`cryptsetup resize`
  + `xfs_growfs` are O(metadata), not O(data)).
- Online AEGIS-vs-pgcrypto back-fill of 1 B rows with 16 workers at the
  article's reported 4410 rows/s/worker: ~1.3 days.

### Failure budget

- 1 replica down: leastconn HAProxy absorbs with < 1% tail latency bump.
- 2 replicas down: elevated p99 on reads; autoscaler fires scale-out.
- Writer down: Patroni failover ~5-15 s; HAProxy `/master` check flips.
- Entire shard down: app-layer shard key rescue — reads to the other
  shard succeed for that player subset; writes fail closed (explicit
  error the app catches).

## 9. Observability

- postgres_exporter per VM → Prometheus (already scraped by book's
  existing stack on lab-server).
- node_exporter per VM → Prometheus.
- HAProxy stats on `:7000` — scrape via `haproxy_exporter` or read
  directly with `curl -s http://pgcat-1:7000/;csv`.
- Patroni REST on `:8008` — `/master`, `/replica`, `/history`, `/cluster`.
- Grafana dashboards: see `docs/grafana/` (to be added — not yet built).
- Wazuh rules for encryption-layer leaks (plaintext in WAL, backup
  bundle) are defined in `chapters/24-security-compliance.md`.
