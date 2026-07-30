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


set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:-https://10.0.0.11:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-}"
ROLE_NAME="${ROLE_NAME:-k8s-cert-issuer}"
POLICY_NAME="${POLICY_NAME:-k8s-cert-issuer}"

if [ -z "$VAULT_TOKEN" ]; then
    echo "Error: VAULT_TOKEN environment variable is not set"
    exit 1
fi

echo "Setting up Kubernetes certificate issuer authentication in OpenBao"
echo "Vault Address: $VAULT_ADDR"
echo "Policy Name: $POLICY_NAME"
echo "Role Name: $ROLE_NAME"
echo ""

export VAULT_ADDR
export VAULT_TOKEN
export VAULT_SKIP_VERIFY=true

enable_approle() {
    echo "[*] Enabling AppRole auth method..."
    if vault auth list -format=json | grep -q '"approle/"'; then
        echo "    AppRole already enabled"
    else
        vault auth enable approle
        echo "    AppRole auth method enabled"
    fi
}

create_policy() {
    echo "[*] Creating policy '$POLICY_NAME'..."

    policy_content=$(cat <<'EOF'
path "pki_int/sign/k8s-internal" {
  capabilities = ["create", "update"]
}

path "pki_int/sign/mtls-service" {
  capabilities = ["create", "update"]
}

path "pki_int/issue/k8s-internal" {
  capabilities = ["create", "update"]
}

path "pki_int/issue/mtls-service" {
  capabilities = ["create", "update"]
}

path "auth/approle/role/k8s-cert-issuer" {
  capabilities = ["read"]
}

path "auth/approle/role/k8s-cert-issuer/secret-id" {
  capabilities = ["update"]
}

path "sys/leases/renew" {
  capabilities = ["update"]
}
EOF
)

    echo "$policy_content" | vault policy write "$POLICY_NAME" -
    echo "    Policy '$POLICY_NAME' created"
}

create_approle() {
    echo "[*] Creating AppRole '$ROLE_NAME'..."

    if vault read "auth/approle/role/$ROLE_NAME" >/dev/null 2>&1; then
        echo "    AppRole '$ROLE_NAME' already exists"
        vault delete "auth/approle/role/$ROLE_NAME/secret-id" 2>/dev/null || true
    fi

    vault write "auth/approle/role/$ROLE_NAME" \
        token_num_uses=0 \
        token_ttl=1h \
        token_max_ttl=4h \
        bind_secret_id=true \
        secret_id_ttl=0 \
        secret_id_num_uses=0 \
        policies="$POLICY_NAME"

    echo "    AppRole '$ROLE_NAME' created"
}

get_role_id() {
    echo "[*] Retrieving Role ID..."
    role_id=$(vault read "auth/approle/role/$ROLE_NAME/role-id" -format=json | jq -r '.data.role_id')
    echo "    Role ID: $role_id"
    echo "$role_id"
}

get_secret_id() {
    echo "[*] Generating new Secret ID..."
    secret_id=$(vault write "auth/approle/role/$ROLE_NAME/secret-id" -format=json | jq -r '.data.secret_id')
    echo "    Secret ID: $secret_id (truncated for security)"
    echo "$secret_id"
}

main() {
    enable_approle
    create_policy
    create_approle

    role_id=$(get_role_id)
    secret_id=$(get_secret_id)

    echo ""
    echo "=========================================="
    echo "Kubernetes Certificate Issuer Setup Complete"
    echo "=========================================="
    echo ""
    echo "Update the following in cluster-issuer.yaml:"
    echo ""
    echo "1. ClusterIssuer roleId:"
    echo "   roleId: $role_id"
    echo ""
    echo "2. Secret openbao-approle-secret:"
    echo "   kubectl create secret generic openbao-approle-secret \\"
    echo "     --from-literal=secret-id='$secret_id' \\"
    echo "     -n cert-manager"
    echo ""
    echo "Or update the existing secret:"
    echo "   kubectl patch secret openbao-approle-secret \\"
    echo "     -n cert-manager \\"
    echo "     -p '{\"stringData\":{\"secret-id\":\"$secret_id\"}}'"
    echo ""
    echo "Test AppRole authentication:"
    echo "   curl -X POST $VAULT_ADDR/v1/auth/approle/login \\"
    echo "     -d '{\"role_id\":\"$role_id\",\"secret_id\":\"$secret_id\"}'"
    echo ""
}

main "$@"
