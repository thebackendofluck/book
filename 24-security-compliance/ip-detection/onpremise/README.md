# IP Detection Pipeline — On-Premises Deployment

Chapter 24 companion code.  Implements the complete 8-gate iGaming security
flowchart as a FastAPI middleware stack with Redis state, MaxMind GeoIP2,
OFAC sanctions checking, and JA3-based device fingerprinting.

---

## Architecture Overview

```
Player Request
     │
     ▼
nginx (reverse proxy)
  - Sets X-Real-IP, X-Forwarded-For
  - Sets X-JA3 via nginx-ja3 module
     │
     ▼
FastAPI (ip_detection_pipeline.py)
     │
     ├── Gate 1: IP Type Check     (MaxMind ASN + Tor exit list in Redis)
     ├── Gate 2: VPN Detection     (proxycheck.io + Redis reputation cache)
     ├── Gate 3: Known Proxy       (datacenter ASN list + proxy IP Redis SET)
     ├── Gate 4: IP Blacklist      (Redis sorted-set abuse/ban DB)
     ├── Gate 5: Fraud Score       (velocity counters + amount anomaly in Redis)
     ├── Gate 6: Device Fingerprint (JA3 + browser hash + Redis history)
     ├── Gate 7: Sanctions/PEP     (OFAC SDN XML → Redis token index + fuzzywuzzy)
     └── Gate 8: KYC Status        (internal KYC service + Redis 5-min cache)
          │
          ▼
     BLOCK (HTTP 403)  /  REVIEW (pass-through + X-Security-Review header)  /  PASS
```

### Decision Flow

Each gate returns one of three verdicts:

| Verdict | HTTP | Action |
|---------|------|--------|
| BLOCK   | 403  | JSON body with `reason`, `detail`, `gate`, `request_id` |
| REVIEW  | pass | Adds `X-Security-Review` header; downstream can act on it |
| PASS    | pass | No modification |

The pipeline short-circuits on the first BLOCK.  All REVIEW flags accumulate
even if a later gate blocks.

### Reason Codes (match flowchart spec)

| Code                    | Gate | Trigger |
|-------------------------|------|---------|
| `BANNED_PROXY_TOR`      | 1    | IP in Tor exit node list |
| `BANNED_PROXY_DC`       | 1    | IP ASN is a datacenter provider |
| `BANNED_PROXY_VPN`      | 2    | VPN confirmed by proxycheck.io or reputation list |
| `BANNED_PROXY_KNOWN`    | 3    | IP or ASN in known proxy database |
| `BANNED_IP_BLACKLIST`   | 4    | IP in Redis abuse/ban database |
| `HIGH_FRAUD_SCORE`      | 5    | Multi-signal fraud score >= threshold |
| `DEVICE_ANOMALY`        | 6    | Headless JA3 or rapid device switching |
| `SANCTIONS_MATCH`       | 7    | OFAC SDN fuzzy match score >= 85% |
| `KYC_REQUIRED`          | 8    | Player KYC not completed |
| `KYC_SUSPENDED`         | 8    | Player account under compliance hold |

---

## File Structure

```
onpremise/
├── ip_detection_pipeline.py    Main FastAPI app + 8-gate middleware
├── ip_blacklist_service.py     Redis-backed IP blacklist (CRUD + AbuseIPDB import)
├── sanctions_checker.py        OFAC SDN downloader + fuzzy name matching
├── device_fingerprint.py       JA3 + browser fingerprint tracker
├── test_pipeline.py            pytest suite (60+ tests, no infrastructure needed)
├── requirements.txt            Python dependencies
└── README.md                   This file
```

---

## Infrastructure Requirements

### Runtime

- Python 3.12+
- Redis 7.x (single instance or Sentinel; Cluster supported with minor changes)
- MaxMind GeoLite2 databases (free with registration):
  - `GeoLite2-ASN.mmdb`  — ASN lookups (Gates 1, 3)
  - `GeoLite2-City.mmdb` — City/country (optional; used for enrichment)

### nginx Configuration

The pipeline expects the following headers to be set by nginx:

```nginx
# /etc/nginx/conf.d/ja3.conf

# JA3 fingerprint (requires ngx_http_ssl_fingerprint or similar module)
proxy_set_header X-JA3         $ssl_ja3;
proxy_set_header X-Real-IP     $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

# Browser signals (set from cookies/JS payload by application layer)
proxy_set_header X-Canvas-Hash    $http_x_canvas_hash;
proxy_set_header X-TZ-Offset      $http_x_tz_offset;
proxy_set_header X-Screen-Res     $http_x_screen_res;
proxy_set_header X-Platform       $http_x_platform;
proxy_set_header X-Plugins-Hash   $http_x_plugins_hash;
proxy_set_header X-WebGL-Vendor   $http_x_webgl_vendor;
proxy_set_header X-WebGL-Renderer $http_x_webgl_renderer;

# Authentication layer sets these after JWT validation
proxy_set_header X-Player-ID   $http_x_player_id;
proxy_set_header X-Player-Name $http_x_player_name;
proxy_set_header X-Session-ID  $http_x_session_id;
```

---

## Configuration

All configuration is via environment variables.  No secrets in code.

| Variable               | Default                                   | Description |
|------------------------|-------------------------------------------|-------------|
| `REDIS_URL`            | `redis://localhost:6379/0`                | Redis connection string |
| `MAXMIND_DB_PATH`      | `/var/lib/GeoIP/GeoLite2-ASN.mmdb`        | ASN database path |
| `MAXMIND_CITY_DB_PATH` | `/var/lib/GeoIP/GeoLite2-City.mmdb`       | City database path |
| `PROXYCHECK_API_KEY`   | _(empty — disables live VPN checks)_      | proxycheck.io API key |
| `PIPELINE_ENV`         | `production`                              | `production` or `staging` |
| `FRAUD_SCORE_THRESHOLD`| `75`                                      | Gate 5 block threshold (0-100) |
| `FRAUD_SCORE_REVIEW`   | `50`                                      | Gate 5 review threshold (0-100) |
| `KYC_SERVICE_URL`      | `http://kyc-service:8080`                 | Internal KYC service base URL |
| `KYC_SERVICE_TOKEN`    | _(empty)_                                 | Bearer token for KYC service |
| `OFAC_SDN_URL`         | `https://www.treasury.gov/ofac/downloads/sdn.xml` | OFAC SDN list URL |
| `OFAC_REFRESH_HOURS`   | `24`                                      | How often to re-download SDN list |
| `OFAC_FUZZY_THRESHOLD` | `85`                                      | Sanctions fuzzy match threshold (0-100) |
| `FP_RAPID_SWITCH_THRESHOLD` | `3`                                  | Max distinct FPs in window before alert |
| `FP_RAPID_WINDOW_SECONDS`   | `300`                                | Rapid-switch detection window (seconds) |

---

## Running

### Development

```bash
pip install -r requirements.txt

# Start Redis (Docker)
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Download MaxMind GeoLite2 databases
# (register at https://www.maxmind.com/en/geolite2/signup)
sudo mkdir -p /var/lib/GeoIP
sudo cp GeoLite2-ASN.mmdb GeoLite2-City.mmdb /var/lib/GeoIP/

# Seed Tor exit node list (optional; from Tor Project)
curl -s https://check.torproject.org/torbulkexitlist | \
  redis-cli -x sadd tor:exit_nodes

# Pre-load OFAC sanctions list
python3 -c "
from sanctions_checker import SanctionsChecker
SanctionsChecker().force_refresh()
print('OFAC SDN list loaded')
"

# Start the pipeline
uvicorn ip_detection_pipeline:app --host 0.0.0.0 --port 8000 --workers 4
```

### Production (systemd)

```ini
# /etc/systemd/system/ip-pipeline.service
[Unit]
Description=IP Detection Pipeline
After=network.target redis.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/ip-pipeline
EnvironmentFile=/etc/ip-pipeline/env
ExecStart=/opt/ip-pipeline/.venv/bin/uvicorn ip_detection_pipeline:app \
    --host 127.0.0.1 --port 8000 --workers 4 --log-level warning
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## Testing

```bash
# Install dev dependencies
pip install -r requirements.txt

# Run all tests (no Redis or MaxMind required — all mocked)
pytest test_pipeline.py -v

# Run a specific gate
pytest test_pipeline.py::TestGate7Sanctions -v

# With coverage
pytest test_pipeline.py --cov=. --cov-report=term-missing
```

---

## Admin API

The pipeline exposes management endpoints (should be behind auth in production):

| Method | Path | Description |
|--------|------|-------------|
| GET  | `/health` | Health check (Redis ping) |
| POST | `/admin/blacklist/add` | Add IP to blacklist |
| POST | `/admin/blacklist/remove` | Remove IP from blacklist |
| GET  | `/admin/blacklist/stats` | Blacklist statistics |
| POST | `/admin/sanctions/refresh` | Force OFAC SDN list refresh |
| GET  | `/admin/sanctions/stats` | Sanctions cache statistics |
| GET  | `/admin/pipeline/check?ip=1.2.3.4&player_id=p001` | On-demand gate run |

### Example: Add IP to blacklist

```bash
curl -X POST http://localhost:8000/admin/blacklist/add \
  -H "Content-Type: application/json" \
  -d '{"ip": "1.2.3.4", "reason": "Credential stuffing", "source": "manual", "ttl_seconds": 86400}'
```

### Example: Import AbuseIPDB CSV

```python
from ip_blacklist_service import IPBlacklistService

with open("abuseipdb_export.csv") as f:
    csv_content = f.read()

svc = IPBlacklistService()
imported, skipped = svc.import_abuseipdb_csv(csv_content, min_confidence=75)
print(f"Imported {imported}, skipped {skipped}")
```

---

## Redis Key Layout

| Key Pattern | Type | Purpose |
|-------------|------|---------|
| `ip_blacklist:entries` | ZSET | All blacklisted IPs (score=expiry or 0) |
| `ip_blacklist:meta:<ip>` | STRING (JSON) | BlacklistEntry metadata per IP |
| `ip_blacklist:stats` | HASH | Operational counters |
| `tor:exit_nodes` | SET | Known Tor exit node IPs |
| `vpn:ip_list` | SET | Known VPN endpoint IPs |
| `vpn:cache:<ip>` | STRING | Cached VPN check result (TTL 1h) |
| `proxy:ip_list` | SET | Known proxy IPs |
| `proxy:asn_list` | SET | Known proxy ASN numbers |
| `vel:ip30:<ip>` | STRING | Request counter per IP (30 s window) |
| `vel:ip5m:<ip>` | STRING | Request counter per IP (5 min window) |
| `vel:player1m:<id>` | STRING | Request counter per player (1 min window) |
| `auth:fail:<id>` | STRING | Failed auth counter per player (15 min TTL) |
| `fraud:profile:<id>` | STRING (JSON) | Player fraud profile (avg_tx, account_age) |
| `device:fp:<id>:history` | ZSET | Fingerprint hashes → last_seen timestamp |
| `device:fp:<id>:detail:<hash>` | HASH | Fingerprint component detail |
| `device:fp:<id>:anomaly_score` | STRING | Last anomaly score (1h TTL) |
| `device:fp:ja3:blocklist` | SET | Blocked JA3 hashes |
| `sanctions:sdn:entries` | HASH | uid → SDNEntry JSON |
| `sanctions:sdn:tok:<token>` | SET | Token → set of SDN UIDs (index) |
| `sanctions:sdn:last_refresh` | STRING | Unix timestamp of last OFAC download |
| `sanctions:sdn:xml_hash` | STRING | SHA-256 of last downloaded SDN XML |
| `kyc:status:<id>` | STRING | Cached KYC status per player (5 min TTL) |

---

## Gate Datacenter ASN List

The following ASNs are treated as datacenter/hosting providers (Gate 1 + Gate 3):

| ASN    | Organization |
|--------|--------------|
| 14061  | DigitalOcean |
| 16509  | Amazon AWS |
| 15169  | Google Cloud |
| 8075   | Microsoft Azure |
| 20473  | Vultr Holdings LLC |
| 63949  | Akamai/Linode |
| 24940  | Hetzner Online GmbH |
| 16276  | OVH SAS |
| 13335  | Cloudflare Inc. |
| 36351  | IBM Cloud (SoftLayer) |
| 19527  | Google LLC (additional range) |
| 32934  | Meta Connectivity |
| 2635   | Internap Network Services |
| 46606  | Unified Layer |

Additional ASNs can be added to the Redis `proxy:asn_list` SET at runtime
without restarting the service.

---

## Performance Characteristics

Under typical load (residential player request with warm Redis cache):

| Stage | Typical latency |
|-------|----------------|
| Gates 1-4 (IP-level, cached) | 2-5 ms |
| Gate 5 (fraud score, Redis pipeline) | 3-6 ms |
| Gate 6 (device fingerprint, Redis) | 2-4 ms |
| Gate 7 (sanctions, cached token index) | 5-15 ms |
| Gate 8 (KYC, cached 5 min) | 1-2 ms |
| Total (all PASS, warm cache) | 15-35 ms |

Cold paths (OFAC token miss → full scan, cold KYC cache) add 50-200 ms.
The KYC cache TTL (5 min) eliminates most cold KYC calls under normal load.
