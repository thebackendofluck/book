# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# RDS PostgreSQL Database
resource "aws_db_instance" "main" {
  identifier = "${var.project_name}-${var.environment}-db"

  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  multi_az                = var.db_multi_az
  backup_retention_period = var.db_backup_retention_period

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [var.db_security_group_id]

  storage_encrypted = true
  kms_key_id        = var.kms_key_arn

  skip_final_snapshot             = false
  final_snapshot_identifier       = "${var.project_name}-${var.environment}-db-final-snapshot"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-database"
  })
}

# DB Subnet Group
resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-${var.environment}-db-subnet-group"
  subnet_ids = var.database_subnet_ids

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-db-subnet-group"
  })
}