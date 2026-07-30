# Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Service Control Policies (SCPs) for Gambling Platform
#
# SCPs enforce organizational guardrails required for gambling compliance:
#   - Region restrictions (data must stay in approved jurisdictions)
#   - Encryption requirements (PCI DSS, data protection)
#   - Audit log protection (regulatory retention requirements)
#   - Instance type limits (cost control in dev environments)
#   - Public access prevention (security baseline)
#
# These policies are attached to OUs, not individual accounts.
# =============================================================================

# ---------------------------------------------------------------------------
# SCP: Restrict AWS Regions
#
# Gambling regulations require data to reside in specific jurisdictions.
# This policy restricts all API calls to approved regions only.
# ---------------------------------------------------------------------------

resource "aws_organizations_policy" "restrict_regions" {
  name        = "restrict-regions"
  description = "Restrict operations to approved gambling jurisdiction regions"
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DenyUnapprovedRegions"
        Effect   = "Deny"
        Action   = "*"
        Resource = "*"
        Condition = {
          StringNotEquals = {
            "aws:RequestedRegion" = [
              "eu-west-1",    # Ireland - EU/MGA operations
              "eu-west-2",    # London - UKGC operations
              "eu-central-1", # Frankfurt - GDPR data processing
              "us-east-1",    # Virginia - US market operations
              "us-east-2",    # Ohio - US DR region
              "ca-central-1", # Canada - Ontario operations
              "sa-east-1",    # Sao Paulo - Brazil operations
            ]
          }
        }
      },
      {
        # Allow global services (IAM, Route53, CloudFront, Organizations)
        Sid    = "AllowGlobalServices"
        Effect = "Allow"
        Action = [
          "iam:*",
          "organizations:*",
          "route53:*",
          "cloudfront:*",
          "waf:*",
          "wafv2:*",
          "waf-regional:*",
          "support:*",
          "sts:*",
          "budgets:*",
        ]
        Resource = "*"
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# SCP: Deny Public S3 Buckets
#
# Prevent any S3 bucket from being made public. Player data, financial
# records, and audit logs must never be publicly accessible.
# ---------------------------------------------------------------------------

resource "aws_organizations_policy" "deny_public_s3" {
  name        = "deny-public-s3"
  description = "Prevent S3 buckets from being made public"
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyS3PublicAccess"
        Effect = "Deny"
        Action = [
          "s3:PutBucketPublicAccessBlock",
          "s3:PutAccountPublicAccessBlock",
        ]
        Resource = "*"
        Condition = {
          StringNotEquals = {
            # Only allow setting public access block to restrict (not enable)
            "s3:publicAccessBlockConfiguration/BlockPublicAcls"       = "true"
            "s3:publicAccessBlockConfiguration/BlockPublicPolicy"     = "true"
            "s3:publicAccessBlockConfiguration/IgnorePublicAcls"      = "true"
            "s3:publicAccessBlockConfiguration/RestrictPublicBuckets" = "true"
          }
        }
      },
      {
        Sid    = "DenyS3PublicObjectACLs"
        Effect = "Deny"
        Action = [
          "s3:PutBucketAcl",
          "s3:PutObjectAcl",
        ]
        Resource = "*"
        Condition = {
          StringLike = {
            "s3:x-amz-acl" = [
              "public-read",
              "public-read-write",
              "authenticated-read",
            ]
          }
        }
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# SCP: Require Encryption
#
# Enforce encryption at rest for all storage services.
# Required for PCI DSS compliance and player data protection.
# ---------------------------------------------------------------------------

resource "aws_organizations_policy" "require_encryption" {
  name        = "require-encryption"
  description = "Enforce encryption for all storage services (PCI DSS requirement)"
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DenyUnencryptedS3Uploads"
        Effect   = "Deny"
        Action   = "s3:PutObject"
        Resource = "*"
        Condition = {
          StringNotEqualsIfExists = {
            "s3:x-amz-server-side-encryption" = ["aws:kms", "AES256"]
          }
          Null = {
            "s3:x-amz-server-side-encryption" = "true"
          }
        }
      },
      {
        Sid    = "DenyUnencryptedRDS"
        Effect = "Deny"
        Action = [
          "rds:CreateDBInstance",
          "rds:CreateDBCluster",
        ]
        Resource = "*"
        Condition = {
          Bool = {
            "rds:StorageEncrypted" = "false"
          }
        }
      },
      {
        Sid      = "DenyUnencryptedEBS"
        Effect   = "Deny"
        Action   = "ec2:CreateVolume"
        Resource = "*"
        Condition = {
          Bool = {
            "ec2:Encrypted" = "false"
          }
        }
      },
      {
        Sid      = "DenyUnencryptedEFS"
        Effect   = "Deny"
        Action   = "elasticfilesystem:CreateFileSystem"
        Resource = "*"
        Condition = {
          Bool = {
            "elasticfilesystem:Encrypted" = "false"
          }
        }
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# SCP: Deny Deletion of Audit Logs
#
# Gambling regulators require retention of audit logs for 5-7 years.
# This policy prevents anyone from deleting CloudTrail, Config, or
# compliance-tagged resources.
# ---------------------------------------------------------------------------

resource "aws_organizations_policy" "deny_delete_logs" {
  name        = "deny-delete-logs"
  description = "Prevent deletion of audit and compliance resources"
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyCloudTrailModification"
        Effect = "Deny"
        Action = [
          "cloudtrail:DeleteTrail",
          "cloudtrail:StopLogging",
          "cloudtrail:UpdateTrail",
          "cloudtrail:PutEventSelectors",
        ]
        Resource = "*"
        Condition = {
          StringNotLike = {
            "aws:PrincipalArn" = [
              "arn:aws:iam::*:role/OrganizationAccountAccessRole",
            ]
          }
        }
      },
      {
        Sid    = "DenyConfigModification"
        Effect = "Deny"
        Action = [
          "config:DeleteConfigurationRecorder",
          "config:DeleteDeliveryChannel",
          "config:StopConfigurationRecorder",
        ]
        Resource = "*"
      },
      {
        Sid    = "DenyGuardDutyDisable"
        Effect = "Deny"
        Action = [
          "guardduty:DeleteDetector",
          "guardduty:DisassociateFromMasterAccount",
          "guardduty:UpdateDetector",
        ]
        Resource = "*"
      },
      {
        Sid    = "DenySecurityHubDisable"
        Effect = "Deny"
        Action = [
          "securityhub:DisableSecurityHub",
          "securityhub:DeleteMembers",
          "securityhub:DisassociateMembers",
        ]
        Resource = "*"
      },
      {
        # Prevent VPC Flow Log deletion (network audit trail)
        Sid      = "DenyFlowLogDeletion"
        Effect   = "Deny"
        Action   = "ec2:DeleteFlowLogs"
        Resource = "*"
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# SCP: Audit Account Read-Only
#
# External auditors get read-only access. They cannot modify any resources.
# ---------------------------------------------------------------------------

resource "aws_organizations_policy" "audit_read_only" {
  name        = "audit-read-only"
  description = "Enforce read-only access in audit account for external auditors"
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyAllWriteActions"
        Effect = "Deny"
        Action = [
          "ec2:Run*",
          "ec2:Create*",
          "ec2:Delete*",
          "ec2:Terminate*",
          "ec2:Modify*",
          "rds:Create*",
          "rds:Delete*",
          "rds:Modify*",
          "s3:Put*",
          "s3:Delete*",
          "s3:Create*",
          "lambda:Create*",
          "lambda:Delete*",
          "lambda:Update*",
          "iam:Create*",
          "iam:Delete*",
          "iam:Put*",
          "iam:Update*",
          "iam:Attach*",
          "iam:Detach*",
        ]
        Resource = "*"
        Condition = {
          StringNotLike = {
            "aws:PrincipalArn" = [
              "arn:aws:iam::*:role/OrganizationAccountAccessRole",
            ]
          }
        }
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# SCP: Limit Instance Types (Development)
#
# Prevent developers from launching expensive instances in dev accounts.
# ---------------------------------------------------------------------------

resource "aws_organizations_policy" "limit_instance_types" {
  name        = "limit-instance-types"
  description = "Limit EC2/RDS instance types in development environments"
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DenyLargeEC2Instances"
        Effect   = "Deny"
        Action   = "ec2:RunInstances"
        Resource = "arn:aws:ec2:*:*:instance/*"
        Condition = {
          StringNotLike = {
            "ec2:InstanceType" = [
              "t3.*",
              "t3a.*",
              "t4g.*",
              "m6i.large",
              "m6i.xlarge",
              "m7g.large",
              "m7g.xlarge",
              "r6i.large",
              "c6i.large",
              "c6i.xlarge",
            ]
          }
        }
      },
      {
        Sid    = "DenyLargeRDSInstances"
        Effect = "Deny"
        Action = [
          "rds:CreateDBInstance",
          "rds:CreateDBCluster",
        ]
        Resource = "*"
        Condition = {
          StringNotLike = {
            "rds:DatabaseClass" = [
              "db.t3.*",
              "db.t4g.*",
              "db.r6g.large",
              "db.r6g.xlarge",
            ]
          }
        }
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# SCP: Deny Leaving Organization
#
# Prevent any account from leaving the organization.
# ---------------------------------------------------------------------------

resource "aws_organizations_policy" "deny_leave_org" {
  name        = "deny-leave-organization"
  description = "Prevent accounts from leaving the organization"
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DenyLeaveOrganization"
        Effect   = "Deny"
        Action   = "organizations:LeaveOrganization"
        Resource = "*"
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# Policy Attachments
# ---------------------------------------------------------------------------

# Attach region restriction to entire gambling platform OU
resource "aws_organizations_policy_attachment" "restrict_regions" {
  policy_id = aws_organizations_policy.restrict_regions.id
  target_id = aws_organizations_organizational_unit.gambling_platform.id
}

# Deny public S3 to all environments
resource "aws_organizations_policy_attachment" "deny_public_s3" {
  policy_id = aws_organizations_policy.deny_public_s3.id
  target_id = aws_organizations_organizational_unit.gambling_platform.id
}

# Require encryption in production, compliance, and security
resource "aws_organizations_policy_attachment" "require_encryption_production" {
  policy_id = aws_organizations_policy.require_encryption.id
  target_id = aws_organizations_organizational_unit.environments["production"].id
}

resource "aws_organizations_policy_attachment" "require_encryption_compliance" {
  policy_id = aws_organizations_policy.require_encryption.id
  target_id = aws_organizations_organizational_unit.environments["compliance"].id
}

resource "aws_organizations_policy_attachment" "require_encryption_security" {
  policy_id = aws_organizations_policy.require_encryption.id
  target_id = aws_organizations_organizational_unit.environments["security"].id
}

# Deny log deletion in compliance account
resource "aws_organizations_policy_attachment" "deny_delete_logs" {
  policy_id = aws_organizations_policy.deny_delete_logs.id
  target_id = aws_organizations_organizational_unit.environments["compliance"].id
}

# Audit account read-only
resource "aws_organizations_policy_attachment" "audit_read_only" {
  policy_id = aws_organizations_policy.audit_read_only.id
  target_id = aws_organizations_organizational_unit.environments["audit"].id
}

# Limit instance types in development
resource "aws_organizations_policy_attachment" "limit_instance_types" {
  policy_id = aws_organizations_policy.limit_instance_types.id
  target_id = aws_organizations_organizational_unit.environments["development"].id
}

# Deny leaving organization - attach to root
resource "aws_organizations_policy_attachment" "deny_leave_org" {
  policy_id = aws_organizations_policy.deny_leave_org.id
  target_id = aws_organizations_organization.casino.roots[0].id
}
