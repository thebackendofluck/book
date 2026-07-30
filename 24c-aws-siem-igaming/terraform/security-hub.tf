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
# AWS Security Hub - Compliance Scoring for iGaming
# =============================================================================
# Security Hub aggregates findings from GuardDuty, Inspector, Config, Macie,
# and third-party tools into a unified view with compliance scoring.
#
# For iGaming operators, Security Hub provides:
#   - PCI DSS v3.2.1 compliance score (required for payment processing)
#   - CIS AWS Foundations benchmark (baseline security hygiene)
#   - AWS Foundational Security Best Practices (comprehensive coverage)
#   - NIST 800-53 (required by some US state regulators)
#
# Regulatory justification:
#   PCI DSS: Required for any operator processing card payments
#   NJ DGE 13:69O-1.1: Continuous compliance monitoring
#   PA PGCB: Monthly security compliance reports
# =============================================================================

# --- Security Hub ---
resource "aws_securityhub_account" "main" {
  enable_default_standards  = false # We'll enable specific standards below
  control_finding_generator = "SECURITY_CONTROL"
  auto_enable_controls      = true
}

# --- PCI DSS v3.2.1 Standard ---
# Mandatory for any operator processing credit/debit card payments.
# Covers 161 automated checks across encryption, access control, logging,
# and network security.
resource "aws_securityhub_standards_subscription" "pci_dss" {
  standards_arn = "arn:${local.partition}:securityhub:${local.region}::standards/pci-dss/v/3.2.1"

  depends_on = [aws_securityhub_account.main]
}

# --- CIS AWS Foundations v1.4.0 ---
# Industry-standard benchmark for AWS account security. Covers IAM,
# logging, monitoring, and networking best practices.
resource "aws_securityhub_standards_subscription" "cis" {
  standards_arn = "arn:${local.partition}:securityhub:${local.region}::standards/cis-aws-foundations-benchmark/v/1.4.0"

  depends_on = [aws_securityhub_account.main]
}

# --- AWS Foundational Security Best Practices ---
# AWS's own comprehensive security checks. More granular than CIS,
# covers service-specific configurations.
resource "aws_securityhub_standards_subscription" "aws_fsbp" {
  standards_arn = "arn:${local.partition}:securityhub:${local.region}::standards/aws-foundational-security-best-practices/v/1.0.0"

  depends_on = [aws_securityhub_account.main]
}

# --- NIST 800-53 Rev 5 ---
# Federal security standard. Some US state gaming regulators reference
# NIST frameworks in their technical standards.
resource "aws_securityhub_standards_subscription" "nist" {
  count = var.enable_nist_standard ? 1 : 0

  standards_arn = "arn:${local.partition}:securityhub:${local.region}::standards/nist-800-53/v/5.0.0"

  depends_on = [aws_securityhub_account.main]
}

# --- Security Hub Action Target ---
# Custom action for security team to acknowledge and escalate findings
# directly from the Security Hub console.
resource "aws_securityhub_action_target" "escalate_to_compliance" {
  name        = "EscalateToCompliance"
  identifier  = "EscalateCompliance"
  description = "Escalate finding to compliance team for regulatory review"

  depends_on = [aws_securityhub_account.main]
}

resource "aws_securityhub_action_target" "create_incident" {
  name        = "CreateSecIncident"
  identifier  = "CreateIncident"
  description = "Create a security incident for investigation and response"

  depends_on = [aws_securityhub_account.main]
}

# --- EventBridge Rule for Security Hub Findings ---
# Route critical compliance failures to SNS for immediate alerting.
# A PCI DSS failure could result in payment processing suspension.
resource "aws_cloudwatch_event_rule" "securityhub_critical" {
  name        = "${local.name_prefix}-securityhub-critical"
  description = "Alert on critical Security Hub compliance failures"

  event_pattern = jsonencode({
    source      = ["aws.securityhub"]
    detail-type = ["Security Hub Findings - Imported"]
    detail = {
      findings = {
        Severity = {
          Label = ["CRITICAL", "HIGH"]
        }
        Compliance = {
          Status = ["FAILED"]
        }
        Workflow = {
          Status = ["NEW"]
        }
      }
    }
  })

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-securityhub-critical-events"
  })
}

resource "aws_cloudwatch_event_target" "securityhub_to_sns" {
  rule      = aws_cloudwatch_event_rule.securityhub_critical.name
  target_id = "securityhub-to-compliance-sns"
  arn       = aws_sns_topic.compliance_alerts.arn
}

# Target: the enrichment and archive Lambda.
# The GuardDuty rule in guardduty.tf sends to both SNS and Lambda; this rule
# sent only to SNS, so process_securityhub_finding() in the alert processor
# never ran and no Security Hub finding was archived to S3 for the 7-year
# record. The lambda:InvokeFunction permission for this rule already existed
# (aws_lambda_permission.eventbridge_securityhub in lambda.tf) which is what
# made the gap easy to miss: the wiring looked complete from the IAM side.
resource "aws_cloudwatch_event_target" "securityhub_to_lambda" {
  rule      = aws_cloudwatch_event_rule.securityhub_critical.name
  target_id = "securityhub-to-lambda"
  arn       = aws_lambda_function.alert_processor.arn

  depends_on = [aws_lambda_permission.eventbridge_securityhub]
}

# --- Security Hub Insight: Multi-Jurisdiction Compliance ---
# Custom insight showing compliance status filtered by account/region,
# useful for multi-state operators (NJ + PA + MI).
resource "aws_securityhub_insight" "compliance_by_standard" {
  name = "${local.name_prefix}-compliance-failures-by-standard"

  filters {
    compliance_status {
      comparison = "EQUALS"
      value      = "FAILED"
    }
    workflow_status {
      comparison = "EQUALS"
      value      = "NEW"
    }
  }

  group_by_attribute = "ComplianceSecurityControlId"

  depends_on = [aws_securityhub_account.main]
}

resource "aws_securityhub_insight" "critical_resources" {
  name = "${local.name_prefix}-critical-findings-by-resource"

  filters {
    severity_label {
      comparison = "EQUALS"
      value      = "CRITICAL"
    }
    workflow_status {
      comparison = "EQUALS"
      value      = "NEW"
    }
  }

  group_by_attribute = "ResourceType"

  depends_on = [aws_securityhub_account.main]
}
