# Companion code for "The Backend of Luck" - Chapter 39, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# vault_policy.tf
# HashiCorp Vault per-application read policies
# Each service gets exactly one policy scoped to its own namespace/secrets path.
# No cross-namespace reads — prevents lateral movement if a service account is compromised.
#
# Usage: terraform apply -var-file=applications.tfvars
# Chapter 39: Security Incident Response -- post-incident Vault migration

variable "applications" {
  description = "List of application definitions for Vault policy creation"
  type = list(object({
    service_account = string
    namespace       = string
  }))
  default = [
    { service_account = "payments-service",     namespace = "payments" },
    { service_account = "wallet-service",        namespace = "wallet" },
    { service_account = "game-integration",      namespace = "game-integration" },
    { service_account = "player-auth",           namespace = "player-auth" },
    { service_account = "notification-service",  namespace = "notifications" },
    { service_account = "reporting-service",     namespace = "reporting" },
    { service_account = "kyc-service",           namespace = "kyc" },
    { service_account = "bonus-engine",          namespace = "bonus" },
  ]
}

# vault_policy.tf -- one policy per application namespace
resource "vault_policy" "app_roles_policy" {
  for_each = { for apps in var.applications : apps.service_account => apps }
  name     = each.value.namespace

  policy = <<EOT
path "secret/${each.value.namespace}/*" {
  policy = "read"
}
path "secret/${each.value.namespace}" {
  policy = "read"
}
EOT
}
