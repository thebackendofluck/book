# Companion code for "The Backend of Luck" - Chapter 21, Caching Strategies and Benefits.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Chapter 38: Caching Strategies - Kubernetes Manifests
# Redis deployment for Kubernetes-based caching
#
# Features:
# - Redis Sentinel for HA (or Redis Cluster)
# - HPA for dynamic scaling
# - PodDisruptionBudget for availability
# - NetworkPolicies for security
# - Prometheus monitoring
#
# For use with EKS or self-managed Kubernetes

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

# -----------------------------------------------------------------------------
# Variables
# -----------------------------------------------------------------------------

variable "namespace" {
  description = "Kubernetes namespace"
  type        = string
  default     = "cache-system"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

variable "redis_replicas" {
  description = "Number of Redis replicas"
  type        = number
  default     = 3
}

variable "redis_memory_limit" {
  description = "Redis memory limit"
  type        = string
  default     = "4Gi"
}

variable "redis_cpu_limit" {
  description = "Redis CPU limit"
  type        = string
  default     = "2"
}

variable "enable_monitoring" {
  description = "Enable Prometheus monitoring"
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Namespace
# -----------------------------------------------------------------------------

resource "kubernetes_namespace" "cache" {
  metadata {
    name = var.namespace

    labels = {
      name        = var.namespace
      project     = "igaming"
      chapter     = "38-caching"
      environment = var.environment
    }

    annotations = {
      "scheduler.alpha.kubernetes.io/defaultTolerations" = jsonencode([
        {
          key      = "cache-node"
          operator = "Equal"
          value    = "true"
          effect   = "NoSchedule"
        }
      ])
    }
  }
}

# -----------------------------------------------------------------------------
# ConfigMap - Redis Configuration
# -----------------------------------------------------------------------------

resource "kubernetes_config_map" "redis_config" {
  metadata {
    name      = "redis-config"
    namespace = kubernetes_namespace.cache.metadata[0].name
  }

  data = {
    "redis.conf" = <<-EOF
      # Redis configuration for iGaming

      # Memory management
      maxmemory 3gb
      maxmemory-policy volatile-lru

      # Persistence (AOF for durability)
      appendonly yes
      appendfsync everysec
      auto-aof-rewrite-percentage 100
      auto-aof-rewrite-min-size 64mb

      # Networking
      tcp-keepalive 300
      timeout 0
      tcp-backlog 511

      # Performance
      hz 10
      dynamic-hz yes

      # Cluster settings
      cluster-enabled no
      cluster-require-full-coverage no

      # Security
      protected-mode yes

      # Logging
      loglevel notice

      # Keyspace notifications for cache invalidation
      notify-keyspace-events Ex

      # Slow log
      slowlog-log-slower-than 10000
      slowlog-max-len 128
    EOF

    "sentinel.conf" = <<-EOF
      # Sentinel configuration
      sentinel monitor mymaster redis-master 6379 2
      sentinel down-after-milliseconds mymaster 5000
      sentinel parallel-syncs mymaster 1
      sentinel failover-timeout mymaster 60000
      sentinel auth-pass mymaster ${random_password.redis_auth.result}
    EOF
  }
}

# Redis auth password
resource "random_password" "redis_auth" {
  length  = 32
  special = false
}

resource "kubernetes_secret" "redis_auth" {
  metadata {
    name      = "redis-auth"
    namespace = kubernetes_namespace.cache.metadata[0].name
  }

  data = {
    password = random_password.redis_auth.result
  }

  type = "Opaque"
}

# -----------------------------------------------------------------------------
# Redis Master StatefulSet
# -----------------------------------------------------------------------------

resource "kubernetes_stateful_set" "redis_master" {
  metadata {
    name      = "redis-master"
    namespace = kubernetes_namespace.cache.metadata[0].name

    labels = {
      app       = "redis"
      role      = "master"
      component = "cache"
    }
  }

  spec {
    service_name = "redis-master"
    replicas     = 1

    selector {
      match_labels = {
        app  = "redis"
        role = "master"
      }
    }

    template {
      metadata {
        labels = {
          app       = "redis"
          role      = "master"
          component = "cache"
        }

        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "9121"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.redis.metadata[0].name

        security_context {
          fs_group    = 1000
          run_as_user = 1000
        }

        container {
          name  = "redis"
          image = "redis:8-alpine"

          command = ["redis-server", "/etc/redis/redis.conf", "--requirepass", "$(REDIS_PASSWORD)"]

          port {
            container_port = 6379
            name           = "redis"
          }

          env {
            name = "REDIS_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.redis_auth.metadata[0].name
                key  = "password"
              }
            }
          }

          resources {
            requests = {
              cpu    = "500m"
              memory = "2Gi"
            }
            limits = {
              cpu    = var.redis_cpu_limit
              memory = var.redis_memory_limit
            }
          }

          volume_mount {
            name       = "redis-config"
            mount_path = "/etc/redis"
          }

          volume_mount {
            name       = "redis-data"
            mount_path = "/data"
          }

          liveness_probe {
            exec {
              command = ["redis-cli", "-a", "$(REDIS_PASSWORD)", "ping"]
            }
            initial_delay_seconds = 30
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          readiness_probe {
            exec {
              command = ["redis-cli", "-a", "$(REDIS_PASSWORD)", "ping"]
            }
            initial_delay_seconds = 5
            period_seconds        = 5
            timeout_seconds       = 3
            failure_threshold     = 3
          }

          security_context {
            run_as_non_root            = true
            run_as_user                = 1000
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
          }
        }

        # Redis exporter sidecar
        dynamic "container" {
          for_each = var.enable_monitoring ? [1] : []
          content {
            name  = "redis-exporter"
            image = "oliver006/redis_exporter:v1.55.0"

            port {
              container_port = 9121
              name           = "metrics"
            }

            env {
              name = "REDIS_PASSWORD"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret.redis_auth.metadata[0].name
                  key  = "password"
                }
              }
            }

            env {
              name  = "REDIS_ADDR"
              value = "redis://localhost:6379"
            }

            resources {
              requests = {
                cpu    = "50m"
                memory = "64Mi"
              }
              limits = {
                cpu    = "100m"
                memory = "128Mi"
              }
            }

            security_context {
              run_as_non_root            = true
              run_as_user                = 1000
              read_only_root_filesystem  = true
              allow_privilege_escalation = false
            }
          }
        }

        volume {
          name = "redis-config"
          config_map {
            name = kubernetes_config_map.redis_config.metadata[0].name
          }
        }

        toleration {
          key      = "cache-node"
          operator = "Equal"
          value    = "true"
          effect   = "NoSchedule"
        }

        affinity {
          pod_anti_affinity {
            required_during_scheduling_ignored_during_execution {
              label_selector {
                match_labels = {
                  app = "redis"
                }
              }
              topology_key = "kubernetes.io/hostname"
            }
          }
        }
      }
    }

    volume_claim_template {
      metadata {
        name = "redis-data"
      }

      spec {
        access_modes       = ["ReadWriteOnce"]
        storage_class_name = "gp3"

        resources {
          requests = {
            storage = "20Gi"
          }
        }
      }
    }
  }
}

# -----------------------------------------------------------------------------
# Redis Replicas StatefulSet
# -----------------------------------------------------------------------------

resource "kubernetes_stateful_set" "redis_replica" {
  metadata {
    name      = "redis-replica"
    namespace = kubernetes_namespace.cache.metadata[0].name

    labels = {
      app       = "redis"
      role      = "replica"
      component = "cache"
    }
  }

  spec {
    service_name = "redis-replica"
    replicas     = var.redis_replicas - 1

    selector {
      match_labels = {
        app  = "redis"
        role = "replica"
      }
    }

    template {
      metadata {
        labels = {
          app       = "redis"
          role      = "replica"
          component = "cache"
        }

        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "9121"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.redis.metadata[0].name

        security_context {
          fs_group    = 1000
          run_as_user = 1000
        }

        container {
          name  = "redis"
          image = "redis:8-alpine"

          command = [
            "redis-server",
            "/etc/redis/redis.conf",
            "--requirepass", "$(REDIS_PASSWORD)",
            "--replicaof", "redis-master", "6379",
            "--masterauth", "$(REDIS_PASSWORD)"
          ]

          port {
            container_port = 6379
            name           = "redis"
          }

          env {
            name = "REDIS_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.redis_auth.metadata[0].name
                key  = "password"
              }
            }
          }

          resources {
            requests = {
              cpu    = "500m"
              memory = "2Gi"
            }
            limits = {
              cpu    = var.redis_cpu_limit
              memory = var.redis_memory_limit
            }
          }

          volume_mount {
            name       = "redis-config"
            mount_path = "/etc/redis"
          }

          volume_mount {
            name       = "redis-data"
            mount_path = "/data"
          }

          liveness_probe {
            exec {
              command = ["redis-cli", "-a", "$(REDIS_PASSWORD)", "ping"]
            }
            initial_delay_seconds = 30
            period_seconds        = 10
          }

          readiness_probe {
            exec {
              command = ["redis-cli", "-a", "$(REDIS_PASSWORD)", "ping"]
            }
            initial_delay_seconds = 5
            period_seconds        = 5
          }

          security_context {
            run_as_non_root            = true
            run_as_user                = 1000
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
          }
        }

        volume {
          name = "redis-config"
          config_map {
            name = kubernetes_config_map.redis_config.metadata[0].name
          }
        }

        toleration {
          key      = "cache-node"
          operator = "Equal"
          value    = "true"
          effect   = "NoSchedule"
        }

        affinity {
          pod_anti_affinity {
            required_during_scheduling_ignored_during_execution {
              label_selector {
                match_labels = {
                  app = "redis"
                }
              }
              topology_key = "kubernetes.io/hostname"
            }
          }
        }
      }
    }

    volume_claim_template {
      metadata {
        name = "redis-data"
      }

      spec {
        access_modes       = ["ReadWriteOnce"]
        storage_class_name = "gp3"

        resources {
          requests = {
            storage = "20Gi"
          }
        }
      }
    }
  }
}

# -----------------------------------------------------------------------------
# Services
# -----------------------------------------------------------------------------

resource "kubernetes_service" "redis_master" {
  metadata {
    name      = "redis-master"
    namespace = kubernetes_namespace.cache.metadata[0].name

    labels = {
      app  = "redis"
      role = "master"
    }
  }

  spec {
    selector = {
      app  = "redis"
      role = "master"
    }

    port {
      port        = 6379
      target_port = 6379
      name        = "redis"
    }

    type = "ClusterIP"
  }
}

resource "kubernetes_service" "redis_replica" {
  metadata {
    name      = "redis-replica"
    namespace = kubernetes_namespace.cache.metadata[0].name

    labels = {
      app  = "redis"
      role = "replica"
    }
  }

  spec {
    selector = {
      app  = "redis"
      role = "replica"
    }

    port {
      port        = 6379
      target_port = 6379
      name        = "redis"
    }

    type = "ClusterIP"
  }
}

# Headless service for StatefulSet DNS
resource "kubernetes_service" "redis_headless" {
  metadata {
    name      = "redis-headless"
    namespace = kubernetes_namespace.cache.metadata[0].name

    labels = {
      app = "redis"
    }
  }

  spec {
    selector = {
      app = "redis"
    }

    port {
      port        = 6379
      target_port = 6379
      name        = "redis"
    }

    cluster_ip = "None"
  }
}

# -----------------------------------------------------------------------------
# Service Account and RBAC
# -----------------------------------------------------------------------------

resource "kubernetes_service_account" "redis" {
  metadata {
    name      = "redis"
    namespace = kubernetes_namespace.cache.metadata[0].name
  }
}

# -----------------------------------------------------------------------------
# PodDisruptionBudget
# -----------------------------------------------------------------------------

resource "kubernetes_pod_disruption_budget" "redis" {
  metadata {
    name      = "redis-pdb"
    namespace = kubernetes_namespace.cache.metadata[0].name
  }

  spec {
    min_available = "2"

    selector {
      match_labels = {
        app = "redis"
      }
    }
  }
}

# -----------------------------------------------------------------------------
# NetworkPolicies
# -----------------------------------------------------------------------------

# Default deny all
resource "kubernetes_network_policy" "default_deny" {
  metadata {
    name      = "default-deny-all"
    namespace = kubernetes_namespace.cache.metadata[0].name
  }

  spec {
    pod_selector {}

    policy_types = ["Ingress", "Egress"]
  }
}

# Allow Redis traffic
resource "kubernetes_network_policy" "allow_redis" {
  metadata {
    name      = "allow-redis-traffic"
    namespace = kubernetes_namespace.cache.metadata[0].name
  }

  spec {
    pod_selector {
      match_labels = {
        app = "redis"
      }
    }

    policy_types = ["Ingress", "Egress"]

    ingress {
      # Allow from application namespace
      from {
        namespace_selector {
          match_labels = {
            name = "igaming-app"
          }
        }
      }

      # Allow Redis replication
      from {
        pod_selector {
          match_labels = {
            app = "redis"
          }
        }
      }

      # Allow Prometheus scraping
      from {
        namespace_selector {
          match_labels = {
            name = "monitoring"
          }
        }
        pod_selector {
          match_labels = {
            app = "prometheus"
          }
        }
      }

      ports {
        port     = 6379
        protocol = "TCP"
      }
      ports {
        port     = 9121
        protocol = "TCP"
      }
    }

    egress {
      # Allow Redis replication
      to {
        pod_selector {
          match_labels = {
            app = "redis"
          }
        }
      }
      ports {
        port     = 6379
        protocol = "TCP"
      }

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
  }
}

# -----------------------------------------------------------------------------
# Cache Application Deployment (Cache Manager)
# -----------------------------------------------------------------------------

resource "kubernetes_deployment" "cache_manager" {
  metadata {
    name      = "cache-manager"
    namespace = kubernetes_namespace.cache.metadata[0].name

    labels = {
      app       = "cache-manager"
      component = "cache"
    }
  }

  spec {
    replicas = 2

    selector {
      match_labels = {
        app = "cache-manager"
      }
    }

    template {
      metadata {
        labels = {
          app       = "cache-manager"
          component = "cache"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.redis.metadata[0].name

        container {
          name  = "cache-manager"
          image = "igaming/cache-manager:1.0.0"

          port {
            container_port = 8080
            name           = "http"
          }

          port {
            container_port = 9090
            name           = "metrics"
          }

          env {
            name  = "REDIS_HOST"
            value = "redis-master"
          }

          env {
            name  = "REDIS_PORT"
            value = "6379"
          }

          env {
            name = "REDIS_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.redis_auth.metadata[0].name
                key  = "password"
              }
            }
          }

          env {
            name  = "CACHE_DEFAULT_TTL"
            value = "300"
          }

          env {
            name  = "ENABLE_STAMPEDE_PROTECTION"
            value = "true"
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "256Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "512Mi"
            }
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 8080
            }
            initial_delay_seconds = 10
            period_seconds        = 10
          }

          readiness_probe {
            http_get {
              path = "/ready"
              port = 8080
            }
            initial_delay_seconds = 5
            period_seconds        = 5
          }

          security_context {
            run_as_non_root            = true
            run_as_user                = 1000
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
          }
        }
      }
    }
  }
}

# HPA for cache manager
resource "kubernetes_horizontal_pod_autoscaler_v2" "cache_manager" {
  metadata {
    name      = "cache-manager-hpa"
    namespace = kubernetes_namespace.cache.metadata[0].name
  }

  spec {
    scale_target_ref {
      api_version = "apps/v1"
      kind        = "Deployment"
      name        = kubernetes_deployment.cache_manager.metadata[0].name
    }

    min_replicas = 2
    max_replicas = 10

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

    behavior {
      scale_down {
        stabilization_window_seconds = 300
        select_policy                = "Min"
        policy {
          type           = "Percent"
          value          = 10
          period_seconds = 60
        }
      }
      scale_up {
        stabilization_window_seconds = 0
        select_policy                = "Max"
        policy {
          type           = "Percent"
          value          = 100
          period_seconds = 15
        }
      }
    }
  }
}

# -----------------------------------------------------------------------------
# Prometheus ServiceMonitor
# -----------------------------------------------------------------------------

resource "kubernetes_manifest" "redis_service_monitor" {
  count = var.enable_monitoring ? 1 : 0

  manifest = {
    apiVersion = "monitoring.coreos.com/v1"
    kind       = "ServiceMonitor"

    metadata = {
      name      = "redis-monitor"
      namespace = kubernetes_namespace.cache.metadata[0].name
      labels = {
        app = "redis"
      }
    }

    spec = {
      selector = {
        matchLabels = {
          app = "redis"
        }
      }

      endpoints = [
        {
          port     = "metrics"
          interval = "15s"
          path     = "/metrics"
        }
      ]
    }
  }
}

# -----------------------------------------------------------------------------
# ResourceQuota
# -----------------------------------------------------------------------------

resource "kubernetes_resource_quota" "cache" {
  metadata {
    name      = "cache-quota"
    namespace = kubernetes_namespace.cache.metadata[0].name
  }

  spec {
    hard = {
      "requests.cpu"    = "10"
      "requests.memory" = "20Gi"
      "limits.cpu"      = "20"
      "limits.memory"   = "40Gi"
      "pods"            = "50"
    }
  }
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "namespace" {
  description = "Kubernetes namespace"
  value       = kubernetes_namespace.cache.metadata[0].name
}

output "redis_master_service" {
  description = "Redis master service name"
  value       = kubernetes_service.redis_master.metadata[0].name
}

output "redis_replica_service" {
  description = "Redis replica service name"
  value       = kubernetes_service.redis_replica.metadata[0].name
}

output "redis_connection_string" {
  description = "Redis connection string"
  value       = "redis://:${random_password.redis_auth.result}@redis-master.${kubernetes_namespace.cache.metadata[0].name}.svc.cluster.local:6379"
  sensitive   = true
}
