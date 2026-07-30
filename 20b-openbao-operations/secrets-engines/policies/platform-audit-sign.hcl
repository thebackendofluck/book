# Policy for the service that SIGNS audit chain entries (the emitter).
#
# Sign only. This policy deliberately does not grant transit/verify on the same
# key, and the reason is the whole point of signing the audit chain.
#
# An identity holding both sign and verify on one key can rewrite history and
# make the rewrite pass inspection: it edits an entry, re-signs the edited
# entry with the same key, and then answers "is this chain valid?" with a
# signature it just produced. Every later verification agrees, because the
# chain genuinely is internally consistent -- it is consistent with the forged
# version. The tamper-evidence property survives only if verification is
# performed by a party the signer cannot compromise. See
# platform-audit-verify.hcl for that side.
#
# Bind this policy to the audit emitter only.

path "transit/sign/platform-audit-sign" {
  capabilities = ["update"]
}

# No read on the key metadata, and no write either. Writing the key's config is
# how an attacker with sign rights would otherwise erase the evidence anyway:
# rotating the key, trimming old versions, or raising min_decryption_version
# invalidates or discards earlier signatures wholesale, which turns "the
# signature does not verify" into an operational excuse rather than an alarm.
path "transit/keys/platform-audit-sign" {
  capabilities = ["deny"]
}

path "transit/keys/platform-audit-sign/*" {
  capabilities = ["deny"]
}
