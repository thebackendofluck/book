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

# shellcheck disable=SC2034,SC2119,SC2120
#
# Interactive Technology Stack Evaluator for iGaming Platforms
#
# Compares frameworks, databases, cloud providers, and key infrastructure
# components specifically for online casino / sportsbook platforms.
#
# Usage:
#   chmod +x tech-stack-evaluator.sh
#   ./tech-stack-evaluator.sh
#   ./tech-stack-evaluator.sh --quick    # Skip detailed comparisons
#   ./tech-stack-evaluator.sh --export   # Export recommendation to JSON

set -euo pipefail

# ---------------------------------------------------------------------------
# Color output helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
header()  { echo -e "\n${BOLD}${CYAN}=== $* ===${NC}\n"; }

EXPORT_MODE=false
QUICK_MODE=false
RECOMMENDATIONS=()

for arg in "$@"; do
    case "$arg" in
        --export) EXPORT_MODE=true ;;
        --quick)  QUICK_MODE=true ;;
    esac
done

# ---------------------------------------------------------------------------
# Score tracking
# ---------------------------------------------------------------------------
declare -A SCORES
declare -A SELECTED

add_score() {
    local category="$1"
    local choice="$2"
    local score="${3:-0}"
    SELECTED["$category"]="$choice"
    SCORES["$category"]="$score"
}

# ---------------------------------------------------------------------------
# Evaluation functions
# ---------------------------------------------------------------------------

evaluate_cloud_provider() {
    header "CLOUD PROVIDER SELECTION"

    echo "Compare cloud providers for iGaming workloads:"
    echo ""
    printf "%-20s %-20s %-20s %-20s\n" "Feature" "AWS" "GCP" "Azure"
    printf "%-20s %-20s %-20s %-20s\n" "---" "---" "---" "---"
    printf "%-20s %-20s %-20s %-20s\n" "Gaming Compliance" "Best (GovCloud)" "Good" "Good"
    printf "%-20s %-20s %-20s %-20s\n" "Global Regions" "33 regions" "40 regions" "60+ regions"
    printf "%-20s %-20s %-20s %-20s\n" "Managed K8s" "EKS (mature)" "GKE (best)" "AKS (good)"
    printf "%-20s %-20s %-20s %-20s\n" "Managed DB" "RDS/Aurora" "Cloud SQL" "Azure SQL"
    printf "%-20s %-20s %-20s %-20s\n" "CDN/Edge" "CloudFront" "Cloud CDN" "Azure CDN"
    printf "%-20s %-20s %-20s %-20s\n" "DDoS Protection" "Shield Adv" "Cloud Armor" "DDoS Prot"
    printf "%-20s %-20s %-20s %-20s\n" "Cost (typical)" "$$$$" "$$$" "$$$"
    printf "%-20s %-20s %-20s %-20s\n" "iGaming Track Rec" "Most used" "Growing" "Limited"
    echo ""

    echo "Key considerations for iGaming:"
    echo "  - Data residency: Servers must be in approved jurisdictions"
    echo "  - PCI DSS: All handle PCI, but AWS has most certifications"
    echo "  - Real-time: Sub-100ms response for live casino and betting"
    echo "  - Scaling: Must handle 10x spikes during major sporting events"
    echo ""

    if [ "$QUICK_MODE" = false ]; then
        echo "Select cloud provider:"
        echo "  1) AWS (recommended for iGaming - most compliance certifications)"
        echo "  2) GCP (best Kubernetes, strong analytics)"
        echo "  3) Azure (enterprise integration, wide region coverage)"
        echo "  4) Multi-cloud (AWS primary + GCP/Azure for DR)"
        read -rp "Choice [1]: " cloud_choice
    else
        cloud_choice="1"
    fi

    case "${cloud_choice:-1}" in
        1) add_score "cloud" "AWS" 90
           RECOMMENDATIONS+=("AWS is the industry standard for iGaming. Use eu-west-1 (Ireland) for EU-licensed operations, us-east-1 for US markets.")
           ;;
        2) add_score "cloud" "GCP" 80
           RECOMMENDATIONS+=("GCP offers excellent Kubernetes (GKE) and BigQuery for analytics. Verify data residency options for your target jurisdictions.")
           ;;
        3) add_score "cloud" "Azure" 75
           RECOMMENDATIONS+=("Azure provides wide region coverage. Consider Azure for markets where Microsoft has strong government relationships.")
           ;;
        4) add_score "cloud" "Multi-cloud" 85
           RECOMMENDATIONS+=("Multi-cloud adds complexity but provides vendor independence and DR flexibility. Use Terraform to abstract provider differences.")
           ;;
    esac

    success "Cloud provider: ${SELECTED[cloud]}"
}

evaluate_backend_framework() {
    header "BACKEND FRAMEWORK SELECTION"

    echo "Compare backend frameworks for casino platform:"
    echo ""
    printf "%-18s %-14s %-14s %-14s %-14s\n" "Feature" "Go" "Java/Kotlin" "Node.js" "Python"
    printf "%-18s %-14s %-14s %-14s %-14s\n" "---" "---" "---" "---" "---"
    printf "%-18s %-14s %-14s %-14s %-14s\n" "Performance" "Excellent" "Very Good" "Good" "Fair"
    printf "%-18s %-14s %-14s %-14s %-14s\n" "Concurrency" "Goroutines" "Virtual Thds" "Event Loop" "AsyncIO"
    printf "%-18s %-14s %-14s %-14s %-14s\n" "Type Safety" "Strong" "Strong" "TS: Strong" "Optional"
    printf "%-18s %-14s %-14s %-14s %-14s\n" "iGaming Libs" "Limited" "Excellent" "Good" "Good"
    printf "%-18s %-14s %-14s %-14s %-14s\n" "Talent Pool" "Growing" "Large" "Very Large" "Very Large"
    printf "%-18s %-14s %-14s %-14s %-14s\n" "Latency (p99)" "<5ms" "<10ms" "<15ms" "<20ms"
    printf "%-18s %-14s %-14s %-14s %-14s\n" "Memory Usage" "Low" "Medium" "Medium" "High"
    printf "%-18s %-14s %-14s %-14s %-14s\n" "Deploy Size" "~10MB" "~200MB" "~100MB" "~150MB"
    echo ""

    echo "iGaming-specific considerations:"
    echo "  - Wallet service: Needs strong consistency (Go or Java)"
    echo "  - Game aggregation: API-heavy, Node.js/TypeScript excels"
    echo "  - Back office: Python/Django fast to build"
    echo "  - Recommendation: Use polyglot - right tool for each service"
    echo ""

    if [ "$QUICK_MODE" = false ]; then
        echo "Primary backend language:"
        echo "  1) Go (best for wallet, payment, and real-time services)"
        echo "  2) Java/Kotlin with Spring Boot (enterprise, strong iGaming ecosystem)"
        echo "  3) Node.js/TypeScript (fast development, great for API gateway)"
        echo "  4) Python (fast prototyping, ML/analytics, back office)"
        echo "  5) Polyglot (recommended: Go for core, TypeScript for API, Python for analytics)"
        read -rp "Choice [5]: " backend_choice
    else
        backend_choice="5"
    fi

    case "${backend_choice:-5}" in
        1) add_score "backend" "Go" 85 ;;
        2) add_score "backend" "Java/Kotlin" 80 ;;
        3) add_score "backend" "Node.js/TypeScript" 78 ;;
        4) add_score "backend" "Python" 70 ;;
        5) add_score "backend" "Polyglot (Go + TypeScript + Python)" 90
           RECOMMENDATIONS+=("Polyglot approach: Go for wallet/payments (performance-critical), TypeScript for API gateway and game integration (rapid development), Python for analytics and back office.")
           ;;
    esac

    success "Backend: ${SELECTED[backend]}"
}

evaluate_database() {
    header "DATABASE SELECTION"

    echo "Compare databases for casino platforms:"
    echo ""
    printf "%-18s %-16s %-16s %-16s\n" "Feature" "PostgreSQL" "MySQL/Aurora" "CockroachDB"
    printf "%-18s %-16s %-16s %-16s\n" "---" "---" "---" "---"
    printf "%-18s %-16s %-16s %-16s\n" "ACID Compliance" "Full" "Full (InnoDB)" "Full"
    printf "%-18s %-16s %-16s %-16s\n" "Multi-region" "Manual" "Aurora Global" "Native"
    printf "%-18s %-16s %-16s %-16s\n" "JSON Support" "Excellent" "Good" "Good"
    printf "%-18s %-16s %-16s %-16s\n" "Partitioning" "Native" "Native" "Automatic"
    printf "%-18s %-16s %-16s %-16s\n" "Max TPS" "~50K" "~80K (Aurora)" "~30K"
    printf "%-18s %-16s %-16s %-16s\n" "Licensing" "Open Source" "GPL/Commercial" "BSL/Commercial"
    printf "%-18s %-16s %-16s %-16s\n" "iGaming Usage" "Very Common" "Most Common" "Growing"
    echo ""

    echo "Additional data stores for iGaming:"
    echo "  - Redis: Session management, rate limiting, leaderboards"
    echo "  - Elasticsearch: Player search, game catalog, audit logs"
    echo "  - ClickHouse/TimescaleDB: Real-time analytics, reporting"
    echo "  - Apache Kafka: Event streaming for wallet transactions"
    echo ""

    if [ "$QUICK_MODE" = false ]; then
        echo "Primary database:"
        echo "  1) PostgreSQL (recommended - best balance of features and cost)"
        echo "  2) MySQL/Aurora (proven in iGaming, best managed option on AWS)"
        echo "  3) CockroachDB (best for multi-region, higher cost)"
        read -rp "Choice [1]: " db_choice
    else
        db_choice="1"
    fi

    case "${db_choice:-1}" in
        1) add_score "database" "PostgreSQL" 90
           RECOMMENDATIONS+=("PostgreSQL with pgbouncer for connection pooling. Use JSONB for flexible game metadata. TimescaleDB extension for time-series analytics.")
           ;;
        2) add_score "database" "MySQL/Aurora" 85
           RECOMMENDATIONS+=("Aurora MySQL for auto-scaling reads. Use Aurora Global for multi-region active-passive. Consider Aurora Serverless v2 for variable workloads.")
           ;;
        3) add_score "database" "CockroachDB" 80
           RECOMMENDATIONS+=("CockroachDB for multi-region active-active. Higher operational cost but eliminates cross-region replication lag for wallet operations.")
           ;;
    esac

    success "Database: ${SELECTED[database]}"
}

evaluate_message_broker() {
    header "MESSAGE BROKER / EVENT STREAMING"

    echo "Critical for iGaming transaction processing:"
    echo ""
    printf "%-18s %-16s %-16s %-16s\n" "Feature" "Apache Kafka" "RabbitMQ" "AWS SQS/SNS"
    printf "%-18s %-16s %-16s %-16s\n" "---" "---" "---" "---"
    printf "%-18s %-16s %-16s %-16s\n" "Throughput" "1M+ msg/s" "~50K msg/s" "~300K msg/s"
    printf "%-18s %-16s %-16s %-16s\n" "Ordering" "Partition" "Queue" "FIFO optional"
    printf "%-18s %-16s %-16s %-16s\n" "Retention" "Configurable" "Until consumed" "14 days max"
    printf "%-18s %-16s %-16s %-16s\n" "Event Sourcing" "Excellent" "Not ideal" "Not ideal"
    printf "%-18s %-16s %-16s %-16s\n" "Complexity" "High" "Low" "Very Low"
    printf "%-18s %-16s %-16s %-16s\n" "Audit Trail" "Built-in" "Plugin" "CloudTrail"
    echo ""

    if [ "$QUICK_MODE" = false ]; then
        echo "Select message broker:"
        echo "  1) Apache Kafka (recommended for wallet events, audit trail)"
        echo "  2) RabbitMQ (simpler, good for smaller scale)"
        echo "  3) AWS SQS/SNS (fully managed, lowest ops overhead)"
        echo "  4) Kafka + SQS (Kafka for core events, SQS for notifications)"
        read -rp "Choice [1]: " mq_choice
    else
        mq_choice="1"
    fi

    case "${mq_choice:-1}" in
        1) add_score "messaging" "Apache Kafka" 90 ;;
        2) add_score "messaging" "RabbitMQ" 75 ;;
        3) add_score "messaging" "AWS SQS/SNS" 70 ;;
        4) add_score "messaging" "Kafka + SQS" 88
           RECOMMENDATIONS+=("Use Kafka for wallet transactions and game events (audit trail + event sourcing). Use SQS for email/SMS notifications and async tasks.")
           ;;
    esac

    success "Messaging: ${SELECTED[messaging]}"
}

evaluate_container_orchestration() {
    header "CONTAINER ORCHESTRATION"

    echo ""
    printf "%-18s %-18s %-18s %-18s\n" "Feature" "Kubernetes" "ECS/Fargate" "Docker Swarm"
    printf "%-18s %-18s %-18s %-18s\n" "---" "---" "---" "---"
    printf "%-18s %-18s %-18s %-18s\n" "Scaling" "Auto (HPA/VPA)" "Auto (Service)" "Manual/basic"
    printf "%-18s %-18s %-18s %-18s\n" "Complexity" "High" "Medium" "Low"
    printf "%-18s %-18s %-18s %-18s\n" "Portability" "Any cloud" "AWS only" "Any host"
    printf "%-18s %-18s %-18s %-18s\n" "Service Mesh" "Istio/Linkerd" "App Mesh" "N/A"
    printf "%-18s %-18s %-18s %-18s\n" "Cost at Scale" "Lower" "Higher" "Lowest"
    printf "%-18s %-18s %-18s %-18s\n" "Team Required" "DevOps/SRE" "Dev team" "Any dev"
    echo ""

    if [ "$QUICK_MODE" = false ]; then
        echo "Select orchestration:"
        echo "  1) Kubernetes (EKS/GKE) - recommended for production iGaming"
        echo "  2) ECS/Fargate - simpler, AWS-native"
        echo "  3) Docker Compose + Swarm - minimal team, quick start"
        read -rp "Choice [1]: " orch_choice
    else
        orch_choice="1"
    fi

    case "${orch_choice:-1}" in
        1) add_score "orchestration" "Kubernetes" 90
           RECOMMENDATIONS+=("Use managed Kubernetes (EKS or GKE). Implement namespace isolation: production, staging, monitoring, compliance. Use Istio service mesh for mTLS between services.")
           ;;
        2) add_score "orchestration" "ECS/Fargate" 80 ;;
        3) add_score "orchestration" "Docker Compose" 60
           warn "Docker Compose is not recommended for production iGaming. Consider upgrading to K8s before launch."
           ;;
    esac

    success "Orchestration: ${SELECTED[orchestration]}"
}

evaluate_monitoring() {
    header "MONITORING & OBSERVABILITY"

    echo "iGaming requires comprehensive monitoring for compliance and operations:"
    echo ""
    echo "  Option 1: Prometheus + Grafana + Loki + Tempo (open source)"
    echo "    Cost: Infrastructure only (~\$2-5K/month)"
    echo "    Pros: Full control, no data egress, customizable"
    echo "    Cons: Requires ops team to maintain"
    echo ""
    echo "  Option 2: Datadog (SaaS)"
    echo "    Cost: ~\$15-30K/month at scale"
    echo "    Pros: All-in-one, excellent APM, easy setup"
    echo "    Cons: Expensive at scale, vendor lock-in"
    echo ""
    echo "  Option 3: ELK Stack + Prometheus (hybrid)"
    echo "    Cost: ~\$5-10K/month (Elastic Cloud)"
    echo "    Pros: Best log analysis, good for compliance audit"
    echo "    Cons: Complex to operate"
    echo ""

    if [ "$QUICK_MODE" = false ]; then
        echo "Select monitoring stack:"
        echo "  1) Prometheus + Grafana (recommended - cost-effective at scale)"
        echo "  2) Datadog (best UX, higher cost)"
        echo "  3) ELK + Prometheus (best for compliance-heavy operations)"
        read -rp "Choice [1]: " mon_choice
    else
        mon_choice="1"
    fi

    case "${mon_choice:-1}" in
        1) add_score "monitoring" "Prometheus + Grafana" 88
           RECOMMENDATIONS+=("Deploy Prometheus with Thanos for long-term retention. Use Grafana for dashboards. Loki for logs, Tempo for distributed tracing. Total cost ~\$3K/month.")
           ;;
        2) add_score "monitoring" "Datadog" 85 ;;
        3) add_score "monitoring" "ELK + Prometheus" 82 ;;
    esac

    success "Monitoring: ${SELECTED[monitoring]}"
}

print_recommendation() {
    header "TECHNOLOGY STACK RECOMMENDATION"

    echo "  Selected Stack:"
    echo "  ----------------------------------------"
    for category in cloud backend database messaging orchestration monitoring; do
        if [[ -n "${SELECTED[$category]:-}" ]]; then
            printf "  %-20s %s (score: %s/100)\n" "$category:" "${SELECTED[$category]}" "${SCORES[$category]}"
        fi
    done

    # Calculate overall score
    local total=0
    local count=0
    for score in "${SCORES[@]}"; do
        total=$((total + score))
        count=$((count + 1))
    done
    local avg=$((total / count))

    echo ""
    echo "  Overall Stack Score: ${avg}/100"
    echo ""

    if [ "$avg" -ge 85 ]; then
        success "This stack is well-suited for production iGaming platforms."
    elif [ "$avg" -ge 70 ]; then
        warn "This stack is adequate but consider upgrades for scale."
    else
        warn "This stack may need significant improvements for production iGaming."
    fi

    echo ""
    echo "  RECOMMENDATIONS"
    echo "  ----------------------------------------"
    for i in "${!RECOMMENDATIONS[@]}"; do
        echo "  $((i + 1)). ${RECOMMENDATIONS[$i]}"
        echo ""
    done

    echo "  ADDITIONAL iGAMING-SPECIFIC RECOMMENDATIONS"
    echo "  ----------------------------------------"
    echo "  - WAF: Deploy AWS WAF or Cloudflare in front of all public endpoints"
    echo "  - CDN: Use CloudFront or Cloudflare for static assets and game launchers"
    echo "  - Secrets: AWS Secrets Manager or HashiCorp Vault for API keys and certs"
    echo "  - CI/CD: GitHub Actions or GitLab CI with security scanning gates"
    echo "  - IaC: Terraform with remote state (S3 + DynamoDB) for all infrastructure"
    echo "  - RNG: Use hardware RNG (CloudHSM) for any server-side game logic"
    echo ""
}

export_json() {
    local output_file="${1:-tech-stack-recommendation.json}"

    # Build JSON manually (no jq dependency)
    cat > "$output_file" << JSONEOF
{
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "stack": {
    "cloud_provider": "${SELECTED[cloud]:-N/A}",
    "backend_framework": "${SELECTED[backend]:-N/A}",
    "database": "${SELECTED[database]:-N/A}",
    "message_broker": "${SELECTED[messaging]:-N/A}",
    "orchestration": "${SELECTED[orchestration]:-N/A}",
    "monitoring": "${SELECTED[monitoring]:-N/A}"
  },
  "scores": {
    "cloud_provider": ${SCORES[cloud]:-0},
    "backend_framework": ${SCORES[backend]:-0},
    "database": ${SCORES[database]:-0},
    "message_broker": ${SCORES[messaging]:-0},
    "orchestration": ${SCORES[orchestration]:-0},
    "monitoring": ${SCORES[monitoring]:-0}
  }
}
JSONEOF

    success "Recommendation exported to ${output_file}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    echo -e "${BOLD}"
    echo "============================================================"
    echo "  iGaming Platform Technology Stack Evaluator"
    echo "  Chapter 22: Casino Implementation Planning"
    echo "============================================================"
    echo -e "${NC}"

    evaluate_cloud_provider
    evaluate_backend_framework
    evaluate_database
    evaluate_message_broker
    evaluate_container_orchestration
    evaluate_monitoring
    print_recommendation

    if [ "$EXPORT_MODE" = true ]; then
        export_json
    fi
}

main
