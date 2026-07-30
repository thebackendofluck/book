# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

locals {
  base_tags = merge(
    {
      Name        = var.name
      Module      = "rds-postgres-aegis"
      Environment = var.environment
      Encryption  = "aegis-layered"
    },
    var.tags,
  )

  # force_ssl is honored only when the caller opts in. On LocalStack the value is
  # accepted but NOT enforced — see Appendix H.
  base_parameters = concat(
    var.force_ssl ? [{
      name         = "rds.force_ssl"
      value        = "1"
      apply_method = "pending-reboot"
    }] : [],
    [
      {
        name         = "log_connections"
        value        = "1"
        apply_method = "pending-reboot"
      },
      {
        name         = "log_disconnections"
        value        = "1"
        apply_method = "pending-reboot"
      },
      {
        name         = "log_statement"
        value        = "ddl"
        apply_method = "pending-reboot"
      },
      {
        name         = "shared_preload_libraries"
        value        = "pg_stat_statements,pgaudit"
        apply_method = "pending-reboot"
      },
    ],
    var.extra_parameters,
  )
}

resource "random_password" "master" {
  length  = 32
  special = true
  # RDS rejects some ASCII symbols in master passwords.
  override_special = "_%@!#"
}

resource "aws_kms_key" "this" {
  count = var.kms_key_arn == null ? 1 : 0

  description             = "CMK for ${var.name} RDS storage + exports"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = local.base_tags
}

resource "aws_kms_alias" "this" {
  count = var.kms_key_arn == null ? 1 : 0

  name          = "alias/${var.name}-rds"
  target_key_id = aws_kms_key.this[0].key_id
}

locals {
  kms_key_arn = var.kms_key_arn != null ? var.kms_key_arn : aws_kms_key.this[0].arn
}

resource "aws_db_subnet_group" "this" {
  name        = "${var.name}-subnets"
  description = "Subnets for ${var.name} RDS PostgreSQL Aegis"
  subnet_ids  = var.db_subnet_ids
  tags        = local.base_tags
}

resource "aws_security_group" "this" {
  name        = "${var.name}-rds"
  description = "RDS PostgreSQL ${var.name} — only TLS clients on 5432"
  vpc_id      = var.vpc_id
  tags        = local.base_tags
}

resource "aws_vpc_security_group_ingress_rule" "db_cidrs" {
  for_each = toset(var.allowed_cidr_blocks)

  security_group_id = aws_security_group.this.id
  cidr_ipv4         = each.value
  from_port         = 5432
  to_port           = 5432
  ip_protocol       = "tcp"
  description       = "PostgreSQL TLS from ${each.value}"
}

resource "aws_vpc_security_group_egress_rule" "db_all" {
  security_group_id = aws_security_group.this.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "Allow all egress (KMS, backups, S3 endpoint)."
}

resource "aws_db_parameter_group" "this" {
  name        = "${var.name}-pg16-aegis"
  family      = "postgres16"
  description = "Aegis parameters: force SSL, audit, extensions"

  dynamic "parameter" {
    for_each = local.base_parameters
    content {
      name         = parameter.value.name
      value        = parameter.value.value
      apply_method = parameter.value.apply_method
    }
  }

  tags = local.base_tags
}

resource "aws_db_instance" "writer" {
  identifier = "${var.name}-writer"

  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = var.allocated_storage_gb
  storage_type      = var.storage_type
  storage_encrypted = true
  kms_key_id        = local.kms_key_arn

  username = "aegis_admin"
  password = random_password.master.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  parameter_group_name   = aws_db_parameter_group.this.name

  iam_database_authentication_enabled = var.enable_iam_auth

  backup_retention_period    = var.backup_retention_days
  backup_window              = "02:00-03:00"
  maintenance_window         = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot      = true
  final_snapshot_identifier  = "${var.name}-final-${replace(timestamp(), ":", "-")}"
  skip_final_snapshot        = var.skip_final_snapshot
  deletion_protection        = var.deletion_protection
  delete_automated_backups   = false
  auto_minor_version_upgrade = true

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  performance_insights_enabled    = true
  performance_insights_kms_key_id = local.kms_key_arn
  monitoring_interval             = 60

  tags = merge(local.base_tags, { Role = "writer" })

  lifecycle {
    ignore_changes = [
      password,
      final_snapshot_identifier,
    ]
  }
}

resource "aws_db_instance" "reader" {
  count = var.reader_count

  identifier          = "${var.name}-reader-${count.index + 1}"
  replicate_source_db = aws_db_instance.writer.identifier

  instance_class = var.reader_instance_class

  # Replicas inherit storage encryption + KMS key from the source; we pass
  # the key explicitly so LocalStack records it even though it's a noop there.
  storage_encrypted = true
  kms_key_id        = local.kms_key_arn

  iam_database_authentication_enabled = var.enable_iam_auth

  # Replicas live in the same SG as the writer.
  vpc_security_group_ids = [aws_security_group.this.id]
  parameter_group_name   = aws_db_parameter_group.this.name

  skip_final_snapshot = true
  publicly_accessible = false

  performance_insights_enabled    = true
  performance_insights_kms_key_id = local.kms_key_arn
  monitoring_interval             = 60

  auto_minor_version_upgrade = true

  tags = merge(local.base_tags, {
    Role       = "reader"
    ReplicaIdx = tostring(count.index + 1)
  })
}
