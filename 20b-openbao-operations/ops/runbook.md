# OpenBao On-Call Runbook

A short, edit-during-incidents runbook for the OpenBao cluster
documented in chapter 20b. Keep it under 500 words so it fits on one
laptop screen when the cluster is sealed and the on-call engineer is
trying to think.

Each outgoing on-call rotation is expected to edit exactly one line of
this file with whatever they learned -- bad assumption corrected, new
alert noticed, command that took longer than expected. The history in
git is the living documentation.

## 1. OpenBao unreachable from an application

First check whether OpenBao is sealed:

```bash
curl -sk https://127.0.0.1:8200/v1/sys/seal-status | jq .
```

- `sealed: true`  -> follow §2 "OpenBao sealed".
- `sealed: false` -> check token validity and network reachability on
  the caller side (`bao status`, `nc -zv openbao.internal 8200`).

## 2. OpenBao sealed

1. Retrieve Shamir shards from custody per the documented key ceremony.
2. Unseal using three of the five shards. Each custodian runs:

   ```bash
   bao operator unseal <their-shard>
   ```

3. Wait ~10 seconds for post-unseal handlers. Verify:

   ```bash
   bao status
   bao audit list
   ```

4. Confirm downstream services recover before stepping away.

## 3. A service is rate-limited

Check `sys/metrics` for rate-limit counters. Rate limits are usually a
runaway client, not insufficient OpenBao capacity. Find the offending
client via the audit log:

```bash
jq -c 'select(.auth.display_name == "SUSPECT_TOKEN_DISPLAY_NAME")
       | {time: .time, path: .request.path}' \
   /var/log/openbao/audit.log | tail -20
```

## 4. An audit device has failed

```bash
bao audit list
bao audit disable <failed-path>      # NOTE: requires config reload on 2.5+
```

If both devices are failing, the cluster is **writeable but silent**.
Treat as a P1 and page a second on-call. With OpenBao 2.5+, restoring
an audit device means editing `/etc/openbao/openbao.hcl` and sending
`SIGHUP` -- the `bao audit enable` API is rejected.

## 5. A snapshot restore is needed

Only when the primary store is definitely lost.

1. `systemctl stop openbao`
2. Move the current data dir aside (never delete until verified):

   ```bash
   mv /opt/openbao/data /opt/openbao/data.quarantine.$(date +%s)
   ```

3. Restore the latest snapshot:

   ```bash
   bao operator raft snapshot restore /var/backups/openbao/snapshot-latest.snap
   ```

4. `systemctl start openbao`
5. Unseal with the **original** Shamir keys (not the new ones).
6. Verify via the canary secret:

   ```bash
   bao kv get platform/secrets/canary
   ```

7. Any step failing -> page for help. Do not improvise a recovery.

## 6. The YubiHSM USB device is missing

The dashboard panel at `https://new.acmetocasino.com/dashboard.html`
monitors `lsusb` for the Yubico VID/PID. A P0 alert fires when the
device disappears. Unless a change ticket explains the removal,
**treat as a tamper event**:

1. Do not unseal or restart OpenBao.
2. Page the CISO immediately.
3. Preserve the ops-host host state for forensics.

## 7. Known good metrics

| Metric | Expected |
|---|---|
| `sys/seal-status` latency | < 5 ms from localhost |
| `transit/encrypt/X` latency | 6 -- 10 ms |
| Memory resident | ~180 MB steady state |
| Daily raft TLS key rotation | ~08:15 local, visible in journald |

Anything outside these envelopes deserves a follow-up ticket even if
the cluster is answering requests.
