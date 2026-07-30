# Monitoring Setup

This directory contains the monitoring configuration for the Fraud Detection System using Prometheus, Grafana, and AlertManager.

## Components

### Prometheus
- **Purpose**: Metrics collection and storage
- **Port**: 9090
- **Configuration**: `prometheus.yml`
- **Alert Rules**: `alert_rules.yml`

### Grafana
- **Purpose**: Visualization and dashboards
- **Port**: 3000
- **Credentials**: `admin` / the value of `GRAFANA_ADMIN_PASSWORD` (set it before starting the stack)
- **Dashboards**:
  - System Overview (`grafana-dashboard.json`)
  - Fraud Analytics (`grafana-fraud-dashboard.json`)

### AlertManager
- **Purpose**: Alert routing and notification
- **Port**: 9093
- **Configuration**: `alertmanager.yml`

### Additional Exporters
- **Node Exporter**: System metrics (Port 9100)
- **cAdvisor**: Container metrics (Port 8080)

## Quick Start

### Using Docker Compose

```bash
# Start monitoring stack
docker-compose -f monitoring/docker-compose.monitoring.yml up -d

# View services
docker-compose -f monitoring/docker-compose.monitoring.yml ps
```

### Manual Setup

```bash
# Start Prometheus
docker run -d -p 9090:9090 -v $(pwd)/monitoring/prometheus.yml:/etc/prometheus/prometheus.yml prom/prometheus

# Start Grafana
docker run -d -p 3000:3000 -e GF_SECURITY_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD first}" grafana/grafana

# Start AlertManager
docker run -d -p 9093:9093 -v $(pwd)/monitoring/alertmanager.yml:/etc/alertmanager/config.yml prom/alertmanager
```

## Dashboards

### System Overview Dashboard
- Service health status
- Event ingestion rates
- API response times
- Error rates
- Kafka consumer lag
- Database connections
- Memory and CPU usage

### Fraud Analytics Dashboard
- Fraud score distribution
- High-risk transaction alerts
- Alert rates by category
- False positive rates
- Model performance metrics
- Blocked amounts
- Top fraud patterns
- Geographic fraud distribution

## Metrics

### Application Metrics
- `fraud_detection_events_ingested_total{type, source}`: Events ingested by type
- `fraud_detection_ingestion_errors_total{type, error_type}`: Ingestion errors
- `fraud_detection_request_duration_seconds{method, endpoint}`: API request duration
- `fraud_detection_active_connections`: Active connections

### Business Metrics
- `fraud_score`: Fraud score distribution
- `fraud_alerts_total{category, severity}`: Alert counts
- `fraud_false_positives_total`: False positive alerts
- `fraud_blocked_amount_total`: Amount blocked by fraud detection

### System Metrics
- `process_cpu_user_seconds_total`: CPU usage
- `process_resident_memory_bytes`: Memory usage
- `kafka_consumer_lag`: Kafka consumer lag
- `pg_stat_activity_count`: Database connections

## Alerts

### Critical Alerts
- Service down
- High error rates (>5%)
- Database connection issues
- Critical fraud patterns detected

### Warning Alerts
- High latency (>1s)
- Memory usage >80%
- Disk space low
- Unusual traffic patterns

### Info Alerts
- Service restarts
- Configuration changes
- Maintenance notifications

## Configuration

### Prometheus Configuration
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'fraud-detection'
    static_configs:
      - targets: ['host.docker.internal:8080']
    metrics_path: '/metrics'

  - job_name: 'node'
    static_configs:
      - targets: ['node-exporter:9100']

  - job_name: 'cadvisor'
    static_configs:
      - targets: ['cadvisor:8080']
```

### Alert Rules
```yaml
groups:
  - name: fraud-detection
    rules:
      - alert: HighErrorRate
        expr: rate(fraud_detection_ingestion_errors_total[5m]) / rate(fraud_detection_events_ingested_total[5m]) > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "High error rate detected"
          description: "Error rate is {{ $value }}%"

      - alert: ServiceDown
        expr: up{job="fraud-detection"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Service {{ $labels.job }} is down"
```

## Integration

### Application Integration
Add Prometheus metrics to your application:

```python
from prometheus_client import Counter, Histogram, Gauge
from src.data_ingestion.metrics import MetricsCollector

# Initialize metrics
metrics = MetricsCollector()

# Use in your code
metrics.increment_counter("events_processed_total", {"type": "transaction"})
metrics.observe_histogram("processing_duration", 0.5, {"operation": "validation"})
```

### Docker Integration
Add to your `docker-compose.yml`:

```yaml
services:
  your-service:
    # ... your service config
    labels:
      - "prometheus-job=your-service"
      - "prometheus-port=8080"
```

## Troubleshooting

### Common Issues

1. **Grafana not loading dashboards**
   - Check provisioning configuration
   - Verify dashboard JSON syntax
   - Check Grafana logs: `docker logs fraud-detection-grafana`

2. **Prometheus not scraping metrics**
   - Verify service is exposing `/metrics` endpoint
   - Check network connectivity
   - Review Prometheus targets: `http://localhost:9090/targets`

3. **Alerts not firing**
   - Check alert rules syntax
   - Verify metrics are being collected
   - Review AlertManager configuration

### Useful Commands

```bash
# Check Prometheus health
curl http://localhost:9090/-/healthy

# Check Grafana health
curl http://localhost:3000/api/health

# View Prometheus metrics
curl http://localhost:9090/api/v1/query?query=up

# Reload Prometheus configuration
curl -X POST http://localhost:9090/-/reload

# View AlertManager alerts
curl http://localhost:9093/api/v2/alerts
```

## Security

### Production Considerations
- Change default Grafana password
- Configure TLS/SSL for all services
- Use authentication for Prometheus endpoints
- Implement network segmentation
- Regular security updates

### Access Control
- Grafana: Configure user roles and permissions
- Prometheus: Implement authentication if needed
- AlertManager: Secure webhook endpoints

## Scaling

### Horizontal Scaling
- Run multiple Prometheus instances with federation
- Use Thanos or Cortex for long-term storage
- Implement load balancing for Grafana

### High Availability
- Configure AlertManager clustering
- Use external storage for Grafana and Prometheus
- Implement backup and recovery procedures

## Maintenance

### Backup
```bash
# Backup Grafana data
docker run --rm -v grafana_data:/source -v $(pwd)/backup:/backup alpine tar czf /backup/grafana-$(date +%Y%m%d).tar.gz -C /source .

# Backup Prometheus data
docker run --rm -v prometheus_data:/source -v $(pwd)/backup:/backup alpine tar czf /backup/prometheus-$(date +%Y%m%d).tar.gz -C /source .
```

### Updates
```bash
# Update monitoring stack
docker-compose -f monitoring/docker-compose.monitoring.yml pull
docker-compose -f monitoring/docker-compose.monitoring.yml up -d
```

### Cleanup
```bash
# Remove old metrics data (be careful!)
docker volume rm fraud-detection_prometheus_data
docker volume rm fraud-detection_grafana_data