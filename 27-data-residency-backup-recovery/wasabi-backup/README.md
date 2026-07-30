# Chapter 27 — Wasabi Encrypted Backup Scripts

Nightly encrypted PostgreSQL backups to Wasabi S3-compatible object storage.  
The pipeline is: `pg_dump` → `gzip -9` → `GPG AES-256` → upload, with SHA-256 checksums and a JSON manifest per backup. A matching restore script downloads, verifies, decrypts, and optionally restores to a test database.

---

## Files

| File | Purpose |
|---|---|
| `wasabi-backup-encrypted.sh` | Nightly backup: dump → compress → encrypt → upload → enforce retention |
| `wasabi-restore.sh` | Restore: download → verify SHA-256 → decrypt → pg_restore into test DB |
| `backup-status-refresh.sh` | Cron/timer helper: updates `backup-status.json` with days-since-restore |
| `wasabi-backup.service` | systemd service unit for the backup script |
| `wasabi-backup.timer` | systemd timer (runs at 03:00 daily, persistent) |
| `Dockerfile` | Reference container with all deps (aws-cli, gpg, postgresql-client) |
| `verify-sanitization.sh` | Grep-based check that no secrets leaked into published files |

---

## Prerequisites

| Tool | Minimum version | Notes |
|---|---|---|
| `aws` CLI v2 | 2.x | `aws configure` with Wasabi endpoint |
| `gpg` | 2.2+ | symmetric AES-256 (`--cipher-algo AES256`) |
| `docker` | 20.x+ | runs `pg_dump` / `pg_restore` inside DB container |
| `postgresql-client` | 14+ | only needed if running outside Docker |
| `python3` | 3.8+ | manifest parsing in `wasabi-restore.sh` |
| `sha256sum` | GNU coreutils | integrity verification |

---

## Setup

### 1. Wasabi credentials

Create a named profile so keys are never embedded in scripts:

```bash
mkdir -p ~/.wasabi
chmod 700 ~/.wasabi

cat > ~/.wasabi/credentials << 'EOF'
[wasabi-acmetocasino]
aws_access_key_id     = <YOUR_WASABI_ACCESS_KEY>
aws_secret_access_key = <YOUR_WASABI_SECRET_KEY>
EOF

chmod 600 ~/.wasabi/credentials
```

Configure the AWS CLI to use this credentials file and endpoint:

```bash
# In ~/.aws/config or pass --endpoint-url at runtime (already in the scripts)
# The scripts use --profile wasabi-acmetocasino --endpoint-url https://s3.eu-central-1.wasabisys.com
```

### 2. Encryption passphrase

```bash
# Generate a strong passphrase and store it in a file — never hardcode it
openssl rand -base64 48 > ~/.wasabi/encryption-passphrase
chmod 600 ~/.wasabi/encryption-passphrase
```

Store a copy of this passphrase in your password manager or secrets vault.  
Without it, encrypted backups cannot be decrypted.

### 3. Working directories

```bash
mkdir -p /opt/backups/wasabi-tmp /opt/backups/wasabi-restore-tmp
chmod 700 /opt/backups/wasabi-tmp /opt/backups/wasabi-restore-tmp
mkdir -p /var/www/acmetocasino.com/api-data
```

### 4. Make scripts executable

```bash
chmod +x /opt/scripts/wasabi-backup-encrypted.sh \
         /opt/scripts/wasabi-restore.sh \
         /opt/scripts/backup-status-refresh.sh
```

---

## Scheduling

### Option A — cron

```cron
# /etc/cron.d/wasabi-backup
# Nightly backup at 03:00
0 3 * * * root /opt/scripts/wasabi-backup-encrypted.sh >> /var/log/wasabi-backup.log 2>&1

# Refresh status JSON every 15 minutes
*/15 * * * * root /opt/scripts/backup-status-refresh.sh >> /var/log/wasabi-backup.log 2>&1
```

### Option B — systemd timer (recommended)

```bash
# Copy units
cp wasabi-backup.service wasabi-backup.timer /etc/systemd/system/

# Enable and start
systemctl daemon-reload
systemctl enable --now wasabi-backup.timer

# Check status
systemctl status wasabi-backup.timer
systemctl list-timers wasabi-backup.timer
journalctl -u wasabi-backup.service -f
```

---

## Running a backup manually

```bash
/opt/scripts/wasabi-backup-encrypted.sh
tail -f /var/log/wasabi-backup.log
```

---

## Restore

### Dry-run (download + verify, no DB changes)

```bash
/opt/scripts/wasabi-restore.sh \
  postgres/new_acmetocasino/2026/04/12/backup-030000.dump.gz.gpg \
  --dry-run
```

### Full restore to a test database

```bash
/opt/scripts/wasabi-restore.sh \
  postgres/new_acmetocasino/2026/04/12/backup-030000.dump.gz.gpg \
  --target-db new_acmetocasino_restore_test
```

The restore script blocks restoration to the production database by name to prevent accidental overwrites.

### List available backups

```bash
aws --profile wasabi-acmetocasino \
    --endpoint-url https://s3.eu-central-1.wasabisys.com \
    s3 ls s3://acmetocasino/postgres/new_acmetocasino/ --recursive \
  | grep '\.dump\.gz\.gpg$' \
  | sort
```

---

## Running as a container

```bash
docker build -t wasabi-backup .

# Backup
docker run --rm \
  -v /root/.wasabi:/root/.wasabi:ro \
  -v /opt/backups:/opt/backups \
  -v /var/log:/var/log \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e PROMETHEUS_PUSHGATEWAY_URL=http://localhost:9091 \
  wasabi-backup

# Restore (dry-run)
docker run --rm \
  -v /root/.wasabi:/root/.wasabi:ro \
  -v /opt/backups:/opt/backups \
  -v /var/log:/var/log \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --entrypoint /opt/scripts/wasabi-restore.sh \
  wasabi-backup \
  postgres/new_acmetocasino/2026/04/12/backup-030000.dump.gz.gpg --dry-run
```

---

## Testing

### Lint with shellcheck

```bash
shellcheck wasabi-backup-encrypted.sh wasabi-restore.sh backup-status-refresh.sh verify-sanitization.sh
```

### Verify no secrets leaked

```bash
bash verify-sanitization.sh
```

Expected output: all `OK` lines, exit 0.

### End-to-end smoke test (non-destructive)

1. Run a manual backup and confirm log shows `Backup complete`.
2. Copy the S3 key from the log output.
3. Run `wasabi-restore.sh <key> --dry-run` and confirm `SHA256 MATCH` and `DRY-RUN complete`.
4. Run `wasabi-restore.sh <key> --target-db acmetocasino_test` and confirm `INTEGRITY CHECK: PASSED`.

---

## Security notes

- **Keys never in scripts.** Credentials live in `~/.wasabi/credentials` (chmod 600), consumed via AWS named profile. The scripts reference the profile name only.
- **Passphrase never in scripts.** The path to the passphrase file is read at runtime via `$PASSPHRASE_FILE` env var (default `/root/.wasabi/encryption-passphrase`, chmod 600). GPG uses `--passphrase-file` in `--batch` mode.
- **Temp files are chmod 700 directories.** Encrypted intermediates in `/opt/backups/wasabi-tmp` are removed immediately after upload.
- **No decrypted data written to disk during backup.** The `pg_dump | gzip | gpg` pipeline is streamed; only the `.gpg` file touches disk.
- **SHA-256 tamper detection.** Each backup has a sidecar `.sha256` file. Restore verifies the checksum before decrypting.
- **Production restore is blocked by default.** `wasabi-restore.sh` refuses to restore directly into the production DB; you must change the `--target-db`.
- **Retention enforcement.** Daily backups kept 30 days; first-of-month backups kept 365 days. Deletion runs automatically after each successful backup.

---

## Prometheus metrics (optional)

Set `PROMETHEUS_PUSHGATEWAY_URL` to push these metrics after each backup:

| Metric | Type | Description |
|---|---|---|
| `wasabi_backup_success` | gauge | 1 = success, 0 = failure |
| `wasabi_backup_size_bytes` | gauge | Encrypted file size |
| `wasabi_backup_duration_seconds` | gauge | Wall-clock time |
| `wasabi_backup_timestamp_seconds` | gauge | Unix timestamp of last run |

---

## Related chapter 27 sections

- **27.1** — Object storage overview: Wasabi vs AWS S3 vs Backblaze B2 cost comparison
- **27.2** — GPG symmetric encryption: AES-256, key stretching, batch mode
- **27.3** — Retention policies: daily/monthly tiering, lifecycle rules
- **27.4** — Restore drills: scheduling weekly dry-runs, integrity checks
- **27.5** — Dashboard integration: `backup-status.json` and the ops panel
