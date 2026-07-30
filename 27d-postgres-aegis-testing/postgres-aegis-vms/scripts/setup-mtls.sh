#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# setup-mtls.sh — issue a cluster CA + per-node server certs + app-side
# client cert bundle for postgres-aegis mTLS.
#
# Output:
#   /etc/pki/postgres-aegis/
#     ca.crt            # shared root CA
#     ca.key            # private (root-only)
#     server-<host>.crt # issued server cert per DB VM (CN = hostname)
#     server-<host>.key # private (root-only)
#     app-client.crt    # client cert for apps (CN = CN of a PG role)
#     app-client.key    # private (apps bundle it)
#
# After running, distribute ca.crt + app-client.crt/key to app hosts.
# Add to pg_hba.conf: hostssl all all 0.0.0.0/0 cert clientcert=verify-full
#
# Usage:
#   sudo setup-mtls.sh init                    # creates CA + enrolls server certs for every VM
#   sudo setup-mtls.sh client <role> <out-dir> # issues a client bundle with CN=role
#   sudo setup-mtls.sh rotate                  # renews every cert that expires < 14 days

set -euo pipefail

PKI_DIR=/etc/pki/postgres-aegis
CA_DAYS=3650
SRV_DAYS=365
CLI_DAYS=365

INVENTORY="${INVENTORY:-/tmp/aegis-test/inventory/proxmox-secondary-host.yml}"

# --- YubiHSM PKCS#11 settings ---
# Connector running on ops-host via yubihsm-connector v3.0.5.
# Override via env vars if connector is remote.
HSM_CONNECTOR="${HSM_CONNECTOR:-http://127.0.0.1:12345}"
HSM_AUTH_KEY_ID="${HSM_AUTH_KEY_ID:-1}"
HSM_KEY_LABEL="${HSM_KEY_LABEL:-postgres-aegis-ca}"
# PKCS#11 library path (Debian/Ubuntu: apt install yubihsm-pkcs11)
PKCS11_LIB="${PKCS11_LIB:-/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so}"
# OpenSSL engine (apt install libengine-pkcs11-openssl)
PKCS11_ENGINE="pkcs11"
# PKCS#11 URI for the CA key stored inside the HSM
HSM_KEY_URI="pkcs11:token=YubiHSM;object=${HSM_KEY_LABEL};type=private"

ensure_ca_hsm() {
  # Creates a 4096-bit RSA CA signing key INSIDE the YubiHSM.
  # The private key material never touches the filesystem.
  # Requires: yubihsm-pkcs11, pkcs11-tool, libengine-pkcs11-openssl.
  mkdir -p "$PKI_DIR"
  chmod 700 "$PKI_DIR"
  if [ -f "$PKI_DIR/ca.crt" ]; then
    echo "[mtls-hsm] CA cert already present — skipping key generation."
    return 0
  fi
  echo "[mtls-hsm] Checking YubiHSM connector at ${HSM_CONNECTOR} ..."
  if ! curl -sf "${HSM_CONNECTOR}/connector/status" | grep -q "status=OK"; then
    echo "ERROR: YubiHSM connector not reachable at ${HSM_CONNECTOR}." >&2
    exit 1
  fi

  echo "[mtls-hsm] Generating CA key in YubiHSM (label: ${HSM_KEY_LABEL}) ..."
  # Generate RSA-4096 key in the HSM. Auth key must have generate-asymmetric-key capability.
  pkcs11-tool \
    --module "${PKCS11_LIB}" \
    --login --pin "0002${HSM_AUTH_KEY_ID}password" \
    --keypairgen --key-type rsa:4096 \
    --label "${HSM_KEY_LABEL}" \
    --usage-sign

  echo "[mtls-hsm] Self-signing CA certificate using HSM key ..."
  # openssl engine pkcs11 invokes the signing operation inside the HSM.
  openssl req -x509 -new \
    -engine "${PKCS11_ENGINE}" -keyform engine \
    -key "${HSM_KEY_URI}" \
    -sha512 -days "${CA_DAYS}" \
    -subj "/C=BR/O=postgres-aegis/CN=postgres-aegis root CA (HSM)" \
    -out "${PKI_DIR}/ca.crt"

  echo "[mtls-hsm] CA certificate written to ${PKI_DIR}/ca.crt"
  echo "[mtls-hsm] CA private key resides in YubiHSM — label='${HSM_KEY_LABEL}'"
}

issue_server_hsm() {
  # Issue a server cert signed by the HSM-held CA key.
  local host="$1" ip="$2"
  local key="${PKI_DIR}/server-${host}.key"
  local crt="${PKI_DIR}/server-${host}.crt"
  [ -f "$crt" ] && return 0
  # Server private key is a normal filesystem key (servers aren't HSM-attached).
  openssl genrsa -out "$key" 4096
  cat > "/tmp/${host}.ext" <<EOF
subjectAltName = DNS:${host},DNS:${host}.local,IP:${ip}
extendedKeyUsage = serverAuth
EOF
  openssl req -new -key "$key" -subj "/CN=${host}" -out "/tmp/${host}.csr"
  # Signing happens inside the HSM — the CA private key never leaves it.
  openssl x509 -req -in "/tmp/${host}.csr" \
    -engine "${PKCS11_ENGINE}" -CAkeyform engine \
    -CA "${PKI_DIR}/ca.crt" -CAkey "${HSM_KEY_URI}" \
    -CAcreateserial \
    -extfile "/tmp/${host}.ext" -days "${SRV_DAYS}" -sha512 \
    -out "$crt"
  chmod 600 "$key"
  rm -f "/tmp/${host}.csr" "/tmp/${host}.ext"
  echo "[mtls-hsm] issued server-${host} (signed by HSM CA)"
}

issue_client_hsm() {
  # Issue a client cert bundle signed by the HSM-held CA key.
  local role="$1" out="$2"
  mkdir -p "$out"
  openssl genrsa -out "$out/postgresql.key" 4096
  openssl req -new -key "$out/postgresql.key" -subj "/CN=${role}" -out /tmp/c.csr
  openssl x509 -req -in /tmp/c.csr \
    -engine "${PKCS11_ENGINE}" -CAkeyform engine \
    -CA "${PKI_DIR}/ca.crt" -CAkey "${HSM_KEY_URI}" \
    -CAcreateserial \
    -days "${CLI_DAYS}" -sha512 -out "$out/postgresql.crt"
  cp "${PKI_DIR}/ca.crt" "$out/root.crt"
  chmod 600 "$out/postgresql.key"
  rm -f /tmp/c.csr
  echo "[mtls-hsm] client bundle for '${role}' → ${out}/"
  echo "       psql 'sslmode=verify-full sslrootcert=${out}/root.crt sslcert=${out}/postgresql.crt sslkey=${out}/postgresql.key host=... user=${role} dbname=casino'"
}

ensure_ca() {
  mkdir -p "$PKI_DIR"
  chmod 700 "$PKI_DIR"
  if [ ! -f "$PKI_DIR/ca.key" ]; then
    echo "[mtls] creating root CA"
    openssl genrsa -out "$PKI_DIR/ca.key" 4096
    openssl req -x509 -new -key "$PKI_DIR/ca.key" -sha512 -days "$CA_DAYS" \
      -subj "/C=BR/O=postgres-aegis/CN=postgres-aegis root CA" \
      -out "$PKI_DIR/ca.crt"
    chmod 600 "$PKI_DIR/ca.key"
  fi
}

issue_server() {
  local host="$1" ip="$2"
  local key="$PKI_DIR/server-${host}.key"
  local crt="$PKI_DIR/server-${host}.crt"
  [ -f "$crt" ] && return 0
  openssl genrsa -out "$key" 4096
  cat > "/tmp/${host}.ext" <<EOF
subjectAltName = DNS:${host},DNS:${host}.local,IP:${ip}
extendedKeyUsage = serverAuth
EOF
  openssl req -new -key "$key" -subj "/CN=${host}" -out "/tmp/${host}.csr"
  openssl x509 -req -in "/tmp/${host}.csr" \
    -CA "$PKI_DIR/ca.crt" -CAkey "$PKI_DIR/ca.key" -CAcreateserial \
    -extfile "/tmp/${host}.ext" -days "$SRV_DAYS" -sha512 \
    -out "$crt"
  chmod 600 "$key"
  rm -f "/tmp/${host}.csr" "/tmp/${host}.ext"
  echo "[mtls] issued server-$host"
}

issue_client() {
  local role="$1" out="$2"
  mkdir -p "$out"
  openssl genrsa -out "$out/postgresql.key" 4096
  openssl req -new -key "$out/postgresql.key" -subj "/CN=${role}" -out /tmp/c.csr
  openssl x509 -req -in /tmp/c.csr \
    -CA "$PKI_DIR/ca.crt" -CAkey "$PKI_DIR/ca.key" -CAcreateserial \
    -days "$CLI_DAYS" -sha512 -out "$out/postgresql.crt"
  cp "$PKI_DIR/ca.crt" "$out/root.crt"
  chmod 600 "$out/postgresql.key"
  rm -f /tmp/c.csr
  echo "[mtls] client bundle for '$role' -> $out/"
  echo "       psql 'sslmode=verify-full sslrootcert=$out/root.crt sslcert=$out/postgresql.crt sslkey=$out/postgresql.key host=... user=${role} dbname=casino'"
}

deploy_server_cert() {
  local host="$1" ip="$2"
  local dst="/etc/postgresql/16/main"
  scp -o StrictHostKeyChecking=no -o BatchMode=yes \
    "$PKI_DIR/server-${host}.crt" "ansible@$ip:/tmp/server.crt"
  scp -o StrictHostKeyChecking=no -o BatchMode=yes \
    "$PKI_DIR/server-${host}.key" "ansible@$ip:/tmp/server.key"
  scp -o StrictHostKeyChecking=no -o BatchMode=yes \
    "$PKI_DIR/ca.crt" "ansible@$ip:/tmp/root.crt"
  ssh -o StrictHostKeyChecking=no -o BatchMode=yes "ansible@$ip" "sudo install -o postgres -g postgres -m 0600 /tmp/server.key $dst/server.key; sudo install -o postgres -g postgres -m 0644 /tmp/server.crt $dst/server.crt; sudo install -o postgres -g postgres -m 0644 /tmp/root.crt $dst/root.crt; sudo systemctl reload postgresql@16-main || sudo systemctl restart patroni"
  echo "[mtls] deployed cert → $host ($ip)"
}

parse_hosts() {
  python3 -c "
import yaml, sys
inv = yaml.safe_load(open('$INVENTORY'))
for g in inv['all']['children'].values():
  for h, hv in (g.get('hosts') or {}).items():
    if h.startswith('pg-shard') or h == 'pg-cat-1':
      print(h, hv['ansible_host'])
"
}

cmd=${1:-}
case "$cmd" in
  init)
    ensure_ca
    while read -r host ip; do issue_server "$host" "$ip"; done < <(parse_hosts)
    while read -r host ip; do deploy_server_cert "$host" "$ip"; done < <(parse_hosts)
    echo "[mtls] done. Append to pg_hba.conf: hostssl all all 0.0.0.0/0 cert clientcert=verify-full"
    ;;

  client)
    ensure_ca
    issue_client "${2:?role}" "${3:?out-dir}"
    ;;

  rotate)
    # Rotate every server cert that expires within $RENEW_DAYS (default 14).
    # Strategy:
    #   * Re-issue cert under same CA (CA itself is NOT touched — that's
    #     a separate `rotate-ca` op; CA has 10-year validity).
    #   * Atomically replace cert+key on each VM (write to .new, mv into
    #     place, then `SELECT pg_reload_conf()`). Postgres reload picks up
    #     the new cert without dropping existing connections.
    #   * Old key file is `shred`ed.
    #   * Audit log line per cert.
    : "${RENEW_DAYS:=14}"
    LOG="/var/log/postgres-aegis-mtls-rotate.log"
    {
      echo "===== rotation pass $(date -u '+%Y-%m-%dT%H:%M:%SZ') ====="
      echo "policy: re-issue when < ${RENEW_DAYS} days remain"
    } | tee -a "$LOG"

    ensure_ca
    rotated=0
    while IFS= read -r host ip; do
      crt="$PKI_DIR/server-${host}.crt"
      [ -f "$crt" ] || { echo "  $host: no cert (run 'init' first)" | tee -a "$LOG"; continue; }
      end=$(openssl x509 -in "$crt" -noout -enddate | cut -d= -f2)
      end_ts=$(date -d "$end" +%s 2>/dev/null || gdate -d "$end" +%s 2>/dev/null)
      now=$(date +%s)
      remain_days=$(( (end_ts - now) / 86400 ))

      if [ "$remain_days" -gt "$RENEW_DAYS" ]; then
        echo "  $host: $remain_days days remain — keep" | tee -a "$LOG"
        continue
      fi

      echo "  $host: $remain_days days remain — RE-ISSUING" | tee -a "$LOG"
      # 1) Issue fresh cert (overwrite local PKI files atomically).
      mv "$PKI_DIR/server-${host}.crt"  "$PKI_DIR/server-${host}.crt.old"
      mv "$PKI_DIR/server-${host}.key" "$PKI_DIR/server-${host}.key.old"
      issue_server "$host" "$ip"        # re-creates with same SAN

      # 2) Push to VM atomically: write to .new, mv into place, reload.
      scp -o StrictHostKeyChecking=no -o BatchMode=yes \
        "$PKI_DIR/server-${host}.crt" "ansible@$ip:/tmp/server.crt.new"
      scp -o StrictHostKeyChecking=no -o BatchMode=yes \
        "$PKI_DIR/server-${host}.key" "ansible@$ip:/tmp/server.key.new"
      ssh -n -o StrictHostKeyChecking=no -o BatchMode=yes "ansible@$ip" "
        sudo install -o postgres -g postgres -m 0644 /tmp/server.crt.new /etc/postgresql/16/main/server.crt
        sudo install -o postgres -g postgres -m 0600 /tmp/server.key.new /etc/postgresql/16/main/server.key
        sudo shred -u /tmp/server.crt.new /tmp/server.key.new 2>/dev/null || true
        # pg_reload_conf is enough; no restart needed for cert reload.
        sudo -u postgres psql -c \"SELECT pg_reload_conf();\" >/dev/null 2>&1 \
          || sudo systemctl reload postgresql@16-main \
          || sudo systemctl reload patroni
      "

      # 3) Shred local .old files (CA history is enough for forensics).
      shred -u "$PKI_DIR/server-${host}.crt.old" "$PKI_DIR/server-${host}.key.old" 2>/dev/null || true
      rotated=$((rotated + 1))
      echo "    deployed + reloaded $host" | tee -a "$LOG"
    done < <(parse_hosts)

    echo "===== rotation done: $rotated server cert(s) re-issued =====" | tee -a "$LOG"
    echo "[mtls] for client bundles, re-run: $0 client <role> <dir>"
    echo "[mtls] schedule: add to root cron — 0 3 * * * $0 rotate >> $LOG 2>&1"
    ;;

  rotate-ca)
    # NEW root CA. Major op: every existing server + client cert becomes
    # invalid until re-issued. Plan:
    #   1. Generate ca.new + ca-bundle (new + old chained for grace window).
    #   2. Re-issue every server cert under ca.new but keep ca-bundle as ssl_ca_file
    #      so old client certs still validate during grace.
    #   3. Re-issue client bundles (apps must redeploy with new cert+CA).
    #   4. After all clients migrated: drop old CA, set ssl_ca_file = ca.new.crt.
    echo "[mtls] CA rotation — destructive; not auto-run."
    cat <<'EOF'
Procedure (manual):
  1. mv $PKI_DIR/ca.crt $PKI_DIR/ca.old.crt
     mv $PKI_DIR/ca.key $PKI_DIR/ca.old.key
  2. ensure_ca   # creates a fresh ca.crt
  3. cat $PKI_DIR/ca.crt $PKI_DIR/ca.old.crt > $PKI_DIR/ca-bundle.crt
  4. Set ssl_ca_file = '/etc/postgresql/16/main/ca-bundle.crt' on every VM.
     Reload PG.
  5. setup-mtls.sh init   # re-issues every server cert under new CA.
  6. setup-mtls.sh client <role> <dir>   # re-issue every client bundle.
  7. After all apps re-deployed (verify via Wazuh: zero auth failures
     for old-CA clients in 24h):
       cp ca.crt ca-bundle.crt   # drop old
       Reload PG.
       shred -u ca.old.{crt,key}
EOF
    ;;
  schedule)
    # Drop a systemd timer that runs `rotate` daily at 03:00 UTC.
    cat > /etc/systemd/system/postgres-aegis-mtls-rotate.service <<EOF
[Unit]
Description=postgres-aegis mTLS cert rotation
[Service]
Type=oneshot
ExecStart=$(readlink -f "$0") rotate
EOF
    cat > /etc/systemd/system/postgres-aegis-mtls-rotate.timer <<EOF
[Unit]
Description=Daily mTLS rotation check
[Timer]
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=30min
Persistent=true
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now postgres-aegis-mtls-rotate.timer
    systemctl list-timers postgres-aegis-mtls-rotate.timer --no-pager
    ;;

  init-hsm)
    # Like 'init' but the CA key lives inside the YubiHSM.
    # Prerequisites:
    #   apt install yubihsm-pkcs11 pkcs11-tools libengine-pkcs11-openssl
    #   yubihsm-connector must be running: systemctl start yubihsm-connector
    #   Auth key ID ${HSM_AUTH_KEY_ID} must have: generate-asymmetric-key,sign-pkcs
    #
    # The CA private key is created with label '${HSM_KEY_LABEL}' in the HSM.
    # Server keys remain on-disk; only CA signing uses HSM.
    ensure_ca_hsm
    while read -r host ip; do issue_server_hsm "$host" "$ip"; done < <(parse_hosts)
    while read -r host ip; do deploy_server_cert "$host" "$ip"; done < <(parse_hosts)
    echo "[mtls-hsm] done. CA key = YubiHSM label '${HSM_KEY_LABEL}'"
    echo "           CA cert = ${PKI_DIR}/ca.crt (public; safe to distribute)"
    ;;

  client-hsm)
    # Issue a client bundle signed by the HSM-held CA key.
    ensure_ca_hsm
    issue_client_hsm "${2:?role}" "${3:?out-dir}"
    ;;

  rotate-hsm)
    # Rotate server certs, signing new ones with the HSM-held CA.
    # Same logic as 'rotate' but uses issue_server_hsm + issue_client_hsm.
    : "${RENEW_DAYS:=14}"
    LOG="/var/log/postgres-aegis-mtls-rotate.log"
    echo "===== HSM rotation pass $(date -u '+%Y-%m-%dT%H:%M:%SZ') =====" | tee -a "$LOG"
    rotated=0
    while read -r host ip; do
      crt="${PKI_DIR}/server-${host}.crt"
      [ -f "$crt" ] || { echo "  $host: no cert (run 'init-hsm' first)" | tee -a "$LOG"; continue; }
      end=$(openssl x509 -in "$crt" -noout -enddate | cut -d= -f2)
      end_ts=$(date -d "$end" +%s 2>/dev/null || gdate -d "$end" +%s 2>/dev/null)
      remain_days=$(( ($(date +%s) - end_ts) / -86400 ))
      if [ "$remain_days" -gt "$RENEW_DAYS" ]; then
        echo "  $host: ${remain_days}d remain — keep" | tee -a "$LOG"
        continue
      fi
      echo "  $host: ${remain_days}d remain — RE-ISSUING via HSM" | tee -a "$LOG"
      mv "${PKI_DIR}/server-${host}.crt" "${PKI_DIR}/server-${host}.crt.old"
      mv "${PKI_DIR}/server-${host}.key" "${PKI_DIR}/server-${host}.key.old"
      issue_server_hsm "$host" "$ip"
      deploy_server_cert "$host" "$ip"
      shred -u "${PKI_DIR}/server-${host}.crt.old" "${PKI_DIR}/server-${host}.key.old" 2>/dev/null || true
      rotated=$((rotated + 1))
      echo "    deployed+reloaded $host (HSM-signed)" | tee -a "$LOG"
    done < <(parse_hosts)
    echo "===== HSM rotation done: ${rotated} cert(s) re-issued =====" | tee -a "$LOG"
    ;;

  hsm-status)
    echo "[mtls-hsm] Checking YubiHSM connector at ${HSM_CONNECTOR} ..."
    curl -sf "${HSM_CONNECTOR}/connector/status" || { echo "ERROR: connector not reachable"; exit 1; }
    echo
    echo "[mtls-hsm] HSM objects with label '${HSM_KEY_LABEL}':"
    pkcs11-tool --module "${PKCS11_LIB}" --list-objects 2>/dev/null | grep -A2 "${HSM_KEY_LABEL}" || echo "(none found)"
    ;;

  *)
    echo "usage: $0 {init|init-hsm|client <role> <dir>|client-hsm <role> <dir>|rotate|rotate-hsm|rotate-ca|schedule|hsm-status}"
    exit 2
    ;;
esac
