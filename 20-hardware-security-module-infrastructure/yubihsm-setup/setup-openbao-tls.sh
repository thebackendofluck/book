#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# setup-openbao-tls.sh — Issue a PKI-signed TLS cert for OpenBao and install it.
#
# Prerequisites:
#   - OpenBao must be initialized, unsealed, and running
#   - PKI secrets engine must be mounted at pki/ with a CA and a 'server' role
#   - Run as a user with sudo access on ops-host
#
# What this script does:
#   1. Authenticates to OpenBao using the root token from the init file
#   2. Updates the PKI 'server' role to allow bare domains (ops-host, ops-host.internal)
#   3. Issues a TLS cert with SANs: ops-host.internal, localhost, ops-host, 127.0.0.1, 10.0.0.11
#   4. Installs the cert+chain to /opt/openbao/tls/tls.crt
#   5. Installs the CA cert to /usr/local/share/ca-certificates/openbao-ca.crt (system trust)
#   6. Runs update-ca-certificates
#   7. Restarts OpenBao and verifies TLS without skip-verify
#
# Compliance: PCI DSS Req. 4 — all crypto in transit must use strong cryptography with
# proper certificate chain of trust. skip-verify defeats this requirement.

set -euo pipefail

INIT_FILE="/opt/yubihsm-evidence/openbao-init.json"
BAO_ADDR="https://127.0.0.1:8200"
TLS_DIR="/opt/openbao/tls"
CA_SYSTEM_PATH="/usr/local/share/ca-certificates/openbao-ca.crt"
CERT_RESPONSE="/tmp/cert-response.json"

export BAO_ADDR
export BAO_SKIP_VERIFY=true  # Temporary — only during cert issuance with old self-signed cert

# ── Step 1: Authenticate ──────────────────────────────────────────────────────
echo "[1/7] Authenticating to OpenBao..."
ROOT_TOKEN=$(sudo python3 -c "import json; print(json.load(open('$INIT_FILE'))['root_token'])")
export BAO_TOKEN="$ROOT_TOKEN"

# ── Step 2: Update PKI role ───────────────────────────────────────────────────
echo "[2/7] Updating PKI 'server' role to allow bare domains..."
bao write pki/roles/server \
    allowed_domains='localhost,ops-host,ops-host.internal' \
    allow_bare_domains=true \
    allow_subdomains=false \
    allow_localhost=true \
    allow_ip_sans=true \
    key_type=rsa \
    key_bits=2048 \
    max_ttl=8760h \
    server_flag=true \
    client_flag=true > /dev/null

# ── Step 3: Issue cert ────────────────────────────────────────────────────────
echo "[3/7] Issuing server TLS cert from PKI engine..."
bao write -format=json pki/issue/server \
    common_name="ops-host.internal" \
    alt_names="localhost,ops-host" \
    ip_sans="127.0.0.1,10.0.0.11" \
    ttl=8760h > "$CERT_RESPONSE"

SERIAL=$(python3 -c "import json; print(json.load(open('$CERT_RESPONSE'))['data']['serial_number'])")
echo "  Serial: $SERIAL"

# ── Step 4: Extract cert files ────────────────────────────────────────────────
echo "[4/7] Extracting cert and key..."
python3 << 'PY'
import json
d = json.load(open("/tmp/cert-response.json"))["data"]
# Full chain: leaf + issuing CA
with open("/tmp/server.crt", "w") as f:
    f.write(d["certificate"] + "\n")
    f.write(d["issuing_ca"] + "\n")
with open("/tmp/server.key", "w") as f:
    f.write(d["private_key"] + "\n")
with open("/tmp/ca.crt", "w") as f:
    f.write(d["issuing_ca"] + "\n")
print(f"  Cert expires: {d['expiration']}")
PY

# Verify chain before installing
openssl verify -CAfile /tmp/ca.crt /tmp/server.crt > /dev/null
echo "  Chain verified OK."

# ── Step 5: Install cert, key, CA ─────────────────────────────────────────────
echo "[5/7] Installing cert and key to $TLS_DIR..."
sudo cp /tmp/server.crt "$TLS_DIR/tls.crt"
sudo cp /tmp/server.key "$TLS_DIR/tls.key"
sudo cp /tmp/ca.crt "$TLS_DIR/ca.crt"
sudo chown openbao:openbao "$TLS_DIR/tls.crt" "$TLS_DIR/tls.key" "$TLS_DIR/ca.crt"
sudo chmod 644 "$TLS_DIR/tls.crt" "$TLS_DIR/ca.crt"
sudo chmod 600 "$TLS_DIR/tls.key"

# ── Step 6: System trust store ────────────────────────────────────────────────
echo "[6/7] Installing CA to system trust store..."
sudo cp /tmp/ca.crt "$CA_SYSTEM_PATH"
sudo update-ca-certificates 2>&1 | grep -E '(added|done|Running)' || true
# Verify it's available
test -f /etc/ssl/certs/openbao-ca.pem || {
    echo "[WARN] CA not found at /etc/ssl/certs/openbao-ca.pem — checking..."
    for f in /etc/ssl/certs/openbao*; do [ -e "$f" ] && echo "  Found: $f" && break; done || echo "  Not found by name, but may be included in bundle."
}

# ── Step 7: Restart and verify ────────────────────────────────────────────────
echo "[7/7] Restarting OpenBao and verifying TLS..."
sudo systemctl restart openbao
sleep 4

# Unseal (BAO_SKIP_VERIFY is still set for this step only)
KEY1=$(sudo python3 -c "import json; print(json.load(open('$INIT_FILE'))['unseal_keys_b64'][0])")
KEY2=$(sudo python3 -c "import json; print(json.load(open('$INIT_FILE'))['unseal_keys_b64'][1])")
KEY3=$(sudo python3 -c "import json; print(json.load(open('$INIT_FILE'))['unseal_keys_b64'][2])")
bao operator unseal "$KEY1" > /dev/null
bao operator unseal "$KEY2" > /dev/null
bao operator unseal "$KEY3" > /dev/null

# Now verify WITHOUT skip-verify
unset BAO_SKIP_VERIFY
export BAO_CACERT=/etc/ssl/certs/openbao-ca.pem
unset BAO_TOKEN

echo ""
echo "=== TLS verification (no skip-verify) ==="
echo | openssl s_client -connect 127.0.0.1:8200 -CAfile /etc/ssl/certs/openbao-ca.pem 2>&1 \
    | grep -E '(Verify return|subject=|issuer=|notAfter)'

echo ""
echo "=== OpenBao status ==="
bao status 2>&1

echo ""
echo "Setup complete. TLS is now backed by PKI chain of trust."
echo "CA cert: /etc/ssl/certs/openbao-ca.pem (system trust)"
echo "Server cert CN: ops-host.internal, SANs: localhost, ops-host, 127.0.0.1, 10.0.0.11"
