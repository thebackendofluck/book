<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-03.jpg" alt="Volume 3" width="150" /></a>

# Chapter 24c: AWS SIEM Implementation for iGaming Compliance

**📙 Part of Volume 3 — Security Engineering and Runtime Defense** · €84.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZCRSTMH) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 24c of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Terraform modules wiring GuardDuty, Security Hub, Config, Macie, Inspector, CloudTrail, and WAF into a PCI-DSS / NJ DGE compliant monitoring stack.

## Overview

Production Terraform code for the AWS-native SIEM stack. Each `.tf` file maps to a specific regulatory requirement (NJ DGE, PA PGCB, MGA TSD 4.3.2, PCI-DSS). EventBridge routes findings to CloudWatch, SNS, and Lambda for alerting and 7-year S3 archival.

## Contents

- `terraform/main.tf` — Provider config, remote state, and module wiring
- `terraform/guardduty.tf` — GuardDuty enablement with S3/EKS/RDS protection plans
- `terraform/security-hub.tf` — Security Hub with PCI-DSS, CIS AWS Foundations, and NIST 800-53 standards
- `terraform/config.tf` — AWS Config rules for encryption-at-rest, MFA, public S3 bucket detection
- `terraform/cloudtrail.tf` — Multi-region CloudTrail with CloudWatch Logs integration
- `terraform/cloudwatch.tf` — Metric filters, alarms, and log groups for iGaming-specific event patterns
- `terraform/waf.tf` — AWS WAF v2 Web ACL with rate limiting and geo-restriction rules
- `terraform/s3.tf` — 7-year log archive bucket with Object Lock (WORM) and lifecycle policies
- `terraform/sns.tf` — SNS topics: `security-critical`, `compliance-alerts`, PagerDuty/Slack endpoints
- `terraform/lambda.tf` — Lambda for custom finding enrichment and incident creation
- `terraform/variables.tf` / `outputs.tf` — Variable declarations and stack outputs
- `terraform/README.md` — Module-level documentation

## Technology Stack

- **IaC:** Terraform ≥ 1.7
- **AWS Services:** GuardDuty, Security Hub, AWS Config, CloudTrail, CloudWatch, WAF v2, Macie, Inspector v2, SNS, Lambda, S3
- **Alerting:** SNS → PagerDuty / Slack / email

## Prerequisites

- AWS CLI configured with permissions for all services above
- Terraform ≥ 1.7 (`terraform version`)
- S3 bucket and DynamoDB table for remote state (configure in `main.tf`)
- `TF_VAR_environment`, `TF_VAR_aws_region`, `TF_VAR_alert_email` set

## How to Run

```bash
cd terraform

terraform init
terraform plan -out=v2-ch24c.tfplan
terraform apply v2-ch24c.tfplan
```

## Security Notes

`terraform/s3.tf` enables S3 Object Lock in COMPLIANCE mode — once activated, logs cannot be deleted for the defined retention period. Verify retention duration matches your jurisdiction requirements before applying (NJ DGE: 5 years; MGA: 3 years minimum). WAF rate limits in `waf.tf` are tuned for iGaming traffic patterns; adjust `RateLimit` thresholds before applying to a live platform.

## Related

- See Chapter 24c in the book for the full AWS SIEM architecture and regulatory requirement mapping.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 2 · last updated 2026-04-16.</sub>
