# Least-authority policy for services that only need to encrypt and decrypt
# PII columns via the Transit engine. Does NOT grant list or key management.
#
# Apply with:
#   bao policy write platform-pii-encrypt policies/platform-pii-encrypt.hcl

path "transit/encrypt/platform-pii" {
  capabilities = ["update"]
}

path "transit/decrypt/platform-pii" {
  capabilities = ["update"]
}

# Belt and braces: explicitly deny any key-management capability on any
# transit key, so that a token with this policy cannot be used to rotate,
# export, or configure keys even if another policy attached to the same
# token were to grant those capabilities.
path "transit/keys/*" {
  capabilities = ["deny"]
}

# Explicit deny on root-token-level paths that should never be reachable
# from a workload token under any circumstances.
path "sys/init" {
  capabilities = ["deny"]
}

path "sys/seal" {
  capabilities = ["deny"]
}
