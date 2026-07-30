# Chapter 24g — Post-Quantum Cryptography for iGaming: Supporting Scripts

This directory contains all supporting configuration files, scripts, and infrastructure code for Chapter 24g. Each file is production-oriented with extensive inline documentation.

---

## Directory Structure

```
pqc/
├── nginx-pqc.conf              # Nginx TLS config with PQC hybrid cipher suites
├── haproxy-pqc.cfg             # HAProxy frontend/backend with PQC TLS
├── pqc-inventory-scan.sh       # Cryptographic inventory scanner (executable)
├── pqc-benchmark.py            # PQC vs classical performance benchmark
├── step-ca-pqc-setup.sh        # Private CA setup with step-ca (executable)
├── suricata-pqc-rules.rules    # Suricata IDS rules for PQC monitoring
├── ci-pqc-gate.yml             # GitHub Actions PQC compliance workflow
├── docker-compose.yml          # PQC testing environment (Nginx OQS + step-ca)
├── terraform/
│   ├── aws-pqc-alb.tf          # AWS ALB with PQC-capable SSL policy
│   └── aws-pqc-cloudfront.tf   # CloudFront distribution with TLS 1.3 + PQC
└── README.md                   # This file
```

---

## File Reference

### `nginx-pqc.conf`

Nginx server configuration enabling Post-Quantum hybrid TLS.

**Key settings:**
- `ssl_ecdh_curve X25519Kyber768Draft00:x25519mlkem768:X25519:secp384r1` — prefers PQC hybrid, falls back to classical
- `ssl_protocols TLSv1.2 TLSv1.3` — disables TLS 1.0/1.1
- OCSP stapling, HSTS with `preload`, optimised session cache for PQC overhead

**Prerequisites:**
```bash
# Build or pull OQS-enabled Nginx
docker pull openquantumsafe/nginx:latest

# Or build from source (see OQS nginx demo):
# https://github.com/open-quantum-safe/oqs-demos/tree/main/nginx
```

**Usage:**
```bash
# Copy into /etc/nginx/nginx.conf and test
nginx -t -c /path/to/nginx-pqc.conf
nginx -s reload
```

---

### `haproxy-pqc.cfg`

HAProxy 2.8+ configuration with PQC TLS on the frontend bind.

**Key settings:**
- `curves X25519Kyber768Draft00:x25519mlkem768:X25519:secp384r1` on the bind directive
- TLS 1.3 ciphersuites, session ticket rotation disabled
- WebSocket backend with 1-hour tunnel timeout for live betting connections
- Sticky sessions via cookie for game state consistency

**Prerequisites:**
```bash
# HAProxy built against OpenSSL 3.x with oqs-provider
haproxy -vv | grep OpenSSL
# Should show: OpenSSL 3.x.x with oqs-provider

# Validate config
haproxy -c -f haproxy-pqc.cfg
```

---

### `pqc-inventory-scan.sh`

Bash script that audits all cryptographic assets on the current host and outputs a prioritised inventory.

**What it scans:**
- All listening TLS ports via `ss` + `openssl s_client`
- JWT signing keys in environment variables (`JWT_SECRET`, `SIGNING_KEY`, etc.)
- JWT/signing config references in YAML, JSON, TOML, `.env` files
- RSA, EC, and PKCS#8 private key files on disk

**Output fields (CSV):**
`system, algorithm, key_size, purpose, pqc_vulnerable, priority, detail`

**Prerequisites:**
```bash
# Install dependencies (Debian/Ubuntu)
apt-get install -y iproute2 openssl

# Make executable
chmod +x pqc-inventory-scan.sh
```

**Usage:**
```bash
# Standard CSV report
sudo ./pqc-inventory-scan.sh

# JSON output for CI/CD
sudo ./pqc-inventory-scan.sh --json

# Write to specific file
sudo ./pqc-inventory-scan.sh --output /tmp/crypto-audit.csv

# Exit codes:
#   0 — no vulnerable assets found
#   1 — vulnerable assets found (non-critical priority)
#   2 — CRITICAL priority vulnerable assets found (use in CI gate)
```

For deeper analysis, combine with [gustcol/post-quantum-check](https://github.com/gustcol/post-quantum-check):
```bash
pip install post-quantum-check
pqcheck --scan /path/to/codebase
```

---

### `pqc-benchmark.py`

Python benchmark that measures cryptographic operation latency and projects the impact on iGaming connection workloads.

**Benchmarks:**
| Algorithm | Operations | Notes |
|-----------|-----------|-------|
| RSA-2048 | keygen, sign, verify | Classical baseline |
| ECDSA-P256 | keygen, sign, verify | Classical baseline |
| ML-KEM-768 | keygen, encaps, decaps | Requires `pyoqs` |
| ML-DSA-65 | keygen, sign, verify | Requires `pyoqs` |

**iGaming scenarios modelled:** Player Login, Payment API, WebSocket Connect, REST API (keep-alive), CDN Cache, Blockchain signing

**Prerequisites:**
```bash
pip install cryptography
pip install pyoqs  # Optional: requires liboqs system library

# Install liboqs (Ubuntu):
sudo apt-get install -y cmake ninja-build libssl-dev
git clone --depth 1 https://github.com/open-quantum-safe/liboqs
cd liboqs && cmake -GNinja -DBUILD_SHARED_LIBS=ON . && sudo ninja install
pip install pyoqs
```

**Usage:**
```bash
# Standard benchmark (1000 iterations)
python pqc-benchmark.py

# Quick benchmark (100 iterations)
python pqc-benchmark.py --iterations 100

# JSON output for dashboards
python pqc-benchmark.py --json --output results.json

# Save human-readable table
python pqc-benchmark.py --output benchmark-report.txt
```

---

### `terraform/aws-pqc-alb.tf`

Terraform configuration for an AWS Application Load Balancer with the most advanced available SSL policy.

**Resources created:**
- `aws_lb` — internet-facing ALB
- `aws_lb_listener` — HTTPS:443 with configurable SSL policy
- `aws_lb_target_group` — HTTP:8080 with sticky sessions
- `aws_security_group` — allows 443/80 inbound

**PQC SSL policy:**
Set `use_pqc_policy = true` in `locals` once `ELBSecurityPolicy-TLS13-1-3-PQC-2024` is available in your region. Default uses `ELBSecurityPolicy-TLS13-1-3-2021-06`.

**Usage:**
```bash
cd terraform

# Create a terraform.tfvars file:
cat > terraform.tfvars << EOF
vpc_id              = "vpc-xxxxxxxxx"
public_subnet_ids   = ["subnet-aaa", "subnet-bbb"]
acm_certificate_arn = "arn:aws:acm:us-east-1:123456789:certificate/xxxxx"
environment         = "staging"
EOF

terraform init
terraform plan
terraform apply
```

**Outputs:** `alb_dns_name`, `alb_arn`, `https_listener_arn`, `ssl_policy_in_use`

---

### `terraform/aws-pqc-cloudfront.tf`

Terraform for a CloudFront distribution with TLS 1.3 and automatic PQC hybrid KEM negotiation.

**PQC behaviour:** CloudFront automatically negotiates `X25519Kyber768Draft00` with PQC-capable clients at the edge. No additional configuration is required — PQC is transparent.

**Key settings:**
- `minimum_protocol_version = "TLSv1.2_2021"` — allows TLS 1.2 and 1.3
- HTTP/3 (QUIC) enabled — additional latency benefit alongside PQC
- Geo-restriction for sanctioned countries (iGaming compliance)
- Separate cache behaviours for API, static assets, and WebSocket traffic

**Usage:**
```bash
cd terraform
# Configure variables as above, plus:
# cf_acm_certificate_arn must be in us-east-1
terraform apply
```

---

### `step-ca-pqc-setup.sh`

Installs and configures a private PKI using [Smallstep step-ca](https://smallstep.com/docs/step-ca/).

**PQC status:** Attempts to detect ML-DSA-65 support in the installed step-ca binary. Falls back to ECDSA P-384 with clear console output explaining the limitation and alternative paths.

**Prerequisites:**
```bash
# Ensure curl or wget is available
# Ensure openssl is installed
chmod +x step-ca-pqc-setup.sh
```

**Usage:**
```bash
# Default: domain=igaming.internal, CA dir=/etc/step-ca
sudo ./step-ca-pqc-setup.sh

# Custom domain and directory
sudo ./step-ca-pqc-setup.sh --domain payments.example.com --ca-dir /opt/igaming-ca

# Issue additional certificates after setup
step ca certificate api.igaming.internal api.crt api.key \
  --ca-url https://localhost:9000 \
  --root /etc/step-ca/certs/root_ca.crt
```

---

### `suricata-pqc-rules.rules`

Suricata 7+ IDS rules for monitoring PQC adoption and detecting compliance violations.

**Rule overview:**

| SID | Purpose | Severity |
|-----|---------|---------|
| 9001000 | ClientHello with ML-KEM-768 (X25519Kyber768Draft00) | Informational |
| 9001001 | ClientHello with x25519mlkem768 (IETF final) | Informational |
| 9001010 | TLS handshake record > 20 KB | Major |
| 9001011 | TLS server record > 16 KB | Informational |
| 9001020 | Legacy TLS 1.0 connection | Major |
| 9001021 | Legacy TLS 1.1 connection | Major |
| 9001030 | Internal server using TLS 1.2 (non-PQC) | Informational |
| 9001040 | RSA certificate from monitored endpoint | High |
| 9001041 | ECDSA certificate from monitored endpoint | High |

**Usage:**
```bash
# Copy rules to Suricata rules directory
cp suricata-pqc-rules.rules /etc/suricata/rules/

# Add to /etc/suricata/suricata.yaml:
# rule-files:
#   - suricata-pqc-rules.rules

# Test configuration
suricata -T -c /etc/suricata/suricata.yaml

# Reload rules without restart
kill -USR2 $(cat /var/run/suricata.pid)
```

---

### `ci-pqc-gate.yml`

GitHub Actions workflow that gates pull requests to `main` on PQC compliance.

**Triggers:** Pull requests to `main` on any Python, JavaScript, Helm, K8s, or Terraform file change.

**Behaviour:**
- `ACTION_REQUIRED` findings → PR fails (exit 1)
- `NEEDS_ATTENTION` findings → warning comment, PR passes
- `COMPLIANT` → green check, PR passes
- Results posted as a PR comment (updated on re-run, not duplicated)

**Setup:**
```bash
# Copy to your repository's workflows directory
mkdir -p .github/workflows
cp ci-pqc-gate.yml .github/workflows/pqc-gate.yml

# Commit and push — the workflow activates on the next pull request
```

Required permissions are set in the `permissions` block; no additional secrets needed beyond `GITHUB_TOKEN`.

---

### `docker-compose.yml`

Local PQC testing environment with four services.

**Services:**

| Service | Image | Purpose |
|---------|-------|---------|
| `nginx-pqc` | `openquantumsafe/nginx` | TLS edge with PQC hybrid KEM |
| `curl-oqs` | `openquantumsafe/curl` | PQC-capable test client (profile: tools) |
| `step-ca` | `smallstep/step-ca` | Certificate authority |
| `test-app` | `python:3.12-slim` | Minimal backend app |

**Usage:**
```bash
# Start the environment
docker compose up -d

# Wait for all services to be healthy
docker compose ps

# Test a PQC handshake
docker compose run --rm curl-oqs \
  curl -v --curves X25519Kyber768Draft00:X25519 \
  https://nginx-pqc/healthz

# Check negotiated cipher
docker compose run --rm curl-oqs \
  curl -sv https://nginx-pqc/api/v1/tls-info 2>&1 | grep -E "SSL|curve|cipher"

# Echo endpoint test
docker compose run --rm curl-oqs \
  curl -X POST https://nginx-pqc/api/v1/echo \
  -H 'Content-Type: application/json' \
  -d '{"player_id":"test123","event":"login"}'

# Tear down
docker compose down -v
```

---

## Quick Start: Full Environment

```bash
# 1. Clone or copy this directory to your workstation
cd /path/to/pqc/

# 2. Make scripts executable
chmod +x pqc-inventory-scan.sh step-ca-pqc-setup.sh

# 3. Start the Docker testing environment
docker compose up -d
docker compose ps  # wait for all services healthy

# 4. Run the PQC handshake test
docker compose run --rm curl-oqs \
  curl -v --curves X25519Kyber768Draft00:X25519 https://nginx-pqc/healthz

# 5. Scan the local host for quantum-vulnerable crypto
sudo ./pqc-inventory-scan.sh --json --output crypto-inventory.json

# 6. Run the benchmark (requires Python 3.10+)
python pqc-benchmark.py --iterations 500

# 7. Add the CI gate to your repository
cp ci-pqc-gate.yml ../../.github/workflows/pqc-gate.yml
```

---

## Algorithm Reference

| Algorithm | NIST Standard | Type | Quantum Safe? | Key Size (PK) | Sig/CT Size |
|-----------|-------------|------|--------------|--------------|------------|
| RSA-2048 | — | Signature/KEM | No | 256 B | 256 B |
| ECDSA-P256 | FIPS 186-5 | Signature | No | 64 B | ~71 B |
| X25519 | RFC 7748 | KEM | No | 32 B | 32 B |
| ML-KEM-768 | FIPS 203 | KEM | Yes | 1184 B | 1088 B (CT) |
| ML-DSA-65 | FIPS 204 | Signature | Yes | 1952 B | 3293 B |
| X25519Kyber768Draft00 | IETF draft | Hybrid KEM | Yes (hybrid) | ~1216 B | ~1120 B |
| SLH-DSA-128s | FIPS 205 | Signature | Yes | 32 B | 7856 B |

---

## Further Reading

- [NIST PQC Standards](https://csrc.nist.gov/pqc-standardization) — FIPS 203, 204, 205
- [Open Quantum Safe (OQS)](https://openquantumsafe.org) — liboqs, oqs-provider, OQS demos
- [IETF TLS PQC](https://datatracker.ietf.org/doc/draft-kwiatkowski-tls-ecdhe-mlkem/) — x25519mlkem768 draft
- [AWS PQC TLS](https://aws.amazon.com/blogs/security/post-quantum-tls-now-supported-in-aws-kms/) — AWS KMS + S3 + ALB PQC
- [Cloudflare PQC](https://blog.cloudflare.com/post-quantum-to-origins/) — CloudFront/Cloudflare PQC deployment
- [gustcol/post-quantum-check](https://github.com/gustcol/post-quantum-check) — Codebase PQC scanner
