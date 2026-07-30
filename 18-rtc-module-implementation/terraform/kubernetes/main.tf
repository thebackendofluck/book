# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Chapter 40: RTC Module - Kubernetes Infrastructure
# =============================================================================
# Kubernetes deployments, services, and policies for RTC system.
#
# Components:
# - RTC API Service (Go) - timestamp generation and signing
# - RTC Consensus Service - multi-node consensus coordination
# - RTC Cache Manager - Redis interface for caching
# - RTC Audit Logger - compliance audit trail
# - Horizontal Pod Autoscalers for traffic spikes
# - Pod Disruption Budgets for high availability
# - Network Policies for security isolation
# - Ingress for load balancing
#
# Performance Targets:
# - P50 Latency: <500μs
# - P99 Latency: <2ms
# - Throughput: 100,000 requests/second
# - Availability: 99.999%
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.23"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.11"
    }
  }
}

# =============================================================================
# Variables
# =============================================================================

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "rtc-igaming"
}

variable "namespace" {
  description = "Kubernetes namespace for RTC services"
  type        = string
  default     = "rtc-system"
}

variable "rtc_api_replicas" {
  description = "Number of RTC API replicas"
  type        = number
  default     = 3
}

variable "rtc_api_image" {
  description = "Docker image for RTC API service"
  type        = string
  default     = "rtc-igaming/rtc-api:latest"
}

variable "rtc_consensus_image" {
  description = "Docker image for RTC consensus service"
  type        = string
  default     = "rtc-igaming/rtc-consensus:latest"
}

variable "redis_endpoint" {
  description = "ElastiCache Redis endpoint"
  type        = string
}

variable "postgres_host" {
  description = "RDS PostgreSQL host"
  type        = string
}

variable "postgres_secret_name" {
  description = "Kubernetes secret name for PostgreSQL credentials"
  type        = string
  default     = "rtc-postgres-credentials"
}

# =============================================================================
# Namespace
# =============================================================================

resource "kubernetes_namespace" "rtc_system" {
  metadata {
    name = var.namespace

    labels = {
      name        = var.namespace
      project     = var.project_name
      environment = "production"
      compliance  = "gli-11"
    }

    annotations = {
      "meta.helm.sh/release-name"      = "rtc-system"
      "meta.helm.sh/release-namespace" = var.namespace
    }
  }
}

# =============================================================================
# ConfigMaps
# =============================================================================

resource "kubernetes_config_map" "rtc_config" {
  metadata {
    name      = "rtc-config"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name

    labels = {
      app     = "rtc-api"
      project = var.project_name
    }
  }

  data = {
    "config.yaml" = yamlencode({
      server = {
        host            = "0.0.0.0"
        port            = 8080
        grpc_port       = 50051
        read_timeout    = "5s"
        write_timeout   = "5s"
        max_connections = 10000
      }

      rtc = {
        precision        = "microsecond"
        drift_threshold  = "100ms"
        consensus_nodes  = 3
        consensus_quorum = 2
        sync_interval    = "1s"
      }

      cache = {
        host          = var.redis_endpoint
        port          = 6379
        pool_size     = 100
        timeout       = "100ms"
        ttl_timestamp = "60s"
        ttl_consensus = "300s"
      }

      database = {
        host               = var.postgres_host
        port               = 5432
        database           = "rtc_timestamps"
        max_connections    = 50
        connection_timeout = "5s"
      }

      monitoring = {
        metrics_port    = 9090
        health_port     = 8081
        tracing_enabled = true
        log_level       = "info"
      }

      security = {
        tls_enabled         = true
        mtls_enabled        = true
        signature_algorithm = "HMAC-SHA256"
        key_rotation_days   = 90
      }

      compliance = {
        audit_enabled   = true
        audit_retention = "7y"
        jurisdiction    = ["UK", "Malta", "Gibraltar"]
      }
    })

    "time-sources.yaml" = yamlencode({
      sources = {
        primary = {
          type       = "rtc_consensus"
          priority   = 1
          confidence = 0.95
        }
        secondary = {
          type           = "gps"
          priority       = 2
          min_satellites = 4
        }
        tertiary = {
          type     = "ntp"
          priority = 3
          servers = [
            "time.google.com",
            "time.aws.com",
            "pool.ntp.org"
          ]
          max_drift = "100ms"
        }
      }

      failover = {
        timeout         = "1s"
        retry_attempts  = 3
        backoff_initial = "100ms"
        backoff_max     = "5s"
      }
    })
  }
}

# =============================================================================
# Secrets (reference to external secrets)
# =============================================================================

resource "kubernetes_secret" "rtc_tls" {
  metadata {
    name      = "rtc-tls"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name

    labels = {
      app     = "rtc-api"
      project = var.project_name
    }
  }

  type = "kubernetes.io/tls"

  # In production, use cert-manager or external secrets operator
  data = {
    "tls.crt" = "" # Populated by cert-manager
    "tls.key" = "" # Populated by cert-manager
  }

  lifecycle {
    ignore_changes = [data]
  }
}

# =============================================================================
# Service Account
# =============================================================================

resource "kubernetes_service_account" "rtc_api" {
  metadata {
    name      = "rtc-api"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name

    labels = {
      app     = "rtc-api"
      project = var.project_name
    }

    annotations = {
      # For AWS IAM roles for service accounts (IRSA)
      "eks.amazonaws.com/role-arn" = "arn:aws:iam::ACCOUNT_ID:role/rtc-api-role"
    }
  }
}

# =============================================================================
# RTC API Deployment
# =============================================================================

resource "kubernetes_deployment" "rtc_api" {
  metadata {
    name      = "rtc-api"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name

    labels = {
      app       = "rtc-api"
      component = "api"
      project   = var.project_name
      version   = "v1"
    }
  }

  spec {
    replicas = var.rtc_api_replicas

    selector {
      match_labels = {
        app       = "rtc-api"
        component = "api"
      }
    }

    strategy {
      type = "RollingUpdate"
      rolling_update {
        max_surge       = "25%"
        max_unavailable = "0"
      }
    }

    template {
      metadata {
        labels = {
          app       = "rtc-api"
          component = "api"
          project   = var.project_name
          version   = "v1"
        }

        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "9090"
          "prometheus.io/path"   = "/metrics"
        }
      }

      spec {
        service_account_name             = kubernetes_service_account.rtc_api.metadata[0].name
        automount_service_account_token  = true
        termination_grace_period_seconds = 30

        # Node selector for time-critical workloads
        node_selector = {
          "node-type" = "rtc"
        }

        # Toleration for RTC node taint
        toleration {
          key      = "workload"
          operator = "Equal"
          value    = "time-critical"
          effect   = "NoSchedule"
        }

        # Anti-affinity for HA across AZs
        affinity {
          pod_anti_affinity {
            required_during_scheduling_ignored_during_execution {
              label_selector {
                match_labels = {
                  app = "rtc-api"
                }
              }
              topology_key = "topology.kubernetes.io/zone"
            }
          }
        }

        # Security context
        security_context {
          run_as_non_root = true
          run_as_user     = 1000
          run_as_group    = 1000
          fs_group        = 1000
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name              = "rtc-api"
          image             = var.rtc_api_image
          image_pull_policy = "Always"

          port {
            name           = "http"
            container_port = 8080
            protocol       = "TCP"
          }

          port {
            name           = "grpc"
            container_port = 50051
            protocol       = "TCP"
          }

          port {
            name           = "metrics"
            container_port = 9090
            protocol       = "TCP"
          }

          port {
            name           = "health"
            container_port = 8081
            protocol       = "TCP"
          }

          env {
            name  = "CONFIG_PATH"
            value = "/etc/rtc/config.yaml"
          }

          env {
            name  = "TIME_SOURCES_PATH"
            value = "/etc/rtc/time-sources.yaml"
          }

          env {
            name = "POD_NAME"
            value_from {
              field_ref {
                field_path = "metadata.name"
              }
            }
          }

          env {
            name = "POD_NAMESPACE"
            value_from {
              field_ref {
                field_path = "metadata.namespace"
              }
            }
          }

          env {
            name = "NODE_NAME"
            value_from {
              field_ref {
                field_path = "spec.nodeName"
              }
            }
          }

          env {
            name = "DB_PASSWORD"
            value_from {
              secret_key_ref {
                name = var.postgres_secret_name
                key  = "password"
              }
            }
          }

          # Resource limits for predictable performance
          resources {
            requests = {
              cpu    = "500m"
              memory = "512Mi"
            }
            limits = {
              cpu    = "2000m"
              memory = "2Gi"
            }
          }

          # Liveness probe
          liveness_probe {
            http_get {
              path = "/health/live"
              port = 8081
            }
            initial_delay_seconds = 10
            period_seconds        = 5
            timeout_seconds       = 2
            failure_threshold     = 3
          }

          # Readiness probe
          readiness_probe {
            http_get {
              path = "/health/ready"
              port = 8081
            }
            initial_delay_seconds = 5
            period_seconds        = 3
            timeout_seconds       = 2
            failure_threshold     = 3
          }

          # Startup probe for slow-starting containers
          startup_probe {
            http_get {
              path = "/health/startup"
              port = 8081
            }
            initial_delay_seconds = 5
            period_seconds        = 5
            timeout_seconds       = 2
            failure_threshold     = 30
          }

          volume_mount {
            name       = "config"
            mount_path = "/etc/rtc"
            read_only  = true
          }

          volume_mount {
            name       = "tls"
            mount_path = "/etc/tls"
            read_only  = true
          }

          # Security context for container
          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            run_as_non_root            = true
            run_as_user                = 1000
            capabilities {
              drop = ["ALL"]
            }
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map.rtc_config.metadata[0].name
          }
        }

        volume {
          name = "tls"
          secret {
            secret_name = kubernetes_secret.rtc_tls.metadata[0].name
          }
        }
      }
    }
  }
}

# =============================================================================
# RTC Consensus Deployment
# =============================================================================

resource "kubernetes_deployment" "rtc_consensus" {
  metadata {
    name      = "rtc-consensus"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name

    labels = {
      app       = "rtc-consensus"
      component = "consensus"
      project   = var.project_name
    }
  }

  spec {
    replicas = 3 # Always 3 for consensus quorum

    selector {
      match_labels = {
        app       = "rtc-consensus"
        component = "consensus"
      }
    }

    strategy {
      type = "RollingUpdate"
      rolling_update {
        max_surge       = "1"
        max_unavailable = "0"
      }
    }

    template {
      metadata {
        labels = {
          app       = "rtc-consensus"
          component = "consensus"
          project   = var.project_name
        }

        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "9090"
        }
      }

      spec {
        service_account_name             = kubernetes_service_account.rtc_api.metadata[0].name
        termination_grace_period_seconds = 60

        node_selector = {
          "node-type" = "rtc"
        }

        toleration {
          key      = "workload"
          operator = "Equal"
          value    = "time-critical"
          effect   = "NoSchedule"
        }

        # Strict anti-affinity - one per zone
        affinity {
          pod_anti_affinity {
            required_during_scheduling_ignored_during_execution {
              label_selector {
                match_labels = {
                  app = "rtc-consensus"
                }
              }
              topology_key = "topology.kubernetes.io/zone"
            }
          }
        }

        security_context {
          run_as_non_root = true
          run_as_user     = 1000
          run_as_group    = 1000
          fs_group        = 1000
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name              = "rtc-consensus"
          image             = var.rtc_consensus_image
          image_pull_policy = "Always"

          port {
            name           = "raft"
            container_port = 7000
            protocol       = "TCP"
          }

          port {
            name           = "metrics"
            container_port = 9090
            protocol       = "TCP"
          }

          env {
            name  = "CONSENSUS_ALGORITHM"
            value = "raft"
          }

          env {
            name  = "CONSENSUS_QUORUM"
            value = "2"
          }

          env {
            name = "POD_NAME"
            value_from {
              field_ref {
                field_path = "metadata.name"
              }
            }
          }

          env {
            name = "POD_IP"
            value_from {
              field_ref {
                field_path = "status.podIP"
              }
            }
          }

          resources {
            requests = {
              cpu    = "250m"
              memory = "256Mi"
            }
            limits = {
              cpu    = "1000m"
              memory = "1Gi"
            }
          }

          liveness_probe {
            tcp_socket {
              port = 7000
            }
            initial_delay_seconds = 10
            period_seconds        = 5
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/consensus/ready"
              port = 9090
            }
            initial_delay_seconds = 5
            period_seconds        = 3
            failure_threshold     = 3
          }

          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            run_as_non_root            = true
            run_as_user                = 1000
            capabilities {
              drop = ["ALL"]
            }
          }
        }
      }
    }
  }
}

# =============================================================================
# RTC Audit Logger Deployment
# =============================================================================

resource "kubernetes_deployment" "rtc_audit_logger" {
  metadata {
    name      = "rtc-audit-logger"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name

    labels = {
      app       = "rtc-audit-logger"
      component = "audit"
      project   = var.project_name
    }
  }

  spec {
    replicas = 2

    selector {
      match_labels = {
        app       = "rtc-audit-logger"
        component = "audit"
      }
    }

    template {
      metadata {
        labels = {
          app       = "rtc-audit-logger"
          component = "audit"
          project   = var.project_name
        }
      }

      spec {
        service_account_name = kubernetes_service_account.rtc_api.metadata[0].name

        affinity {
          pod_anti_affinity {
            preferred_during_scheduling_ignored_during_execution {
              weight = 100
              pod_affinity_term {
                label_selector {
                  match_labels = {
                    app = "rtc-audit-logger"
                  }
                }
                topology_key = "topology.kubernetes.io/zone"
              }
            }
          }
        }

        security_context {
          run_as_non_root = true
          run_as_user     = 1000
          fs_group        = 1000
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name  = "audit-logger"
          image = "rtc-igaming/rtc-audit-logger:latest"

          env {
            name  = "AUDIT_RETENTION_YEARS"
            value = "7"
          }

          env {
            name  = "S3_BUCKET"
            value = "${var.project_name}-audit-logs"
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "128Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "512Mi"
            }
          }

          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            capabilities {
              drop = ["ALL"]
            }
          }
        }
      }
    }
  }
}

# =============================================================================
# Services
# =============================================================================

resource "kubernetes_service" "rtc_api" {
  metadata {
    name      = "rtc-api"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name

    labels = {
      app     = "rtc-api"
      project = var.project_name
    }

    annotations = {
      "service.beta.kubernetes.io/aws-load-balancer-type"                              = "nlb"
      "service.beta.kubernetes.io/aws-load-balancer-cross-zone-load-balancing-enabled" = "true"
    }
  }

  spec {
    type = "ClusterIP"

    selector = {
      app       = "rtc-api"
      component = "api"
    }

    port {
      name        = "http"
      port        = 80
      target_port = 8080
      protocol    = "TCP"
    }

    port {
      name        = "grpc"
      port        = 50051
      target_port = 50051
      protocol    = "TCP"
    }

    session_affinity = "None"
  }
}

resource "kubernetes_service" "rtc_consensus" {
  metadata {
    name      = "rtc-consensus"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name

    labels = {
      app     = "rtc-consensus"
      project = var.project_name
    }
  }

  spec {
    type       = "ClusterIP"
    cluster_ip = "None" # Headless service for StatefulSet-like behavior

    selector = {
      app       = "rtc-consensus"
      component = "consensus"
    }

    port {
      name        = "raft"
      port        = 7000
      target_port = 7000
      protocol    = "TCP"
    }
  }
}

# =============================================================================
# Horizontal Pod Autoscalers
# =============================================================================

resource "kubernetes_horizontal_pod_autoscaler_v2" "rtc_api" {
  metadata {
    name      = "rtc-api-hpa"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name
  }

  spec {
    scale_target_ref {
      api_version = "apps/v1"
      kind        = "Deployment"
      name        = kubernetes_deployment.rtc_api.metadata[0].name
    }

    min_replicas = 3
    max_replicas = 20

    metric {
      type = "Resource"
      resource {
        name = "cpu"
        target {
          type                = "Utilization"
          average_utilization = 70
        }
      }
    }

    metric {
      type = "Resource"
      resource {
        name = "memory"
        target {
          type                = "Utilization"
          average_utilization = 80
        }
      }
    }

    # Custom metric for request latency
    metric {
      type = "Pods"
      pods {
        metric {
          name = "rtc_request_latency_p99"
        }
        target {
          type          = "AverageValue"
          average_value = "2m" # 2ms P99 latency threshold
        }
      }
    }

    behavior {
      scale_up {
        stabilization_window_seconds = 30
        select_policy                = "Max"
        policy {
          type           = "Percent"
          value          = 100
          period_seconds = 15
        }
        policy {
          type           = "Pods"
          value          = 4
          period_seconds = 15
        }
      }

      scale_down {
        stabilization_window_seconds = 300
        select_policy                = "Min"
        policy {
          type           = "Percent"
          value          = 10
          period_seconds = 60
        }
      }
    }
  }
}

# =============================================================================
# Pod Disruption Budgets
# =============================================================================

resource "kubernetes_pod_disruption_budget" "rtc_api" {
  metadata {
    name      = "rtc-api-pdb"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name
  }

  spec {
    min_available = "2"

    selector {
      match_labels = {
        app = "rtc-api"
      }
    }
  }
}

resource "kubernetes_pod_disruption_budget" "rtc_consensus" {
  metadata {
    name      = "rtc-consensus-pdb"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name
  }

  spec {
    min_available = "2" # Must maintain quorum

    selector {
      match_labels = {
        app = "rtc-consensus"
      }
    }
  }
}

# =============================================================================
# Network Policies
# =============================================================================

resource "kubernetes_network_policy" "rtc_api_ingress" {
  metadata {
    name      = "rtc-api-ingress"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name
  }

  spec {
    pod_selector {
      match_labels = {
        app = "rtc-api"
      }
    }

    policy_types = ["Ingress", "Egress"]

    # Ingress rules
    ingress {
      # Allow from ingress controller
      from {
        namespace_selector {
          match_labels = {
            name = "ingress-nginx"
          }
        }
      }
      ports {
        port     = 8080
        protocol = "TCP"
      }
      ports {
        port     = 50051
        protocol = "TCP"
      }
    }

    # Allow metrics scraping from monitoring namespace
    ingress {
      from {
        namespace_selector {
          match_labels = {
            name = "monitoring"
          }
        }
      }
      ports {
        port     = 9090
        protocol = "TCP"
      }
    }

    # Egress rules
    egress {
      # Allow to Redis (ElastiCache)
      to {
        ip_block {
          cidr = "10.40.0.0/16" # VPC CIDR
        }
      }
      ports {
        port     = 6379
        protocol = "TCP"
      }
    }

    egress {
      # Allow to PostgreSQL (RDS)
      to {
        ip_block {
          cidr = "10.40.0.0/16"
        }
      }
      ports {
        port     = 5432
        protocol = "TCP"
      }
    }

    egress {
      # Allow to consensus service
      to {
        pod_selector {
          match_labels = {
            app = "rtc-consensus"
          }
        }
      }
      ports {
        port     = 7000
        protocol = "TCP"
      }
    }

    egress {
      # Allow DNS
      to {
        namespace_selector {}
      }
      ports {
        port     = 53
        protocol = "UDP"
      }
      ports {
        port     = 53
        protocol = "TCP"
      }
    }

    egress {
      # Allow NTP for time validation
      to {
        ip_block {
          cidr = "0.0.0.0/0"
        }
      }
      ports {
        port     = 123
        protocol = "UDP"
      }
    }
  }
}

resource "kubernetes_network_policy" "rtc_consensus_policy" {
  metadata {
    name      = "rtc-consensus-policy"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name
  }

  spec {
    pod_selector {
      match_labels = {
        app = "rtc-consensus"
      }
    }

    policy_types = ["Ingress", "Egress"]

    ingress {
      # Allow from RTC API
      from {
        pod_selector {
          match_labels = {
            app = "rtc-api"
          }
        }
      }
      ports {
        port     = 7000
        protocol = "TCP"
      }
    }

    ingress {
      # Allow peer-to-peer Raft communication
      from {
        pod_selector {
          match_labels = {
            app = "rtc-consensus"
          }
        }
      }
      ports {
        port     = 7000
        protocol = "TCP"
      }
    }

    egress {
      # Allow peer communication
      to {
        pod_selector {
          match_labels = {
            app = "rtc-consensus"
          }
        }
      }
      ports {
        port     = 7000
        protocol = "TCP"
      }
    }

    egress {
      # Allow DNS
      ports {
        port     = 53
        protocol = "UDP"
      }
    }
  }
}

# =============================================================================
# Ingress
# =============================================================================

resource "kubernetes_ingress_v1" "rtc_api" {
  metadata {
    name      = "rtc-api-ingress"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name

    annotations = {
      "kubernetes.io/ingress.class"                    = "nginx"
      "nginx.ingress.kubernetes.io/ssl-redirect"       = "true"
      "nginx.ingress.kubernetes.io/backend-protocol"   = "HTTP"
      "nginx.ingress.kubernetes.io/proxy-body-size"    = "10m"
      "nginx.ingress.kubernetes.io/proxy-read-timeout" = "60"
      "nginx.ingress.kubernetes.io/limit-rps"          = "1000"
      "nginx.ingress.kubernetes.io/limit-connections"  = "100"
      "cert-manager.io/cluster-issuer"                 = "letsencrypt-prod"
    }
  }

  spec {
    tls {
      hosts       = ["rtc.igaming-platform.com"]
      secret_name = "rtc-tls-cert"
    }

    rule {
      host = "rtc.igaming-platform.com"

      http {
        path {
          path      = "/api/v1/timestamp"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.rtc_api.metadata[0].name
              port {
                number = 80
              }
            }
          }
        }

        path {
          path      = "/health"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.rtc_api.metadata[0].name
              port {
                number = 80
              }
            }
          }
        }

        path {
          path      = "/metrics"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.rtc_api.metadata[0].name
              port {
                number = 80
              }
            }
          }
        }
      }
    }
  }
}

# =============================================================================
# Resource Quotas
# =============================================================================

resource "kubernetes_resource_quota" "rtc_system" {
  metadata {
    name      = "rtc-system-quota"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name
  }

  spec {
    hard = {
      "requests.cpu"    = "20"
      "requests.memory" = "20Gi"
      "limits.cpu"      = "40"
      "limits.memory"   = "40Gi"
      "pods"            = "50"
      "services"        = "10"
      "secrets"         = "20"
      "configmaps"      = "20"
    }
  }
}

# =============================================================================
# Limit Ranges
# =============================================================================

resource "kubernetes_limit_range" "rtc_system" {
  metadata {
    name      = "rtc-system-limits"
    namespace = kubernetes_namespace.rtc_system.metadata[0].name
  }

  spec {
    limit {
      type = "Container"

      default = {
        cpu    = "1000m"
        memory = "1Gi"
      }

      default_request = {
        cpu    = "100m"
        memory = "128Mi"
      }

      min = {
        cpu    = "50m"
        memory = "64Mi"
      }

      max = {
        cpu    = "4000m"
        memory = "8Gi"
      }
    }

    limit {
      type = "PersistentVolumeClaim"

      min = {
        storage = "1Gi"
      }

      max = {
        storage = "100Gi"
      }
    }
  }
}

# =============================================================================
# Outputs
# =============================================================================

output "namespace" {
  description = "Kubernetes namespace for RTC system"
  value       = kubernetes_namespace.rtc_system.metadata[0].name
}

output "rtc_api_service" {
  description = "RTC API service name"
  value       = kubernetes_service.rtc_api.metadata[0].name
}

output "rtc_consensus_service" {
  description = "RTC consensus service name"
  value       = kubernetes_service.rtc_consensus.metadata[0].name
}

output "ingress_host" {
  description = "Ingress hostname"
  value       = kubernetes_ingress_v1.rtc_api.spec[0].rule[0].host
}
