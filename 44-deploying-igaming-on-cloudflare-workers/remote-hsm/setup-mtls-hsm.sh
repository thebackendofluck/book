#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# setup-mtls-hsm.sh — Deploy mTLS between Cloudflare Workers and HSM Proxy API
#
# What this script does:
#   1. Reads root token from /opt/yubihsm-evidence/openbao-init.json
#   2. Creates pki-mtls secrets mount in OpenBao
#   3. Generates Root CA ("AcmeToCasino mTLS CA")
#   4. Issues client certificate for Cloudflare Workers
#   5. Writes CA cert to /etc/nginx/ssl/hsm-client-ca.crt
#   6. Deploys nginx mTLS config (port 8443, separate from 443 shared vhosts)
#   7. Reloads nginx
#
# Prerequisites: OpenBao unsealed, nginx installed, ops-host.crt/key in /etc/nginx/ssl/
#
# Run as root on ops-host:
#   bash setup-mtls-hsm.sh
#
# After running:
#   - Upload /tmp/worker-client.crt + /tmp/worker-client.key to Cloudflare mTLS certificates
#   - Set BAO_MTLS_CERT_ID in wrangler.toml [[mtls_certificates]] binding
#   - Add pfSense NAT rule: external 203.0.113.2:8443 -> ops-host:8443

set -euo pipefail

BAILEON_INIT_JSON="/opt/yubihsm-evidence/openbao-init.json"
NGINX_SSL_DIR="/etc/nginx/ssl"
NGINX_SITES_AVAILABLE="/etc/nginx/sites-available"
NGINX_SITES_ENABLED="/etc/nginx/sites-enabled"
BAO_ADDR="https://127.0.0.1:8200"
BAO_SKIP_VERIFY="true"
CLIENT_CERT_CN="cloudflare-worker-hsm-client"
CLIENT_CERT_TTL="8760h"
CA_TTL="43800h"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[mtls-setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC}      $*"; }
err()  { echo -e "${RED}[error]${NC}     $*" >&2; exit 1; }

# ── Step 0: Read root token ────────────────────────────────────────────────────
log "Step 0: Reading OpenBao root token"
BAO_TOKEN="$(python3 -c "
import json
with open('${BAILEON_INIT_JSON}') as f:
    d = json.load(f)
print(d['root_token'].strip())
")"
export BAO_ADDR BAO_SKIP_VERIFY BAO_TOKEN
log "Root token obtained (${#BAO_TOKEN} chars)"

# ── Step 1: Enable and tune pki-mtls mount ────────────────────────────────────
log "Step 1: Configuring pki-mtls secrets mount"
if bao secrets enable -path=pki-mtls pki 2>/dev/null; then
    log "pki-mtls mount enabled"
else
    log "pki-mtls mount already exists"
fi
bao secrets tune -max-lease-ttl="${CA_TTL}" pki-mtls
log "pki-mtls max TTL set to ${CA_TTL}"

# ── Step 2: Generate Root CA ──────────────────────────────────────────────────
log "Step 2: Generating Root CA for mTLS"
mkdir -p "${NGINX_SSL_DIR}"
bao write -field=certificate pki-mtls/root/generate/internal \
    common_name="AcmeToCasino mTLS CA" \
    ttl="${CA_TTL}" \
    key_type=rsa \
    key_bits=4096 | tee "${NGINX_SSL_DIR}/hsm-client-ca.crt" > /dev/null
chmod 644 "${NGINX_SSL_DIR}/hsm-client-ca.crt"
log "Root CA written to ${NGINX_SSL_DIR}/hsm-client-ca.crt"
openssl x509 -in "${NGINX_SSL_DIR}/hsm-client-ca.crt" -noout -subject -dates

# ── Step 3: Create worker-client role ─────────────────────────────────────────
log "Step 3: Creating worker-client PKI role"
bao write pki-mtls/roles/worker-client \
    allowed_domains="workers.acmetocasino.com" \
    allow_any_name=true \
    client_flag=true \
    server_flag=false \
    max_ttl=8760h \
    key_type=rsa \
    key_bits=2048
log "Role worker-client created"

# ── Step 4: Issue client certificate ─────────────────────────────────────────
log "Step 4: Issuing client certificate (CN=${CLIENT_CERT_CN})"
CERT_JSON="$(bao write -format=json pki-mtls/issue/worker-client \
    common_name="${CLIENT_CERT_CN}" \
    ttl="${CLIENT_CERT_TTL}")"

python3 << PYEOF
import json, os
d = json.loads('''${CERT_JSON}''')["data"]
open("/tmp/worker-client.crt",       "w").write(d["certificate"] + "\n")
open("/tmp/worker-client.key",       "w").write(d["private_key"] + "\n")
open("/tmp/worker-client-chain.crt", "w").write(d["certificate"] + "\n" + d["issuing_ca"] + "\n")
os.chmod("/tmp/worker-client.key", 0o600)
print(f"Cert serial:  {d['serial_number']}")
print(f"Cert expires: {d['expiration']} (epoch)")
PYEOF

openssl x509 -in /tmp/worker-client.crt -noout -subject -issuer -dates
openssl verify -CAfile "${NGINX_SSL_DIR}/hsm-client-ca.crt" /tmp/worker-client.crt

# ── Step 5: Write nginx mTLS config ───────────────────────────────────────────
log "Step 5: Writing nginx mTLS config (port 8443)"

# Disable old stub configs if present
for stub in hsm-api fraud-api; do
    if [ -L "${NGINX_SITES_ENABLED}/${stub}" ]; then
        rm "${NGINX_SITES_ENABLED}/${stub}"
        log "Disabled old ${stub} config"
    fi
done

cat > "${NGINX_SITES_AVAILABLE}/hsm-api-mtls" << 'NGINXEOF'
# HSM Proxy API — dedicated mTLS server block
#
# Port: 8443 (separate from shared 443 vhosts to ensure ssl_verify_client
#        works correctly — nginx only sends CertificateRequest per vhost when
#        it is the sole vhost on that IP:port pair)
#
# pfSense NAT required: 203.0.113.2:8443 -> ops-host:8443
# CA cert: /etc/nginx/ssl/hsm-client-ca.crt  (issued by OpenBao pki-mtls)
# Server cert: /etc/nginx/ssl/ops-host.crt    (existing self-signed)
#
# Defense in depth:
#   Layer 1: mTLS — client cert must be signed by AcmeToCasino mTLS CA
#   Layer 2: CN check — only cloudflare-worker-hsm-client is accepted
#   Layer 3: IP allowlist — pfSense NAT IP + Cloudflare ranges + loopback
#   Layer 4: API key — checked by FastAPI upstream (X-API-Key header)

server {
    listen 8443 ssl;
    server_name 203.0.113.2 ops-host.internal 127.0.0.1 localhost;

    ssl_certificate     /etc/nginx/ssl/ops-host.crt;
    ssl_certificate_key /etc/nginx/ssl/ops-host.key;

    # mTLS: request client cert on every connection
    ssl_client_certificate /etc/nginx/ssl/hsm-client-ca.crt;
    ssl_verify_client      optional;
    ssl_verify_depth       2;

    ssl_protocols    TLSv1.2 TLSv1.3;
    ssl_ciphers      ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers on;
    ssl_session_cache   shared:SSL_HSM_MTLS:10m;
    ssl_session_timeout 10m;
    ssl_session_tickets off;

    # == /hsm-api/ — mTLS enforced ============================================
    location /hsm-api/ {
        # Layer 1: require valid client certificate
        set $mtls_ok 0;
        if ($ssl_client_verify = SUCCESS) {
            set $mtls_ok 1;
        }
        if ($mtls_ok = 0) {
            return 400 '{"detail":"Client certificate required"}';
        }

        # Layer 2: restrict to the approved Worker CN
        set $cn_ok 0;
        if ($ssl_client_s_dn ~ "CN=cloudflare-worker-hsm-client") {
            set $cn_ok 1;
        }
        if ($cn_ok = 0) {
            return 403 '{"detail":"Invalid client certificate CN"}';
        }

        # Layer 3: IP allowlist
        allow 203.0.113.1;        # pfSense NAT (production server)
        allow 127.0.0.1;         # loopback (local tests, monitoring)
        allow ::1;
        # Cloudflare IPv4 ranges (https://www.cloudflare.com/ips-v4 — update quarterly)
        allow 103.21.244.0/22;
        allow 103.22.200.0/22;
        allow 103.31.4.0/22;
        allow 104.16.0.0/13;
        allow 104.24.0.0/14;
        allow 108.162.192.0/18;
        allow 131.0.72.0/22;
        allow 141.101.64.0/18;
        allow 162.158.0.0/15;
        allow 172.64.0.0/13;
        allow 173.245.48.0/20;
        allow 188.114.96.0/20;
        allow 190.93.240.0/20;
        allow 197.234.240.0/22;
        allow 198.41.128.0/17;
        deny all;

        # Proxy to HSM API (port 8190, localhost-only)
        proxy_pass         http://127.0.0.1:8190/;
        proxy_http_version 1.1;
        proxy_set_header   Connection           "";
        proxy_set_header   Host                $host;
        proxy_set_header   X-Real-IP           $remote_addr;
        proxy_set_header   X-Forwarded-For     $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto   $scheme;
        # Forward cert info to upstream for audit logging
        proxy_set_header   X-Client-Cert-CN    $ssl_client_s_dn;
        proxy_set_header   X-Client-Cert-Verified $ssl_client_verify;

        proxy_connect_timeout  5s;
        proxy_send_timeout    10s;
        proxy_read_timeout    15s;
        client_max_body_size   64k;
        proxy_buffering        off;
    }

    # == catch-all =============================================================
    location / {
        return 404;
    }

    access_log /var/log/nginx/hsm-api-access.log;
    error_log  /var/log/nginx/hsm-api-error.log warn;
}
NGINXEOF

ln -sf "${NGINX_SITES_AVAILABLE}/hsm-api-mtls" "${NGINX_SITES_ENABLED}/hsm-api-mtls"

# ── Step 6: Validate and reload nginx ────────────────────────────────────────
log "Step 6: Testing and reloading nginx"
nginx -t
systemctl reload nginx
log "nginx reloaded with mTLS config"

# ── Step 7: Smoke test ───────────────────────────────────────────────────────
log "Step 7: Running smoke test"
sleep 1

# Test: no cert -> 400
CODE=$(curl -sf -k --no-sessionid -w "%{http_code}" -o /dev/null \
    https://127.0.0.1:8443/hsm-api/hsm/health 2>&1)
if [ "${CODE}" = "400" ]; then
    log "Smoke test PASS: no cert -> 400"
else
    err "Smoke test FAIL: expected 400, got ${CODE}"
fi

# Test: valid cert -> 200
HSM_KEY="$(grep HSM_API_KEY /etc/hsm-proxy-api/env 2>/dev/null | cut -d= -f2 || \
           cat "/proc/$(ss -tlnp | grep 8190 | grep -oP 'pid=\K[0-9]+' | head -1)/environ" 2>/dev/null | tr '\0' '\n' | grep HSM_API_KEY | cut -d= -f2)"
CODE=$(curl -sf -k --no-sessionid \
    --cert /tmp/worker-client.crt --key /tmp/worker-client.key \
    -H "X-API-Key: ${HSM_KEY}" \
    -w "%{http_code}" -o /dev/null \
    https://127.0.0.1:8443/hsm-api/hsm/health 2>&1)
if [ "${CODE}" = "200" ]; then
    log "Smoke test PASS: valid cert + valid key -> 200"
else
    err "Smoke test FAIL: expected 200, got ${CODE}"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
log "mTLS setup complete."
warn "Next steps:"
warn "  1. Upload client cert to Cloudflare mTLS:"
warn "     curl -X POST https://api.cloudflare.com/client/v4/accounts/<your-cf-account-id>/mtls_certificates \\"
warn "         -H 'Authorization: Bearer \$CF_API_TOKEN' \\"
warn "         -F 'certificate=@/tmp/worker-client.crt' \\"
warn "         -F 'private_key=@/tmp/worker-client.key' \\"
warn "         -F 'name=hsm-proxy-client'"
warn "  2. Add wrangler.toml binding:"
warn "     [[mtls_certificates]]"
warn "     binding = \"HSM_CLIENT_CERT\""
warn "     certificate_id = \"<cert-id-from-step-1>\""
warn "  3. Add pfSense NAT rule: 203.0.113.2:8443 -> $(hostname -I | awk '{print $1}'):8443"
warn "  4. Rotate cert before expiry: bao write pki-mtls/issue/worker-client common_name=${CLIENT_CERT_CN} ttl=${CLIENT_CERT_TTL}"
echo ""
warn "Client cert files:"
warn "  /tmp/worker-client.crt   — upload to Cloudflare"
warn "  /tmp/worker-client.key   — upload to Cloudflare (keep secret)"
warn "  /tmp/worker-client-chain.crt — full chain (if needed)"
