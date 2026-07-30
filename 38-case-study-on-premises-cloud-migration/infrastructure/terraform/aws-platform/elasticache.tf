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
# ElastiCache Redis — Session Store & Game State Cache
# =============================================================================
# Regulatory context:
#   NJ DGE 13:69O-1.4  — Session management must prevent unauthorized access;
#                         session tokens must be encrypted in transit and at rest.
#   PA PGCB §809a.7    — Player session data must be available across failover
#                         events without data loss.
#   PCI-DSS 4.1        — Encrypt all data in transit over public/open networks.
#   PCI-DSS 3.4        — Render stored cardholder data unreadable.
#
# Usage in casino platform:
#   - Player session tokens (JWT blacklist, active sessions)
#   - Game state cache (active game rounds, bet slips)
#   - Rate limiting (API throttling, responsible gaming limits)
#   - Real-time leaderboards and jackpot counters
# =============================================================================

# --- Subnet Group ------------------------------------------------------------

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project_name}-${var.environment}-redis-subnet"
  subnet_ids = aws_subnet.data[*].id

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-redis-subnet"
  })
}

# --- Parameter Group ---------------------------------------------------------

resource "aws_elasticache_parameter_group" "igaming" {
  name   = "${var.project_name}-${var.environment}-redis71-igaming"
  family = "redis7"

  # Session timeout — auto-expire inactive player sessions
  parameter {
    name  = "timeout"
    value = "1800"
    # 30 minutes — NJ DGE requires idle session termination
  }

  # Eviction policy — never evict game state; fail writes instead
  parameter {
    name  = "maxmemory-policy"
    value = "noeviction"
    # Game state and bet data must never be silently evicted
  }

  # Keyspace notifications for session expiry callbacks
  parameter {
    name  = "notify-keyspace-events"
    value = "Ex"
    # Enables expired-event notifications for session cleanup
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-redis71-igaming"
  })
}

# --- Replication Group (Multi-AZ) --------------------------------------------

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${var.project_name}-${var.environment}-redis"
  description          = "Redis cluster for casino session/cache (multi-AZ, encrypted)"

  # Engine
  engine               = "redis"
  engine_version       = var.redis_engine_version
  node_type            = var.redis_node_type
  num_cache_clusters   = var.redis_num_cache_clusters
  parameter_group_name = aws_elasticache_parameter_group.igaming.name
  port                 = 6379

  # Network
  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  # High Availability
  automatic_failover_enabled = true
  multi_az_enabled           = true
  # PA PGCB §809a.7: Session data must survive AZ failure

  # Encryption at rest
  at_rest_encryption_enabled = true
  # NJ DGE 13:69O-1.4: Session tokens encrypted at rest
  # PCI-DSS 3.4: Stored data must be rendered unreadable

  # Encryption in transit
  transit_encryption_enabled = true
  # PCI-DSS 4.1: All data encrypted in transit

  # Maintenance
  maintenance_window         = "Sun:03:00-Sun:04:00"
  snapshot_retention_limit   = 7
  snapshot_window            = "02:00-03:00"
  auto_minor_version_upgrade = true

  # Notifications
  notification_topic_arn = aws_sns_topic.alarms.arn

  tags = merge(var.tags, {
    Name       = "${var.project_name}-${var.environment}-redis"
    Compliance = "NJ-DGE-13:69O-1.4,PCI-DSS-3.4,PCI-DSS-4.1"
    DataClass  = "confidential"
  })

  lifecycle {
    ignore_changes = [num_cache_clusters]
  }
}
