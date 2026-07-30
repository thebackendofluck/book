#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# C-01/apply.sh — Add authentication to /pam/players endpoint
# Default: dry-run. Pass --apply to make real changes.
#
# Usage:
#   ./apply.sh           # dry-run
#   ./apply.sh --apply   # execute

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=true
CASINO_HOST="203.0.113.1"
CASINO_USER="root"
REMOTE_FILE="/opt/new-platform/app/pam/router.py"
CONTAINER_NAME="new-casino-api"
BACKUP_SUFFIX=".bak.$(date +%Y%m%d%H%M%S)"

# Colors
if [[ -t 1 ]]; then
  GREEN='\033[0;32m' YELLOW='\033[1;33m' RED='\033[0;31m' RESET='\033[0m'
else
  GREEN='' YELLOW='' RED='' RESET=''
fi
log()  { echo -e "[$(date '+%H:%M:%S')] $*"; }
info() { log "${GREEN}[INFO]${RESET} $*"; }
warn() { log "${YELLOW}[WARN]${RESET} $*"; }
err()  { log "${RED}[ERR]${RESET}  $*" >&2; }
dry()  { log "${YELLOW}[DRY] ${RESET} Would: $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)   DRY_RUN=false; shift ;;
    --dry-run) DRY_RUN=true;  shift ;;
    *) err "Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ "$DRY_RUN" == "true" ]]; then
  warn "DRY-RUN mode — no changes will be made. Pass --apply to execute."
fi

# --------------------------------------------------------------------------
# Step 1: Verify SSH connectivity
info "Step 1: Verifying SSH connectivity to ${CASINO_HOST}..."
if [[ "$DRY_RUN" == "false" ]]; then
  if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "${CASINO_USER}@${CASINO_HOST}" "echo ok" > /dev/null 2>&1; then
    err "Cannot connect to ${CASINO_HOST} via SSH. Aborting."
    exit 1
  fi
  info "SSH connection OK"
else
  dry "ssh ${CASINO_USER}@${CASINO_HOST} 'echo ok'"
fi

# --------------------------------------------------------------------------
# Step 2: Check that the remote file exists and show current state
info "Step 2: Inspecting remote file ${REMOTE_FILE}..."
if [[ "$DRY_RUN" == "false" ]]; then
  CURRENT_CONTENT=$(ssh "${CASINO_USER}@${CASINO_HOST}" "cat '${REMOTE_FILE}'" 2>/dev/null) || {
    err "Cannot read ${REMOTE_FILE} on remote host. Aborting."
    exit 1
  }

  if echo "$CURRENT_CONTENT" | grep -q "Depends(get_ops_user)"; then
    if echo "$CURRENT_CONTENT" | grep -A5 '@router.get("/pam/players")' | grep -q "Depends(get_ops_user)"; then
      info "Endpoint already has authentication. Nothing to do."
      exit 0
    fi
  fi

  # Check that get_ops_user is importable in this file
  if ! echo "$CURRENT_CONTENT" | grep -qE "(from|import).*get_ops_user"; then
    warn "get_ops_user not imported in router.py. Will need to add import."
    warn "Verify the correct import path with: grep -r 'def get_ops_user' /opt/new-platform/"
  fi

  info "Current endpoint definition:"
  echo "$CURRENT_CONTENT" | grep -A5 'pam/players' || true
else
  dry "cat ${REMOTE_FILE} | grep -A5 'pam/players'"
  echo ""
  echo "  Expected to find:"
  echo "  @router.get(\"/pam/players\")"
  echo "  def list_players(limit: ..., offset: ...):"
  echo "      return service.list_players(...)"
fi

# --------------------------------------------------------------------------
# Step 3: Backup remote file
info "Step 3: Backing up ${REMOTE_FILE}..."
BACKUP_PATH="${REMOTE_FILE}${BACKUP_SUFFIX}"
if [[ "$DRY_RUN" == "false" ]]; then
  ssh "${CASINO_USER}@${CASINO_HOST}" "cp '${REMOTE_FILE}' '${BACKUP_PATH}'"
  info "Backup created: ${BACKUP_PATH}"
  # Store backup path for rollback
  echo "$BACKUP_PATH" > "${SCRIPT_DIR}/.last_backup_path"
else
  dry "cp ${REMOTE_FILE} ${BACKUP_PATH}"
  echo "$BACKUP_PATH" > "${SCRIPT_DIR}/.last_backup_path.dryrun"
fi

# --------------------------------------------------------------------------
# Step 4: Apply the patch
info "Step 4: Applying authentication patch..."

# The patch uses Python to safely manipulate the source — avoids sed fragility
PATCH_SCRIPT='
import re, sys

with open(sys.argv[1], "r") as f:
    content = f.read()

# Ensure Depends is imported
if "from fastapi import" in content and "Depends" not in content.split("from fastapi import")[1].split("\n")[0]:
    content = content.replace(
        "from fastapi import",
        "from fastapi import Depends, ",
        1
    )
elif "Depends" not in content:
    # Add import at top of fastapi imports block
    content = re.sub(
        r"(from fastapi import [^\n]+)",
        r"\1, Depends",
        content,
        count=1
    )

# Ensure get_ops_user is imported — adjust path as needed for the actual project
if "get_ops_user" not in content:
    # Insert import after last "from app." import line
    content = re.sub(
        r"(from app\.[^\n]+\n)(?!from app\.)",
        r"\1from app.auth.dependencies import get_ops_user\n",
        content,
        count=1
    )

# Add Depends(get_ops_user) to the endpoint
pattern = r"""(@router\.get\("/pam/players"[^\)]*\)\s*\ndef list_players\s*\()([^)]*?)(\):)"""

def add_auth(m):
    params = m.group(2)
    if "get_ops_user" in params:
        return m.group(0)  # already patched
    # Append the dependency parameter
    if params.strip().endswith(",") or params.strip() == "":
        new_params = params + "\n    current_user=Depends(get_ops_user),"
    else:
        new_params = params + ",\n    current_user=Depends(get_ops_user),"
    return m.group(1) + new_params + m.group(3)

new_content = re.sub(pattern, add_auth, content, flags=re.DOTALL)

if new_content == content:
    print("WARNING: pattern not matched — verify router.py structure manually", file=sys.stderr)
    sys.exit(1)

with open(sys.argv[1], "w") as f:
    f.write(new_content)

print("Patch applied successfully")
'

if [[ "$DRY_RUN" == "false" ]]; then
  # Upload the patch script and run it remotely
  REMOTE_PATCH_SCRIPT="/tmp/c01_patch_$$.py"
  echo "$PATCH_SCRIPT" | ssh "${CASINO_USER}@${CASINO_HOST}" "cat > '${REMOTE_PATCH_SCRIPT}'"
  if ! ssh "${CASINO_USER}@${CASINO_HOST}" "python3 '${REMOTE_PATCH_SCRIPT}' '${REMOTE_FILE}' && rm -f '${REMOTE_PATCH_SCRIPT}'"; then
    err "Patch script failed. Restoring backup."
    ssh "${CASINO_USER}@${CASINO_HOST}" "cp '${BACKUP_PATH}' '${REMOTE_FILE}'"
    exit 1
  fi
  info "Patch applied. Showing diff:"
  ssh "${CASINO_USER}@${CASINO_HOST}" "diff '${BACKUP_PATH}' '${REMOTE_FILE}'" || true
else
  dry "Apply Python patch to ${REMOTE_FILE}"
  echo ""
  echo "  Patch will add 'current_user=Depends(get_ops_user)' to list_players()"
  echo "  and ensure 'Depends' and 'get_ops_user' are imported."
fi

# --------------------------------------------------------------------------
# Step 5: Reload / restart container
info "Step 5: Reloading FastAPI container ${CONTAINER_NAME}..."
if [[ "$DRY_RUN" == "false" ]]; then
  # Try graceful reload first (SIGHUP), fall back to restart
  COMPOSE_FILE="/opt/new-platform/docker-compose.yml"
  if ssh "${CASINO_USER}@${CASINO_HOST}" "docker compose -f '${COMPOSE_FILE}' ps '${CONTAINER_NAME}' | grep -q running" 2>/dev/null; then
    info "Sending SIGHUP for graceful reload..."
    ssh "${CASINO_USER}@${CASINO_HOST}" "docker kill --signal=SIGHUP '${CONTAINER_NAME}'" 2>/dev/null || {
      warn "SIGHUP failed, falling back to restart..."
      ssh "${CASINO_USER}@${CASINO_HOST}" "docker compose -f '${COMPOSE_FILE}' restart '${CONTAINER_NAME}'"
    }
  else
    warn "Container not found via compose — trying direct docker restart..."
    ssh "${CASINO_USER}@${CASINO_HOST}" "docker restart '${CONTAINER_NAME}'"
  fi

  # Wait for container to be healthy
  info "Waiting for container to become healthy (max 30s)..."
  for i in $(seq 1 6); do
    sleep 5
    STATUS=$(ssh "${CASINO_USER}@${CASINO_HOST}" "docker inspect --format='{{.State.Health.Status}}' '${CONTAINER_NAME}' 2>/dev/null || echo 'unknown'")
    if [[ "$STATUS" == "healthy" || "$STATUS" == "running" ]]; then
      info "Container is ${STATUS}. OK."
      break
    fi
    if [[ $i -eq 6 ]]; then
      err "Container not healthy after 30s. Check manually: docker ps -a"
      exit 1
    fi
    warn "Still waiting... (${STATUS})"
  done
else
  dry "docker compose -f /opt/new-platform/docker-compose.yml restart ${CONTAINER_NAME}"
  dry "Wait for container healthy status (max 30s)"
fi

info "C-01 apply complete."
if [[ "$DRY_RUN" == "true" ]]; then
  warn "This was a dry-run. Run with --apply to execute."
fi
