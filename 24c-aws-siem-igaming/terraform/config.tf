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
# AWS Config - Configuration Compliance for iGaming
# =============================================================================
# AWS Config continuously records resource configurations and evaluates them
# against compliance rules. For iGaming, this ensures that infrastructure
# never drifts from regulatory requirements.
#
# If someone creates an unencrypted EBS volume or opens an S3 bucket to the
# public, Config detects it within minutes and can auto-remediate.
#
# Regulatory justification:
#   NJ DGE 13:69O-1.3: Encryption at rest for all sensitive data
#   PA PGCB: Network segmentation requirements
#   MI MGCB: Configuration management and change tracking
#   PCI DSS 3.4: Render PAN unreadable anywhere it is stored
# =============================================================================

# --- IAM Role for AWS Config ---
resource "aws_iam_role" "config" {
  name = "${local.name_prefix}-config-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "config.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-config-role"
  })
}

resource "aws_iam_role_policy_attachment" "config" {
  role       = aws_iam_role.config.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWS_ConfigRole"
}

resource "aws_iam_role_policy" "config_s3" {
  name = "${local.name_prefix}-config-s3-policy"
  role = aws_iam_role.config.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:PutObjectAcl"]
        Resource = "${aws_s3_bucket.log_archive.arn}/config/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.log_archive.arn
      },
      {
        # The archive bucket applies SSE-KMS by default and its policy denies
        # any PutObject that is not aws:kms encrypted. S3 permissions alone are
        # therefore not enough: without the data-key grant every configuration
        # snapshot and history file is rejected with KMS AccessDenied, and the
        # recorder keeps reporting healthy while nothing persists. Config
        # failures are silent by design, so this one hides until an examiner
        # asks for the configuration state of a resource six months ago.
        Effect = "Allow"
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt"
        ]
        Resource = aws_kms_key.cloudtrail.arn
      }
    ]
  })
}

# --- Config Recorder ---
# Records all resource types. This is the foundation for compliance tracking.
resource "aws_config_configuration_recorder" "main" {
  name     = "${local.name_prefix}-recorder"
  role_arn = aws_iam_role.config.arn

  recording_group {
    all_supported                 = true
    include_global_resource_types = true
  }

  recording_mode {
    recording_frequency = "CONTINUOUS"
  }
}

# --- Config Delivery Channel ---
# Sends configuration snapshots and change notifications to S3.
resource "aws_config_delivery_channel" "main" {
  name           = "${local.name_prefix}-delivery"
  s3_bucket_name = aws_s3_bucket.log_archive.id
  s3_key_prefix  = "config"
  sns_topic_arn  = aws_sns_topic.compliance_alerts.arn

  # Name the CMK explicitly. Left unset, Config writes with SSE-S3 (AES256),
  # which the bucket policy's DenyUnencryptedUploads statement rejects.
  s3_kms_key_arn = aws_kms_key.cloudtrail.arn

  snapshot_delivery_properties {
    delivery_frequency = "Six_Hours"
  }

  depends_on = [aws_config_configuration_recorder.main]
}

# --- Enable the Recorder ---
resource "aws_config_configuration_recorder_status" "main" {
  name       = aws_config_configuration_recorder.main.name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.main]
}

# =============================================================================
# Config Rules - iGaming Compliance Checks
# =============================================================================

# --- Encryption Rules ---
# PCI DSS 3.4: All volumes storing player data must be encrypted.
resource "aws_config_config_rule" "encrypted_volumes" {
  name = "${local.name_prefix}-encrypted-volumes"

  source {
    owner             = "AWS"
    source_identifier = "ENCRYPTED_VOLUMES"
  }

  tags = merge(local.common_tags, {
    Compliance = "pci-dss-3.4"
    Purpose    = "ensure-ebs-encryption"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# RDS instances must use encryption at rest -- player wallets and transaction
# data are stored in RDS.
resource "aws_config_config_rule" "rds_encryption" {
  name = "${local.name_prefix}-rds-storage-encrypted"

  source {
    owner             = "AWS"
    source_identifier = "RDS_STORAGE_ENCRYPTED"
  }

  tags = merge(local.common_tags, {
    Compliance = "pci-dss-3.4"
    Purpose    = "ensure-rds-encryption"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# --- S3 Security Rules ---
# No S3 bucket should ever allow public read -- player data exposure
# is an immediate license-threatening event.
resource "aws_config_config_rule" "s3_public_read" {
  name = "${local.name_prefix}-s3-public-read-prohibited"

  source {
    owner             = "AWS"
    source_identifier = "S3_BUCKET_PUBLIC_READ_PROHIBITED"
  }

  tags = merge(local.common_tags, {
    Compliance = "data-protection"
    Purpose    = "prevent-data-exposure"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

resource "aws_config_config_rule" "s3_public_write" {
  name = "${local.name_prefix}-s3-public-write-prohibited"

  source {
    owner             = "AWS"
    source_identifier = "S3_BUCKET_PUBLIC_WRITE_PROHIBITED"
  }

  tags = merge(local.common_tags, {
    Compliance = "data-protection"
    Purpose    = "prevent-unauthorized-writes"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# S3 versioning required on log buckets -- prevents deletion of audit trail.
resource "aws_config_config_rule" "s3_versioning" {
  name = "${local.name_prefix}-s3-versioning-enabled"

  source {
    owner             = "AWS"
    source_identifier = "S3_BUCKET_VERSIONING_ENABLED"
  }

  tags = merge(local.common_tags, {
    Compliance = "audit-trail-integrity"
    Purpose    = "tamper-proof-logs"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# S3 bucket-level encryption enforcement.
resource "aws_config_config_rule" "s3_encryption" {
  name = "${local.name_prefix}-s3-default-encryption"

  source {
    owner             = "AWS"
    source_identifier = "S3_DEFAULT_ENCRYPTION_KMS"
  }

  tags = merge(local.common_tags, {
    Compliance = "pci-dss-3.4"
    Purpose    = "ensure-s3-encryption"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# --- Logging Rules ---
# CloudTrail must be enabled -- this is the API audit trail.
resource "aws_config_config_rule" "cloudtrail_enabled" {
  name = "${local.name_prefix}-cloudtrail-enabled"

  source {
    owner             = "AWS"
    source_identifier = "CLOUD_TRAIL_ENABLED"
  }

  tags = merge(local.common_tags, {
    Compliance = "nj-dge-13-69O-1.1"
    Purpose    = "audit-trail-active"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# Multi-region CloudTrail -- catch activity in ALL regions.
resource "aws_config_config_rule" "multi_region_cloudtrail" {
  name = "${local.name_prefix}-multi-region-cloudtrail"

  source {
    owner             = "AWS"
    source_identifier = "MULTI_REGION_CLOUD_TRAIL_ENABLED"
  }

  tags = merge(local.common_tags, {
    Compliance = "defense-in-depth"
    Purpose    = "global-audit-coverage"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# VPC Flow Logs must be enabled on all VPCs.
resource "aws_config_config_rule" "vpc_flow_logs" {
  name = "${local.name_prefix}-vpc-flow-logs-enabled"

  source {
    owner             = "AWS"
    source_identifier = "VPC_FLOW_LOGS_ENABLED"
  }

  tags = merge(local.common_tags, {
    Compliance = "mi-mgcb-network-monitoring"
    Purpose    = "network-visibility"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# --- IAM Rules ---
# Password policy must meet PCI DSS 8.2 requirements.
resource "aws_config_config_rule" "iam_password_policy" {
  name = "${local.name_prefix}-iam-password-policy"

  source {
    owner             = "AWS"
    source_identifier = "IAM_PASSWORD_POLICY"
  }

  input_parameters = jsonencode({
    RequireUppercaseCharacters = "true"
    RequireLowercaseCharacters = "true"
    RequireSymbols             = "true"
    RequireNumbers             = "true"
    MinimumPasswordLength      = "14"
    PasswordReusePrevention    = "24"
    MaxPasswordAge             = "90"
  })

  tags = merge(local.common_tags, {
    Compliance = "pci-dss-8.2"
    Purpose    = "password-complexity"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# Root account MFA -- root should never be used, but must have MFA if it is.
resource "aws_config_config_rule" "root_mfa" {
  name = "${local.name_prefix}-root-account-mfa"

  source {
    owner             = "AWS"
    source_identifier = "ROOT_ACCOUNT_MFA_ENABLED"
  }

  tags = merge(local.common_tags, {
    Compliance = "pci-dss-8.3"
    Purpose    = "root-account-protection"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# IAM users must have MFA enabled.
resource "aws_config_config_rule" "iam_user_mfa" {
  name = "${local.name_prefix}-iam-user-mfa"

  source {
    owner             = "AWS"
    source_identifier = "IAM_USER_MFA_ENABLED"
  }

  tags = merge(local.common_tags, {
    Compliance = "pci-dss-8.3"
    Purpose    = "mfa-enforcement"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# --- GuardDuty Enabled ---
# GuardDuty must be active for continuous threat detection.
resource "aws_config_config_rule" "guardduty_enabled" {
  name = "${local.name_prefix}-guardduty-enabled"

  source {
    owner             = "AWS"
    source_identifier = "GUARDDUTY_ENABLED_CENTRALIZED"
  }

  tags = merge(local.common_tags, {
    Compliance = "nj-dge-13-69O-1.4"
    Purpose    = "threat-detection-active"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# --- Network Security Rules ---
# No security groups should allow unrestricted SSH access.
resource "aws_config_config_rule" "restricted_ssh" {
  name = "${local.name_prefix}-restricted-ssh"

  source {
    owner             = "AWS"
    source_identifier = "INCOMING_SSH_DISABLED"
  }

  tags = merge(local.common_tags, {
    Compliance = "network-segmentation"
    Purpose    = "prevent-open-ssh"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

# No security groups should allow unrestricted common ports.
resource "aws_config_config_rule" "restricted_common_ports" {
  name = "${local.name_prefix}-restricted-common-ports"

  source {
    owner             = "AWS"
    source_identifier = "RESTRICTED_INCOMING_TRAFFIC"
  }

  input_parameters = jsonencode({
    blockedPort1 = "3389"  # RDP
    blockedPort2 = "3306"  # MySQL
    blockedPort3 = "5432"  # PostgreSQL
    blockedPort4 = "6379"  # Redis
    blockedPort5 = "27017" # MongoDB
  })

  tags = merge(local.common_tags, {
    Compliance = "network-segmentation"
    Purpose    = "prevent-open-database-ports"
  })

  depends_on = [aws_config_configuration_recorder.main]
}
