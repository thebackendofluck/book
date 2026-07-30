# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# ANTI-FRAUD SYSTEM - KUBERNETES DEPLOYMENT
# =============================================================================
# This Terraform configuration deploys the anti-fraud detection microservices
# to a Kubernetes cluster (EKS, GKE, or on-premises).
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.24"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }
  }
}

# =============================================================================
# VARIABLES
# =============================================================================

variable "namespace" {
  description = "Kubernetes namespace for deployment"
  type        = string
  default     = "fraud-detection"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}

variable "image_registry" {
  description = "Container image registry"
  type        = string
  default     = "your-account-id.dkr.ecr.us-east-1.amazonaws.com"
}

variable "image_tag" {
  description = "Container image tag"
  type        = string
  default     = "latest"
}

# Service Configuration
variable "kafka_brokers" {
  description = "Kafka bootstrap servers"
  type        = string
}

variable "redis_host" {
  description = "Redis host"
  type        = string
}

variable "postgres_host" {
  description = "PostgreSQL host"
  type        = string
}

variable "postgres_secret_name" {
  description = "Kubernetes secret name for PostgreSQL credentials"
  type        = string
  default     = "postgres-credentials"
}

# Replica Counts
variable "ingestion_replicas" {
  description = "Number of data ingestion replicas"
  type        = number
  default     = 3
}

variable "feature_replicas" {
  description = "Number of feature engineering replicas"
  type        = number
  default     = 5
}

variable "model_replicas" {
  description = "Number of model serving replicas"
  type        = number
  default     = 10
}

variable "alerting_replicas" {
  description = "Number of alerting replicas"
  type        = number
  default     = 2
}

variable "compliance_replicas" {
  description = "Number of compliance replicas"
  type        = number
  default     = 2
}

variable "dashboard_replicas" {
  description = "Number of dashboard replicas"
  type        = number
  default     = 2
}

# =============================================================================
# NAMESPACE
# =============================================================================

resource "kubernetes_namespace" "fraud_detection" {
  metadata {
    name = var.namespace

    labels = {
      name        = var.namespace
      environment = var.environment
      managed-by  = "terraform"
    }

    annotations = {
      "istio-injection" = "enabled"
    }
  }
}

# =============================================================================
# CONFIG MAPS
# =============================================================================

resource "kubernetes_config_map" "app_config" {
  metadata {
    name      = "fraud-detection-config"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  data = {
    ENVIRONMENT        = var.environment
    LOG_LEVEL          = var.environment == "production" ? "INFO" : "DEBUG"
    KAFKA_BROKERS      = var.kafka_brokers
    REDIS_HOST         = var.redis_host
    REDIS_PORT         = "6379"
    POSTGRES_HOST      = var.postgres_host
    POSTGRES_PORT      = "5432"
    POSTGRES_DB        = "fraud_detection"
    POLARS_MAX_THREADS = "4"
    MAX_WORKERS        = "4"
  }
}

resource "kubernetes_config_map" "prometheus_config" {
  metadata {
    name      = "prometheus-config"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  data = {
    "prometheus.yml" = <<-EOF
      global:
        scrape_interval: 15s
        evaluation_interval: 15s

      scrape_configs:
        - job_name: 'fraud-detection-services'
          kubernetes_sd_configs:
            - role: pod
              namespaces:
                names:
                  - ${var.namespace}
          relabel_configs:
            - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
              action: keep
              regex: true
            - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
              action: replace
              target_label: __metrics_path__
              regex: (.+)
            - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
              action: replace
              regex: ([^:]+)(?::\d+)?;(\d+)
              replacement: $1:$2
              target_label: __address__
    EOF
  }
}

# =============================================================================
# DATA INGESTION SERVICE
# =============================================================================

resource "kubernetes_deployment" "data_ingestion" {
  metadata {
    name      = "data-ingestion"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name

    labels = {
      app       = "data-ingestion"
      component = "ingestion"
    }
  }

  spec {
    replicas = var.ingestion_replicas

    selector {
      match_labels = {
        app = "data-ingestion"
      }
    }

    template {
      metadata {
        labels = {
          app       = "data-ingestion"
          component = "ingestion"
        }

        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "8080"
          "prometheus.io/path"   = "/metrics"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.app.metadata[0].name

        container {
          name  = "data-ingestion"
          image = "${var.image_registry}/fraud-detection/data-ingestion:${var.image_tag}"

          port {
            container_port = 8080
            name           = "http"
          }

          env_from {
            config_map_ref {
              name = kubernetes_config_map.app_config.metadata[0].name
            }
          }

          env {
            name = "POSTGRES_USER"
            value_from {
              secret_key_ref {
                name = var.postgres_secret_name
                key  = "username"
              }
            }
          }

          env {
            name = "POSTGRES_PASSWORD"
            value_from {
              secret_key_ref {
                name = var.postgres_secret_name
                key  = "password"
              }
            }
          }

          resources {
            requests = {
              cpu    = "500m"
              memory = "512Mi"
            }
            limits = {
              cpu    = "1000m"
              memory = "1Gi"
            }
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 8080
            }
            initial_delay_seconds = 30
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/ready"
              port = 8080
            }
            initial_delay_seconds = 10
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

          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }

        volume {
          name = "tmp"
          empty_dir {}
        }

        affinity {
          pod_anti_affinity {
            preferred_during_scheduling_ignored_during_execution {
              weight = 100
              pod_affinity_term {
                label_selector {
                  match_labels = {
                    app = "data-ingestion"
                  }
                }
                topology_key = "kubernetes.io/hostname"
              }
            }
          }
        }
      }
    }

    strategy {
      type = "RollingUpdate"
      rolling_update {
        max_surge       = "25%"
        max_unavailable = "25%"
      }
    }
  }
}

resource "kubernetes_service" "data_ingestion" {
  metadata {
    name      = "data-ingestion"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name

    labels = {
      app = "data-ingestion"
    }
  }

  spec {
    selector = {
      app = "data-ingestion"
    }

    port {
      name        = "http"
      port        = 80
      target_port = 8080
    }

    type = "ClusterIP"
  }
}

# =============================================================================
# FEATURE ENGINEERING SERVICE
# =============================================================================

resource "kubernetes_deployment" "feature_engineering" {
  metadata {
    name      = "feature-engineering"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name

    labels = {
      app       = "feature-engineering"
      component = "processing"
    }
  }

  spec {
    replicas = var.feature_replicas

    selector {
      match_labels = {
        app = "feature-engineering"
      }
    }

    template {
      metadata {
        labels = {
          app       = "feature-engineering"
          component = "processing"
        }

        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "8081"
          "prometheus.io/path"   = "/metrics"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.app.metadata[0].name

        container {
          name  = "feature-engineering"
          image = "${var.image_registry}/fraud-detection/feature-engineering:${var.image_tag}"

          port {
            container_port = 8081
            name           = "http"
          }

          env_from {
            config_map_ref {
              name = kubernetes_config_map.app_config.metadata[0].name
            }
          }

          env {
            name = "POSTGRES_USER"
            value_from {
              secret_key_ref {
                name = var.postgres_secret_name
                key  = "username"
              }
            }
          }

          env {
            name = "POSTGRES_PASSWORD"
            value_from {
              secret_key_ref {
                name = var.postgres_secret_name
                key  = "password"
              }
            }
          }

          resources {
            requests = {
              cpu    = "1000m"
              memory = "2Gi"
            }
            limits = {
              cpu    = "2000m"
              memory = "4Gi"
            }
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 8081
            }
            initial_delay_seconds = 60
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/ready"
              port = 8081
            }
            initial_delay_seconds = 30
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

          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }

        volume {
          name = "tmp"
          empty_dir {}
        }
      }
    }
  }
}

resource "kubernetes_service" "feature_engineering" {
  metadata {
    name      = "feature-engineering"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    selector = {
      app = "feature-engineering"
    }

    port {
      name        = "http"
      port        = 80
      target_port = 8081
    }

    type = "ClusterIP"
  }
}

# =============================================================================
# MODEL SERVING SERVICE
# =============================================================================

resource "kubernetes_deployment" "model_serving" {
  metadata {
    name      = "model-serving"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name

    labels = {
      app       = "model-serving"
      component = "ml"
    }
  }

  spec {
    replicas = var.model_replicas

    selector {
      match_labels = {
        app = "model-serving"
      }
    }

    template {
      metadata {
        labels = {
          app       = "model-serving"
          component = "ml"
        }

        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "8082"
          "prometheus.io/path"   = "/metrics"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.app.metadata[0].name

        container {
          name  = "model-serving"
          image = "${var.image_registry}/fraud-detection/model-serving:${var.image_tag}"

          port {
            container_port = 8082
            name           = "http"
          }

          env_from {
            config_map_ref {
              name = kubernetes_config_map.app_config.metadata[0].name
            }
          }

          resources {
            requests = {
              cpu    = "1000m"
              memory = "2Gi"
            }
            limits = {
              cpu    = "2000m"
              memory = "4Gi"
            }
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 8082
            }
            initial_delay_seconds = 120
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/ready"
              port = 8082
            }
            initial_delay_seconds = 60
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

          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }

          volume_mount {
            name       = "models"
            mount_path = "/app/models"
            read_only  = true
          }
        }

        volume {
          name = "tmp"
          empty_dir {}
        }

        volume {
          name = "models"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.models.metadata[0].name
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "model_serving" {
  metadata {
    name      = "model-serving"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    selector = {
      app = "model-serving"
    }

    port {
      name        = "http"
      port        = 80
      target_port = 8082
    }

    type = "ClusterIP"
  }
}

# =============================================================================
# ALERTING SERVICE
# =============================================================================

resource "kubernetes_deployment" "alerting" {
  metadata {
    name      = "alerting"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name

    labels = {
      app       = "alerting"
      component = "alerting"
    }
  }

  spec {
    replicas = var.alerting_replicas

    selector {
      match_labels = {
        app = "alerting"
      }
    }

    template {
      metadata {
        labels = {
          app       = "alerting"
          component = "alerting"
        }

        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "8083"
          "prometheus.io/path"   = "/metrics"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.app.metadata[0].name

        container {
          name  = "alerting"
          image = "${var.image_registry}/fraud-detection/alerting:${var.image_tag}"

          port {
            container_port = 8083
            name           = "http"
          }

          env_from {
            config_map_ref {
              name = kubernetes_config_map.app_config.metadata[0].name
            }
          }

          env {
            name = "POSTGRES_USER"
            value_from {
              secret_key_ref {
                name = var.postgres_secret_name
                key  = "username"
              }
            }
          }

          env {
            name = "POSTGRES_PASSWORD"
            value_from {
              secret_key_ref {
                name = var.postgres_secret_name
                key  = "password"
              }
            }
          }

          resources {
            requests = {
              cpu    = "250m"
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
              port = 8083
            }
            initial_delay_seconds = 30
            period_seconds        = 10
          }

          readiness_probe {
            http_get {
              path = "/ready"
              port = 8083
            }
            initial_delay_seconds = 10
            period_seconds        = 5
          }

          security_context {
            run_as_non_root            = true
            run_as_user                = 1000
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
          }

          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }

        volume {
          name = "tmp"
          empty_dir {}
        }
      }
    }
  }
}

resource "kubernetes_service" "alerting" {
  metadata {
    name      = "alerting"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    selector = {
      app = "alerting"
    }

    port {
      name        = "http"
      port        = 80
      target_port = 8083
    }

    type = "ClusterIP"
  }
}

# =============================================================================
# COMPLIANCE SERVICE
# =============================================================================

resource "kubernetes_deployment" "compliance" {
  metadata {
    name      = "compliance"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name

    labels = {
      app       = "compliance"
      component = "compliance"
    }
  }

  spec {
    replicas = var.compliance_replicas

    selector {
      match_labels = {
        app = "compliance"
      }
    }

    template {
      metadata {
        labels = {
          app       = "compliance"
          component = "compliance"
        }

        annotations = {
          "prometheus.io/scrape" = "true"
          "prometheus.io/port"   = "8084"
          "prometheus.io/path"   = "/metrics"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.app.metadata[0].name

        container {
          name  = "compliance"
          image = "${var.image_registry}/fraud-detection/compliance:${var.image_tag}"

          port {
            container_port = 8084
            name           = "http"
          }

          env_from {
            config_map_ref {
              name = kubernetes_config_map.app_config.metadata[0].name
            }
          }

          env {
            name = "POSTGRES_USER"
            value_from {
              secret_key_ref {
                name = var.postgres_secret_name
                key  = "username"
              }
            }
          }

          env {
            name = "POSTGRES_PASSWORD"
            value_from {
              secret_key_ref {
                name = var.postgres_secret_name
                key  = "password"
              }
            }
          }

          resources {
            requests = {
              cpu    = "250m"
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
              port = 8084
            }
            initial_delay_seconds = 30
            period_seconds        = 10
          }

          readiness_probe {
            http_get {
              path = "/ready"
              port = 8084
            }
            initial_delay_seconds = 10
            period_seconds        = 5
          }

          security_context {
            run_as_non_root            = true
            run_as_user                = 1000
            read_only_root_filesystem  = true
            allow_privilege_escalation = false
          }

          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }

        volume {
          name = "tmp"
          empty_dir {}
        }
      }
    }
  }
}

resource "kubernetes_service" "compliance" {
  metadata {
    name      = "compliance"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    selector = {
      app = "compliance"
    }

    port {
      name        = "http"
      port        = 80
      target_port = 8084
    }

    type = "ClusterIP"
  }
}

# =============================================================================
# DASHBOARD SERVICE
# =============================================================================

resource "kubernetes_deployment" "dashboard" {
  metadata {
    name      = "dashboard"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name

    labels = {
      app       = "dashboard"
      component = "frontend"
    }
  }

  spec {
    replicas = var.dashboard_replicas

    selector {
      match_labels = {
        app = "dashboard"
      }
    }

    template {
      metadata {
        labels = {
          app       = "dashboard"
          component = "frontend"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.app.metadata[0].name

        container {
          name  = "dashboard"
          image = "${var.image_registry}/fraud-detection/dashboard:${var.image_tag}"

          port {
            container_port = 80
            name           = "http"
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "128Mi"
            }
            limits = {
              cpu    = "200m"
              memory = "256Mi"
            }
          }

          liveness_probe {
            http_get {
              path = "/"
              port = 80
            }
            initial_delay_seconds = 10
            period_seconds        = 10
          }

          readiness_probe {
            http_get {
              path = "/"
              port = 80
            }
            initial_delay_seconds = 5
            period_seconds        = 5
          }

          security_context {
            run_as_non_root            = false
            read_only_root_filesystem  = false
            allow_privilege_escalation = false
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "dashboard" {
  metadata {
    name      = "dashboard"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    selector = {
      app = "dashboard"
    }

    port {
      name        = "http"
      port        = 80
      target_port = 80
    }

    type = "ClusterIP"
  }
}

# =============================================================================
# INGRESS
# =============================================================================

resource "kubernetes_ingress_v1" "main" {
  metadata {
    name      = "fraud-detection-ingress"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name

    annotations = {
      "kubernetes.io/ingress.class"                 = "nginx"
      "nginx.ingress.kubernetes.io/ssl-redirect"    = "true"
      "nginx.ingress.kubernetes.io/proxy-body-size" = "50m"
      "cert-manager.io/cluster-issuer"              = "letsencrypt-prod"
    }
  }

  spec {
    tls {
      hosts       = ["fraud-detection.example.com"]
      secret_name = "fraud-detection-tls"
    }

    rule {
      host = "fraud-detection.example.com"

      http {
        path {
          path      = "/api/ingestion"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.data_ingestion.metadata[0].name
              port {
                number = 80
              }
            }
          }
        }

        path {
          path      = "/api/features"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.feature_engineering.metadata[0].name
              port {
                number = 80
              }
            }
          }
        }

        path {
          path      = "/api/models"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.model_serving.metadata[0].name
              port {
                number = 80
              }
            }
          }
        }

        path {
          path      = "/api/alerts"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.alerting.metadata[0].name
              port {
                number = 80
              }
            }
          }
        }

        path {
          path      = "/api/compliance"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.compliance.metadata[0].name
              port {
                number = 80
              }
            }
          }
        }

        path {
          path      = "/"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.dashboard.metadata[0].name
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
# HORIZONTAL POD AUTOSCALER
# =============================================================================

resource "kubernetes_horizontal_pod_autoscaler_v2" "data_ingestion" {
  metadata {
    name      = "data-ingestion-hpa"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    scale_target_ref {
      api_version = "apps/v1"
      kind        = "Deployment"
      name        = kubernetes_deployment.data_ingestion.metadata[0].name
    }

    min_replicas = var.ingestion_replicas
    max_replicas = var.ingestion_replicas * 3

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
          period_seconds = 15
        }
        policy {
          type           = "Pods"
          value          = 4
          period_seconds = 15
        }
      }
    }
  }
}

resource "kubernetes_horizontal_pod_autoscaler_v2" "model_serving" {
  metadata {
    name      = "model-serving-hpa"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    scale_target_ref {
      api_version = "apps/v1"
      kind        = "Deployment"
      name        = kubernetes_deployment.model_serving.metadata[0].name
    }

    min_replicas = var.model_replicas
    max_replicas = var.model_replicas * 3

    metric {
      type = "Resource"
      resource {
        name = "cpu"
        target {
          type                = "Utilization"
          average_utilization = 60
        }
      }
    }

    behavior {
      scale_up {
        stabilization_window_seconds = 30
        select_policy                = "Max"
        policy {
          type           = "Percent"
          value          = 200
          period_seconds = 15
        }
      }
    }
  }
}

# =============================================================================
# POD DISRUPTION BUDGETS
# =============================================================================

resource "kubernetes_pod_disruption_budget" "data_ingestion" {
  metadata {
    name      = "data-ingestion-pdb"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    min_available = "50%"

    selector {
      match_labels = {
        app = "data-ingestion"
      }
    }
  }
}

resource "kubernetes_pod_disruption_budget" "model_serving" {
  metadata {
    name      = "model-serving-pdb"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    min_available = "70%"

    selector {
      match_labels = {
        app = "model-serving"
      }
    }
  }
}

# =============================================================================
# SERVICE ACCOUNT
# =============================================================================

resource "kubernetes_service_account" "app" {
  metadata {
    name      = "fraud-detection-sa"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name

    annotations = {
      "eks.amazonaws.com/role-arn" = "arn:aws:iam::ACCOUNT_ID:role/fraud-detection-pod-role"
    }
  }
}

# =============================================================================
# NETWORK POLICIES
# =============================================================================

resource "kubernetes_network_policy" "default_deny" {
  metadata {
    name      = "default-deny"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
  }
}

resource "kubernetes_network_policy" "allow_internal" {
  metadata {
    name      = "allow-internal"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    pod_selector {}

    ingress {
      from {
        namespace_selector {
          match_labels = {
            name = var.namespace
          }
        }
      }
    }

    egress {
      to {
        namespace_selector {
          match_labels = {
            name = var.namespace
          }
        }
      }

      # Allow DNS
      to {
        namespace_selector {}
        pod_selector {
          match_labels = {
            "k8s-app" = "kube-dns"
          }
        }
      }
      ports {
        protocol = "UDP"
        port     = "53"
      }
    }

    policy_types = ["Ingress", "Egress"]
  }
}

# =============================================================================
# PERSISTENT VOLUME CLAIM
# =============================================================================

resource "kubernetes_persistent_volume_claim" "models" {
  metadata {
    name      = "models-pvc"
    namespace = kubernetes_namespace.fraud_detection.metadata[0].name
  }

  spec {
    access_modes       = ["ReadOnlyMany"]
    storage_class_name = "efs-sc"

    resources {
      requests = {
        storage = "50Gi"
      }
    }
  }
}

# =============================================================================
# OUTPUTS
# =============================================================================

output "namespace" {
  description = "Kubernetes namespace"
  value       = kubernetes_namespace.fraud_detection.metadata[0].name
}

output "services" {
  description = "Deployed services"
  value = {
    data_ingestion      = kubernetes_service.data_ingestion.metadata[0].name
    feature_engineering = kubernetes_service.feature_engineering.metadata[0].name
    model_serving       = kubernetes_service.model_serving.metadata[0].name
    alerting            = kubernetes_service.alerting.metadata[0].name
    compliance          = kubernetes_service.compliance.metadata[0].name
    dashboard           = kubernetes_service.dashboard.metadata[0].name
  }
}

output "ingress_host" {
  description = "Ingress hostname"
  value       = "fraud-detection.example.com"
}
