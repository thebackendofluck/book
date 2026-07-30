# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# default application policy -- simplified for production
resource "vault_policy" "app_roles_policy" {
  for_each = toset(var.applications)
  name     = each.value

  policy = <<EOT
path "secret/${each.value}/*" {
  policy = "read"
}
path "secret/${each.value}" {
  policy = "read"
}
EOT
}
