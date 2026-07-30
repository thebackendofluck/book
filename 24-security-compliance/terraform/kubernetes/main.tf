# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Chapter 29: Security and Compliance - Kubernetes Security Infrastructure
# =============================================================================
# This Terraform configuration deploys comprehensive Kubernetes security:
# - Pod Security Policies (via admission controllers)
# - Network Policies for micro-segmentation
# - Security scanning deployments (Trivy, Falco)
# - RBAC configurations
# - Service mesh integration preparation
#
# Compliance: PCI DSS, GDPR, CIS Kubernetes Benchmark
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.23"
    }
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.11"
    }
  }
}

# =============================================================================
# Variables
# =============================================================================

variable "namespace" {
  description = "Kubernetes namespace for security tools"
  type        = string
  default     = "security-system"
}

variable "app_namespace" {
  description = "Application namespace to protect"
  type        = string
  default     = "igaming-prod"
}

variable "enable_falco" {
  description = "Enable Falco runtime security"
  type        = bool
  default     = true
}

variable "enable_trivy" {
  description = "Enable Trivy vulnerability scanning"
  type        = bool
  default     = true
}

variable "enable_network_policies" {
  description = "Enable NetworkPolicies for micro-segmentation"
  type        = bool
  default     = true
}

variable "ids_replicas" {
  description = "Number of IDS/IPS deployment replicas"
  type        = number
  default     = 3
}

variable "redis_host" {
  description = "Redis host for IDS alert storage"
  type        = string
  default     = "redis-master.redis.svc.cluster.local"
}

variable "slack_webhook_url" {
  description = "Slack webhook for security alerts"
  type        = string
  default     = ""
  sensitive   = true
}

variable "common_labels" {
  description = "Common labels for all resources"
  type        = map(string)
  default = {
    "app.kubernetes.io/part-of"    = "security-system"
    "app.kubernetes.io/managed-by" = "terraform"
    "compliance/pci-dss"           = "true"
  }
}

# =============================================================================
# Namespaces
# =============================================================================

resource "kubernetes_namespace" "security" {
  metadata {
    name = var.namespace

    labels = merge(var.common_labels, {
      "pod-security.kubernetes.io/enforce" = "restricted"
      "pod-security.kubernetes.io/audit"   = "restricted"
      "pod-security.kubernetes.io/warn"    = "restricted"
    })

    annotations = {
      "description" = "Security tools and monitoring namespace"
    }
  }
}

# =============================================================================
# ConfigMaps
# =============================================================================

resource "kubernetes_config_map" "ids_config" {
  metadata {
    name      = "ids-config"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  data = {
    "config.yaml" = yamlencode({
      redis = {
        host = var.redis_host
        port = 6379
        db   = 2
      }
      alerting = {
        slack_enabled = var.slack_webhook_url != ""
        email_enabled = true
      }
      thresholds = {
        requests_per_minute      = 1000
        failed_logins_per_minute = 50
        suspicious_ips_per_hour  = 10
        bonus_abuse_attempts     = 20
      }
      rules = {
        sql_injection_enabled    = true
        xss_detection_enabled    = true
        path_traversal_enabled   = true
        bonus_abuse_enabled      = true
        money_laundering_enabled = true
      }
    })

    "detection-rules.yaml" = yamlencode({
      rules = [
        {
          id       = "SQL-001"
          name     = "SQL Injection Attempt"
          pattern  = "(\\x27|\\x2D\\x2D|\\x3B)"
          severity = "HIGH"
          category = "injection"
          action   = "block"
        },
        {
          id       = "XSS-001"
          name     = "Cross-Site Scripting"
          pattern  = "(<script|javascript:|on\\w+\\s*=)"
          severity = "HIGH"
          category = "xss"
          action   = "block"
        },
        {
          id       = "FRAUD-001"
          name     = "Bonus Abuse Pattern"
          pattern  = "(multiple.*bonus|bonus.*farm)"
          severity = "MEDIUM"
          category = "gambling_fraud"
          action   = "alert"
        },
        {
          id       = "AML-001"
          name     = "Money Laundering Indicator"
          pattern  = "(large.*deposit.*withdraw|rapid.*transfer)"
          severity = "CRITICAL"
          category = "financial_crime"
          action   = "quarantine"
        }
      ]
    })
  }
}

resource "kubernetes_config_map" "network_monitor_config" {
  metadata {
    name      = "network-monitor-config"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  data = {
    "config.yaml" = yamlencode({
      monitoring = {
        interface      = "eth0"
        capture_filter = "tcp port 80 or tcp port 443"
        threshold      = 95.0
      }
      alerting = {
        cooldown_minutes = 15
        recipients       = ["security@company.com"]
      }
      compliance = {
        pci_dss_enabled = true
        gdpr_enabled    = true
      }
    })
  }
}

resource "kubernetes_config_map" "cis_scanner_config" {
  metadata {
    name      = "cis-scanner-config"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  data = {
    "controls.yaml" = yamlencode({
      cis_version = "1.6.0"
      enabled_controls = [
        "1.1.1", "1.1.2",
        "2.1", "2.2", "2.3", "2.4",
        "4.1", "4.2", "4.4", "4.6",
        "5.1", "5.2", "5.4", "5.7", "5.12", "5.25", "5.28"
      ]
      schedule = "0 2 * * *" # Daily at 2 AM
    })
  }
}

# =============================================================================
# Secrets
# =============================================================================

resource "kubernetes_secret" "ids_credentials" {
  metadata {
    name      = "ids-credentials"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  data = {
    redis-password = base64encode("changeme-in-production")
    slack-webhook  = base64encode(var.slack_webhook_url)
  }

  type = "Opaque"
}

# =============================================================================
# IDS/IPS Deployment
# =============================================================================

resource "kubernetes_deployment" "ids" {
  metadata {
    name      = "gambling-ids"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels = merge(var.common_labels, {
      "app.kubernetes.io/name"      = "gambling-ids"
      "app.kubernetes.io/component" = "security"
    })
  }

  spec {
    replicas = var.ids_replicas

    selector {
      match_labels = {
        "app.kubernetes.io/name" = "gambling-ids"
      }
    }

    template {
      metadata {
        labels = merge(var.common_labels, {
          "app.kubernetes.io/name" = "gambling-ids"
        })

        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "8080"
          "prometheus.io/path"   = "/metrics"
        }
      }

      spec {
        service_account_name            = kubernetes_service_account.ids.metadata[0].name
        automount_service_account_token = false

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
          name              = "ids"
          image             = "ghcr.io/igaming/gambling-ids:1.0.0"
          image_pull_policy = "Always" # CKV_K8S_15: Ensure image pull policy is Always

          port {
            container_port = 8080
            name           = "http"
            protocol       = "TCP"
          }

          port {
            container_port = 9090
            name           = "metrics"
            protocol       = "TCP"
          }

          env {
            name = "REDIS_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.ids_credentials.metadata[0].name
                key  = "redis-password"
              }
            }
          }

          env {
            name = "SLACK_WEBHOOK"
            value_from {
              secret_key_ref {
                name     = kubernetes_secret.ids_credentials.metadata[0].name
                key      = "slack-webhook"
                optional = true
              }
            }
          }

          env {
            name  = "LOG_LEVEL"
            value = "INFO"
          }

          resources {
            requests = {
              cpu    = "250m"
              memory = "512Mi"
            }
            limits = {
              cpu    = "1000m"
              memory = "1Gi"
            }
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

          volume_mount {
            name       = "config"
            mount_path = "/app/config"
            read_only  = true
          }

          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }

          liveness_probe {
            http_get {
              path = "/health/live"
              port = 8080
            }
            initial_delay_seconds = 30
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/health/ready"
              port = 8080
            }
            initial_delay_seconds = 10
            period_seconds        = 5
            timeout_seconds       = 3
            failure_threshold     = 3
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map.ids_config.metadata[0].name
          }
        }

        volume {
          name = "tmp"
          empty_dir {}
        }

        topology_spread_constraint {
          max_skew           = 1
          topology_key       = "topology.kubernetes.io/zone"
          when_unsatisfiable = "ScheduleAnyway"

          label_selector {
            match_labels = {
              "app.kubernetes.io/name" = "gambling-ids"
            }
          }
        }

        affinity {
          pod_anti_affinity {
            preferred_during_scheduling_ignored_during_execution {
              weight = 100
              pod_affinity_term {
                topology_key = "kubernetes.io/hostname"
                label_selector {
                  match_labels = {
                    "app.kubernetes.io/name" = "gambling-ids"
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}

# =============================================================================
# Network Monitor Deployment
# =============================================================================

resource "kubernetes_deployment" "network_monitor" {
  metadata {
    name      = "network-monitor"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels = merge(var.common_labels, {
      "app.kubernetes.io/name"      = "network-monitor"
      "app.kubernetes.io/component" = "monitoring"
    })
  }

  spec {
    replicas = 2

    selector {
      match_labels = {
        "app.kubernetes.io/name" = "network-monitor"
      }
    }

    template {
      metadata {
        labels = merge(var.common_labels, {
          "app.kubernetes.io/name" = "network-monitor"
        })
      }

      spec {
        service_account_name = kubernetes_service_account.network_monitor.metadata[0].name

        security_context {
          run_as_non_root = true
          run_as_user     = 1000
          fs_group        = 1000
        }

        container {
          name              = "monitor"
          image             = "ghcr.io/igaming/network-monitor:1.0.0"
          image_pull_policy = "Always" # CKV_K8S_15: Ensure image pull policy is Always

          port {
            container_port = 5000
            name           = "dashboard"
          }

          port {
            container_port = 9090
            name           = "metrics"
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

          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            run_as_non_root            = true

            capabilities {
              drop = ["ALL"]
            }
          }

          volume_mount {
            name       = "config"
            mount_path = "/app/config"
            read_only  = true
          }

          volume_mount {
            name       = "data"
            mount_path = "/app/data"
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 5000
            }
            initial_delay_seconds = 15
            period_seconds        = 10
          }

          readiness_probe {
            http_get {
              path = "/health"
              port = 5000
            }
            initial_delay_seconds = 5
            period_seconds        = 5
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map.network_monitor_config.metadata[0].name
          }
        }

        volume {
          name = "data"
          empty_dir {
            size_limit = "1Gi"
          }
        }
      }
    }
  }
}

# =============================================================================
# CIS Scanner CronJob
# =============================================================================

resource "kubernetes_cron_job_v1" "cis_scanner" {
  metadata {
    name      = "cis-docker-scanner"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  spec {
    schedule                      = "0 2 * * *" # Daily at 2 AM
    concurrency_policy            = "Forbid"
    successful_jobs_history_limit = 3
    failed_jobs_history_limit     = 3

    job_template {
      metadata {
        labels = var.common_labels
      }

      spec {
        ttl_seconds_after_finished = 86400 # Clean up after 24 hours

        template {
          metadata {
            labels = var.common_labels
          }

          spec {
            service_account_name = kubernetes_service_account.cis_scanner.metadata[0].name
            restart_policy       = "OnFailure"

            security_context {
              run_as_non_root = true
              run_as_user     = 1000
            }

            container {
              name  = "scanner"
              image = "ghcr.io/igaming/cis-scanner:1.0.0"

              command = ["/bin/sh", "-c"]
              args    = ["python -m cis_scanner --output /reports/scan-$(date +%Y%m%d).json"]

              resources {
                requests = {
                  cpu    = "200m"
                  memory = "256Mi"
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

              volume_mount {
                name       = "config"
                mount_path = "/app/config"
                read_only  = true
              }

              volume_mount {
                name       = "reports"
                mount_path = "/reports"
              }

              volume_mount {
                name       = "docker-sock"
                mount_path = "/var/run/docker.sock"
                read_only  = true
              }
            }

            volume {
              name = "config"
              config_map {
                name = kubernetes_config_map.cis_scanner_config.metadata[0].name
              }
            }

            volume {
              name = "reports"
              empty_dir {}
            }

            volume {
              name = "docker-sock"
              host_path {
                path = "/var/run/docker.sock"
                type = "Socket"
              }
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

resource "kubernetes_service" "ids" {
  metadata {
    name      = "gambling-ids"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  spec {
    selector = {
      "app.kubernetes.io/name" = "gambling-ids"
    }

    port {
      name        = "http"
      port        = 80
      target_port = 8080
      protocol    = "TCP"
    }

    port {
      name        = "metrics"
      port        = 9090
      target_port = 9090
      protocol    = "TCP"
    }

    type = "ClusterIP"
  }
}

resource "kubernetes_service" "network_monitor" {
  metadata {
    name      = "network-monitor"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  spec {
    selector = {
      "app.kubernetes.io/name" = "network-monitor"
    }

    port {
      name        = "dashboard"
      port        = 80
      target_port = 5000
    }

    port {
      name        = "metrics"
      port        = 9090
      target_port = 9090
    }

    type = "ClusterIP"
  }
}

# =============================================================================
# Service Accounts
# =============================================================================

resource "kubernetes_service_account" "ids" {
  metadata {
    name      = "gambling-ids"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  automount_service_account_token = false
}

resource "kubernetes_service_account" "network_monitor" {
  metadata {
    name      = "network-monitor"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  automount_service_account_token = false
}

resource "kubernetes_service_account" "cis_scanner" {
  metadata {
    name      = "cis-scanner"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }
}

# =============================================================================
# RBAC - Roles and Bindings
# =============================================================================

resource "kubernetes_role" "security_reader" {
  metadata {
    name      = "security-reader"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  rule {
    api_groups = [""]
    resources  = ["pods", "services", "configmaps"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments", "replicasets"]
    verbs      = ["get", "list", "watch"]
  }
}

resource "kubernetes_cluster_role" "cis_scanner" {
  metadata {
    name   = "cis-scanner"
    labels = var.common_labels
  }

  rule {
    api_groups = [""]
    resources  = ["nodes", "pods", "namespaces", "services"]
    verbs      = ["get", "list"]
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments", "daemonsets", "statefulsets"]
    verbs      = ["get", "list"]
  }

  rule {
    api_groups = ["batch"]
    resources  = ["cronjobs", "jobs"]
    verbs      = ["get", "list"]
  }

  rule {
    api_groups = ["networking.k8s.io"]
    resources  = ["networkpolicies"]
    verbs      = ["get", "list"]
  }
}

resource "kubernetes_cluster_role_binding" "cis_scanner" {
  metadata {
    name   = "cis-scanner"
    labels = var.common_labels
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.cis_scanner.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.cis_scanner.metadata[0].name
    namespace = kubernetes_namespace.security.metadata[0].name
  }
}

# =============================================================================
# Network Policies
# =============================================================================

# Default deny all ingress in security namespace
resource "kubernetes_network_policy" "default_deny_ingress" {
  count = var.enable_network_policies ? 1 : 0

  metadata {
    name      = "default-deny-ingress"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  spec {
    pod_selector {}

    policy_types = ["Ingress"]
  }
}

# Allow IDS to receive traffic from ingress controller
resource "kubernetes_network_policy" "ids_ingress" {
  count = var.enable_network_policies ? 1 : 0

  metadata {
    name      = "ids-ingress"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  spec {
    pod_selector {
      match_labels = {
        "app.kubernetes.io/name" = "gambling-ids"
      }
    }

    policy_types = ["Ingress", "Egress"]

    ingress {
      from {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = "ingress-nginx"
          }
        }
      }

      ports {
        port     = 8080
        protocol = "TCP"
      }
    }

    # Allow Prometheus scraping
    ingress {
      from {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = "monitoring"
          }
        }
      }

      ports {
        port     = 9090
        protocol = "TCP"
      }
    }

    egress {
      # To Redis
      to {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = "redis"
          }
        }
      }

      ports {
        port     = 6379
        protocol = "TCP"
      }
    }

    egress {
      # DNS
      to {
        namespace_selector {}
      }

      ports {
        port     = 53
        protocol = "UDP"
      }
    }
  }
}

# Network monitor policy
resource "kubernetes_network_policy" "network_monitor" {
  count = var.enable_network_policies ? 1 : 0

  metadata {
    name      = "network-monitor"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  spec {
    pod_selector {
      match_labels = {
        "app.kubernetes.io/name" = "network-monitor"
      }
    }

    policy_types = ["Ingress", "Egress"]

    ingress {
      from {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = "monitoring"
          }
        }
      }

      ports {
        port     = 5000
        protocol = "TCP"
      }

      ports {
        port     = 9090
        protocol = "TCP"
      }
    }

    egress {
      # DNS
      ports {
        port     = 53
        protocol = "UDP"
      }
    }
  }
}

# =============================================================================
# Horizontal Pod Autoscaler
# =============================================================================

resource "kubernetes_horizontal_pod_autoscaler_v2" "ids" {
  metadata {
    name      = "gambling-ids"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  spec {
    scale_target_ref {
      api_version = "apps/v1"
      kind        = "Deployment"
      name        = kubernetes_deployment.ids.metadata[0].name
    }

    min_replicas = var.ids_replicas
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
        stabilization_window_seconds = 60
        select_policy                = "Max"

        policy {
          type           = "Percent"
          value          = 100
          period_seconds = 30
        }

        policy {
          type           = "Pods"
          value          = 4
          period_seconds = 30
        }
      }
    }
  }
}

# =============================================================================
# Pod Disruption Budget
# =============================================================================

resource "kubernetes_pod_disruption_budget_v1" "ids" {
  metadata {
    name      = "gambling-ids"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  spec {
    min_available = "50%"

    selector {
      match_labels = {
        "app.kubernetes.io/name" = "gambling-ids"
      }
    }
  }
}

# =============================================================================
# Resource Quota
# =============================================================================

resource "kubernetes_resource_quota" "security" {
  metadata {
    name      = "security-quota"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  spec {
    hard = {
      "requests.cpu"    = "4"
      "requests.memory" = "8Gi"
      "limits.cpu"      = "8"
      "limits.memory"   = "16Gi"
      "pods"            = "20"
      "services"        = "10"
    }
  }
}

# =============================================================================
# Limit Range
# =============================================================================

resource "kubernetes_limit_range" "security" {
  metadata {
    name      = "security-limits"
    namespace = kubernetes_namespace.security.metadata[0].name
    labels    = var.common_labels
  }

  spec {
    limit {
      type = "Container"

      default = {
        cpu    = "500m"
        memory = "512Mi"
      }

      default_request = {
        cpu    = "100m"
        memory = "128Mi"
      }

      max = {
        cpu    = "2"
        memory = "2Gi"
      }

      min = {
        cpu    = "50m"
        memory = "64Mi"
      }
    }
  }
}

# =============================================================================
# Outputs
# =============================================================================

output "namespace" {
  description = "Security namespace name"
  value       = kubernetes_namespace.security.metadata[0].name
}

output "ids_service_name" {
  description = "IDS service name"
  value       = kubernetes_service.ids.metadata[0].name
}

output "network_monitor_service_name" {
  description = "Network monitor service name"
  value       = kubernetes_service.network_monitor.metadata[0].name
}

output "ids_endpoint" {
  description = "IDS service endpoint"
  value       = "${kubernetes_service.ids.metadata[0].name}.${kubernetes_namespace.security.metadata[0].name}.svc.cluster.local"
}
