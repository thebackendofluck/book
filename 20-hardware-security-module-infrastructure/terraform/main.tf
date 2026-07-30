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

  backend "s3" {
    # Configure via terraform.tfvars or CLI
    # bucket = "your-terraform-state-bucket"
    # key    = "yubihsm-infrastructure/terraform.tfstate"
    # region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project       = var.project_name
      Environment   = var.environment
      ManagedBy     = "Terraform"
      SecurityLevel = "High"
    }
  }
}

# Data sources
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Local values
locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    Owner       = var.owner
    CostCenter  = var.cost_center
  }

  name_prefix = "${var.project_name}-${var.environment}"
}

# Modules
module "networking" {
  source = "./modules/networking"

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region

  vpc_cidr_block     = var.vpc_cidr_block
  availability_zones = var.availability_zones

  public_subnet_cidrs   = var.public_subnet_cidrs
  private_subnet_cidrs  = var.private_subnet_cidrs
  database_subnet_cidrs = var.database_subnet_cidrs

  enable_nat_gateway = var.enable_nat_gateway
  single_nat_gateway = var.single_nat_gateway

  tags = local.common_tags
}

module "security" {
  source = "./modules/security"

  project_name = var.project_name
  environment  = var.environment

  vpc_id             = module.networking.vpc_id
  private_subnet_ids = module.networking.private_subnet_ids

  yubihsm_auth_key_id = var.yubihsm_auth_key_id
  yubihsm_password    = var.yubihsm_password

  enable_key_rotation = var.enable_key_rotation
  key_rotation_days   = var.key_rotation_days

  tags = local.common_tags
}

module "compute" {
  source = "./modules/compute"

  project_name = var.project_name
  environment  = var.environment

  vpc_id             = module.networking.vpc_id
  private_subnet_ids = module.networking.private_subnet_ids
  public_subnet_ids  = module.networking.public_subnet_ids

  app_server_instance_type    = var.app_server_instance_type
  nitro_enclave_instance_type = var.nitro_enclave_instance_type

  app_server_ami_id    = var.app_server_ami_id
  nitro_enclave_ami_id = var.nitro_enclave_ami_id

  key_pair_name = var.key_pair_name

  yubihsm_security_group_id = module.security.yubihsm_security_group_id
  app_security_group_id     = module.security.app_security_group_id

  kms_key_arn = module.security.kms_key_arn

  user_data_scripts = {
    app_server    = file("${path.module}/scripts/app_server_init.sh")
    nitro_enclave = file("${path.module}/scripts/nitro_enclave_init.sh")
  }

  tags = local.common_tags
}

module "storage" {
  source = "./modules/storage"

  project_name = var.project_name
  environment  = var.environment

  vpc_id             = module.networking.vpc_id
  private_subnet_ids = module.networking.private_subnet_ids

  kms_key_arn = module.security.kms_key_arn

  ebs_volume_size = var.ebs_volume_size
  ebs_encrypted   = var.ebs_encrypted

  enable_efs    = var.enable_efs
  efs_encrypted = var.enable_efs ? var.efs_encrypted : false

  s3_bucket_name = var.s3_bucket_name
  s3_versioning  = var.s3_versioning

  tags = local.common_tags
}

module "database" {
  source = "./modules/database"

  project_name = var.project_name
  environment  = var.environment

  vpc_id              = module.networking.vpc_id
  database_subnet_ids = module.networking.database_subnet_ids

  db_instance_class = var.db_instance_class
  db_engine_version = var.db_engine_version

  db_allocated_storage     = var.db_allocated_storage
  db_max_allocated_storage = var.db_max_allocated_storage

  db_name     = var.db_name
  db_username = var.db_username
  db_password = var.db_password

  db_multi_az                = var.db_multi_az
  db_backup_retention_period = var.db_backup_retention_period

  kms_key_arn = module.security.kms_key_arn

  db_security_group_id = module.security.database_security_group_id

  enable_tde = var.enable_tde

  tags = local.common_tags
}

module "containers" {
  source = "./modules/containers"

  project_name = var.project_name
  environment  = var.environment

  vpc_id             = module.networking.vpc_id
  private_subnet_ids = module.networking.private_subnet_ids

  ecs_cluster_name = var.ecs_cluster_name

  vaultwarden_image  = var.vaultwarden_image
  vaultwarden_cpu    = var.vaultwarden_cpu
  vaultwarden_memory = var.vaultwarden_memory

  yubihsm_connector_image  = var.yubihsm_connector_image
  yubihsm_connector_cpu    = var.yubihsm_connector_cpu
  yubihsm_connector_memory = var.yubihsm_connector_memory

  container_security_group_id = module.security.container_security_group_id

  kms_key_arn = module.security.kms_key_arn

  tags = local.common_tags
}

# CloudWatch Alarms
resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "${local.name_prefix}-high-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = "300"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "This metric monitors ec2 cpu utilization"
  alarm_actions       = []

  dimensions = {
    InstanceId = module.compute.app_server_instance_id
  }

  tags = local.common_tags
}

# Route 53 Health Checks (if domain is provided)
resource "aws_route53_health_check" "vaultwarden" {
  count = var.vaultwarden_domain != "" ? 1 : 0

  fqdn              = var.vaultwarden_domain
  port              = 443
  type              = "HTTPS"
  resource_path     = "/api/status"
  failure_threshold = "3"
  request_interval  = "30"

  tags = local.common_tags
}

# SNS Topic for alerts
resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"

  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}