<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-03.jpg" alt="Volume 3" width="150" /></a>

# Chapter 24j: IP Reputation and Blocklist Integration for iGaming Platforms

**📙 Part of Volume 3 — Security Engineering and Runtime Defense** · €84.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZCRSTMH) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 24j of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Suricata iprep integration, OPNsense alias management, AbuseIPDB aggregation, and automated false-positive detection for iGaming threat intelligence.

## Overview

Production scripts for integrating multi-source IP reputation feeds (Data-Shield, Emerging Threats, Spamhaus, FireHOL, Tor, blocklist.de, abuse.ch, optionally AbuseIPDB) into Suricata 7.x via the `iprep` system and OPNsense URL table aliases. Includes an aggregation pipeline that merges feeds, deduplicates entries per category, assigns confidence scores, and detects false positives against payment provider whitelists. A systemd timer keeps blocklists updated every 4 hours.

Every category referenced by `suricata/ip-reputation.rules` has a feed in `iprep_update.py` that writes it, and every score a feed assigns clears the threshold of the rules that read it. Those two invariants are what make the rules able to fire at all, so re-check both when adding a feed or retuning a score. `reputation.meta.json` records the per-category line counts of the last run, which is the fastest way to see that a category went empty.

## Contents

- `python/iprep_aggregator.py` — Consensus scoring helper: given all entries for one IP, applies a multi-source bonus
- `python/iprep_update.py` — The pipeline. Downloads, validates and merges every feed, writes `reputation.list`, `whitelist.list`, the OPNsense lists under `serve/`, and `reputation.meta.json`, then reloads Suricata through `suricatasc` (falling back to SIGUSR2)
- `python/parse_datashield.py` — Parses Data-Shield IPv4 blocklist format into Suricata `iprep` category entries
- `python/iprep_abuseipdb.py` — AbuseIPDB integration: `download_blacklist()` fetches the bulk blacklist for category 12, `check_and_add_to_iprep()` does per-IP lookups. Both need `ABUSEIPDB_API_KEY`
- `python/iprep_fp_detector.py` — Scans Suricata `eve.json` for iprep drops that carry a payment provider User-Agent, which is the signature of a false positive
- `python/blocklist_server.py` — Local HTTP server exposing the aggregated blocklists for OPNsense URL table alias pulls
- `suricata/suricata.yaml` — Suricata 7.x configuration fragment enabling `iprep` and reputation rules
- `suricata/ip-reputation.rules` — Custom Suricata rules matching high-confidence blocklist categories
- `suricata/categories.txt` — iprep category definitions (Botnet, Scanner, ProxyVPN, TorExit, CredStuffing, DataShield, EmergingThreats, AbuseIPDB, Spamhaus, FireHOL, Whitelist and reserved IDs), annotated with which feed populates each
- `suricata/whitelist.txt` — Authoritative whitelist: payment processors, game provider IPs, Cloudflare ranges
- `bash/fetch_datashield.sh` — Fetches both Data-Shield lists and records a sha256 for each. Upstream publishes no signed digest, so this detects a truncated or unchanged download rather than proving authenticity
- `bash/iprep_systemd_setup.sh` — Installs systemd service and timer for scheduled blocklist updates
- `bash/iprep-notify.sh` — Runs as `ExecStartPost`. Appends the entry count, file digest and per-category counts to `/var/log/iprep/audit.log` on every update, and posts the same summary to Slack when `IPREP_SLACK_WEBHOOK` is set
- `bash/iprep-rollback.sh` — Rolls back to the previous known-good `iprep` file if validation fails
- `bash/verify_opnsense_alias.sh` — Verifies OPNsense URL table alias has loaded the updated blocklist
- `systemd/iprep-update.{service,timer}` — Systemd unit for 4-hour scheduled blocklist refresh

## Technology Stack

- **IDS/IPS:** Suricata 7.0.x (iprep reputation system)
- **Firewall:** OPNsense (URL table aliases)
- **Language:** Python 3.11+, Bash
- **Feeds:** Data-Shield IPv4 Blocklist, AbuseIPDB API, Emerging Threats
- **Scheduler:** systemd timers

## Prerequisites

- Suricata 7.x with `iprep` support compiled in
- Python 3.11+, standard library only. No third-party packages
- `ABUSEIPDB_API_KEY` for the AbuseIPDB feed (category 12). Without it that feed is disabled and rule 9100080 is inert by design; every other rule works
- `systemctl` available for timer installation
- OPNsense admin access for alias verification

## How to Run

```bash
# Install systemd timer for automated updates
sudo bash bash/iprep_systemd_setup.sh

# Manual blocklist refresh
sudo python python/iprep_update.py --loglevel INFO

# Download and validate everything without writing or reloading
python python/iprep_update.py --dry-run

# Run against a scratch directory rather than /etc/suricata
IPREP_DIR=/tmp/iprep-test IPREP_STAGING_DIR=/tmp/iprep-var \
  python python/iprep_update.py --dry-run

# Check for false positives in the last 24h of Suricata alerts
python python/iprep_fp_detector.py --eve /var/log/suricata/eve.json --hours 24

# Serve the aggregated lists to OPNsense (bind to the management VLAN in prod)
BLOCKLIST_SERVE_DIR=/var/lib/iprep/serve python python/blocklist_server.py

# Verify OPNsense alias loaded
bash bash/verify_opnsense_alias.sh
```

Exit codes from `iprep_update.py`: `0` success, `1` the reputation file was
written but the Suricata reload failed, `2` nothing was deployed. The systemd
unit treats `1` as success on purpose so the notification and audit record
still run for exactly the case that needs investigating.

## Security Notes

Each feed is validated on entry count, on total address space covered, and on a minimum prefix length before anything is written, so a single bad upstream line cannot put a `/8` behind a drop rule. The previous file is kept as `reputation.list.bak` and `iprep-rollback.sh` restores it. Payment processor IP ranges in `suricata/whitelist.txt` must be reviewed every quarter, since blocking a PSP IP during a peak event is a high-severity incident. Whitelist entries are filtered out while the blocklist is built and also written to `whitelist.list` as category 15, which gives the pass rule (sid 9100090) data to match and lets it override any drop rule.

## Related

- See Chapter 24j in the book for the threat taxonomy, Data-Shield feed analysis, and the 77.7% blocklist overlap case study from the 2023 Champions League attack.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 2 · last updated 2026-04-16.</sub>
