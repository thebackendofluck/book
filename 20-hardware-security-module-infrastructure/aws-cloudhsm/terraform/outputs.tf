# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

output "cloudhsm_cluster_id" {
  description = "CloudHSM v2 cluster ID — required for client SDK configuration"
  value       = aws_cloudhsm_v2_cluster.main.cluster_id
}

output "cloudhsm_cluster_state" {
  description = "CloudHSM cluster state. Must be ACTIVE before OpenBao can use it."
  value       = aws_cloudhsm_v2_cluster.main.cluster_state
}

output "cloudhsm_cluster_certificates" {
  description = "CloudHSM cluster certificates. Required for client configuration and initial trust establishment."
  value       = aws_cloudhsm_v2_cluster.main.cluster_certificates
  sensitive   = true
}

output "cloudhsm_security_group_id" {
  description = "Security group ID applied to the CloudHSM cluster (TCP 2223-2225)"
  value       = aws_security_group.hsm_cluster.id
}

output "openbao_security_group_id" {
  description = "Security group ID for OpenBao EC2 nodes (TCP 8200/8201)"
  value       = aws_security_group.openbao.id
}

output "openbao_nlb_dns_name" {
  description = "Internal NLB DNS name for OpenBao API. Use this as VAULT_ADDR / BAO_ADDR in platform services."
  value       = aws_lb.openbao.dns_name
}

output "openbao_nlb_arn" {
  description = "Internal NLB ARN"
  value       = aws_lb.openbao.arn
}

output "openbao_asg_name" {
  description = "OpenBao Auto Scaling Group name"
  value       = aws_autoscaling_group.openbao.name
}

output "openbao_iam_role_arn" {
  description = "IAM role ARN attached to OpenBao EC2 instances — attach additional policies here"
  value       = aws_iam_role.openbao.arn
}

output "openbao_kms_key_arn" {
  description = "KMS key ARN used for OpenBao fallback unseal and EBS encryption"
  value       = aws_kms_key.openbao_unseal.arn
}

output "openbao_kms_key_id" {
  description = "KMS key ID used for OpenBao fallback unseal"
  value       = aws_kms_key.openbao_unseal.key_id
}

output "backup_bucket_name" {
  description = "S3 bucket name for CloudHSM backups and NLB access logs"
  value       = aws_s3_bucket.backups.bucket
}

output "backup_bucket_arn" {
  description = "S3 bucket ARN for CloudHSM backups"
  value       = aws_s3_bucket.backups.arn
}

output "audit_log_group_name" {
  description = "CloudWatch log group for OpenBao audit logs (PCI DSS Req. 10.2)"
  value       = aws_cloudwatch_log_group.openbao_audit.name
}

output "operational_log_group_name" {
  description = "CloudWatch log group for OpenBao operational logs"
  value       = aws_cloudwatch_log_group.openbao_operational.name
}
