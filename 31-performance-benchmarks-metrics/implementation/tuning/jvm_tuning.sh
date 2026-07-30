#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2004,SC2009
# =============================================================================
# JVM Tuning for Casino Game Servers
# =============================================================================
# Generates optimized JVM flags for casino game server processes:
#   - Slots engine (high throughput, many short-lived objects)
#   - Table games (moderate latency, complex state)
#   - Live dealer proxy (real-time, low-latency critical)
#   - RNG service (cryptographic workload, low GC pause)
#   - Bonus engine (rule evaluation, moderate memory)
#
# Usage:
#   ./jvm_tuning.sh --type slots --heap 4g --output jvm_flags.conf
#   ./jvm_tuning.sh --type live-dealer --heap 8g
#   ./jvm_tuning.sh --type rng --heap 2g
#   ./jvm_tuning.sh --check-running           # Analyze running JVM processes
#   ./jvm_tuning.sh --benchmark --type slots   # Run JVM tuning benchmark
#
# Requirements: JDK 17+ (for ZGC and other modern features)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
GAME_TYPE="slots"
HEAP_SIZE="4g"
OUTPUT_FILE=""
ACTION="generate"
JAVA_VERSION=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Argument Parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --type)           GAME_TYPE="$2"; shift 2;;
        --heap)           HEAP_SIZE="$2"; shift 2;;
        --output|-o)      OUTPUT_FILE="$2"; shift 2;;
        --check-running)  ACTION="check"; shift;;
        --benchmark)      ACTION="benchmark"; shift;;
        --help)
            echo "Usage: $0 [options]"
            echo "  --type <slots|table-games|live-dealer|rng|bonus-engine>"
            echo "  --heap <size>         Heap size (e.g., 4g, 8g, 16g)"
            echo "  --output <file>       Write flags to file"
            echo "  --check-running       Analyze running JVM processes"
            echo "  --benchmark           Run tuning benchmark"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1;;
    esac
done

# Detect Java version
detect_java_version() {
    if command -v java &>/dev/null; then
        JAVA_VERSION=$(java -version 2>&1 | head -1 | grep -oP '\d+' | head -1)
        echo -e "${GREEN}Detected Java version: $JAVA_VERSION${NC}"
    else
        echo -e "${YELLOW}Java not found — generating flags for JDK 21${NC}"
        JAVA_VERSION=21
    fi
}

# ---------------------------------------------------------------------------
# JVM Flag Generation
# ---------------------------------------------------------------------------
generate_flags() {
    detect_java_version

    local flags=""
    local gc_flags=""
    local heap_flags=""
    local diagnostic_flags=""
    local casino_flags=""

    echo "============================================================"
    echo "  JVM Tuning: $GAME_TYPE (Heap: $HEAP_SIZE, JDK $JAVA_VERSION)"
    echo "============================================================"
    echo ""

    # Parse heap size to bytes for calculations
    local heap_bytes=0
    if [[ "$HEAP_SIZE" =~ ^([0-9]+)g$ ]]; then
        heap_bytes=$(( ${BASH_REMATCH[1]} * 1024 * 1024 * 1024 ))
    elif [[ "$HEAP_SIZE" =~ ^([0-9]+)m$ ]]; then
        heap_bytes=$(( ${BASH_REMATCH[1]} * 1024 * 1024 ))
    fi

    # Common heap settings
    heap_flags="-Xms${HEAP_SIZE} -Xmx${HEAP_SIZE}"
    heap_flags+=" -XX:MaxMetaspaceSize=512m"
    heap_flags+=" -XX:MetaspaceSize=256m"

    # Common diagnostic/monitoring flags
    diagnostic_flags="-XX:+HeapDumpOnOutOfMemoryError"
    diagnostic_flags+=" -XX:HeapDumpPath=/var/log/casino/heapdump_%p.hprof"
    diagnostic_flags+=" -XX:+ExitOnOutOfMemoryError"
    diagnostic_flags+=" -XX:ErrorFile=/var/log/casino/hs_err_%p.log"

    # JMX for monitoring (Prometheus JMX exporter)
    diagnostic_flags+=" -Dcom.sun.management.jmxremote"
    diagnostic_flags+=" -Dcom.sun.management.jmxremote.port=9999"
    diagnostic_flags+=" -Dcom.sun.management.jmxremote.authenticate=false"
    diagnostic_flags+=" -Dcom.sun.management.jmxremote.ssl=false"

    # Prometheus JMX agent
    diagnostic_flags+=" -javaagent:/opt/casino/lib/jmx_prometheus_javaagent.jar=9404:/opt/casino/conf/jmx_exporter.yaml"

    # GC logging (unified in JDK 9+)
    diagnostic_flags+=" -Xlog:gc*,gc+age=trace,gc+phases=debug:file=/var/log/casino/gc_%p.log:utctime,pid,tags:filecount=10,filesize=50m"

    # Profile-specific tuning
    case "$GAME_TYPE" in
        slots)
            # Slots: Very high throughput, millions of short-lived bet objects
            # ZGC for <1ms pause times, large young generation
            echo "# Slots Engine — High throughput, sub-ms GC pauses"
            echo "# Workload: ~10K spins/sec, many short-lived Bet/Result objects"
            echo "#"

            if [[ "$JAVA_VERSION" -ge 21 ]]; then
                # ZGC Generational (JDK 21+) — best for slots
                gc_flags="-XX:+UseZGC -XX:+ZGenerational"
                gc_flags+=" -XX:SoftMaxHeapSize=$((${heap_bytes} * 80 / 100))"  # Soft limit at 80%
            elif [[ "$JAVA_VERSION" -ge 17 ]]; then
                gc_flags="-XX:+UseZGC"
            else
                # G1GC fallback
                gc_flags="-XX:+UseG1GC"
                gc_flags+=" -XX:MaxGCPauseMillis=5"
                gc_flags+=" -XX:G1NewSizePercent=40"
                gc_flags+=" -XX:G1MaxNewSizePercent=60"
                gc_flags+=" -XX:G1HeapRegionSize=16m"
            fi

            # Thread optimization for high concurrency
            casino_flags="-XX:+UseStringDeduplication"
            casino_flags+=" -XX:+OptimizeStringConcat"
            casino_flags+=" -XX:AutoBoxCacheMax=20000"  # Cache Integer for bet amounts
            casino_flags+=" -XX:+UseCompressedOops"
            casino_flags+=" -XX:+UseCompressedClassPointers"

            # Netty optimization (used in casino APIs)
            casino_flags+=" -Dio.netty.allocator.type=pooled"
            casino_flags+=" -Dio.netty.leakDetection.level=disabled"
            casino_flags+=" -Dio.netty.recycler.maxCapacityPerThread=4096"

            # Casino game specific
            casino_flags+=" -Dcasino.slots.batch-size=100"
            casino_flags+=" -Dcasino.slots.result-cache-size=10000"
            ;;

        table-games)
            # Table games: Moderate throughput, complex game state, mixed object lifetimes
            echo "# Table Games Engine — Balanced throughput/latency"
            echo "# Workload: Blackjack/Roulette/Baccarat, complex state machines"
            echo "#"

            if [[ "$JAVA_VERSION" -ge 17 ]]; then
                gc_flags="-XX:+UseZGC"
            else
                gc_flags="-XX:+UseG1GC"
                gc_flags+=" -XX:MaxGCPauseMillis=10"
                gc_flags+=" -XX:G1NewSizePercent=30"
                gc_flags+=" -XX:G1MaxNewSizePercent=50"
                gc_flags+=" -XX:G1HeapRegionSize=8m"
                gc_flags+=" -XX:InitiatingHeapOccupancyPercent=45"
            fi

            casino_flags="-XX:+UseStringDeduplication"
            casino_flags+=" -XX:+UseCompressedOops"
            casino_flags+=" -Dcasino.table.max-concurrent-tables=500"
            casino_flags+=" -Dcasino.table.hand-history-cache=5000"
            ;;

        live-dealer)
            # Live dealer: Ultra-low latency, real-time video/audio sync
            echo "# Live Dealer Proxy — Ultra-low latency, real-time"
            echo "# Workload: Video stream proxying, real-time bet acceptance"
            echo "#"

            if [[ "$JAVA_VERSION" -ge 21 ]]; then
                gc_flags="-XX:+UseZGC -XX:+ZGenerational"
            elif [[ "$JAVA_VERSION" -ge 17 ]]; then
                gc_flags="-XX:+UseZGC"
            else
                gc_flags="-XX:+UseShenandoahGC"
                gc_flags+=" -XX:ShenandoahGCHeuristics=compact"
            fi

            # Pin to cores for consistent latency
            casino_flags="-XX:+UseCompressedOops"
            casino_flags+=" -XX:-UseBiasedLocking"  # Deprecated but explicit
            casino_flags+=" -XX:+AlwaysPreTouch"     # Pre-touch heap pages
            casino_flags+=" -XX:+UseTransparentHugePages"

            # Netty for WebSocket/video proxy
            casino_flags+=" -Dio.netty.allocator.type=pooled"
            casino_flags+=" -Dio.netty.eventLoopThreads=16"
            casino_flags+=" -Dio.netty.leakDetection.level=disabled"

            # Live dealer specific
            casino_flags+=" -Dcasino.live.video-buffer-ms=200"
            casino_flags+=" -Dcasino.live.bet-window-ms=15000"
            casino_flags+=" -Dcasino.live.max-concurrent-tables=100"
            ;;

        rng)
            # RNG: Cryptographic workload, security critical, low latency
            echo "# RNG Service — Cryptographic workload, regulatory critical"
            echo "# Workload: SecureRandom, DRBG, NIST SP 800-90A compliance"
            echo "#"

            if [[ "$JAVA_VERSION" -ge 17 ]]; then
                gc_flags="-XX:+UseZGC"
            else
                gc_flags="-XX:+UseG1GC -XX:MaxGCPauseMillis=1"
            fi

            # Security-specific flags
            casino_flags="-Djava.security.egd=file:/dev/urandom"
            casino_flags+=" -Dcom.sun.jndi.rmi.object.trustURLCodebase=false"
            casino_flags+=" -Djava.rmi.server.hostname=localhost"
            casino_flags+=" -XX:+UseCompressedOops"
            casino_flags+=" -XX:+AlwaysPreTouch"

            # RNG specific
            casino_flags+=" -Dcasino.rng.algorithm=DRBG"
            casino_flags+=" -Dcasino.rng.reseed-interval=1000000"
            casino_flags+=" -Dcasino.rng.pool-size=8"
            casino_flags+=" -Dcasino.rng.audit-log=true"

            # FIPS mode if required by regulation
            casino_flags+=" -Dcom.sun.net.ssl.checkRevocation=true"
            ;;

        bonus-engine)
            # Bonus engine: Rule evaluation, wagering calculations
            echo "# Bonus Engine — Rule processing, wagering calculation"
            echo "# Workload: Complex rule evaluation, real-time wagering tracking"
            echo "#"

            if [[ "$JAVA_VERSION" -ge 17 ]]; then
                gc_flags="-XX:+UseG1GC -XX:MaxGCPauseMillis=20"
            else
                gc_flags="-XX:+UseG1GC -XX:MaxGCPauseMillis=20"
            fi

            gc_flags+=" -XX:G1HeapRegionSize=8m"

            casino_flags="-XX:+UseCompressedOops"
            casino_flags+=" -XX:+UseStringDeduplication"
            casino_flags+=" -Dcasino.bonus.rule-cache-size=1000"
            casino_flags+=" -Dcasino.bonus.wagering-batch-size=500"
            ;;

        *)
            echo "Unknown game type: $GAME_TYPE"
            echo "Valid types: slots, table-games, live-dealer, rng, bonus-engine"
            exit 1
            ;;
    esac

    # Assemble all flags
    flags="$heap_flags $gc_flags $diagnostic_flags $casino_flags"

    echo "# Generated JVM flags:"
    echo "# =================================================================="
    echo ""
    echo "JAVA_OPTS=\"\\"
    for flag in $flags; do
        echo "  $flag \\"
    done
    echo "\""
    echo ""

    # Output to file if requested
    if [[ -n "$OUTPUT_FILE" ]]; then
        {
            echo "# Casino JVM Tuning — $GAME_TYPE"
            echo "# Generated: $(date -u +'%Y-%m-%d %H:%M:%S UTC')"
            echo "# Heap: $HEAP_SIZE, JDK: $JAVA_VERSION"
            echo "#"
            echo "JAVA_OPTS=\"\\"
            for flag in $flags; do
                echo "  $flag \\"
            done
            echo "\""
        } > "$OUTPUT_FILE"
        echo -e "${GREEN}Flags written to: $OUTPUT_FILE${NC}"
    fi

    # Systemd service example
    echo ""
    echo "# --- Example systemd service unit ---"
    cat << UNIT
[Unit]
Description=Casino ${GAME_TYPE} Server
After=network.target postgresql.service redis.service
Wants=prometheus-node-exporter.service

[Service]
Type=simple
User=casino-game
Group=casino-game
WorkingDirectory=/opt/casino/${GAME_TYPE}
EnvironmentFile=/opt/casino/${GAME_TYPE}/jvm_flags.conf
ExecStart=/usr/bin/java \$JAVA_OPTS -jar /opt/casino/${GAME_TYPE}/server.jar
Restart=always
RestartSec=5
LimitNOFILE=1048576
LimitNPROC=65535
LimitMEMLOCK=infinity

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/log/casino /tmp

[Install]
WantedBy=multi-user.target
UNIT
}

# ---------------------------------------------------------------------------
# Check Running JVMs
# ---------------------------------------------------------------------------
check_running() {
    echo "============================================================"
    echo "  Running JVM Process Analysis"
    echo "============================================================"
    echo ""

    if ! command -v jps &>/dev/null; then
        echo -e "${YELLOW}jps not found — using ps to find Java processes${NC}"
        ps aux | grep -E '[j]ava|[j]dk' | while read -r line; do
            echo "  $line"
        done
        return
    fi

    jps -lvm 2>/dev/null | while IFS= read -r line; do
        local pid
        pid=$(echo "$line" | awk '{print $1}')
        echo -e "${BLUE}PID: $pid${NC}"
        echo "  Command: $(echo "$line" | cut -d' ' -f2-)"

        # Check heap usage if jstat available
        if command -v jstat &>/dev/null; then
            echo "  Heap Usage:"
            jstat -gc "$pid" 2>/dev/null | tail -1 | awk '{
                s0u=$3; s1u=$5; eu=$7; ou=$9;
                s0c=$2; s1c=$4; ec=$6; oc=$8;
                total_used=(s0u+s1u+eu+ou)/1024;
                total_cap=(s0c+s1c+ec+oc)/1024;
                printf "    Young: %.0fMB / %.0fMB | Old: %.0fMB / %.0fMB | Total: %.0fMB / %.0fMB\n",
                    (s0u+s1u+eu)/1024, (s0c+s1c+ec)/1024,
                    ou/1024, oc/1024, total_used, total_cap
            }' || echo "    (jstat data unavailable)"

            # GC stats
            echo "  GC Stats:"
            jstat -gcutil "$pid" 2>/dev/null | tail -1 | awk '{
                printf "    Young GC: %s collections, %.3fs total | Full GC: %s collections, %.3fs total\n", $13, $14, $15, $16
            }' || echo "    (GC stats unavailable)"
        fi

        # Check GC algorithm
        local gc_algo
        gc_algo=$(jcmd "$pid" VM.flags 2>/dev/null | grep -oP 'Use\w+GC' || echo "unknown")
        echo "  GC Algorithm: $gc_algo"
        echo ""
    done
}

# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------
run_benchmark() {
    echo "============================================================"
    echo "  JVM Tuning Benchmark — $GAME_TYPE"
    echo "============================================================"
    echo ""

    if ! command -v java &>/dev/null; then
        echo -e "${RED}Java not found — cannot run benchmark${NC}"
        exit 1
    fi

    # Create a simple benchmark Java class
    local bench_dir="/tmp/casino-jvm-bench"
    mkdir -p "$bench_dir"

    cat > "$bench_dir/CasinoBenchmark.java" << 'JAVA'
import java.util.*;
import java.util.concurrent.*;
import java.security.SecureRandom;

public class CasinoBenchmark {
    static final int WARMUP = 1_000_000;
    static final int ITERATIONS = 5_000_000;
    static final SecureRandom rng = new SecureRandom();

    public static void main(String[] args) throws Exception {
        System.out.println("Casino JVM Benchmark");
        System.out.println("Heap Max: " + Runtime.getRuntime().maxMemory() / 1024 / 1024 + "MB");
        System.out.println("GC: " + java.lang.management.ManagementFactory.getGarbageCollectorMXBeans());
        System.out.println();

        // Warm up
        for (int i = 0; i < WARMUP; i++) simulateSlotSpin();

        // Benchmark: Slot spins (object allocation heavy)
        long start = System.nanoTime();
        for (int i = 0; i < ITERATIONS; i++) simulateSlotSpin();
        long slotNs = System.nanoTime() - start;
        System.out.printf("Slot Spins: %d iterations in %.2fs (%.0f spins/sec)%n",
            ITERATIONS, slotNs / 1e9, ITERATIONS / (slotNs / 1e9));

        // Benchmark: RNG generation
        start = System.nanoTime();
        for (int i = 0; i < ITERATIONS; i++) rng.nextInt(37);
        long rngNs = System.nanoTime() - start;
        System.out.printf("RNG Calls:  %d iterations in %.2fs (%.0f calls/sec)%n",
            ITERATIONS, rngNs / 1e9, ITERATIONS / (rngNs / 1e9));

        // Report GC stats
        for (var gc : java.lang.management.ManagementFactory.getGarbageCollectorMXBeans()) {
            System.out.printf("GC [%s]: %d collections, %dms total%n",
                gc.getName(), gc.getCollectionCount(), gc.getCollectionTime());
        }
    }

    static Map<String, Object> simulateSlotSpin() {
        int[] reels = new int[5];
        for (int i = 0; i < 5; i++) reels[i] = rng.nextInt(12);
        Map<String, Object> result = new HashMap<>();
        result.put("reels", reels);
        result.put("stake", 100 + rng.nextInt(9900));
        result.put("payout", rng.nextDouble() < 0.3 ? rng.nextInt(50000) : 0);
        result.put("timestamp", System.currentTimeMillis());
        return result;
    }
}
JAVA

    echo "Compiling benchmark..."
    javac "$bench_dir/CasinoBenchmark.java" 2>/dev/null

    echo ""
    echo "Running with default flags..."
    java -Xms512m -Xmx512m -cp "$bench_dir" CasinoBenchmark 2>/dev/null
    echo ""

    if [[ "$JAVA_VERSION" -ge 17 ]]; then
        echo "Running with ZGC..."
        java -Xms512m -Xmx512m -XX:+UseZGC -cp "$bench_dir" CasinoBenchmark 2>/dev/null
        echo ""
    fi

    echo "Running with G1GC (low pause)..."
    java -Xms512m -Xmx512m -XX:+UseG1GC -XX:MaxGCPauseMillis=5 -cp "$bench_dir" CasinoBenchmark 2>/dev/null
    echo ""

    rm -rf "$bench_dir"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "$ACTION" in
    generate)  generate_flags;;
    check)     check_running;;
    benchmark) run_benchmark;;
esac
