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

# Data sources
data "aws_ami" "amazon_linux_2" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*-x86_64-gp2"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

# Application Server
resource "aws_instance" "app_server" {
  ami           = var.app_server_ami_id != "" ? var.app_server_ami_id : data.aws_ami.amazon_linux_2.id
  instance_type = var.app_server_instance_type

  subnet_id                   = var.private_subnet_ids[0]
  vpc_security_group_ids      = [var.app_security_group_id]
  associate_public_ip_address = false

  key_name = var.key_pair_name != "" ? var.key_pair_name : null

  user_data = lookup(var.user_data_scripts, "app_server", null)

  root_block_device {
    encrypted   = true
    kms_key_id  = var.kms_key_arn
    volume_size = 20
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-app-server"
  })
}

# Nitro Enclave
resource "aws_instance" "nitro_enclave" {
  ami           = var.nitro_enclave_ami_id != "" ? var.nitro_enclave_ami_id : data.aws_ami.amazon_linux_2.id
  instance_type = var.nitro_enclave_instance_type

  subnet_id                   = var.private_subnet_ids[0]
  vpc_security_group_ids      = [var.yubihsm_security_group_id]
  associate_public_ip_address = false

  key_name = var.key_pair_name != "" ? var.key_pair_name : null

  user_data = lookup(var.user_data_scripts, "nitro_enclave", null)

  enclave_options {
    enabled = true
  }

  root_block_device {
    encrypted   = true
    kms_key_id  = var.kms_key_arn
    volume_size = 20
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-nitro-enclave"
  })
}