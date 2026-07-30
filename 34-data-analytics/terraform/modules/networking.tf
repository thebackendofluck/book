# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Networking Module for AWS Data Lake
# Includes VPC, VPN Gateway, and Direct Connect for on-premise connectivity
#
# This module creates:
# - VPC with public/private subnets
# - NAT Gateways for outbound internet access
# - VPN Gateway for on-premise connectivity
# - VPC Endpoints for AWS services
# - Security groups for data lake components

# =============================================================================
# VARIABLES
# =============================================================================

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.100.0.0/16"
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "project_name" {
  description = "Project name"
  type        = string
}

variable "on_premise_cidr" {
  description = "On-premise network CIDR"
  type        = string
  default     = "192.168.0.0/16"
}

variable "enable_vpn_gateway" {
  description = "Enable VPN Gateway for on-premise connectivity"
  type        = bool
  default     = true
}

variable "customer_gateway_ip" {
  description = "Customer gateway public IP address"
  type        = string
  default     = ""
}

variable "availability_zones" {
  description = "List of availability zones"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    Module      = "networking"
  }

  # Calculate subnet CIDRs
  public_subnets = [
    cidrsubnet(var.vpc_cidr, 4, 0), # 10.100.0.0/20
    cidrsubnet(var.vpc_cidr, 4, 1), # 10.100.16.0/20
    cidrsubnet(var.vpc_cidr, 4, 2), # 10.100.32.0/20
  ]

  private_subnets = [
    cidrsubnet(var.vpc_cidr, 4, 4), # 10.100.64.0/20
    cidrsubnet(var.vpc_cidr, 4, 5), # 10.100.80.0/20
    cidrsubnet(var.vpc_cidr, 4, 6), # 10.100.96.0/20
  ]

  database_subnets = [
    cidrsubnet(var.vpc_cidr, 4, 8),  # 10.100.128.0/20
    cidrsubnet(var.vpc_cidr, 4, 9),  # 10.100.144.0/20
    cidrsubnet(var.vpc_cidr, 4, 10), # 10.100.160.0/20
  ]
}

# =============================================================================
# VPC
# =============================================================================

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-vpc"
  })
}

# =============================================================================
# INTERNET GATEWAY
# =============================================================================

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-igw"
  })
}

# =============================================================================
# SUBNETS
# =============================================================================

resource "aws_subnet" "public" {
  count                   = length(local.public_subnets)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.public_subnets[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-public-${var.availability_zones[count.index]}"
    Type = "public"
  })
}

resource "aws_subnet" "private" {
  count             = length(local.private_subnets)
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.private_subnets[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-private-${var.availability_zones[count.index]}"
    Type = "private"
  })
}

resource "aws_subnet" "database" {
  count             = length(local.database_subnets)
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.database_subnets[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-database-${var.availability_zones[count.index]}"
    Type = "database"
  })
}

# =============================================================================
# NAT GATEWAYS
# =============================================================================

resource "aws_eip" "nat" {
  count  = length(local.public_subnets)
  domain = "vpc"

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-nat-eip-${count.index}"
  })

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count         = length(local.public_subnets)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-nat-${var.availability_zones[count.index]}"
  })

  depends_on = [aws_internet_gateway.main]
}

# =============================================================================
# ROUTE TABLES
# =============================================================================

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-public-rt"
  })
}

resource "aws_route_table" "private" {
  count  = length(local.private_subnets)
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-private-rt-${var.availability_zones[count.index]}"
  })
}

resource "aws_route_table_association" "public" {
  count          = length(local.public_subnets)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(local.private_subnets)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_route_table_association" "database" {
  count          = length(local.database_subnets)
  subnet_id      = aws_subnet.database[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# =============================================================================
# VPN GATEWAY (On-Premise Connectivity)
# =============================================================================

resource "aws_vpn_gateway" "main" {
  count  = var.enable_vpn_gateway ? 1 : 0
  vpc_id = aws_vpc.main.id

  amazon_side_asn = 64512

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-vpn-gw"
  })
}

resource "aws_customer_gateway" "on_premise" {
  count      = var.enable_vpn_gateway && var.customer_gateway_ip != "" ? 1 : 0
  bgp_asn    = 65000
  ip_address = var.customer_gateway_ip
  type       = "ipsec.1"

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-customer-gw"
  })
}

resource "aws_vpn_connection" "on_premise" {
  count               = var.enable_vpn_gateway && var.customer_gateway_ip != "" ? 1 : 0
  vpn_gateway_id      = aws_vpn_gateway.main[0].id
  customer_gateway_id = aws_customer_gateway.on_premise[0].id
  type                = "ipsec.1"
  static_routes_only  = false

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-vpn-connection"
  })
}

# Route to on-premise through VPN
resource "aws_route" "vpn_to_on_premise" {
  count                  = var.enable_vpn_gateway ? length(local.private_subnets) : 0
  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = var.on_premise_cidr
  gateway_id             = aws_vpn_gateway.main[0].id
}

# =============================================================================
# VPC ENDPOINTS (Private connectivity to AWS services)
# =============================================================================

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-s3-endpoint"
  })
}

resource "aws_vpc_endpoint" "glue" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.glue"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-glue-endpoint"
  })
}

resource "aws_vpc_endpoint" "kinesis" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.kinesis-streams"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-kinesis-endpoint"
  })
}

resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-secrets-endpoint"
  })
}

resource "aws_vpc_endpoint" "kms" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.kms"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-kms-endpoint"
  })
}

# =============================================================================
# SECURITY GROUPS
# =============================================================================

resource "aws_security_group" "vpc_endpoints" {
  name        = "${var.project_name}-${var.environment}-vpc-endpoints-sg"
  description = "Security group for VPC endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "HTTPS from VPC"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-vpc-endpoints-sg"
  })
}

resource "aws_security_group" "glue_jobs" {
  name        = "${var.project_name}-${var.environment}-glue-jobs-sg"
  description = "Security group for Glue ETL jobs"
  vpc_id      = aws_vpc.main.id

  # Self-referencing rule for Glue worker communication
  ingress {
    from_port = 0
    to_port   = 65535
    protocol  = "tcp"
    self      = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-glue-jobs-sg"
  })
}

resource "aws_security_group" "on_premise_connector" {
  name        = "${var.project_name}-${var.environment}-on-premise-sg"
  description = "Security group for on-premise data connectors"
  vpc_id      = aws_vpc.main.id

  # Allow from on-premise CIDR
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.on_premise_cidr]
    description = "HTTPS from on-premise"
  }

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.on_premise_cidr]
    description = "PostgreSQL from on-premise"
  }

  ingress {
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    cidr_blocks = [var.on_premise_cidr]
    description = "MySQL from on-premise"
  }

  ingress {
    from_port   = 1433
    to_port     = 1433
    protocol    = "tcp"
    cidr_blocks = [var.on_premise_cidr]
    description = "SQL Server from on-premise"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-${var.environment}-on-premise-sg"
  })
}

# =============================================================================
# DATA SOURCES
# =============================================================================

data "aws_region" "current" {}

# =============================================================================
# OUTPUTS
# =============================================================================

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "VPC CIDR block"
  value       = aws_vpc.main.cidr_block
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = aws_subnet.private[*].id
}

output "database_subnet_ids" {
  description = "Database subnet IDs"
  value       = aws_subnet.database[*].id
}

output "vpn_gateway_id" {
  description = "VPN Gateway ID"
  value       = var.enable_vpn_gateway ? aws_vpn_gateway.main[0].id : null
}

output "glue_security_group_id" {
  description = "Glue jobs security group ID"
  value       = aws_security_group.glue_jobs.id
}

output "on_premise_security_group_id" {
  description = "On-premise connector security group ID"
  value       = aws_security_group.on_premise_connector.id
}
