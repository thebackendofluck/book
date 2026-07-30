# Policy for the service that VERIFIES audit chain entries (the auditor).
#
# Verify only. Bind this to an identity the audit emitter cannot obtain,
# influence or impersonate. Concretely, that means at minimum:
#
#   * a separate AppRole (or Kubernetes service account) whose secret_id the
#     emitter's host never sees;
#   * running on a different host from the emitter, so compromising the
#     emitter's machine does not hand over the verifier's credential;
#   * ideally a different trust domain and a different on-call rota -- an
#     internal audit or compliance function rather than the platform team that
#     operates the emitter.
#
# If the same team, host or credential can both sign and verify, the audit
# chain proves nothing that the signer does not choose to let it prove. A
# forged entry re-signed with the legitimate key verifies correctly; the only
# thing that catches it is a verifier outside the signer's reach comparing what
# the chain says against an independent record.
#
# Note that transit/verify takes an "update" capability despite being a
# read-only operation: OpenBao models it as a write because the request body
# carries the input and the signature. Granting "update" here does not let the
# holder produce signatures -- transit/sign is a separate path, denied below.

path "transit/verify/platform-audit-sign" {
  capabilities = ["update"]
}

# Explicitly denied: the verifier must never be able to sign. Without this,
# a compromised verifier could forge entries and the separation of duties
# collapses in the other direction.
path "transit/sign/platform-audit-sign" {
  capabilities = ["deny"]
}

# The verifier needs the public half to check signatures offline. Reading the
# key metadata exposes the public key and version list, not the private key,
# and it lets the verifier notice a rotation it was not told about.
path "transit/keys/platform-audit-sign" {
  capabilities = ["read"]
}

# Read-only on the key: no rotation, no trimming, no config changes.
path "transit/keys/platform-audit-sign/*" {
  capabilities = ["deny"]
}
