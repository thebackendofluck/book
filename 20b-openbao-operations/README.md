<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-03.jpg" alt="Volume 3" width="150" /></a>

# Chapter 20b: OpenBao Operations: Secret Engines, Dynamic Credentials and Disaster Recovery

**📙 Part of Volume 3 — Security Engineering and Runtime Defense** · €84.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZCRSTMH) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 20b of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

Scripts that accompany `chapters/20b-openbao-operations.md`. Every one of
them targets the isolated sandbox started by `setup/start-sandbox.sh` and
is safe to run against a developer machine or the ops-host host without
any risk to the production OpenBao instance on `:8200`.

## Layout

```
chapter-20b/
├── lib/
│   └── common.sh                     # shared helpers (ensure_sandbox, retry, load_root_token)
├── setup/
│   ├── start-sandbox.sh              # boots an isolated OpenBao on 127.0.0.1:18300
│   └── stop-sandbox.sh               # terminates and (optionally) wipes it
├── secrets-engines/
│   ├── transit-setup.sh              # enable transit + create platform-pii / platform-audit-sign
│   ├── kv2-setup.sh                  # enable kv-v2 at platform/secrets/ with CAS required
│   ├── db-engine-postgres.sh         # configure PostgreSQL database engine (dynamic + static)
│   ├── pki-setup.sh                  # build a two-tier CA (pki/ + pki_int/)
│   ├── pki-issue-cert.sh             # issue and verify a leaf certificate
│   └── policies/
│       ├── platform-pii-encrypt.hcl    # least-authority policy for PII encrypt/decrypt
│       ├── platform-audit-sign.hcl     # SIGN-only, for the audit emitter
│       └── platform-audit-verify.hcl   # VERIFY-only, for an independent auditor
├── ops/
│   ├── rotate-transit-key.sh         # rotate + rewrap + advance min_decryption_version
│   ├── rewrap-all-ciphertexts.py     # batch rewrap for a Postgres column + gate report
│   ├── audit-enable.sh               # enable file + syslog audit devices
│   ├── audit-rotate-test.sh          # simulate logrotate; verify audit keeps writing
│   ├── validate-retention-policy.py  # CI gate: logrotate config vs jurisdictional minima
│   ├── pki-revoke-and-check.sh       # revoke a cert and verify it appears in the CRL
│   ├── backup-raft.sh                # raft snapshot + sha256 manifest + 14d prune
│   ├── verify-raft-snapshot.sh       # restore into a throw-away verify sandbox + canary read
│   ├── hsm-collector.py              # 30s Python collector -> Redis (reference of /opt/hsm-collector.py)
│   ├── redis-dashboard-query.sh      # read-side queries for the HSM Security dashboard panel
│   ├── health-check.py               # Nagios-compatible health check (seal + audit + disk)
│   ├── monitor-seal-status.sh        # 30s seal-status probe -> syslog + Prometheus textfile
│   ├── systemd/
│   │   ├── monitor-seal-status.service
│   │   └── monitor-seal-status.timer  # OnUnitActiveSec=30s
│   └── wazuh/
│       └── bao-seal-rule.xml         # P1 alert rule on `sealed=true`
├── disaster-recovery/
│   ├── force-single-node-recovery.sh    # peers.json quorum-loss drill (RESTORE)
│   ├── snapshot-cron.sh                 # daily raft snapshot + HSM-encrypted Wasabi upload (BACKUP)
│   ├── decrypt-snapshot.sh              # BAOSNAP1 envelope -> cleartext raft snapshot (RESTORE)
│   └── restore-from-snapshot-runbook.md # step-by-step recovery for 3 failure scenarios
└── tests/
    ├── kv2-cas-test.sh               # verify cas_required rejects stale writes
    ├── test-collector-liveness.sh    # verify collector death is detected via TTL expiry
    └── smoke-test.sh                 # end-to-end exerciser for every script in order
```

## Quick start

```bash
# 1. Start the sandbox
./setup/start-sandbox.sh

# 2. Provision the engines
./secrets-engines/transit-setup.sh
./secrets-engines/kv2-setup.sh

# 3. Run the smoke test for a one-shot verification
./tests/smoke-test.sh

# 4. Tear down
./setup/stop-sandbox.sh
```

## Safety guarantees

* `lib/common.sh :: ensure_sandbox` parses the host and port out of `$BAO_ADDR`
  and aborts unless it is loopback on `$SANDBOX_PORT`. `SANDBOX_PORT` itself is
  validated at source time: it must be numeric, in the 18300-18399 range, and
  not a production port, so `SANDBOX_PORT=8200` cannot turn production into a
  "sandbox".
* `common.sh` *defaults* `BAO_ADDR` rather than assigning it, so a value the
  operator exported survives and is what the guards actually judge.
* `disaster-recovery/force-single-node-recovery.sh` additionally rejects
  `:8200`/`:8201` **before** sourcing `common.sh`, and refuses to run if
  `SANDBOX_DIR` points at a production path.

`disaster-recovery/decrypt-snapshot.sh` is the one script here that
deliberately does NOT source `lib/common.sh`: a restore runs against a real
cluster, and `ensure_sandbox` would refuse exactly when the script is needed.
* The sandbox uses a file backend under `/tmp/openbao-sandbox-20b/` by
  default. If the host reboots before teardown, `/tmp` is cleared and the
  sandbox leaves nothing behind.
* No script in this tree writes to `/etc/openbao/`, `/opt/openbao/`,
  `/var/log/openbao/`, or any path owned by the production `openbao`
  systemd unit.

## Running on ops-host

The scripts are safe to run directly on the ops-host host as long as you
use a non-root user. The production openbao instance is untouched because
it listens on `:8200` (TLS) while the sandbox listens on `:18300`
(plaintext, 127.0.0.1 only). See the chapter text under "Why a Separate
Operations Chapter" for the full isolation rationale.
