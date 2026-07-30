# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

variable "vault_api_token" {
  description = "API Token used to authenticate with vault"
  type        = string
}
variable "vaulted_private_key" {
  description = "Private key used for decrypting secrets and storing them on vault"
  type        = string
}

variable "applications" {
  default = [
    "global-id",
    "platform",
    "payments"
  ]
}
