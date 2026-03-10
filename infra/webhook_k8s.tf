# ─── Kubernetes resources for the alertmanager webhook server ─────────────────
# Requires the AgentCore Runtime to exist first (runtime ID injected as a secret).

resource "kubernetes_namespace" "alertmanager_agent" {
  metadata {
    name = "alertmanager-agent"
  }
}

resource "kubernetes_service_account" "webhook_server" {
  metadata {
    name      = "webhook-server-sa"
    namespace = kubernetes_namespace.alertmanager_agent.metadata[0].name
    annotations = {
      # IRSA annotation — lets the pod exchange the SA token for the IAM role
      "eks.amazonaws.com/role-arn" = aws_iam_role.webhook_server_irsa.arn
    }
  }
}

resource "kubernetes_cluster_role" "webhook_server" {
  metadata {
    name = "webhook-server-role"
  }

  # Read pods, logs, events
  rule {
    api_groups = [""]
    resources  = ["pods", "pods/log", "events", "namespaces"]
    verbs      = ["get", "list", "watch"]
  }

  # Read & patch workloads (OOM auto-fix: patch resource limits + rollout restart)
  rule {
    api_groups = ["apps"]
    resources  = ["deployments", "statefulsets", "daemonsets", "replicasets"]
    verbs      = ["get", "list", "watch", "patch", "update"]
  }
}

resource "kubernetes_cluster_role_binding" "webhook_server" {
  metadata {
    name = "webhook-server-rolebinding"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.webhook_server.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.webhook_server.metadata[0].name
    namespace = kubernetes_namespace.alertmanager_agent.metadata[0].name
  }
}

# Secret injected into the webhook server pod — includes the AgentCore Runtime ID
# populated after the runtime is deployed by null_resource.agentcore_runtime
resource "kubernetes_secret" "webhook_secrets" {
  metadata {
    name      = "webhook-secrets"
    namespace = kubernetes_namespace.alertmanager_agent.metadata[0].name
  }

  data = {
    BEDROCK_AGENT_RUNTIME_ID = trimspace(data.local_file.agentcore_runtime_id.content)
    AWS_REGION               = var.aws_region
    AWS_ACCOUNT_ID           = var.aws_account_id
    JIRA_BASE_URL            = var.jira_base_url
    JIRA_PROJECT_KEY         = var.jira_project_key
    JIRA_EMAIL               = var.jira_email
    JIRA_API_TOKEN           = var.jira_api_token
    SLACK_WEBHOOK_URL        = var.slack_webhook_url
  }
}

resource "kubernetes_deployment" "webhook_server" {
  wait_for_rollout = false

  metadata {
    name      = "alertmanager-webhook-server"
    namespace = kubernetes_namespace.alertmanager_agent.metadata[0].name
    labels = {
      app = "alertmanager-webhook-server"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        app = "alertmanager-webhook-server"
      }
    }

    template {
      metadata {
        labels = {
          app = "alertmanager-webhook-server"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.webhook_server.metadata[0].name

        container {
          name              = "webhook"
          image             = "ranjini/alertmanager-webhook-server:latest"
          image_pull_policy = "Always"

          port {
            container_port = 8080
            name           = "http"
          }

          env_from {
            secret_ref {
              name = kubernetes_secret.webhook_secrets.metadata[0].name
            }
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 8080
            }
            initial_delay_seconds = 15
            period_seconds        = 15
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/health"
              port = 8080
            }
            initial_delay_seconds = 10
            period_seconds        = 10
            failure_threshold     = 3
          }

          resources {
            requests = {
              memory = "256Mi"
              cpu    = "100m"
            }
            limits = {
              memory = "512Mi"
              cpu    = "500m"
            }
          }
        }
      }
    }
  }

  depends_on = [
    kubernetes_secret.webhook_secrets,
    null_resource.agentcore_runtime,
  ]
}

resource "kubernetes_service" "webhook_server" {
  metadata {
    name      = "alertmanager-webhook-server"
    namespace = kubernetes_namespace.alertmanager_agent.metadata[0].name
    labels = {
      app = "alertmanager-webhook-server"
    }
  }

  spec {
    selector = {
      app = "alertmanager-webhook-server"
    }

    port {
      name        = "http"
      port        = 80
      target_port = 8080
    }

    type = "ClusterIP"
  }
}
