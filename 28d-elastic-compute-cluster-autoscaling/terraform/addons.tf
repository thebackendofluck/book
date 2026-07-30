# Companion code for "The Backend of Luck" - Chapter 28d, Elastic Compute and Cluster Autoscaling on EKS.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Managed EKS add-ons (CIS / ISO 27001 baseline)
# -----------------------------------------------------------------------------
# vpc-cni is configured with NetworkPolicy enforcement ON so the default-deny
# segmentation in k8s/security/ actually takes effect (ISO A.8.20/A.8.22,
# PCI Req 1). The rest are the standard data-plane + least-privilege add-ons.
# Gated by var.create_addons so the module still applies on emulators whose
# EKS implementation does not cover the addon API.
# =============================================================================

locals {
  managed_addons = {
    vpc-cni = {
      configuration_values = jsonencode({
        enableNetworkPolicy = "true" # verify key against the pinned VPC CNI values.yaml
      })
    }
    coredns                = { configuration_values = null }
    kube-proxy             = { configuration_values = null }
    aws-ebs-csi-driver     = { configuration_values = null }
    eks-pod-identity-agent = { configuration_values = null }
  }
}

resource "aws_eks_addon" "this" {
  for_each = var.create_eks && var.create_addons ? local.managed_addons : {}

  cluster_name                = aws_eks_cluster.this[0].name
  addon_name                  = each.key
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"
  configuration_values        = each.value.configuration_values

  tags = var.tags
}
