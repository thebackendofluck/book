# Companion code for "The Backend of Luck" - Chapter 39, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# vault_roles.tf
# HashiCorp Vault Kubernetes auth backend roles
# Binds each K8s service account to its namespace and Vault policy.
# token_ttl=3600: stolen tokens expire within 1 hour (lateral movement mitigation)
#
# Chapter 39: Security Incident Response -- post-incident Vault migration

# Kubernetes auth backend (assumed already configured)
data "vault_auth_backend" "kubernetes" {
  path = "kubernetes"
}

# vault_roles.tf -- bind each K8s service account to its namespace
resource "vault_kubernetes_auth_backend_role" "app_roles" {
  for_each = { for apps in var.applications : apps.service_account => apps }

  backend                          = data.vault_auth_backend.kubernetes.path
  role_name                        = each.value.service_account
  bound_service_account_names      = [each.value.service_account]
  bound_service_account_namespaces = [each.value.namespace]
  token_policies                   = [each.value.namespace]
  token_ttl                        = 3600   # 1 hour — mitigates stolen token lateral movement

  depends_on = [vault_policy.app_roles_policy]
}
