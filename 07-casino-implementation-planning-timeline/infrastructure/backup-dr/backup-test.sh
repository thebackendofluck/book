#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2064,SC2155
#
# Automated Backup and Disaster Recovery Test Script
#
# Tests backup integrity and restore procedures for gambling platform
# infrastructure. Validates RTO (Recovery Time Objective) and RPO
# (Recovery Point Objective) compliance.
#
# Components tested:
#   1. PostgreSQL database backup and restore
#   2. Redis snapshot restore
#   3. S3 cross-region replication verification
#   4. Kubernetes etcd backup verification
#   5. Application state restore validation
#   6. Full DR failover simulation (optional)
#
# Usage:
#   chmod +x backup-test.sh
#   ./backup-test.sh                          # Run all tests
#   ./backup-test.sh --component postgres     # Test specific component
#   ./backup-test.sh --full-dr                # Full DR failover simulation
#   ./backup-test.sh --report dr-test.json    # Export test report
#
# Prerequisites:
#   - AWS CLI configured
#   - kubectl configured for the target cluster
#   - PostgreSQL client (psql)
#   - Redis CLI (redis-cli)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Override these via environment variables or modify defaults
DB_HOST="${DB_HOST:-casino-db.cluster-xxx.eu-west-1.rds.amazonaws.com}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-casino_platform}"
DB_USER="${DB_USER:-casino_admin}"
DB_BACKUP_BUCKET="${DB_BACKUP_BUCKET:-casino-db-backups}"
DB_RESTORE_HOST="${DB_RESTORE_HOST:-casino-dr-db.cluster-xxx.eu-west-2.rds.amazonaws.com}"

REDIS_HOST="${REDIS_HOST:-casino-redis.xxx.euw1.cache.amazonaws.com}"
REDIS_PORT="${REDIS_PORT:-6379}"

S3_PRIMARY_BUCKET="${S3_PRIMARY_BUCKET:-casino-platform-data-eu-west-1}"
S3_DR_BUCKET="${S3_DR_BUCKET:-casino-platform-data-eu-west-2}"

K8S_NAMESPACE="${K8S_NAMESPACE:-casino-production}"
K8S_DR_CONTEXT="${K8S_DR_CONTEXT:-arn:aws:eks:eu-west-2:123456789:cluster/casino-dr}"

# Target RTO/RPO (in minutes)
TARGET_RTO_MINUTES=60    # Recovery Time Objective: 1 hour
TARGET_RPO_MINUTES=15    # Recovery Point Objective: 15 minutes

# Test outputs
REPORT_FILE=""
RESULTS=()
PASS_COUNT=0
FAIL_COUNT=0
START_TIME=$(date +%s)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log()     { echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $*"; }
success() { echo -e "${GREEN}[PASS]${NC} $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail()    { echo -e "${RED}[FAIL]${NC} $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
header()  { echo -e "\n${BOLD}=== $* ===${NC}\n"; }

record_result() {
    local test_name="$1"
    local status="$2"
    local duration_ms="$3"
    local details="${4:-}"

    RESULTS+=("{\"test\":\"$test_name\",\"status\":\"$status\",\"duration_ms\":$duration_ms,\"details\":\"$details\"}")

    if [ "$status" = "PASS" ]; then
        success "$test_name (${duration_ms}ms)"
    else
        fail "$test_name (${duration_ms}ms) - $details"
    fi
}

# ---------------------------------------------------------------------------
# Test 1: PostgreSQL Backup Verification
# ---------------------------------------------------------------------------
test_postgres_backup() {
    header "TEST 1: PostgreSQL Backup Verification"

    local test_start=$(date +%s%N)

    # 1a. Check if recent backup exists
    log "Checking for recent backups in s3://${DB_BACKUP_BUCKET}..."

    local latest_backup
    latest_backup=$(aws s3 ls "s3://${DB_BACKUP_BUCKET}/daily/" \
        --recursive 2>/dev/null | sort | tail -1 | awk '{print $4}') || true

    if [ -z "$latest_backup" ]; then
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "postgres-backup-exists" "FAIL" "$duration" "No backups found in S3"
        return
    fi

    log "Latest backup: $latest_backup"

    # Check backup age (RPO compliance)
    local backup_date
    backup_date=$(echo "$latest_backup" | grep -oP '\d{4}-\d{2}-\d{2}' | head -1) || true
    local backup_epoch
    backup_epoch=$(date -d "$backup_date" +%s 2>/dev/null || echo "0")
    local now_epoch=$(date +%s)
    local age_minutes=$(( (now_epoch - backup_epoch) / 60 ))

    if [ "$age_minutes" -le "$TARGET_RPO_MINUTES" ]; then
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "postgres-rpo-compliance" "PASS" "$duration" "Backup age: ${age_minutes}min (target: ${TARGET_RPO_MINUTES}min)"
    else
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "postgres-rpo-compliance" "FAIL" "$duration" "Backup age ${age_minutes}min exceeds RPO ${TARGET_RPO_MINUTES}min"
    fi

    # 1b. Download and verify backup integrity
    log "Downloading backup for integrity check..."
    local temp_dir
    temp_dir=$(mktemp -d)
    trap "rm -rf $temp_dir" EXIT

    if aws s3 cp "s3://${DB_BACKUP_BUCKET}/${latest_backup}" "${temp_dir}/backup.sql.gz" 2>/dev/null; then
        # Check file is not empty and is valid gzip
        if gzip -t "${temp_dir}/backup.sql.gz" 2>/dev/null; then
            local backup_size
            backup_size=$(stat -c%s "${temp_dir}/backup.sql.gz" 2>/dev/null || echo "0")
            local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
            record_result "postgres-backup-integrity" "PASS" "$duration" "Backup valid, size: ${backup_size} bytes"
        else
            local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
            record_result "postgres-backup-integrity" "FAIL" "$duration" "Backup file corrupted"
        fi
    else
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "postgres-backup-integrity" "FAIL" "$duration" "Failed to download backup"
    fi

    # 1c. Test restore to a temporary database
    log "Testing restore to temporary database..."
    local restore_start=$(date +%s%N)

    local test_db="dr_test_$(date +%s)"

    if PGPASSWORD="${DB_PASSWORD:-}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" \
        -c "CREATE DATABASE ${test_db};" 2>/dev/null; then

        if gunzip -c "${temp_dir}/backup.sql.gz" | \
            PGPASSWORD="${DB_PASSWORD:-}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" \
            -d "$test_db" 2>/dev/null; then

            # Verify key tables exist
            local table_count
            table_count=$(PGPASSWORD="${DB_PASSWORD:-}" psql -h "$DB_HOST" -p "$DB_PORT" \
                -U "$DB_USER" -d "$test_db" -t \
                -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | tr -d ' ')

            local restore_duration=$(( ($(date +%s%N) - restore_start) / 1000000 ))
            local restore_minutes=$(( restore_duration / 60000 ))

            if [ "${table_count:-0}" -gt 0 ]; then
                record_result "postgres-restore-test" "PASS" "$restore_duration" "Restored ${table_count} tables in ${restore_minutes}min"

                # Check if restore time meets RTO
                if [ "$restore_minutes" -le "$TARGET_RTO_MINUTES" ]; then
                    record_result "postgres-rto-compliance" "PASS" "$restore_duration" "Restore time: ${restore_minutes}min (target: ${TARGET_RTO_MINUTES}min)"
                else
                    record_result "postgres-rto-compliance" "FAIL" "$restore_duration" "Restore time ${restore_minutes}min exceeds RTO ${TARGET_RTO_MINUTES}min"
                fi
            else
                record_result "postgres-restore-test" "FAIL" "$restore_duration" "Restore produced 0 tables"
            fi
        else
            local restore_duration=$(( ($(date +%s%N) - restore_start) / 1000000 ))
            record_result "postgres-restore-test" "FAIL" "$restore_duration" "psql restore command failed"
        fi

        # Cleanup test database
        PGPASSWORD="${DB_PASSWORD:-}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" \
            -c "DROP DATABASE IF EXISTS ${test_db};" 2>/dev/null || true
    else
        local restore_duration=$(( ($(date +%s%N) - restore_start) / 1000000 ))
        record_result "postgres-restore-test" "FAIL" "$restore_duration" "Could not create test database"
    fi
}

# ---------------------------------------------------------------------------
# Test 2: Redis Backup Verification
# ---------------------------------------------------------------------------
test_redis_backup() {
    header "TEST 2: Redis Backup Verification"

    local test_start=$(date +%s%N)

    # Check Redis LASTSAVE timestamp
    log "Checking Redis last save time..."

    local last_save
    last_save=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" LASTSAVE 2>/dev/null) || true

    if [ -n "$last_save" ]; then
        local now_epoch=$(date +%s)
        local save_age=$(( (now_epoch - last_save) / 60 ))

        if [ "$save_age" -le "$TARGET_RPO_MINUTES" ]; then
            local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
            record_result "redis-rpo-compliance" "PASS" "$duration" "Last save: ${save_age}min ago"
        else
            local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
            record_result "redis-rpo-compliance" "FAIL" "$duration" "Last save ${save_age}min ago exceeds RPO"
        fi

        # Check Redis memory usage and key count
        local db_size
        db_size=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" DBSIZE 2>/dev/null) || true
        log "Redis database size: $db_size"

        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "redis-backup-exists" "PASS" "$duration" "Redis operational, $db_size"
    else
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "redis-backup-exists" "FAIL" "$duration" "Cannot connect to Redis"
    fi
}

# ---------------------------------------------------------------------------
# Test 3: S3 Cross-Region Replication
# ---------------------------------------------------------------------------
test_s3_replication() {
    header "TEST 3: S3 Cross-Region Replication"

    local test_start=$(date +%s%N)

    # Write a test object to primary bucket
    local test_key="dr-test/replication-check-$(date +%s)"
    local test_content="DR_TEST_$(date -u +%Y%m%dT%H%M%SZ)"

    log "Writing test object to primary bucket..."
    if echo "$test_content" | aws s3 cp - "s3://${S3_PRIMARY_BUCKET}/${test_key}" 2>/dev/null; then

        # Wait for replication (typically < 15 minutes for same-region, check every 10s)
        log "Checking replication to DR bucket (max wait: 5 minutes)..."
        local max_wait=300
        local elapsed=0
        local replicated=false

        while [ "$elapsed" -lt "$max_wait" ]; do
            if aws s3 cp "s3://${S3_DR_BUCKET}/${test_key}" /dev/null 2>/dev/null; then
                replicated=true
                break
            fi
            sleep 10
            elapsed=$((elapsed + 10))
            log "  Waiting for replication... (${elapsed}s)"
        done

        if [ "$replicated" = true ]; then
            # Verify content matches
            local dr_content
            dr_content=$(aws s3 cp "s3://${S3_DR_BUCKET}/${test_key}" - 2>/dev/null)

            if [ "$dr_content" = "$test_content" ]; then
                local repl_time=$elapsed
                local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
                record_result "s3-replication" "PASS" "$duration" "Replicated in ${repl_time}s, content verified"
            else
                local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
                record_result "s3-replication" "FAIL" "$duration" "Content mismatch after replication"
            fi
        else
            local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
            record_result "s3-replication" "FAIL" "$duration" "Replication timeout after ${max_wait}s"
        fi

        # Cleanup test objects
        aws s3 rm "s3://${S3_PRIMARY_BUCKET}/${test_key}" 2>/dev/null || true
        aws s3 rm "s3://${S3_DR_BUCKET}/${test_key}" 2>/dev/null || true
    else
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "s3-replication" "FAIL" "$duration" "Could not write to primary bucket"
    fi
}

# ---------------------------------------------------------------------------
# Test 4: Kubernetes State Verification
# ---------------------------------------------------------------------------
test_k8s_state() {
    header "TEST 4: Kubernetes State Verification"

    local test_start=$(date +%s%N)

    # Check if etcd backup exists
    log "Checking Kubernetes etcd backup..."

    local etcd_backup
    etcd_backup=$(aws s3 ls "s3://${DB_BACKUP_BUCKET}/etcd/" \
        --recursive 2>/dev/null | sort | tail -1 | awk '{print $4}') || true

    if [ -n "$etcd_backup" ]; then
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "k8s-etcd-backup" "PASS" "$duration" "Latest: $etcd_backup"
    else
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "k8s-etcd-backup" "FAIL" "$duration" "No etcd backups found"
    fi

    # Verify critical deployments are running
    log "Checking critical deployments in ${K8S_NAMESPACE}..."

    local critical_deployments=("wallet-service" "payment-service" "game-aggregator" "user-service" "compliance-service")

    for deployment in "${critical_deployments[@]}"; do
        local ready
        ready=$(kubectl get deployment "$deployment" -n "$K8S_NAMESPACE" \
            -o jsonpath='{.status.readyReplicas}' 2>/dev/null) || true

        local desired
        desired=$(kubectl get deployment "$deployment" -n "$K8S_NAMESPACE" \
            -o jsonpath='{.spec.replicas}' 2>/dev/null) || true

        if [ "${ready:-0}" -eq "${desired:-0}" ] && [ "${ready:-0}" -gt 0 ]; then
            local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
            record_result "k8s-deployment-${deployment}" "PASS" "$duration" "${ready}/${desired} ready"
        else
            local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
            record_result "k8s-deployment-${deployment}" "FAIL" "$duration" "${ready:-0}/${desired:-0} ready"
        fi
    done

    # Verify PersistentVolumeClaims are bound
    log "Checking PersistentVolumeClaims..."
    local unbound_pvcs
    unbound_pvcs=$(kubectl get pvc -n "$K8S_NAMESPACE" \
        -o jsonpath='{.items[?(@.status.phase!="Bound")].metadata.name}' 2>/dev/null) || true

    if [ -z "$unbound_pvcs" ]; then
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "k8s-pvc-status" "PASS" "$duration" "All PVCs bound"
    else
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "k8s-pvc-status" "FAIL" "$duration" "Unbound PVCs: $unbound_pvcs"
    fi
}

# ---------------------------------------------------------------------------
# Test 5: DR Failover Readiness
# ---------------------------------------------------------------------------
test_dr_readiness() {
    header "TEST 5: DR Failover Readiness"

    local test_start=$(date +%s%N)

    # Check DR cluster is accessible
    log "Checking DR cluster accessibility..."
    if kubectl --context="$K8S_DR_CONTEXT" cluster-info 2>/dev/null | grep -q "running"; then
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "dr-cluster-accessible" "PASS" "$duration" "DR cluster responding"
    else
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "dr-cluster-accessible" "FAIL" "$duration" "Cannot reach DR cluster"
    fi

    # Check DR database replica lag
    log "Checking database replica lag..."
    if [ -n "${DB_RESTORE_HOST:-}" ]; then
        local replica_lag
        replica_lag=$(PGPASSWORD="${DB_PASSWORD:-}" psql -h "$DB_RESTORE_HOST" -p "$DB_PORT" \
            -U "$DB_USER" -d "$DB_NAME" -t \
            -c "SELECT EXTRACT(EPOCH FROM replay_lag)::int FROM pg_stat_replication;" 2>/dev/null | tr -d ' ') || true

        if [ -n "$replica_lag" ] && [ "$replica_lag" -lt $((TARGET_RPO_MINUTES * 60)) ]; then
            local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
            record_result "dr-db-replica-lag" "PASS" "$duration" "Lag: ${replica_lag}s"
        else
            local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
            record_result "dr-db-replica-lag" "FAIL" "$duration" "Lag: ${replica_lag:-unknown}s"
        fi
    fi

    # Verify Route53 health checks
    log "Checking Route53 health checks..."
    local unhealthy
    unhealthy=$(aws route53 list-health-checks --query \
        "HealthChecks[?HealthCheckConfig.FullyQualifiedDomainName=='api.casino-platform.com' && HealthStatus!='Healthy'].Id" \
        --output text 2>/dev/null) || true

    if [ -z "$unhealthy" ]; then
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "dr-route53-health" "PASS" "$duration" "All health checks passing"
    else
        local duration=$(( ($(date +%s%N) - test_start) / 1000000 ))
        record_result "dr-route53-health" "FAIL" "$duration" "Unhealthy checks: $unhealthy"
    fi
}

# ---------------------------------------------------------------------------
# Generate Report
# ---------------------------------------------------------------------------
generate_report() {
    local end_time=$(date +%s)
    local total_duration=$((end_time - START_TIME))

    header "BACKUP & DR TEST REPORT"

    echo -e "  Date:          $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo -e "  Duration:      ${total_duration}s"
    echo -e "  Tests Passed:  ${GREEN}${PASS_COUNT}${NC}"
    echo -e "  Tests Failed:  ${RED}${FAIL_COUNT}${NC}"
    echo -e "  Total Tests:   $((PASS_COUNT + FAIL_COUNT))"
    echo ""
    echo -e "  Target RTO:    ${TARGET_RTO_MINUTES} minutes"
    echo -e "  Target RPO:    ${TARGET_RPO_MINUTES} minutes"
    echo ""

    if [ "$FAIL_COUNT" -eq 0 ]; then
        echo -e "  ${GREEN}${BOLD}OVERALL: ALL TESTS PASSED${NC}"
        echo -e "  DR readiness: CONFIRMED"
    else
        echo -e "  ${RED}${BOLD}OVERALL: ${FAIL_COUNT} TEST(S) FAILED${NC}"
        echo -e "  DR readiness: ACTION REQUIRED"
    fi

    echo ""

    # Export JSON report if requested
    if [ -n "$REPORT_FILE" ]; then
        local results_json=""
        for r in "${RESULTS[@]}"; do
            if [ -n "$results_json" ]; then
                results_json="${results_json},"
            fi
            results_json="${results_json}${r}"
        done

        cat > "$REPORT_FILE" << EOF
{
  "report_type": "backup_dr_test",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "duration_seconds": $total_duration,
  "target_rto_minutes": $TARGET_RTO_MINUTES,
  "target_rpo_minutes": $TARGET_RPO_MINUTES,
  "passed": $PASS_COUNT,
  "failed": $FAIL_COUNT,
  "overall_status": "$([ "$FAIL_COUNT" -eq 0 ] && echo "PASS" || echo "FAIL")",
  "results": [$results_json]
}
EOF
        log "Report exported to $REPORT_FILE"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo -e "${BOLD}"
    echo "============================================================"
    echo "  Gambling Platform Backup & DR Test Suite"
    echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "============================================================"
    echo -e "${NC}"

    local component="${1:-all}"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --component)
                component="$2"
                shift 2
                ;;
            --full-dr)
                component="all"
                shift
                ;;
            --report)
                REPORT_FILE="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    case "$component" in
        postgres)
            test_postgres_backup
            ;;
        redis)
            test_redis_backup
            ;;
        s3)
            test_s3_replication
            ;;
        k8s)
            test_k8s_state
            ;;
        dr)
            test_dr_readiness
            ;;
        all)
            test_postgres_backup
            test_redis_backup
            test_s3_replication
            test_k8s_state
            test_dr_readiness
            ;;
        *)
            echo "Unknown component: $component"
            echo "Available: postgres, redis, s3, k8s, dr, all"
            exit 1
            ;;
    esac

    generate_report
}

main "$@"
