# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

provider "vault" {
  address = "https://vault.k8s-ext.new.acmetocasino.com"
  token   = var.vault_api_token
}
provider "vaulted" {
  address             = "https://vault.k8s-ext.new.acmetocasino.com"
  token               = var.vault_api_token
  skip_tls_verify     = true
  private_key_content = var.vaulted_private_key
  version             = "~>0.4.0"
}
