# Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Terraform Infrastructure - Chapter 22: Casino Implementation Planning and Timeline
#
# AWS infrastructure setup for an online casino platform including VPC,
# EKS cluster, and RDS MariaDB database with multi-AZ deployment.
#
# Part of the iGaming Platform Engineering book.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

# IAM Role for EKS Cluster
resource "aws_iam_role" "eks_cluster" {
  name = "casino-eks-cluster-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "eks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

# Random password for database
resource "random_password" "db_password" {
  length  = 32
  special = true
}

# VPC Configuration
resource "aws_vpc" "casino_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "casino-vpc"
    Environment = "production"
    Project     = "online-casino"
  }
}

# Private Subnets
resource "aws_subnet" "private_1" {
  vpc_id            = aws_vpc.casino_vpc.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = "eu-west-1a"

  tags = {
    Name        = "casino-private-1"
    Environment = "production"
  }
}

resource "aws_subnet" "private_2" {
  vpc_id            = aws_vpc.casino_vpc.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = "eu-west-1b"

  tags = {
    Name        = "casino-private-2"
    Environment = "production"
  }
}

resource "aws_subnet" "private_3" {
  vpc_id            = aws_vpc.casino_vpc.id
  cidr_block        = "10.0.3.0/24"
  availability_zone = "eu-west-1c"

  tags = {
    Name        = "casino-private-3"
    Environment = "production"
  }
}

# EKS Cluster
resource "aws_eks_cluster" "casino_cluster" {
  name     = "casino-cluster"
  role_arn = aws_iam_role.eks_cluster.arn
  version  = "1.27"

  vpc_config {
    subnet_ids = [
      aws_subnet.private_1.id,
      aws_subnet.private_2.id,
      aws_subnet.private_3.id
    ]
  }

  tags = {
    Environment = "production"
  }
}

# RDS Database
resource "aws_db_instance" "casino_db" {
  allocated_storage    = 100
  engine               = "mariadb"
  engine_version       = "18.0"
  instance_class       = "db.r6g.2xlarge"
  db_name              = "casino"
  username             = "admin"
  password             = random_password.db_password.result
  parameter_group_name = "mariadb10.11"

  multi_az                = true
  backup_retention_period = 7
  skip_final_snapshot     = false

  tags = {
    Environment = "production"
  }
}
