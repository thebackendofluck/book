#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 21, Caching Strategies and Benefits.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Chapter 38: Caching Strategies - Local Development Setup
# Sets up configuration files for Docker Compose

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${SCRIPT_DIR}/config"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Create config directories
mkdir -p "${CONFIG_DIR}/grafana/dashboards"
mkdir -p "${CONFIG_DIR}/grafana/datasources"

# Generate credentials if not supplied. Never fall back to a fixed default:
# a shipped default password is a shipped password.
gen_password() { openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-24; }
REDIS_PASSWORD="${REDIS_PASSWORD:-$(gen_password)}"
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-$(gen_password)}"

log_info "Creating Redis configuration..."
cat > "${CONFIG_DIR}/redis.conf" << 'EOF'
# Redis configuration for iGaming development

# Memory management
maxmemory 1gb
maxmemory-policy volatile-lru

# Persistence
appendonly yes
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# Networking
tcp-keepalive 300
timeout 0
tcp-backlog 511

# Performance
hz 10
dynamic-hz yes

# Security
protected-mode yes

# Keyspace notifications
notify-keyspace-events Ex

# Slow log
slowlog-log-slower-than 10000
slowlog-max-len 128

# Logging
loglevel notice
EOF

log_info "Creating Sentinel configuration..."
cat > "${CONFIG_DIR}/sentinel.conf" << EOF
# Sentinel configuration
port 26379

sentinel monitor mymaster redis-master 6379 1
sentinel down-after-milliseconds mymaster 5000
sentinel parallel-syncs mymaster 1
sentinel failover-timeout mymaster 60000
sentinel auth-pass mymaster ${REDIS_PASSWORD}

# Logging
loglevel notice
EOF

log_info "Creating Prometheus configuration..."
cat > "${CONFIG_DIR}/prometheus.yml" << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

alerting:
  alertmanagers:
    - static_configs:
        - targets: []

rule_files: []

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'redis'
    static_configs:
      - targets: ['redis-exporter:9121']
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance
        replacement: 'redis-master'

  - job_name: 'cache-manager'
    static_configs:
      - targets: ['cache-tester:9090']
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance
        replacement: 'cache-manager'
EOF

log_info "Creating Grafana datasource configuration..."
cat > "${CONFIG_DIR}/grafana/datasources/datasources.yml" << 'EOF'
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false

  - name: Redis
    type: redis-datasource
    access: proxy
    url: redis://redis:6379
    editable: false
    jsonData:
      client: standalone
    secureJsonData:
      password: ${REDIS_PASSWORD}
EOF

log_info "Creating Grafana dashboard provisioning..."
cat > "${CONFIG_DIR}/grafana/dashboards/dashboards.yml" << 'EOF'
apiVersion: 1

providers:
  - name: 'default'
    orgId: 1
    folder: 'iGaming Caching'
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /etc/grafana/provisioning/dashboards
EOF

log_info "Creating Redis cache dashboard..."
cat > "${CONFIG_DIR}/grafana/dashboards/redis-cache.json" << 'EOF'
{
  "annotations": {
    "list": []
  },
  "title": "Redis Cache - iGaming",
  "uid": "redis-cache-igaming",
  "version": 1,
  "panels": [
    {
      "title": "Cache Hit Rate",
      "type": "gauge",
      "gridPos": {"h": 8, "w": 6, "x": 0, "y": 0},
      "targets": [
        {
          "expr": "redis_keyspace_hits_total / (redis_keyspace_hits_total + redis_keyspace_misses_total)",
          "legendFormat": "Hit Rate"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "thresholds": {
            "mode": "percentage",
            "steps": [
              {"color": "red", "value": null},
              {"color": "yellow", "value": 70},
              {"color": "green", "value": 85}
            ]
          },
          "unit": "percentunit",
          "min": 0,
          "max": 1
        }
      }
    },
    {
      "title": "Memory Usage",
      "type": "gauge",
      "gridPos": {"h": 8, "w": 6, "x": 6, "y": 0},
      "targets": [
        {
          "expr": "redis_memory_used_bytes / redis_memory_max_bytes",
          "legendFormat": "Memory"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "thresholds": {
            "mode": "percentage",
            "steps": [
              {"color": "green", "value": null},
              {"color": "yellow", "value": 70},
              {"color": "red", "value": 85}
            ]
          },
          "unit": "percentunit"
        }
      }
    },
    {
      "title": "Operations/sec",
      "type": "stat",
      "gridPos": {"h": 8, "w": 6, "x": 12, "y": 0},
      "targets": [
        {
          "expr": "rate(redis_commands_processed_total[1m])",
          "legendFormat": "Ops/sec"
        }
      ]
    },
    {
      "title": "Connected Clients",
      "type": "stat",
      "gridPos": {"h": 8, "w": 6, "x": 18, "y": 0},
      "targets": [
        {
          "expr": "redis_connected_clients",
          "legendFormat": "Clients"
        }
      ]
    },
    {
      "title": "Operations Over Time",
      "type": "timeseries",
      "gridPos": {"h": 10, "w": 12, "x": 0, "y": 8},
      "targets": [
        {
          "expr": "rate(redis_commands_total{cmd=\"get\"}[1m])",
          "legendFormat": "GET"
        },
        {
          "expr": "rate(redis_commands_total{cmd=\"set\"}[1m])",
          "legendFormat": "SET"
        }
      ]
    },
    {
      "title": "Memory Usage Over Time",
      "type": "timeseries",
      "gridPos": {"h": 10, "w": 12, "x": 12, "y": 8},
      "targets": [
        {
          "expr": "redis_memory_used_bytes",
          "legendFormat": "Used"
        },
        {
          "expr": "redis_memory_max_bytes",
          "legendFormat": "Max"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "unit": "bytes"
        }
      }
    }
  ]
}
EOF

log_info "Creating test Dockerfile..."
cat > "${SCRIPT_DIR}/Dockerfile.tester" << 'EOF'
# Cache tester application
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    redis \
    pymemcache \
    aiohttp \
    prometheus-client \
    structlog

# Copy cache patterns module
COPY ../cache-patterns/ /app/cache_patterns/

# Copy test script
COPY test_cache.py /app/

EXPOSE 8080 9090

CMD ["python", "test_cache.py"]
EOF

log_info "Creating test script..."
cat > "${SCRIPT_DIR}/test_cache.py" << 'EOF'
#!/usr/bin/env python3
"""Cache testing application for development."""

import asyncio
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import redis
from pymemcache.client import base as memcache

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ["REDIS_PASSWORD"]
MEMCACHED_HOST = os.getenv("MEMCACHED_HOST", "localhost")
MEMCACHED_PORT = int(os.getenv("MEMCACHED_PORT", "11211"))


class CacheTester:
    """Test cache operations."""

    def __init__(self):
        self.redis = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        self.memcached = memcache.Client((MEMCACHED_HOST, MEMCACHED_PORT))
        self.stats = {"redis_ops": 0, "memcached_ops": 0, "errors": 0}

    def test_redis(self) -> dict:
        """Test Redis operations."""
        results = {}
        try:
            # Test basic operations
            start = time.perf_counter()
            self.redis.set("test:key", "value")
            results["set_latency_ms"] = (time.perf_counter() - start) * 1000

            start = time.perf_counter()
            value = self.redis.get("test:key")
            results["get_latency_ms"] = (time.perf_counter() - start) * 1000

            # Test hash operations
            self.redis.hset("test:hash", mapping={"field1": "value1", "field2": "value2"})
            results["hash_set"] = True

            # Get info
            info = self.redis.info()
            results["connected_clients"] = info.get("connected_clients", 0)
            results["used_memory_human"] = info.get("used_memory_human", "0")
            results["keyspace_hits"] = info.get("keyspace_hits", 0)
            results["keyspace_misses"] = info.get("keyspace_misses", 0)

            self.stats["redis_ops"] += 1
            results["status"] = "healthy"

        except Exception as e:
            self.stats["errors"] += 1
            results["status"] = "error"
            results["error"] = str(e)

        return results

    def test_memcached(self) -> dict:
        """Test Memcached operations."""
        results = {}
        try:
            start = time.perf_counter()
            self.memcached.set("test:key", b"value")
            results["set_latency_ms"] = (time.perf_counter() - start) * 1000

            start = time.perf_counter()
            value = self.memcached.get("test:key")
            results["get_latency_ms"] = (time.perf_counter() - start) * 1000

            self.stats["memcached_ops"] += 1
            results["status"] = "healthy"

        except Exception as e:
            self.stats["errors"] += 1
            results["status"] = "error"
            results["error"] = str(e)

        return results

    def get_stats(self) -> dict:
        """Get test statistics."""
        return self.stats


tester = CacheTester()


class RequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler."""

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "healthy"}')

        elif self.path == "/ready":
            try:
                tester.redis.ping()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status": "ready"}')
            except Exception:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b'{"status": "not ready"}')

        elif self.path == "/test/redis":
            result = tester.test_redis()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        elif self.path == "/test/memcached":
            result = tester.test_memcached()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        elif self.path == "/stats":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(tester.get_stats()).encode())

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress logging


if __name__ == "__main__":
    print(f"Starting cache tester on port 8080...")
    print(f"Redis: {REDIS_HOST}:{REDIS_PORT}")
    print(f"Memcached: {MEMCACHED_HOST}:{MEMCACHED_PORT}")

    server = HTTPServer(("0.0.0.0", 8080), RequestHandler)
    server.serve_forever()
EOF

log_info "Creating .env file..."
cat > "${SCRIPT_DIR}/.env" << EOF
# Redis configuration
REDIS_PASSWORD=${REDIS_PASSWORD}

# Grafana configuration
GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}
EOF
chmod 600 "${SCRIPT_DIR}/.env"

log_info "Setup complete!"
echo ""
echo "To start the stack:"
echo "  cd ${SCRIPT_DIR}"
echo "  docker-compose up -d"
echo ""
echo "Services:"
echo "  Redis:           localhost:6379 (password: ${REDIS_PASSWORD})"
echo "  Redis Replica:   localhost:6380"
echo "  Redis Sentinel:  localhost:26379"
echo "  Memcached:       localhost:11211"
echo "  Redis Commander: http://localhost:8081 (admin / \$GRAFANA_ADMIN_PASSWORD)"
echo "  Prometheus:      http://localhost:9090"
echo "  Grafana:         http://localhost:3000 (admin / see .env)"
echo ""
echo "Test endpoints (with test profile):"
echo "  docker-compose --profile test up -d"
echo "  curl http://localhost:8080/test/redis"
echo "  curl http://localhost:8080/test/memcached"
