# postgres-aegis — Operator Runbook

Step-by-step procedures an oncall engineer needs during incidents and
for routine ops. Every command is copy-paste ready; all "danger zone"
steps include the rollback.

## 0. Prerequisites for any operation

```bash
export BAO_TOKEN=$(cat /etc/vault/token.env | sed 's/^VAULT_TOKEN=//')
export TARGET=lab-server    # or secondary-host

# Proxmox creds live in ~/.config/postgres-aegis/secondary-host.env (0600, not in repo)
source ~/.config/postgres-aegis/secondary-host.env 2>/dev/null || true
```

## 1. Daily — health check

```bash
# Patroni view of the cluster
for shard in shard-a shard-b; do
  WRITER=$(python3 -c "
import yaml
inv = yaml.safe_load(open('inventory/${TARGET}.yml'))
g = 'shard_' + '$shard'.split('-')[-1] + '_writer'
print(next(iter(inv['all']['children'][g]['hosts'].values()))['ansible_host'])
")
  echo "=== $shard (leader at $WRITER) ==="
  curl -s "http://$WRITER:8008/cluster" | jq '.members[] | {name, role, state, lag}'
done

# HAProxy
curl -s http://pg-cat-1:7000/\;csv | awk -F, 'NR==1 || $2 ~ /writer|reader/ {print $1,$2,$18}'
```

Expect: 1 leader + N replicas in state=streaming, lag < 5 MB, HAProxy
shows all servers UP.

## 2. Leader failover (planned, e.g. for node maintenance)

```bash
# Current leader voluntarily yields
SHARD=shard-a
WRITER=$(python3 -c "...")
curl -s -X POST "http://$WRITER:8008/switchover" \
  -H 'Content-Type: application/json' \
  -d '{"leader":"'$(curl -s "http://$WRITER:8008/cluster" | jq -r '.members[] | select(.role=="Leader") | .name')'", "candidate":""}'
# Patroni picks the lowest-lag replica.
# HAProxy's /master check updates within 3 s.
```

Rollback: wait 60 s for the new leader to settle; initiate reverse
switchover if the old leader recovers.

## 3. Unplanned leader loss

**No manual action needed.** Patroni detects via etcd lease expiry
(30 s default TTL) and promotes a replica. HAProxy re-binds via the
`/master` health check.

If you need to verify:
```bash
curl -s "http://$ANY_REPLICA:8008/cluster" | jq '.members'
```

If the cluster doesn't recover within 2 min, check etcd:
```bash
ETCDCTL_API=3 etcdctl --endpoints=http://10.0.42.20:2379,http://10.0.42.21:2379,http://10.0.42.22:2379 endpoint status --write-out=table
```

## 4. Add a read replica under load

```bash
bash scripts/add-read-shard.sh shard-a 10.0.42.36
# Takes 5-10 min. Traffic is not routed until the replica is caught up.
```

Rollback: `bash scripts/remove-read-shard.sh pg-shard-a-reader-N --destroy-vm`

## 5. Rotate the LUKS/DEK keys

```bash
bash scripts/rotate-dek.sh ${TARGET}
# 1. Generates a new AES-128 DEK in the YubiHSM.
# 2. Writes the wrapped DEK to OpenBao (KV v2 — old version retained).
# 3. SIGHUPs Patroni on every node; pg_aegis re-reads unwrapped DEK from
#    shared memory within 5 s.
# 4. Old ciphertext is still decryptable by the previous DEK version for
#    the 7-day grace window, then `scripts/clear-old-dek.sh` drops it.
```

**Rollback within grace window**: `bao kv get -version=<N-1> casino/postgres/aegis/dek_wrapped`,
promote that version back via `bao kv put`, SIGHUP again.

## 6. Backup (automatic) + manual full

Daily full runs at 02:00 via systemd timer; hourly diff. To trigger a
manual full:

```bash
ssh ansible@pg-shard-a-writer-1 'sudo -u postgres pgbackrest --stanza=casino-aegis --type=full backup'
ssh ansible@pg-shard-a-writer-1 'sudo -u postgres pgbackrest --stanza=casino-aegis info'
```

## 7. Restore (point-in-time)

```bash
# Tests/T11_backup_restore_rto.sh does this automatically against a
# throwaway VM. For a real restore on the production cluster you MUST
# do it on a scratch VM first and verify row counts — never restore
# into the running cluster.

# Scratch restore smoke:
bash tests/T11_backup_restore_rto.sh ${TARGET}
```

RTO observed: target < 30 min for 1 TB (proportional).
RPO: within the 5-minute archive_push cadence (hourly diff = 1 h worst
case if archiving is broken).

## 8. Disk full

Scale up the data disk online:

```bash
bash scripts/resize-encrypted-disk.sh pg-shard-a-reader-3 200
# Grows qcow2 (libvirt) or Proxmox disk, then LUKS resize + FS grow.
# Zero Postgres downtime.
```

## 9. WAL archive stuck

```bash
# Identify the WAL consumer:
ssh ansible@pg-shard-a-writer-1 'sudo -u postgres psql -c "SELECT * FROM pg_replication_slots;"'
# A slot with active=false + restart_lsn in the distant past is the
# culprit. Drop it (WARNING: destroys that replica's WAL continuity —
# the replica will need re-basebackup):
ssh ansible@pg-shard-a-writer-1 "sudo -u postgres psql -c \"SELECT pg_drop_replication_slot('<slot_name>');\""
```

## 10. Autoscaler seems stuck

```bash
# Dry-run once, see the decision
python3 scripts/autoscaler.py --once --dry-run

# Force a scale-out manually
bash scripts/add-read-shard.sh shard-a 10.0.90.NN
# Adjust cooldown in the systemd unit, or delete state:
rm /var/lib/postgres-aegis/autoscaler.state
```

## 11. Suspected plaintext leak

```bash
# Run the 5-layer encryption check. Fails loud on any leak.
bash tests/matrix/T-encryption-layers.sh ${TARGET}
```

If (3) fails (plaintext in WAL), **rotate the DEK immediately**
(`rotate-dek.sh`) and open an incident per `chapters/24`.

## 12. Emergency cluster-wide shutdown

```bash
# Close all LUKS containers (data becomes opaque) — irreversible except
# with Bao access.
for host in $(python3 -c "
import yaml
inv = yaml.safe_load(open('inventory/${TARGET}.yml'))
for grp_name, grp in inv['all']['children'].items():
    if grp_name in ('writers','readers'):
        for h,_ in (grp.get('children') or {}).items():
            for host in (inv['all']['children'][h].get('hosts') or {}):
                print(host)
"); do
  ssh ansible@$host 'sudo systemctl stop patroni && sudo umount /var/lib/postgresql/16/main && sudo cryptsetup close pgdata_crypt' &
done
wait
```

Recovery: boot each VM; `pg-aegis-luks-unlock.service` re-opens the LUKS
container using the Vault Agent + Bao key path; Patroni joins back.
