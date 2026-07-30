# Chapter 38: Infrastructure as Code Examples

Sanitized Terraform and Ansible configurations from the on-premises to AWS cloud migration described in Chapter 38.

## Structure

```
infrastructure/
├── terraform/
│   ├── alb.tf          # Application Load Balancers (per-service isolation)
│   ├── asg.tf          # Auto Scaling Groups (elastic capacity)
│   ├── ecr.tf          # Container registry (cross-account image management)
│   ├── msk.tf          # Kafka clusters (event streaming for risk alerting)
│   ├── rds.tf          # PostgreSQL databases (primary + read replicas)
│   ├── iam.tf          # IAM policies (multi-account, least-privilege)
│   └── variables.tf    # Multi-environment configuration and account structure
└── ansible/
    ├── site.yml        # Main playbook (30+ roles for server configuration)
    └── roles/
        ├── instance-config/  # User, group, SSH key, and AWS CLI setup
        └── docker-config/    # Container runtime installation
```

## Sanitization

All files have been sanitized: AWS account IDs, domain names, IP addresses, SSH keys, and database credentials have been replaced with placeholder values. Resource patterns and architectural decisions are preserved exactly as used in production.
