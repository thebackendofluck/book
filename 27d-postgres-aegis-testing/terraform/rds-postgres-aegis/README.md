# `rds-postgres-aegis` Terraform module

Provisions an AWS RDS-for-PostgreSQL writer + N read replicas with:

- Customer Managed Key (created if `kms_key_arn` is null) with automatic rotation.
- `rds.force_ssl=1` on the parameter group (TLS-only).
- `pg_stat_statements` + `pgaudit` preloaded.
- IAM database authentication optional.
- Performance Insights + CloudWatch logs exported (`postgresql`, `upgrade`).
- Security group with deny-by-default ingress; caller supplies allowed CIDRs.

## Honesty note on LocalStack

On LocalStack Pro at `http://lab-server:4567` the apply succeeds and the resources
appear in state, but:

- `storage_encrypted = true` is a **flag**, not real encryption (KMS emulated).
- `replicate_source_db` creates the replica as a **CRUD record**; it does not replicate data.
- `iam_database_authentication_enabled = true` is accepted but not enforced.
- `performance_insights_enabled` returns empty dashboards.

See `../../../../../appendices/Appendix_H_LocalStack_vs_AWS_Gotchas.md`
for the full table. Do not use LocalStack to "prove" encryption.

## Usage (LocalStack)

```hcl
module "casino_aegis_localstack" {
  source = "./modules/rds-postgres-aegis"

  name          = "casino-aegis"
  environment   = "localstack"
  db_subnet_ids = [aws_subnet.a.id, aws_subnet.b.id]
  vpc_id        = aws_vpc.main.id

  reader_count          = 5           # real AWS: 10 hits RDS per-source limit of 15
  allowed_cidr_blocks   = ["10.0.0.0/8"]
  skip_final_snapshot   = true
  deletion_protection   = false
}
```

## Usage (AWS real)

```hcl
module "casino_aegis_prod" {
  source = "./modules/rds-postgres-aegis"

  name          = "casino-aegis"
  environment   = "prod"
  db_subnet_ids = module.vpc.database_subnets
  vpc_id        = module.vpc.vpc_id

  reader_count          = 10
  instance_class        = "db.m6g.2xlarge"
  reader_instance_class = "db.m6g.xlarge"
  allowed_cidr_blocks   = ["10.0.0.0/8"]
  backup_retention_days = 30
}
```

## Inputs / Outputs

See `variables.tf` and `outputs.tf` for the complete contract.

## Validation

```bash
terraform -chdir=modules/rds-postgres-aegis init -backend=false
terraform -chdir=modules/rds-postgres-aegis validate
```
