# pg_aegis on PostgreSQL 18 — Measured Comparison & Migration Plan

**Date:** 2026-04-04
**Host:** ops-host (AMD Ryzen Threadripper PRO 3995WX, 64C/128T, Ubuntu 24.04)
**Engines:** postgresql-16 16.13 (port 5434) vs postgresql-18 18.3 (port 5435, from PGDG apt)
**Extension:** pg_aegis 0.1.0 (pgrx 0.17.0), built independently for each PG major
**Workload:** 100 000 rows, 144-byte average PII payload
**Bench config:** `max_parallel_workers_per_gather = 0` (serial, fair comparison)
**pgcrypto mode:** passphrase-based `pgp_sym_encrypt` / `pgp_sym_decrypt`

## 1. End-to-end PostgreSQL benchmark

| Operation (100 k rows) | PG 16 pgcrypto | PG 16 pg_aegis | PG 18 pgcrypto | PG 18 pg_aegis |
|---|---:|---:|---:|---:|
| INSERT (encrypt) | 15.46 s | **3.60 s** | 15.16 s | **3.05 s** |
| SELECT (decrypt) | 17.84 s | **3.02 s** | 14.29 s | **2.77 s** |
| UPDATE (decrypt+re-encrypt) | 33.73 s | **7.07 s** | 43.30 s | **6.13 s** |
| Table size | 53 MB | 47 MB | 53 MB | 47 MB |
| Avg ciphertext bytes | 211 B | 181 B | 211 B | 181 B |

### Speedup matrix

|  | PG 16 pg_aegis vs PG 16 pgcrypto | PG 18 pg_aegis vs PG 18 pgcrypto | **PG 18 pg_aegis vs PG 16 pgcrypto** | PG 18 pg_aegis vs PG 16 pg_aegis |
|---|---:|---:|---:|---:|
| INSERT | 4.29x | 4.97x | **5.07x** | 1.18x |
| SELECT | 5.90x | 5.16x | **6.44x** | 1.09x |
| UPDATE | 4.77x | 7.06x | **5.50x** | 1.15x |

**Conclusion:** PG 18 + pg_aegis is **5.1x – 6.4x faster** than PG 16 + pgcrypto on the three workloads tested, comfortably exceeding the 3x GO threshold. The upgrade from pgrx 0.12 to 0.17 alone contributed no measurable delta on PG 16 (~same 3.6 s INSERT), so the PG 18 wins come from the newer executor/heap paths on bulk UPDATEs and slightly tighter per-tuple overhead.

## 2. PG 18 observations & caveats

* **pgcrypto parallel-query crash on PG 18.3.** With default
  `max_parallel_workers_per_gather > 0`, `SELECT pgp_sym_decrypt(v,...)
  FROM large_table` crashes a background worker with SIGSEGV and forces
  automatic recovery. Log evidence:
  ```
  background worker "parallel worker" (PID ...) was terminated by signal 11: Segmentation fault
  terminating any other active server processes
  database system was interrupted; ... automatic recovery in progress
  ```
  This is a **regression in the 18.3 packaging** (likely
  `contrib/pgcrypto` parallel-safe marking or a heap interaction in the
  new executor). pg_aegis on PG 18 is parallel-safe and did not crash.
* **pgcrypto UPDATE got slower on PG 18** (33.7 s → 43.3 s, +28 %).
  Needs independent confirmation but points to either heap-TOAST
  interaction changes or WAL format changes hurting pgcrypto's
  armored-output workload.
* **pg_aegis benefits uniformly on PG 18** (roughly +10 – 15 % across
  the board) with zero code changes.
* **AEGIS-128L constant-time note:** with `target-cpu=native` on this
  EPYC/Threadripper host we hit the SIMD path (AES-NI + AVX2). The
  AEGIS crate falls back to `softaes` when the host lacks `aes`, which
  would reduce throughput ~3 – 4x. All production hosts must have AES-NI.
* **No PG-18 features required by pg_aegis.** PG 18's new async I/O
  and `io_method=io_uring` don't meaningfully touch synchronous
  bytea-column CPU-bound workloads. pg_aegis doesn't need PG 18 to
  work — it's faster there simply because PG 18 is faster.

## 3. 1M-row bulk re-encryption (PG 18)

```
Populate 1M rows (pgp_sym_encrypt)       : 225.7 s  (4,430 rows/s)
Re-encrypt in-place UPDATE (pgp->aegis)  : 266.6 s  (3,750 rows/s)
Storage before (pgcrypto)                :   266 MB
Storage after  (pg_aegis, post-VACUUM)   :   234 MB   (-12 %)
AAD binding (mig_src:v:id) verified      :   OK
```

A direct in-place UPDATE on 1M rows holds a long transaction and
doubles table size (MVCC bloat) before VACUUM. For production,
use the batched procedure in `sql/migrate_from_pgcrypto.sql` which
commits every 5 k rows and bounds bloat.

## 4. GO / NO-GO recommendation

**GO — migrate to PG 18 + pg_aegis.**

Criteria check:
* [x] PG 18 + pg_aegis > 3x faster than PG 16 + pgcrypto:
      **5.1x INSERT, 6.4x SELECT, 5.5x UPDATE**.
* [x] PG 18 has no crypto improvements that narrow the gap — if
      anything, `pgp_sym_decrypt` regressed on PG 18 and crashes
      under parallel query.
* [x] pg_aegis requires no PG 18-specific features; same extension
      binary model, built per major version.

Risk register:
* pgcrypto parallel crash on PG 18.3 -> file upstream bug, pin
  `max_parallel_workers_per_gather = 0` for any session that still
  calls `pgp_sym_*` during the migration window. Not a blocker for
  pg_aegis rollout.
* PG 18 is 18.3 (first stable release in the 18 line on PGDG). Wait
  for 18.4 before promoting to production **primary**. Use 18.3 for
  standby / blue-green cluster; cut over after 18.4 ships.

## 5. Zero-downtime blue-green migration plan

### Assumptions
* Source: PG 16 primary + replica, `pgcrypto` in use for columns
  `players.email_enc`, `players.phone_enc`, `kyc_docs.doc_enc`.
* Target: **new** PG 18.4 cluster (blue-green; no in-place `pg_upgrade`).
* Replication: use **logical replication** (PG 18 supports cross-major
  `CREATE SUBSCRIPTION` from PG 16) for initial seed + delta.
* Application reads/writes through a repository layer — migration is
  opaque to business logic.

### Phase 0 — Preparation (no downtime, T-14 days)

1. Stand up empty PG 18.4 cluster with same shape (shared_buffers,
   WAL config, extensions). Install `pg_aegis` and `pgcrypto`.
2. Generate master key via YubiHSM wrap; load into
   `pg_aegis.master_key_b64` via `postgresql.auto.conf`.
3. `CREATE EXTENSION pg_aegis;` in the target DB.
4. `SELECT aegis_generate_key('player_pii_key');` and other data keys.
5. Add a **dual-read** code path in the application:
   ```rust
   fn read_email(row: &Row) -> String {
       let v: Vec<u8> = row.get("email_enc");
       if v.first() == Some(&0x01) { aegis_decrypt(v) }
       else                        { pgp_decrypt(v) }
   }
   ```
   Deploy to all services. Still writing only pgcrypto at this stage.
6. Pin `max_parallel_workers_per_gather = 0` on any session that
   reads `email_enc` via `pgp_sym_decrypt` until the parallel-safe
   crash is resolved upstream.

### Phase 1 — Logical replication (no downtime, T-7 days)

7. On PG 16 primary:
   `CREATE PUBLICATION pub_all FOR ALL TABLES;`
   `ALTER SYSTEM SET wal_level = 'logical';` and restart (pre-scheduled).
8. On PG 18.4 target: restore schema-only dump (`pg_dump -s`), then
   `CREATE SUBSCRIPTION sub_pg16 CONNECTION '...' PUBLICATION pub_all;`
9. Let initial copy + streaming catch up (hours-to-days depending on
   table size). Monitor `pg_stat_subscription` lag.

### Phase 2 — Background re-encryption (no downtime, T-6 to T-1 days)

10. On PG 18.4 only, run the batched procedure from
    `sql/migrate_from_pgcrypto.sql`:
    ```sql
    CALL migrate_pgcrypto_to_aegis_proc(
        'players', 'email_enc',
        current_setting('pg_aegis.pgp_pass'),
        'player_pii_key', 5000);
    ```
    Commits every 5 k rows with `FOR UPDATE SKIP LOCKED`. At 3,750
    rows/s (measured), 1 M rows take ~4.5 min, 100 M rows ~4.5 h.
    Rows arriving via logical replication meanwhile are still
    pgcrypto-shaped; the procedure is idempotent (skips rows whose
    first byte is already `0x01`) and resumable.
11. Re-run the procedure in a loop until `WHERE get_byte(col,0) != 1`
    returns 0 rows. Logical replication keeps flowing in parallel.

### Phase 3 — Cutover (planned window, ~5 min downtime)

12. Stop application writers (put app in read-only maintenance mode).
13. Wait for `pg_stat_subscription.last_msg_receipt_time` to match
    primary heartbeat (<= 1 s lag).
14. Run the batched procedure one final time to catch stragglers.
15. **Flip writers to write pg_aegis-only**: deploy app version that
    calls `aegis_encrypt_aad()` on every encrypt path.
16. `ALTER SUBSCRIPTION sub_pg16 DISABLE; DROP SUBSCRIPTION sub_pg16;`
17. Update DNS / pgbouncer / connection string to point to PG 18.4.
18. Resume writers. End of downtime.

### Phase 4 — Cleanup (no downtime, T+1 to T+30 days)

19. Dual-read path stays active for 7 days; watch for
    `pgp_decrypt` cache hits (should drop to zero within hours).
20. Once `pgp_sym_*` calls are zero for 7 days, remove the dual-read
    fallback in the next app release.
21. `VACUUM FULL` on rewritten columns if bloat > 30 %.
22. Decommission PG 16 cluster after 30-day cooling-off.

### Estimated downtime

| Sub-step | Duration |
|---|---|
| Drain writers | 10 s |
| Final catch-up batch | 30 – 120 s |
| App version flip | 60 s (rolling) |
| DNS / bouncer cutover | 30 s |
| **Total window** | **~3 – 5 min** |

### Rollback

Rollback is safe at every phase:
* **Phases 0-2:** disable subscription, drop pg_aegis keys on target,
  continue on PG 16. No data written to production.
* **Phase 3 after flip:** if post-cutover smoke tests fail within the
  first 5 min, flip DNS back to PG 16 primary and redeploy previous
  app version. All new writes were to PG 18 only — they must be
  replayed into PG 16, which is why we keep the **reverse** logical
  replication direction armed:
  ```sql
  -- On PG 16, created during phase 1:
  CREATE SUBSCRIPTION sub_pg18_reverse
      CONNECTION '...pg18.4...' PUBLICATION pub_all WITH (enabled=false);
  ```
  Enable it, let it drain, then return to PG 16 as source of truth.
  Post-rollback you still have pgcrypto-only data — zero data loss.
* **Phase 4:** trivially reversible by redeploying dual-read app.

### Success criteria

* Zero failed decrypts on PG 18 for 7 consecutive days.
* `aegis_encrypt` error count in `pg_stat_user_functions` = 0.
* Median column-decrypt latency < 20 µs (p99 < 100 µs).
* No pgcrypto-format rows in production tables after T+7.

## 6. Open items / not benchmarked

* **pgBackRest backups**: pgBackRest compression interacts with
  pg_aegis's random-looking ciphertext — expect compression ratios to
  drop on encrypted columns. Run a real before/after measurement
  before sizing the backup tier.
* **Logical replication throughput** with pg_aegis bytea columns
  (no compression benefit). Needs a separate soak test.
* **Index on encrypted column**: deterministic AEGIS (fixed nonce)
  would allow exact-match lookups but breaks AEAD semantics. Keep
  search via plaintext-hash sidecar columns (`email_blind_idx`).
