# AWS DDoS Detection & Campaign-Aware Autoscaling

AWS-native system for iGaming platforms that detects DDoS attacks, pre-scales
resources for marketing campaigns, and automates the full incident response
lifecycle — from WAF IP blocking through post-attack abuse reporting.

## Architecture

```
CloudWatch Alarms
  (ALB RequestCount, 5xx rate, connection spike, WAF block rate)
           │
           ▼ EventBridge
  ┌─────────────────────┐
  │  ddos_detector      │◄── Shield Advanced indicators
  │  _lambda.py         │◄── Athena geo distribution (CloudFront logs)
  │                     │◄── DynamoDB marketing calendar
  └──────────┬──────────┘
             │ classification: ATTACK / CAMPAIGN / ORGANIC
             ├─── ATTACK ──► waf_shield_manager.py
             │                  ├── Block IPs in WAF IP sets (10K limit + overflow)
             │                  ├── Enable Shield Advanced
             │                  ├── Create rate-based WAF rules
             │                  └── CloudFront emergency cache mode
             │
             ├─── CAMPAIGN ──► campaign_autoscaler.py
             │                   ├── Pre-scale ASG (small 2x / medium 5x / large 10x / mega 20x)
             │                   ├── Scale ECS service desired count
             │                   ├── Add ElastiCache replicas (≥5x only)
             │                   └── CloudFront cache invalidation (edge warm-up)
             │
             └─── POST-ATTACK ──► attack_evidence_collector.py
                                    ├── Athena WAF log query
                                    ├── MaxMind ASN resolution
                                    ├── Group by ASN → abuse reports
                                    ├── Save JSON + CSV + .txt to S3
                                    └── Optional SES abuse report delivery
```

## Files

| File | Purpose |
|------|---------|
| `ddos_detector_lambda.py` | Traffic classifier triggered by CloudWatch alarms |
| `campaign_autoscaler.py` | Pre-scale ASG/ECS/ElastiCache for marketing campaigns |
| `waf_shield_manager.py` | WAF IP blocking, Shield Advanced, emergency CloudFront mode |
| `attack_evidence_collector.py` | Post-attack forensics and ISP abuse reporting |
| `cloudformation.yaml` | Complete IaC — all resources, IAM roles, alarms, rules |
| `test_detector.py` | moto-based unit and integration tests |
| `requirements.txt` | Python dependencies |

## Classification Logic

The detector combines five signals with weighted scoring:

| Signal | ATTACK indicator | CAMPAIGN indicator |
|--------|-----------------|-------------------|
| WAF block rate | ≥20% → attack | <2% → clean |
| Shield Advanced | Detected → strong attack | — |
| 5xx error rate | ≥5% → overloaded | — |
| Geo concentration | ≥80% single prefix + no campaign → attack | ≥80% matches campaign geo → campaign |
| DynamoDB calendar | No active campaign | Active campaign found |

Final classification is the signal with the highest normalised probability score.

## Scale Profiles

| Profile | Multiplier | Use case |
|---------|-----------|----------|
| `small` | 2x | Email newsletters, small influencer posts |
| `medium` | 5x | TV ads, mid-tier affiliate bursts |
| `large` | 10x | National TV, major sports events |
| `mega` | 20x | World Cup, Super Bowl, IPL Final |

## Prerequisites

### DynamoDB Marketing Calendar

Insert campaigns before they start:

```python
import boto3
from datetime import datetime, timedelta, timezone

ddb = boto3.resource("dynamodb")
table = ddb.Table("marketing-campaign-calendar-prod")

now = datetime.now(timezone.utc)
table.put_item(Item={
    "campaign_id":        "wc2026-final",
    "start_time":         now.isoformat(),
    "end_time":           (now + timedelta(hours=3)).isoformat(),
    "campaign_name":      "World Cup 2026 Final",
    "target_geo":         "GRU",          # São Paulo CloudFront edge prefix
    "expected_multiplier": "20.0",
    "status":             "ACTIVE",
})
```

### MaxMind Database

Download `GeoLite2-ASN.mmdb` from [maxmind.com](https://dev.maxmind.com/geoip/geolite2-free-geolocation-data)
and package it as a Lambda layer:

```bash
mkdir -p layer/opt
cp GeoLite2-ASN.mmdb layer/opt/
cd layer && zip -r ../maxmind-layer.zip opt/
aws lambda publish-layer-version \
    --layer-name maxmind-asn \
    --zip-file fileb://../maxmind-layer.zip \
    --compatible-runtimes python3.12
```

Set `MAXMIND_DB_PATH=/opt/GeoLite2-ASN.mmdb` in the evidence collector Lambda.

## Deployment

### 1. Package Lambda functions

```bash
# Each module is its own ZIP (dependencies are in a separate layer)
for module in ddos_detector_lambda campaign_autoscaler waf_shield_manager attack_evidence_collector; do
    zip "${module}.zip" "${module}.py"
done

# Upload to S3
BUCKET=my-lambda-code-bucket
aws s3 sync . s3://${BUCKET}/v1.0/ --exclude "*" \
    --include "*.zip"
```

### 2. Deploy CloudFormation stack

```bash
aws cloudformation deploy \
    --template-file cloudformation.yaml \
    --stack-name casino-ddos-protection-prod \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
        Environment=prod \
        ALBFullName="app/casino-prod-alb/0123456789abcdef" \
        CloudFrontDistributionId="E1ABCDEF123456" \
        ASGNames="casino-app-asg,casino-api-asg" \
        ECSServices="casino-prod:casino-app,casino-prod:casino-worker" \
        ElastiCacheGroups="casino-session-cache,casino-odds-cache" \
        AthenaDatabase="cloudfront_logs" \
        AthenaResultsBucket="my-athena-results-bucket" \
        EvidenceBucket="my-attack-evidence-bucket" \
        LambdaCodeBucket="my-lambda-code-bucket" \
        LambdaCodeVersion="v1.0"
```

### 3. Associate WAF WebACL with CloudFront

After deployment, note the `WAFWebACLArn` from CloudFormation outputs, then
associate it with the CloudFront distribution:

```bash
WAF_ARN=$(aws cloudformation describe-stacks \
    --stack-name casino-ddos-protection-prod \
    --query "Stacks[0].Outputs[?OutputKey=='WAFWebACLArn'].OutputValue" \
    --output text)

aws cloudfront associate-web-acl \
    --web-acl-id "${WAF_ARN}" \
    --distribution-id "E1ABCDEF123456"
```

## Running Tests

```bash
pip install -r requirements.txt
pytest test_detector.py -v --tb=short

# With coverage
pytest test_detector.py -v --cov=. --cov-report=term-missing
```

Tests use [moto](https://docs.getmoto.org) to mock all AWS API calls — no real
AWS credentials or resources are required.

## Environment Variables Reference

### ddos_detector_lambda.py

| Variable | Default | Description |
|----------|---------|-------------|
| `ALB_FULL_NAME` | `app/casino-prod-alb/...` | ALB resource path for CloudWatch dimensions |
| `CLOUDFRONT_DIST_ID` | — | CloudFront distribution ID |
| `WAF_WEB_ACL_ARN` | — | WAF WebACL ARN (for block rate metric dimensions) |
| `SHIELD_PROTECTION_ID` | — | Shield protection ID (leave blank to skip) |
| `DYNAMO_CALENDAR_TABLE` | `marketing-campaign-calendar` | Marketing calendar DynamoDB table |
| `ATHENA_DATABASE` | `cloudfront_logs` | Athena database for geo queries |
| `ATHENA_RESULTS_BUCKET` | — | S3 bucket for Athena results |
| `SNS_NOC_TOPIC_ARN` | — | SNS topic for NOC alerts |
| `LOOKBACK_MINUTES` | `5` | Metric lookback window |
| `WAF_BLOCK_RATE_ATTACK_THRESHOLD` | `0.20` | WAF block rate above which ATTACK is suspected |

### campaign_autoscaler.py

| Variable | Default | Description |
|----------|---------|-------------|
| `ASG_NAMES` | `casino-app-asg,casino-api-asg` | Comma-separated ASG names |
| `ECS_SERVICES` | `casino-prod:casino-app,...` | cluster:service pairs |
| `ELASTICACHE_GROUPS` | — | Replication group IDs |
| `WARMUP_LEAD_MINUTES` | `15` | How far ahead to pre-scale |
| `SCALEDOWN_GRACE_MINUTES` | `30` | Grace period after campaign end |
| `ASG_BASELINE_CAPACITIES` | `casino-app-asg=4,...` | Baseline instance counts |

### waf_shield_manager.py

| Variable | Default | Description |
|----------|---------|-------------|
| `WAF_SCOPE` | `CLOUDFRONT` | `CLOUDFRONT` or `REGIONAL` |
| `WAF_WEB_ACL_ID` | — | WebACL ID for dynamic rule creation |
| `WAF_PRIMARY_IP_SET_ID` | — | Primary WAF IP set ID |
| `EMERGENCY_RATE_LIMIT` | `2000` | Requests per 5 min for emergency rate rule |

### attack_evidence_collector.py

| Variable | Default | Description |
|----------|---------|-------------|
| `ATHENA_WAF_TABLE` | `waf_blocked_requests` | WAF logs Athena table |
| `EVIDENCE_BUCKET` | — | S3 bucket for evidence files |
| `MAXMIND_DB_PATH` | `/opt/GeoLite2-ASN.mmdb` | Path to MaxMind MMDB file |
| `SES_FROM_ADDRESS` | — | Verified SES sender address |
| `MAX_IPS_PER_ASN_IN_REPORT` | `200` | Maximum IPs in each abuse report |

## WAF IP Set Capacity

AWS WAFv2 enforces a hard limit of 10,000 CIDR entries per IP set. The
`waf_shield_manager` handles this transparently:

1. Primary set (`ddos-block-primary`): first 9,900 IPs
2. On overflow: creates `ddos-block-overflow-001`, `-002`, etc. automatically
3. Each overflow set is automatically added to the WebACL block rule via
   a separate IP set reference statement (requires updating the WebACL after
   overflow set creation — add the new set to the DDoSIPBlockPrimary rule)

For attacks exceeding 100,000 unique source IPs, consider AWS Shield Advanced
with automatic DDoS mitigation, which handles blocking at the network layer
without WAF IP set limits.
