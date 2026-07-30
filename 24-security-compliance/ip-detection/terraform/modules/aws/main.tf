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
# modules/aws/main.tf
# Chapter 24: AWS layer — DynamoDB, Lambda, API Gateway, WAF, CloudWatch, SNS
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "aws_region" { type = string }
variable "aws_account_id" { type = string }
variable "environment" { type = string }
variable "name_suffix" { type = string }
variable "kms_key_arn" { type = string }
variable "lambda_memory_mb" { type = number }
variable "lambda_timeout_seconds" { type = number }
variable "fraud_score_block_threshold" { type = number }
variable "fraud_score_review_threshold" { type = number }
variable "elasticache_enabled" { type = bool }
variable "elasticache_node_type" { type = string }
variable "attack_evidence_retention_days" { type = number }
variable "waf_block_threshold" { type = number }
variable "threat_list_schedule" { type = string }
variable "alert_email" { type = string }
variable "alert_pagerduty_url" { type = string }
variable "maxmind_license_key" {
  type      = string
  sensitive = true
}
variable "ip_reputation_api_key" {
  type      = string
  sensitive = true
}
variable "common_tags" { type = map(string) }

variable "scale_profiles" {
  type = map(object({
    min_capacity     = number
    max_capacity     = number
    cooldown_seconds = number
  }))
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  use_custom_kms = var.kms_key_arn != ""
  is_prod        = var.environment == "production"

  # All Lambda functions share this base environment.
  lambda_base_env = {
    ENVIRONMENT              = var.environment
    AWS_REGION_NAME          = var.aws_region
    FRAUD_SCORE_THRESHOLD    = tostring(var.fraud_score_block_threshold)
    FRAUD_SCORE_REVIEW       = tostring(var.fraud_score_review_threshold)
    DYNAMODB_BLACKLIST_TABLE = aws_dynamodb_table.ip_blacklist.name
    DYNAMODB_FP_TABLE        = aws_dynamodb_table.device_fingerprints.name
    DYNAMODB_CALENDAR_TABLE  = aws_dynamodb_table.marketing_calendar.name
    DYNAMODB_SCALING_TABLE   = aws_dynamodb_table.scaling_state.name
    DYNAMODB_BLOCK_LOG_TABLE = aws_dynamodb_table.ip_block_log.name
    S3_THREAT_LISTS_BUCKET   = aws_s3_bucket.threat_lists.bucket
    S3_EVIDENCE_BUCKET       = aws_s3_bucket.attack_evidence.bucket
    SNS_ALERTS_TOPIC_ARN     = aws_sns_topic.noc_alerts.arn
    POWERTOOLS_SERVICE_NAME  = "igaming-ip-gate"
    LOG_LEVEL                = local.is_prod ? "INFO" : "DEBUG"
  }
}

# =============================================================================
# Data sources
# =============================================================================

data "aws_partition" "current" {}

# Existing VPC for ElastiCache (only resolved when elasticache_enabled = true).
data "aws_vpc" "default" {
  count   = var.elasticache_enabled ? 1 : 0
  default = true
}

data "aws_subnets" "default" {
  count = var.elasticache_enabled ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }
}

# =============================================================================
# S3 Buckets
# =============================================================================

# Unique suffix to prevent bucket name conflicts across accounts.
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# threat-lists — versioned GeoIP databases + OFAC SDN list.
resource "aws_s3_bucket" "threat_lists" {
  #checkov:skip=CKV2_AWS_62: S3 event notifications are not required for threat list storage — changes are triggered by scheduled Lambda, not S3 events.
  #checkov:skip=CKV_AWS_144: Cross-region replication is not required — this is a single-region deployment by design.
  bucket = "igaming-threat-lists-${var.aws_account_id}-${var.environment}-${random_id.bucket_suffix.hex}"

  tags = merge(var.common_tags, {
    Component = "threat-lists"
    DataClass = "Internal"
  })
}

resource "aws_s3_bucket_versioning" "threat_lists" {
  bucket = aws_s3_bucket.threat_lists.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "threat_lists" {
  bucket = aws_s3_bucket.threat_lists.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = local.use_custom_kms ? "aws:kms" : "AES256"
      kms_master_key_id = local.use_custom_kms ? var.kms_key_arn : null
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "threat_lists" {
  bucket                  = aws_s3_bucket.threat_lists.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "threat_lists" {
  bucket = aws_s3_bucket.threat_lists.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_logging" "threat_lists" {
  bucket        = aws_s3_bucket.threat_lists.id
  target_bucket = aws_s3_bucket.threat_lists.id
  target_prefix = "access-logs/threat-lists/"
}

# attack-evidence — compliance-grade storage for blocked request payloads.
resource "aws_s3_bucket" "attack_evidence" {
  #checkov:skip=CKV2_AWS_62: S3 event notifications are not required for attack evidence storage — writes are driven by Lambda invocation, not S3 events.
  #checkov:skip=CKV_AWS_144: Cross-region replication is not required — this is a single-region deployment by design.
  bucket = "igaming-attack-evidence-${var.aws_account_id}-${var.environment}-${random_id.bucket_suffix.hex}"

  tags = merge(var.common_tags, {
    Component = "attack-evidence"
    DataClass = "Restricted"
    Retention = "${var.attack_evidence_retention_days}d"
  })
}

resource "aws_s3_bucket_versioning" "attack_evidence" {
  bucket = aws_s3_bucket.attack_evidence.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "attack_evidence" {
  bucket = aws_s3_bucket.attack_evidence.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = local.use_custom_kms ? "aws:kms" : "AES256"
      kms_master_key_id = local.use_custom_kms ? var.kms_key_arn : null
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "attack_evidence" {
  bucket                  = aws_s3_bucket.attack_evidence.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "attack_evidence" {
  bucket = aws_s3_bucket.attack_evidence.id

  rule {
    id     = "evidence-retention"
    status = "Enabled"
    filter {}
    expiration {
      days = var.attack_evidence_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    # Transition to cheaper storage after 90 days for cost optimisation.
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 180
      storage_class = "GLACIER"
    }
  }
}

resource "aws_s3_bucket_logging" "attack_evidence" {
  bucket        = aws_s3_bucket.attack_evidence.id
  target_bucket = aws_s3_bucket.attack_evidence.id
  target_prefix = "access-logs/attack-evidence/"
}

# =============================================================================
# DynamoDB Tables
# =============================================================================

# Table 1: IPBlacklist — banned IP addresses with reason codes and TTL.
resource "aws_dynamodb_table" "ip_blacklist" {
  name         = "ip-blacklist${var.name_suffix}"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "ip_address"

  attribute {
    name = "ip_address"
    type = "S"
  }
  attribute {
    name = "reason"
    type = "S"
  }
  attribute {
    name = "added_at"
    type = "N"
  }

  # GSI: query all IPs blocked for a specific reason, sorted by time.
  global_secondary_index {
    name            = "reason-code-index"
    hash_key        = "reason"
    range_key       = "added_at"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = local.is_prod
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = local.use_custom_kms ? var.kms_key_arn : null
  }

  tags = merge(var.common_tags, {
    Component = "ip-blacklist"
  })
}

# Table 2: DeviceFingerprints — JA3/browser fingerprint history per IP and user.
resource "aws_dynamodb_table" "device_fingerprints" {
  name         = "device-fingerprints${var.name_suffix}"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "fingerprint_id"
  range_key = "seen_at"

  attribute {
    name = "fingerprint_id"
    type = "S"
  }
  attribute {
    name = "seen_at"
    type = "N"
  }
  attribute {
    name = "user_id"
    type = "S"
  }
  attribute {
    name = "ip_address"
    type = "S"
  }

  # GSI: all fingerprints for a given user, sorted by time.
  global_secondary_index {
    name            = "user-fp-index"
    hash_key        = "user_id"
    range_key       = "seen_at"
    projection_type = "ALL"
  }

  # GSI: all fingerprints seen from a given IP.
  global_secondary_index {
    name            = "ip-fp-index"
    hash_key        = "ip_address"
    range_key       = "seen_at"
    projection_type = "INCLUDE"
    non_key_attributes = [
      "fingerprint_id",
      "user_id",
      "anomaly_type",
    ]
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = local.is_prod
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = local.use_custom_kms ? var.kms_key_arn : null
  }

  tags = merge(var.common_tags, {
    Component = "device-fingerprints"
  })
}

# Table 3: MarketingCalendar — campaign schedule that drives the autoscaler.
resource "aws_dynamodb_table" "marketing_calendar" {
  name         = "marketing-calendar${var.name_suffix}"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "campaign_id"
  range_key = "start_time"

  attribute {
    name = "campaign_id"
    type = "S"
  }
  attribute {
    name = "start_time"
    type = "N"
  }
  attribute {
    name = "status"
    type = "S"
  }

  # GSI: all campaigns in a given status (SCHEDULED / ACTIVE / COMPLETE).
  global_secondary_index {
    name            = "status-index"
    hash_key        = "status"
    range_key       = "start_time"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = local.is_prod
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = local.use_custom_kms ? var.kms_key_arn : null
  }

  tags = merge(var.common_tags, {
    Component = "campaign-autoscaler"
  })
}

# Table 4: ScalingState — current scaling profile per service.
resource "aws_dynamodb_table" "scaling_state" {
  name         = "scaling-state${var.name_suffix}"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "service_id"

  attribute {
    name = "service_id"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = local.use_custom_kms ? var.kms_key_arn : null
  }

  tags = merge(var.common_tags, {
    Component = "campaign-autoscaler"
  })
}

# Table 5: IPBlockLog — append-only audit log of every block decision.
resource "aws_dynamodb_table" "ip_block_log" {
  name         = "ip-block-log${var.name_suffix}"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "event_id"
  range_key = "blocked_at"

  attribute {
    name = "event_id"
    type = "S"
  }
  attribute {
    name = "blocked_at"
    type = "N"
  }
  attribute {
    name = "ip_address"
    type = "S"
  }
  attribute {
    name = "reason"
    type = "S"
  }

  # GSI: all events for a given IP (fraud investigation queries).
  global_secondary_index {
    name            = "ip-time-index"
    hash_key        = "ip_address"
    range_key       = "blocked_at"
    projection_type = "ALL"
  }

  # GSI: all events by reason code (trend analysis).
  global_secondary_index {
    name            = "reason-time-index"
    hash_key        = "reason"
    range_key       = "blocked_at"
    projection_type = "INCLUDE"
    non_key_attributes = [
      "ip_address",
      "event_id",
      "gate_number",
      "fraud_score",
    ]
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = local.is_prod
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = local.use_custom_kms ? var.kms_key_arn : null
  }

  tags = merge(var.common_tags, {
    Component = "ip-block-log"
    Audit     = "true"
  })
}

# =============================================================================
# SSM Parameter Store — API keys stored outside Lambda env vars and state.
# =============================================================================

resource "aws_ssm_parameter" "maxmind_license_key" {
  name        = "/igaming/ip-detection${var.name_suffix}/maxmind-license-key"
  description = "MaxMind license key for GeoLite2 database downloads"
  type        = "SecureString"
  value       = var.maxmind_license_key != "" ? var.maxmind_license_key : "REPLACE_ME"
  key_id      = local.use_custom_kms ? var.kms_key_arn : "alias/aws/ssm"

  lifecycle {
    ignore_changes = [value]
  }

  tags = var.common_tags
}

resource "aws_ssm_parameter" "ip_reputation_api_key" {
  name        = "/igaming/ip-detection${var.name_suffix}/ip-reputation-api-key"
  description = "IPQualityScore (or compatible) API key for VPN detection Gate 2"
  type        = "SecureString"
  value       = var.ip_reputation_api_key != "" ? var.ip_reputation_api_key : "REPLACE_ME"
  key_id      = local.use_custom_kms ? var.kms_key_arn : "alias/aws/ssm"

  lifecycle {
    ignore_changes = [value]
  }

  tags = var.common_tags
}

# =============================================================================
# SNS Topic — NOC security alerts
# =============================================================================

resource "aws_sns_topic" "noc_alerts" {
  name              = "igaming-noc-alerts${var.name_suffix}"
  kms_master_key_id = local.use_custom_kms ? var.kms_key_arn : "alias/aws/sns"

  tags = merge(var.common_tags, {
    Component = "alerting"
  })
}

resource "aws_sns_topic_subscription" "noc_email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.noc_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_sns_topic_subscription" "noc_pagerduty" {
  count     = var.alert_pagerduty_url != "" ? 1 : 0
  topic_arn = aws_sns_topic.noc_alerts.arn
  protocol  = "https"
  endpoint  = var.alert_pagerduty_url
}

# =============================================================================
# IAM — Lambda execution roles with least-privilege policies
# =============================================================================

# Shared assume-role policy for all Lambda functions.
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ── ip-gate Lambda role ────────────────────────────────────────────────────

resource "aws_iam_role" "ip_gate" {
  name               = "igaming-ip-gate${var.name_suffix}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = var.common_tags
}

data "aws_iam_policy_document" "ip_gate" {
  # CloudWatch Logs
  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/igaming-ip-gate${var.name_suffix}:*"]
  }

  # DynamoDB — read blacklist, fingerprints, write block log.
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]
    resources = [
      aws_dynamodb_table.ip_blacklist.arn,
      "${aws_dynamodb_table.ip_blacklist.arn}/index/*",
      aws_dynamodb_table.device_fingerprints.arn,
      "${aws_dynamodb_table.device_fingerprints.arn}/index/*",
      aws_dynamodb_table.ip_block_log.arn,
      "${aws_dynamodb_table.ip_block_log.arn}/index/*",
    ]
  }

  # S3 — read threat lists (GeoIP + SDN).
  statement {
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.threat_lists.arn,
      "${aws_s3_bucket.threat_lists.arn}/*",
    ]
  }

  # S3 — write attack evidence.
  statement {
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.attack_evidence.arn}/*"]
  }

  # SSM — read API keys.
  statement {
    effect  = "Allow"
    actions = ["ssm:GetParameter"]
    resources = [
      aws_ssm_parameter.maxmind_license_key.arn,
      aws_ssm_parameter.ip_reputation_api_key.arn,
    ]
  }

  # SNS — publish critical alerts.
  statement {
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.noc_alerts.arn]
  }

  # WAF — add IPs to IP sets (automatic blacklist escalation).
  statement {
    effect = "Allow"
    actions = [
      "wafv2:GetIPSet",
      "wafv2:UpdateIPSet",
    ]
    resources = [
      aws_wafv2_ip_set.primary.arn,
      aws_wafv2_ip_set.overflow.arn,
    ]
  }

  # KMS decrypt — if custom key is provided.
  dynamic "statement" {
    for_each = local.use_custom_kms ? [1] : []
    content {
      effect    = "Allow"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [var.kms_key_arn]
    }
  }
}

resource "aws_iam_role_policy" "ip_gate" {
  name   = "ip-gate-policy"
  role   = aws_iam_role.ip_gate.id
  policy = data.aws_iam_policy_document.ip_gate.json
}

# ── DDoS detector Lambda role ──────────────────────────────────────────────

resource "aws_iam_role" "ddos_detector" {
  name               = "igaming-ddos-detector${var.name_suffix}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = var.common_tags
}

data "aws_iam_policy_document" "ddos_detector" {
  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/igaming-ddos-detector${var.name_suffix}:*"]
  }

  statement {
    effect    = "Allow"
    actions   = ["cloudwatch:GetMetricStatistics", "cloudwatch:DescribeAlarms"]
    resources = ["*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "wafv2:GetIPSet",
      "wafv2:UpdateIPSet",
      "wafv2:GetWebACL",
    ]
    resources = [
      aws_wafv2_ip_set.primary.arn,
      aws_wafv2_ip_set.overflow.arn,
      aws_wafv2_web_acl.main.arn,
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.noc_alerts.arn]
  }

  statement {
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.ip_block_log.arn,
      "${aws_dynamodb_table.ip_block_log.arn}/index/*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.attack_evidence.arn}/*"]
  }
}

resource "aws_iam_role_policy" "ddos_detector" {
  name   = "ddos-detector-policy"
  role   = aws_iam_role.ddos_detector.id
  policy = data.aws_iam_policy_document.ddos_detector.json
}

# ── Campaign autoscaler Lambda role ──────────────────────────────────────

resource "aws_iam_role" "campaign_autoscaler" {
  name               = "igaming-campaign-autoscaler${var.name_suffix}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = var.common_tags
}

data "aws_iam_policy_document" "campaign_autoscaler" {
  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/igaming-campaign-autoscaler${var.name_suffix}:*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [
      aws_dynamodb_table.marketing_calendar.arn,
      "${aws_dynamodb_table.marketing_calendar.arn}/index/*",
      aws_dynamodb_table.scaling_state.arn,
    ]
  }

  # Application Auto Scaling — adjust ECS / ASG targets for campaign bursts.
  statement {
    effect = "Allow"
    actions = [
      "application-autoscaling:RegisterScalableTarget",
      "application-autoscaling:PutScalingPolicy",
      "application-autoscaling:DescribeScalableTargets",
    ]
    resources = ["*"]
  }

  statement {
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.noc_alerts.arn]
  }
}

resource "aws_iam_role_policy" "campaign_autoscaler" {
  name   = "campaign-autoscaler-policy"
  role   = aws_iam_role.campaign_autoscaler.id
  policy = data.aws_iam_policy_document.campaign_autoscaler.json
}

# ── Evidence collector Lambda role ────────────────────────────────────────

resource "aws_iam_role" "evidence_collector" {
  name               = "igaming-evidence-collector${var.name_suffix}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = var.common_tags
}

data "aws_iam_policy_document" "evidence_collector" {
  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/igaming-evidence-collector${var.name_suffix}:*"]
  }

  statement {
    effect    = "Allow"
    actions   = ["s3:PutObject", "s3:PutObjectTagging"]
    resources = ["${aws_s3_bucket.attack_evidence.arn}/*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "dynamodb:Query",
      "dynamodb:GetItem",
    ]
    resources = [
      aws_dynamodb_table.ip_block_log.arn,
      "${aws_dynamodb_table.ip_block_log.arn}/index/*",
    ]
  }
}

resource "aws_iam_role_policy" "evidence_collector" {
  name   = "evidence-collector-policy"
  role   = aws_iam_role.evidence_collector.id
  policy = data.aws_iam_policy_document.evidence_collector.json
}

# ── SDN refresh Lambda role ────────────────────────────────────────────────

resource "aws_iam_role" "sdn_refresh" {
  name               = "igaming-sdn-refresh${var.name_suffix}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = var.common_tags
}

data "aws_iam_policy_document" "sdn_refresh" {
  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/igaming-sdn-refresh${var.name_suffix}:*"]
  }

  statement {
    effect  = "Allow"
    actions = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.threat_lists.arn,
      "${aws_s3_bucket.threat_lists.arn}/*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.maxmind_license_key.arn]
  }

  statement {
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.noc_alerts.arn]
  }
}

resource "aws_iam_role_policy" "sdn_refresh" {
  name   = "sdn-refresh-policy"
  role   = aws_iam_role.sdn_refresh.id
  policy = data.aws_iam_policy_document.sdn_refresh.json
}

# =============================================================================
# Lambda Functions
# Note: Lambda deployment packages are built by CI/CD and referenced via S3
# or local path. A stub zip is created here so Terraform can manage the
# function configuration; real code is deployed by the pipeline.
# =============================================================================

# Stub archive — replaced by CI/CD with real deployment package.
# In a real deployment, use:
#   data "archive_file" "ip_gate" { ... }
# or reference the S3 object created by your build pipeline.
data "archive_file" "lambda_stub" {
  type        = "zip"
  output_path = "${path.module}/lambda_stub.zip"

  source {
    content  = "# Placeholder — replace with actual deployment package via CI/CD"
    filename = "handler.py"
  }
}

# ── ip-gate ────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "ip_gate" {
  name              = "/aws/lambda/igaming-ip-gate${var.name_suffix}"
  retention_in_days = local.is_prod ? 90 : 14
  kms_key_id        = local.use_custom_kms ? var.kms_key_arn : null
  tags              = var.common_tags
}

resource "aws_lambda_function" "ip_gate" {
  function_name = "igaming-ip-gate${var.name_suffix}"
  role          = aws_iam_role.ip_gate.arn
  runtime       = "python3.12"
  handler       = "lambda_ip_gate.handler"
  memory_size   = var.lambda_memory_mb
  timeout       = var.lambda_timeout_seconds

  filename         = data.archive_file.lambda_stub.output_path
  source_code_hash = data.archive_file.lambda_stub.output_base64sha256

  environment {
    variables = merge(local.lambda_base_env, {
      SSM_MAXMIND_KEY_PATH       = aws_ssm_parameter.maxmind_license_key.name
      SSM_IP_REPUTATION_KEY_PATH = aws_ssm_parameter.ip_reputation_api_key.name
    })
  }

  depends_on = [aws_cloudwatch_log_group.ip_gate]

  tags = merge(var.common_tags, {
    Component = "ip-gate"
  })
}

# ── ddos-detector ─────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "ddos_detector" {
  name              = "/aws/lambda/igaming-ddos-detector${var.name_suffix}"
  retention_in_days = local.is_prod ? 90 : 14
  kms_key_id        = local.use_custom_kms ? var.kms_key_arn : null
  tags              = var.common_tags
}

resource "aws_lambda_function" "ddos_detector" {
  function_name = "igaming-ddos-detector${var.name_suffix}"
  role          = aws_iam_role.ddos_detector.arn
  runtime       = "python3.12"
  handler       = "ddos_detector.handler"
  memory_size   = 256
  timeout       = 30

  filename         = data.archive_file.lambda_stub.output_path
  source_code_hash = data.archive_file.lambda_stub.output_base64sha256

  environment {
    variables = merge(local.lambda_base_env, {
      WAF_WEBACL_ARN = aws_wafv2_web_acl.main.arn
      WAF_IP_SET_ARN = aws_wafv2_ip_set.primary.arn
      WAF_IP_SET_ID  = aws_wafv2_ip_set.primary.id
      WAF_IP_SCOPE   = "REGIONAL"
    })
  }

  depends_on = [aws_cloudwatch_log_group.ddos_detector]

  tags = merge(var.common_tags, {
    Component = "ddos-detector"
  })
}

# ── campaign-autoscaler ────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "campaign_autoscaler" {
  name              = "/aws/lambda/igaming-campaign-autoscaler${var.name_suffix}"
  retention_in_days = local.is_prod ? 30 : 7
  kms_key_id        = local.use_custom_kms ? var.kms_key_arn : null
  tags              = var.common_tags
}

resource "aws_lambda_function" "campaign_autoscaler" {
  function_name = "igaming-campaign-autoscaler${var.name_suffix}"
  role          = aws_iam_role.campaign_autoscaler.arn
  runtime       = "python3.12"
  handler       = "campaign_autoscaler.handler"
  memory_size   = 256
  timeout       = 60

  filename         = data.archive_file.lambda_stub.output_path
  source_code_hash = data.archive_file.lambda_stub.output_base64sha256

  environment {
    variables = merge(local.lambda_base_env, {
      SCALE_PROFILES_JSON = jsonencode(var.scale_profiles)
    })
  }

  depends_on = [aws_cloudwatch_log_group.campaign_autoscaler]

  tags = merge(var.common_tags, {
    Component = "campaign-autoscaler"
  })
}

# ── evidence-collector ────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "evidence_collector" {
  name              = "/aws/lambda/igaming-evidence-collector${var.name_suffix}"
  retention_in_days = local.is_prod ? 90 : 14
  kms_key_id        = local.use_custom_kms ? var.kms_key_arn : null
  tags              = var.common_tags
}

resource "aws_lambda_function" "evidence_collector" {
  function_name = "igaming-evidence-collector${var.name_suffix}"
  role          = aws_iam_role.evidence_collector.arn
  runtime       = "python3.12"
  handler       = "evidence_collector.handler"
  memory_size   = 256
  timeout       = 30

  filename         = data.archive_file.lambda_stub.output_path
  source_code_hash = data.archive_file.lambda_stub.output_base64sha256

  environment {
    variables = local.lambda_base_env
  }

  depends_on = [aws_cloudwatch_log_group.evidence_collector]

  tags = merge(var.common_tags, {
    Component = "evidence-collector"
  })
}

# ── sdn-refresh ────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "sdn_refresh" {
  name              = "/aws/lambda/igaming-sdn-refresh${var.name_suffix}"
  retention_in_days = local.is_prod ? 30 : 7
  kms_key_id        = local.use_custom_kms ? var.kms_key_arn : null
  tags              = var.common_tags
}

resource "aws_lambda_function" "sdn_refresh" {
  function_name = "igaming-sdn-refresh${var.name_suffix}"
  role          = aws_iam_role.sdn_refresh.arn
  runtime       = "python3.12"
  handler       = "sdn_refresh.handler"
  memory_size   = 512
  timeout       = 300

  filename         = data.archive_file.lambda_stub.output_path
  source_code_hash = data.archive_file.lambda_stub.output_base64sha256

  environment {
    variables = merge(local.lambda_base_env, {
      SSM_MAXMIND_KEY_PATH = aws_ssm_parameter.maxmind_license_key.name
    })
  }

  depends_on = [aws_cloudwatch_log_group.sdn_refresh]

  tags = merge(var.common_tags, {
    Component = "sdn-refresh"
  })
}

# =============================================================================
# API Gateway HTTP API v2 — ip-gate endpoint
# =============================================================================

resource "aws_apigatewayv2_api" "ip_gate" {
  name          = "igaming-ip-gate${var.name_suffix}"
  protocol_type = "HTTP"
  description   = "iGaming 8-gate IP detection API"

  cors_configuration {
    allow_headers = ["Content-Type", "Authorization", "X-Player-Id", "X-Forwarded-For"]
    allow_methods = ["POST", "OPTIONS"]
    allow_origins = ["https://${var.aws_region}.amazonaws.com"]
    max_age       = 300
  }

  tags = var.common_tags
}

resource "aws_apigatewayv2_stage" "ip_gate" {
  api_id      = aws_apigatewayv2_api.ip_gate.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format = jsonencode({
      requestId        = "$context.requestId"
      ip               = "$context.identity.sourceIp"
      requestTime      = "$context.requestTime"
      httpMethod       = "$context.httpMethod"
      routeKey         = "$context.routeKey"
      status           = "$context.status"
      responseLength   = "$context.responseLength"
      integrationError = "$context.integrationErrorMessage"
    })
  }

  default_route_settings {
    throttling_burst_limit = 5000
    throttling_rate_limit  = 2000
  }

  tags = var.common_tags
}

resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/igaming-ip-gate${var.name_suffix}"
  retention_in_days = local.is_prod ? 30 : 7
  tags              = var.common_tags
}

resource "aws_apigatewayv2_integration" "ip_gate" {
  api_id             = aws_apigatewayv2_api.ip_gate.id
  integration_type   = "AWS_PROXY"
  integration_uri    = aws_lambda_function.ip_gate.invoke_arn
  integration_method = "POST"

  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "check_ip" {
  #checkov:skip=CKV_AWS_309: Authorization is enforced inside the ip-gate Lambda (8-gate pipeline); API GW layer is unauthenticated by design.
  api_id             = aws_apigatewayv2_api.ip_gate.id
  route_key          = "POST /check"
  target             = "integrations/${aws_apigatewayv2_integration.ip_gate.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_route" "health" {
  #checkov:skip=CKV_AWS_309: Health check endpoint is intentionally public — no auth required for liveness probes.
  api_id             = aws_apigatewayv2_api.ip_gate.id
  route_key          = "GET /health"
  target             = "integrations/${aws_apigatewayv2_integration.ip_gate.id}"
  authorization_type = "NONE"
}

# Allow API Gateway to invoke the Lambda.
resource "aws_lambda_permission" "api_gateway_ip_gate" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ip_gate.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ip_gate.execution_arn}/*/*"
}

# =============================================================================
# WAF v2 — Regional WebACL
# =============================================================================

# Primary IP set — up to 10,000 CIDRs auto-populated by the DDoS detector.
resource "aws_wafv2_ip_set" "primary" {
  name               = "igaming-blocked-ips${var.name_suffix}"
  description        = "Primary IP blocklist — auto-maintained by ddos-detector Lambda"
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  addresses          = []

  tags = merge(var.common_tags, {
    Component = "waf-ip-set"
  })
}

# Overflow IP set — secondary set when primary reaches capacity.
resource "aws_wafv2_ip_set" "overflow" {
  name               = "igaming-blocked-ips-overflow${var.name_suffix}"
  description        = "Overflow IP blocklist"
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  addresses          = []

  tags = merge(var.common_tags, {
    Component = "waf-ip-set-overflow"
  })
}

# IPv6 IP set — for complete dual-stack coverage.
resource "aws_wafv2_ip_set" "ipv6" {
  name               = "igaming-blocked-ips-v6${var.name_suffix}"
  description        = "IPv6 blocklist — auto-maintained by ddos-detector Lambda"
  scope              = "REGIONAL"
  ip_address_version = "IPV6"
  addresses          = []

  tags = merge(var.common_tags, {
    Component = "waf-ip-set-v6"
  })
}

# Regional WebACL — associated with the API Gateway stage.
resource "aws_wafv2_web_acl" "main" {
  name        = "igaming-ip-detection${var.name_suffix}"
  scope       = "REGIONAL"
  description = "WAF WebACL for iGaming IP detection pipeline"

  default_action {
    allow {}
  }

  # Rule 1: Block IPs in the primary blocklist.
  rule {
    name     = "BlockPrimaryIPSet"
    priority = 1

    action {
      block {}
    }

    statement {
      ip_set_reference_statement {
        arn = aws_wafv2_ip_set.primary.arn
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "BlockPrimaryIPSet"
    }
  }

  # Rule 2: Block IPs in the overflow blocklist.
  rule {
    name     = "BlockOverflowIPSet"
    priority = 2

    action {
      block {}
    }

    statement {
      ip_set_reference_statement {
        arn = aws_wafv2_ip_set.overflow.arn
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "BlockOverflowIPSet"
    }
  }

  # Rule 3: Block IPv6 blocklist.
  rule {
    name     = "BlockIPv6Set"
    priority = 3

    action {
      block {}
    }

    statement {
      ip_set_reference_statement {
        arn = aws_wafv2_ip_set.ipv6.arn
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "BlockIPv6Set"
    }
  }

  # Rule 4: Rate-based rule — per-IP request throttle.
  rule {
    name     = "RateBasedPerIP"
    priority = 4

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_block_threshold
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "RateBasedPerIP"
    }
  }

  # Rule 5: AWS Managed Rules — Core Rule Set (CRS).
  rule {
    name     = "AWSManagedRulesCRS"
    priority = 5

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesCRS"
    }
  }

  # Rule 6: AWS Managed Rules — Known Bad Inputs.
  rule {
    name     = "AWSManagedRulesKnownBadInputs"
    priority = 6

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesKnownBadInputs"
    }
  }

  # Rule 7: AWS Managed Rules — Anonymous IP (Tor, VPN, proxies).
  rule {
    name     = "AWSManagedRulesAnonymousIP"
    priority = 7

    override_action {
      count {} # Count only — blocks handled by ip-gate Lambda with full context.
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAnonymousIpList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesAnonymousIP"
    }
  }

  # Rule 8: AWS Managed Rules — SQL database protection.
  rule {
    name     = "AWSManagedRulesSQLi"
    priority = 8

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesSQLi"
    }
  }

  visibility_config {
    sampled_requests_enabled   = true
    cloudwatch_metrics_enabled = true
    metric_name                = "igaming-ip-detection${var.name_suffix}"
  }

  tags = merge(var.common_tags, {
    Component = "waf-webacl"
  })
}

# Associate WAF WebACL with the API Gateway stage.
resource "aws_wafv2_web_acl_association" "api_gateway" {
  resource_arn = aws_apigatewayv2_stage.ip_gate.arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}

# WAF logging to S3 for compliance and forensics.
resource "aws_wafv2_web_acl_logging_configuration" "main" {
  log_destination_configs = [aws_cloudwatch_log_group.waf_logs.arn]
  resource_arn            = aws_wafv2_web_acl.main.arn

  logging_filter {
    default_behavior = "KEEP"
    filter {
      behavior    = "KEEP"
      requirement = "MEETS_ANY"
      condition {
        action_condition {
          action = "BLOCK"
        }
      }
    }
  }
}

resource "aws_cloudwatch_log_group" "waf_logs" {
  # WAF log group name MUST start with "aws-waf-logs-".
  name              = "aws-waf-logs-igaming-ip-detection${var.name_suffix}"
  retention_in_days = local.is_prod ? 90 : 14
  kms_key_id        = local.use_custom_kms ? var.kms_key_arn : null
  tags              = var.common_tags
}

# =============================================================================
# ElastiCache Redis (conditional)
# =============================================================================

resource "aws_elasticache_subnet_group" "main" {
  count      = var.elasticache_enabled ? 1 : 0
  name       = "igaming-ip-detection${var.name_suffix}"
  subnet_ids = data.aws_subnets.default[0].ids

  tags = var.common_tags
}

resource "aws_security_group" "elasticache" {
  count       = var.elasticache_enabled ? 1 : 0
  name        = "igaming-elasticache${var.name_suffix}"
  description = "Allow Redis access from ip-gate Lambda"
  vpc_id      = data.aws_vpc.default[0].id

  ingress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    description = "Redis from Lambda (add Lambda SG here)"
    cidr_blocks = [data.aws_vpc.default[0].cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = var.common_tags
}

resource "aws_elasticache_cluster" "velocity_cache" {
  count                = var.elasticache_enabled ? 1 : 0
  cluster_id           = "igaming-velocity${var.name_suffix}"
  engine               = "redis"
  node_type            = var.elasticache_node_type
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.1"
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.main[0].name

  security_group_ids = [aws_security_group.elasticache[0].id]

  snapshot_retention_limit = local.is_prod ? 1 : 0

  tags = merge(var.common_tags, {
    Component = "velocity-cache"
  })
}

# =============================================================================
# CloudWatch Alarms — NOC visibility
# =============================================================================

# Alarm 1: Request spike — 5x baseline in 5 minutes.
resource "aws_cloudwatch_metric_alarm" "request_spike" {
  alarm_name          = "igaming-ip-gate-request-spike${var.name_suffix}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Count"
  namespace           = "AWS/ApiGateway"
  period              = 300
  statistic           = "Sum"
  threshold           = var.waf_block_threshold * 2
  alarm_description   = "Request spike detected — possible DDoS in progress"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ApiId = aws_apigatewayv2_api.ip_gate.id
  }

  alarm_actions             = [aws_sns_topic.noc_alerts.arn, aws_lambda_function.ddos_detector.arn]
  ok_actions                = [aws_sns_topic.noc_alerts.arn]
  insufficient_data_actions = []

  tags = var.common_tags
}

# Alarm 2: Lambda 5xx error rate > 5% over 5 minutes.
resource "aws_cloudwatch_metric_alarm" "lambda_error_rate" {
  alarm_name          = "igaming-ip-gate-5xx-rate${var.name_suffix}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 5
  alarm_description   = "ip-gate Lambda 5xx error rate above 5%"
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "error_rate"
    expression  = "errors / invocations * 100"
    label       = "Error Rate (%)"
    return_data = true
  }

  metric_query {
    id = "errors"
    metric {
      metric_name = "Errors"
      namespace   = "AWS/Lambda"
      period      = 300
      stat        = "Sum"
      dimensions = {
        FunctionName = aws_lambda_function.ip_gate.function_name
      }
    }
  }

  metric_query {
    id = "invocations"
    metric {
      metric_name = "Invocations"
      namespace   = "AWS/Lambda"
      period      = 300
      stat        = "Sum"
      dimensions = {
        FunctionName = aws_lambda_function.ip_gate.function_name
      }
    }
  }

  alarm_actions = [aws_sns_topic.noc_alerts.arn]
  tags          = var.common_tags
}

# Alarm 3: WAF block rate surge — attacker testing is accelerating.
resource "aws_cloudwatch_metric_alarm" "waf_block_rate" {
  alarm_name          = "igaming-waf-block-surge${var.name_suffix}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "BlockedRequests"
  namespace           = "AWS/WAFV2"
  period              = 300
  statistic           = "Sum"
  threshold           = var.waf_block_threshold
  alarm_description   = "WAF block surge — possible coordinated attack"
  treat_missing_data  = "notBreaching"

  dimensions = {
    WebACL = aws_wafv2_web_acl.main.name
    Region = var.aws_region
    Rule   = "ALL"
  }

  alarm_actions = [aws_sns_topic.noc_alerts.arn, aws_lambda_function.ddos_detector.arn]
  tags          = var.common_tags
}

# Alarm 4: Lambda p99 latency > 8s (approaching API GW timeout).
resource "aws_cloudwatch_metric_alarm" "lambda_p99_latency" {
  alarm_name          = "igaming-ip-gate-p99-latency${var.name_suffix}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "Duration"
  namespace           = "AWS/Lambda"
  period              = 60
  extended_statistic  = "p99"
  threshold           = 8000
  alarm_description   = "ip-gate p99 latency above 8s — risk of API Gateway timeout"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.ip_gate.function_name
  }

  alarm_actions = [aws_sns_topic.noc_alerts.arn]
  tags          = var.common_tags
}

# Alarm 5: Lambda concurrent execution limit approaching.
resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  alarm_name          = "igaming-ip-gate-throttles${var.name_suffix}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Throttles"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 50
  alarm_description   = "ip-gate Lambda is being throttled — increase concurrency limit"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.ip_gate.function_name
  }

  alarm_actions = [aws_sns_topic.noc_alerts.arn]
  tags          = var.common_tags
}

# Allow CloudWatch Alarms to invoke the DDoS detector Lambda.
resource "aws_lambda_permission" "cloudwatch_ddos_detector" {
  statement_id  = "AllowCloudWatchAlarmInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ddos_detector.function_name
  principal     = "lambda.alarms.cloudwatch.amazonaws.com"
  source_arn    = aws_cloudwatch_metric_alarm.request_spike.arn
}

# =============================================================================
# EventBridge Rules — scheduled automation
# =============================================================================

resource "aws_cloudwatch_event_rule" "sdn_refresh" {
  name                = "igaming-sdn-refresh${var.name_suffix}"
  description         = "Scheduled OFAC SDN list + GeoIP database refresh"
  schedule_expression = var.threat_list_schedule

  tags = var.common_tags
}

resource "aws_cloudwatch_event_target" "sdn_refresh" {
  rule      = aws_cloudwatch_event_rule.sdn_refresh.name
  target_id = "sdn-refresh-lambda"
  arn       = aws_lambda_function.sdn_refresh.arn
}

resource "aws_lambda_permission" "eventbridge_sdn_refresh" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sdn_refresh.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.sdn_refresh.arn
}

# Daily campaign calendar scan — check for upcoming campaign scaling events.
resource "aws_cloudwatch_event_rule" "campaign_scan" {
  name                = "igaming-campaign-scan${var.name_suffix}"
  description         = "Hourly scan of marketing calendar for upcoming campaign events"
  schedule_expression = "rate(1 hour)"

  tags = var.common_tags
}

resource "aws_cloudwatch_event_target" "campaign_scan" {
  rule      = aws_cloudwatch_event_rule.campaign_scan.name
  target_id = "campaign-autoscaler-lambda"
  arn       = aws_lambda_function.campaign_autoscaler.arn
}

resource "aws_lambda_permission" "eventbridge_campaign_autoscaler" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.campaign_autoscaler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.campaign_scan.arn
}

# =============================================================================
# Outputs
# =============================================================================

output "api_gateway_url" { value = aws_apigatewayv2_stage.ip_gate.invoke_url }
output "waf_webacl_arn" { value = aws_wafv2_web_acl.main.arn }
output "waf_webacl_id" { value = aws_wafv2_web_acl.main.id }
output "dynamodb_ip_blacklist_table" { value = aws_dynamodb_table.ip_blacklist.name }
output "dynamodb_device_fingerprints_table" { value = aws_dynamodb_table.device_fingerprints.name }
output "dynamodb_marketing_calendar_table" { value = aws_dynamodb_table.marketing_calendar.name }
output "dynamodb_scaling_state_table" { value = aws_dynamodb_table.scaling_state.name }
output "dynamodb_ip_block_log_table" { value = aws_dynamodb_table.ip_block_log.name }
output "s3_threat_lists_bucket" { value = aws_s3_bucket.threat_lists.bucket }
output "s3_attack_evidence_bucket" { value = aws_s3_bucket.attack_evidence.bucket }
output "lambda_ip_gate_arn" { value = aws_lambda_function.ip_gate.arn }
output "lambda_ddos_detector_arn" { value = aws_lambda_function.ddos_detector.arn }
output "lambda_campaign_autoscaler_arn" { value = aws_lambda_function.campaign_autoscaler.arn }
output "lambda_sdn_refresh_arn" { value = aws_lambda_function.sdn_refresh.arn }
output "sns_noc_alerts_arn" { value = aws_sns_topic.noc_alerts.arn }
output "elasticache_endpoint" {
  value = var.elasticache_enabled ? aws_elasticache_cluster.velocity_cache[0].cache_nodes[0].address : ""
}
output "ssm_maxmind_key_path" { value = aws_ssm_parameter.maxmind_license_key.name }
output "ssm_ip_reputation_key_path" { value = aws_ssm_parameter.ip_reputation_api_key.name }
