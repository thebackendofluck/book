#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034,SC2155
# Deployment Script for Hardware Monitoring Stack - Chapter 23: Operational Playbooks
#
# Manages the Docker Compose hardware monitoring stack lifecycle:
#   deploy  - Check prerequisites, pull images, start services, verify health
#   scale   - Scale hardware-agent replicas (e.g., scale 3)
#   update  - Reload Prometheus and AlertManager configuration via HUP signal
#   backup  - Backup InfluxDB, Elasticsearch snapshots, and Grafana data
#   stop    - Bring down the stack
#   restart - Restart all services
#   logs    - Follow logs for all or a specific service
#   status  - Show service status
#
# Usage: ./deploy.sh {deploy|scale [N]|update|backup|stop|restart|logs [svc]|status}
#
# Part of the iGaming Platform Engineering book.

set -e

# Configuration
STACK_NAME="hardware-monitoring"
DOCKER_COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

error() {
    echo -e "${RED}[ERROR] $1${NC}" >&2
}

warn() {
    echo -e "${YELLOW}[WARNING] $1${NC}"
}

# Check prerequisites
check_prerequisites() {
    log "Checking prerequisites..."

    if ! command -v docker &> /dev/null; then
        error "Docker is not installed"
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null; then
        error "Docker Compose is not installed"
        exit 1
    fi

    if [ ! -f "$ENV_FILE" ]; then
        warn "Environment file $ENV_FILE not found. Creating template..."

        # Fail fast if secrets are not provided via the caller's environment.
        # Never bake default passwords/tokens into the generated .env: the
        # template would otherwise ship reproducible credentials to disk.
        INFLUXDB_PASSWORD="${INFLUXDB_PASSWORD:?INFLUXDB_PASSWORD must be set before deploy}"
        INFLUXDB_ADMIN_TOKEN="${INFLUXDB_ADMIN_TOKEN:?INFLUXDB_ADMIN_TOKEN must be set before deploy}"
        GRAFANA_PASSWORD="${GRAFANA_PASSWORD:?GRAFANA_PASSWORD must be set before deploy}"

        cat > "$ENV_FILE" <<EOF
# Hardware Monitoring Environment Configuration

# Elasticsearch
ELASTICSEARCH_HOST=elasticsearch:9200

# InfluxDB
INFLUXDB_HOST=influxdb:8086
DOCKER_INFLUXDB_INIT_USERNAME=admin
DOCKER_INFLUXDB_INIT_PASSWORD=$INFLUXDB_PASSWORD
DOCKER_INFLUXDB_INIT_ORG=igaming
DOCKER_INFLUXDB_INIT_BUCKET=hardware_metrics
DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=$INFLUXDB_ADMIN_TOKEN

# Prometheus
PROMETHEUS_RETENTION=200h

# Grafana
GF_SECURITY_ADMIN_PASSWORD=$GRAFANA_PASSWORD

# Twilio (WhatsApp)
TWILIO_ACCOUNT_SID=your-twilio-account-sid
TWILIO_AUTH_TOKEN=your-twilio-auth-token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
ALERT_WHATSAPP_NUMBERS=+1234567890,+0987654321

# Email
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=alerts@igaming.com
SMTP_PASSWORD=your-smtp-password
ALERT_EMAIL_TO=ops@igaming.com

# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK

# PagerDuty
PAGERDUTY_INTEGRATION_KEY=your-pagerduty-integration-key
EOF
        warn "Please edit $ENV_FILE with your actual configuration values"
        exit 1
    fi

    log "Prerequisites check completed"
}

# Deploy the stack
deploy_stack() {
    log "Deploying hardware monitoring stack..."

    # Pull latest images
    log "Pulling Docker images..."
    docker-compose -f "$DOCKER_COMPOSE_FILE" pull

    # Start the stack
    log "Starting services..."
    docker-compose -f "$DOCKER_COMPOSE_FILE" up -d

    # Wait for services to be healthy
    log "Waiting for services to start..."
    sleep 30

    # Check service health
    check_services_health

    log "Hardware monitoring stack deployed successfully!"
    log "Access points:"
    log "  - Grafana: http://localhost:3000 (credentials in your .env)"
    log "  - Prometheus: http://localhost:9090"
    log "  - AlertManager: http://localhost:9093"
    log "  - Elasticsearch: http://localhost:9200"
    log "  - InfluxDB: http://localhost:8086"
}

# Check service health
check_services_health() {
    log "Checking service health..."

    services=("elasticsearch" "influxdb" "prometheus" "grafana" "alertmanager")

    for service in "${services[@]}"; do
        if docker-compose -f "$DOCKER_COMPOSE_FILE" ps "$service" | grep -q "Up"; then
            log "✓ $service is running"
        else
            error "$service failed to start"
            docker-compose -f "$DOCKER_COMPOSE_FILE" logs "$service"
            exit 1
        fi
    done
}

# Scale monitoring agents
scale_agents() {
    local num_agents=${1:-3}
    log "Scaling hardware agents to $num_agents replicas..."

    docker-compose -f "$DOCKER_COMPOSE_FILE" up -d --scale hardware-agent="$num_agents"
}

# Update configuration
update_config() {
    log "Updating configuration..."

    # Reload Prometheus configuration
    docker-compose -f "$DOCKER_COMPOSE_FILE" exec -T prometheus kill -HUP 1

    # Reload AlertManager configuration
    docker-compose -f "$DOCKER_COMPOSE_FILE" exec -T alertmanager kill -HUP 1

    log "Configuration updated"
}

# Backup data
backup_data() {
    local backup_dir="./backups/$(date +%Y%m%d_%H%M%S)"
    log "Creating backup in $backup_dir..."

    mkdir -p "$backup_dir"

    # Backup InfluxDB
    docker-compose -f "$DOCKER_COMPOSE_FILE" exec -T influxdb influx backup /tmp/backup
    docker cp "$(docker-compose -f "$DOCKER_COMPOSE_FILE" ps -q influxdb)":/tmp/backup "$backup_dir/influxdb"

    # Backup Elasticsearch
    docker-compose -f "$DOCKER_COMPOSE_FILE" exec -T elasticsearch curl -X PUT "localhost:9200/_snapshot/hardware_backup" -H 'Content-Type: application/json' -d"{\"type\": \"fs\",\"settings\": {\"location\": \"/tmp/es_backup\"}}"
    docker-compose -f "$DOCKER_COMPOSE_FILE" exec -T elasticsearch curl -X PUT "localhost:9200/_snapshot/hardware_backup/snapshot_$(date +%Y%m%d_%H%M%S)?wait_for_completion=true"
    docker cp "$(docker-compose -f "$DOCKER_COMPOSE_FILE" ps -q elasticsearch)":/tmp/es_backup "$backup_dir/elasticsearch"

    # Backup Grafana
    docker cp "$(docker-compose -f "$DOCKER_COMPOSE_FILE" ps -q grafana)":/var/lib/grafana "$backup_dir/grafana"

    log "Backup completed: $backup_dir"
}

# Main script
main() {
    case "${1:-deploy}" in
        "deploy")
            check_prerequisites
            deploy_stack
            ;;
        "scale")
            scale_agents "$2"
            ;;
        "update")
            update_config
            ;;
        "backup")
            backup_data
            ;;
        "stop")
            log "Stopping hardware monitoring stack..."
            docker-compose -f "$DOCKER_COMPOSE_FILE" down
            ;;
        "restart")
            log "Restarting hardware monitoring stack..."
            docker-compose -f "$DOCKER_COMPOSE_FILE" restart
            ;;
        "logs")
            docker-compose -f "$DOCKER_COMPOSE_FILE" logs -f "${2:-}"
            ;;
        "status")
            docker-compose -f "$DOCKER_COMPOSE_FILE" ps
            ;;
        *)
            echo "Usage: $0 {deploy|scale|update|backup|stop|restart|logs|status}"
            echo "  deploy: Deploy the monitoring stack"
            echo "  scale <num>: Scale hardware agents"
            echo "  update: Reload configuration"
            echo "  backup: Create data backup"
            echo "  stop: Stop the stack"
            echo "  restart: Restart the stack"
            echo "  logs [service]: Show logs"
            echo "  status: Show service status"
            exit 1
            ;;
    esac
}

main "$@"
