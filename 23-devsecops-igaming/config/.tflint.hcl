# TFLint Configuration for DevSecOps Standard
# Terraform linting and security validation

# Plugin Configuration for Multiple Cloud Providers
plugin "aws" {
  enabled = true
  version = "0.30.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}

plugin "azurerm" {
  enabled = true
  version = "0.25.1"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}

plugin "google" {
  enabled = true
  version = "0.27.1"
  source  = "github.com/terraform-linters/tflint-ruleset-google"
}

# Core Configuration
config {
  # Enable module inspection
  module = true

  # Force initialization
  force = false

  # Disable color output
  disabled_by_default = false

  # Plugin directory
  plugin_dir = "~/.tflint.d/plugins"

  # Maximum number of issues to report
  max_issues = 0

  # Minimum severity level
  minimum_failure_severity = "warning"
}

# Rule Configuration
# Enable/disable specific rules
rule "terraform_deprecated_interpolation" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}

rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_typed_variables" {
  enabled = true
}

rule "terraform_naming_convention" {
  enabled = true

  # Custom naming conventions for different resource types
  custom_conventions {
    # AWS Resources
    aws_instance = {
      format = "^([a-z0-9]+-)*[a-z0-9]+$"
      prefix = ""
      suffix = ""
    }

    aws_s3_bucket = {
      format = "^([a-z0-9]+-)*[a-z0-9]+$"
      prefix = ""
      suffix = ""
    }

    # Azure Resources
    azurerm_resource_group = {
      format = "^([a-z0-9]+-)*[a-z0-9]+$"
      prefix = "rg-"
      suffix = ""
    }

    azurerm_storage_account = {
      format = "^[a-z0-9]{3,24}$"
      prefix = ""
      suffix = ""
    }

    # GCP Resources
    google_compute_instance = {
      format = "^([a-z0-9]+-)*[a-z0-9]+$"
      prefix = ""
      suffix = ""
    }

    google_storage_bucket = {
      format = "^([a-z0-9]+-)*[a-z0-9]+$"
      prefix = ""
      suffix = ""
    }
  }
}

rule "terraform_required_version" {
  enabled = true

  # Require specific Terraform version
  required_version = ">= 1.0"
}

rule "terraform_required_providers" {
  enabled = true

  # Require specific provider versions
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }

    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.0"
    }

    google = {
      source  = "hashicorp/google"
      version = ">= 4.0"
    }
  }
}

rule "terraform_standard_module_structure" {
  enabled = true
}

rule "terraform_unused_declarations" {
  enabled = true
}

rule "terraform_comment_syntax" {
  enabled = true
}

rule "terraform_block_standard" {
  enabled = true
}

rule "terraform_module_pinned_source" {
  enabled = true

  # Require specific version pinning for modules
  style = "flexible"
}

rule "terraform_module_version" {
  enabled = true

  # Require version constraints for modules
  exact = false
}

rule "terraform_output_separate" {
  enabled = true
}

rule "terraform_variable_separate" {
  enabled = true
}

rule "terraform_variable_nullable_false" {
  enabled = true
}

# AWS-Specific Rules
rule "aws_instance_invalid_type" {
  enabled = true
}

rule "aws_instance_previous_type" {
  enabled = true
}

rule "aws_elb_invalid_instance" {
  enabled = true
}

rule "aws_elb_invalid_subnet" {
  enabled = true
}

rule "aws_elb_invalid_security_group" {
  enabled = true
}

rule "aws_s3_bucket_invalid_acl" {
  enabled = true
}

rule "aws_s3_bucket_invalid_region" {
  enabled = true
}

rule "aws_s3_bucket_name" {
  enabled = true
}

rule "aws_s3_bucket_policy" {
  enabled = true
}

rule "aws_s3_bucket_versioning" {
  enabled = true
}

rule "aws_s3_bucket_encryption" {
  enabled = true
}

rule "aws_s3_bucket_public_access_block" {
  enabled = true
}

rule "aws_iam_policy_document_gov_friendly_arns" {
  enabled = true
}

rule "aws_iam_policy_gov_friendly_arns" {
  enabled = true
}

rule "aws_iam_role_policy_gov_friendly_arns" {
  enabled = true
}

rule "aws_iam_user_policy_gov_friendly_arns" {
  enabled = true
}

rule "aws_route53_record_overriding" {
  enabled = true
}

rule "aws_route53_record_type_invalid" {
  enabled = true
}

# Azure-Specific Rules
rule "azurerm_kubernetes_cluster_invalid_network_plugin" {
  enabled = true
}

rule "azurerm_kubernetes_cluster_invalid_network_policy" {
  enabled = true
}

rule "azurerm_kubernetes_cluster_invalid_load_balancer_sku" {
  enabled = true
}
}

rule "azurerm_storage_account_invalid_name" {
  enabled = true
}

rule "azurerm_storage_account_invalid_account_kind" {
  enabled = true
}

rule "azurerm_storage_account_invalid_account_tier" {
  enabled = true
}

rule "azurerm_storage_account_invalid_access_tier" {
  enabled = true
}

rule "azurerm_storage_account_invalid_blob_encryption_type" {
  enabled = true
}

rule "azurerm_storage_account_invalid_file_encryption_type" {
  enabled = true
}

rule "azurerm_storage_account_invalid_queue_encryption_type" {
  enabled = true
}

rule "azurerm_storage_account_invalid_table_encryption_type" {
  enabled = true
}

rule "azurerm_storage_container_invalid_name" {
  enabled = true
}

rule "azurerm_storage_container_invalid_access_type" {
  enabled = true
}

rule "azurerm_virtual_machine_invalid_vm_size" {
  enabled = true
}

rule "azurerm_virtual_machine_scale_set_invalid_vm_size" {
  enabled = true
}

rule "azurerm_key_vault_invalid_name" {
  enabled = true
}

rule "azurerm_key_vault_key_invalid_name" {
  enabled = true
}

rule "azurerm_key_vault_secret_invalid_name" {
  enabled = true
}

rule "azurerm_key_vault_certificate_invalid_name" {
  enabled = true
}

# GCP-Specific Rules
rule "google_compute_instance_invalid_machine_type" {
  enabled = true
}

rule "google_compute_instance_invalid_zone" {
  enabled = true
}

rule "google_compute_disk_invalid_type" {
  enabled = true
}

rule "google_compute_disk_invalid_zone" {
  enabled = true
}

rule "google_compute_network_invalid_name" {
  enabled = true
}

rule "google_compute_subnetwork_invalid_name" {
  enabled = true
}

rule "google_compute_subnetwork_invalid_region" {
  enabled = true
}

rule "google_compute_subnetwork_invalid_network" {
  enabled = true
}

rule "google_compute_router_invalid_name" {
  enabled = true
}

rule "google_compute_router_invalid_network" {
  enabled = true
}

rule "google_compute_router_invalid_region" {
  enabled = true
}

rule "google_compute_router_nat_invalid_name" {
  enabled = true
}

rule "google_compute_router_nat_invalid_router" {
  enabled = true
}

rule "google_compute_router_nat_invalid_nat_ip_allocate_option" {
  enabled = true
}

rule "google_compute_router_nat_invalid_source_subnetwork_ip_ranges_to_nat" {
  enabled = true
}

rule "google_compute_security_policy_invalid_name" {
  enabled = true
}

rule "google_compute_ssl_policy_invalid_name" {
  enabled = true
}

rule "google_compute_ssl_policy_invalid_profile" {
  enabled = true
}

rule "google_compute_ssl_policy_invalid_min_tls_version" {
  enabled = true
}

rule "google_compute_target_pool_invalid_name" {
  enabled = true
}

rule "google_compute_target_pool_invalid_region" {
  enabled = true
}

rule "google_compute_url_map_invalid_name" {
  enabled = true
}

rule "google_compute_vpn_gateway_invalid_name" {
  enabled = true
}

rule "google_compute_vpn_gateway_invalid_network" {
  enabled = true
}

rule "google_compute_vpn_gateway_invalid_region" {
  enabled = true
}

rule "google_container_cluster_invalid_name" {
  enabled = true
}

rule "google_container_cluster_invalid_location" {
  enabled = true
}

rule "google_container_node_pool_invalid_name" {
  enabled = true
}

rule "google_container_node_pool_invalid_location" {
  enabled = true
}

rule "google_dns_managed_zone_invalid_name" {
  enabled = true
}

rule "google_dns_record_set_invalid_name" {
  enabled = true
}

rule "google_dns_record_set_invalid_type" {
  enabled = true
}

rule "google_project_invalid_name" {
  enabled = true
}

rule "google_project_invalid_project_id" {
  enabled = true
}

rule "google_project_invalid_org_id" {
  enabled = true
}

rule "google_project_invalid_folder_id" {
  enabled = true
}

rule "google_project_iam_binding_invalid_role" {
  enabled = true
}

rule "google_project_iam_member_invalid_role" {
  enabled = true
}

rule "google_project_iam_policy_invalid_project" {
  enabled = true
}

rule "google_project_service_invalid_service" {
  enabled = true
}

rule "google_service_account_invalid_account_id" {
  enabled = true
}

rule "google_sql_database_instance_invalid_name" {
  enabled = true
}

rule "google_sql_database_instance_invalid_database_version" {
  enabled = true
}

rule "google_sql_database_invalid_name" {
  enabled = true
}

rule "google_sql_user_invalid_name" {
  enabled = true
}

rule "google_storage_bucket_invalid_name" {
  enabled = true
}

rule "google_storage_bucket_invalid_location" {
  enabled = true
}

rule "google_storage_bucket_invalid_storage_class" {
  enabled = true
}

# Security-Focused Rules
rule "aws_security_group_rule_description" {
  enabled = true
}

rule "aws_iam_policy_versioning" {
  enabled = true
}

rule "aws_iam_role_policy_attachment_limit" {
  enabled = true
}

rule "aws_iam_user_policy_attachment_limit" {
  enabled = true
}

rule "aws_cloudwatch_log_group_retention" {
  enabled = true
}

rule "aws_config_configuration_recorder_all_regions" {
  enabled = true
}

rule "aws_db_instance_backup_retention" {
  enabled = true
}

rule "aws_db_instance_deletion_protection" {
  enabled = true
}

rule "aws_db_instance_encryption" {
  enabled = true
}

rule "aws_db_instance_multi_az" {
  enabled = true
}

rule "aws_db_instance_publicly_accessible" {
  enabled = true
}

rule "aws_db_instance_storage_encrypted" {
  enabled = true
}

rule "aws_db_instance_vpc_security_group" {
  enabled = true
}

rule "aws_db_snapshot_encrypted" {
  enabled = true
}

rule "aws_ebs_volume_encryption" {
  enabled = true
}

rule "aws_ec2_instance_monitoring" {
  enabled = true
}

rule "aws_elb_access_logging" {
  enabled = true
}

rule "aws_elb_tls_policy" {
  enabled = true
}

rule "aws_kms_key_rotation" {
  enabled = true
}

rule "aws_lambda_function_tracing" {
  enabled = true
}

rule "aws_rds_cluster_backup_retention" {
  enabled = true
}

rule "aws_rds_cluster_deletion_protection" {
  enabled = true
}

rule "aws_rds_cluster_encryption" {
  enabled = true
}

rule "aws_rds_cluster_publicly_accessible" {
  enabled = true
}

rule "aws_s3_bucket_encryption" {
  enabled = true
}

rule "aws_s3_bucket_logging" {
  enabled = true
}

rule "aws_s3_bucket_public_access_block" {
  enabled = true
}

rule "aws_s3_bucket_versioning" {
  enabled = true
}

rule "aws_s3_bucket_website" {
  enabled = true
}

rule "aws_security_group_description" {
  enabled = true
}

rule "aws_sns_topic_encryption" {
  enabled = true
}

rule "aws_sqs_queue_encryption" {
  enabled = true
}

rule "aws_vpc_flow_log_destination" {
  enabled = true
}

rule "aws_vpc_nat_gateway_per_subnet" {
  enabled = true
}

# Output Configuration
config {
  # Format options: default, json, checkstyle, junit, compact, sarif
  format = "default"

  # Output file path
  output = ""

  # Disable color output
  disable = false

  # Enable plugin cache
  plugin_cache = true

  # Plugin cache directory
  plugin_cache_dir = "~/.tflint.d/plugin-cache"

  # Maximum number of issues to report
  max_issues = 0

  # Minimum failure severity
  minimum_failure_severity = "warning"
}
