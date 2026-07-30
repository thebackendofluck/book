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
# Auto Scaling Groups -- Elastic Capacity
# =============================================================================
# CONTEXT: On-premises, scaling meant a 47-step manual provisioning process
# that took 6.5 hours. With ASG, capacity adjusts automatically based on
# demand. During the 2020 Champions League, the platform scaled from 2 to 4
# instances in under 3 minutes -- no human intervention required.
#
# Each brand/product gets its own ASG with tuned min/max/desired values.
# The launch configuration references a corporate base AMI that includes
# hardened Ubuntu 20.04, Datadog agent, and Docker runtime.
# =============================================================================

module "platform_asg" {
  source  = "terraform-aws-modules/autoscaling/aws"
  version = "~> 4.0"

  name = "platform-prod"

  min_size                  = 2
  max_size                  = 4
  desired_capacity          = 2
  wait_for_capacity_timeout = 0
  health_check_type         = "EC2"
  vpc_zone_identifier       = var.default_subnets

  lc_name                     = "platform-lc"
  description                 = "Launch configuration for platform frontend"
  update_default_version      = true
  associate_public_ip_address = true

  use_lc    = true
  create_lc = true

  image_id          = var.base_ubuntu_ami
  instance_type     = "m4.2xlarge"
  ebs_optimized     = true
  enable_monitoring = true
  key_name          = var.ssh_key_name

  block_device_mappings = [
    {
      device_name = "/dev/sda1"
      ebs = {
        delete_on_termination = true
        encrypted             = false
        volume_size           = 200
        volume_type           = "gp2"
      }
    }
  ]

  security_groups = [
    aws_security_group.platform_instances.id,
    aws_security_group.common_monitoring.id
  ]

  tags = [
    {
      key                 = "Environment"
      value               = "prod"
      propagate_at_launch = true
    },
    {
      key                 = "Terraform"
      value               = "true"
      propagate_at_launch = true
    }
  ]
}

# --- Affiliate Portal ASG ----------------------------------------------------
# Smaller footprint -- affiliate portals see less traffic but still need
# resilience. min_size=1 with max_size=2 keeps costs down while allowing
# scale-out during reporting periods (end of month affiliate reconciliation).

module "affiliate_portal_asg" {
  source  = "terraform-aws-modules/autoscaling/aws"
  version = "~> 4.0"

  name = "affiliate-portal-prod"

  min_size                  = 1
  max_size                  = 2
  desired_capacity          = 1
  wait_for_capacity_timeout = 0
  health_check_type         = "EC2"
  vpc_zone_identifier       = var.default_subnets

  lc_name                     = "affiliate-portal-lc"
  description                 = "Launch configuration for affiliate portal"
  update_default_version      = true
  associate_public_ip_address = true

  use_lc    = true
  create_lc = true

  image_id          = var.base_ubuntu_ami
  instance_type     = "m4.2xlarge"
  ebs_optimized     = true
  enable_monitoring = true
  key_name          = var.ssh_key_name

  block_device_mappings = [
    {
      device_name = "/dev/sda1"
      ebs = {
        delete_on_termination = true
        encrypted             = false
        volume_size           = 200
        volume_type           = "gp2"
      }
    }
  ]

  security_groups = [
    aws_security_group.affiliate_instances.id,
    aws_security_group.common_monitoring.id
  ]

  tags = [
    {
      key                 = "Environment"
      value               = "prod"
      propagate_at_launch = true
    }
  ]
}
