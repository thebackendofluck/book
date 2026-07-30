# Chapter 24c — AWS SIEM: Terraform Configuration

These files are part of the book's simulation platform for Chapter 24c (AWS SIEM for iGaming compliance).

## What This Deploys

A complete AWS security monitoring stack designed for iGaming regulatory compliance (NJ DGE, PA PGCB, MI MGCB):

| Service | Purpose |
|---------|---------|
| GuardDuty | Threat detection and anomaly analysis |
| Security Hub | Compliance scoring and findings aggregation |
| CloudTrail | Full API audit trail for all AWS activity |
| AWS Config | Configuration drift detection and compliance rules |
| CloudWatch | Log aggregation, metrics, and automated alarms |
| SNS | Alert routing to ops teams |
| Lambda | Custom alert processing and enrichment |
| S3 | 7-year log archive (regulatory requirement) |
| WAF | Web application protection |
| KMS | Encryption at rest for all security data |

## Files

| File | Purpose |
|------|---------|
| `main.tf` | Provider config, Terraform version constraints |
| `variables.tf` | Input variables (region, environment, alert emails) |
| `outputs.tf` | Exported resource IDs and ARNs |
| `guardduty.tf` | GuardDuty detector and findings export |
| `security-hub.tf` | Security Hub with CIS, PCI-DSS, and NIST standards |
| `cloudtrail.tf` | Multi-region CloudTrail with S3 and CloudWatch integration |
| `config.tf` | AWS Config recorder and compliance rules |
| `cloudwatch.tf` | Log groups, metric filters, and alarms |
| `sns.tf` | Alert topics and subscriptions |
| `lambda.tf` | Alert processing Lambda function |
| `s3.tf` | Log archive bucket with lifecycle policies |
| `firehose.tf` | CloudWatch Logs -> Firehose -> S3 delivery for the application log groups |
| `waf.tf` | WAF v2 web ACL and its resource association |

## Dependencies

- AWS account with sufficient IAM permissions
- Terraform >= 1.5.0
- AWS provider ~> 5.0
- No other chapter's infrastructure required (standalone)

## How to Run

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your AWS account details

terraform init
terraform plan -var-file="terraform.tfvars"
terraform apply -var-file="terraform.tfvars"

# To tear down
terraform destroy -var-file="terraform.tfvars"
```

## Two things to set before this protects anything

**`waf_protected_resource_arns`.** The Web ACL is created with sixteen rules but a
Web ACL evaluates nothing until it is associated with a resource. With `waf_scope
= "REGIONAL"` (the default) pass the ALB / API Gateway stage / AppSync ARNs to
attach to; the ACL is otherwise live, billing, and inspecting no traffic. A `check`
block warns at plan time when the list is empty. With `waf_scope = "CLOUDFRONT"`
there is no association resource: set `web_acl_id` on the distribution using the
`waf_web_acl_arn` output. Verify with:

```bash
aws wafv2 list-resources-for-web-acl \
  --web-acl-arn "$(terraform output -raw waf_web_acl_arn)" \
  --resource-type APPLICATION_LOAD_BALANCER
```

**Retention thresholds.** `cash_report_threshold` and `structuring_band_floor`
default to USD 10,000 / USD 8,000 in cents (FinCEN CTR). Confirm the reportable
amount for each jurisdiction before relying on the structuring-band alarm, and
confirm the retention schedule in `s3.tf` against the licence conditions rather
than inheriting the seven-year example.

## What the alarms do and do not correlate

Metric filters count matching log lines. They hold no state between events, so no
alarm in `cloudwatch.tf` performs per-player, per-IP or cross-event correlation,
and the resource names say so deliberately: `withdrawal-volume-high` and
`account-creation-rate-high` are platform-wide volume signals, not structuring or
multi-accounting detection. The one exception is
`structuring-band-withdrawals`, which compares an amount within a single event and
is genuinely expressible here. Account linkage and per-player velocity belong in a
query over the `app-logs/` archive or in the fraud pipeline (Chapter 19).

## Source

Original files: `new-platform/terraform/aws-siem/`

These configurations are from the book's simulation platform. They demonstrate a production-grade AWS SIEM implementation meeting multi-state iGaming regulatory requirements.
