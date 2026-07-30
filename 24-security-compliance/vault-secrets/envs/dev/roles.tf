# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

resource "vault_kubernetes_auth_backend_role" "app_roles" {
  for_each                         = { for apps in var.applications : apps.service_account => apps }
  backend                          = vault_auth_backend.kubernetes.path
  role_name                        = each.value.service_account
  bound_service_account_names      = [each.value.service_account]
  bound_service_account_namespaces = [each.value.namespace]
  token_policies                   = [each.value.namespace]
  token_ttl                        = 3600
}
