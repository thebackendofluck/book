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
# RDS PostgreSQL -- Managed Databases
# =============================================================================
# CONTEXT: On-premises, all services shared two PostgreSQL servers. A bad query
# from the BI team could slow player-facing operations. After migration, each
# service gets its own RDS instance, and the main player database has dedicated
# read replicas for reporting -- no more contention.
#
# Key improvements over on-premises:
#   - multi_az = true gives automatic failover (was manual scripts before)
#   - Performance Insights with 731-day retention for long-term query analysis
#   - 35-day backup retention (was 7 days on-prem at best)
#   - Read replicas for BI/reporting workloads
#   - deletion_protection prevents accidental destruction
# =============================================================================

resource "aws_db_subnet_group" "prod_subnet" {
  name        = "prod-subnet"
  subnet_ids  = var.default_subnets.*
  description = "Production Subnets"
}

# --- Main Player Database -----------------------------------------------------
# This is the largest and most critical database: 40TB allocated, provisioned
# IOPS storage for consistent performance, Multi-AZ for automatic failover.

module "player_database_prod" {
  source  = "terraform-aws-modules/rds/aws"
  version = "3.3.0"

  identifier = "player-db-prod"

  engine            = "postgres"
  engine_version    = "11.6"
  instance_class    = "db.m5.12xlarge"
  allocated_storage = 40000
  storage_encrypted = false
  storage_type      = "io1"
  iops              = 40000

  max_allocated_storage = 65536

  name = "player_db"
  port = "5432"

  # Credentials managed through AWS Secrets Manager -- never in Terraform state
  username = var.db_admin_username
  password = var.db_admin_password

  vpc_security_group_ids = [aws_security_group.rds_player_db.id]

  maintenance_window = "Mon:00:00-Mon:03:00"
  backup_window      = "03:00-04:00"

  tags = {
    Environment = "prod"
    Terraformed = "true"
  }

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  # DB subnet group
  subnet_ids = var.default_subnets

  # DB parameter group
  family = "postgres11"

  # DB option group
  major_engine_version = "11.4"

  # Snapshot on deletion
  final_snapshot_identifier = "player-db-prod"

  # Database Deletion Protection
  deletion_protection = true

  create_db_option_group    = true
  create_db_parameter_group = true

  performance_insights_enabled          = true
  performance_insights_retention_period = 731 # 2 years of query data

  multi_az                   = true
  backup_retention_period    = 35
  auto_minor_version_upgrade = false
  copy_tags_to_snapshot      = true
  monitoring_interval        = 60
}

# --- Read Replica for Reporting -----------------------------------------------
# BI team runs heavy analytical queries here without affecting production.
# On-prem, reporting queries competed with player-facing operations.

module "player_database_read_replica" {
  source  = "terraform-aws-modules/rds/aws"
  version = "3.3.0"

  identifier = "player-db-readreplica"

  replicate_source_db   = module.player_database_prod.db_instance_id
  instance_class        = "db.m5.12xlarge"
  allocated_storage     = 40000
  storage_type          = "gp2"
  max_allocated_storage = 65536

  password = ""
  port     = 5432

  vpc_security_group_ids = [aws_security_group.rds_player_db.id]

  maintenance_window = "Tue:00:00-Tue:03:00"
  backup_window      = "03:00-06:00"

  backup_retention_period    = 0
  auto_minor_version_upgrade = false
  create_db_subnet_group     = false
  create_db_option_group     = false
  create_db_parameter_group  = false

  timeouts = {
    create = "2h"
  }
}

# --- Read Replica for BI (with tuned parameters) ------------------------------
# Separate replica with relaxed standby delay settings so long-running
# analytical queries don't get cancelled by replication conflicts.

module "player_database_read_replica_bi" {
  source  = "terraform-aws-modules/rds/aws"
  version = "3.3.0"

  identifier = "player-db-readreplica-bi"

  replicate_source_db   = module.player_database_prod.db_instance_id
  instance_class        = "db.m5.2xlarge"
  allocated_storage     = 40000
  storage_type          = "gp2"
  max_allocated_storage = 65536

  password = ""
  port     = 5432

  vpc_security_group_ids = [aws_security_group.rds_player_db.id]

  family = "postgres11"

  maintenance_window = "Tue:00:00-Tue:03:00"
  backup_window      = "03:00-06:00"

  backup_retention_period    = 0
  auto_minor_version_upgrade = false
  create_db_subnet_group     = false
  create_db_option_group     = true
  create_db_parameter_group  = true

  timeouts = {
    create = "2h"
  }

  # Tuned for long-running analytical queries
  parameters = [
    {
      name  = "checkpoint_timeout"
      value = 1800
    },
    {
      name  = "max_wal_size"
      value = 20480
    },
    {
      name  = "session_replication_role"
      value = "origin"
    },
    {
      name  = "synchronous_commit"
      value = "off"
    },
    {
      name  = "max_standby_archive_delay"
      value = 10800000 # 3 hours -- lets BI queries run without cancellation
    },
    {
      name  = "max_standby_streaming_delay"
      value = 10800000
    }
  ]
}

# --- Per-Service Databases ----------------------------------------------------
# Each microservice gets its own database so one service's bad query
# can't take down the entire platform.

module "game_service_db" {
  source  = "terraform-aws-modules/rds/aws"
  version = "3.3.0"

  identifier = "game-service-prod"

  engine            = "postgres"
  engine_version    = "12.7"
  instance_class    = "db.m5.xlarge"
  allocated_storage = 200
  storage_encrypted = false
  multi_az          = true

  name     = "gameservice_db"
  username = var.db_admin_username
  password = var.db_admin_password
  port     = "5432"

  vpc_security_group_ids = [aws_security_group.rds_player_db.id]

  maintenance_window         = "Mon:00:00-Mon:03:00"
  backup_window              = "03:00-06:00"
  backup_retention_period    = 2
  auto_minor_version_upgrade = false

  tags = {
    Environment = var.env
    Terraformed = "true"
  }

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  family               = "postgres12"
  major_engine_version = "12.5"
  deletion_protection  = true

  create_db_option_group    = false
  create_db_parameter_group = true

  performance_insights_enabled          = true
  performance_insights_retention_period = 7

  db_subnet_group_name  = "prod-subnet"
  copy_tags_to_snapshot = true
  monitoring_interval   = 60
  monitoring_role_arn   = aws_iam_role.rds_enhanced_monitoring.arn
}

# --- Real-Time Analytics Database ---------------------------------------------
module "realtime_analytics_db" {
  source  = "terraform-aws-modules/rds/aws"
  version = "3.3.0"

  identifier = "realtime-analytics-prod"

  engine            = "postgres"
  engine_version    = "11.12"
  instance_class    = "db.m5.large"
  allocated_storage = 50
  storage_encrypted = true
  multi_az          = true

  name     = "realtime_analytics"
  username = var.db_admin_username
  password = var.db_admin_password
  port     = "5432"

  vpc_security_group_ids = [aws_security_group.rds_player_db.id]

  apply_immediately  = true
  maintenance_window = "wed:06:00-wed:07:00"
  backup_window      = "03:00-06:00"

  backup_retention_period    = 1
  auto_minor_version_upgrade = false

  tags = {
    Environment = "prod"
    Terraformed = "true"
  }

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  subnet_ids             = var.default_subnets
  create_db_subnet_group = true

  family               = "postgres11"
  major_engine_version = "11.4"

  final_snapshot_identifier = "realtime-analytics-prod"
  deletion_protection       = true

  create_db_option_group    = true
  create_db_parameter_group = true

  performance_insights_enabled          = true
  performance_insights_retention_period = 7

  parameters = [
    {
      name  = "checkpoint_timeout"
      value = 1800
    },
    {
      name  = "max_wal_size"
      value = 20480
    },
    {
      name  = "session_replication_role"
      value = "origin"
    },
    {
      name  = "synchronous_commit"
      value = "off"
    }
  ]
}
