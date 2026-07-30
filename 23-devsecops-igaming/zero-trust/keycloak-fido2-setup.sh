#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# keycloak-fido2-setup.sh
# Configure Keycloak realm for FIDO2 enforcement with Tailscale OIDC
# Prerequisites: Keycloak 24+ running, admin CLI available
#
# Chapter 23 — DevSecOps for iGaming

set -euo pipefail

KEYCLOAK_URL="${KEYCLOAK_URL:-https://auth.acmetocasino.com}"
REALM="igaming"
ADMIN_USER="${KEYCLOAK_ADMIN:-admin}"
ADMIN_PASS="${KEYCLOAK_ADMIN_PASSWORD:?Set KEYCLOAK_ADMIN_PASSWORD}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# Authenticate to Keycloak admin API
log "Authenticating to Keycloak..."
TOKEN=$(curl -sf -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=${ADMIN_USER}" \
    -d "password=${ADMIN_PASS}" \
    -d "grant_type=password" \
    -d "client_id=admin-cli" | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

AUTH="Authorization: Bearer ${TOKEN}"

# Create OIDC client for Tailscale
log "Creating Tailscale OIDC client..."
curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/clients" \
    -H "${AUTH}" \
    -H "Content-Type: application/json" \
    -d '{
    "clientId": "tailscale",
    "name": "Tailscale VPN",
    "enabled": true,
    "protocol": "openid-connect",
    "publicClient": false,
    "standardFlowEnabled": true,
    "directAccessGrantsEnabled": false,
    "serviceAccountsEnabled": false,
    "redirectUris": ["https://login.tailscale.com/a/oauth_response"],
    "webOrigins": ["https://login.tailscale.com"],
    "defaultClientScopes": ["openid", "email", "profile"],
    "attributes": {
        "pkce.code.challenge.method": "S256"
    }
}'

# Get the client UUID
CLIENT_UUID=$(curl -sf "${KEYCLOAK_URL}/admin/realms/${REALM}/clients?clientId=tailscale" \
    -H "${AUTH}" | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])")

# Get the client secret
CLIENT_SECRET=$(curl -sf "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}/client-secret" \
    -H "${AUTH}" | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])")

# Create FIDO2 WebAuthn authentication flow
log "Creating FIDO2 authentication flow..."
# Step 1: Copy the browser flow
curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/authentication/flows/browser/copy" \
    -H "${AUTH}" \
    -H "Content-Type: application/json" \
    -d '{"newName": "browser-fido2-required"}'

# Step 2: Get the flow executions
FLOW_ID=$(curl -sf "${KEYCLOAK_URL}/admin/realms/${REALM}/authentication/flows" \
    -H "${AUTH}" | python3 -c "
import json, sys
flows = json.load(sys.stdin)
for f in flows:
    if f['alias'] == 'browser-fido2-required':
        print(f['id'])
        break
")

# Step 3: Add WebAuthn Passwordless execution
curl -sf -X POST \
    "${KEYCLOAK_URL}/admin/realms/${REALM}/authentication/flows/browser-fido2-required/executions/execution" \
    -H "${AUTH}" \
    -H "Content-Type: application/json" \
    -d '{"provider": "webauthn-authenticator-passwordless"}'

# Step 4: Set WebAuthn execution to REQUIRED
EXECUTIONS=$(curl -sf \
    "${KEYCLOAK_URL}/admin/realms/${REALM}/authentication/flows/browser-fido2-required/executions" \
    -H "${AUTH}")

WEBAUTHN_EXEC_ID=$(echo "${EXECUTIONS}" | python3 -c "
import json, sys
execs = json.load(sys.stdin)
for e in execs:
    if e.get('providerId') == 'webauthn-authenticator-passwordless':
        print(e['id'])
        break
")

curl -sf -X PUT \
    "${KEYCLOAK_URL}/admin/realms/${REALM}/authentication/executions/${WEBAUTHN_EXEC_ID}" \
    -H "${AUTH}" \
    -H "Content-Type: application/json" \
    -d "{\"id\": \"${WEBAUTHN_EXEC_ID}\", \"requirement\": \"REQUIRED\"}"

# Step 5: Bind the flow to the Tailscale client
curl -sf -X PUT "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}" \
    -H "${AUTH}" \
    -H "Content-Type: application/json" \
    -d "{\"authenticationFlowBindingOverrides\": {\"browser\": \"${FLOW_ID}\"}}"

log "FIDO2 flow bound to Tailscale client"

# Configure WebAuthn policy at realm level
log "Setting WebAuthn policy..."
curl -sf -X PUT "${KEYCLOAK_URL}/admin/realms/${REALM}" \
    -H "${AUTH}" \
    -H "Content-Type: application/json" \
    -d '{
    "webAuthnPolicyPasswordlessRpEntityName": "AcmeToCasino",
    "webAuthnPolicyPasswordlessSignatureAlgorithms": ["ES256"],
    "webAuthnPolicyPasswordlessRpId": "auth.acmetocasino.com",
    "webAuthnPolicyPasswordlessAttestationConveyancePreference": "direct",
    "webAuthnPolicyPasswordlessAuthenticatorAttachment": "cross-platform",
    "webAuthnPolicyPasswordlessRequireResidentKey": "No",
    "webAuthnPolicyPasswordlessUserVerificationRequirement": "required",
    "webAuthnPolicyPasswordlessCreateTimeout": 60,
    "webAuthnPolicyPasswordlessAvoidSameAuthenticatorRegister": true
}'

cat <<TAILSCALE_CONFIG

==============================================================
Tailscale OIDC Configuration
==============================================================
Add these settings in the Tailscale admin console:
  Settings > Identity Providers > Add OIDC

  Issuer URL:     ${KEYCLOAK_URL}/realms/${REALM}
  Client ID:      tailscale
  Client Secret:  ${CLIENT_SECRET}

Users must register a YubiKey (FIDO2) before they can
authenticate to Tailscale. The Keycloak flow enforces this.
==============================================================

TAILSCALE_CONFIG
