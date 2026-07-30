<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-04.jpg" alt="Volume 4" width="150" /></a>

# Chapter 24: Security and Compliance

**📕 Part of Volume 4 — Compliance, Player Safety, Data Residency, and Governance** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS473SJ) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 24 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

Enterprise security infrastructure for iGaming platforms, implementing comprehensive protection against cyber threats, fraud, and compliance violations.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Security Infrastructure                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │  CloudFront │───▶│  AWS WAF    │───▶│    ALB      │───▶│    EKS      │  │
│  │    CDN      │    │  (Global)   │    │  (Regional) │    │  Cluster    │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│                            │                  │                  │          │
│                            ▼                  ▼                  ▼          │
│                     ┌─────────────────────────────────────────────────┐    │
│                     │              Security Services                   │    │
│                     │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌────────┐ │    │
│                     │  │ Guard   │ │Security │ │  AWS    │ │ Shield │ │    │
│                     │  │  Duty   │ │   Hub   │ │Inspector│ │Advanced│ │    │
│                     │  └─────────┘ └─────────┘ └─────────┘ └────────┘ │    │
│                     └─────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Table of Contents

1. [AWS WAF Implementation](#aws-waf-implementation)
2. [WAF Rules Reference](#waf-rules-reference)
3. [WAF Association Guide](#waf-association-guide)
4. [Rate Limiting Configuration](#rate-limiting-configuration)
5. [Debugging and Troubleshooting](#debugging-and-troubleshooting)
6. [Log Analysis](#log-analysis)
7. [Custom Rules Development](#custom-rules-development)
8. [Anti-DDoS Protection](#anti-ddos-protection)

---

## AWS WAF Implementation

### Overview

This implementation provides comprehensive Web Application Firewall protection with **full OWASP Top 10 2021 coverage** and **granular rule control**. Each rule can be individually enabled/disabled and configured.

| Web ACL | Scope | Use Case | Rules |
|---------|-------|----------|-------|
| `igaming-security-regional-waf` | REGIONAL | ALB, API Gateway, AppSync | 21 configurable rules |
| `igaming-security-cloudfront-waf` | CLOUDFRONT | CloudFront distributions | 4 rules |

### OWASP Top 10 2021 Coverage

| OWASP ID | Vulnerability | WAF Protection |
|----------|---------------|----------------|
| A01 | Broken Access Control | Geo Blocking, Rate Limiting, Admin Protection |
| A02 | Cryptographic Failures | (Application level - WAF inspects encrypted traffic after TLS termination) |
| A03 | Injection | SQLi Rules, Common Rules, XSS Rules, Known Bad Inputs |
| A04 | Insecure Design | (Application level) |
| A05 | Security Misconfiguration | Common Rules, Admin Path Protection |
| A06 | Vulnerable Components | Known Bad Inputs (Log4j, CVEs) |
| A07 | Auth Failures | Login Rate Limiting, Bot Control, Registration Limits |
| A08 | Software Integrity | (Application level) |
| A09 | Security Logging | WAF Logging (always enabled) |
| A10 | SSRF | Common Rules, Known Bad Inputs |

### Deployment

```bash
cd terraform/aws

# Initialize
terraform init

# Plan with default WAF configuration
terraform plan \
  -var="environment=production" \
  -var="notification_email=security@company.com"

# Or with custom WAF rules (see Granular Configuration below)
terraform plan \
  -var-file="waf-custom.tfvars"

# Apply
terraform apply -auto-approve
```

### Granular WAF Configuration

All WAF rules can be individually configured via the `waf_rules` variable. Create a `waf-custom.tfvars` file:

```hcl
# waf-custom.tfvars - Custom WAF rule configuration
waf_rules = {
  # OWASP A03 - SQL Injection Protection
  sqli_rule_set = {
    enabled        = true    # Enable/disable this rule group
    action         = "block" # "block" or "count" (monitor mode)
    excluded_rules = []      # List of specific rules to exclude
  }

  # OWASP A03 - XSS Protection
  xss_rule_set = {
    enabled        = true
    action         = "block"
    excluded_rules = []
  }

  # OWASP A06 - Known Vulnerabilities (Log4j, etc.)
  known_bad_inputs = {
    enabled        = true
    action         = "block"
    excluded_rules = []
  }

  # OWASP A01 - IP Reputation
  ip_reputation_list = {
    enabled = true
    action  = "block"
  }

  # OWASP A01 - Anonymous IP Control
  anonymous_ip_list = {
    enabled       = true
    action        = "count"  # Start with "count" to monitor
    block_vpn     = false    # May affect legitimate players
    block_tor     = true     # High fraud risk
    block_proxy   = false    # May affect legitimate players
    block_hosting = true     # Block datacenter IPs (bot farms)
  }

  # OWASP A07 - Bot Control
  bot_control = {
    enabled          = true
    inspection_level = "COMMON"  # "COMMON" or "TARGETED"
    action           = "block"
  }

  # Rate Limiting Configuration
  rate_limiting = {
    enabled            = true
    global_limit       = 2000  # requests per 5 min per IP
    login_limit        = 10    # login attempts per 5 min
    payment_limit      = 20    # payment requests per 5 min
    api_limit          = 500   # general API calls per 5 min
    registration_limit = 5     # registrations per 5 min per IP
  }

  # Geographic Access Control
  geo_blocking = {
    enabled           = true
    blocked_countries = ["KP", "IR", "SY", "CU", "RU", "BY"]  # OFAC + high-risk
    allowed_countries = []  # Empty = all except blocked
  }

  # Linux/Unix Protection
  linux_rule_set = {
    enabled        = true
    action         = "block"
    excluded_rules = []
  }

  # POSIX OS Protection
  posix_rule_set = {
    enabled        = true
    action         = "block"
    excluded_rules = []
  }

  # PHP Protection (enable if using PHP backends)
  php_rule_set = {
    enabled        = false
    action         = "block"
    excluded_rules = []
  }

  # WordPress Protection (enable for marketing sites)
  wordpress_rule_set = {
    enabled        = false
    action         = "block"
    excluded_rules = []
  }

  # Custom iGaming Rules
  igaming_custom_rules = {
    block_security_scanners = true   # Block sqlmap, nikto, nessus, etc.
    block_scraping_tools    = true   # Block scrapy, puppeteer, selenium
    admin_path_protection   = true   # Restrict /admin, /backoffice access
    bonus_abuse_protection  = true   # Protect bonus endpoints
    multi_account_detection = true   # Track multi-account patterns
  }

  # Account Takeover Prevention (requires AWS subscription)
  atp_rule_set = {
    enabled           = false
    login_path        = "/api/auth/login"
    registration_path = "/api/auth/register"
  }
}
```

### Output Values

```bash
# Get WAF ARNs for association
terraform output regional_waf_arn
terraform output cloudfront_waf_arn
terraform output waf_association_instructions
```

---

## WAF Rules Reference

### Complete Rule List (21 Rules)

| Priority | Rule Name | OWASP | Default | Description |
|----------|-----------|-------|---------|-------------|
| 0 | WhitelistedIPs | - | Always | Bypass rules for trusted IPs |
| 1 | GeoBlock | A01 | Enabled | Block sanctioned countries |
| 2 | RateLimitPerIP | A01 | Enabled | Global rate limiting (2000/5min) |
| 3 | AWSManagedRulesCommonRuleSet | A03, A05, A10 | Enabled | OWASP common protections |
| 4 | AWSManagedRulesSQLiRuleSet | A03 | Enabled | SQL injection detection |
| 5 | AWSManagedRulesXSSRuleSet | A03, A07 | Enabled | XSS detection |
| 6 | AWSManagedRulesKnownBadInputsRuleSet | A06 | Enabled | Log4j, CVE patterns |
| 7 | AWSManagedRulesAmazonIpReputationList | A01 | Enabled | AWS threat intelligence |
| 8 | AWSManagedRulesAnonymousIpList | A01 | Count | VPN/Proxy/Tor detection |
| 9 | AWSManagedRulesBotControlRuleSet | A07 | Enabled | Bot detection |
| 10 | AWSManagedRulesLinuxRuleSet | - | Enabled | Linux-specific attacks |
| 11 | AWSManagedRulesUnixRuleSet | - | Enabled | POSIX-specific attacks |
| 12 | AWSManagedRulesPHPRuleSet | - | Disabled | PHP-specific attacks |
| 13 | AWSManagedRulesWordPressRuleSet | - | Disabled | WordPress attacks |
| 14 | LoginEndpointRateLimit | A07 | Enabled | Brute force protection (10/5min) |
| 15 | PaymentEndpointRateLimit | - | Enabled | Transaction protection (20/5min) |
| 16 | RegistrationRateLimit | A07 | Enabled | Account spam prevention (5/5min) |
| 17 | APIRateLimit | - | Enabled | General API protection (500/5min) |
| 18 | AdminPathProtection | A05 | Enabled | Block non-whitelisted admin access |
| 19 | BlockSecurityScanners | - | Enabled | Block sqlmap, nikto, nessus, etc. |
| 20 | BlockScrapingTools | - | Enabled | Block scrapy, puppeteer, selenium |

### AWS Managed Rule Groups

| Rule Group | Rules Count | Cost | Description |
|------------|-------------|------|-------------|
| AWSManagedRulesCommonRuleSet | 24 | Free | Core protection rules |
| AWSManagedRulesSQLiRuleSet | 8 | Free | SQL injection protection |
| AWSManagedRulesKnownBadInputsRuleSet | 15 | Free | CVE and bad input patterns |
| AWSManagedRulesAmazonIpReputationList | 4 | Free | AWS threat intelligence |
| AWSManagedRulesAnonymousIpList | 4 | Free | Anonymous IP detection |
| AWSManagedRulesBotControlRuleSet | 17 | $10/mo | Bot detection and control |
| AWSManagedRulesLinuxRuleSet | 5 | Free | Linux-specific protection |
| AWSManagedRulesUnixRuleSet | 2 | Free | POSIX-specific protection |
| AWSManagedRulesPHPRuleSet | 5 | Free | PHP-specific protection |
| AWSManagedRulesWordPressRuleSet | 6 | Free | WordPress protection |

### Excluding Specific Rules

To exclude rules that cause false positives, add them to `excluded_rules`:

```hcl
common_rule_set = {
  enabled = true
  action  = "block"
  excluded_rules = [
    "SizeRestrictions_BODY",        # Large JSON payloads in gaming APIs
    "CrossSiteScripting_BODY",      # Rich text content
    "GenericLFI_BODY"               # File path parameters
  ]
}
```

### Common Rules to Exclude for iGaming

| Rule Name | Reason to Exclude |
|-----------|-------------------|
| `SizeRestrictions_BODY` | Gaming APIs may have large payloads |
| `CrossSiteScripting_BODY` | Chat/messaging features with HTML |
| `GenericLFI_BODY` | Game asset path parameters |
| `EC2MetaDataSSRF_BODY` | Internal service communications |

### Rule Priority Explanation

```
Lower number = Higher priority = Evaluated first

Priority 0: WhitelistedIPs
    ↓ (if not matched)
Priority 1: GeoBlock
    ↓ (if not matched)
Priority 2: RateLimitPerIP
    ↓ (if not matched)
...continues through all rules...
    ↓
Default Action: ALLOW
```

### Custom Response Bodies

| Key | HTTP Code | Content-Type | Message |
|-----|-----------|--------------|---------|
| `geo-blocked` | 403 | application/json | `{"error": "access_denied", "message": "Service not available in your region"}` |
| `rate-limited` | 429 | application/json | `{"error": "rate_limit_exceeded", "message": "Too many requests. Please try again later."}` |
| `login-rate-limited` | 429 | application/json | `{"error": "login_rate_limit", "message": "Too many login attempts. Please try again in 5 minutes."}` |
| `payment-rate-limited` | 429 | application/json | `{"error": "payment_rate_limit", "message": "Too many payment requests. Please try again later."}` |

---

## WAF Association Guide

### Associate with Application Load Balancer (ALB)

```bash
# Using AWS CLI
aws wafv2 associate-web-acl \
  --web-acl-arn "arn:aws:wafv2:us-east-1:123456789012:regional/webacl/igaming-security-regional-waf/abc123" \
  --resource-arn "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/my-alb/50dc6c495c0c9188" \
  --region us-east-1

# Verify association
aws wafv2 get-web-acl-for-resource \
  --resource-arn "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/my-alb/50dc6c495c0c9188"
```

### Associate with API Gateway

```bash
# Get the API Gateway stage ARN format
# arn:aws:apigateway:{region}::/restapis/{api-id}/stages/{stage-name}

aws wafv2 associate-web-acl \
  --web-acl-arn "arn:aws:wafv2:us-east-1:123456789012:regional/webacl/igaming-security-regional-waf/abc123" \
  --resource-arn "arn:aws:apigateway:us-east-1::/restapis/abc123def/stages/prod" \
  --region us-east-1
```

### Associate with CloudFront

```hcl
# In your CloudFront Terraform configuration
resource "aws_cloudfront_distribution" "main" {
  # ... other configuration ...

  web_acl_id = aws_wafv2_web_acl.cloudfront.arn

  # ... rest of configuration ...
}
```

### Associate with Network Load Balancer (NLB)

**Note:** AWS WAF does not directly support NLB. Use one of these approaches:

1. **ALB in front of NLB**: Route traffic through ALB with WAF, then to NLB
2. **AWS Global Accelerator**: Use GA with Shield Advanced
3. **CloudFront**: Use CloudFront with WAF in front of NLB

---

## Rate Limiting Configuration

### Default Rate Limits

| Endpoint Pattern | Limit | Window | Rationale |
|------------------|-------|--------|-----------|
| Global (all endpoints) | 2000 | 5 minutes | General DDoS protection |
| `/api/auth/login` | 100 | 5 minutes | Brute force prevention |
| `/api/auth/register` | 50 | 5 minutes | Account spam prevention |
| `/api/payment/*` | 50 | 5 minutes | Transaction fraud prevention |
| `/api/withdraw/*` | 50 | 5 minutes | Withdrawal fraud prevention |

### Adjusting Rate Limits

```hcl
# In terraform.tfvars
waf_rate_limit = 3000  # Increase for high-traffic periods

# Or create custom rule for specific endpoints
resource "aws_wafv2_web_acl" "custom" {
  rule {
    name     = "CustomEndpointRateLimit"
    priority = 15

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 500  # Custom limit
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = "/api/custom/endpoint"
            positional_constraint = "STARTS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }
  }
}
```

### Rate Limit by User (Forwarded Header)

```hcl
# Rate limit by authenticated user instead of IP
statement {
  rate_based_statement {
    limit              = 1000
    aggregate_key_type = "FORWARDED_IP"

    forwarded_ip_config {
      header_name       = "X-Forwarded-For"
      fallback_behavior = "MATCH"
    }
  }
}
```

---

## Debugging and Troubleshooting

### 1. Check WAF is Associated

```bash
# List all associations for a Web ACL
aws wafv2 list-resources-for-web-acl \
  --web-acl-arn "arn:aws:wafv2:us-east-1:123456789012:regional/webacl/igaming-security-regional-waf/abc123" \
  --resource-type APPLICATION_LOAD_BALANCER

# Expected output:
{
  "ResourceArns": [
    "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/my-alb/50dc6c495c0c9188"
  ]
}
```

### 2. Test WAF Rules Manually

```bash
# Test SQL injection detection
curl -X POST "https://api.example.com/api/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "1 OR 1=1"}' \
  -v

# Expected: HTTP 403 Forbidden

# Test rate limiting
for i in {1..150}; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    "https://api.example.com/api/auth/login" \
    -X POST -d '{"user":"test","pass":"test"}'
done | sort | uniq -c

# Expected: Initially 401 (auth failed), then 429 (rate limited)

# Test geo-blocking (from blocked country)
curl -X GET "https://api.example.com/api/health" \
  -H "X-Forwarded-For: 175.45.176.1" \
  -v

# Expected: HTTP 403 with geo-blocked message
```

### 3. View Real-time Metrics

```bash
# Get sampled requests (last hour)
aws wafv2 get-sampled-requests \
  --web-acl-arn "arn:aws:wafv2:us-east-1:123456789012:regional/webacl/igaming-security-regional-waf/abc123" \
  --rule-metric-name "RateLimitedRequests" \
  --scope REGIONAL \
  --time-window "StartTime=$(date -u -d '1 hour ago' +%s),EndTime=$(date -u +%s)" \
  --max-items 100
```

### 4. CloudWatch Metrics

```bash
# View blocked requests by rule
aws cloudwatch get-metric-statistics \
  --namespace "AWS/WAFV2" \
  --metric-name "BlockedRequests" \
  --dimensions Name=WebACL,Value=igaming-security-regional-waf Name=Region,Value=us-east-1 Name=Rule,Value=ALL \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 \
  --statistics Sum
```

### 5. Common Issues and Solutions

| Issue | Symptom | Solution |
|-------|---------|----------|
| WAF not blocking | Requests pass through | Verify association, check rule priority |
| Legitimate traffic blocked | 403/429 errors for users | Add IPs to whitelist, adjust rate limits |
| High latency | Slow response times | Reduce rule count, use COUNT before BLOCK |
| Missing logs | No entries in CloudWatch | Enable logging configuration |
| False positives | Good requests blocked | Review sampled requests, tune rules |

### 6. Enable Debug Logging

```hcl
# Full request logging (expensive, use for debugging only)
resource "aws_wafv2_web_acl_logging_configuration" "debug" {
  log_destination_configs = [aws_cloudwatch_log_group.waf_logs.arn]
  resource_arn            = aws_wafv2_web_acl.regional.arn

  # Log ALL requests (not just blocked)
  logging_filter {
    default_behavior = "KEEP"

    filter {
      behavior    = "KEEP"
      requirement = "MEETS_ALL"

      condition {
        action_condition {
          action = "ALLOW"
        }
      }
    }
  }
}
```

---

## Log Analysis

### CloudWatch Logs Insights Queries

```sql
-- Top 10 blocked IPs
fields @timestamp, httpRequest.clientIp, action
| filter action = "BLOCK"
| stats count(*) as blocked_count by httpRequest.clientIp
| sort blocked_count desc
| limit 10

-- Blocked requests by rule
fields @timestamp, terminatingRuleId, httpRequest.uri
| filter action = "BLOCK"
| stats count(*) as count by terminatingRuleId
| sort count desc

-- Rate limited requests timeline
fields @timestamp, httpRequest.clientIp, httpRequest.uri
| filter terminatingRuleId = "RateLimitPerIP"
| stats count(*) as rate_limited by bin(5m)

-- Suspicious patterns (potential attacks)
fields @timestamp, httpRequest.clientIp, httpRequest.uri, httpRequest.args
| filter action = "BLOCK" and terminatingRuleId = "AWSManagedRulesSQLiRuleSet"
| sort @timestamp desc
| limit 50

-- Geographic distribution of blocked requests
fields @timestamp, httpRequest.country
| filter action = "BLOCK"
| stats count(*) as blocked by httpRequest.country
| sort blocked desc
```

### Export Logs to S3

```hcl
resource "aws_wafv2_web_acl_logging_configuration" "s3_export" {
  log_destination_configs = [aws_kinesis_firehose_delivery_stream.waf_logs.arn]
  resource_arn            = aws_wafv2_web_acl.regional.arn
}

resource "aws_kinesis_firehose_delivery_stream" "waf_logs" {
  name        = "waf-logs-to-s3"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose.arn
    bucket_arn = aws_s3_bucket.security_logs.arn
    prefix     = "waf-logs/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"
  }
}
```

---

## Custom Rules Development

### Create Custom Rule for iGaming-Specific Patterns

```hcl
# Block bonus abuse patterns
rule {
  name     = "BlockBonusAbusePatterns"
  priority = 12

  action {
    block {
      custom_response {
        response_code = 403
        custom_response_body_key = "fraud-detected"
      }
    }
  }

  statement {
    or_statement {
      statement {
        byte_match_statement {
          search_string         = "bonus_abuse"
          positional_constraint = "CONTAINS"
          field_to_match {
            body {}
          }
          text_transformation {
            priority = 0
            type     = "LOWERCASE"
          }
        }
      }
      statement {
        byte_match_statement {
          search_string         = "multiple_accounts"
          positional_constraint = "CONTAINS"
          field_to_match {
            body {}
          }
          text_transformation {
            priority = 0
            type     = "LOWERCASE"
          }
        }
      }
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "BonusAbuseBlocked"
    sampled_requests_enabled   = true
  }
}
```

### Regex Pattern Rule

```hcl
# Block suspicious bet patterns
rule {
  name     = "BlockSuspiciousBetPatterns"
  priority = 13

  action {
    count {}  # Start with COUNT, move to BLOCK after tuning
  }

  statement {
    regex_pattern_set_reference_statement {
      arn = aws_wafv2_regex_pattern_set.suspicious_bets.arn
      field_to_match {
        body {}
      }
      text_transformation {
        priority = 0
        type     = "LOWERCASE"
      }
    }
  }
}

resource "aws_wafv2_regex_pattern_set" "suspicious_bets" {
  name        = "suspicious-bet-patterns"
  scope       = "REGIONAL"

  regular_expression {
    regex_string = "bet_amount.*[0-9]{6,}"  # Very large bets
  }

  regular_expression {
    regex_string = "consecutive_wins.*[0-9]{2,}"  # Many consecutive wins
  }
}
```

---

## Anti-DDoS Protection

### Layer 3/4 Protection (AWS Shield)

```hcl
# Enable Shield Advanced (requires subscription)
variable "enable_shield_advanced" {
  default = false
}

resource "aws_shield_protection" "alb" {
  count        = var.enable_shield_advanced ? 1 : 0
  name         = "alb-protection"
  resource_arn = aws_lb.main.arn
}

resource "aws_shield_protection_group" "all" {
  count               = var.enable_shield_advanced ? 1 : 0
  protection_group_id = "all-resources"
  aggregation         = "MAX"
  pattern             = "ALL"
}
```

### Layer 7 Protection (WAF Rate Limiting)

The WAF configuration includes multiple rate limiting rules:

1. **Global Rate Limit**: 2000 req/5min per IP
2. **Login Rate Limit**: 100 req/5min for authentication endpoints
3. **Payment Rate Limit**: 50 req/5min for financial transactions

### Emergency Response

```bash
# Block all traffic from specific IP immediately
aws wafv2 update-ip-set \
  --name "emergency-block-list" \
  --scope REGIONAL \
  --id "abc123" \
  --addresses "1.2.3.4/32" "5.6.7.8/32" \
  --lock-token "token123"

# Reduce rate limit during attack
terraform apply -var="waf_rate_limit=500"
```

---

## Python Security Modules

| Module | Description | Key Features |
|--------|-------------|--------------|
| `pentest_framework.py` | Penetration testing framework | OWASP Top 10, API security, business logic testing |
| `ids_ips.py` | Intrusion Detection/Prevention | Pattern matching, behavioral analysis, Redis storage |
| `network_monitor.py` | Network encryption monitor | TLS detection, compliance checking, reporting |
| `reporting.py` | Security report generator | Daily/weekly reports, compliance mapping, email delivery |
| `cis_scanner.py` | CIS Docker Benchmark scanner | 17 automated controls, remediation guidance |

---

## Compliance Mapping

| Framework | Requirements Covered | WAF Rules |
|-----------|---------------------|-----------|
| PCI DSS 6.5 | Protect against injection | SQLi, XSS rules |
| PCI DSS 6.6 | Application firewall | All WAF rules |
| PCI DSS 10.6 | Log monitoring | CloudWatch logging |
| GDPR Art. 32 | Security measures | Rate limiting, encryption |
| ISO 27001 A.13.1 | Network security | Geo-blocking, IP reputation |

---

## Cost Estimation

| Component | Unit Cost | Monthly Est. |
|-----------|-----------|--------------|
| WAF Web ACL | $5/month | $10 (2 ACLs) |
| WAF Rules | $1/rule/month | $16 (16 rules) |
| WAF Requests | $0.60/million | $60-300 |
| Managed Rules | $1-20/rule/month | $50-100 |
| Shield Advanced | $3,000/month | $3,000 (optional) |
| **Total (without Shield)** | | **$136-426** |
| **Total (with Shield)** | | **$3,136-3,426** |

---

## File Structure

```
scripts/chapter-24/
├── security-compliance/
│   ├── __init__.py           # Module exports
│   ├── pentest_framework.py  # Penetration testing (632 lines)
│   ├── ids_ips.py           # Intrusion detection (573 lines)
│   ├── network_monitor.py   # Encryption monitoring (482 lines)
│   ├── reporting.py         # Report generation (598 lines)
│   └── cis_scanner.py       # CIS Docker scanning (722 lines)
├── terraform/
│   ├── aws/
│   │   └── main.tf          # WAF, GuardDuty, Security Hub (1,401 lines)
│   └── kubernetes/
│       └── main.tf          # K8s security resources (1,177 lines)
├── docker/
│   ├── docker-compose.yml   # Full stack compose (292 lines)
│   ├── Dockerfile.ids       # IDS container (66 lines)
│   └── Dockerfile.monitor   # Monitor container (70 lines)
└── README.md                # This file
```

---

## Support

- **Documentation**: See individual module docstrings
- **AWS WAF Documentation**: https://docs.aws.amazon.com/waf/
- **Issues**: Report via GitHub Issues
- **Security Vulnerabilities**: security@company.com
