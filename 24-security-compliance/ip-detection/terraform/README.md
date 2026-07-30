# IP Detection & DDoS Protection — Terraform

Modular Terraform configuration that provisions the complete Chapter 24 IP
detection and DDoS protection infrastructure across all three platforms:

| Platform    | Resources                                          |
|-------------|---------------------------------------------------|
| Cloudflare  | 8 KV namespaces, 2 Workers, WAF, firewall rules   |
| AWS         | 5 DynamoDB tables, 5 Lambdas, API GW, WAF, SNS    |
| On-premises | FastAPI service, systemd units, Redis, cron       |

## Prerequisites

| Tool         | Minimum version | Purpose                         |
|-------------|-----------------|----------------------------------|
| Terraform   | 1.5.0           | Infrastructure provisioning      |
| AWS CLI     | 2.x             | Credential configuration         |
| wrangler    | 3.x             | Build CF Worker bundles          |
| Node.js     | 18.x            | Worker TypeScript compilation    |
| SSH access  | —               | On-premises provisioning         |

### AWS credentials

```bash
aws configure --profile igaming-prod
export AWS_PROFILE=igaming-prod
```

### Cloudflare credentials

```bash
export CLOUDFLARE_API_TOKEN="your-api-token-here"
```

The token requires the following permissions:
- Workers Scripts: Edit
- Workers KV Storage: Edit
- Zone WAF: Edit
- Zone Settings: Edit
- DNS: Edit

### Build Worker bundles before apply

The Cloudflare module reads compiled Worker JS from disk. Build them first:

```bash
cd ../cloudflare
npm install
npm run build
# Outputs: dist/ip-detection-worker.js and dist/edge-classifier.js
cd ../terraform
```

## Quickstart

```bash
# 1. Copy and edit the example vars file.
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your account IDs, domain, SSH host, etc.

# 2. Select or create a workspace (drives the environment suffix).
terraform workspace new production
# or
terraform workspace select production

# 3. Initialise providers and modules.
terraform init

# 4. Preview changes.
terraform plan -var-file=terraform.tfvars

# 5. Apply.
terraform apply -var-file=terraform.tfvars
```

Terraform will provision resources in this order:
1. AWS resources (DynamoDB, S3, IAM, Lambda, API GW, WAF, SNS, EventBridge)
2. Cloudflare resources (KV namespaces, Workers, WAF rulesets, firewall rules)
3. On-premises provisioning (via SSH null_resource provisioners)

## Workspace-based environments

Workspaces drive the name suffix applied to all resources:

| Workspace   | Suffix      | Example table name          |
|-------------|------------|------------------------------|
| production  | (none)      | ip-blacklist                |
| staging     | -staging    | ip-blacklist-staging        |
| dev         | -dev        | ip-blacklist-dev            |

```bash
# Create the staging environment.
terraform workspace new staging
terraform apply -var-file=terraform.tfvars -var 'environment=staging'
```

## Post-apply steps

### 1. Confirm SNS email subscription

After the first apply, check your inbox for the SNS confirmation email and
click the confirmation link. Alerts will not be delivered until confirmed.

### 2. Upload real API keys to SSM

The initial apply writes placeholder values to SSM. Replace them:

```bash
# MaxMind license key
aws ssm put-parameter \
  --name "/igaming/ip-detection/maxmind-license-key" \
  --type SecureString \
  --value "YOUR_MAXMIND_KEY" \
  --overwrite

# IP reputation (IPQualityScore) API key
aws ssm put-parameter \
  --name "/igaming/ip-detection/ip-reputation-api-key" \
  --type SecureString \
  --value "YOUR_IPQS_KEY" \
  --overwrite
```

### 3. Deploy real Lambda code

The Terraform config manages Lambda configuration and IAM; the actual code is
deployed by CI/CD. To deploy manually:

```bash
# Build the deployment package
cd ../aws
pip install -r requirements.txt -t ./package
cd package && zip -r ../deployment.zip . && cd ..
zip -g deployment.zip *.py

# Update the function code
aws lambda update-function-code \
  --function-name igaming-ip-gate \
  --zip-file fileb://deployment.zip
```

### 4. Run the first threat list refresh

Trigger the SDN refresh Lambda immediately after deployment:

```bash
aws lambda invoke \
  --function-name igaming-sdn-refresh \
  --payload '{"source": "manual"}' \
  response.json
cat response.json
```

### 5. Associate WAF with your ALB (optional)

The WAF WebACL is created for the API Gateway stage automatically. To
associate it with an existing ALB:

```bash
WAF_ARN=$(terraform output -raw waf_webacl_arn)
ALB_ARN="arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/..."

aws wafv2 associate-web-acl \
  --web-acl-arn "$WAF_ARN" \
  --resource-arn "$ALB_ARN"
```

## Customising the threat list schedule

The `threat_list_schedule` variable accepts a standard 5-field cron
expression (minute, hour, day-of-month, month, day-of-week).

| Frequency      | Value              |
|---------------|---------------------|
| 3x daily      | `0 0,8,16 * * *`   |
| Every 6 hours | `0 0,6,12,18 * * *`|
| Hourly        | `0 * * * *`        |
| Daily at 02:00| `0 2 * * *`        |

```bash
terraform apply -var 'threat_list_schedule=0 0,6,12,18 * * *'
```

## Adding a marketing campaign

Insert a new campaign entry into the MarketingCalendar DynamoDB table. The
campaign autoscaler Lambda scans the table hourly and scales capacity
according to the matching `scale_profile`.

```bash
aws dynamodb put-item \
  --table-name marketing-calendar \
  --item '{
    "campaign_id":  {"S": "black-friday-2026"},
    "start_time":   {"N": "1764537600"},
    "end_time":     {"N": "1764624000"},
    "status":       {"S": "SCHEDULED"},
    "scale_profile":{"S": "black_friday"},
    "description":  {"S": "Black Friday 2026 — 2x traffic expected"}
  }'
```

Available scale profiles (customisable via `scale_profiles` variable):
- `normal` — baseline capacity
- `campaign_small` — small promotion
- `campaign_large` — major campaign
- `black_friday` — peak traffic event

## Triggering a manual SDN sync

```bash
# Via AWS CLI
aws lambda invoke \
  --function-name igaming-sdn-refresh \
  --payload '{"source": "manual", "force": true}' \
  /dev/stdout

# On-premises: run directly
ssh deploy@YOUR_HOST \
  "/opt/ip-detection/venv/bin/python3 \
   /opt/ip-detection/threat-lists/consolidate_threat_lists.py"
```

## Activating DDoS emergency mode

During a confirmed L7 DDoS attack, enable cache-everything mode on
Cloudflare to serve cached responses from the edge and drop origin load to
near zero:

```bash
# Enable the cache-everything page rule
terraform apply \
  -var-file=terraform.tfvars \
  -target=module.cloudflare.cloudflare_page_rule.ddos_cache_everything
```

Set the rule status to `"active"` in `modules/cloudflare/main.tf` and apply.
Revert by setting it back to `"disabled"` and applying again.

## Outputs reference

After a successful apply, retrieve key outputs:

```bash
# API Gateway endpoint
terraform output api_gateway_url

# WAF WebACL ARN (for ALB association)
terraform output waf_webacl_arn

# All Cloudflare KV namespace IDs (paste into wrangler.toml)
terraform output cf_kv_ip_blacklist_id
terraform output cf_kv_device_fingerprints_id
terraform output cf_kv_fraud_velocity_id
terraform output cf_kv_sanctions_list_id
terraform output cf_kv_rate_limits_id
terraform output cf_kv_campaigns_id
terraform output cf_kv_attack_log_id
terraform output cf_kv_ja3_blocklist_id

# DynamoDB table names
terraform output dynamodb_ip_blacklist_table
terraform output dynamodb_device_fingerprints_table

# On-premises health check command
terraform output onpremise_service_status_command
```

## Destroying resources

```bash
# Destroy all resources in the current workspace.
terraform destroy -var-file=terraform.tfvars

# Destroy only one platform (e.g. on-premises module).
terraform destroy -var-file=terraform.tfvars -target=module.onpremise
```

Note: S3 buckets with content must be emptied before Terraform can delete
them. The attack-evidence bucket may contain compliance data — confirm
retention requirements before destroying.

## Troubleshooting

**Lambda stub deployed instead of real code**
The Terraform config deploys a placeholder zip at initial apply. Deploy real
code via CI/CD or manually (see Post-apply steps above).

**Cloudflare Worker not routing**
Verify the Worker route pattern matches your domain exactly. The `/*` suffix
is required to match all paths. Check the Cloudflare dashboard under
Workers & Pages → your worker → Routes.

**On-premises provisioning fails with SSH timeout**
- Confirm `onpremise_ssh_key_path` points to the correct private key.
- Ensure the deploy user has sudo access for systemd and /etc writes.
- Check that the SSH port is open in the server's firewall.

**WAF not blocking expected traffic**
The Anonymous IP managed rule group is set to `count` mode by default so
you can observe traffic before enforcing. Switch it to `none` (enforce) in
`modules/aws/main.tf` after reviewing the sampled requests.

**ElastiCache provisioning fails**
The module uses the default VPC and subnets. If your region has no default
VPC, create one with `aws ec2 create-default-vpc` or set
`elasticache_enabled = false` and use the on-premises Redis instead.
