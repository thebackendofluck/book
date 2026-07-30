# Runbook -- Restore OpenBao from a Raft Snapshot

_Owner: ops · Status: living document · Last updated: 2026-04-17_

## Why

This runbook is the RESTORE counterpart to `snapshot-cron.sh`. It assumes you
have one of three failure scenarios in front of you:

1. **Single-node corruption with snapshots intact** -- one node died, its
   data directory is unreadable, but the encrypted snapshots in Wasabi are
   fine and the HSM is reachable.
2. **Full quorum loss** -- enough nodes died simultaneously that Raft cannot
   elect a leader; surviving nodes refuse writes; you must rebuild from a
   snapshot.
3. **Total wipe with HSM lost** -- the worst recoverable case: data, config,
   nodes, and the HSM (or its wrapping keys) are all unrecoverable. You
   need the recovery shards, a replacement HSM (or Shamir fallback), and a
   verified snapshot.

If you have lost the recovery shards AND the HSM AND the data on disk, this
runbook does not help -- the cluster is unrecoverable and the next document
to read is the post-incident communication template, not this one.

## Pre-flight checklist

Run BEFORE touching anything in production. All must be green.

- [ ] Identify the target snapshot file (encrypted, `.enc` suffix). Latest
      daily snapshot path on Wasabi: `s3://bao-snapshots-<host>/daily/snapshot-<host>-<YYYY-MM-DDTHHMMZ>.snap.enc`
- [ ] Verify the snapshot's SHA-256 against the manifest:
      `sha256sum snapshot-<host>-<ts>.snap.enc | grep -F "$(grep snapshot-<host>-<ts>.snap.enc /var/lib/bao-snapshots/manifest.sha256 | awk '{print $2}')"`
- [ ] Confirm the encrypted snapshot's header is intact:
      `head -c 8 snapshot-<host>-<ts>.snap.enc | xxd | head -1` -- expect `BAOSNAP1`
- [ ] HSM is reachable from the restore host (otherwise the DEK cannot be
      unwrapped to decrypt the snapshot file): run `yubihsm-shell -a get-device-info`
      and confirm a serial number is returned.
- [ ] Disk space on restore host: at least `4 * snapshot_size` free under
      the OpenBao data directory (raft restore creates a temporary copy).
- [ ] Network reachability from the restore host to the Wasabi endpoint
      (only needed for scenario 3 if the local snapshot is gone too).
- [ ] Recovery shard custodians on standby (scenario 3 only -- you will
      need 3-of-5 shards within the maintenance window).
- [ ] Maintenance window confirmed in change ticket; on-call notified;
      every consumer of OpenBao understands the cluster will be sealed
      for the duration of the restore.
- [ ] `monitor-seal-status.sh` paging is silenced for the maintenance
      window (otherwise you will page yourself with the expected sealed
      state during the procedure).

## Time estimates

| Scenario | Wall-clock | Hands-on time | Recovery confidence |
|---|---|---|---|
| 1. Single-node corruption | 15 min | 10 min | High -- this is the well-trodden path |
| 2. Quorum loss | 35 min | 25 min | Medium -- requires `peers.json` step from `force-single-node-recovery.sh` |
| 3. Total wipe + HSM lost  | 4 hours | 2 hours of focused work, plus custodian coordination | Low -- practice this in sandbox at least once per quarter |

## Steps -- Scenario 1: Single-node corruption with HSM and snapshots intact

This is the recovery path the snapshot automation is designed for.

### Step 1 -- Stop OpenBao on the affected node

```bash
sudo systemctl stop openbao
sudo systemctl status openbao   # confirm "Active: inactive (dead)"
```

### Step 2 -- Move (do not delete) the corrupted data directory aside

NEVER delete the corrupted data until the restore is verified and you have
re-read at least one canary secret. The cost of keeping it is one
directory of disk; the cost of deleting it prematurely is "the snapshot
turned out to be corrupt and now you have nothing".

```bash
sudo mv /opt/openbao/data /opt/openbao/data.broken-$(date +%s)
sudo mkdir -p /opt/openbao/data
sudo chown openbao:openbao /opt/openbao/data
sudo chmod 700 /opt/openbao/data
```

### Step 3 -- Decrypt the snapshot file

The snapshot was encrypted by `snapshot-cron.sh` using a DEK wrapped by
`transit/encrypt/snapshot-key`. You need a temporary OpenBao instance OR
the original cluster's HSM to unwrap. For scenario 1 the original cluster
exists -- start a sandbox on the recovery host that mounts the same HSM
seal type, or restore directly via the recovery script:

```bash
cd /opt/egambling-book/writing/new-book/scripts/chapter-20b

# BAO_ADDR must point at a cluster that still holds the wrapping key, and the
# token needs `update transit/decrypt/snapshot-key` and nothing more.
export BAO_ADDR=https://127.0.0.1:8200
export BAO_TOKEN_FILE=/etc/bao-snapshot/restore-token

./disaster-recovery/decrypt-snapshot.sh \
  --in /var/lib/bao-snapshots/snapshot-ops-host-2026-04-17T0300Z.snap.enc \
  --out /tmp/snapshot.snap \
  --manifest /var/lib/bao-snapshots/manifest.sha256
```

`decrypt-snapshot.sh` reads the `BAOSNAP1` header, verifies the file's SHA-256
against the manifest before spending an HSM operation, calls
`transit/decrypt/snapshot-key` to unwrap the per-snapshot DEK, AES-GCM decrypts
the body, and writes the cleartext raft snapshot to `--out` with mode 0600. It
checks that the result is gzip-framed before handing it to you, so a decrypt
that produced garbage is reported as such rather than surfacing later as a
confusing restore failure.

Exit codes worth knowing at 3am: `2` means the integrity check failed or the
file is not a `BAOSNAP1` envelope (try the previous snapshot); `3` means the
transit unwrap failed (check the token policy, and check whether
`min_decryption_version` has been advanced past the key version that wrapped
this DEK); `4` means AES-GCM rejected the body, which is corruption. On any
non-zero exit the script removes the partial output rather than leaving a
half-decrypted file that looks restorable.

If the local host is gone and you pulled the snapshot from Wasabi, the manifest
is there too: `snapshot-cron.sh` uploads `manifest/manifest.sha256` and a
per-snapshot `<key>.sha256` sidecar alongside each object.

```bash
aws --endpoint-url https://s3.wasabisys.com \
  s3 cp s3://bao-snapshots-<host>/manifest/manifest.sha256 /tmp/manifest.sha256
```

### Step 4 -- Start OpenBao with the empty data directory

```bash
sudo systemctl start openbao
# Wait for sealed state to be reachable
until curl -ksf https://127.0.0.1:8200/v1/sys/seal-status >/dev/null; do sleep 1; done
bao status        # expect Initialized: false (or true with sealed: true if HSM auto-unseal triggered)
```

### Step 5 -- Restore the snapshot

```bash
bao operator raft snapshot restore -force /tmp/snapshot.snap
```

### Step 6 -- Unseal (if not auto-unsealed by HSM)

If the cluster uses Shamir, supply the unseal shards:

```bash
bao operator unseal <shard-1>
bao operator unseal <shard-2>
bao operator unseal <shard-3>
bao status   # expect Sealed: false
```

If the cluster uses HSM auto-unseal, the seal flips to `false`
automatically within 5 seconds of the restore completing.

### Step 7 -- Verify

The canary secret is the freshness beacon maintained by your snapshot
discipline (see `verify-raft-snapshot.sh`). Read a known secret:

```bash
bao kv get platform/secrets/canary
# Expect a non-empty value with the timestamp of the last freshness write
```

If this fails, the snapshot is corrupt and you are now in scenario 3.

### Step 8 -- Re-enable monitoring and silence the maintenance window

```bash
sudo systemctl start monitor-seal-status.timer    # if you stopped it
# Reopen the change ticket with restore complete + verification timestamp.
# Tail monitor-seal-status output: expect HEALTHY heartbeats every 30s.
sudo journalctl -u monitor-seal-status.service -f -n 5
```

### Step 9 -- Clean up

```bash
shred -u /tmp/snapshot.snap
# After 24 hours of stable operation, the .broken directory can be removed:
# sudo rm -rf /opt/openbao/data.broken-<timestamp>
```

## Steps -- Scenario 2: Quorum loss in a multi-node cluster

You have at least one surviving node but cannot elect a leader. The
`force-single-node-recovery.sh` script writes the `peers.json` file
described in the chapter's "Failure 3: Losing Quorum" sub-section. Run it
on the surviving node, then add new nodes back one at a time:

```bash
# On the surviving node
sudo systemctl stop openbao
sudo /opt/egambling-book/writing/new-book/scripts/chapter-20b/disaster-recovery/force-single-node-recovery.sh
sudo systemctl start openbao
# Unseal as in scenario 1, step 6.

# For each replacement node:
ssh new-node-2 'systemctl stop openbao && rm -rf /opt/openbao/data && systemctl start openbao'
ssh new-node-2 'bao operator raft join https://surviving-node:8200'
# Repeat until quorum restored.
bao operator raft list-peers
```

If even the surviving node's data is too damaged to use, fall back to
scenario 1 on that node first (restore from snapshot) and then add peers.

## Steps -- Scenario 3: Total wipe with HSM lost

This is the once-per-decade recovery and the reason the recovery shards
exist. High-level flow:

1. Provision a new host (or reuse the old one with a fresh OS install).
2. Install `openbao-hsm` and the HSM software stack. If the HSM hardware
   is also lost, you can fall back to Shamir-only by initialising with
   `seal "shamir"` -- but the new cluster is a NEW cluster, not the old
   one, until you migrate via `bao operator seal-migration`.
3. Provision a replacement HSM with a fresh wrapping key.
4. Initialise the new cluster:
   ```bash
   bao operator init -recovery-shares=5 -recovery-threshold=3
   # Save the new recovery shards to the new custodians.
   ```
5. Pull the latest encrypted snapshot from Wasabi:
   ```bash
   aws --endpoint-url https://s3.wasabisys.com s3 cp \
     s3://bao-snapshots-<host>/daily/snapshot-<host>-<latest>.snap.enc \
     /tmp/snapshot.snap.enc
   ```
6. **Decrypting the snapshot in this scenario is the hard part.** The DEK
   wrapped in the snapshot header was wrapped by the OLD HSM's transit
   key, which is gone. If you maintained an off-HSM age-encrypted backup
   of the snapshot (see chapter "Raft Snapshots and Backup" sub-section
   on age archival), use that path instead -- the age recipients are
   independent of the HSM. Without either, the snapshot's contents are
   permanently encrypted and you must rebuild every secret from upstream
   sources (re-issuing database credentials, regenerating PKI, refetching
   API keys from vendor dashboards). Time estimate: 2-5 days for a
   medium platform; this is the cost of skipping the age-archive belt
   when the HSM is the braces.
7. If you do have the age-encrypted snapshot:
   ```bash
   age -d -i /etc/openbao/archive-recipients.key /tmp/snapshot.snap.age > /tmp/snapshot.snap
   bao operator raft snapshot restore -force /tmp/snapshot.snap
   ```
8. After restore, the master key inside the snapshot is encrypted by the
   OLD HSM's wrapping key, which the new HSM does not have. OpenBao will
   refuse to unseal until you run a recovery-shard-driven seal migration:
   ```bash
   bao operator seal-migration  # interactive; supply 3 of 5 OLD recovery shards
   ```
9. Once the migration completes, the master key is re-wrapped under the
   new HSM and the cluster unseals normally.
10. Verify with the canary read (as in scenario 1, step 7).
11. Rotate every secret as a precaution. Even a successful recovery from
    this scenario means the OLD HSM is unaccounted for, and a missing
    HSM is a tamper signal until proven otherwise. PKI roots, database
    credentials, signing keys -- all rotated.

## Verification (all scenarios)

```bash
# 1. Seal status
bao status | grep -E '^(Sealed|Initialized|HA Mode)'
# expect: Sealed: false, Initialized: true, HA Mode: active or standby

# 2. Canary secret
bao kv get platform/secrets/canary

# 3. Probe heartbeat resumed
sudo journalctl -u monitor-seal-status.service -n 3 | grep -F HEALTHY

# 4. Audit device active
bao audit list

# 5. End-to-end via a test policy
bao token create -policy=default -ttl=5m | grep -F token=
```

If any of these fail, do NOT close the incident. Page a second on-call,
keep the maintenance window open, and revert by stopping OpenBao and
restoring the `data.broken-<ts>` directory aside as a fallback.

## Post-incident

- [ ] File a post-mortem within 48 hours
- [ ] Update this runbook with anything that did not match reality
- [ ] If scenario 3, audit recovery shard custody and replace any shard
      whose chain of custody is now ambiguous
- [ ] Run `verify-raft-snapshot.sh` against the most recent snapshot in
      the throw-away sandbox to confirm future recoveries will work
- [ ] Re-enable monitor-seal-status paging if it was silenced
- [ ] Update the change ticket with start/end times and any deviation
      from the runbook
