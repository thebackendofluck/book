# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# outputs.tf — Root module outputs
# Chapter 24: IP Detection & DDoS Protection
# =============================================================================

# ---------------------------------------------------------------------------
# AWS outputs
# ---------------------------------------------------------------------------

output "api_gateway_url" {
  description = "HTTP API v2 invoke URL for the ip-gate Lambda. Pass to clients as the security check endpoint."
  value       = module.aws.api_gateway_url
}

output "waf_webacl_arn" {
  description = "ARN of the regional WAF WebACL. Associate with ALB/API Gateway as needed."
  value       = module.aws.waf_webacl_arn
}

output "waf_webacl_id" {
  description = "ID of the regional WAF WebACL."
  value       = module.aws.waf_webacl_id
}

output "dynamodb_ip_blacklist_table" {
  description = "Name of the DynamoDB IP blacklist table."
  value       = module.aws.dynamodb_ip_blacklist_table
}

output "dynamodb_device_fingerprints_table" {
  description = "Name of the DynamoDB device fingerprints table."
  value       = module.aws.dynamodb_device_fingerprints_table
}

output "dynamodb_marketing_calendar_table" {
  description = "Name of the DynamoDB marketing calendar table (campaign scaling triggers)."
  value       = module.aws.dynamodb_marketing_calendar_table
}

output "dynamodb_scaling_state_table" {
  description = "Name of the DynamoDB scaling state table."
  value       = module.aws.dynamodb_scaling_state_table
}

output "dynamodb_ip_block_log_table" {
  description = "Name of the DynamoDB IP block event log table."
  value       = module.aws.dynamodb_ip_block_log_table
}

output "s3_threat_lists_bucket" {
  description = "Name of the S3 bucket storing versioned threat lists (GeoIP DBs, OFAC SDN)."
  value       = module.aws.s3_threat_lists_bucket
}

output "s3_attack_evidence_bucket" {
  description = "Name of the S3 bucket storing attack evidence (retained for compliance)."
  value       = module.aws.s3_attack_evidence_bucket
}

output "lambda_ip_gate_arn" {
  description = "ARN of the ip-gate Lambda function."
  value       = module.aws.lambda_ip_gate_arn
}

output "lambda_ddos_detector_arn" {
  description = "ARN of the DDoS detector Lambda function."
  value       = module.aws.lambda_ddos_detector_arn
}

output "lambda_campaign_autoscaler_arn" {
  description = "ARN of the campaign autoscaler Lambda function."
  value       = module.aws.lambda_campaign_autoscaler_arn
}

output "lambda_sdn_refresh_arn" {
  description = "ARN of the SDN list refresh Lambda function."
  value       = module.aws.lambda_sdn_refresh_arn
}

output "sns_noc_alerts_arn" {
  description = "ARN of the SNS topic for NOC security alerts."
  value       = module.aws.sns_noc_alerts_arn
}

output "elasticache_endpoint" {
  description = "ElastiCache Redis primary endpoint. Empty string if elasticache_enabled = false."
  value       = module.aws.elasticache_endpoint
}

output "ssm_maxmind_key_path" {
  description = "SSM Parameter Store path where the MaxMind license key is stored."
  value       = module.aws.ssm_maxmind_key_path
}

output "ssm_ip_reputation_key_path" {
  description = "SSM Parameter Store path where the IP reputation API key is stored."
  value       = module.aws.ssm_ip_reputation_key_path
}

# ---------------------------------------------------------------------------
# Cloudflare outputs
# ---------------------------------------------------------------------------

output "cf_kv_ip_blacklist_id" {
  description = "Cloudflare KV namespace ID for IP_BLACKLIST. Add to wrangler.toml."
  value       = module.cloudflare.kv_ip_blacklist_id
}

output "cf_kv_device_fingerprints_id" {
  description = "Cloudflare KV namespace ID for DEVICE_FINGERPRINTS."
  value       = module.cloudflare.kv_device_fingerprints_id
}

output "cf_kv_fraud_velocity_id" {
  description = "Cloudflare KV namespace ID for FRAUD_VELOCITY."
  value       = module.cloudflare.kv_fraud_velocity_id
}

output "cf_kv_sanctions_list_id" {
  description = "Cloudflare KV namespace ID for SANCTIONS_LIST."
  value       = module.cloudflare.kv_sanctions_list_id
}

output "cf_kv_rate_limits_id" {
  description = "Cloudflare KV namespace ID for RATE_LIMITS."
  value       = module.cloudflare.kv_rate_limits_id
}

output "cf_kv_campaigns_id" {
  description = "Cloudflare KV namespace ID for CAMPAIGNS."
  value       = module.cloudflare.kv_campaigns_id
}

output "cf_kv_attack_log_id" {
  description = "Cloudflare KV namespace ID for ATTACK_LOG."
  value       = module.cloudflare.kv_attack_log_id
}

output "cf_kv_ja3_blocklist_id" {
  description = "Cloudflare KV namespace ID for JA3_BLOCKLIST."
  value       = module.cloudflare.kv_ja3_blocklist_id
}

output "cf_worker_ip_detection_id" {
  description = "Cloudflare Worker script name for the ip-detection pipeline."
  value       = module.cloudflare.worker_ip_detection_id
}

output "cf_worker_edge_classifier_id" {
  description = "Cloudflare Worker script name for the edge classifier."
  value       = module.cloudflare.worker_edge_classifier_id
}

output "cf_worker_url" {
  description = "Base URL where the ip-detection Worker handles requests."
  value       = "https://${var.cloudflare_domain}"
}

# ---------------------------------------------------------------------------
# On-premises outputs
# ---------------------------------------------------------------------------

output "onpremise_service_status_command" {
  description = "SSH command to check the on-premises ip-detection service status."
  value       = "ssh -p ${var.onpremise_ssh_port} ${var.onpremise_user}@${var.onpremise_host} 'systemctl status ip-detection'"
}

output "onpremise_app_dir" {
  description = "Installation directory of the ip-detection service on the on-premises server."
  value       = var.onpremise_app_dir
}
