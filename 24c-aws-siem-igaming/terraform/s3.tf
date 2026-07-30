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
# S3 - Log Archive Bucket for iGaming Compliance
# =============================================================================
# This bucket is the central archive for all security logs, findings, and
# audit trail data. It receives logs from:
#   - CloudTrail (API audit trail)                          cloudtrail/
#   - AWS Config (configuration snapshots)                  config/
#   - GuardDuty findings (via Lambda archiver)              security-findings/
#   - Security Hub findings (via Lambda archiver)           security-findings/
#   - Application, auth, payment, game and security event
#     logs (via Firehose, see firehose.tf)                  app-logs/
#
# Every prefix above needs its own lifecycle rule. A prefix with no rule keeps
# its objects in S3 Standard forever and, worse, gives the false impression the
# retention schedule covers it.
#
# The lifecycle policy implements the cost-optimized 7-year retention:
#   Day 0-90:     S3 Standard       ($0.023/GB/mo) -- hot access
#   Day 90-365:   S3 Glacier IR     ($0.004/GB/mo) -- warm access
#   Day 365-2555: Glacier Deep      ($0.00099/GB/mo) -- cold storage
#   Day 2556:     Delete            -- 7 years complete
#
# Regulatory justification:
#   NJ DGE 13:69O-1.1(b): 7-year log retention requirement
#   PA PGCB: Log retention for duration of license plus 5 years
#   MI MGCB: Complete audit trail retention
#   PCI DSS 10.7: Retain audit trail for at least 1 year (we exceed this)
# =============================================================================

# --- Log Archive Bucket ---
resource "aws_s3_bucket" "log_archive" {
  bucket = "${local.name_prefix}-log-archive-${local.account_id}"

  # Prevent accidental deletion of 7 years of compliance data
  force_destroy = false

  tags = merge(local.common_tags, {
    Name               = "${local.name_prefix}-log-archive"
    Purpose            = "7-year-compliance-log-retention"
    DataClassification = "confidential"
  })
}

# --- Versioning ---
# Required for tamper-proof audit trail. Even if an object is overwritten,
# the original version is preserved. Regulators may request proof that
# logs haven't been altered.
resource "aws_s3_bucket_versioning" "log_archive" {
  bucket = aws_s3_bucket.log_archive.id

  versioning_configuration {
    status = "Enabled"
    # MFA delete adds an extra layer -- requires MFA to delete versions.
    # Enable this manually after initial deployment (requires root credentials).
    # mfa_delete = "Enabled"
  }
}

# --- Server-Side Encryption ---
# All log data encrypted at rest using the dedicated KMS key.
# NJ DGE 13:69O-1.3 requires encryption of sensitive data at rest.
resource "aws_s3_bucket_server_side_encryption_configuration" "log_archive" {
  bucket = aws_s3_bucket.log_archive.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.cloudtrail.arn
    }
    bucket_key_enabled = true # Reduces KMS API calls and cost
  }
}

# --- Block All Public Access ---
# This bucket must NEVER be publicly accessible. Player PII, financial
# records, and security findings are stored here.
resource "aws_s3_bucket_public_access_block" "log_archive" {
  bucket = aws_s3_bucket.log_archive.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- Lifecycle Policy: 7-Year Tiered Retention ---
resource "aws_s3_bucket_lifecycle_configuration" "log_archive" {
  bucket = aws_s3_bucket.log_archive.id

  # CloudTrail logs
  rule {
    id     = "cloudtrail-lifecycle"
    status = "Enabled"

    filter {
      prefix = "cloudtrail/"
    }

    # Move to Glacier Instant Retrieval after 90 days
    # Still accessible in milliseconds for active investigations
    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }

    # Move to Glacier Deep Archive after 1 year
    # 12-hour retrieval time -- acceptable for historical audits
    transition {
      days          = 365
      storage_class = "DEEP_ARCHIVE"
    }

    # Delete after 7 years (2555 days)
    expiration {
      days = 2555
    }

    # Clean up incomplete multipart uploads
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Config snapshots
  rule {
    id     = "config-lifecycle"
    status = "Enabled"

    filter {
      prefix = "config/"
    }

    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }

    transition {
      days          = 365
      storage_class = "DEEP_ARCHIVE"
    }

    expiration {
      days = 2555
    }
  }

  # Security findings from Lambda archiver
  rule {
    id     = "security-findings-lifecycle"
    status = "Enabled"

    filter {
      prefix = "security-findings/"
    }

    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }

    transition {
      days          = 365
      storage_class = "DEEP_ARCHIVE"
    }

    expiration {
      days = 2555
    }
  }

  # Application, authentication, payment, game and security event logs
  # delivered from CloudWatch Logs by Firehose (see firehose.tf). These carry
  # the transaction and gaming records behind every AML and fraud alarm, so
  # they get the same schedule as the API audit trail.
  rule {
    id     = "app-logs-lifecycle"
    status = "Enabled"

    filter {
      prefix = "app-logs/"
    }

    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }

    transition {
      days          = 365
      storage_class = "DEEP_ARCHIVE"
    }

    expiration {
      days = 2555
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Delete old version markers after 7 years + 30 days
  rule {
    id     = "noncurrent-version-cleanup"
    status = "Enabled"

    filter {}

    noncurrent_version_transition {
      noncurrent_days = 90
      storage_class   = "GLACIER_IR"
    }

    noncurrent_version_transition {
      noncurrent_days = 365
      storage_class   = "DEEP_ARCHIVE"
    }

    noncurrent_version_expiration {
      noncurrent_days = 2585 # 7 years + 30 day buffer
    }
  }
}

# --- Bucket Policy ---
# Allow CloudTrail, Config, and the alert processor Lambda to write logs.
resource "aws_s3_bucket_policy" "log_archive" {
  bucket = aws_s3_bucket.log_archive.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudTrailACLCheck"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.log_archive.arn
        Condition = {
          StringEquals = {
            "aws:SourceArn" = "arn:${local.partition}:cloudtrail:${local.region}:${local.account_id}:trail/${local.name_prefix}-trail"
          }
        }
      },
      {
        Sid    = "AllowCloudTrailWrite"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.log_archive.arn}/cloudtrail/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"  = "bucket-owner-full-control"
            "aws:SourceArn" = "arn:${local.partition}:cloudtrail:${local.region}:${local.account_id}:trail/${local.name_prefix}-trail"
          }
        }
      },
      {
        Sid    = "AllowConfigACLCheck"
        Effect = "Allow"
        Principal = {
          Service = "config.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.log_archive.arn
      },
      {
        Sid    = "AllowConfigWrite"
        Effect = "Allow"
        Principal = {
          Service = "config.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.log_archive.arn}/config/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
      {
        Sid       = "DenyUnencryptedUploads"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.log_archive.arn}/*"
        Condition = {
          StringNotEquals = {
            "s3:x-amz-server-side-encryption" = "aws:kms"
          }
        }
      },
      {
        Sid       = "DenyHTTPAccess"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.log_archive.arn,
          "${aws_s3_bucket.log_archive.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

# --- Object Lock Configuration ---
# Uncomment to enable object lock (WORM) for the bucket.
# This provides regulatory-grade immutability but must be enabled
# at bucket creation time (cannot be added later).
#
# resource "aws_s3_bucket_object_lock_configuration" "log_archive" {
#   bucket = aws_s3_bucket.log_archive.id
#
#   rule {
#     default_retention {
#       mode = "COMPLIANCE"
#       days = 2555 # 7 years
#     }
#   }
# }
