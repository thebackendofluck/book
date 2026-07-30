# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Example of an encrypted secret stored via the vaulted provider.
# The encrypted payload is generated using the public key in files/public.pem.
# Only the Vault server with the corresponding private key can decrypt it.
resource "vaulted_vault_secret" "backoffice_recruitment_tests_rds_password" {
  path         = "secret/backoffice/tests_rds_password"
  payload_json = "$VED;1.0::ENCRYPTED_PAYLOAD_PLACEHOLDER"
}
