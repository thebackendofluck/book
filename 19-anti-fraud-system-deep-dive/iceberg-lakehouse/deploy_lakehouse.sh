#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Deploy Iceberg Fraud Lakehouse on ops-host (10.0.0.11)
# =============================================================================
#
# Usage:
#   bash deploy_lakehouse_ops-host.sh [--skip-copy] [--skip-deploy] [--test-only]
#
# Steps:
#   1. Copy lakehouse files to ~/Projetos/fraud-iceberg-lakehouse/ on ops-host
#   2. Start docker-compose-lakehouse-ops-host.yml
#   3. Wait for all services to become healthy
#   4. Initialise Iceberg tables (runs iceberg-init container)
#   5. Create Kafka topic fraud.raw.events
#   6. Seed with backfill from existing Elasticsearch data
#   7. Run a validation smoke-test
#
# Requirements (local):
#   - SSH key at ~/.ssh/id_ed25519 with access to ops-host
#   - MINIO_ROOT_PASSWORD and ICEBERG_PG_PASSWORD exported in the environment
# =============================================================================

set -euo pipefail

# Fail before anything is copied or started, rather than halfway through a
# deployment, if the credentials the compose file demands are missing. `set -u`
# plus the :? form turns an unset variable into an immediate, named error.
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD must be set (MinIO root password / S3 secret key)}"
: "${ICEBERG_PG_PASSWORD:?ICEBERG_PG_PASSWORD must be set (Iceberg catalog PostgreSQL password)}"

REMOTE_HOST="admin@ops-server"
# Remote layout is configurable: nothing here assumes a particular operator account.
REMOTE_USER="${REMOTE_USER:-operator}"
REMOTE_HOME="${REMOTE_HOME:-/home/${REMOTE_USER}}"
REMOTE_DIR="${REMOTE_HOME}/Projetos/fraud-iceberg-lakehouse"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# SSH_OPTS is intentionally left unquoted when passed to ssh/scp so that the
# individual flags are parsed as separate arguments. shellcheck disable=SC2086
# suppresses the warning at each call site.
SSH_OPTS="-o PasswordAuthentication=no -i ${HOME}/.ssh/id_ed25519 -o ConnectTimeout=15"

SKIP_COPY=false
SKIP_DEPLOY=false
TEST_ONLY=false

for arg in "$@"; do
  case $arg in
    --skip-copy)   SKIP_COPY=true ;;
    --skip-deploy) SKIP_DEPLOY=true ;;
    --test-only)   TEST_ONLY=true; SKIP_COPY=true; SKIP_DEPLOY=true ;;
  esac
done

log()  { echo "[$(date +%H:%M:%S)] $*"; }
ok()   { echo "[$(date +%H:%M:%S)] OK: $*"; }
fail() { echo "[$(date +%H:%M:%S)] FAIL: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Step 1: Copy files to ops-host
# ---------------------------------------------------------------------------
if [[ "$SKIP_COPY" == "false" ]]; then
  log "Creating remote directory ${REMOTE_DIR}..."
  # shellcheck disable=SC2029,SC2086
  ssh $SSH_OPTS "${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/scripts"

  log "Copying lakehouse files..."
  # shellcheck disable=SC2086
  scp $SSH_OPTS \
    "${SCRIPT_DIR}/docker-compose-lakehouse-ops-host.yml" \
    "${SCRIPT_DIR}/iceberg_fraud_lakehouse.py" \
    "${SCRIPT_DIR}/spark_fraud_batch.py" \
    "${SCRIPT_DIR}/flink_fraud_realtime.py" \
    "${SCRIPT_DIR}/fraud_pipeline_orchestrator.py" \
    "${SCRIPT_DIR}/iceberg_fraud_dashboard_data.py" \
    "${SCRIPT_DIR}/es_to_kafka_bridge.py" \
    "${REMOTE_HOST}:${REMOTE_DIR}/scripts/"

  ok "Files copied to ${REMOTE_HOST}:${REMOTE_DIR}/scripts/"
fi

# ---------------------------------------------------------------------------
# Step 2: Deploy docker-compose
# ---------------------------------------------------------------------------
if [[ "$SKIP_DEPLOY" == "false" ]]; then
  log "Starting Iceberg lakehouse stack on ops-host..."
  # SC2086: SSH_OPTS intentionally unquoted (multi-word flags)
  # SC2087: heredoc without quoted delimiter — local variables expand intentionally
  # shellcheck disable=SC2086,SC2087
  ssh $SSH_OPTS "${REMOTE_HOST}" bash <<EOF
set -euo pipefail
cd ${REMOTE_DIR}

# The compose file resolves both of these with \${VAR:?...}, so they have to
# exist in the remote shell. They travel on ssh's stdin as part of this heredoc,
# not in argv, so they never appear in the remote host's process list.
export MINIO_ROOT_PASSWORD='${MINIO_ROOT_PASSWORD}'
export ICEBERG_PG_PASSWORD='${ICEBERG_PG_PASSWORD}'

# Pull images before starting to give a clear error if any are unavailable
docker compose -f docker-compose-lakehouse-ops-host.yml pull --quiet 2>&1 || true

# Start the stack (detached)
docker compose -f docker-compose-lakehouse-ops-host.yml up -d --remove-orphans

echo "Stack started. Waiting for services to become healthy..."
EOF
  ok "Stack started"
fi

# ---------------------------------------------------------------------------
# Step 3: Wait for services
# ---------------------------------------------------------------------------
if [[ "$TEST_ONLY" == "false" ]]; then
  log "Waiting for MinIO to be healthy (up to 120s)..."
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "${REMOTE_HOST}" bash <<'EOF'
for i in $(seq 1 24); do
  if curl -sf http://localhost:9010/minio/health/live > /dev/null 2>&1; then
    echo "MinIO ready after $((i*5))s"
    break
  fi
  echo "  waiting for MinIO... ${i}/24"
  sleep 5
done
EOF

  log "Waiting for Iceberg REST catalog (up to 120s)..."
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "${REMOTE_HOST}" bash <<'EOF'
for i in $(seq 1 24); do
  if curl -sf http://localhost:8181/v1/config > /dev/null 2>&1; then
    echo "Iceberg REST ready after $((i*5))s"
    break
  fi
  echo "  waiting for Iceberg REST... ${i}/24"
  sleep 5
done
EOF
  ok "Core services healthy"
fi

# ---------------------------------------------------------------------------
# Step 4: Verify Iceberg table initialisation
# ---------------------------------------------------------------------------
if [[ "$TEST_ONLY" == "false" ]]; then
  log "Checking Iceberg table initialisation..."
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "${REMOTE_HOST}" bash <<'EOF'
set -euo pipefail
INIT_STATUS=$(docker inspect iceberg-init --format '{{.State.Status}}' 2>/dev/null || echo "not found")
if [[ "$INIT_STATUS" == "exited" ]]; then
  EXIT_CODE=$(docker inspect iceberg-init --format '{{.State.ExitCode}}')
  if [[ "$EXIT_CODE" == "0" ]]; then
    echo "iceberg-init completed successfully"
    docker logs iceberg-init 2>&1 | tail -5
  else
    echo "iceberg-init failed (exit code ${EXIT_CODE})"
    docker logs iceberg-init 2>&1 | tail -20
    exit 1
  fi
else
  echo "iceberg-init status: ${INIT_STATUS}"
  docker logs iceberg-init 2>&1 | tail -10
fi
EOF
  ok "Iceberg tables initialised"
fi

# ---------------------------------------------------------------------------
# Step 5: Create fraud.raw.events Kafka topic
# ---------------------------------------------------------------------------
if [[ "$TEST_ONLY" == "false" ]]; then
  log "Creating Kafka topic fraud.raw.events..."
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "${REMOTE_HOST}" bash <<'EOF'
set -euo pipefail
docker exec fraud-detection-kafka \
  kafka-topics \
    --bootstrap-server localhost:9092 \
    --create \
    --if-not-exists \
    --topic fraud.raw.events \
    --partitions 4 \
    --replication-factor 1 \
    --config retention.ms=604800000 \
    --config compression.type=snappy

echo "Topic list:"
docker exec fraud-detection-kafka kafka-topics \
  --bootstrap-server localhost:9092 --list 2>/dev/null \
  | grep -E 'fraud|casino|game'
EOF
  ok "Kafka topic fraud.raw.events ready"
fi

# ---------------------------------------------------------------------------
# Step 6: Backfill existing ES events into Kafka
# ---------------------------------------------------------------------------
if [[ "$TEST_ONLY" == "false" ]]; then
  log "Installing bridge dependencies and running ES-to-Kafka backfill..."
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "${REMOTE_HOST}" bash <<'EOF'
set -euo pipefail
if [[ ! -d ${REMOTE_HOME}/.venvs/fraud-bridge ]]; then
  python3 -m venv ${REMOTE_HOME}/.venvs/fraud-bridge
fi
source ${REMOTE_HOME}/.venvs/fraud-bridge/bin/activate
pip install --quiet 'elasticsearch>=8,<9' 'kafka-python>=2.0' 2>&1 | tail -3

echo "Running backfill (last 30 days from Elasticsearch)..."
BACKFILL_DATE=$(date -d "30 days ago" +%Y-%m-%d 2>/dev/null || date -v -30d +%Y-%m-%d)
python3 ${REMOTE_DIR}/scripts/es_to_kafka_bridge.py \
  --es-url http://localhost:9200 \
  --es-index "casino-events-*" \
  --kafka-bootstrap localhost:9092 \
  --kafka-topic fraud.raw.events \
  --state-file ${REMOTE_DIR}/bridge-state.json \
  --backfill-from "${BACKFILL_DATE}" \
  --once

echo "Backfill complete"
deactivate
EOF
  ok "ES-to-Kafka backfill complete"
fi

# ---------------------------------------------------------------------------
# Step 7: Install cron job for continuous bridge
# ---------------------------------------------------------------------------
if [[ "$TEST_ONLY" == "false" ]]; then
  log "Installing cron job for continuous ES-to-Kafka bridge (every 60s)..."
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "${REMOTE_HOST}" bash <<'EOF'
CRON_LINE="* * * * * ${REMOTE_HOME}/.venvs/fraud-bridge/bin/python3 ${REMOTE_DIR}/scripts/es_to_kafka_bridge.py --es-url http://localhost:9200 --kafka-bootstrap localhost:9092 --kafka-topic fraud.raw.events --state-file ${REMOTE_DIR}/bridge-state.json --once >> ${REMOTE_DIR}/bridge.log 2>&1"

if crontab -l 2>/dev/null | grep -q "es_to_kafka_bridge"; then
  echo "Cron job already installed"
else
  (crontab -l 2>/dev/null || true; echo "${CRON_LINE}") | crontab -
  echo "Cron job installed"
fi

crontab -l | grep es_to_kafka_bridge
EOF
  ok "Cron bridge installed"
fi

# ---------------------------------------------------------------------------
# Step 8: Smoke tests
# ---------------------------------------------------------------------------
log "Running smoke tests..."
# shellcheck disable=SC2086
ssh $SSH_OPTS "${REMOTE_HOST}" bash <<'EOF'
set -euo pipefail

echo "=== MinIO Health ==="
curl -sf http://localhost:9010/minio/health/live && echo "MinIO: healthy" || echo "MinIO: FAILED"

echo "=== Iceberg REST Config ==="
curl -sf http://localhost:8181/v1/config \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("Iceberg REST: configured")' \
  || echo "Iceberg REST: FAILED"

echo "=== Iceberg Namespaces ==="
curl -sf http://localhost:8181/v1/namespaces \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("Namespaces:", d.get("namespaces",[]))' \
  || echo "No namespaces yet"

echo "=== Iceberg Tables ==="
curl -sf "http://localhost:8181/v1/namespaces/fraud_analytics/tables" 2>/dev/null \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); [print(" -", t) for t in d.get("identifiers",[])]' \
  || echo "Tables not yet available"

echo "=== Flink JobManager ==="
curl -sf http://localhost:8086/config \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("Flink:", d.get("flink-version","?"))' \
  || echo "Flink: starting..."

echo "=== Kafka Topic fraud.raw.events ==="
docker exec fraud-detection-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --describe \
  --topic fraud.raw.events 2>/dev/null || echo "Topic not found"

echo "=== Container Summary ==="
docker ps --filter name=iceberg --format 'table {{.Names}}\t{{.Status}}' 2>/dev/null

echo ""
echo "Smoke test complete."
EOF

ok "Deployment and smoke tests done."
echo ""
echo "=== Access Summary ==="
echo "  MinIO Console:    http://ops-host:9091  (minioadmin / \$MINIO_ROOT_PASSWORD)"
echo "  Iceberg REST:     http://ops-host:8181/v1/config"
echo "  Spark Master UI:  http://ops-host:8088"
echo "  Flink Dashboard:  http://ops-host:8086"
echo ""
echo "=== Next Steps ==="
printf "  1. Run Spark batch job:\n"
printf "     docker exec iceberg-spark /opt/spark/bin/spark-submit \\\\\n"
printf "       --master spark://spark:7077 \\\\\n"
printf "       --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0 \\\\\n"
printf "       /opt/fraud-scripts/spark_fraud_batch.py \\\\\n"
printf "       --date \$(date +%%Y-%%m-%%d) --catalog-uri http://iceberg-rest:8181\n"
echo ""
printf "  2. Submit Flink real-time job (when PyFlink is available):\n"
printf "     flink run -py /opt/fraud-scripts/flink_fraud_realtime.py \\\\\n"
printf "       --kafka-bootstrap kafka:29092 --iceberg-catalog http://iceberg-rest:8181\n"
echo ""
echo "  3. Query Iceberg tables with time-travel:"
echo "     python3 iceberg_fraud_lakehouse.py --action stats"
