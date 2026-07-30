<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 33e: Docker Disk Lifecycle, Truncation, and the Anatomy of a Disk-Full Incident

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 33e of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

This directory contains the artefacts described in Chapter 33e of the book.

## Files

| File | Purpose |
|---|---|
| `docker-containers.logrotate` | logrotate config for Docker JSON logs (copytruncate) |
| `docker-daily-prune.sh` | Daily Docker prune script (image + system + volume) |
| `disk-alerts.yml` | Prometheus alert rule group (5 rules) |
| `wazuh-disk-rule.xml` | Wazuh local rule for maintenance escalation |
| `config.env.example` | Customisable values for your environment |
| `deploy.sh` | One-shot installer (idempotent) |
| `verify.sh` | Post-install health check |

## Quick start

```bash
# 1. Customise config (optional — defaults are sensible)
cp config.env.example config.env
$EDITOR config.env

# 2. Install
sudo ./deploy.sh

# 3. Verify
./verify.sh
```

## What gets installed where

- `/etc/logrotate.d/docker-containers` — log rotation
- `/usr/local/sbin/docker-daily-prune.sh` — daily prune script
- `/etc/systemd/system/docker-daily-prune.{service,timer}` — systemd unit + timer (enabled)
- `/etc/prometheus-rules-staging/disk-alerts.yml` — Prometheus rules (you mount this into your Prometheus container)
- `/etc/prometheus-rules-staging/wazuh-disk-rule.xml` — Wazuh rule (you append this to your Wazuh `local_rules.xml`)

## Uninstall

```bash
sudo ./deploy.sh --uninstall
```

This removes the systemd timer + service + script + logrotate config. Prometheus and Wazuh artefacts in the staging dir are NOT auto-removed (their integration into your monitoring stack varies).

## Read the chapter

For the why behind each file, read Chapter 33e in the book.
