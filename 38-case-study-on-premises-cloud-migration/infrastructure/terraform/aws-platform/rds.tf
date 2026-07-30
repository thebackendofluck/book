# Companion code for "The Backend of Luck" - Chapter 38, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# RDS PostgreSQL 16 — Casino Platform Database
# =============================================================================
# Regulatory context:
#   NJ DGE 13:69O-1.6  — All gaming data must be stored in encrypted databases
#                         with automated backups retained for 7 years.
#   PA PGCB §809a.9    — Player account data requires Multi-AZ redundancy.
#   PCI-DSS 3.4        — Render PAN (if stored) unreadable using encryption.
#   PCI-DSS 8.2.1      — Database access must use unique credentials.
#
# Backup strategy:
#   - RDS automated backups: 35 days (AWS maximum)
#   - AWS Backup: 7-year vault with compliance lock (NJ DGE requirement)
#   - Point-in-time recovery enabled for sub-second RPO
# =============================================================================

# --- DB Subnet Group ---------------------------------------------------------

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-${var.environment}-db-subnet"
  subnet_ids = aws_subnet.data[*].id

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-db-subnet"
    Compliance = "PCI-DSS-1.3.7"
  })
}

# --- Parameter Group (iGaming-optimized) -------------------------------------

resource "aws_db_parameter_group" "igaming" {
  name   = "${var.project_name}-${var.environment}-pg16-igaming"
  family = "postgres16"

  # Connection management — iGaming has bursty connection patterns
  parameter {
    name  = "max_connections"
    value = "200"
  }

  # WAL settings for durability — regulatory requirement for data integrity
  parameter {
    name  = "wal_level"
    value = "logical"
    # NJ DGE: Logical replication support for audit data extraction
  }

  # Query performance for game transaction lookups
  parameter {
    name  = "shared_buffers"
    value = "{DBInstanceClassMemory/4}"
    # 25% of instance memory — standard for transaction-heavy workloads
  }

  parameter {
    name  = "effective_cache_size"
    value = "{DBInstanceClassMemory*3/4}"
  }

  parameter {
    name  = "work_mem"
    value = "65536"
    # 64MB — sufficient for complex game analytics queries
  }

  # Logging — NJ DGE requires all DB queries to be auditable
  parameter {
    name  = "log_statement"
    value = "ddl"
    # Log DDL statements for schema change audit trail
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
    # Log queries taking > 1s for performance monitoring
  }

  parameter {
    name  = "log_connections"
    value = "1"
    # PCI-DSS 10.2.4: Log all access to database
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
  }

  # SSL enforcement
  parameter {
    name  = "rds.force_ssl"
    value = "1"
    # PCI-DSS 4.1: Encrypt data in transit
  }

  # Timezone for consistent game round timestamps
  parameter {
    name  = "timezone"
    value = "UTC"
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-pg16-igaming"
    Compliance = "NJ-DGE-13:69O-1.6,PCI-DSS-3.4"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# --- RDS Instance ------------------------------------------------------------

resource "aws_db_instance" "main" {
  identifier = "${var.project_name}-${var.environment}-postgres"

  # Engine
  engine               = "postgres"
  engine_version       = "16.4"
  instance_class       = var.db_instance_class
  parameter_group_name = aws_db_parameter_group.igaming.name

  # Storage
  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true
  # NJ DGE 13:69O-1.6: Encryption at rest mandatory for gaming data

  # Database
  db_name  = var.db_name
  username = var.db_username
  password = random_password.db_password.result
  port     = 5432

  # Network
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  publicly_accessible    = false
  # PCI-DSS 1.3.7: Database must not be publicly accessible

  # High Availability
  multi_az = true
  # NJ DGE 13:69O-1.3: Multi-AZ required for gaming data
  # PA PGCB §809a.9: Redundancy required for player account data

  # Backups
  backup_retention_period  = var.db_backup_retention_days
  backup_window            = "03:00-04:00"
  maintenance_window       = "Mon:04:00-Mon:05:00"
  copy_tags_to_snapshot    = true
  delete_automated_backups = false
  # NJ DGE: Automated backups retained; 7-year handled by AWS Backup below

  # Monitoring
  monitoring_interval                   = 60
  monitoring_role_arn                   = aws_iam_role.rds_monitoring.arn
  performance_insights_enabled          = true
  performance_insights_retention_period = 731 # 2 years (maximum free tier)
  enabled_cloudwatch_logs_exports       = ["postgresql", "upgrade"]
  # PCI-DSS 10.1: Detailed monitoring and log export

  # Protection
  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.project_name}-${var.environment}-final-${formatdate("YYYYMMDD", timestamp())}"

  # Auto minor version upgrades for security patches
  auto_minor_version_upgrade = true

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-postgres"
    Compliance = "NJ-DGE-13:69O-1.6,PA-PGCB-809a.9,PCI-DSS-3.4"
    DataClass  = "confidential"
  })

  lifecycle {
    ignore_changes = [password, final_snapshot_identifier]
  }
}

# --- AWS Backup for 7-Year Retention ----------------------------------------
# NJ DGE 13:69O-1.6: Gaming data must be retained for minimum 7 years.
# RDS automated backups max out at 35 days, so we use AWS Backup for long-term.

resource "aws_backup_vault" "main" {
  name = "${var.project_name}-${var.environment}-vault"

  tags = merge(var.tags, {
    Compliance = "NJ-DGE-7yr-retention"
  })
}

resource "aws_backup_plan" "rds_7yr" {
  name = "${var.project_name}-${var.environment}-rds-7yr"

  # Daily backup retained for 7 years
  rule {
    rule_name         = "daily-7yr-retention"
    target_vault_name = aws_backup_vault.main.name
    schedule          = "cron(0 5 * * ? *)"

    lifecycle {
      cold_storage_after = 30
      delete_after       = var.db_backup_7yr_retention_days
    }

    copy_action {
      destination_vault_arn = aws_backup_vault.main.arn
      lifecycle {
        delete_after = var.db_backup_7yr_retention_days
      }
    }
  }

  # Monthly backup for compliance audits
  rule {
    rule_name         = "monthly-compliance"
    target_vault_name = aws_backup_vault.main.name
    schedule          = "cron(0 5 1 * ? *)"

    lifecycle {
      cold_storage_after = 30
      delete_after       = var.db_backup_7yr_retention_days
    }
  }

  tags = merge(var.tags, {
    Compliance = "NJ-DGE-7yr-retention"
  })
}

resource "aws_backup_selection" "rds" {
  name         = "${var.project_name}-${var.environment}-rds-selection"
  plan_id      = aws_backup_plan.rds_7yr.id
  iam_role_arn = aws_iam_role.backup.arn

  resources = [aws_db_instance.main.arn]
}

# --- IAM Role for RDS Enhanced Monitoring ------------------------------------

resource "aws_iam_role" "rds_monitoring" {
  name = "${var.project_name}-${var.environment}-rds-monitoring"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "monitoring.rds.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# --- IAM Role for AWS Backup ------------------------------------------------

resource "aws_iam_role" "backup" {
  name = "${var.project_name}-${var.environment}-backup-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "backup.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "backup" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_iam_role_policy_attachment" "backup_restore" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores"
}

# --- Random Password ---------------------------------------------------------

resource "random_password" "db_password" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
  # PCI-DSS 8.2.3: Passwords must contain complexity requirements
}
