# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# AWS CloudHSM v2 Terraform Module
# iGaming Platform — CloudHSM cluster, OpenBao ASG, NLB
#
# This module provisions:
#   - CloudHSM v2 cluster with HSMs across multiple AZs
#   - Security groups for HSM-to-OpenBao communication
#   - IAM role for OpenBao EC2 instances
#   - KMS key for fallback unseal and storage encryption
#   - OpenBao Auto Scaling Group with PKCS#11 seal configuration
#   - Internal NLB for OpenBao API access
#   - CloudWatch log groups and alarms
#
# Relation to production module:
#   This is the chapter reference implementation.
#   The full production module is at:
#   writing/infrastructure/infra-terraform/improvements/hsm/
#
# Compliance: PCI DSS v4.0.1 Req. 3.5-3.7, FIPS 140-2 Level 3, ISO 27001 A.8.24

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  name_prefix = "${var.env}-hsm"

  common_tags = merge(var.tags, {
    Environment = var.env
    Module      = "cloudhsm"
    ManagedBy   = "terraform"
    Compliance  = "pci-dss-4.0.1"
    FipsLevel   = "140-2-L3"
    # Cost allocation tags (required for financial tracking)
    CostCentre   = var.cost_centre
    BusinessUnit = var.business_unit
  })
}

# ── Data sources ──────────────────────────────────────────────────────────────

data "aws_caller_identity" "current" {}

data "aws_vpc" "selected" {
  id = var.vpc_id
}

# ── KMS key: OpenBao auto-unseal fallback and storage encryption ──────────────
# Used when CloudHSM is unavailable (e.g., during initial bootstrap)
# and for encrypting OpenBao Raft storage at the EBS level.

resource "aws_kms_key" "openbao_unseal" {
  description             = "${local.name_prefix}-openbao-unseal"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  multi_region            = false

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowRootAccountFull"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowOpenBaoUnseal"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.openbao.arn
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:DescribeKey",
        ]
        Resource = "*"
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-openbao-unseal"
    Purpose = "OpenBao fallback unseal and EBS encryption"
  })
}

resource "aws_kms_alias" "openbao_unseal" {
  name          = "alias/${local.name_prefix}-openbao-unseal"
  target_key_id = aws_kms_key.openbao_unseal.key_id
}

# ── CloudHSM v2 Cluster ───────────────────────────────────────────────────────

resource "aws_cloudhsm_v2_cluster" "main" {
  hsm_type   = var.hsm_type
  subnet_ids = var.hsm_subnet_ids

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-cluster"
    Purpose = "FIPS 140-2 Level 3 cryptographic operations"
  })
}

# HSM devices — one per subnet/AZ for HA
# Minimum 2 required for production; 3 recommended for full AZ redundancy
resource "aws_cloudhsm_v2_hsm" "nodes" {
  count      = length(var.hsm_subnet_ids)
  cluster_id = aws_cloudhsm_v2_cluster.main.cluster_id
  subnet_id  = var.hsm_subnet_ids[count.index]
}

# ── Security Groups ───────────────────────────────────────────────────────────

resource "aws_security_group" "hsm_cluster" {
  name        = "${local.name_prefix}-cluster-sg"
  description = "CloudHSM cluster — PKCS11 from OpenBao nodes only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "PKCS11 from OpenBao nodes (TCP 2223-2225)"
    from_port       = 2223
    to_port         = 2225
    protocol        = "tcp"
    security_groups = [aws_security_group.openbao.id]
  }

  egress {
    description = "Allow all outbound within VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [data.aws_vpc.selected.cidr_block]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-cluster-sg"
  })
}

resource "aws_security_group" "openbao" {
  name        = "${local.name_prefix}-openbao-sg"
  description = "OpenBao nodes — API (8200) and Raft (8201)"
  vpc_id      = var.vpc_id

  dynamic "ingress" {
    for_each = length(var.openbao_allowed_cidrs) > 0 ? [1] : []
    content {
      description = "OpenBao API from allowed CIDRs"
      from_port   = 8200
      to_port     = 8200
      protocol    = "tcp"
      cidr_blocks = var.openbao_allowed_cidrs
    }
  }

  ingress {
    description = "OpenBao Raft replication (self)"
    from_port   = 8201
    to_port     = 8201
    protocol    = "tcp"
    self        = true
  }

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-openbao-sg"
  })
}

# ── IAM: OpenBao EC2 role ─────────────────────────────────────────────────────

resource "aws_iam_role" "openbao" {
  name = "${local.name_prefix}-openbao-role"
  path = "/igaming/"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_instance_profile" "openbao" {
  name = "${local.name_prefix}-openbao-profile"
  role = aws_iam_role.openbao.name
}

resource "aws_iam_role_policy" "openbao_kms" {
  name = "${local.name_prefix}-openbao-kms-unseal"
  role = aws_iam_role.openbao.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:DescribeKey",
        ]
        Resource = aws_kms_key.openbao_unseal.arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "openbao_cloudhsm" {
  name = "${local.name_prefix}-openbao-cloudhsm"
  role = aws_iam_role.openbao.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "cloudhsm:DescribeClusters",
          "cloudhsm:ListTags",
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "openbao_ssm" {
  role       = aws_iam_role.openbao.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "openbao_cloudwatch" {
  role       = aws_iam_role.openbao.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

# ── CloudWatch log groups ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "openbao_audit" {
  name              = "/igaming/${var.env}/openbao/audit"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.openbao_unseal.arn

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-openbao-audit"
    Purpose = "PCI DSS audit log — immutable record of all key operations"
  })
}

resource "aws_cloudwatch_log_group" "openbao_operational" {
  name              = "/igaming/${var.env}/openbao/operational"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.openbao_unseal.arn

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-openbao-operational"
  })
}

# ── S3 bucket for CloudHSM backups and NLB access logs ───────────────────────

resource "aws_s3_bucket" "backups" {
  bucket        = "${local.name_prefix}-backups-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-backups"
    Purpose = "CloudHSM cluster backups and NLB access logs"
  })
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.openbao_unseal.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "expire-old-backups"
    status = "Enabled"

    filter {
      prefix = "cloudhsm/"
    }

    expiration {
      days = 365
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  rule {
    id     = "expire-old-nlb-logs"
    status = "Enabled"

    filter {
      prefix = "openbao-nlb/"
    }

    expiration {
      days = 90
    }
  }
}

# ── OpenBao Launch Template ───────────────────────────────────────────────────

resource "aws_launch_template" "openbao" {
  name_prefix   = "${local.name_prefix}-openbao-"
  image_id      = var.openbao_ami_id
  instance_type = var.openbao_instance_type
  key_name      = var.openbao_key_name

  iam_instance_profile {
    arn = aws_iam_instance_profile.openbao.arn
  }

  network_interfaces {
    associate_public_ip_address = false
    security_groups             = [aws_security_group.openbao.id]
    delete_on_termination       = true
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = 50
      iops                  = 3000
      throughput            = 125
      encrypted             = true
      kms_key_id            = aws_kms_key.openbao_unseal.arn
      delete_on_termination = true
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only — security hardening
    http_put_response_hop_limit = 1
  }

  monitoring {
    enabled = true
  }

  user_data = base64encode(templatefile("${path.module}/templates/user_data.sh.tpl", {
    env              = var.env
    region           = var.region
    kms_key_id       = aws_kms_key.openbao_unseal.key_id
    hsm_cluster_id   = aws_cloudhsm_v2_cluster.main.cluster_id
    log_group_audit  = aws_cloudwatch_log_group.openbao_audit.name
    log_group_ops    = aws_cloudwatch_log_group.openbao_operational.name
    cloudhsm_pin_ssm = var.cloudhsm_pin_ssm_parameter
  }))

  tag_specifications {
    resource_type = "instance"
    tags = merge(local.common_tags, {
      Name = "${local.name_prefix}-openbao"
    })
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ── OpenBao Auto Scaling Group ────────────────────────────────────────────────

resource "aws_autoscaling_group" "openbao" {
  name_prefix         = "${local.name_prefix}-openbao-"
  vpc_zone_identifier = var.hsm_subnet_ids
  min_size            = var.openbao_min_size
  max_size            = var.openbao_max_size
  desired_capacity    = var.openbao_desired_capacity

  launch_template {
    id      = aws_launch_template.openbao.id
    version = "$Latest"
  }

  health_check_type         = "ELB"
  health_check_grace_period = 300

  # Raft requires care on scale-in — prefer oldest instances
  termination_policies = ["OldestInstance"]

  tag {
    key                 = "Name"
    value               = "${local.name_prefix}-openbao"
    propagate_at_launch = true
  }

  dynamic "tag" {
    for_each = local.common_tags
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }

  lifecycle {
    create_before_destroy = true
    ignore_changes        = [desired_capacity]
  }
}

# ── Internal NLB for OpenBao API ──────────────────────────────────────────────

resource "aws_lb" "openbao" {
  name               = "${local.name_prefix}-openbao-nlb"
  internal           = true
  load_balancer_type = "network"
  subnets            = var.hsm_subnet_ids

  enable_deletion_protection       = true
  enable_cross_zone_load_balancing = true

  access_logs {
    bucket  = aws_s3_bucket.backups.bucket
    prefix  = "openbao-nlb"
    enabled = true
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-openbao-nlb"
  })
}

resource "aws_lb_target_group" "openbao_api" {
  name        = "${local.name_prefix}-openbao-api"
  port        = 8200
  protocol    = "TCP"
  vpc_id      = var.vpc_id
  target_type = "instance"

  health_check {
    protocol            = "HTTPS"
    path                = "/v1/sys/health"
    port                = "traffic-port"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 10
  }

  tags = local.common_tags
}

resource "aws_lb_listener" "openbao_api" {
  load_balancer_arn = aws_lb.openbao.arn
  port              = 8200
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.openbao_api.arn
  }
}

resource "aws_autoscaling_attachment" "openbao" {
  autoscaling_group_name = aws_autoscaling_group.openbao.id
  lb_target_group_arn    = aws_lb_target_group.openbao_api.arn
}

# ── CloudWatch Alarms ─────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "openbao_unhealthy_hosts" {
  alarm_name          = "${local.name_prefix}-openbao-unhealthy-hosts"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HealthyHostCount"
  namespace           = "AWS/NetworkELB"
  period              = 60
  statistic           = "Average"
  threshold           = var.openbao_min_size
  alarm_description   = "OpenBao NLB healthy host count below quorum — possible CloudHSM connectivity issue"
  treat_missing_data  = "breaching"
  alarm_actions       = var.alarm_sns_topic_arns
  ok_actions          = var.alarm_sns_topic_arns

  dimensions = {
    LoadBalancer = aws_lb.openbao.arn_suffix
    TargetGroup  = aws_lb_target_group.openbao_api.arn_suffix
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "hsm_cluster_available" {
  alarm_name          = "${local.name_prefix}-hsm-cluster-hsm-count"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HsmCount"
  namespace           = "AWS/CloudHSM"
  period              = 300
  statistic           = "Average"
  threshold           = 2
  alarm_description   = "CloudHSM cluster has fewer than 2 active HSMs — HA at risk"
  treat_missing_data  = "breaching"
  alarm_actions       = var.alarm_sns_topic_arns
  ok_actions          = var.alarm_sns_topic_arns

  dimensions = {
    ClusterId = aws_cloudhsm_v2_cluster.main.cluster_id
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "hsm_request_failures" {
  alarm_name          = "${local.name_prefix}-hsm-request-failures"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "FailedRequestCount"
  namespace           = "AWS/CloudHSM"
  period              = 60
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "CloudHSM PKCS#11 request failures exceeded threshold — check OpenBao connectivity"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_sns_topic_arns

  dimensions = {
    ClusterId = aws_cloudhsm_v2_cluster.main.cluster_id
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "openbao_asg_too_few" {
  alarm_name          = "${local.name_prefix}-openbao-asg-capacity"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "GroupInServiceInstances"
  namespace           = "AWS/AutoScaling"
  period              = 120
  statistic           = "Average"
  threshold           = var.openbao_min_size
  alarm_description   = "OpenBao ASG in-service instance count below minimum"
  treat_missing_data  = "breaching"
  alarm_actions       = var.alarm_sns_topic_arns

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.openbao.name
  }

  tags = local.common_tags
}
