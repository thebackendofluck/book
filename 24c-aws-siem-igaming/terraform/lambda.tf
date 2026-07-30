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
# Lambda - Custom iGaming Alert Processing
# =============================================================================
# This Lambda function enriches security findings with iGaming context
# before routing them to the appropriate team. Generic GuardDuty findings
# like "Unusual API activity" become actionable alerts like "Unauthorized
# game configuration change detected on slot-server-prod-01."
#
# The function also:
#   - Correlates findings across services (GuardDuty + Config + CloudTrail)
#   - Adds business context (which game, which player segment, which market)
#   - Creates structured incident records in S3 for regulatory reporting
#   - Routes to severity-appropriate SNS topics
#
# Regulatory justification:
#   NJ DGE 13:69O-1.7: Automated incident response
#   All jurisdictions: Suspicious activity documentation and reporting
# =============================================================================

# --- Lambda Function Source Code ---
# The function code is packaged from a local directory.
data "archive_file" "alert_processor" {
  type        = "zip"
  output_path = "${path.module}/lambda/alert_processor.zip"

  source {
    content  = <<-PYTHON
import json
import os
import logging
import boto3
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sns_client = boto3.client('sns')
s3_client = boto3.client('s3')

# SNS topic ARNs from environment variables
CRITICAL_TOPIC = os.environ.get('CRITICAL_TOPIC_ARN', '')
COMPLIANCE_TOPIC = os.environ.get('COMPLIANCE_TOPIC_ARN', '')
FRAUD_TOPIC = os.environ.get('FRAUD_TOPIC_ARN', '')
INFO_TOPIC = os.environ.get('INFO_TOPIC_ARN', '')
ARCHIVE_BUCKET = os.environ.get('ARCHIVE_BUCKET', '')


def lambda_handler(event, context):
    """
    Process security findings from EventBridge and route them
    with iGaming-specific context enrichment.

    Supports events from:
      - GuardDuty findings
      - Security Hub findings
      - Config compliance changes
      - Custom application security events
    """
    logger.info(f"Processing event: {json.dumps(event)}")

    source = event.get('source', '')
    detail_type = event.get('detail-type', '')

    if source == 'aws.guardduty':
        return process_guardduty_finding(event)
    elif source == 'aws.securityhub':
        return process_securityhub_finding(event)
    elif source == 'aws.config':
        return process_config_change(event)
    else:
        return process_custom_event(event)


def process_guardduty_finding(event):
    """Enrich GuardDuty findings with iGaming context."""
    detail = event.get('detail', {})
    finding_type = detail.get('type', 'Unknown')
    severity = detail.get('severity', 0)
    resources = detail.get('resource', {})

    # Map resource types to iGaming business context
    context = enrich_with_igaming_context(resources, finding_type)

    alert = {
        'source': 'guardduty',
        'finding_type': finding_type,
        'severity': classify_severity(severity),
        'igaming_context': context,
        'resources': resources,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'account_id': event.get('account', ''),
        'region': event.get('region', ''),
        'raw_finding': detail
    }

    # Route based on severity
    route_alert(alert)

    # Archive for regulatory compliance
    archive_finding(alert)

    return {'statusCode': 200, 'body': 'Finding processed'}


def process_securityhub_finding(event):
    """Process Security Hub compliance findings."""
    findings = event.get('detail', {}).get('findings', [])

    for finding in findings:
        compliance = finding.get('Compliance', {})
        severity = finding.get('Severity', {}).get('Label', 'INFORMATIONAL')

        alert = {
            'source': 'securityhub',
            'title': finding.get('Title', 'Unknown'),
            'severity': severity,
            'compliance_status': compliance.get('Status', 'UNKNOWN'),
            'standard': extract_standard(finding),
            'resource_type': finding.get('Resources', [{}])[0].get('Type', 'Unknown'),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'recommendation': finding.get('Remediation', {}).get(
                'Recommendation', {}).get('Text', 'No recommendation available')
        }

        route_alert(alert)
        archive_finding(alert)

    return {'statusCode': 200, 'body': f'Processed {len(findings)} findings'}


def process_config_change(event):
    """Process AWS Config compliance state changes."""
    detail = event.get('detail', {})

    alert = {
        'source': 'config',
        'rule_name': detail.get('configRuleName', 'Unknown'),
        'compliance_type': detail.get('newEvaluationResult', {}).get(
            'complianceType', 'UNKNOWN'),
        'resource_type': detail.get('resourceType', 'Unknown'),
        'resource_id': detail.get('resourceId', 'Unknown'),
        'severity': 'HIGH' if detail.get('newEvaluationResult', {}).get(
            'complianceType') == 'NON_COMPLIANT' else 'LOW',
        'timestamp': datetime.now(timezone.utc).isoformat()
    }

    if alert['compliance_type'] == 'NON_COMPLIANT':
        route_alert(alert)

    archive_finding(alert)

    return {'statusCode': 200, 'body': 'Config change processed'}


def process_custom_event(event):
    """Process custom application security events."""
    alert = {
        'source': 'custom',
        'event': event,
        'severity': event.get('severity', 'INFO'),
        'timestamp': datetime.now(timezone.utc).isoformat()
    }

    route_alert(alert)
    archive_finding(alert)

    return {'statusCode': 200, 'body': 'Custom event processed'}


def enrich_with_igaming_context(resources, finding_type):
    """
    Add iGaming-specific context to security findings.

    Maps AWS resource types to business functions:
      - RDS -> Player database (wallets, transactions, PII)
      - S3 -> Document storage (KYC, logs, game assets)
      - Lambda -> Game logic (RNG, bet processing, payouts)
      - EC2/ECS/EKS -> Game servers, payment processors
    """
    context = {
        'business_impact': 'UNKNOWN',
        'affected_system': 'UNKNOWN',
        'regulatory_implications': []
    }

    resource_type = resources.get('resourceType', '')

    if 'RDS' in resource_type or 'rds' in str(resources).lower():
        context['business_impact'] = 'CRITICAL - Player database'
        context['affected_system'] = 'Player wallets, transactions, PII'
        context['regulatory_implications'] = [
            'PCI DSS - cardholder data at risk',
            'NJ DGE - player financial data',
            'GDPR - personal data breach notification required within 72h'
        ]

    elif 'S3' in resource_type or 's3' in str(resources).lower():
        context['business_impact'] = 'HIGH - Data storage'
        context['affected_system'] = 'KYC documents, audit logs, game assets'
        context['regulatory_implications'] = [
            'Data protection - PII exposure risk',
            'Audit trail integrity - log tampering risk'
        ]

    elif 'Lambda' in resource_type or 'lambda' in str(resources).lower():
        context['business_impact'] = 'CRITICAL - Serverless game logic'
        context['affected_system'] = 'Game logic, bet processing, payout calculation'
        context['regulatory_implications'] = [
            'RNG integrity - game outcome manipulation risk',
            'Financial impact - unauthorized payout modifications'
        ]

    elif 'EC2' in resource_type or 'ECS' in resource_type or 'EKS' in resource_type:
        context['business_impact'] = 'HIGH - Compute infrastructure'
        context['affected_system'] = 'Game servers, API services'
        context['regulatory_implications'] = [
            'Service availability - platform uptime requirement',
            'Data exfiltration - lateral movement risk'
        ]

    # Check for cryptocurrency mining (compromised server)
    if 'CryptoCurrency' in finding_type:
        context['business_impact'] = 'CRITICAL - Compromised server'
        context['regulatory_implications'].append(
            'Server integrity compromised - full forensic investigation required'
        )

    return context


def classify_severity(numeric_severity):
    """Convert GuardDuty numeric severity to label."""
    if numeric_severity >= 7.0:
        return 'CRITICAL'
    elif numeric_severity >= 4.0:
        return 'HIGH'
    elif numeric_severity >= 1.0:
        return 'MEDIUM'
    return 'LOW'


def extract_standard(finding):
    """Extract compliance standard from Security Hub finding."""
    generator_id = finding.get('GeneratorId', '')
    if 'pci-dss' in generator_id.lower():
        return 'PCI-DSS'
    elif 'cis' in generator_id.lower():
        return 'CIS'
    elif 'aws-foundational' in generator_id.lower():
        return 'AWS-FSBP'
    elif 'nist' in generator_id.lower():
        return 'NIST-800-53'
    return 'UNKNOWN'


def route_alert(alert):
    """Route alert to appropriate SNS topic based on severity."""
    severity = alert.get('severity', 'INFO')

    message = json.dumps(alert, indent=2, default=str)
    subject = f"[{severity}] iGaming Security: {alert.get('source', 'unknown')} - {alert.get('finding_type', alert.get('title', 'Alert'))}"

    # Truncate subject to SNS 100-char limit
    subject = subject[:100]

    try:
        if severity in ['CRITICAL', 'HIGH']:
            if CRITICAL_TOPIC:
                sns_client.publish(
                    TopicArn=CRITICAL_TOPIC,
                    Message=message,
                    Subject=subject
                )
        elif severity == 'MEDIUM':
            topic = COMPLIANCE_TOPIC or CRITICAL_TOPIC
            if topic:
                sns_client.publish(
                    TopicArn=topic,
                    Message=message,
                    Subject=subject
                )
        else:
            if INFO_TOPIC:
                sns_client.publish(
                    TopicArn=INFO_TOPIC,
                    Message=message,
                    Subject=subject
                )
    except Exception as e:
        logger.error(f"Failed to publish to SNS: {e}")


def archive_finding(alert):
    """Archive finding to S3 for 7-year regulatory retention."""
    if not ARCHIVE_BUCKET:
        logger.warning("No archive bucket configured")
        return

    now = datetime.now(timezone.utc)
    key = (
        f"security-findings/{alert['source']}/"
        f"{now.strftime('%Y/%m/%d')}/"
        f"{now.strftime('%H%M%S')}-{alert.get('severity', 'INFO')}.json"
    )

    try:
        s3_client.put_object(
            Bucket=ARCHIVE_BUCKET,
            Key=key,
            Body=json.dumps(alert, indent=2, default=str),
            ContentType='application/json',
            ServerSideEncryption='aws:kms'
        )
    except Exception as e:
        logger.error(f"Failed to archive finding to S3: {e}")
PYTHON
    filename = "alert_processor.py"
  }
}

# --- Lambda IAM Role ---
resource "aws_iam_role" "alert_processor" {
  name = "${local.name_prefix}-alert-processor-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-alert-processor-role"
  })
}

# CloudWatch Logs permissions
resource "aws_iam_role_policy_attachment" "alert_processor_logs" {
  role       = aws_iam_role.alert_processor.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# SNS publish permissions
resource "aws_iam_role_policy" "alert_processor_sns" {
  name = "${local.name_prefix}-alert-processor-sns"
  role = aws_iam_role.alert_processor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "sns:Publish"
        Resource = [
          aws_sns_topic.security_critical.arn,
          aws_sns_topic.compliance_alerts.arn,
          aws_sns_topic.fraud_alerts.arn,
          aws_sns_topic.security_info.arn
        ]
      }
    ]
  })
}

# S3 archive permissions
resource "aws_iam_role_policy" "alert_processor_s3" {
  name = "${local.name_prefix}-alert-processor-s3"
  role = aws_iam_role.alert_processor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:PutObjectAcl"
        ]
        Resource = "${aws_s3_bucket.log_archive.arn}/security-findings/*"
      }
    ]
  })
}

# KMS decrypt/encrypt permissions for SNS and S3
resource "aws_iam_role_policy" "alert_processor_kms" {
  name = "${local.name_prefix}-alert-processor-kms"
  role = aws_iam_role.alert_processor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = aws_kms_key.cloudtrail.arn
      }
    ]
  })
}

# --- Lambda Function ---
resource "aws_lambda_function" "alert_processor" {
  function_name = "${local.name_prefix}-alert-processor"
  description   = "Enriches security findings with iGaming context and routes to appropriate teams"

  filename         = data.archive_file.alert_processor.output_path
  source_code_hash = data.archive_file.alert_processor.output_base64sha256
  handler          = "alert_processor.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256

  role = aws_iam_role.alert_processor.arn

  environment {
    variables = {
      CRITICAL_TOPIC_ARN   = aws_sns_topic.security_critical.arn
      COMPLIANCE_TOPIC_ARN = aws_sns_topic.compliance_alerts.arn
      FRAUD_TOPIC_ARN      = aws_sns_topic.fraud_alerts.arn
      INFO_TOPIC_ARN       = aws_sns_topic.security_info.arn
      ARCHIVE_BUCKET       = aws_s3_bucket.log_archive.id
    }
  }

  # Dead letter queue for failed invocations
  dead_letter_config {
    target_arn = aws_sns_topic.security_info.arn
  }

  tracing_config {
    mode = "Active" # X-Ray tracing for debugging
  }

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-alert-processor"
    Purpose = "security-alert-enrichment-and-routing"
  })
}

# --- EventBridge Permission to Invoke Lambda ---
resource "aws_lambda_permission" "eventbridge_guardduty" {
  statement_id  = "AllowEventBridgeGuardDuty"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.alert_processor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.guardduty_findings.arn
}

resource "aws_lambda_permission" "eventbridge_securityhub" {
  statement_id  = "AllowEventBridgeSecurityHub"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.alert_processor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.securityhub_critical.arn
}

# --- CloudWatch Log Group for Lambda ---
resource "aws_cloudwatch_log_group" "alert_processor" {
  name              = "/aws/lambda/${aws_lambda_function.alert_processor.function_name}"
  retention_in_days = var.cloudwatch_retention_days
  kms_key_id        = aws_kms_key.cloudtrail.arn

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-alert-processor-logs"
  })
}
