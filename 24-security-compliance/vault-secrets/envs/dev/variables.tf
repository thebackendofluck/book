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
    # brand-alpha namespace
    {
      namespace       = "brand-alpha"
      service_account = "platform-brand-alpha"
    },
    {
      namespace       = "brand-alpha"
      service_account = "spoke-platform-mi-brand-alpha"
    },
    {
      namespace       = "brand-alpha"
      service_account = "spoke-platform-pa-brand-alpha"
    },
    {
      namespace       = "brand-alpha"
      service_account = "mailer-brand-alpha"
    },
    {
      namespace       = "brand-alpha"
      service_account = "globalid-brand-alpha"
    },
    {
      namespace       = "brand-alpha"
      service_account = "payments-brand-alpha"
    },
    {
      namespace       = "brand-alpha"
      service_account = "backoffice-brand-alpha"
    },
    # brand-beta namespace
    {
      namespace       = "brand-beta"
      service_account = "platform-brand-beta"
    },
    {
      namespace       = "brand-beta"
      service_account = "spoke-platform-nj-brand-beta"
    },
    {
      namespace       = "brand-beta"
      service_account = "spoke-platform-pa-brand-beta"
    },
    {
      namespace       = "brand-beta"
      service_account = "globalid-brand-beta"
    },
    {
      namespace       = "brand-beta"
      service_account = "payments-brand-beta"
    },
    # brand-gamma namespace
    {
      namespace       = "nl-pnp-brand-gamma"
      service_account = "platform-nl-pnp-brand-gamma"
    },
    {
      namespace       = "nl-pnp-brand-gamma"
      service_account = "mailer-nl-pnp-brand-gamma"
    },
    {
      namespace       = "nl-pnp-brand-gamma"
      service_account = "globalid-nl-pnp-brand-gamma"
    },
    {
      namespace       = "nl-pnp-brand-gamma"
      service_account = "payments-nl-pnp-brand-gamma"
    },
    {
      namespace       = "nl-pnp-brand-gamma"
      service_account = "backoffice-nl-pnp-brand-gamma"
    },
  ]
}
