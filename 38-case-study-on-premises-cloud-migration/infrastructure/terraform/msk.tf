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
# MSK (Managed Streaming for Apache Kafka) -- Event Streaming
# =============================================================================
# CONTEXT: On-premises, real-time event processing ran on a single RabbitMQ
# instance that the ops team restarted every Sunday morning "just in case."
# MSK replaced this with a proper distributed streaming platform: 3 brokers,
# KMS encryption at rest, TLS in transit, and CloudWatch logging.
#
# Two clusters serve different purposes:
#   - risk-alerts:  Production risk/fraud detection (larger instances, more storage)
#   - event-stream: General event processing for analytics (smaller, cost-optimized)
# =============================================================================

# KMS key for encryption at rest -- all Kafka data encrypted with
# a customer-managed key for compliance with gambling commission requirements.
resource "aws_kms_key" "event_stream_kms" {
  description = "event-stream-kms"
}

resource "aws_kms_key" "risk_alerts_kms" {
  description = "risk-alerts-kms"
}

# Kafka broker configuration
# auto.create.topics.enable=true is set for development velocity --
# in a more mature setup, you'd disable this and manage topics through IaC.
resource "aws_msk_configuration" "auto_create_true" {
  kafka_versions = ["2.1.0", "2.2.1", "2.4.1", "2.4.1.1"]
  name           = "AutoCreateTrueProdConfig"

  server_properties = <<PROPERTIES
auto.create.topics.enable=true
default.replication.factor=3
min.insync.replicas=2
num.io.threads=8
num.network.threads=5
num.partitions=1
num.replica.fetchers=2
socket.request.max.bytes=104857600
unclean.leader.election.enable=true
PROPERTIES
}

# --- Production Risk Alerts Cluster -------------------------------------------
# Larger instance type (kafka.m5.large) and more storage (1500 GB per broker)
# because risk alert events must be retained for regulatory audit trails.

resource "aws_cloudwatch_log_group" "kafka_prod_broker" {
  name = "risk-alerts-msk-broker-logs"
}

resource "aws_s3_bucket" "kafka_prod_broker" {
  bucket = "acme-risk-alerts-msk-broker-logs"
  acl    = "private"
}

resource "aws_msk_cluster" "risk_alerts_msk" {
  cluster_name           = "prod-risk-alerts"
  kafka_version          = "2.4.1.1"
  number_of_broker_nodes = 3

  broker_node_group_info {
    instance_type   = "kafka.m5.large"
    ebs_volume_size = 1500
    client_subnets  = var.default_subnets
    security_groups = [aws_security_group.msk_cluster.id]
  }

  encryption_info {
    encryption_at_rest_kms_key_arn = aws_kms_key.risk_alerts_kms.arn
    encryption_in_transit {
      client_broker = "TLS_PLAINTEXT"
    }
  }

  open_monitoring {
    prometheus {
      jmx_exporter {
        enabled_in_broker = false
      }
      node_exporter {
        enabled_in_broker = false
      }
    }
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled = false
      }
      firehose {
        enabled = false
      }
      s3 {
        enabled = false
      }
    }
  }

  configuration_info {
    arn      = aws_msk_configuration.auto_create_true.arn
    revision = "1"
  }

  tags = {
    Terraform = "true"
  }
}

# --- Staging Event Stream Cluster ---------------------------------------------
# Smaller instance type for non-production workloads. Same architecture
# pattern (3 brokers, encryption) but cost-optimized with kafka.t3.small.

resource "aws_msk_cluster" "event_stream_stage" {
  cluster_name           = "event-stream-stage"
  kafka_version          = "2.4.1.1"
  number_of_broker_nodes = 3

  broker_node_group_info {
    instance_type   = "kafka.t3.small"
    ebs_volume_size = 300
    client_subnets  = var.default_subnets
    security_groups = [aws_security_group.msk_cluster.id]
  }

  encryption_info {
    encryption_at_rest_kms_key_arn = aws_kms_key.event_stream_kms.arn
    encryption_in_transit {
      client_broker = "TLS_PLAINTEXT"
    }
  }

  open_monitoring {
    prometheus {
      jmx_exporter {
        enabled_in_broker = false
      }
      node_exporter {
        enabled_in_broker = false
      }
    }
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled = false
      }
      firehose {
        enabled = false
      }
      s3 {
        enabled = false
      }
    }
  }

  configuration_info {
    arn      = aws_msk_configuration.auto_create_true.arn
    revision = "1"
  }

  tags = {
    Environment = "stage"
    Name        = "event-stream"
    Service     = "msk"
    Terraform   = "true"
  }
}
