# Operational Runbooks

## Overview

This document provides operational runbooks for common procedures, incident response, maintenance tasks, and troubleshooting guides for the Fraud Detection System.

## Daily Operations

### Morning Health Check (9:00 AM)

**Objective:** Verify system health and identify any issues requiring immediate attention.

**Procedure:**

1. **Access Monitoring Dashboard**
   ```bash
   # Open Grafana dashboard
   open https://monitoring.fraud-detection.com
   ```

2. **Check System Components**
   - [ ] All services show green status
   - [ ] Database connections are healthy
   - [ ] Message queues have normal depths
   - [ ] External API integrations are responding

3. **Review Key Metrics**
   ```sql
   -- Check system health metrics
   SELECT
     service_name,
     status,
     response_time_p95,
     error_rate,
     last_check
   FROM system_health_checks
   WHERE check_time >= CURRENT_TIMESTAMP - INTERVAL '1 hour'
   ORDER BY check_time DESC;
   ```

4. **Verify Data Ingestion**
   - [ ] Transaction ingestion rate is within normal range (1000-5000 txn/min)
   - [ ] No data backlog in Kafka topics
   - [ ] Feature engineering pipeline is processing data

5. **Check Alert Status**
   - [ ] Review overnight alerts
   - [ ] Verify alert response times (< 15 min for critical)
   - [ ] Confirm no stuck alerts in queue

6. **Performance Verification**
   - [ ] CPU usage < 80%
   - [ ] Memory usage < 85%
   - [ ] Disk space > 20% free
   - [ ] Network latency < 50ms

**Escalation Criteria:**
- Any service showing red status
- Data ingestion stopped or significantly delayed
- Critical alerts unacknowledged > 15 minutes
- System performance degraded > 20%

### Midday Operations Check (12:00 PM)

**Objective:** Monitor ongoing operations and handle any emerging issues.

**Procedure:**

1. **Performance Monitoring**
   ```bash
   # Check current system load
   kubectl top pods -n fraud-detection
   kubectl top nodes
   ```

2. **Queue Monitoring**
   ```bash
   # Check Kafka topic lag
   kafka-consumer-groups --bootstrap-server kafka-cluster:9092 \
     --group fraud-detection-consumer \
     --describe
   ```

3. **Alert Response Verification**
   - [ ] All critical alerts acknowledged within 5 minutes
   - [ ] High priority alerts addressed within 15 minutes
   - [ ] Investigation cases created for suspicious activities

4. **Business Metrics Review**
   - [ ] Fraud detection rate within expected range
   - [ ] False positive rate < 5%
   - [ ] System throughput meeting requirements

### Evening Operations Summary (5:00 PM)

**Objective:** Prepare end-of-day reports and ensure system readiness for overnight processing.

**Procedure:**

1. **Generate Daily Reports**
   ```bash
   # Run daily reporting job
   kubectl create job daily-report-$(date +%Y%m%d) \
     --from=cronjob/daily-reporting-job \
     -n fraud-detection
   ```

2. **Backup Verification**
   ```bash
   # Check backup status
   velero backup get
   # Verify latest backup completed successfully
   velero backup logs <backup-name> | tail -20
   ```

3. **Security Scan Review**
   ```bash
   # Check security scan results
   kubectl logs -l app=security-scanner -n fraud-detection --tail=50
   ```

4. **Capacity Planning**
   - [ ] Review resource usage trends
   - [ ] Identify potential capacity issues
   - [ ] Plan for upcoming high-load periods

## Incident Response

### Critical Incident Response (P0)

**Definition:** System completely down or major fraud detection failure.

**Immediate Actions (First 5 minutes):**
1. **Acknowledge Incident**
   ```bash
   # Acknowledge in incident management system
   curl -X POST https://incident-api.company.com/acknowledge \
     -H "Authorization: Bearer $API_TOKEN" \
     -d '{"incident_id": "'$INCIDENT_ID'", "responder": "'$USER'"}'
   ```

2. **Assess Impact**
   - Determine affected services
   - Estimate user impact
   - Check if fraud detection is compromised

3. **Initial Containment**
   ```bash
   # Scale down affected services to prevent further damage
   kubectl scale deployment affected-service --replicas=0 -n fraud-detection
   ```

4. **Notify Stakeholders**
   - Alert executive team
   - Notify customer success
   - Update status page

**Investigation (5-30 minutes):**
1. **Gather Evidence**
   ```bash
   # Collect logs from affected services
   kubectl logs -l app=affected-service -n fraud-detection \
     --since=1h > incident_logs_$(date +%s).log
   ```

2. **Check Monitoring**
   - Review Grafana dashboards for leading indicators
   - Check system metrics before incident
   - Analyze error patterns

3. **Database Investigation**
   ```sql
   -- Check recent errors
   SELECT
     timestamp,
     error_message,
     service_name,
     severity
   FROM error_logs
   WHERE timestamp >= CURRENT_TIMESTAMP - INTERVAL '1 hour'
   ORDER BY timestamp DESC
   LIMIT 50;
   ```

**Resolution (30-120 minutes):**
1. **Implement Fix**
   ```bash
   # Deploy hotfix or rollback
   kubectl set image deployment/affected-service \
     app=affected-service:new-fixed-version -n fraud-detection
   ```

2. **Verify Fix**
   ```bash
   # Run health checks
   kubectl exec -it health-check-pod -n fraud-detection -- ./health-check.sh
   ```

3. **Gradual Rollout**
   ```bash
   # Scale back up gradually
   kubectl scale deployment affected-service --replicas=1 -n fraud-detection
   # Wait and verify
   sleep 300
   kubectl scale deployment affected-service --replicas=3 -n fraud-detection
   ```

**Post-Incident (2-24 hours):**
1. **Root Cause Analysis**
   - Document timeline
   - Identify root cause
   - Determine preventive measures

2. **Communication**
   - Send incident report to stakeholders
   - Update knowledge base
   - Schedule post-mortem meeting

### High Priority Incident Response (P1)

**Definition:** Significant degradation or single component failure.

**Response Timeline:** Resolution within 1 hour.

**Procedure:**
1. **Triage and Assessment (10 minutes)**
   - Determine exact impact
   - Check if workaround available
   - Assess customer impact

2. **Containment (20 minutes)**
   ```bash
   # Restart affected service
   kubectl rollout restart deployment/affected-service -n fraud-detection
   ```

3. **Investigation (20 minutes)**
   - Review logs and metrics
   - Check for patterns or triggers
   - Consult runbooks for similar incidents

4. **Resolution (10 minutes)**
   - Apply known fix or workaround
   - Verify system recovery
   - Monitor for 15 minutes

### Medium Priority Incident Response (P2)

**Definition:** Minor issues or performance degradation.

**Response Timeline:** Resolution within 4 hours.

**Procedure:**
1. **Assessment and Planning**
   - Gather information about the issue
   - Determine best resolution approach
   - Schedule fix during low-traffic period if needed

2. **Implementation**
   - Apply fix or configuration change
   - Test in staging environment first
   - Deploy to production with monitoring

3. **Verification**
   - Confirm issue is resolved
   - Monitor for side effects
   - Document changes made

## Maintenance Procedures

### Weekly Maintenance

**Schedule:** Every Sunday 2:00 AM - 4:00 AM

**Pre-Maintenance:**
```bash
# Notify team of maintenance window
curl -X POST https://slack-webhook.com \
  -H 'Content-type: application/json' \
  -d '{"text": "Starting weekly maintenance - system may be slow"}'
```

**Database Maintenance:**
```sql
-- Vacuum and analyze tables
VACUUM ANALYZE fraud_detection.transactions;
VACUUM ANALYZE fraud_detection.player_features;
VACUUM ANALYZE fraud_detection.alerts;

-- Update statistics
ANALYZE fraud_detection.transactions;
ANALYZE fraud_detection.player_features;
```

**Log Rotation:**
```bash
# Rotate application logs
kubectl exec -it log-rotator-pod -n fraud-detection -- ./rotate-logs.sh

# Archive old logs to S3
aws s3 sync /logs/archive/ s3://fraud-detection-logs/archive/$(date +%Y/%m)/
```

**Certificate Renewal:**
```bash
# Check certificate expiry
openssl x509 -enddate -noout -in /etc/ssl/certs/fraud-detection.crt

# Renew if needed
certbot renew --cert-name fraud-detection.company.com
kubectl rollout restart deployment ingress-controller -n ingress-nginx
```

**Post-Maintenance:**
```bash
# Verify system health
curl -f https://fraud-detection.company.com/health

# Notify completion
curl -X POST https://slack-webhook.com \
  -H 'Content-type: application/json' \
  -d '{"text": "Weekly maintenance completed successfully"}'
```

### Monthly Maintenance

**Schedule:** First Sunday of month 1:00 AM - 3:00 AM

**Backup Verification:**
```bash
# Test backup restoration
velero restore create test-restore \
  --from-backup $(velero backup get -o json | jq -r '.items[0].metadata.name') \
  --include-namespaces fraud-detection

# Wait for restore completion
kubectl wait --for=condition=completed job/test-restore -n velero --timeout=1800s

# Verify restored data
kubectl exec -it test-db-pod -n fraud-detection -- psql -c "SELECT COUNT(*) FROM transactions;"

# Clean up test restore
velero restore delete test-restore
```

**Security Updates:**
```bash
# Update base images
kubectl set image deployment/fraud-detection-app \
  app=fraud-detection-app:new-secure-image -n fraud-detection

# Update dependencies
pip install --upgrade -r requirements.txt
npm audit fix
```

**Performance Optimization:**
```bash
# Analyze slow queries
kubectl exec -it db-pod -n fraud-detection -- psql -c "
SELECT
  query,
  calls,
  total_time,
  mean_time,
  rows
FROM pg_stat_statements
ORDER BY total_time DESC
LIMIT 10;
"
```

### Quarterly Maintenance

**Schedule:** First Sunday of quarter 12:00 AM - 6:00 AM

**Major Updates:**
- Operating system updates
- Major dependency upgrades
- Database schema changes
- Infrastructure migrations

**Disaster Recovery Testing:**
```bash
# Full DR test procedure
./run-dr-test.sh
```

**Capacity Planning:**
- Review resource utilization trends
- Plan infrastructure scaling
- Update monitoring thresholds

## Troubleshooting Guides

### High CPU Usage

**Symptoms:**
- CPU usage > 85%
- Slow response times
- Alert processing delays

**Diagnosis:**
```bash
# Check CPU usage by pod
kubectl top pods -n fraud-detection --sort-by=cpu

# Check specific container CPU
kubectl exec -it pod-name -n fraud-detection -- top -b -n 1

# Check application metrics
curl http://localhost:9090/metrics | grep cpu_usage
```

**Common Causes:**
1. **Memory Pressure**
   ```bash
   # Check memory usage
   kubectl top pods -n fraud-detection --sort-by=memory
   # Solution: Increase memory limits or optimize memory usage
   ```

2. **Inefficient Queries**
   ```sql
   -- Find slow queries
   SELECT query, mean_time, calls
   FROM pg_stat_statements
   WHERE mean_time > 1000
   ORDER BY mean_time DESC;
   ```

3. **High Request Volume**
   ```bash
   # Check request rates
   kubectl logs -l app=ingress-controller -n ingress-nginx | grep "POST /api"
   ```

**Solutions:**
```bash
# Scale horizontally
kubectl scale deployment model-serving --replicas=5 -n fraud-detection

# Optimize resource limits
kubectl patch deployment model-serving -n fraud-detection \
  --type='json' -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/resources/limits/cpu", "value": "2000m"}]'
```

### Data Ingestion Lag

**Symptoms:**
- Increasing lag in Kafka topics
- Delayed feature updates
- Stale fraud predictions

**Diagnosis:**
```bash
# Check consumer lag
kafka-consumer-groups --bootstrap-server kafka-cluster:9092 \
  --group fraud-detection-consumer \
  --describe

# Check pod status
kubectl get pods -l app=data-ingestion -n fraud-detection

# Check logs for errors
kubectl logs -l app=data-ingestion -n fraud-detection --tail=100
```

**Common Causes:**
1. **Consumer Group Issues**
   ```bash
   # Restart consumer group
   kubectl rollout restart deployment data-ingestion-consumer -n fraud-detection
   ```

2. **Database Bottleneck**
   ```sql
   -- Check database connections
   SELECT count(*) FROM pg_stat_activity WHERE state = 'active';
   ```

3. **Network Issues**
   ```bash
   # Test network connectivity
   kubectl exec -it data-ingestion-pod -n fraud-detection -- ping database-service
   ```

### Alert Flood

**Symptoms:**
- Hundreds of alerts generated per minute
- Alert queue growing rapidly
- Analyst fatigue and missed alerts

**Diagnosis:**
```sql
-- Check alert generation rates
SELECT
  DATE_TRUNC('minute', created_at) as minute,
  COUNT(*) as alert_count,
  AVG(severity_score) as avg_severity
FROM alerts
WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '1 hour'
GROUP BY DATE_TRUNC('minute', created_at)
ORDER BY minute DESC;
```

**Common Causes:**
1. **Misconfigured Alert Rules**
   ```sql
   -- Check recently modified rules
   SELECT rule_id, name, condition, modified_at
   FROM alert_rules
   WHERE modified_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours';
   ```

2. **Sudden Behavior Change**
   ```sql
   -- Check for unusual patterns
   SELECT
     player_segment,
     COUNT(*) as transaction_count,
     AVG(fraud_score) as avg_fraud_score
   FROM transactions t
   JOIN player_features pf ON t.player_id = pf.player_id
   WHERE t.created_at >= CURRENT_TIMESTAMP - INTERVAL '1 hour'
   GROUP BY player_segment;
   ```

**Solutions:**
```sql
-- Temporarily disable problematic rule
UPDATE alert_rules
SET enabled = false
WHERE rule_id = 'problematic_rule_id';

-- Adjust thresholds
UPDATE alert_rules
SET threshold = threshold * 1.5
WHERE rule_id = 'high_volume_rule';
```

### Model Performance Degradation

**Symptoms:**
- Increasing false positive/negative rates
- Model accuracy dropping
- Alert quality declining

**Diagnosis:**
```python
# Check model metrics
from mlflow.tracking import MlflowClient
client = MlflowClient()

# Get latest model metrics
runs = client.search_runs(
    experiment_ids=["fraud_detection_experiment"],
    filter_string="attributes.status = 'FINISHED'",
    order_by=["attributes.start_time DESC"],
    max_results=5
)

for run in runs:
    metrics = run.data.metrics
    print(f"Run {run.info.run_id}: accuracy={metrics.get('accuracy', 'N/A')}")
```

**Common Causes:**
1. **Data Drift**
   ```sql
   -- Check for feature distribution changes
   SELECT
     feature_name,
     AVG(value) as current_avg,
     STDDEV(value) as current_std
   FROM feature_values
   WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '1 day'
   GROUP BY feature_name;
   ```

2. **Concept Drift**
   ```sql
   -- Compare fraud rates over time
   SELECT
     DATE_TRUNC('day', created_at) as day,
     COUNT(*) as total_transactions,
     SUM(CASE WHEN fraud_score > 0.8 THEN 1 ELSE 0 END) as high_risk_count,
     AVG(fraud_score) as avg_fraud_score
   FROM transactions
   WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
   GROUP BY DATE_TRUNC('day', created_at)
   ORDER BY day;
   ```

**Solutions:**
```bash
# Trigger model retraining
kubectl create job model-retraining-$(date +%s) \
  --from=cronjob/model-retraining-job \
  -n fraud-detection

# Update model in production
kubectl set image deployment model-serving \
  model-serving=new-model-version -n fraud-detection
```

## Emergency Contacts

**24/7 On-Call Engineers:**
- Primary: +1-555-0101 (John Smith)
- Secondary: +1-555-0102 (Jane Doe)
- Tertiary: +1-555-0103 (Bob Johnson)

**Management Contacts:**
- Engineering Manager: +1-555-0201
- Product Manager: +1-555-0202
- Executive Team: +1-555-0301

**Vendor Contacts:**
- AWS Support: 1-888-280-4331
- Databricks Support: 1-866-330-0121
- Kafka Support: support@confluent.io

## Runbook Updates

**Update Procedure:**
1. Propose changes via GitHub pull request
2. Review by at least 2 team members
3. Test procedures in staging environment
4. Update date and version in document header
5. Notify team of changes via Slack

**Version History:**
- v2.1.0 - Added quarterly maintenance procedures
- v2.0.0 - Major rewrite with new incident response framework
- v1.5.0 - Added performance troubleshooting guides
- v1.0.0 - Initial operational runbooks