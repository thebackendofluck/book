# Companion code for "The Backend of Luck" - Chapter 28d, Elastic Compute and Cluster Autoscaling on EKS.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

output "cluster_name" {
  description = "EKS cluster name (used by Karpenter discovery tags)"
  value       = var.create_eks ? aws_eks_cluster.this[0].name : null
}

output "cluster_endpoint" {
  description = "EKS API server endpoint"
  value       = var.create_eks ? aws_eks_cluster.this[0].endpoint : null
}

output "node_role_arn" {
  description = "Worker node IAM role ARN — referenced by the Karpenter EC2NodeClass"
  value       = aws_iam_role.node.arn
}

output "karpenter_controller_role_arn" {
  description = "Karpenter controller IAM role ARN — bound via EKS Pod Identity"
  value       = aws_iam_role.karpenter.arn
}

output "interruption_queue_name" {
  description = "SQS interruption queue name — set as Karpenter's --interruption-queue"
  value       = aws_sqs_queue.interruption.name
}

output "ecr_repository_urls" {
  description = "Multi-arch ECR repositories"
  value       = { for k, r in aws_ecr_repository.service : k => r.repository_url }
}

output "private_subnet_ids" {
  description = "Subnets tagged for Karpenter discovery"
  value       = aws_subnet.private[*].id
}
