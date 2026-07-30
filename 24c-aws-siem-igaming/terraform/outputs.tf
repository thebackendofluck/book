# Companion code for "The Backend of Luck" - Chapter 24c, AWS SIEM Implementation for iGaming Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Outputs - AWS SIEM for iGaming Compliance
# =============================================================================
# Key ARNs and identifiers for integration with other modules.
# =============================================================================

# --- GuardDuty ---

output "guardduty_detector_id" {
  description = "GuardDuty detector ID"
  value       = aws_guardduty_detector.main.id
}

output "guardduty_detector_arn" {
  description = "GuardDuty detector ARN"
  value       = aws_guardduty_detector.main.arn
}

# --- Security Hub ---

output "securityhub_arn" {
  description = "Security Hub ARN"
  value       = aws_securityhub_account.main.arn
}

# --- CloudTrail ---

output "cloudtrail_arn" {
  description = "CloudTrail trail ARN"
  value       = aws_cloudtrail.main.arn
}

output "cloudtrail_log_group_arn" {
  description = "CloudWatch Log Group ARN for CloudTrail"
  value       = aws_cloudwatch_log_group.cloudtrail.arn
}

output "cloudtrail_log_group_name" {
  description = "CloudWatch Log Group name for CloudTrail"
  value       = aws_cloudwatch_log_group.cloudtrail.name
}

# --- KMS ---

output "kms_key_arn" {
  description = "KMS key ARN used for encrypting all security logs"
  value       = aws_kms_key.cloudtrail.arn
}

output "kms_key_alias" {
  description = "KMS key alias"
  value       = aws_kms_alias.cloudtrail.name
}

# --- S3 Log Archive ---

output "log_archive_bucket_name" {
  description = "S3 bucket name for the 7-year log archive"
  value       = aws_s3_bucket.log_archive.id
}

output "log_archive_bucket_arn" {
  description = "S3 bucket ARN for the 7-year log archive"
  value       = aws_s3_bucket.log_archive.arn
}

# --- SNS Topics ---

output "sns_security_critical_arn" {
  description = "SNS topic ARN for critical security alerts"
  value       = aws_sns_topic.security_critical.arn
}

output "sns_compliance_alerts_arn" {
  description = "SNS topic ARN for compliance alerts"
  value       = aws_sns_topic.compliance_alerts.arn
}

output "sns_fraud_alerts_arn" {
  description = "SNS topic ARN for fraud/AML alerts"
  value       = aws_sns_topic.fraud_alerts.arn
}

output "sns_security_info_arn" {
  description = "SNS topic ARN for informational security alerts"
  value       = aws_sns_topic.security_info.arn
}

# --- Lambda ---

output "alert_processor_function_arn" {
  description = "Lambda function ARN for the alert processor"
  value       = aws_lambda_function.alert_processor.arn
}

output "alert_processor_function_name" {
  description = "Lambda function name for the alert processor"
  value       = aws_lambda_function.alert_processor.function_name
}

# --- WAF ---

output "waf_web_acl_arn" {
  description = "WAF Web ACL ARN - attach to ALB or CloudFront distribution"
  value       = aws_wafv2_web_acl.igaming.arn
}

output "waf_web_acl_id" {
  description = "WAF Web ACL ID"
  value       = aws_wafv2_web_acl.igaming.id
}

output "waf_manual_blocklist_arn" {
  description = "WAF IP Set ARN for manual IP blocking"
  value       = aws_wafv2_ip_set.manual_blocklist.arn
}

output "waf_protected_resource_arns" {
  description = "Resources the Web ACL is actually attached to. An empty list on REGIONAL scope means the rules are evaluating nothing"
  value       = [for a in aws_wafv2_web_acl_association.igaming : a.resource_arn]
}

# --- Application Log Archive Delivery ---

output "log_archive_delivery_streams" {
  description = "Firehose delivery streams carrying application log groups into the 7-year S3 archive"
  value       = { for k, s in aws_kinesis_firehose_delivery_stream.log_archive : k => s.name }
}

output "log_archive_app_logs_prefix" {
  description = "S3 prefix where application, auth, payment, game and security event logs land (Hive-partitioned by date for Athena)"
  value       = "s3://${aws_s3_bucket.log_archive.id}/app-logs/"
}

# --- CloudWatch Log Groups ---

output "log_group_application" {
  description = "CloudWatch Log Group for application logs"
  value       = aws_cloudwatch_log_group.application.name
}

output "log_group_authentication" {
  description = "CloudWatch Log Group for authentication logs"
  value       = aws_cloudwatch_log_group.authentication.name
}

output "log_group_payment" {
  description = "CloudWatch Log Group for payment transaction logs"
  value       = aws_cloudwatch_log_group.payment.name
}

output "log_group_game_events" {
  description = "CloudWatch Log Group for game event logs"
  value       = aws_cloudwatch_log_group.game_events.name
}

output "log_group_security" {
  description = "CloudWatch Log Group for security event logs"
  value       = aws_cloudwatch_log_group.security_events.name
}

output "log_group_waf" {
  description = "CloudWatch Log Group for WAF request logs"
  value       = aws_cloudwatch_log_group.waf.name
}

# --- Config ---

output "config_recorder_id" {
  description = "AWS Config recorder ID"
  value       = aws_config_configuration_recorder.main.id
}

# --- Summary ---

output "deployment_summary" {
  description = "Summary of deployed SIEM components"
  value = {
    guardduty_enabled   = true
    securityhub_enabled = true
    cloudtrail_enabled  = true
    config_enabled      = true
    waf_enabled         = true
    log_retention_days  = 2555
    encryption          = "KMS (AES-256)"
    region              = var.aws_region
    environment         = var.environment
    compliance          = var.compliance_frameworks
  }
}
