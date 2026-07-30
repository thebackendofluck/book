#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Jurisdiction-Aware Wasabi Backup
# =============================================================================
# Uploads encrypted backup archives to the jurisdiction-correct Wasabi region.
# Wasabi is S3-compatible — the AWS CLI (v2) is used with a custom endpoint.
#
# This script is the offsite copy (Copy 3 of the 3-2-1 rule).  It does NOT
# create the backup itself; it consumes archives produced by backup_manager.sh
# and pushes them to the jurisdiction-approved Wasabi bucket.
#
# Usage:
#   ./wasabi-backup.sh upload  <file> [<object-key-suffix>]
#   ./wasabi-backup.sh sync    <local-dir>
#   ./wasabi-backup.sh list
#   ./wasabi-backup.sh verify  <object-key>
#   ./wasabi-backup.sh lifecycle-apply
#   ./wasabi-backup.sh check-region
#
# Required environment variables (set in /etc/igaming/wasabi-<jurisdiction>.env):
#   JURISDICTION          Jurisdiction code: mga | ukgc | dge_nj | pgcb_pa | mgcb_mi | agco_on | pagcor
#   WASABI_ACCESS_KEY     Wasabi IAM access key
#   WASABI_SECRET_KEY     Wasabi IAM secret key
#   WASABI_REGION         Wasabi region (MUST match wasabi-jurisdiction-config.py)
#   WASABI_ENDPOINT       https://s3.<region>.wasabisys.com
#   WASABI_BUCKET         Full bucket name (use bucket_name() from the config)
#   ENCRYPTION_KEY_FILE   Path to client-side encryption key (AES-256-CBC)
#
# Optional:
#   SECONDARY_REGION      If cross-region replica is permitted
#   SECONDARY_ENDPOINT    https://s3.<secondary-region>.wasabisys.com
#   SECONDARY_BUCKET      Bucket name in secondary region
#   ALERT_WEBHOOK         Slack/PagerDuty webhook URL
#   ALERT_EMAIL           Email for backup alerts
#
# Jurisdiction-to-region mapping (enforced at runtime by check-region):
#   mga       → eu-central-1  (Amsterdam, NL)
#   ukgc      → eu-west-1     (London, UK)
#   dge_nj    → us-east-1     (Ashburn, VA)  [archive only, needs DGE approval]
#   pgcb_pa   → us-east-1     (Ashburn, VA)
#   mgcb_mi   → us-east-1     (Ashburn, VA)
#   agco_on   → ca-central-1  (Toronto, CA)
#   pagcor    → ap-southeast-1 (Singapore)   [needs PAGCOR approval]
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Jurisdiction → approved region map (mirrors wasabi-jurisdiction-config.py)
# Update both files if adding a new jurisdiction.
# ---------------------------------------------------------------------------
declare -A APPROVED_PRIMARY_REGIONS=(
    [mga]="eu-central-1"
    [ukgc]="eu-west-1"
    [dge_nj]="us-east-1"
    [pgcb_pa]="us-east-1"
    [mgcb_mi]="us-east-1"
    [agco_on]="ca-central-1"
    [pagcor]="ap-southeast-1"
)

declare -A APPROVED_SECONDARY_REGIONS=(
    [mga]="eu-central-2"
    [pgcb_pa]="us-east-2"
    [mgcb_mi]="us-east-2"
    # ukgc, dge_nj, agco_on, pagcor: no secondary permitted
)

# ---------------------------------------------------------------------------
# Load jurisdiction environment
# ---------------------------------------------------------------------------
JURISDICTION="${JURISDICTION:-}"
WASABI_REGION="${WASABI_REGION:-}"
WASABI_ENDPOINT="${WASABI_ENDPOINT:-}"
WASABI_BUCKET="${WASABI_BUCKET:-}"
WASABI_ACCESS_KEY="${WASABI_ACCESS_KEY:-}"
WASABI_SECRET_KEY="${WASABI_SECRET_KEY:-}"
ENCRYPTION_KEY_FILE="${ENCRYPTION_KEY_FILE:-/etc/igaming/backup-encryption.key}"
SECONDARY_REGION="${SECONDARY_REGION:-}"
SECONDARY_ENDPOINT="${SECONDARY_ENDPOINT:-}"
SECONDARY_BUCKET="${SECONDARY_BUCKET:-}"
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"
ALERT_EMAIL="${ALERT_EMAIL:-dba@acme-casino.com}"

LOG_DIR="${LOG_DIR:-/var/log/igaming/wasabi}"
mkdir -p "$LOG_DIR"

DATE_STAMP=$(date -u +"%Y%m%dT%H%M%SZ")
LOG_FILE="${LOG_DIR}/wasabi_${JURISDICTION}_${DATE_STAMP}.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    local level="$1"
    shift
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "[$ts] [$level] [$JURISDICTION] $*" | tee -a "$LOG_FILE"
}

log_info()  { log "INFO"  "$@"; }
log_warn()  { log "WARN"  "$@"; }
log_error() { log "ERROR" "$@"; }

# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------
send_alert() {
    local severity="$1"
    local message="$2"

    log_warn "ALERT [$severity]: $message"

    if [[ -n "$ALERT_WEBHOOK" ]]; then
        curl -s -X POST "$ALERT_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"severity\":\"$severity\",\"message\":\"[Wasabi Backup] [$JURISDICTION] $message\"}" \
            >/dev/null 2>&1 || true
    fi

    if [[ -n "$ALERT_EMAIL" ]] && command -v mail &>/dev/null; then
        echo "$message" | mail -s "[Wasabi Alert][$severity][$JURISDICTION]" "$ALERT_EMAIL" || true
    fi
}

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
check_prerequisites() {
    local missing=0

    for var in JURISDICTION WASABI_REGION WASABI_ENDPOINT WASABI_BUCKET \
               WASABI_ACCESS_KEY WASABI_SECRET_KEY; do
        if [[ -z "${!var:-}" ]]; then
            log_error "Required environment variable not set: $var"
            missing=$((missing + 1))
        fi
    done

    if ! command -v aws &>/dev/null; then
        log_error "AWS CLI v2 not found (required for Wasabi S3-compatible API)"
        missing=$((missing + 1))
    fi

    if ! command -v openssl &>/dev/null; then
        log_error "openssl not found (required for client-side encryption before upload)"
        missing=$((missing + 1))
    fi

    if [[ ! -f "$ENCRYPTION_KEY_FILE" ]]; then
        log_error "ENCRYPTION_KEY_FILE not found: $ENCRYPTION_KEY_FILE (required for client-side encryption before upload)"
        missing=$((missing + 1))
    fi

    if [[ $missing -gt 0 ]]; then
        log_error "$missing prerequisite(s) missing — aborting"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Client-side encryption before upload
# ---------------------------------------------------------------------------
# --sse AES256 below is Wasabi-managed server-side encryption: Wasabi holds
# the key, so it protects data at rest against a disk walking off the rack
# but not against a compromised Wasabi credential/admin, and it does not
# satisfy "encrypted before it leaves our network" clauses some jurisdiction
# DPAs require. This wraps the archive in a second, client-side AES-256
# layer using a key that never leaves our infrastructure, using the same
# openssl invocation as backup_manager.sh so the two layers stay consistent.
encrypt_for_upload() {
    local plain_file="$1"
    local enc_file="$2"

    openssl enc -aes-256-cbc \
        -pass file:"$ENCRYPTION_KEY_FILE" \
        -pbkdf2 \
        -in "$plain_file" \
        -out "$enc_file"
}

# ---------------------------------------------------------------------------
# CRITICAL: Enforce jurisdiction-to-region mapping before any upload.
# A misconfigured WASABI_REGION environment variable could silently route
# player data to the wrong country.  This check is a non-optional guardrail.
# ---------------------------------------------------------------------------
check_region() {
    log_info "Checking region compliance for jurisdiction: $JURISDICTION"

    local approved_primary="${APPROVED_PRIMARY_REGIONS[$JURISDICTION]:-}"

    if [[ -z "$approved_primary" ]]; then
        log_error "Unknown jurisdiction: $JURISDICTION"
        log_error "Registered jurisdictions: ${!APPROVED_PRIMARY_REGIONS[*]}"
        send_alert "critical" "Unknown jurisdiction '$JURISDICTION' — refusing to upload"
        exit 1
    fi

    if [[ "$WASABI_REGION" != "$approved_primary" ]]; then
        # Check if secondary is configured and matches
        local approved_secondary="${APPROVED_SECONDARY_REGIONS[$JURISDICTION]:-}"
        if [[ -z "$approved_secondary" || "$WASABI_REGION" != "$approved_secondary" ]]; then
            log_error "JURISDICTION VIOLATION BLOCKED"
            log_error "  Jurisdiction:     $JURISDICTION"
            log_error "  Configured region: $WASABI_REGION"
            log_error "  Approved primary:  $approved_primary"
            [[ -n "$approved_secondary" ]] && log_error "  Approved secondary: $approved_secondary"
            log_error "  This upload would have violated data residency law."
            send_alert "critical" \
                "Region mismatch for $JURISDICTION: configured=$WASABI_REGION approved=$approved_primary — UPLOAD BLOCKED"
            exit 2
        fi
        log_info "Region $WASABI_REGION matches approved secondary region for $JURISDICTION"
    else
        log_info "Region $WASABI_REGION matches approved primary region for $JURISDICTION — OK"
    fi
}

# ---------------------------------------------------------------------------
# Configure AWS CLI to target Wasabi endpoint
# ---------------------------------------------------------------------------
aws_wasabi() {
    AWS_ACCESS_KEY_ID="$WASABI_ACCESS_KEY" \
    AWS_SECRET_ACCESS_KEY="$WASABI_SECRET_KEY" \
    aws --endpoint-url "$WASABI_ENDPOINT" \
        --region "$WASABI_REGION" \
        "$@"
}

aws_wasabi_secondary() {
    AWS_ACCESS_KEY_ID="$WASABI_ACCESS_KEY" \
    AWS_SECRET_ACCESS_KEY="$WASABI_SECRET_KEY" \
    aws --endpoint-url "$SECONDARY_ENDPOINT" \
        --region "$SECONDARY_REGION" \
        "$@"
}

# ---------------------------------------------------------------------------
# Ensure bucket exists with correct configuration
# ---------------------------------------------------------------------------
ensure_bucket() {
    local bucket="$1"
    local endpoint="$2"
    local region="$3"

    log_info "Ensuring bucket exists: $bucket ($region, $endpoint)"

    if ! AWS_ACCESS_KEY_ID="$WASABI_ACCESS_KEY" \
         AWS_SECRET_ACCESS_KEY="$WASABI_SECRET_KEY" \
         aws --endpoint-url "$endpoint" --region "$region" \
         s3api head-bucket --bucket "$bucket" 2>/dev/null; then

        log_info "Creating bucket: $bucket"
        AWS_ACCESS_KEY_ID="$WASABI_ACCESS_KEY" \
        AWS_SECRET_ACCESS_KEY="$WASABI_SECRET_KEY" \
        aws --endpoint-url "$endpoint" --region "$region" \
            s3api create-bucket \
            --bucket "$bucket" \
            --region "$region" \
            --create-bucket-configuration LocationConstraint="$region" \
            2>> "$LOG_FILE"

        log_info "Enabling versioning on $bucket"
        AWS_ACCESS_KEY_ID="$WASABI_ACCESS_KEY" \
        AWS_SECRET_ACCESS_KEY="$WASABI_SECRET_KEY" \
        aws --endpoint-url "$endpoint" --region "$region" \
            s3api put-bucket-versioning \
            --bucket "$bucket" \
            --versioning-configuration Status=Enabled \
            2>> "$LOG_FILE"

        log_info "Bucket $bucket created and versioning enabled"
    else
        log_info "Bucket $bucket already exists"
    fi
}

# ---------------------------------------------------------------------------
# Apply lifecycle rules to enforce retention policy
# ---------------------------------------------------------------------------
lifecycle_apply() {
    check_prerequisites
    check_region

    # Retention days are derived from wasabi-jurisdiction-config.py.
    # Hardcoded here for shell independence; keep both in sync.
    declare -A RETENTION_DAYS=(
        [mga]=2555
        [ukgc]=1825
        [dge_nj]=2555
        [pgcb_pa]=2555
        [mgcb_mi]=1825
        [agco_on]=2555
        [pagcor]=1825
    )

    local retention="${RETENTION_DAYS[$JURISDICTION]:-2555}"

    log_info "Applying lifecycle rules to $WASABI_BUCKET"
    log_info "  Retention: $retention days (legal basis: see wasabi-jurisdiction-config.py)"

    # Wasabi Object Lock is not available on all plans.
    # Use lifecycle expiration as the retention mechanism.
    # For regulated archives, also enable MFA delete via Wasabi console.
    local lifecycle_config
    lifecycle_config=$(cat <<LIFECYCLE
{
    "Rules": [
        {
            "ID": "compliance-retention-${JURISDICTION}",
            "Status": "Enabled",
            "Filter": {
                "Prefix": ""
            },
            "Expiration": {
                "Days": ${retention}
            },
            "NoncurrentVersionExpiration": {
                "NoncurrentDays": ${retention}
            }
        }
    ]
}
LIFECYCLE
)

    aws_wasabi s3api put-bucket-lifecycle-configuration \
        --bucket "$WASABI_BUCKET" \
        --lifecycle-configuration "$lifecycle_config" \
        2>> "$LOG_FILE"

    log_info "Lifecycle configuration applied: objects expire after $retention days"

    # Apply to secondary bucket if configured
    if [[ -n "$SECONDARY_BUCKET" && -n "$SECONDARY_ENDPOINT" ]]; then
        log_info "Applying lifecycle rules to secondary bucket: $SECONDARY_BUCKET"
        AWS_ACCESS_KEY_ID="$WASABI_ACCESS_KEY" \
        AWS_SECRET_ACCESS_KEY="$WASABI_SECRET_KEY" \
        aws --endpoint-url "$SECONDARY_ENDPOINT" --region "$SECONDARY_REGION" \
            s3api put-bucket-lifecycle-configuration \
            --bucket "$SECONDARY_BUCKET" \
            --lifecycle-configuration "$lifecycle_config" \
            2>> "$LOG_FILE"
        log_info "Secondary lifecycle configuration applied"
    fi
}

# ---------------------------------------------------------------------------
# Upload a single file to Wasabi
# ---------------------------------------------------------------------------
cmd_upload() {
    local source_file="${1:-}"
    local object_suffix="${2:-}"

    if [[ -z "$source_file" ]]; then
        log_error "Usage: $0 upload <file> [<object-key-suffix>]"
        exit 1
    fi

    if [[ ! -f "$source_file" ]]; then
        log_error "Source file not found: $source_file"
        exit 1
    fi

    check_prerequisites
    check_region
    ensure_bucket "$WASABI_BUCKET" "$WASABI_ENDPOINT" "$WASABI_REGION"

    local filename
    filename=$(basename "$source_file")
    local year_month
    year_month=$(date -u +"%Y/%m")
    local object_key="${JURISDICTION}/${year_month}/${filename}"
    [[ -n "$object_suffix" ]] && object_key="${JURISDICTION}/${year_month}/${object_suffix}/${filename}"
    # The uploaded bytes are the client-side-encrypted form, not the source
    # file's own bytes — mark that on the key, matching backup_manager.sh's
    # *.enc convention.
    [[ "$object_key" != *.enc ]] && object_key="${object_key}.enc"

    local enc_file
    enc_file=$(mktemp "${TMPDIR:-/tmp}/wasabi-upload-XXXXXX.enc")
    trap 'rm -f "$enc_file"' RETURN
    encrypt_for_upload "$source_file" "$enc_file"

    local file_size
    file_size=$(stat -c%s "$enc_file" 2>/dev/null || stat -f%z "$enc_file" 2>/dev/null || echo 0)
    local checksum
    checksum=$(sha256sum "$enc_file" | awk '{print $1}')

    log_info "Uploading: $source_file → s3://$WASABI_BUCKET/$object_key (client-side AES-256 encrypted)"
    log_info "  Size: $file_size bytes | SHA256 (encrypted): $checksum"

    # Metadata is stored alongside the object for audit evidence
    aws_wasabi s3 cp "$enc_file" "s3://${WASABI_BUCKET}/${object_key}" \
        --sse AES256 \
        --metadata "jurisdiction=${JURISDICTION},sha256=${checksum},source-host=$(hostname -f 2>/dev/null || echo unknown),upload-timestamp=${DATE_STAMP},client-encrypted=aes-256-cbc" \
        --storage-class STANDARD \
        2>> "$LOG_FILE"

    log_info "Upload complete: s3://$WASABI_BUCKET/$object_key"

    # Cross-region replica if permitted for this jurisdiction
    if [[ -n "$SECONDARY_BUCKET" && -n "$SECONDARY_ENDPOINT" && -n "$SECONDARY_REGION" ]]; then
        local approved_secondary="${APPROVED_SECONDARY_REGIONS[$JURISDICTION]:-}"
        if [[ -n "$approved_secondary" ]]; then
            log_info "Cross-region replica → s3://$SECONDARY_BUCKET/$object_key ($SECONDARY_REGION)"
            ensure_bucket "$SECONDARY_BUCKET" "$SECONDARY_ENDPOINT" "$SECONDARY_REGION"
            aws_wasabi_secondary s3 cp "$enc_file" "s3://${SECONDARY_BUCKET}/${object_key}" \
                --sse AES256 \
                --metadata "jurisdiction=${JURISDICTION},sha256=${checksum},source-host=$(hostname -f 2>/dev/null || echo unknown),upload-timestamp=${DATE_STAMP},replica-of=s3://${WASABI_BUCKET}/${object_key},client-encrypted=aes-256-cbc" \
                --storage-class STANDARD \
                2>> "$LOG_FILE"
            log_info "Secondary replica complete"
        else
            log_warn "Cross-region replica configured but not permitted for $JURISDICTION — skipping"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Sync a local directory of backup archives to Wasabi
# ---------------------------------------------------------------------------
cmd_sync() {
    local local_dir="${1:-}"

    if [[ -z "$local_dir" || ! -d "$local_dir" ]]; then
        log_error "Usage: $0 sync <local-dir>"
        exit 1
    fi

    check_prerequisites
    check_region
    ensure_bucket "$WASABI_BUCKET" "$WASABI_ENDPOINT" "$WASABI_REGION"

    local year_month
    year_month=$(date -u +"%Y/%m")
    local s3_prefix="${JURISDICTION}/${year_month}/"

    local staging_dir
    staging_dir=$(mktemp -d "${TMPDIR:-/tmp}/wasabi-sync-XXXXXX")
    trap 'rm -rf "$staging_dir"' RETURN

    log_info "Encrypting $local_dir → $staging_dir before sync (client-side AES-256)"
    local file rel_path dest
    while IFS= read -r -d '' file; do
        rel_path="${file#"$local_dir"/}"
        dest="${staging_dir}/${rel_path}.enc"
        mkdir -p "$(dirname "$dest")"
        encrypt_for_upload "$file" "$dest"
    done < <(find "$local_dir" -type f ! -name "*.tmp" ! -name "*.lock" -print0)

    log_info "Syncing $staging_dir → s3://$WASABI_BUCKET/$s3_prefix (client-side AES-256 encrypted)"

    aws_wasabi s3 sync "$staging_dir" "s3://${WASABI_BUCKET}/${s3_prefix}" \
        --sse AES256 \
        --storage-class STANDARD \
        2>> "$LOG_FILE"

    log_info "Sync complete"
}

# ---------------------------------------------------------------------------
# List objects in the bucket
# ---------------------------------------------------------------------------
cmd_list() {
    check_prerequisites
    check_region

    log_info "Listing s3://$WASABI_BUCKET/$JURISDICTION/"
    aws_wasabi s3 ls "s3://${WASABI_BUCKET}/${JURISDICTION}/" --recursive \
        2>> "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Verify an uploaded object — download and check SHA256
# ---------------------------------------------------------------------------
cmd_verify() {
    local object_key="${1:-}"

    if [[ -z "$object_key" ]]; then
        log_error "Usage: $0 verify <object-key>"
        exit 1
    fi

    check_prerequisites
    check_region

    log_info "Verifying s3://$WASABI_BUCKET/$object_key"

    local temp_file
    temp_file=$(mktemp /tmp/wasabi-verify-XXXXXX)

    aws_wasabi s3 cp "s3://${WASABI_BUCKET}/${object_key}" "$temp_file" \
        2>> "$LOG_FILE"

    # Retrieve expected checksum from object metadata
    local expected_sha256
    expected_sha256=$(aws_wasabi s3api head-object \
        --bucket "$WASABI_BUCKET" \
        --key "$object_key" \
        --query 'Metadata.sha256' \
        --output text 2>/dev/null || echo "")

    local actual_sha256
    actual_sha256=$(sha256sum "$temp_file" | awk '{print $1}')

    rm -f "$temp_file"

    if [[ -n "$expected_sha256" && "$expected_sha256" != "None" ]]; then
        if [[ "$actual_sha256" == "$expected_sha256" ]]; then
            log_info "PASS: SHA256 matches: $actual_sha256"
        else
            log_error "FAIL: SHA256 mismatch"
            log_error "  Expected: $expected_sha256"
            log_error "  Actual:   $actual_sha256"
            send_alert "critical" "Backup integrity check FAILED for $object_key ($JURISDICTION)"
            exit 1
        fi
    else
        log_warn "No SHA256 metadata found on object — file integrity cannot be verified from metadata"
        log_info "Downloaded SHA256: $actual_sha256"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local command="${1:-help}"
    shift || true

    case "$command" in
        upload)
            cmd_upload "$@"
            ;;
        sync)
            cmd_sync "$@"
            ;;
        list)
            cmd_list
            ;;
        verify)
            cmd_verify "$@"
            ;;
        lifecycle-apply)
            lifecycle_apply
            ;;
        check-region)
            check_prerequisites
            check_region
            log_info "Region check passed"
            ;;
        help|*)
            cat <<USAGE
Usage: $0 <command> [args]

Commands:
  upload <file> [suffix]   Upload a backup archive to jurisdiction-correct Wasabi bucket
  sync <dir>               Sync a directory of archives to Wasabi
  list                     List objects in the configured bucket
  verify <object-key>      Download and SHA256-verify an uploaded object
  lifecycle-apply          Apply regulatory retention lifecycle rules to the bucket
  check-region             Validate WASABI_REGION matches jurisdiction requirements

Required environment variables:
  JURISDICTION        mga | ukgc | dge_nj | pgcb_pa | mgcb_mi | agco_on | pagcor
  WASABI_ACCESS_KEY   Wasabi IAM key
  WASABI_SECRET_KEY   Wasabi IAM secret
  WASABI_REGION       Must match approved region for JURISDICTION
  WASABI_ENDPOINT     https://s3.<region>.wasabisys.com
  WASABI_BUCKET       Bucket name (e.g. acmetocasino-mga-backup-prod-db)

Optional:
  SECONDARY_REGION    DR region (only for jurisdictions where replication is allowed)
  SECONDARY_ENDPOINT  https://s3.<secondary-region>.wasabisys.com
  SECONDARY_BUCKET    Bucket name in secondary region

Load per-jurisdiction config from:
  /etc/igaming/wasabi-<jurisdiction>.env
USAGE
            ;;
    esac
}

main "$@"
