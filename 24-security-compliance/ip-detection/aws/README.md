# AWS IP Detection Pipeline — 8-Gate iGaming Security Filter

A production-grade AWS-native IP detection and fraud prevention system implementing eight sequential security gates for iGaming platforms. Built for regulatory compliance (UKGC, MGA, Curaçao) and designed to handle millions of requests per day at sub-100ms p99 latency.

---

## Architecture Overview

```
Client Request
      │
      ▼
  CloudFront (optional)
      │
      ▼
  AWS WAF WebACL
  ├─ IP Set block (auto-populated from Gate 4 + post-block actions)
  ├─ AWS Managed Rules (CommonRuleSet, KnownBadInputs)
  └─ Rate limit: 1,000 req/IP/min
      │
      ▼
  API Gateway (HTTP API v2)
  ├─ POST /check   ← main entry point
  └─ GET  /health
      │
      ▼
  Lambda (Python 3.12, 512MB)
  ├─ Gate 1: IP Type Check        → ASN + MaxMind GeoIP2
  ├─ Gate 2: VPN Detection        → IP reputation API (IPQualityScore)
  ├─ Gate 3: Known Proxy Check    → DynamoDB known-proxies table
  ├─ Gate 4: IP Blacklist         → DynamoDB ip-blacklist (TTL)
  ├─ Gate 5: Fraud Score          → Multi-signal composite score
  ├─ Gate 6: Device Fingerprint   → DynamoDB anomaly detection
  ├─ Gate 7: Sanctions / PEP      → OFAC SDN list (S3) + fuzzy match
  └─ Gate 8: KYC Status           → DynamoDB kyc-status table
      │
      ├─ BLOCK → WAF IP Set update + SNS alert + auto-blacklist
      ├─ REVIEW → SNS alert (soft flag, allow with logging)
      └─ PASS  → 200 OK
      │
  DynamoDB (5 tables)          S3 (2 buckets)
  ├─ ip-blacklist              ├─ GeoIP databases (MaxMind .mmdb)
  ├─ device-fingerprints       └─ OFAC SDN XML (daily refresh)
  ├─ kyc-status
  ├─ tor-exit-nodes
  └─ known-proxies

  ElastiCache Redis (optional)
  └─ Velocity counters (ip req/min, accounts/IP/day)

  SNS Topic
  └─ Email/webhook alerts on BLOCK verdicts

  CloudWatch
  ├─ Structured audit logs (compliance trail)
  ├─ Lambda metrics + alarms
  └─ WAF block rate dashboard
```

---

## Gate Definitions

| Gate | Name | Verdict on Hit | Reason Code | Data Source |
|------|------|---------------|-------------|-------------|
| 1 | IP Type Check | BLOCK | `BANNED_PROXY_TOR` | DynamoDB tor-exit-nodes |
| 1 | IP Type Check | BLOCK | `BANNED_PROXY_DC` | MaxMind ASN DB |
| 2 | VPN Detection | BLOCK | `BANNED_PROXY_VPN` | IPQualityScore API |
| 3 | Known Proxy | BLOCK | `BANNED_PROXY_KNOWN` | DynamoDB known-proxies |
| 4 | IP Blacklist | BLOCK | `BANNED_IP_BLACKLIST` | DynamoDB ip-blacklist |
| 5 | Fraud Score | BLOCK | `HIGH_FRAUD_SCORE` | Redis velocity + GeoIP |
| 6 | Device Fingerprint | BLOCK | `DEVICE_ANOMALY` | DynamoDB device-fingerprints |
| 7 | Sanctions / PEP | BLOCK | `SANCTIONS_MATCH` | OFAC SDN S3 + fuzzy match |
| 8 | KYC Status | BLOCK | `KYC_FAILED` | DynamoDB kyc-status |

**Short-circuit**: The pipeline stops at the first BLOCK verdict. REVIEW verdicts accumulate but do not stop execution.

### Datacenter ASNs (Gate 1)

Automatically blocked ASNs:

| Provider | ASN |
|----------|-----|
| DigitalOcean | AS14061 |
| Amazon AWS | AS16509 |
| Google Cloud | AS15169 |
| Microsoft Azure | AS8075 |
| Vultr | AS20473 |
| Linode/Akamai | AS63949 |
| Hetzner | AS24940 |
| OVH | AS16276 |
| Cloudflare | AS13335 |

---

## File Structure

```
aws/
├── lambda_ip_gate.py          # Main Lambda handler (8-gate pipeline)
├── dynamodb_blacklist.py      # DynamoDB IP blacklist service
├── s3_sanctions_checker.py    # OFAC SDN parser + fuzzy matcher
├── device_fingerprint_dynamo.py # Device fingerprint tracker + anomaly detection
├── waf_integration.py         # AWS WAF IP set management
├── cloudformation.yaml        # Full IaC (Lambda, API GW, DynamoDB, S3, WAF, SNS)
├── test_lambda.py             # pytest test suite (all 8 gates)
├── requirements.txt           # Python dependencies
└── README.md                  # This file
```

---

## Deployment

### Prerequisites

- AWS CLI configured with appropriate permissions
- Python 3.12
- An S3 bucket for Lambda artifacts: `igaming-lambda-artifacts-{ACCOUNT_ID}`
- MaxMind GeoLite2 license key (free at maxmind.com)

### Step 1: Bootstrap the artifact bucket

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws s3 mb s3://igaming-lambda-artifacts-${ACCOUNT_ID} --region us-east-1
```

### Step 2: Package the Lambda

```bash
# Install dependencies into a package directory
pip install -r requirements.txt \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --target ./package

# Copy source files
cp lambda_ip_gate.py dynamodb_blacklist.py s3_sanctions_checker.py \
   device_fingerprint_dynamo.py waf_integration.py ./package/

# Create the deployment ZIP
cd package && zip -r9 ../lambda.zip . && cd ..

# Upload to S3
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws s3 cp lambda.zip \
  s3://igaming-lambda-artifacts-${ACCOUNT_ID}/ip-gate/production/lambda.zip
```

### Step 3: Deploy the CloudFormation stack

```bash
aws cloudformation deploy \
  --template-file cloudformation.yaml \
  --stack-name igaming-ip-gate-production \
  --parameter-overrides \
    Environment=production \
    AlertEmail=security@yourdomain.com \
    LambdaMemorySize=512 \
    FraudScoreThreshold=75 \
    FraudReviewThreshold=50 \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

### Step 4: Upload MaxMind GeoIP databases

Download GeoLite2-ASN.mmdb and GeoLite2-City.mmdb from maxmind.com:

```bash
GEOIP_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name igaming-ip-gate-production \
  --query 'Stacks[0].Outputs[?OutputKey==`GeoIPBucketName`].OutputValue' \
  --output text)

aws s3 cp GeoLite2-ASN.mmdb s3://${GEOIP_BUCKET}/GeoLite2-ASN.mmdb
aws s3 cp GeoLite2-City.mmdb s3://${GEOIP_BUCKET}/GeoLite2-City.mmdb
```

### Step 5: Trigger the initial OFAC SDN download

```bash
aws lambda invoke \
  --function-name igaming-sdn-refresh-production \
  --payload '{}' \
  /dev/stdout
```

### Step 6: Verify the deployment

```bash
API_URL=$(aws cloudformation describe-stacks \
  --stack-name igaming-ip-gate-production \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' \
  --output text)

# Test with a clean residential IP
curl -X POST ${API_URL} \
  -H "Content-Type: application/json" \
  -H "X-Forwarded-For: 73.100.1.1" \
  -d '{"user_id": "test_user"}'
```

---

## API Reference

### POST /check

Evaluates an IP address through all 8 security gates.

**Request Headers**:
```
X-Forwarded-For: <client-ip>      # Primary IP source
X-User-ID: <user-id>              # Optional: enables KYC + fingerprint rotation checks
X-Session-ID: <session-id>        # Optional: used for velocity tracking
```

**Request Body** (JSON):
```json
{
  "user_id": "u_12345",
  "session_id": "sess_abc",
  "full_name": "John Doe",
  "date_of_birth": "1980-01-15",
  "nationality": "US",
  "fingerprint": {
    "canvas_hash": "a1b2c3d4...",
    "webgl_hash": "e5f6g7h8...",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
    "screen_resolution": "1920x1080",
    "timezone": "America/New_York",
    "language": "en-US",
    "platform": "Win32",
    "hardware_concurrency": 8,
    "device_memory": 8.0,
    "color_depth": 24,
    "touch_support": false,
    "audio_hash": "...",
    "font_hash": "...",
    "plugins_hash": "..."
  }
}
```

**Response — PASS (200)**:
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "verdict": "PASS",
  "reason_code": null,
  "blocking_gate": null,
  "gates": [
    {"gate_id": 1, "gate_name": "ip_type_check", "verdict": "PASS", "latency_ms": 12.3},
    {"gate_id": 2, "gate_name": "vpn_detection", "verdict": "PASS", "latency_ms": 45.7},
    ...
  ],
  "total_latency_ms": 87.4,
  "timestamp": 1748549200
}
```

**Response — BLOCK (403)**:
```json
{
  "request_id": "...",
  "verdict": "BLOCK",
  "reason_code": "BANNED_PROXY_TOR",
  "blocking_gate": 1,
  "gates": [
    {"gate_id": 1, "gate_name": "ip_type_check", "verdict": "BLOCK",
     "reason_code": "BANNED_PROXY_TOR", "detail": "IP is a known Tor exit node",
     "latency_ms": 2.1}
  ],
  "total_latency_ms": 2.1,
  "timestamp": 1748549200
}
```

**Response — REVIEW (202)**:
```json
{
  "verdict": "REVIEW",
  "reason_code": "HIGH_FRAUD_SCORE",
  "blocking_gate": 5,
  ...
}
```

---

## DynamoDB Table Schemas

### ip-blacklist

| Attribute | Type | Description |
|-----------|------|-------------|
| `ip_address` | String (PK) | IPv4 or IPv6 address |
| `reason` | String | Reason code (GSI partition key) |
| `added_at` | Number | Unix timestamp (GSI sort key) |
| `expires_at` | Number | TTL (auto-deleted by DynamoDB) |
| `added_by` | String | Source system identifier |
| `severity` | String | LOW / MEDIUM / HIGH / PERMANENT |
| `comment` | String | Human-readable note |

GSI: `reason-code-index` (reason → added_at) for bulk queries by reason code.

### device-fingerprints

| Attribute | Type | Description |
|-----------|------|-------------|
| `fingerprint_id` | String (PK) | SHA-256 of stable FP components |
| `seen_at` | Number (SK) | Unix timestamp |
| `user_id` | String | Associated user (GSI partition key) |
| `ip_address` | String | Source IP (GSI partition key) |
| `canvas_hash` | String | Canvas fingerprint |
| `webgl_hash` | String | WebGL fingerprint |
| `anomaly_detected` | Boolean | Whether anomaly was found |
| `anomaly_type` | String | FINGERPRINT_ROTATION / HEADLESS / etc. |
| `expires_at` | Number | TTL (90 days) |

Four GSIs: `user-fp-index`, `ip-fp-index`, `fp-users-index`, `fp-ip-index`.

### kyc-status

| Attribute | Type | Description |
|-----------|------|-------------|
| `user_id` | String (PK) | User identifier |
| `kyc_status` | String | VERIFIED / PENDING / REJECTED / NOT_STARTED |
| `kyc_level` | Number | 1 (basic), 2 (enhanced), 3 (full) |
| `verified_at` | Number | Unix timestamp of verification |
| `expires_at` | Number | Expiry timestamp (TTL) |
| `rejection_reason` | String | Human-readable rejection detail |

---

## Fraud Score Signals (Gate 5)

The fraud score is a composite of four signals, each weighted 0–30 points:

| Signal | Max Points | Condition |
|--------|-----------|-----------|
| IP velocity | 30 | >100 requests in window |
| Multi-account | 20 | >5 accounts from same IP in 24h |
| Country risk | 25 | High-risk jurisdiction (NG, PK, AF, etc.) |
| Reputation | 25 | IP reputation score from Gate 2 |

- **Block threshold**: 75 (configurable via `FRAUD_SCORE_THRESHOLD`)
- **Review threshold**: 50 (configurable via `FRAUD_REVIEW_THRESHOLD`)

---

## Device Fingerprint Anomaly Types (Gate 6)

| Type | Severity | Trigger |
|------|----------|---------|
| `HEADLESS_BROWSER` | HIGH | Blank/known-bot canvas hash, missing plugins on Linux |
| `UA_MISMATCH` | MEDIUM | Windows UA + Linux platform (or similar cross-platform mismatch) |
| `FINGERPRINT_ROTATION` | HIGH | >5 distinct fingerprints from same user in 24h |
| `SHARED_FINGERPRINT` | HIGH | >3 users sharing same fingerprint in 1h |
| `IMPOSSIBLE_TRAVEL` | HIGH | Same fingerprint from different continents within 1h |

---

## Sanctions Matching (Gate 7)

The OFAC SDN list is downloaded daily from treasury.gov and stored in S3. Matching uses a multi-strategy fuzzy approach:

1. **Token-set ratio** — handles word-order variation (e.g. "Smith John" vs "John Smith")
2. **Levenshtein ratio** — handles transliteration variation (e.g. "Mohammed" vs "Mohamad")
3. **DOB corroboration** — +0.05 score bonus when date-of-birth digits match
4. **Nationality corroboration** — +0.02 score bonus when ISO country code matches

| Score | Verdict |
|-------|---------|
| ≥ 0.90 | BLOCK |
| 0.75–0.90 | REVIEW |
| < 0.75 | PASS |

---

## Post-Block Actions

When Gate 4 through 8 produce a BLOCK verdict, three automatic actions fire asynchronously:

1. **WAF IP Set**: The IP is added to the AWS WAF IP set for edge-level blocking on subsequent requests (bypasses Lambda entirely).

2. **Auto-blacklist**: The IP is written to DynamoDB `ip-blacklist` with a reason-code-specific TTL:
   - Tor exit node: 30 days
   - Datacenter: 7 days
   - VPN: 3 days
   - Sanctions match: 1 year

3. **SNS Alert**: A structured notification is published to the security alert SNS topic with full pipeline context.

---

## Running Tests

```bash
pip install -r requirements.txt
pytest test_lambda.py -v --tb=short --cov=. --cov-report=term-missing
```

Test coverage targets:
- All 8 gates: PASS / REVIEW / BLOCK paths
- Short-circuit behaviour (gate 1 Tor block stops at gate 1)
- Handler HTTP status codes (200 / 202 / 400 / 403)
- DynamoDB TTL expiry handling
- Fingerprint rotation detection
- Levenshtein similarity edge cases
- WAF CIDR normalisation

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAXMIND_DB_BUCKET` | `igaming-geoip-databases` | S3 bucket for MaxMind MMDB files |
| `BLACKLIST_TABLE` | `ip-blacklist` | DynamoDB table name |
| `DEVICE_FP_TABLE` | `device-fingerprints` | DynamoDB table name |
| `KYC_TABLE` | `kyc-status` | DynamoDB table name |
| `SDN_BUCKET` | `igaming-sanctions` | S3 bucket for OFAC SDN XML |
| `SDN_KEY` | `ofac/sdn_advanced.xml` | S3 key for SDN file |
| `SNS_ALERT_TOPIC` | — | SNS topic ARN for alerts |
| `WAF_IP_SET_ID` | — | WAF IP Set ID (from stack output) |
| `WAF_IP_SET_SCOPE` | `REGIONAL` | `REGIONAL` or `CLOUDFRONT` |
| `FRAUD_SCORE_THRESHOLD` | `75.0` | Fraud score block threshold |
| `FRAUD_REVIEW_THRESHOLD` | `50.0` | Fraud score review threshold |
| `ELASTICACHE_ENDPOINT` | — | Redis endpoint `host:port` |
| `IP_REPUTATION_API_KEY` | — | IPQualityScore API key |
| `VELOCITY_WINDOW_SECONDS` | `300` | Velocity tracking window |

---

## Operational Notes

### Cold Start Latency

On Lambda cold start, the function downloads MaxMind databases (~15MB) from S3 to `/tmp`. This adds ~500ms to the first invocation. Subsequent warm invocations reuse the loaded readers with negligible overhead.

To eliminate cold start latency for the GeoIP databases:
- Use a Lambda layer pre-packaged with the MMDB files
- Enable Lambda SnapStart (Java runtimes) or Provisioned Concurrency

### WAF Optimistic Locking

AWS WAF UpdateIPSet requires a lock token. The `WAFIntegration` service implements automatic retry with exponential backoff (up to 3 attempts) on `WAFOptimisticLockException`. Concurrent Lambda invocations adding different IPs simultaneously will serialise correctly.

### OFAC SDN Refresh

The `SDNRefreshLambda` runs daily at 02:00 UTC via EventBridge. In-memory SDN indexes in warm Lambda containers automatically refresh after 24 hours. To force an immediate refresh:

```bash
aws lambda invoke \
  --function-name igaming-sdn-refresh-production \
  --payload '{}' \
  response.json
```

### DynamoDB TTL Lag

DynamoDB TTL deletion is eventually consistent with up to 48 hours of lag. The `IPBlacklistService.get()` method includes a runtime expiry check to guard against serving stale blacklist entries during the lag window.

### Compliance Logging

Every pipeline decision emits a structured CloudWatch log entry with:
- `request_id`, `ip_address`, `user_id`, `session_id`
- `verdict`, `reason_code`, `blocking_gate`
- `total_latency_ms`, `gates_evaluated`, `timestamp`

Log retention is 365 days in production (configurable). These logs constitute the audit trail required by UKGC's Technical Standards and MGA's AML directives.
