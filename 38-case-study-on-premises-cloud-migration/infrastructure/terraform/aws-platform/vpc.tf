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
# VPC — Multi-AZ Network for iGaming Casino Platform
# =============================================================================
# Regulatory context:
#   NJ DGE 13:69O-1.2  — Systems must be located in authorized data centers
#                         with redundant network paths.
#   PA PGCB § 809a      — Network segmentation required between public-facing
#                         and internal/data tiers.
#   PCI-DSS 1.3         — Firewall/segmentation between DMZ and internal zones.
#
# Architecture:
#   3 AZs × 3 tiers = 9 subnets
#     Public  — ALB, NAT Gateways (internet-facing)
#     Private — ECS Fargate tasks, Lambda (application tier)
#     Data    — RDS, ElastiCache (database tier, no internet route)
# =============================================================================

# --- VPC ---------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-vpc"
    # NJ DGE: Network infrastructure must be documented and auditable
    Compliance = "NJ-DGE-13:69O-1.2"
  })
}

# --- Internet Gateway --------------------------------------------------------

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-igw"
  })
}

# --- Public Subnets (ALB, NAT GW) -------------------------------------------

resource "aws_subnet" "public" {
  count = length(var.availability_zones)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-public-${var.availability_zones[count.index]}"
    Tier = "public"
  })
}

# --- Private Subnets (ECS Fargate, Application Tier) -------------------------

resource "aws_subnet" "private" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-private-${var.availability_zones[count.index]}"
    Tier = "private"
    # PCI-DSS 1.3: Application tier isolated from public internet
    Compliance = "PCI-DSS-1.3"
  })
}

# --- Data Subnets (RDS, ElastiCache) -----------------------------------------

resource "aws_subnet" "data" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = var.data_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-data-${var.availability_zones[count.index]}"
    Tier = "data"
    # PCI-DSS 1.3.7: Database tier must not be directly accessible from DMZ
    Compliance = "PCI-DSS-1.3.7"
  })
}

# --- NAT Gateways (one per AZ for HA) ---------------------------------------
# NJ DGE requires redundant network paths — single NAT is a compliance risk.

resource "aws_eip" "nat" {
  count  = length(var.availability_zones)
  domain = "vpc"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-nat-eip-${count.index}"
  })
}

resource "aws_nat_gateway" "main" {
  count = length(var.availability_zones)

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-nat-${var.availability_zones[count.index]}"
    # NJ DGE: Redundant egress path per AZ
    Compliance = "NJ-DGE-HA-requirement"
  })

  depends_on = [aws_internet_gateway.main]
}

# --- Route Tables ------------------------------------------------------------

# Public route table — routes to IGW
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-public-rt"
  })
}

resource "aws_route_table_association" "public" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Private route tables — one per AZ, routes to respective NAT GW
resource "aws_route_table" "private" {
  count  = length(var.availability_zones)
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-private-rt-${count.index}"
  })
}

resource "aws_route_table_association" "private" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# Data route tables — no internet route (isolated)
resource "aws_route_table" "data" {
  vpc_id = aws_vpc.main.id

  # No routes to internet — data tier is fully isolated
  # PCI-DSS 1.3.7: No direct route from database tier to untrusted networks

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-data-rt"
    # PCI-DSS: Data tier has no egress to internet
    Compliance = "PCI-DSS-1.3.7"
  })
}

resource "aws_route_table_association" "data" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.data.id
}

# --- VPC Flow Logs -----------------------------------------------------------
# NJ DGE 13:69O-1.9  — All network traffic must be logged and retained.
# PA PGCB §809a.12   — Network monitoring and logging required.
# PCI-DSS 10.1       — Audit trails for all access to network resources.

resource "aws_flow_log" "vpc" {
  count = var.enable_vpc_flow_logs ? 1 : 0

  vpc_id                   = aws_vpc.main.id
  traffic_type             = "ALL"
  iam_role_arn             = aws_iam_role.flow_log[0].arn
  log_destination          = aws_cloudwatch_log_group.flow_log[0].arn
  log_destination_type     = "cloud-watch-logs"
  max_aggregation_interval = 60

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-vpc-flow-log"
    Compliance = "NJ-DGE-13:69O-1.9,PCI-DSS-10.1"
  })
}

resource "aws_cloudwatch_log_group" "flow_log" {
  count = var.enable_vpc_flow_logs ? 1 : 0

  name              = "/aws/vpc/flow-log/${var.project_name}-${var.environment}"
  retention_in_days = 2557 # ~7 years — NJ DGE retention requirement

  tags = merge(var.tags, {
    Compliance = "NJ-DGE-7yr-retention"
  })
}

resource "aws_iam_role" "flow_log" {
  count = var.enable_vpc_flow_logs ? 1 : 0

  name = "${var.project_name}-${var.environment}-flow-log-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "vpc-flow-logs.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "flow_log" {
  count = var.enable_vpc_flow_logs ? 1 : 0

  name = "${var.project_name}-${var.environment}-flow-log-policy"
  role = aws_iam_role.flow_log[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams"
      ]
      Resource = "*"
    }]
  })
}

# --- Security Groups ---------------------------------------------------------

# ALB Security Group — allows inbound HTTPS from internet
resource "aws_security_group" "alb" {
  name_prefix = "${var.project_name}-${var.environment}-alb-"
  vpc_id      = aws_vpc.main.id
  description = "ALB ingress — HTTPS only (PCI-DSS 1.2.1)"

  ingress {
    description = "HTTPS from internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP redirect to HTTPS"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-alb-sg"
    Compliance = "PCI-DSS-1.2.1"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# ECS Security Group — allows traffic only from ALB
resource "aws_security_group" "ecs" {
  name_prefix = "${var.project_name}-${var.environment}-ecs-"
  vpc_id      = aws_vpc.main.id
  description = "ECS tasks — ingress from ALB only (PCI-DSS 1.3)"

  ingress {
    description     = "Traffic from ALB"
    from_port       = var.api_port
    to_port         = var.api_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "Allow all outbound (ECR pull, secrets, etc.)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-ecs-sg"
    Compliance = "PCI-DSS-1.3"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Database Security Group — allows traffic only from ECS
resource "aws_security_group" "database" {
  name_prefix = "${var.project_name}-${var.environment}-db-"
  vpc_id      = aws_vpc.main.id
  description = "Database tier — ingress from ECS only (PCI-DSS 1.3.7)"

  ingress {
    description     = "PostgreSQL from ECS"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    description = "No outbound needed"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-db-sg"
    Compliance = "PCI-DSS-1.3.7"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Redis Security Group — allows traffic only from ECS
resource "aws_security_group" "redis" {
  name_prefix = "${var.project_name}-${var.environment}-redis-"
  vpc_id      = aws_vpc.main.id
  description = "Redis tier — ingress from ECS only"

  ingress {
    description     = "Redis from ECS"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-redis-sg"
  })

  lifecycle {
    create_before_destroy = true
  }
}
